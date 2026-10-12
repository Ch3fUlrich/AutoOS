#!/usr/bin/env python3
"""gateway_alerts: read-only check of the gateway call log for provider-suspension signals (P1.2, D-1179).

A provider that suspends, bans or disables the account answers 401/403 or says so in the error text. The check
looks at the last --window minutes of call_logs and appends ONE line per provider per 24 h to the alert file
(--alert-file, default $AUTOOS_ALERT_INBOX; none -> stderr only):

    <utc> GATEWAY-ALERT <provider> suspension signal: <n> row(s), <statuses/text>, last <timestamp>

Watched providers: xiaomi-mimo (the MiMo flash/pro legs). Signal = HTTP 401/403, or the words suspend / banned /
disabled in error_summary / error_type. Source: the OmniRoute sqlite opened mode=ro (--db, default
${XDG_DATA_HOME:-~/.local/share}/autoos/ai-stack/omniroute/storage.sqlite). Only status, provider, timestamp and
the error text are read; no key value, no request body.

    gateway_alerts.py [--db P] [--window 60] [--alert-file F] [--state S]      exit 0 always when the check ran
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

WATCHED = ("xiaomi-mimo",)
SIGNAL_STATUS = (401, 403)
SIGNAL_TEXT = re.compile(r"suspend|banned|disabled", re.I)
REALERT_S = 24 * 3600


class AlertError(Exception):
    pass


def _default_db(env):
    base = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "autoos" / "ai-stack" / "omniroute" / "storage.sqlite"


def _default_state(env):
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "autoos" / "gateway-alerts.json"


def _utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_state(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def signals(db_path, since_iso):
    """{provider: (rows, sorted statuses/words, last timestamp)} for watched providers since `since_iso`."""
    if not Path(db_path).is_file():
        raise AlertError("call-log db not found: %s" % db_path)
    marks = ",".join("?" * len(WATCHED))
    try:
        con = sqlite3.connect("file:%s?mode=ro" % Path(db_path).resolve(), uri=True)
        try:
            cur = con.execute(
                "select provider, status, error_summary, error_type, timestamp from call_logs"
                " where provider in (%s) and timestamp >= ? order by timestamp" % marks,
                list(WATCHED) + [since_iso])
            rows = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise AlertError("call-log db unreadable (%s)" % type(e).__name__)
    found = {}
    for provider, status, summary, etype, ts in rows:
        text = " ".join(x for x in (summary, etype) if x)
        words = {m.lower() for m in SIGNAL_TEXT.findall(text)}
        if status not in SIGNAL_STATUS and not words:
            continue
        n, tags, _ = found.get(provider, (0, set(), ""))
        if status in SIGNAL_STATUS:
            tags.add(str(status))
        tags |= words
        found[provider] = (n + 1, tags, ts)
    return found


def check(db_path, alert_file, state_path, now, window_min=60):
    """Run one check; return the providers alerted now."""
    since = _utc(now - datetime.timedelta(minutes=window_min))
    found = signals(db_path, since)
    state = _read_state(state_path)
    alerted = []
    for provider in sorted(found):
        last = state.get(provider)
        if isinstance(last, (int, float)) and now.timestamp() - last < REALERT_S:
            continue
        n, tags, ts = found[provider]
        line = "%s GATEWAY-ALERT %s suspension signal: %d row(s), %s, last %s" % (
            _utc(now), provider, n, "/".join(sorted(tags)), ts)
        if alert_file:
            with open(alert_file, "a") as fh:
                fh.write(line + "\n")
        else:
            sys.stderr.write(line + "\n")
        state[provider] = now.timestamp()
        alerted.append(provider)
    if alerted:
        _write_state(state_path, state)
    return alerted


def main(argv=None, env=None, now=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(_default_db(env)))
    ap.add_argument("--window", type=int, default=60, help="minutes of call log to scan (default 60)")
    ap.add_argument("--alert-file", default=env.get("AUTOOS_ALERT_INBOX") or None)
    ap.add_argument("--state", default=str(_default_state(env)))
    args = ap.parse_args(argv)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        alerted = check(args.db, args.alert_file, args.state, now, args.window)
    except AlertError as e:
        sys.stderr.write("gateway_alerts: %s\n" % e)
        return 1
    sys.stdout.write("alerted: %s\n" % (", ".join(alerted) if alerted else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
