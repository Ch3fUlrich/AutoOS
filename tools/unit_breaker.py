#!/usr/bin/env python3
"""unit_breaker: circuit breaker around ExecStart of every unit that starts an agent (P1.2).

D-1173: a watcher restarted agents for hours with no stop (systemd's default 5/10 s start limit never trips at
RestartSec=20). Wrap the command:

    unit_breaker.py --unit oc-l0-watch.service [--threshold 5] -- <cmd...>

It runs <cmd>, passes its exit code through, and keeps a failure signature per unit: the exit code plus the sha1
of the last non-empty stderr line (digits -> N, so a pid or a timestamp does not make two failures differ).
After THRESHOLD identical consecutive failures it runs `systemctl --user disable --now <unit>`, appends ONE
alert line to $AUTOOS_ALERT_INBOX (stderr when unset) and refuses to run <cmd> until reset. A success resets the
counter. A stop by signal (exit 130/143) is neither a failure nor a success. On a host without systemctl a
`<unit>.tripped` marker is written beside the state and the wrapper exits non-zero.

    unit_breaker.py status          one line per unit: <unit> state=ok|failing|tripped count=<n> sig=<short>
    unit_breaker.py reset <unit>    clear a trip (state and marker)

State: $XDG_STATE_HOME/autoos/breaker/<unit>.json (default ~/.local/state), written atomically.
"""
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_THRESHOLD = 5
STOP_CODES = (130, 143)  # SIGINT / SIGTERM exit: a stop, not a crash
ALERT_TAIL_CHARS = 200


def state_dir(env):
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "autoos" / "breaker"


def normalise(line):
    return re.sub(r"\d+", "N", line.strip())


def signature(code, last_line):
    """`<exit code>:<sha1 of the normalised last stderr line>`."""
    return "%d:%s" % (code, hashlib.sha1(normalise(last_line).encode("utf-8", "replace")).hexdigest())


def sig_short(sig):
    return sig.split(":", 1)[-1][:8]


def _read(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alert(env, line):
    inbox = env.get("AUTOOS_ALERT_INBOX")
    if inbox:
        try:
            with open(inbox, "a") as fh:
                fh.write(line + "\n")
            return
        except OSError:
            pass
    sys.stderr.write(line + "\n")


def default_runner(argv):
    return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30).returncode


def run_command(cmd):
    """Run cmd, tee its stderr, return (exit code, last non-empty stderr line)."""
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)

    def forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except OSError:
            pass
    old = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT)}
    last = ""
    try:
        for raw in iter(proc.stderr.readline, b""):
            text = raw.decode("utf-8", "replace")
            sys.stderr.write(text)
            sys.stderr.flush()
            text = text.strip()
            if text:
                last = text
        code = proc.wait()
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    return (128 - code if code < 0 else code), last


def trip(unit, data, env, runner, directory):
    disabled = False
    try:
        disabled = runner(["systemctl", "--user", "disable", "--now", unit]) == 0
    except (OSError, subprocess.SubprocessError):
        disabled = False
    if not disabled:
        (directory / (unit + ".tripped")).write_text(_utc() + "\n")
    data["tripped"] = True
    if not data.get("alerted"):
        data["alerted"] = True
        tail = data.get("last", "")[:ALERT_TAIL_CHARS]
        _alert(env, "%s BREAKER %s tripped after %d x %s: %s"
               % (_utc(), unit, data["count"], sig_short(data["sig"]), tail))


def wrap(unit, threshold, cmd, env, runner):
    directory = state_dir(env)
    path = directory / (unit + ".json")
    data = _read(path)
    if data.get("tripped"):
        sys.stderr.write("unit_breaker: %s is tripped (reset with: unit_breaker.py reset %s)\n" % (unit, unit))
        return 1
    code, last = run_command(cmd)
    if code in STOP_CODES:
        return code
    if code == 0:
        _write(path, {"count": 0, "updated": _utc()})
        return 0
    sig = signature(code, last)
    count = data.get("count", 0) + 1 if data.get("sig") == sig else 1
    data = {"sig": sig, "count": count, "last": last[:ALERT_TAIL_CHARS], "updated": _utc()}
    if count >= threshold:
        trip(unit, data, env, runner, directory)
    _write(path, data)
    return code


def status(env, out):
    directory = state_dir(env)
    units = sorted(p.name[:-5] for p in directory.glob("*.json")) if directory.is_dir() else []
    for unit in units:
        d = _read(directory / (unit + ".json"))
        count = d.get("count", 0)
        state = "tripped" if d.get("tripped") else ("failing" if count else "ok")
        out.write("%s state=%s count=%d sig=%s\n" % (unit, state, count, sig_short(d["sig"]) if d.get("sig") else "-"))
    return 0


def reset(unit, env):
    directory = state_dir(env)
    for name in (unit + ".json", unit + ".tripped"):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
    return 0


def main(argv=None, env=None, runner=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    runner = runner or default_runner
    if argv[:1] == ["status"]:
        return status(env, sys.stdout)
    if argv[:1] == ["reset"] and len(argv) == 2:
        return reset(argv[1], env)
    unit, threshold, cmd = None, DEFAULT_THRESHOLD, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            cmd = argv[i + 1:]
            break
        if a in ("--unit", "--threshold") and i + 1 < len(argv):
            if a == "--unit":
                unit = argv[i + 1]
            else:
                try:
                    threshold = int(argv[i + 1])
                except ValueError:
                    cmd = None
                    break
            i += 2
            continue
        break
    if not unit or not cmd or threshold < 1 or "/" in unit:
        sys.stderr.write("usage: unit_breaker.py --unit <name> [--threshold N] -- <cmd...> | status | reset <unit>\n")
        return 2
    return wrap(unit, threshold, cmd, env, runner)


if __name__ == "__main__":
    sys.exit(main())
