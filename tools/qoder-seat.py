#!/usr/bin/env python3
"""qoder-seat - run one headless, tool-less qodercli call and write a seat directory.

GW-QODER-401. Standard library only. The prompt is a self-contained review brief;
it goes to the client on STDIN (never on argv), the client runs with no tools and
no session persistence, and the LAST JSON object line of stdout is parsed.

Output (both created only after a successful run, never on failure):

    <out-dir>/review.txt     the model result, verbatim, UTF-8
    <out-dir>/evidence.json  verbatim client fields + run facts (D-2494: no remapping)

Exit codes: 0 ok, 1 client/runtime failure, 2 refusal BEFORE the review call
(prompt banner, out-dir already holding a seat, leftover .partial-*, client
path, failed persistence probe), 3 client reply not usable (is_error, wrong
type, empty result, modelUsage shape, truncated stop_reason, unknown verdict
word, non-UTF-8 result).

Refusals that never publish a seat, each quoting the client's own words in the
failure note (the note is never published as seat output):

    * the killed client that does not exit within KILL_WAIT=10s of the kill
      -> exit 1 "client did not exit within 10s of the kill";
    * stop_reason other than end_turn/stop (a truncated reply)
      -> exit 3 with the verbatim stop_reason;
    * an unknown verdict word in the final VERDICT line
      -> exit 3 with the verbatim line;
    * a failed session-persistence probe
      -> exit 2: unknown is not "disabled", so the run never starts.

Evidence fields (D-2494: client values verbatim, a missing field stays null):
usage, modelUsage, session_id, subtype, num_turns are the client's own;
finish_reason is the client's stop_reason; request_id is the top-level field,
else usage.request_id, else null; served_model is the sole modelUsage key;
argv is the FULL argv handed the client; no_session_persistence is the probe's
True/False (a failed probe refuses instead); verdict_parse is "known" (an
unknown word refuses instead); test_gaming is the LAST TEST-GAMING line (an
echoed brief example must not shadow the seat's own answer) or null.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import signal
import tempfile
from autoos_redact import redact_argv
from datetime import datetime, timezone
from pathlib import Path

BANNER = "You have NO tools and no repository access. Everything you need is below. Answer in plain text only."
DEFAULT_MODEL = "qwen3.8-flash"
DEFAULT_TIMEOUT = 900
KILL_WAIT = 10  # hard bound on the post-kill wait: a client that holds the pipes past this is reported, never waited on
TEST_GAMING_RE = re.compile(r"^\s*TEST-GAMING:\s*(yes|no|unsure)\b", re.IGNORECASE | re.MULTILINE)
VERDICT_RE = re.compile(r"^\s*VERDICT:.*$", re.MULTILINE | re.IGNORECASE)
KNOWN_VERDICT_WORDS = frozenset({
    "APPROVE", "ACCEPT", "PASS", "REJECT", "FIX", "HALT", "CHANGES",
})


class ClientDidNotExit(subprocess.SubprocessError):
    """The killed client (or a descendant holding its pipes) never exited."""


def fail(code: int, message: str) -> "NoReturn":  # noqa: F821 - py<3.11 typing hint only
    print(f"qoder-seat: error: {redact_argv([message])[0]}", file=sys.stderr)
    sys.exit(code)


def resolve_qodercli(explicit: str | None) -> str:
    """PATH first, then the per-user install under the runtime home."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            fail(2, f"--qodercli not found: {explicit}")
        return str(path)
    found = shutil.which("qodercli")
    if found:
        return found
    home = Path.home()  # runtime home: USERPROFILE/HOME of this process, never hard-coded
    for candidate in (
        home / ".qoder" / "bin" / "qodercli" / "qodercli",
        home / ".qoder" / "bin" / "qodercli" / "qodercli.exe",
    ):
        if candidate.exists():
            return str(candidate)
    fail(2, "qodercli not found on PATH and not under ~/.qoder/bin/qodercli (use --qodercli)")


def spawn_prefix(executable: str) -> list[str]:
    """Use executables directly; never re-quote an empty tool argument via cmd."""
    path = Path(executable)
    if os.name == "nt" and path.suffix.lower() in (".cmd", ".bat"):
        native = path.with_suffix(".exe")
        if native.is_file():
            return [str(native)]
        fail(2, "batch clients are unsafe for empty argv; use --qodercli with the resolved executable")
    if path.suffix.lower() == ".py":
        return [sys.executable, executable]
    return [executable]


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the client and its process group/tree once the timeout fired."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    else:
        os.killpg(proc.pid, signal.SIGKILL)


def run_client(cmd: list[str], timeout: int, prompt: bytes | None = None):
    """Bound the whole process tree, including descendants holding output pipes."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=os.name != "nt")
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            # A descendant still holds the pipes after the kill: the wait for
            # them is bounded too, so the adapter reports it instead of hanging.
            raise ClientDidNotExit(
                f"client did not exit within {KILL_WAIT}s of the kill"
            ) from None
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def probe_no_session_persistence(executable: str) -> bool | None:
    """A failed probe is unknown, not evidence that persistence is disabled."""
    try:
        proc = run_client(spawn_prefix(executable) + ["--help"], 15)
        if proc.returncode:
            raise OSError("help failed")
    except (OSError, subprocess.SubprocessError):
        print("qoder-seat: warning: session-persistence probe failed; persistence status unknown", file=sys.stderr)
        return None
    return b"--no-session-persistence" in (proc.stdout + proc.stderr)


def last_json_object(stdout: bytes) -> dict | None:
    """The last stdout line that parses as a JSON object (noise lines tolerated)."""
    found = None
    for raw in stdout.split(b"\n"):
        line = raw.strip()
        if not line.startswith(b"{"):
            continue
        try:
            data = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            found = data
    return found


def extract_test_gaming(text: str) -> str | None:
    """The LAST match: a TEST-GAMING line echoed from the brief must not shadow
    the seat's own answer later in the text (and a later "yes" is the
    fail-closed direction). A text with no line at all stays null."""
    matches = list(TEST_GAMING_RE.finditer(text))
    return matches[-1].group(1).lower() if matches else None


def extract_verdict(text: str) -> str | None:
    lines = text.rstrip().splitlines()
    return lines[-1].strip() if lines and VERDICT_RE.fullmatch(lines[-1]) else None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qoder-seat.py",
        description="Run one tool-less qodercli call and write a seat directory.",
    )
    parser.add_argument("--prompt-file", required=True, help="prompt file; first line must be the NO-tools banner")
    parser.add_argument("--out-dir", required=True, help="created; must not already hold review.txt/evidence.json")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"requested model (default: {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--qodercli", default=None, help="client executable (default: PATH, else ~/.qoder/bin/qodercli/qodercli)")
    parser.add_argument("--item", required=True, help="plan item recorded in evidence.json")
    parser.add_argument("--head", required=True, help="commit head recorded in evidence.json")
    args = parser.parse_args(argv)
    if not args.item.strip() or not args.head.strip():
        parser.error("--item and --head must be non-empty")
    if args.timeout <= 0:
        parser.error("--timeout must be a positive number of seconds")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    # --- prompt: read and validate BEFORE anything is created -------------------
    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_file():
        return fail(2, f"--prompt-file not found: {args.prompt_file}")
    prompt_bytes = prompt_path.read_bytes()
    try:
        prompt_text = prompt_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return fail(2, f"--prompt-file is not valid UTF-8: {args.prompt_file}")
    lines = prompt_text.splitlines()
    first_line = lines[0] if lines else ""
    if first_line != BANNER:
        return fail(
            2,
            "prompt first line must be exactly: "
            f"{BANNER!r} (got {first_line!r}); no files were written",
        )

    # --- out-dir: refuse a directory that already holds seat output --------------
    out_dir = Path(args.out_dir)
    if out_dir.exists():
        for name in ("review.txt", "evidence.json"):
            if (out_dir / name).exists():
                return fail(2, f"--out-dir already contains {name}: {out_dir}; partial seat: move it aside to recover; no files were written")
    # A leftover .partial-* is recovery work: refuse BEFORE the client runs, so
    # a stale staging directory never costs a review call to discover.
    if list(out_dir.parent.glob(out_dir.name + ".partial-*")):
        return fail(2, "leftover partial seat detected; move the .partial-* directory aside to recover; no files were written")

    executable = resolve_qodercli(args.qodercli)
    with_persistence_flag = probe_no_session_persistence(executable)
    if with_persistence_flag is None:
        # Unknown is not "disabled": never run a client whose session
        # persistence status could not be determined.
        return fail(2, "session-persistence probe failed; refusing to run a client "
                       "whose session-persistence status is unknown; no files were written")

    cmd = spawn_prefix(executable) + ["-p", "-o", "json", "--tools", "", "--model", args.model]
    if with_persistence_flag:
        cmd.append("--no-session-persistence")

    started = time.monotonic()
    try:
        proc = run_client(cmd, args.timeout, prompt_bytes)
    except ClientDidNotExit as exc:
        return fail(1, f"{exc}; no evidence was written")
    except subprocess.TimeoutExpired:
        return fail(1, f"qodercli timed out after {args.timeout}s; no evidence was written")
    except OSError as exc:
        return fail(1, f"could not run {executable}: {exc}")
    secs = round(time.monotonic() - started, 3)

    if proc.returncode != 0:
        # Redact BEFORE truncation: slicing first can remove a credential prefix.
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        detail = redact_argv([detail])[0][-600:] if detail else "(no output)"
        return fail(1, f"qodercli exited {proc.returncode}: {detail}; no evidence was written")

    data = last_json_object(proc.stdout or b"")
    if data is None:
        return fail(3, "no JSON object line in stdout")
    if data.get("is_error"):
        return fail(3, "client returned is_error")
    if data.get("type") != "result":
        return fail(3, f"expected type 'result', got {data.get('type')!r}")
    result = data.get("result")
    if not isinstance(result, str) or not result.strip():
        return fail(3, "result text is empty")
    usage = data.get("modelUsage")
    if not isinstance(usage, dict) or len(usage) != 1:
        return fail(3, f"modelUsage must hold exactly one model, got {usage!r}")
    served_model = next(iter(usage))

    # A truncated reply never publishes: end_turn and stop are the only two
    # completed reasons, and the note quotes the client's own word verbatim.
    stop_reason = data.get("stop_reason")
    if stop_reason not in ("end_turn", "stop"):
        return fail(3, f"client reply is truncated: stop_reason={stop_reason} "
                       "(only end_turn and stop publish); no evidence was written")

    verdict = extract_verdict(result)
    if verdict is None:
        return fail(3, "review must end with a VERDICT line")
    word = verdict.split(":", 1)[1].strip().upper()
    if word not in KNOWN_VERDICT_WORDS:
        # An unknown word refuses with the line quoted verbatim; it is never
        # published and never softened into a verdict_parse marker.
        return fail(3, f"unknown verdict word in the final line: {verdict}; no evidence was written")
    verdict_parse = "known"  # an unknown word refused above; the key is the contract pin
    try:
        review_bytes = result.encode("utf-8")
    except UnicodeEncodeError:
        return fail(3, "review result is not encodable as UTF-8; no evidence was written")
    usage_envelope = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    request_id = data.get("request_id") or usage_envelope.get("request_id")
    evidence = {
        "usage": data.get("usage"),
        "modelUsage": data.get("modelUsage"),
        "argv": cmd,
        "no_session_persistence": with_persistence_flag,
        "verdict_parse": verdict_parse,
        "served_model": served_model,
        "finish_reason": stop_reason,
        "request_id": request_id,
        "session_id": data.get("session_id"),
        "subtype": data.get("subtype"),
        "num_turns": data.get("num_turns"),
        "requested_model": args.model,
        "client": "qodercli",
        "item": args.item,
        "head": args.head,
        "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "prompt_bytes": len(prompt_bytes),
        "review_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "review_bytes": len(review_bytes),
        "test_gaming": extract_test_gaming(result),
        "verdict_line": extract_verdict(result),
        "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "secs": secs,
    }
    evidence_bytes = (json.dumps(evidence, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=out_dir.name + ".partial-", dir=out_dir.parent))
    try:
        (staging / "review.txt").write_bytes(review_bytes)
        (staging / "evidence.json").write_bytes(evidence_bytes)
        if out_dir.exists():
            out_dir.rmdir()  # Only an empty directory is safe to replace.
        staging.rename(out_dir)
    except OSError:
        return fail(1, "atomic seat publish failed; move the .partial-* directory aside to recover")

    print(
        f"qoder-seat: out_dir={out_dir} served_model={served_model} "
        f"finish_reason={evidence['finish_reason']} verdict_line={evidence['verdict_line']} "
        f"test_gaming={evidence['test_gaming']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
