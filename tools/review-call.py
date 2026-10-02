#!/usr/bin/env python3
"""One sanctioned gateway review call: a single tool-less chat completion (D-439/D-440).

Run it ONLY through `exec`, the one form that puts the OmniRoute client key in
this process's environment (D-370) and nowhere else:

    python3 tools/autoos_gateway_key.py exec -- python3 tools/review-call.py \
        --model ovh/gpt-oss-120b --prompt-file prompt.md --out-dir out \
        [--title my-review] [--max-tokens 16000] [--gateway-url URL] \
        [--temperature 0.2]

This tool never reads configuration/api-keys.yml, never resolves a key, and
never prints the key, the request headers or the prompt. The key comes ONLY
from the env var AUTOOS_OMNIROUTE_KEY that `exec` set; missing -> exit 2 with
`run me via autoos_gateway_key.py exec -- ...`.

Gateway URL: --gateway-url, else AUTOOS_OMNIROUTE_URL, else
http://127.0.0.1:20128/v1 - normalized by the same rule as autoos-agent.py's
gateway_base_url() (a URL with no path gains `/v1`; a reverse-proxied gateway
that already carries a path keeps it). autoos-agent.py itself is NOT imported:
it is not importable without dragging its whole module-level dependency graph
(registry, resolver, overlay, ...) into this one-shot tool, so its request
header keys and `<tag>/<run-id>` value are replicated EXACTLY instead.

The call: POST <base>/chat/completions with `stream: false`, NO `tools`,
NO `temperature` (unless `--temperature FLOAT` is given), `max_tokens`
(default 16000 - RC-2: reasoning models burned 6.2k completion tokens on an
11k prompt, so 4096 cut the answer) and the prompt as one user message.
temperature is OFF by
default because of a MEASURED defect on the central OmniRoute 2026-10-02
(ovh/gpt-oss-120b): a request carrying `"temperature": 0` is cut at 64
completion tokens (finish_reason 'length', content empty - the reasoning
model spends them on reasoning) regardless of max_tokens /
max_completion_tokens, while the SAME request WITHOUT temperature finishes
normally ('stop', full content) - so sending temperature is opt-in via
`--temperature`. The request stamps the spawner's own attribution headers -
`x-omniroute-session-id: review/<title or prompt-sha12>/<run-id>` and
`X-AutoOS-Run-Id: <run-id>` - so the gateway call log attributes the call.

Written into --out-dir:
    review.txt     the answer text (the key masked, should it ever echo back)
    evidence.json  requested_model, served_model, status, correlation_id (the
                   ONLY response header VALUE recorded), response_header_names
                   (names only), finish_reason, verdict (the D-337 parse: pass
                   / fail-with-findings / null), session_tag, run_id,
                   prompt_sha256, tokens (usage), started/finished (UTC)
    error.json     only when the gateway answers non-200 -> exit 3

Printed, and nothing else: the two paths, the served model, and the answer's
verdict normalised as `VERDICT: pass` / `VERDICT: fail-with-findings` (or
`VERDICT: missing`).

Exit codes: 0 ok; 2 missing key / bad arguments (message on stderr); 3 the
gateway answered non-200 (error.json written); 4 the gateway could not be
reached or answered something unparseable, OR the answer was truncated/empty
(finish_reason 'length' or no answer text: evidence.json is still written with
its finish_reason, `review-call: answer truncated/empty (finish_reason=<x>)`
goes to stderr, and the success lines are not printed).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

GATEWAY_ENV_VAR = "AUTOOS_OMNIROUTE_URL"
KEY_ENV_VAR = "AUTOOS_OMNIROUTE_KEY"
DEFAULT_GATEWAY = "http://127.0.0.1:20128/v1"
DEFAULT_MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 300
MAX_ERROR_BODY = 4000

# The exact message D-440 names: a key that did not come through `exec` is a
# usage error, and the fix is the one command line that sets it.
MISSING_KEY_MESSAGE = "run me via autoos_gateway_key.py exec -- ..."

# Request header keys and value shape, copied EXACTLY from
# tools/autoos-agent.py (SESSION_TAG_HEADER / RUN_ID_HEADER / session_header_value
# / RUN_ID_RE / RUN_ID_SLUG_CAP) so the gateway's call_logs attribute this call
# the same way they attribute a spawned run's legs.
SESSION_TAG_HEADER = "x-omniroute-session-id"
RUN_ID_HEADER = "X-AutoOS-Run-Id"
SESSION_TAG_MAX_LEN = 120
SESSION_TAG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,%d}$" % SESSION_TAG_MAX_LEN)
OMNIROUTE_SESSION_ID_MAX = 128
RUN_ID_SLUG_CAP = 24
RUN_ID_RE = re.compile(r"^(\d{8}-\d{6})-([a-z0-9]+(?:-[a-z0-9]+)*)-([0-9a-f]{6})$")

# Response headers that may carry the gateway's correlation id. OmniRoute has no
# single documented spelling (`call_logs.correlation_id` falls back to the
# request's traceId), so the first present header of any of these names wins and
# only ITS value is recorded; every other response header contributes its NAME
# to evidence.json and nothing else.
CORRELATION_HEADER_NAMES = (
    "x-correlation-id",
    "correlation-id",
    "x-omniroute-correlation-id",
    "x-request-id",
    "request-id",
)

# --- the verdict parse, COPIED from tools/autoos_agent_mcp.py ---------------
# Its D-337 rules (`_VERDICT_LINE_RE` ~line 910, `_verdict_value` ~line 940,
# `review_verdict` ~line 962). That module is NOT imported here - it pulls the
# whole MCP stack into this one-shot tool (the same reason autoos-agent.py is
# replicated instead of imported, see the header) - so the needed logic is
# copied verbatim, changing only the value set: a review call answers `pass` /
# `fail-with-findings`, never the runner's `ready` / `fix-first` / `not-ready`.

# The verdict line a review answer states, anchored at the line start so a
# sentence that merely mentions a verdict does not read as one. The decoration
# it tolerates is what a markdown-speaking reviewer wraps a real decision in:
# a heading, a bullet, or bold on the label - both `**VERDICT**: pass` and the
# colon-inside form `**Verdict:** pass` (the closing `**` sits before the
# colon, which the optional `\*\*` in front of `:` accepts).
_VERDICT_LINE_RE = re.compile(
    r"(?i)^\s*(?:#{1,6}\s*|[-*+]\s+)*(?:\*\*)?VERDICT(?:\*\*)?\s*:\s*(\S.*)$")

# What a verdict IS for a review call. A line that opens with the label and
# then says something else (`VERDICT: pass, but the ref is never read`) is a
# reviewer *talking*, and grading it as pass merges the very review that
# raised a finding.
_VERDICT_VALUES = ("pass", "fail-with-findings")

# A fence opener, at markdown's own indentation: up to 3 spaces of leading
# space and then three or more ` or ~. A closing fence is the opener's OWN
# character, at least as long, and nothing but whitespace after it (CommonMark
# - `~~~` does not close a ``` block and ' ```' does not close ' ````').
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")

# A unified-diff hunk header; the b/d line counts decide how far the hunk body
# reaches. Inside that body nothing is markdown: a `+VERDICT: pass` line is the
# DIFFED FILE's text, not the reviewer's (VERDICTFENCE-R2 (b)).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def utc_now() -> str:
    """One UTC stamp format for both evidence times."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gateway_base_url(url: str) -> str:
    """The gateway address as an API root - autoos-agent.py gateway_base_url()'s
    rule, replicated: no path means `/v1`, an existing path is kept."""
    parts = urllib.parse.urlsplit((url or "").rstrip("/"))
    path = parts.path if parts.path not in ("", "/") else "/v1"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def slugify(text: str, cap: int = 40) -> str:
    """autoos-agent.py slugify(), replicated: [a-z0-9-], capped, never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:cap].strip("-")
    return slug or "task"


def session_tag(title: str | None, prompt_sha256: str) -> str:
    """`review/<title>` when the title is header-safe, else `review/<prompt-sha12>`.

    The tag must survive OmniRoute's 128-char (and our own 120-char) charset
    check as the `<tag>/<run-id>` header value, so an unusable title falls back
    to the prompt fingerprint rather than being rewritten.
    """
    candidate = (title or "").strip()
    if candidate and SESSION_TAG_RE.match("review/" + candidate):
        return "review/" + candidate
    return "review/" + prompt_sha256[:12]


def mint_run_id(slug: str) -> str:
    """Same shape the spawner mints (autoos-agent.py mint_run_id, always UTC):
    `YYYYMMDD-HHMMSS-<slug>-<hex6>`."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return "%s-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), slug, secrets.token_hex(3))


def session_header_value(tag: str, run_id: str) -> str:
    """`<tag>/<run-id>`; a pair past the gateway's 128-char cap goes alone
    (with a warning, exactly as the spawner behaves - a silently truncated run
    id would make every run of the lane share one conversation)."""
    if not run_id:
        return tag
    combined = "%s/%s" % (tag, run_id)
    if len(combined) <= OMNIROUTE_SESSION_ID_MAX:
        return combined
    print("review-call: session tag %r + run id would pass the gateway's %d-char "
          "%s cap, sending the tag alone" % (tag, OMNIROUTE_SESSION_ID_MAX,
                                             SESSION_TAG_HEADER), file=sys.stderr)
    return tag


def request_headers(key: str, tag: str, run_id: str) -> dict:
    """The one Authorization header plus the spawner's attribution pair."""
    return {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        SESSION_TAG_HEADER: session_header_value(tag, run_id),
        RUN_ID_HEADER: run_id,
    }


def request_body(model: str, prompt: str, max_tokens: int,
                 temperature: float | None = None) -> dict:
    """One tool-less completion: stream off, no `tools`, no `temperature`
    unless the caller explicitly asked for one.

    temperature is deliberately absent by default: MEASURED on the central
    OmniRoute 2026-10-02 (ovh/gpt-oss-120b), a request carrying
    `"temperature": 0` is cut at 64 completion tokens (finish_reason 'length',
    content empty) regardless of max_tokens / max_completion_tokens, while the
    SAME request without temperature finishes normally - so the gateway would
    otherwise always yield an empty review. Send it only via `--temperature`.

    Key is deliberately absent - a review call never asks the model to act.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        body["temperature"] = temperature
    return body


def answer_text(payload: dict) -> str:
    """The assistant message's text, accepting the string and list-of-parts
    shapes OpenAI-compatible gateways use for `content`."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def finish_reason_of(payload: dict) -> str | None:
    """choices[0].finish_reason verbatim, or None when the response carries
    none - recorded in evidence.json on EVERY run so a cut answer (see
    request_body) is diagnosable after the fact."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    reason = choices[0].get("finish_reason")
    return reason if isinstance(reason, str) else None


def _verdict_value(stripped: str) -> str | None:
    """The normalised verdict word a single line states, or None.

    Copied from tools/autoos_agent_mcp.py `_verdict_value` (D-337 SB-A2 item
    D), revalued for this tool: a `>`-quoted line is someone else's text, a
    line carrying `<` or `|` is template syntax (the brief echoed back / a
    table row), and the value has to be the bare word - `pass, but ...` is a
    reviewer *talking* and reads as NO verdict. Bold/italic decoration and one
    trailing `. ! ?` (`VERDICT: pass.`) are stripped before the test, so
    `**Verdict:** pass` and `**VERDICT**: pass` both land on `pass`.
    """
    if stripped.startswith(">") or "<" in stripped or "|" in stripped:
        return None
    m = _VERDICT_LINE_RE.match(stripped)
    if not m:
        return None
    value = m.group(1)
    # Twice: `pass.` needs the punctuation then the decorators, while
    # `**pass**.` needs a decorator round after the punctuation falls off.
    for _ in range(2):
        value = value.strip().strip("*_` ").strip()
        if value[-1:] in (".", "!", "?"):
            value = value[:-1]
    value = value.strip().lower()
    return value if value in _VERDICT_VALUES else None


def review_verdict(text: str) -> str | None:
    """The answer's verdict (`pass` / `fail-with-findings`), or None.

    Copied from tools/autoos_agent_mcp.py `review_verdict` (D-337): the LAST
    qualifying line wins - a reviewer that changed its mind said so - a
    verdict line inside a fenced code block or a unified-diff hunk body is not
    the reviewer's own word, a fence still open at the END of the text fails
    CLOSED (ignore everything from the first fence marker on: the answer was
    cut mid-block, so nothing in it is trusted), and fences follow CommonMark.
    """
    raws = [_ANSI_RE.sub("", line) for line in (text or "").splitlines()]
    found = None               # last verdict outside fences and hunks
    found_before_fence = None  # ...and before the text's first fence marker
    fence = None               # (char, length) while a block is open
    first_marker = None
    hunk = None                # (old, new) lines left in the active hunk body
    for i, raw in enumerate(raws):
        if hunk is not None:
            old, new = hunk
            if old <= 0 and new <= 0:
                hunk = None
            elif raw.startswith(" "):
                hunk = (old - 1, new - 1)
                continue
            elif raw.startswith("-"):
                hunk = (old - 1, new)
                continue
            elif raw.startswith("+"):
                hunk = (old, new - 1)
                continue
            elif raw.startswith("\\"):
                continue  # "\ No newline at end of file" counts toward neither
            elif raw == "":
                # a blank context line that lost its single leading space to a
                # trailing-whitespace strip is still hunk content
                hunk = (old - 1, new - 1)
                continue
            else:
                hunk = None
        if fence is not None:
            m = _FENCE_CLOSE_RE.match(raw)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
            continue
        if raw.startswith(("diff --git", "index ", "---", "+++")):
            continue
        m = _HUNK_RE.match(raw)
        if m:
            hunk = (int(m.group(1) or 1), int(m.group(2) or 1))
            continue
        m = _FENCE_OPEN_RE.match(raw)
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            if first_marker is None:
                first_marker = i
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        value = _verdict_value(stripped)
        if value:
            found = value
            if first_marker is None:
                found_before_fence = value
    return found_before_fence if fence is not None else found


def verdict_line(text: str) -> str:
    """The one stdout verdict line: `VERDICT: pass` / `VERDICT:
    fail-with-findings` / `VERDICT: missing` (the parse is review_verdict's)."""
    return "VERDICT: " + (review_verdict(text) or "missing")


def correlation_id(headers) -> tuple:
    """(value or None, [header names as received]).

    Only the correlation header's value is ever recorded; everything else
    contributes a name.
    """
    items = [(name, value) for name, value in headers.items()]
    names = dict((name.lower(), value) for name, value in items)
    for candidate in CORRELATION_HEADER_NAMES:
        if candidate in names and names[candidate]:
            return names[candidate], [name for name, _ in items]
    for name, value in items:
        if "correlation" in name.lower() and value:
            return value, [n for n, _ in items]
    return None, [name for name, _ in items]


def mask_key(text: str, key: str) -> str:
    """Last line of defence: model- or gateway-supplied output that echoes the
    key back is redacted before anything is written."""
    return text.replace(key, "[redacted]") if key else text


def parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="review-call.py",
        description="One tool-less gateway review call; run it via "
                    "autoos_gateway_key.py exec -- ... (D-440).")
    parser.add_argument("--model", required=True,
                        help="OmniRoute model id, e.g. ovh/gpt-oss-120b")
    parser.add_argument("--prompt-file", required=True,
                        help="file whose exact contents are the single user message")
    parser.add_argument("--out-dir", required=True,
                        help="where review.txt / evidence.json are written")
    parser.add_argument("--title", default=None,
                        help="session slug: review/<title>; falls back to "
                             "review/<prompt-sha12>")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help="max_tokens for the completion (default %d)"
                             % DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=None,
                        help="send `temperature` with this value; OFF by "
                             "default - measured on this gateway, a "
                             "temperature-0 request is cut at 64 completion "
                             "tokens (finish_reason 'length', empty answer)")
    parser.add_argument("--gateway-url", default=None,
                        help="gateway API root; overrides %s and the default"
                             % GATEWAY_ENV_VAR)
    args = parser.parse_args(argv)
    if args.max_tokens <= 0:
        parser.error("--max-tokens must be a positive integer")
    return args


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    # The key exists ONLY where `exec` put it. No file, no fallback, no prompt.
    key = (os.environ.get(KEY_ENV_VAR) or "").strip()
    if not key:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return 2

    try:
        with open(args.prompt_file, "rb") as handle:
            raw_prompt = handle.read()
    except OSError as exc:
        print("review-call: cannot read --prompt-file: %s"
              % mask_key(str(exc), key), file=sys.stderr)
        return 2
    try:
        prompt = raw_prompt.decode("utf-8")
    except UnicodeDecodeError:
        print("review-call: --prompt-file is not valid UTF-8", file=sys.stderr)
        return 2

    try:
        os.makedirs(args.out_dir, exist_ok=True)
    except OSError as exc:
        print("review-call: cannot create --out-dir: %s"
              % mask_key(str(exc), key), file=sys.stderr)
        return 2

    prompt_sha256 = hashlib.sha256(raw_prompt).hexdigest()
    tag = session_tag(args.title, prompt_sha256)
    run_id = mint_run_id(slugify(args.title or "review", RUN_ID_SLUG_CAP))
    base = gateway_base_url(args.gateway_url
                            or os.environ.get(GATEWAY_ENV_VAR)
                            or DEFAULT_GATEWAY)
    url = base.rstrip("/") + "/chat/completions"

    request = urllib.request.Request(
        url,
        data=json.dumps(request_body(args.model, prompt, args.max_tokens,
                                     args.temperature))
             .encode("utf-8"),
        headers=request_headers(key, tag, run_id),
        method="POST",
    )

    started = utc_now()
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            names = [name for name, _ in response.headers.items()]
            raw_body = response.read()
    except urllib.error.HTTPError as exc:
        # Non-200 is a recorded outcome, not a crash: the error file carries the
        # status and a (key-masked, truncated) body, and we exit 3 (D-440).
        try:
            raw_error = exc.read()
        except OSError:
            raw_error = b""
        header_names = [name for name, _ in exc.headers.items()] \
            if exc.headers is not None else []
        error_doc = {
            "status": int(exc.code),
            "reason": str(exc.reason),
            "body": mask_key(raw_error.decode("utf-8", "replace"),
                             key)[:MAX_ERROR_BODY],
            "requested_model": args.model,
            "response_header_names": header_names,
            "session_tag": tag,
            "run_id": run_id,
            "prompt_sha256": prompt_sha256,
            "started": started,
            "finished": utc_now(),
        }
        error_path = os.path.join(args.out_dir, "error.json")
        try:
            with open(error_path, "w", encoding="utf-8") as handle:
                json.dump(error_doc, handle, indent=2)
                handle.write("\n")
        except OSError as exc:
            print("review-call: cannot write error.json: %s"
                  % (exc.strerror or exc), file=sys.stderr)
        return 3
    except urllib.error.URLError as exc:
        print("review-call: gateway unreachable: %s"
              % mask_key(str(getattr(exc, "reason", exc)), key), file=sys.stderr)
        return 4
    except OSError as exc:
        print("review-call: gateway call failed: %s"
              % mask_key(str(exc), key), file=sys.stderr)
        return 4

    finished = utc_now()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("response is not a JSON object")
    except (UnicodeDecodeError, ValueError) as exc:
        print("review-call: unparseable gateway response: %s"
              % mask_key(str(exc), key), file=sys.stderr)
        return 4

    served_model = payload.get("model")
    if served_model is not None and not isinstance(served_model, str):
        served_model = str(served_model)
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    cid, header_names = correlation_id(response.headers)
    answer = answer_text(payload)
    text = mask_key(answer, key)
    finish = finish_reason_of(payload)
    tokens = dict(usage)
    # The same parse the stdout line prints, captured as a machine-readable
    # field: pass / fail-with-findings / null (null, never a sentinel word).
    verdict = review_verdict(text)

    review_path = os.path.join(args.out_dir, "review.txt")
    evidence_path = os.path.join(args.out_dir, "evidence.json")
    evidence = {
        "requested_model": args.model,
        "served_model": served_model,
        "status": int(status),
        "finish_reason": finish,
        "verdict": verdict,
        "correlation_id": cid,
        "response_header_names": header_names,
        "session_tag": tag,
        "run_id": run_id,
        "prompt_sha256": prompt_sha256,
        "tokens": tokens,
        "started": started,
        "finished": finished,
    }
    try:
        with open(review_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        with open(evidence_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(evidence, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        print("review-call: cannot write output: %s"
              % mask_key(str(exc), key), file=sys.stderr)
        return 4

    # Truncated or absent answer: evidence.json already carries finish_reason,
    # so say which on stderr and exit 4 instead of printing a `VERDICT:
    # missing` success line an empty review.txt would be read as.
    if finish == "length" or not answer:
        print("review-call: answer truncated/empty (finish_reason=%s)"
              % (finish or "unknown"), file=sys.stderr)
        return 4

    # The ONLY stdout of a successful run: two paths, the served model, the
    # verdict line. Never the key, never the request headers, never the prompt.
    print("review.txt: " + review_path)
    print("evidence.json: " + evidence_path)
    print("model: " + (served_model if served_model is not None else args.model))
    print(verdict_line(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
