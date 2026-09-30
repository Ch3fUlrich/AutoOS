#!/usr/bin/env python3
"""Sweep cheap t2 models through the OmniRoute combos (lane P1-sweep, 2026-09-30).

For each candidate leg, sends through the live gateway:
  (a) an ack chat ("Say OK", max_tokens 16);
  (b) ONE tool-call round trip (the probe-toolcalls.py house shape: get_weather
      in Paris, then feed back {temp_c:18}).

Records per leg: model id, ack ok/fail + latency_ms, tool-call ok/fail +
latency_ms, context/output limits from /v1/models, verbatim error token.

For each combo, sends one chat through the combo name to test routing and
record which leg served (finish_reason + first 80 chars of content).

Never prints or logs the gateway key. Handles 429/503 with the probe_common
retry, plus a 60-120s admission backoff for chat_admission_busy / Rate limit
exceeded. Logs every admission/rate event with a UTC timestamp.

Usage:
    python3 tools/probe-sweep.py
    python3 tools/probe-sweep.py --legs deepseek/deepseek-flash free-ai/qwen7b
    python3 tools/probe-sweep.py --dry-run

Exit codes: 0 the sweep ran; 2 bad arguments; 3 no key or gateway down.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from probe_common import (  # noqa: E402
    DEFAULT_GATEWAY,
    DEFAULT_REGISTRY,
    now_iso as _now_iso,
    post_with_retry,
    load_agent_module as _load_agent_module,
    gateway_up,
)
from registry import resolve_leg  # noqa: E402

# probe-toolcalls.py has a hyphen — load by path, like probe_common.load_agent_module.
_spec = importlib.util.spec_from_file_location(
    "probe_toolcalls", os.path.join(HERE, "probe-toolcalls.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
single_call_body = _mod.single_call_body
round_trip_body = _mod.round_trip_body
_check_single = _mod._check_single
_check_round_trip = _mod._check_round_trip
_toolcalls_make_post = _mod.make_post

GATEWAY_BASE = DEFAULT_GATEWAY.rsplit("/v1/", 1)[0]
MODELS_URL = GATEWAY_BASE + "/v1/models"
ADMISSION_BACKOFF_MIN_S = 60
ADMISSION_BACKOFF_MAX_S = 120
ACK_MAX_TOKENS = 16
TOOLCALL_MAX_TOKENS = 2048
SWEEP_LOG = os.path.join(ROOT, "logs", "probe-sweep-%s.jsonl" %
                         datetime.now(timezone.utc).strftime("%Y%m%d"))

_sleep = time.sleep

# ---------------------------------------------------------------------------
# Candidate legs — from the operator directive + the current combos.json.
# Each entry: (leg_id, source) where source is "combo:<name>" or "operator"
# or "registry".
# ---------------------------------------------------------------------------

def candidate_legs(registry):
    """Return the ordered list of (leg, source) candidates to sweep.

    Derived from the live combos.json (read-only) + operator directive list.
    """
    combos_path = os.path.join(ROOT, "configuration", "omniroute", "combos.json")
    with open(combos_path, encoding="utf-8") as fh:
        combos = json.load(fh)
    combo_map = {c["name"]: c for c in combos.get("combos", [])}

    legs = []      # (leg, source)
    seen = set()

    def add(leg, source):
        if leg not in seen:
            seen.add(leg)
            legs.append((leg, source))

    # 1. All legs from t2-worker, t1-orchestrator, t3-driver, and their
    #    free-only / clean variants — these are the combos the sweep mission
    #    targets.
    for cname in ["t2-worker", "t2-worker-free-only", "t1-orchestrator",
                  "t1-orchestrator-free-only", "t3-driver",
                  "t3-driver-free-only"]:
        combo = combo_map.get(cname)
        if not combo:
            continue
        for leg in combo.get("models", []):
            add(leg, "combo:" + cname)

    # 2. Operator-explicit candidates not in the combos above.
    operator_legs = [
        "groq/openai/gpt-oss-120b",
        "cerebras/gpt-oss-120b",
        "antigravity/gemini-3.7-flash-high",
        "antigravity/gemini-3.7-flash-medium",
        "morph/morph-dsv4flash",
        "morph/morph-glm52-744b",
    ]
    for leg in operator_legs:
        add(leg, "operator")

    return legs


# ---------------------------------------------------------------------------
# Gateway /v1/models — context/output limits per leg.
# ---------------------------------------------------------------------------

def fetch_models_metadata(key, timeout=15):
    """Return {model_id: {context_length, ...}} from /v1/models. Never logs key."""
    req = urllib.request.Request(
        MODELS_URL,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", "replace"))
        out = {}
        for m in data.get("data", []):
            mid = m.get("id", "")
            out[mid] = {
                "context_length": m.get("context_length") or m.get("context_window"),
                "max_output": m.get("max_output_tokens") or m.get("max_tokens"),
            }
        return out
    except Exception as exc:
        sys.stderr.write("probe-sweep: cannot fetch /v1/models: %s\n" %
                         type(exc).__name__)
        return {}


# ---------------------------------------------------------------------------
# Ack chat — a minimal "Say OK" request.
# ---------------------------------------------------------------------------

def ack_body(leg, max_tokens=ACK_MAX_TOKENS):
    return {
        "model": leg,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": max_tokens,
    }


def run_ack(leg, post):
    """Send an ack chat. Return {ok, status, latency_ms, content_preview, error}."""
    t0 = time.monotonic()
    status, parsed, error = post_with_retry(post, ack_body(leg), _sleep)
    latency_ms = int((time.monotonic() - t0) * 1000)
    if status != 200 or parsed is None:
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "content_preview": "", "error": error or "no response"}
    try:
        content = parsed["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        content = ""
    return {"ok": True, "status": 200, "latency_ms": latency_ms,
            "content_preview": content[:80], "error": None}


# ---------------------------------------------------------------------------
# Tool-call trial — one single-call + round-trip, the probe-toolcalls shape.
# ---------------------------------------------------------------------------

def run_toolcall(leg, post):
    """One tool-call trial. Return {ok, status, latency_ms, note, single, round}."""
    t0 = time.monotonic()
    status, parsed, error = post_with_retry(
        post, single_call_body(leg, TOOLCALL_MAX_TOKENS), _sleep)
    single_latency = int((time.monotonic() - t0) * 1000)
    if status != 200 or parsed is None:
        return {"ok": False, "status": status, "latency_ms": single_latency,
                "note": error or "no response", "single": "error", "round": "skipped"}
    ok, call, note = _check_single(parsed)
    if not ok:
        return {"ok": False, "status": status, "latency_ms": single_latency,
                "note": note, "single": "fail", "round": "skipped"}
    # Round trip
    t1 = time.monotonic()
    assistant_message = parsed["choices"][0]["message"]
    rt_status, rt_parsed, rt_error = post_with_retry(
        post, round_trip_body(leg, assistant_message, call.get("id"),
                              TOOLCALL_MAX_TOKENS), _sleep)
    rt_latency = int((time.monotonic() - t1) * 1000)
    if rt_status != 200 or rt_parsed is None:
        return {"ok": False, "status": status, "latency_ms": single_latency + rt_latency,
                "note": "round trip: %s" % (rt_error or "HTTP %s" % rt_status),
                "single": "pass", "round": "error"}
    rt_ok, rt_note = _check_round_trip(rt_parsed)
    return {"ok": rt_ok, "status": status,
            "latency_ms": single_latency + rt_latency,
            "note": rt_note, "single": "pass", "round": "pass" if rt_ok else "fail"}


# ---------------------------------------------------------------------------
# Combo routing test — send a chat through the combo name, record which leg
# served.
# ---------------------------------------------------------------------------

def combo_chat_body(combo_name, max_tokens=ACK_MAX_TOKENS):
    return {
        "model": combo_name,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": max_tokens,
    }


def run_combo_test(combo_name, post):
    """Send a chat through a combo name. Return {ok, status, latency_ms, model_served, error}."""
    t0 = time.monotonic()
    status, parsed, error = post_with_retry(post, combo_chat_body(combo_name), _sleep)
    latency_ms = int((time.monotonic() - t0) * 1000)
    if status != 200 or parsed is None:
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "model_served": "", "error": error or "no response"}
    try:
        model_served = parsed.get("model", "")
        content = parsed["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        model_served = ""
        content = ""
    return {"ok": True, "status": 200, "latency_ms": latency_ms,
            "model_served": model_served, "content_preview": content[:80],
            "error": None}


# ---------------------------------------------------------------------------
# Admission / rate-limit detection and backoff.
# ---------------------------------------------------------------------------

ADMISSION_PATTERNS = ("chat_admission_busy", "Rate limit exceeded",
                      "rate_limit_exceeded", "admission")

admission_count = 0


def is_admission_error(status, error):
    """True when the error indicates admission/rate-limit (not a normal failure)."""
    if status == 429:
        return True
    if error and any(p in str(error) for p in ADMISSION_PATTERNS):
        return True
    if status == 503:
        return True
    return False


def admission_backoff(leg, status, error):
    """Log the admission event and sleep 60-120s. Returns the sleep duration."""
    global admission_count
    admission_count += 1
    delay = random.randint(ADMISSION_BACKOFF_MIN_S, ADMISSION_BACKOFF_MAX_S)
    ts = _now_iso()
    sys.stderr.write("probe-sweep: [%s] ADMISSION/RATE leg=%s status=%s "
                     "error=%s backing off %ds (count=%d)\n" %
                     (ts, leg, status, (error or "")[:120], delay, admission_count))
    _sleep(delay)
    return delay


# ---------------------------------------------------------------------------
# JSONL log writer.
# ---------------------------------------------------------------------------

def log_result(record):
    """Append one record to the sweep JSONL log (git-ignored)."""
    os.makedirs(os.path.dirname(SWEEP_LOG), exist_ok=True)
    with open(SWEEP_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main sweep.
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    global SWEEP_LOG
    ap = argparse.ArgumentParser(
        description="Sweep cheap t2 models through the OmniRoute combos.")
    ap.add_argument("--legs", action="append", default=[],
                    help="only these legs (repeatable); default: all candidates")
    ap.add_argument("--dry-run", action="store_true",
                    help="list candidates; make no request")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--log", default=None,
                    help="JSONL log file (default: logs/probe-sweep-YYYYMMDD.jsonl)")
    args = ap.parse_args(argv)

    if args.log:
        SWEEP_LOG = args.log

    # Load registry for resolve_leg metadata.
    try:
        with open(args.registry, encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError) as exc:
        print("probe-sweep: cannot read registry %s: %s" % (args.registry, exc),
              file=sys.stderr)
        return 2

    candidates = candidate_legs(registry)
    if args.legs:
        leg_set = set(args.legs)
        candidates = [(l, s) for l, s in candidates if l in leg_set]
        # Also allow explicit legs not in the candidate list.
        for leg in args.legs:
            if leg not in leg_set:
                pass  # already handled above
        # Re-add any explicitly named legs not in candidates.
        existing = {l for l, _ in candidates}
        for leg in args.legs:
            if leg not in existing:
                candidates.append((leg, "cli"))
                existing.add(leg)

    if args.dry_run:
        print("# probe-sweep dry-run — %d candidates" % len(candidates))
        for leg, source in candidates:
            try:
                prov, model = resolve_leg(leg, registry)
            except ValueError:
                prov, model = "?", "?"
            print("  %s\t%s\tprovider=%s" % (leg, source, prov))
        # Also list combos to test.
        combos_path = os.path.join(ROOT, "configuration", "omniroute", "combos.json")
        with open(combos_path, encoding="utf-8") as fh:
            combos = json.load(fh)
        print("# combos to test:")
        for c in combos.get("combos", []):
            if c["name"].startswith("t1-") or c["name"].startswith("t2-") or \
               c["name"].startswith("t3-") or c["name"] == "deepseek-v4.1-flash":
                print("  %s\tlegs=%s" % (c["name"], " -> ".join(c.get("models", []))))
        return 0

    # Load the client key (never printed, never logged).
    agent = _load_agent_module()
    key = agent.client_key(ROOT)
    if not key:
        print("probe-sweep: no OmniRoute client key (export AUTOOS_OMNIROUTE_KEY or "
              "add 'omniroute:' to configuration/api-keys.yml)", file=sys.stderr)
        return 3

    if not gateway_up(args.gateway):
        print("probe-sweep: gateway not reachable at %s" % args.gateway,
              file=sys.stderr)
        return 3

    # Fetch /v1/models for context/output metadata.
    models_meta = fetch_models_metadata(key)
    sys.stderr.write("probe-sweep: /v1/models returned %d entries\n" %
                     len(models_meta))

    # Build the post function (reuse probe_toolcalls.make_post for the
    # body-reading classifier on 400).
    post = _toolcalls_make_post(args.gateway, key)

    # --- Sweep each candidate leg ---
    results = []
    print("# probe-sweep — %d legs, %s" % (len(candidates), _now_iso()))
    print("# %-45s  %-7s  %-7s  %-8s  %-8s  %-8s  %s" %
          ("leg", "ack", "tool", "ack_ms", "tool_ms", "ctx", "error"))
    for leg, source in candidates:
        record = {
            "ts": _now_iso(),
            "leg": leg,
            "source": source,
            "ack": None,
            "toolcall": None,
            "meta": models_meta.get(leg, {}),
        }
        # Ack
        ack = run_ack(leg, post)
        if is_admission_error(ack["status"], ack.get("error")):
            admission_backoff(leg, ack["status"], ack.get("error"))
            ack = run_ack(leg, post)
        record["ack"] = ack

        # Tool-call (skip if ack failed with a hard error)
        if ack["ok"]:
            tc = run_toolcall(leg, post)
            if is_admission_error(tc["status"], tc.get("note")):
                admission_backoff(leg, tc["status"], tc.get("note"))
                tc = run_toolcall(leg, post)
            record["toolcall"] = tc
        else:
            record["toolcall"] = {"ok": False, "status": ack["status"],
                                  "latency_ms": 0, "note": "skipped: ack failed",
                                  "single": "skipped", "round": "skipped"}

        log_result(record)
        results.append(record)

        ctx = record["meta"].get("context_length") or "-"
        ack_s = "ok" if ack["ok"] else "FAIL"
        tc_r = record["toolcall"]
        tc_s = "ok" if tc_r.get("ok") else "FAIL"
        err = (ack.get("error") or tc_r.get("note") or "")[:60]
        print("  %-45s  %-7s  %-7s  %-8d  %-8d  %-8s  %s" %
              (leg, ack_s, tc_s, ack["latency_ms"],
               tc_r.get("latency_ms", 0), ctx, err))
        sys.stdout.flush()

    # --- Combo routing tests ---
    combos_path = os.path.join(ROOT, "configuration", "omniroute", "combos.json")
    with open(combos_path, encoding="utf-8") as fh:
        combos_data = json.load(fh)

    combo_names = [c["name"] for c in combos_data.get("combos", [])
                   if c["name"].startswith("t1-") or c["name"].startswith("t2-") or
                   c["name"].startswith("t3-") or c["name"] == "deepseek-v4.1-flash"]

    print("\n# combo routing tests — %d combos, %s" % (len(combo_names), _now_iso()))
    print("# %-25s  %-7s  %-8s  %-30s  %s" %
          ("combo", "ok", "ms", "model_served", "error"))
    for cname in combo_names:
        combo_result = run_combo_test(cname, post)
        if is_admission_error(combo_result["status"], combo_result.get("error")):
            admission_backoff(cname, combo_result["status"], combo_result.get("error"))
            combo_result = run_combo_test(cname, post)
        combo_record = {
            "ts": _now_iso(),
            "combo": cname,
            "result": combo_result,
        }
        log_result(combo_record)
        ok_s = "ok" if combo_result["ok"] else "FAIL"
        err = (combo_result.get("error") or "")[:50]
        print("  %-25s  %-7s  %-8d  %-30s  %s" %
              (cname, ok_s, combo_result["latency_ms"],
               combo_result.get("model_served", ""), err))
        sys.stdout.flush()

    # --- Summary ---
    ack_ok = sum(1 for r in results if r["ack"]["ok"])
    tc_ok = sum(1 for r in results if r["toolcall"] and r["toolcall"]["ok"])
    print("\n# summary: %d/%d ack ok, %d/%d tool-call ok, %d admission backoffs" %
          (ack_ok, len(results), tc_ok, len(results), admission_count))
    print("# log: %s" % SWEEP_LOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
