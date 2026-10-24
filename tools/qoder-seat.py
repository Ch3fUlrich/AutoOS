#!/usr/bin/env python3
"""qoder-seat - run one headless, tool-less qodercli call and write a seat directory.

GW-QODER-401. Standard library only. The prompt is a self-contained review brief;
it goes to the client on STDIN (never on argv), the client runs with no tools and
no session persistence, and the LAST JSON object line of stdout is parsed.

Output (both created only after a successful run, never on failure):

    <out-dir>/review.txt     the model result, verbatim, UTF-8
    <out-dir>/evidence.json  verbatim client fields + run facts (D-2494: no remapping)

Exit codes: 0 ok, 1 client/runtime failure, 2 usage (banner, out-dir, client path),
3 client reply not usable (is_error, wrong type, empty result, modelUsage shape).
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
TEST_GAMING_RE = re.compile(r"^\s*TEST-GAMING:\s*(yes|no|unsure)\b", re.IGNORECASE | re.MULTILINE)
VERDICT_RE = re.compile(r"^\s*VERDICT:.*$", re.MULTILINE | re.IGNORECASE)


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


def run_client(cmd: list[str], timeout: int, prompt: bytes | None = None):
    """Bound the whole process tree, including descendants holding output pipes."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=os.name != "nt")
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
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
    match = TEST_GAMING_RE.search(text)
    return match.group(1).lower() if match else None


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

    executable = resolve_qodercli(args.qodercli)
    with_persistence_flag = probe_no_session_persistence(executable)

    cmd = spawn_prefix(executable) + ["-p", "-o", "json", "--tools", "", "--model", args.model]
    if with_persistence_flag:
        cmd.append("--no-session-persistence")

    started = time.monotonic()
    try:
        proc = run_client(cmd, args.timeout, prompt_bytes)
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

    verdict = extract_verdict(result)
    if verdict is None:
        return fail(3, "review must end with a VERDICT line")
    verdict_parse = "known" if verdict.split(":", 1)[1].strip().upper() in {
        "APPROVE", "ACCEPT", "PASS", "REJECT", "FIX", "HALT", "CHANGES"
    } else "unknown"
    review_bytes = result.encode("utf-8")
    usage_envelope = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    request_id = data.get("request_id") or usage_envelope.get("request_id")
    evidence = {
        "usage": data.get("usage"),
        "modelUsage": data.get("modelUsage"),
        "argv": cmd,
        "no_session_persistence": with_persistence_flag,
        "verdict_parse": verdict_parse,
        "served_model": served_model,
        "finish_reason": data.get("stop_reason"),
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
    partials = list(out_dir.parent.glob(out_dir.name + ".partial-*"))
    if partials:
        return fail(2, "leftover partial seat detected; move the .partial-* directory aside to recover")
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
