#!/usr/bin/env python3
"""Refresh the AutoOS daily cost gate and its one-line status.

Runs the budget checker (tools/run_budget.py, `day` subcommand) as a
subprocess, then writes the resulting gate JSON to the state directory
atomically, and the one-line status file next to it. The state dir is
${XDG_STATE_HOME:-~/.local/state}/autoos/ on Linux and
%LOCALAPPDATA%\\autoos\\ on Windows (when LOCALAPPDATA is unset there is
no default; pass --state-dir). The config file is
${XDG_CONFIG_HOME:-~/.config}/autoos/daily-gate.conf on Linux and
%APPDATA%\\autoos\\daily-gate.conf on Windows; it carries exactly two
lines, `warn=<number>` and `block=<number>`, nothing else. The built-in
defaults (warn 20, block 25) apply when the file is absent or unusable,
and the status line records the reason.

The gateway key is never read or printed here. `--gateway` only lets
run_budget.py do what it does today (it reads the manage key itself).
On a host whose gateway does not accept that key, the caller passes
--rows with an exported call-log rows file instead (the Windows
wrapper does that).

`run_budget.py day` exits 0 (ok), 20 (warn), 21 (block); all three are
success here when stdout is valid JSON. Any other exit, or invalid
JSON, is a failure: the old gate file is kept, the status line
becomes `<UTC iso> UNAVAILABLE: <reason>`, and this script exits 3.

The helpers (`state_dir`, `config_path`, `read_config`, `build_status`,
and `main` with injectable `run` and `now`) are importable so tests
can drive the script without ever calling a gateway or the real
run_budget.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

GATE_FILE_NAME = "daily-gate.json"
STATUS_FILE_NAME = "daily-gate.status"
DEFAULT_WARN_USD = 20.0
DEFAULT_BLOCK_USD = 25.0
EXIT_REFRESH_FAILURE = 3
# The fields a valid `day` document must carry; see run_budget.py.
_DAY_FIELDS = ("day", "usd", "by_provider", "verdict", "unverified",
               "budget", "bad_rows", "unpriced_models",
               "unpriced_default_used", "truncated")
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


def _defaults(reason=None):
    return DEFAULT_WARN_USD, DEFAULT_BLOCK_USD, reason


def _short(err):
    """First line of an exception message, for status reasons."""
    s = str(err).strip()
    return s.splitlines()[0][:200] if s else type(err).__name__


def _utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_dir(env=None):
    """The autoos state dir for this platform, honoring the env override.

    Returns None when the platform has no default (Windows without
    LOCALAPPDATA, a homeless POSIX box): then the caller must pass
    --state-dir, exactly like the spawner's default gate path."""
    if env is None:
        env = os.environ
    if os.name == "nt":
        base = (env.get("LOCALAPPDATA") or "").strip()
        if not base:
            return None
        return Path(base) / "autoos"
    state = (env.get("XDG_STATE_HOME") or "").strip()
    if not state:
        home = (env.get("HOME") or "").strip()
        if not home:
            return None
        state = os.path.join(home, ".local", "state")
    return Path(state) / "autoos"


def config_path(env=None):
    """The daily-gate.conf path for this platform, honoring the env override."""
    if env is None:
        env = os.environ
    if os.name == "nt":
        base = env.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return Path(base) / "autoos" / "daily-gate.conf"
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "autoos" / "daily-gate.conf"


def read_config(path):
    """Read the two-line gate config.

    Returns (warn, block, note): the file's values when it is exactly
    `warn=<number>` and `block=<number>` with 0 <= warn < block, else
    the built-in defaults and a short note saying why the file was
    ignored. Never raises.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return _defaults()
        raw = p.read_bytes()
    except Exception as e:
        return _defaults("config unreadable (%s)" % _short(e))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _defaults("config is not valid UTF-8")
    if "\x00" in text:
        return _defaults("config contains NUL bytes")
    vals = {}
    for raw_line in text.splitlines():
        s = raw_line.strip()
        if s == "":
            continue
        if s.startswith("warn="):
            if "warn" in vals:
                return _defaults("duplicate warn key")
            vals["warn"] = s[len("warn="):].strip()
        elif s.startswith("block="):
            if "block" in vals:
                return _defaults("duplicate block key")
            vals["block"] = s[len("block="):].strip()
        else:
            return _defaults("unknown key")
    for key in ("warn", "block"):
        if key not in vals:
            return _defaults("missing %s" % key)
        if not _NUMBER_RE.match(vals[key]):
            return _defaults("%s is not a number" % key)
        v = float(vals[key])
        if not math.isfinite(v):
            return _defaults("%s is not finite" % key)
        vals[key] = v
    if vals["warn"] < 0.0:
        return _defaults("warn must be >= 0")
    if vals["warn"] >= vals["block"]:
        return _defaults("warn must be < block")
    return vals["warn"], vals["block"], None


def build_status(doc, note=None, now=None):
    """Render the one-line status for a `day` document.

    Layout: `<UTC iso> verdict= usd= budget= truncated= unpriced=
    unpriced_default_used= bad_rows=`, with ` ; daily gate config
    ignored: <reason>` appended when `note` is set. A document that
    lacks the required fields yields an `UNAVAILABLE` line instead.
    `now` (default the current UTC time) stamps the line so tests can
    pin it."""
    if now is None:
        now = datetime.now(timezone.utc)
    stamp = _utc_iso(now)
    try:
        if not isinstance(doc, dict):
            raise ValueError("not an object")
        for key in _DAY_FIELDS:
            if key not in doc:
                raise ValueError("missing required field: %s" % key)
        verdict = doc["verdict"]
        if not isinstance(verdict, str) or not verdict:
            raise ValueError("verdict is not a string")
        usd = float(doc["usd"])
        budget = float(doc["budget"])
        truncated = bool(doc["truncated"])
        bad_rows = doc["bad_rows"]
        if isinstance(bad_rows, bool) or not isinstance(bad_rows, int):
            raise ValueError("bad_rows is not an integer")
        unpriced = 0
        models = doc["unpriced_models"]
        if isinstance(models, list):
            for m in models:
                if isinstance(m, dict) and isinstance(m.get("count"), int) \
                        and not isinstance(m.get("count"), bool):
                    unpriced += m["count"]
        line = ("%s verdict=%s usd=%s budget=%s truncated=%s unpriced=%d "
                "unpriced_default_used=%s bad_rows=%d") % (
            stamp, verdict, repr(usd), repr(budget),
            "true" if truncated else "false", unpriced,
            "true" if doc["unpriced_default_used"] else "false", bad_rows)
    except Exception as e:
        line = "%s UNAVAILABLE: %s" % (stamp, _short(e))
    if note:
        line += " ; daily gate config ignored: %s" % note
    return line


def atomic_write(path, data):
    """Write bytes to `path` atomically: a temp file in the same
    directory, then os.replace. The destination is either untouched or
    fully replaced, never half-written."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _unavailable(reason, st, now, note=None):
    """Write an UNAVAILABLE status line (best effort) and return 3."""
    line = "%s UNAVAILABLE: %s" % (_utc_iso(now), reason)
    if note:
        line += " ; daily gate config ignored: %s" % note
    if st is not None:
        try:
            atomic_write(st / STATUS_FILE_NAME, (line + "\n").encode("utf-8"))
        except Exception:
            pass
    print("cost-gate-refresh: %s" % reason, file=sys.stderr)
    return EXIT_REFRESH_FAILURE


def _ensure_state_dir(st):
    try:
        st.mkdir(parents=True, exist_ok=True)
        return None
    except OSError as e:
        return "state dir not writable: %s" % _short(e)


def _default_python():
    return getattr(sys, "executable", None) or "python"


def main(argv=None, run=None, now=None):
    """Entry point. `run` (default subprocess.run) is injectable so
    tests never call a real gateway or run_budget; `now` (default the
    current UTC time) fixes the status timestamp for tests."""
    ap = argparse.ArgumentParser(
        prog="cost-gate-refresh.py",
        description="Refresh the AutoOS daily cost gate and its status line")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--gateway", action="store_true",
                     help="let run_budget.py pull rows from the gateway "
                          "itself (it reads the manage key; this script "
                          "never sees it). No --rows means --gateway.")
    src.add_argument("--rows", nargs="+", metavar="FILE",
                     help="exported call-log rows file(s) for run_budget.py")
    ap.add_argument("--state-dir", metavar="DIR", default=None,
                    help="override the state dir (default: platform autoos dir)")
    ap.add_argument("--config", metavar="FILE", default=None,
                    help="override the daily-gate.conf path")
    ap.add_argument("--run-budget", metavar="PATH",
                    default=str(Path(__file__).resolve().parent / "run_budget.py"),
                    help="path to run_budget.py (default: next to this script)")
    args = ap.parse_args(argv)

    now = now if now is not None else datetime.now(timezone.utc)
    if run is None:
        run = subprocess.run

    st = Path(args.state_dir).expanduser() if args.state_dir else state_dir()
    if st is None:
        return _unavailable("no state dir", None, now)
    err = _ensure_state_dir(st)
    if err:
        return _unavailable(err, st, now)
    rb = Path(args.run_budget).expanduser()
    if not rb.is_file():
        return _unavailable("run budget script not found: %s" % rb, st, now)

    cfg = Path(args.config).expanduser() if args.config else config_path()
    warn, block, note = read_config(cfg)

    cmd = [_default_python(), str(rb), "day",
           "--warn", repr(warn), "--budget", repr(block)]
    if args.rows:
        cmd.extend(["--rows"] + list(args.rows))
    else:
        cmd.append("--gateway")

    try:
        proc = run(cmd, capture_output=True)
    except Exception as e:
        return _unavailable("run budget did not complete: %s" % _short(e),
                            st, now, note)
    if proc.returncode not in (0, 20, 21):
        return _unavailable("run budget exit %d" % proc.returncode, st, now, note)
    try:
        doc = json.loads(proc.stdout.decode("utf-8-sig"))
    except Exception:
        return _unavailable("run budget output is not valid JSON", st, now, note)
    if not isinstance(doc, dict):
        return _unavailable("run budget output is not a JSON object", st, now, note)

    line = build_status(doc, note, now=now)
    if line.split(" ", 2)[1].startswith("UNAVAILABLE:"):
        # The JSON parsed but is not a valid gate document: same failure
        # path, old gate file kept.
        return _unavailable(line.split(" ", 2)[2] or "invalid gate document",
                            st, now, note)

    gate_path = st / GATE_FILE_NAME
    status_path = st / STATUS_FILE_NAME
    gate_data = (json.dumps(doc, indent=2) + "\n").encode("utf-8")
    status_data = (line + "\n").encode("utf-8")
    try:
        atomic_write(gate_path, gate_data)
        atomic_write(status_path, status_data)
    except Exception as e:
        return _unavailable("could not write gate file: %s" % _short(e),
                            st, now, note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
