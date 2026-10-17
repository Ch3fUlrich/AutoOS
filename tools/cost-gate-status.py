#!/usr/bin/env python3
"""Print the AutoOS daily cost gate status line, with a freshness suffix.

Reads the one-line status file next to the gate file in the state
directory (${XDG_STATE_HOME:-~/.local/state}/autoos/ on Linux,
%LOCALAPPDATA%\\autoos\\ on Windows) and prints it. A suffix is
appended when the gate cannot be trusted:

  STALE      the status file is missing, says UNAVAILABLE, or is older
             than 30 minutes (29:59 is fresh, 30:01 is stale). A line
             dated more than 5 minutes in the future is STALE too: a
             clock that ran ahead must not make a dead refresher look
             healthy.
  PRICE-GAP  otherwise, when the line says unpriced_default_used=true.

The exit code is always 0; the line is the machine-readable contract
for the spawner. `--now ISO` fixes "now" for deterministic output in
tests. The status line's own UTC timestamp is parsed from the file; a
line whose timestamp cannot be parsed is stale.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUS_FILE_NAME = "daily-gate.status"
STALE_AFTER = timedelta(minutes=30)
FUTURE_SKEW = timedelta(minutes=5)


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


def _utc_now():
    return datetime.now(timezone.utc)


def _utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_when(text):
    """The UTC instant carried by the first token of a status line."""
    s = text.split()[0] if text.split() else ""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_status(status_text, now):
    """The status line to print: the stored line plus STALE or
    PRICE-GAP where due. `status_text` is None when the file is
    missing; `now` is a tz-aware UTC datetime (injectable for tests)."""
    if status_text is None:
        return "%s STALE" % _utc_iso(now)
    line = status_text.rstrip("\r\n")
    if not line.strip():
        return "%s STALE" % _utc_iso(now)
    if "UNAVAILABLE:" in line:
        return line + " STALE"
    try:
        age = now - _parse_when(line)
    except Exception:
        return line + " STALE"
    if age > STALE_AFTER:
        return line + " STALE"
    if age < -FUTURE_SKEW:
        return line + " STALE"
    if "unpriced_default_used=true" in line:
        return line + " PRICE-GAP"
    return line


def main(argv=None, now=None):
    """Entry point; always exits 0. `now` (default the current UTC
    time) is injectable so tests are deterministic."""
    ap = argparse.ArgumentParser(
        prog="cost-gate-status.py",
        description="Print the AutoOS daily cost gate status line")
    ap.add_argument("--state-dir", metavar="DIR", default=None,
                    help="override the state dir (default: platform autoos dir)")
    ap.add_argument("--now", metavar="ISO", default=None,
                    help="treat this UTC instant as now (deterministic output)")
    args = ap.parse_args(argv)

    if now is None:
        now = _utc_now()
    if args.now is not None:
        s = args.now.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            now = datetime.fromisoformat(s)
        except ValueError:
            print("cost-gate-status: --now is not a valid UTC instant: %s"
                  % args.now, file=sys.stderr)
            return 0
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

    st = Path(args.state_dir).expanduser() if args.state_dir else state_dir()
    if st is None:
        # The spawner's default gate path is None on such a host too, so
        # the line cannot be read: a dead refresher must read as stale.
        print("%s STALE" % _utc_iso(now))
        return 0
    path = st / STATUS_FILE_NAME
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        text = None
    try:
        print(build_status(text, now))
    except Exception as e:
        # The contract is a single line and exit 0; a broken line must
        # never crash the spawner's poll.
        print("%s UNAVAILABLE: %s STALE"
              % (_utc_iso(now), type(e).__name__), file=sys.stderr)
        print("%s STALE" % _utc_iso(now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
