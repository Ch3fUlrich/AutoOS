#!/usr/bin/env python3
"""Leg-health probe for the ws-omniroute-20260930 leg-health lane (L1-beta).

Self-contained, measurement-only. It:

  * loads the OmniRoute client key **in-process** (env first, then the main
    checkout's git-ignored ``configuration/api-keys.yml`` ``omniroute:`` line)
    and never prints, logs or stores it;
  * reads the combo ids from ``configuration/omniroute/combos.json`` (read-only)
    and the hand entries above the ``AUTOOS-MANAGED-START`` marker in
    ``opencode.jsonc``;
  * sends ONE chat per COMBO and ONE chat per distinct LEG, ``max_tokens`` 256,
    ``>=3 s`` spacing, exactly one retry on a non-200;
  * records TRUE UTC (``datetime.now(timezone.utc)``) per attempt: HTTP status,
    ``latency_ms`` and the raw response body with Authorization / the key
    scrubbed -- plus e-mail addresses, URLs and IPv4 literals, which AGENTS.md
    forbids in a tracked file and which upstream error bodies can carry;
  * runs ``omniroute providers test-all --json`` once, timestamped;
  * appends JSON lines to ``docs/handoff/2026-09-30-leg-health-raw.jsonl``.

It is resumable: an (probe_id, attempt) already present in the JSONL is skipped,
so a re-run after an interruption continues instead of re-spending quota.

Nothing here edits configuration, registry or gateway state.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
COMBOS = os.path.join(ROOT, "configuration", "omniroute", "combos.json")
OPENCODE = os.path.join(ROOT, "opencode.jsonc")
OUT = os.path.join(HERE, "2026-09-30-leg-health-raw.jsonl")

GATEWAY = "http://127.0.0.1:20128/v1/chat/completions"
HEALTH = "http://127.0.0.1:20128/api/health"

PROMPT = "ack"
MAIN_MAX_TOKENS = 256
CONTROL_MAX_TOKENS = (8, 256)
# A reasoning leg whose tiny-budget behaviour is the documented trap: the model
# spends max_tokens on reasoning, so a small budget yields null content while a
# larger one answers. Vertex Pro answers 200 (below) so the pair isolates the
# token budget rather than a provider outage. The control probes it at 8 vs 256.
CONTROL_LEG = "vertex/gemini-3.1-pro-preview"

SPACING_S = 3.0
TIMEOUT_S = 60


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_client_key() -> str | None:
    """The client key, in-process. Never returned to a caller that prints it."""
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY")
    if key:
        return key
    # Lane worktrees carry no api-keys.yml (it is git-ignored); fall back to the
    # main checkout the worktree shares a common dir with.
    roots = [ROOT]
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        main = os.path.dirname(r.stdout.strip())
        if os.path.realpath(main) != os.path.realpath(ROOT):
            roots.append(main)
    for root in roots:
        path = os.path.join(root, "configuration", "api-keys.yml")
        if not os.path.isfile(path):
            continue
        for line in open(path, encoding="utf-8"):
            m = re.match(r"^omniroute\s*:\s*(.+?)\s*$", line)
            if m:
                val = m.group(1).strip("\"'")
                return None if val.startswith("REPLACE_WITH_") else val
    return None


def scrub(text: str, key: str) -> str:
    if not text:
        return text
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)bearer\s+\S+",
                  r"\1Bearer [REDACTED]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]{8,}", "Bearer [REDACTED]", text)
    # AGENTS.md hard rule 1: no e-mail address, hostname/URL or IP in a tracked
    # file. Upstream error bodies and the providers test-all report can carry
    # all three, so redact them here as well.
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED-EMAIL]", text)
    text = re.sub(r"https?://\S+", "[REDACTED-URL]", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "[REDACTED-IP]", text)
    return text


def parse_inputs():
    """(combos, legs) as ordered, de-duplicated lists of (name, id)."""
    with open(COMBOS, encoding="utf-8") as fh:
        doc = json.load(fh)
    combos = [(c["name"], c["name"]) for c in doc.get("combos", [])]

    region = open(OPENCODE, encoding="utf-8").read().split("AUTOOS-MANAGED-START", 1)[0]
    hand = re.findall(
        r'"([^"]+)"\s*:\s*\{[^{}]*?"modelID"\s*:\s*"([^"]+)"', region, re.S)

    legs: list[tuple[str, str]] = []
    seen = set()
    for _name, _id in combos:
        for model in [c["models"] for c in doc["combos"] if c["name"] == _name][0]:
            if model not in seen:
                seen.add(model)
                legs.append((model, model))
    for alias, model in hand:
        if model not in seen:
            seen.add(model)
            legs.append((alias, model))
    return combos, legs, hand


def load_done():
    """{probe_id, ...} already finished - resume support.

    A probe is finished when its 200 is recorded, when a retry (attempt >= 1)
    is recorded, or when it is the test-all record. A probe interrupted after a
    non-200 first attempt stays unfinished, so the retry still happens.
    """
    done = set()
    if os.path.isfile(OUT):
        for line in open(OUT, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("kind") == "test_all" or rec.get("status") == 200
                    or (isinstance(rec.get("attempt"), int)
                        and rec["attempt"] >= 1)):
                done.add(rec.get("probe_id"))
    return done


def append(rec: dict) -> None:
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def post(model: str, max_tokens: int, key: str):
    """(status, raw_body_text). HTTP errors keep their body so a 502 is attributable."""
    data = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": PROMPT}],
                       "max_tokens": max_tokens}).encode("utf-8")
    req = urllib.request.Request(
        GATEWAY, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - any transport failure is a finding
        return "ERR", "transport error: %s: %s" % (type(exc).__name__, exc)


def run_probe(probe_id, kind, name, model, max_tokens, key, done, last=[0.0]):
    """One logical probe: the first attempt, plus exactly one retry on non-200."""
    if probe_id in done:
        return
    attempt = 0
    while True:
        wait = SPACING_S - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        t0 = time.monotonic()
        status, body = post(model, max_tokens, key)
        latency = int(round((time.monotonic() - t0) * 1000))
        last[0] = time.monotonic()
        append({
            "probe_id": probe_id, "kind": kind, "name": name, "model": model,
            "max_tokens": max_tokens, "attempt": attempt,
            "utc": now_utc(), "status": status, "latency_ms": latency,
            "body": scrub(body, key),
        })
        print("%s\t%s\t%s\t%s\t%dms" % (kind, name, model, status, latency),
              file=sys.stderr)
        if status == 200 or attempt >= 1:
            return
        attempt += 1


def run_test_all(key: str, done):
    if "test-all" in done:
        return
    t0 = time.monotonic()
    started = now_utc()
    proc = subprocess.run(["omniroute", "providers", "test-all", "--json"],
                          capture_output=True, text=True, shell=True)
    elapsed = int(round((time.monotonic() - t0) * 1000))
    out = (proc.stdout or "")
    i = out.find("{")
    payload = out[i:] if i >= 0 else out
    append({
        "probe_id": "test-all", "kind": "test_all", "attempt": 0,
        "utc": started, "utc_end": now_utc(), "elapsed_ms": elapsed,
        "exit_code": proc.returncode,
        "output": scrub(payload, key),
        "stderr": scrub(proc.stderr or "", key)[-2000:],
    })
    print("test-all\texit=%s\t%dms" % (proc.returncode, elapsed), file=sys.stderr)


def main() -> int:
    key = load_client_key()
    if not key:
        print("probe: no OmniRoute client key (env or api-keys.yml)", file=sys.stderr)
        return 3
    try:
        with urllib.request.urlopen(HEALTH, timeout=5) as resp:
            up = resp.status == 200
    except Exception:  # noqa: BLE001
        up = False
    if not up:
        print("probe: gateway not reachable at %s" % HEALTH, file=sys.stderr)
        return 3

    combos, legs, hand = parse_inputs()
    done = load_done()
    last = [0.0]
    for name, model in combos:
        run_probe("combo:" + name, "combo", name, model, MAIN_MAX_TOKENS, key, done, last)
    for name, model in legs:
        run_probe("leg:" + model, "leg", name, model, MAIN_MAX_TOKENS, key, done, last)
    for mt in CONTROL_MAX_TOKENS:
        run_probe("control:%s:%d" % (CONTROL_LEG, mt), "control",
                  CONTROL_LEG, CONTROL_LEG, mt, key, done, last)
    run_test_all(key, done)
    print("probe: done -> %s" % OUT, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
