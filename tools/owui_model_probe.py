#!/usr/bin/env python3
"""OW-MODEL-TESTS (D-994): probe every model one OmniRoute-shape gateway lists.

Read-only against one gateway's OpenAI-compatible surface. It lists the models,
runs two calls per model, and writes a machine-readable row stream plus one
markdown verdict table. It never installs, never mutates the gateway, and is
safe to interrupt and re-run.

What it measures, per model id:
  1. chat   -- POST /v1/chat/completions, stream=true, the prompt
               "Reply OK and today's date (YYYY-MM-DD).", max_tokens 1024. The
               stream is timed, so the row carries the first-token latency and
               the total time, and whether any content came back.
  2. tool   -- POST /v1/chat/completions, non-stream, one offered function tool
               `get_time` with no arguments, the model asked to call it,
               max_tokens 1024. The row carries whether a tool call came back.

Exactly one call per test per model, run sequentially, each with its own
--timeout (default 120 s). --limit N probes only the first N models this run.

Secrets (AGENTS.md rule 1 -- this ships in a public repo):
  - The base URL is only ever what --base-url names. Nothing is hardcoded.
  - The client key comes ONLY from the environment variable
    AUTOOS_OMNIROUTE_KEY. It is never taken from argv, never printed, never
    written to any output file, and never echoed back on an error. It rides only
    in the in-memory Authorization header.
  - An error string (from a non-2xx body or a transport failure) is read only
    to classify the failure: control characters are stripped, bearer tokens /
    vendor key prefixes / `key=value` carriers / the literal env key are masked
    through tools/autoos_redact.py, and the result is truncated to 160 chars.

Rules this tool applies, documented here because the brief asked and because a
drifting rule is a wrong table:

  * alias skip -- /v1/models lists every id a gateway answers to, but some are
    aliases that resolve to another id. An entry is skipped when either:
      (a) the payload flags it: `alias` is truthy, or it names a different id
          through one of the pointer fields (alias_of / mapped_from /
          aliased_from / canonical / target); or
      (b) its target duplicates a target an earlier kept entry already claimed
          -- the entry's own id when no pointer is present, else the pointer.
    The first entry to claim a target is kept; every later entry on that target
    is treated as an alias and skipped. A plain list of distinct ids keeps them
    all. This is the rule `select_real_models` implements.

  * family heuristic -- a coarse provider tag from the id, for grouping the
    table; NOT the routing family any gatekeeper uses. With a `/` in the id the
    provider is the first segment, except that a route prefix (`omniroute`) is
    skipped and the next segment is the provider. The family is then the leading
    alphanumeric run of that segment (before its first `-` or `.`), so
    "deepseek/deepseek-chat" and "omniroute/deepseek-direct-flash" both read as
    "deepseek". With no `/` the same rule applies to the id itself, and an id
    with no leading token falls back to the payload's `owned_by`, else "-".

  * tier -- taken from /v1/models metadata when the entry exposes a `tier`
    field, else "-". Never guessed.

Usage:
    AUTOOS_OMNIROUTE_KEY=... python tools/owui_model_probe.py \
        --base-url https://<gateway-host> --out-dir logs/owui-probe \
        [--timeout 120] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import autoos_redact  # noqa: E402  stdlib-only, safe to import here

KEY_ENV = "AUTOOS_OMNIROUTE_KEY"

# The prompt of the streamed chat test. The date is asked for so a reply that
# is only a canned "OK" is still counted as an answer, but the row never stores
# the answer text -- only whether anything non-empty came back.
CHAT_PROMPT = "Reply OK and today's date (YYYY-MM-DD)."
# The tool test offers exactly this no-argument function and asks for it.
TOOL_NAME = "get_time"
TOOL_PROMPT = "Call the get_time tool to report the current time."
MAX_TOKENS = 1024

# A `/v1/models` entry naming one of these fields to point at a DIFFERENT id is
# an alias of that id, not a model of its own.
_ALIAS_POINTER_FIELDS = ("alias_of", "mapped_from", "aliased_from", "canonical", "target")
# A first id segment that names the gateway route, not a provider, is skipped
# when deriving the family (e.g. "omniroute/deepseek-chat" -> deepseek).
_ROUTE_PREFIXES = frozenset({"omniroute"})
# An error is stored no longer than this, after masking.
ERROR_MAX_CHARS = 160
# Bound how much of a (possibly huge) error body we read to classify it, so a
# runaway body cannot pin the run; the mask + truncate keep the rest out.
_ERROR_BODY_BYTES = 600

# The stored-row columns, in table order. One source of truth: rows.jsonl keys,
# the markdown header, and the count lines all read this.
ROW_FIELDS = ("id", "family", "tier", "chat_status", "answered",
              "first_token_ms", "total_ms", "tool_status", "tool_call", "error")
TABLE_HEADER = ("model id", "family", "tier", "chat", "answered",
                "first ms", "total ms", "tool call", "error")


# ---------------------------------------------------------------------------
# the /v1/models rules: alias skip, family, tier
# ---------------------------------------------------------------------------

def target_of(entry):
    """The id an entry resolves to: its alias pointer, else its own id."""
    for field in _ALIAS_POINTER_FIELDS:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return entry.get("id")


def is_flagged_alias(entry):
    """True when the payload itself says this entry is an alias."""
    if entry.get("alias"):
        return True
    own = entry.get("id")
    pointer = None
    for field in _ALIAS_POINTER_FIELDS:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            pointer = value.strip()
            break
    return pointer is not None and pointer != own


def select_real_models(data):
    """The model entries that are not aliases, in payload order.

    Skips (a) any entry flagged an alias and (b) any entry whose target was
    already claimed by an earlier kept entry -- the duplicate-target rule. See
    the module docstring.
    """
    kept = []
    claimed = set()
    for entry in data or []:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        if is_flagged_alias(entry):
            continue
        target = target_of(entry)
        if target in claimed:
            continue
        claimed.add(target)
        kept.append(entry)
    return kept


def family_of(entry):
    """Coarse provider tag from the id (see the module docstring)."""
    mid = entry.get("id") or ""
    if "/" in mid:
        parts = mid.split("/")
        first = parts[0]
        seg = parts[1] if (first.lower() in _ROUTE_PREFIXES and len(parts) > 1) else first
    else:
        seg = mid
    match = re.match(r"[A-Za-z0-9]+", seg)
    if match:
        return match.group(0).lower()
    return str(entry.get("owned_by") or "-").lower()


def tier_of(entry):
    """The declared tier, or '-' when the entry exposes none. Never guessed."""
    value = entry.get("tier")
    if value is None or value == "":
        return "-"
    return str(value)


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------

def chat_body(model_id):
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": CHAT_PROMPT}],
        "stream": True,
        "max_tokens": MAX_TOKENS,
    }


def tool_body(model_id):
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": TOOL_PROMPT}],
        "tools": [{
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Return the current server time.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }],
        "tool_choice": "auto",
        "stream": False,
        "max_tokens": MAX_TOKENS,
    }


# ---------------------------------------------------------------------------
# HTTP: the models list, the streamed chat call, the tool call
# ---------------------------------------------------------------------------

def _auth_headers(key):
    return {"Content-Type": "application/json", "Authorization": "Bearer " + key}


def models_url(base):
    return base.rstrip("/") + "/v1/models"


def chat_url(base):
    return base.rstrip("/") + "/v1/chat/completions"


def fetch_models(key, base, timeout):
    """The `data` list of /v1/models, or raise on any failure (no secret leaks)."""
    req = urllib.request.Request(models_url(base), headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    parsed = json.loads(raw)
    return parsed.get("data") or []


def stream_chat_call(key, body, url, timeout):
    """One streamed chat call: (status, answered, first_token_ms, total_ms, err).

    Times the stream so the row carries first-token latency. `answered` is true
    only when some delta carried non-empty content. A transport failure (or a
    mid-stream timeout) reads status 'ERR'.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_auth_headers(key))
    start = time.monotonic()
    first_token_ms = None
    content_any = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                delta = _chunk_delta(chunk)
                content = delta.get("content")
                if isinstance(content, str) and content:
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - start) * 1000)
                    content_any = True
        return status, "y" if content_any else "n", first_token_ms, \
            int((time.monotonic() - start) * 1000), None
    except urllib.error.HTTPError as exc:
        return exc.code, "n", None, int((time.monotonic() - start) * 1000), \
            "HTTP %d %s" % (exc.code, _read_body(exc))
    except Exception as exc:
        return "ERR", "n", None, int((time.monotonic() - start) * 1000), _transport_note(exc)


def json_call(key, body, url, timeout):
    """One non-stream call: (status, parsed_or_None, total_ms, err)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_auth_headers(key))
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None, int((time.monotonic() - start) * 1000), \
            "HTTP %d %s" % (exc.code, _read_body(exc))
    except Exception as exc:
        return "ERR", None, int((time.monotonic() - start) * 1000), _transport_note(exc)
    try:
        return status, json.loads(raw.decode("utf-8", "replace")), \
            int((time.monotonic() - start) * 1000), None
    except ValueError:
        return status, None, int((time.monotonic() - start) * 1000), "invalid JSON body"


def _chunk_delta(chunk):
    try:
        return chunk["choices"][0].get("delta") or {}
    except (KeyError, IndexError, TypeError, AttributeError):
        return {}


def tool_call_made(parsed):
    """True when the reply carries a tool call to the probe's `get_time`."""
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False
    for call in message.get("tool_calls") or []:
        try:
            if call["function"]["name"] == TOOL_NAME:
                return True
        except (KeyError, TypeError):
            continue
    return False


def _read_body(exc):
    try:
        return exc.read(_ERROR_BODY_BYTES).decode("utf-8", "replace")
    except Exception:
        return ""


def _transport_note(exc):
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    return "transport: %s" % type(exc).__name__


# ---------------------------------------------------------------------------
# the row: assemble, mask, persist
# ---------------------------------------------------------------------------

def mask_error(text, key):
    """A stored error: one line, secrets masked, first 160 chars.

    Control characters (CR/LF/tab) go first so the error stays a single line in
    JSONL and the markdown table; then the bearer/prefix/assignment shapes and
    the literal env key are masked through autoos_redact; then truncate. The key
    value is added to the mask list even when it is shorter than autoos_redact's
    literal floor, because the probe is the one that handed it over.
    """
    if not text:
        return ""
    values = [key] if key else []
    values += autoos_redact.secret_env_values(os.environ)
    cleaned = autoos_redact.sanitize_text(str(text))
    return autoos_redact.Redactor(values).text(cleaned)[:ERROR_MAX_CHARS]


def probe_model(key, base, entry, timeout):
    """Run both tests for one model entry and return its row dict."""
    mid = entry["id"]
    url = chat_url(base)
    chat_status, answered, first_ms, total_ms, chat_err = \
        stream_chat_call(key, chat_body(mid), url, timeout)
    tool_status, parsed, _tool_ms, tool_err = json_call(key, tool_body(mid), url, timeout)
    tool_call = "y" if (tool_status == 200 and parsed is not None and tool_call_made(parsed)) else "n"
    err = mask_error(chat_err or tool_err or "", key)
    return {
        "id": mid,
        "family": family_of(entry),
        "tier": tier_of(entry),
        "chat_status": chat_status,
        "answered": answered,
        "first_token_ms": first_ms,
        "total_ms": total_ms,
        "tool_status": tool_status,
        "tool_call": tool_call,
        "error": err,
    }


# ---------------------------------------------------------------------------
# resume + output
# ---------------------------------------------------------------------------

def rows_path(out_dir):
    return os.path.join(out_dir, "rows.jsonl")


def md_path(out_dir):
    return os.path.join(out_dir, "owui-models.md")


def read_done_ids(path):
    """Ids already recorded in rows.jsonl; an unparsable line is ignored."""
    done = []
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                done.append(rec["id"])
    return done


def load_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                rows.append({field: rec.get(field) for field in ROW_FIELDS})
    return rows


def append_row(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _listed(rows_or_ids, needle):
    """The ids carrying `needle` (case-insensitive substring)."""
    ids = [r["id"] if isinstance(r, dict) else r for r in rows_or_ids]
    return [i for i in ids if needle in str(i).lower()]


def render_md(rows, listed_ids):
    """The markdown verdict: header lines, counts, then one table."""
    antigravity = _listed(listed_ids, "antigravity")
    deepseek = _listed(listed_ids, "deepseek")
    total = len(rows)
    answered = sum(1 for r in rows if r.get("answered") == "y")
    tool_ok = sum(1 for r in rows if r.get("tool_call") == "y")

    lines = []
    lines.append("antigravity listed: %s (ids: %s)" % (
        "y" if antigravity else "n", ", ".join(sorted(antigravity)) or "-"))
    lines.append("deepseek listed: %s (ids: %s)" % (
        "y" if deepseek else "n", ", ".join(sorted(deepseek)) or "-"))
    lines.append("total: %d" % total)
    lines.append("answered: %d" % answered)
    lines.append("tool-call ok: %d" % tool_ok)
    lines.append("")
    lines.append("| " + " | ".join(TABLE_HEADER) + " |")
    lines.append("|" + "|".join(["---"] * len(TABLE_HEADER)) + "|")
    for r in rows:
        cells = [_cell(r.get("id")), _cell(r.get("family")), _cell(r.get("tier")),
                 _cell(r.get("chat_status")), _cell(r.get("answered")),
                 _cell(r.get("first_token_ms")), _cell(r.get("total_ms")),
                 _cell(r.get("tool_call")), _cell(r.get("error"))]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _cell(value):
    """One table cell: never a pipe or a newline, so a row stays a row."""
    text = "-" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Probe every model a gateway lists.")
    ap.add_argument("--base-url", required=True,
                    help="gateway base URL (https://host); /v1/* is appended")
    ap.add_argument("--out-dir", required=True,
                    help="where rows.jsonl and owui-models.md are written")
    ap.add_argument("--timeout", type=int, default=120, help="per-call timeout in seconds")
    ap.add_argument("--limit", type=int, default=0, help="probe only the first N models this run")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    key = os.environ.get(KEY_ENV, "")
    if not key:
        # Never print the value -- the variable name is all a caller may see.
        print("owui_model_probe: %s is not set" % KEY_ENV, file=sys.stderr)
        return 3

    os.makedirs(args.out_dir, exist_ok=True)
    rpath = rows_path(args.out_dir)

    try:
        data = fetch_models(key, args.base_url, args.timeout)
    except urllib.error.HTTPError as exc:
        print("owui_model_probe: /v1/models -> HTTP %d" % exc.code, file=sys.stderr)
        return 4
    except Exception:
        print("owui_model_probe: /v1/models request failed", file=sys.stderr)
        return 4

    entries = select_real_models(data)
    listed_ids = [e["id"] for e in entries]
    done = set(read_done_ids(rpath))

    todo = [e for e in entries if e["id"] not in done]
    if args.limit and args.limit > 0:
        todo = todo[:args.limit]

    for entry in todo:
        row = probe_model(key, args.base_url, entry, args.timeout)
        append_row(rpath, row)
        print(row["id"], flush=True)

    with open(md_path(args.out_dir), "w", encoding="utf-8") as fh:
        fh.write(render_md(load_rows(rpath), listed_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
