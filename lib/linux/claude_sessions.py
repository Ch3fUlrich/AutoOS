#!/usr/bin/env python3
"""claude_sessions.py — the data engine behind `claude-autostart` on Linux/macOS.

It answers questions about data and nothing else:

    config      what did the user configure?     -> shell assignments for `eval`
    set         change a setting                 -> merges into autoos.config.json
    probe-live  is Claude running at all?        -> a count
    snapshot    which sessions are live?         -> writes and prints the state
    plan        what should restore start?       -> one TSV line per session
    state       what is recorded right now?      -> the state, as JSON
    summary     the status screen's facts        -> key<TAB>value lines
    list        the recorded sessions            -> one TSV line each
    paths       where the state lives            -> a path
    herdr-pane  which pane should this land in?  -> a pane id

It deliberately owns no presentation and launches no processes. `claude-sessions.sh`
renders through the shared UI layer and owns every terminal it starts, because a
restored session has to land somewhere a human can see it (ADR 0002).

Discovery reads ~/.claude/projects/<slug>/<session-id>.jsonl. A transcript record
carries `cwd` and `sessionId` verbatim, so the working directory is read rather
than reconstructed from the lossy directory slug, and two sessions in one
directory stay distinct because each already has its own file (ADR 0001).

Every input arrives as an environment variable so the shell stays the single
place that reads autoos.config.json:

    AUTOOS_CLAUDE_HOME                  Claude Code's home dir  (default ~/.claude)
    AUTOOS_CLAUDE_STATE_FILE            where the snapshot lives
    AUTOOS_CLAUDE_LIVENESS_WINDOW_MINS  how far back "was running" reaches (240)
    AUTOOS_CLAUDE_MAX_SESSIONS          cap on restored sessions (8)
    AUTOOS_CLAUDE_REMOTE_CONTROL        snapshot | always | never
    AUTOOS_CLAUDE_LIVE_COUNT            override the live probe (tests, diagnostics)
"""
import json
import os
import sys
import time
from pathlib import Path

STATE_VERSION = 2

# The shipped example config is the single home for the default of every setting,
# for the PowerShell side as much as for this one. Keeping the defaults in code
# would mean three copies (here, AutoOS.ClaudeAutostart.psm1, the example file)
# drifting apart silently — which is exactly how the web UI ended up writing keys
# nothing read. The literal below is a backstop for a broken checkout only.
_BACKSTOP_DEFAULTS = {
    "enabled": True,
    "snapshot_interval_mins": 5,
    "liveness_window_mins": 240,
    "max_sessions": 8,
    "resume_mode": "full",
    "fallback": "continue",
    "fallback_cwd": "",
    "fallback_name": "main",
    "remote_control": "snapshot",
    "terminal_host": "auto",
}


def load_defaults():
    example = Path(__file__).resolve().parents[2] / "autoos.config.example.json"
    try:
        with open(example, encoding="utf-8") as fh:
            block = json.load(fh).get("claude_autostart")
        if isinstance(block, dict) and block:
            return block
    except (OSError, ValueError):
        pass
    return dict(_BACKSTOP_DEFAULTS)


DEFAULTS = load_defaults()


# ── locations ────────────────────────────────────────────────────────────────

def claude_home():
    return Path(os.environ.get("AUTOOS_CLAUDE_HOME") or Path.home() / ".claude")


def state_file_path():
    override = os.environ.get("AUTOOS_CLAUDE_STATE_FILE")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "autoos" / "claude-sessions.json"


def _env_int(name, fallback):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return fallback


# ── state i/o ────────────────────────────────────────────────────────────────

def load_state(path=None):
    f = path or state_file_path()
    try:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("sessions"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": STATE_VERSION, "captured_at": 0, "sessions": []}


def save_state(state, path=None):
    f = path or state_file_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename: a snapshot interrupted by the shutdown it is racing must
    # not leave a half-written file where the next boot's restore will read it.
    tmp = f.with_name(f"{f.name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, f)


# ── liveness ─────────────────────────────────────────────────────────────────

def _own_pids():
    return {os.getpid(), os.getppid()}


def _is_claude(cmdline):
    """A Claude Code process, not one of our own helpers.

    npm installs are hosted by node, so the executable name alone is not enough;
    `pgrep -x claude` finding nothing on such a machine is what made the first
    implementation report zero sessions.
    """
    if not cmdline:
        return False
    joined = " ".join(cmdline)
    if "claude_sessions" in joined or "claude-sessions" in joined:
        return False
    head = os.path.basename((cmdline[0] or "").split()[0] if cmdline[0] else "")
    if head in ("claude", "claude.exe"):
        return True
    return head in ("node", "node.exe", "bun", "deno") and "claude" in joined


def count_live_processes():
    override = os.environ.get("AUTOOS_CLAUDE_LIVE_COUNT")
    if override is not None:
        return _env_int("AUTOOS_CLAUDE_LIVE_COUNT", 0)

    mine = _own_pids()
    proc = Path("/proc")
    if proc.is_dir():
        n = 0
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) in mine:
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if _is_claude(raw.decode(errors="replace").split("\0")):
                n += 1
        return n

    # macOS and anything else without procfs.
    import subprocess
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,args="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    n = 0
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) in mine:
            continue
        if _is_claude(parts[1].split()):
            n += 1
    return n


# ── discovery ────────────────────────────────────────────────────────────────

def read_transcript_head(path, max_lines=40):
    """The first record carrying a cwd, or None.

    A transcript opens with queue-operation records that have no cwd, so this
    reads forward rather than trusting line 1 — but it stops well short of
    parsing a multi-megabyte conversation.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("cwd"):
                    return record
    except OSError:
        return None
    return None


def discover_sessions():
    """Every session whose transcript was touched inside the liveness window.

    Returns newest-first and capped, so `max_sessions` keeps the sessions the
    user was actually in rather than an arbitrary slice of the directory listing.
    """
    if count_live_processes() < 1:
        # Nothing is running, so nothing was running. A timer firing mid-boot must
        # not be able to describe the machine as empty (ADR 0001).
        return []

    window = _env_int("AUTOOS_CLAUDE_LIVENESS_WINDOW_MINS", DEFAULTS["liveness_window_mins"])
    cap = _env_int("AUTOOS_CLAUDE_MAX_SESSIONS", DEFAULTS["max_sessions"])
    cutoff = time.time() - window * 60

    found = []
    projects = claude_home() / "projects"
    if not projects.is_dir():
        return []

    for transcript in projects.glob("*/*.jsonl"):
        try:
            mtime = transcript.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        record = read_transcript_head(transcript)
        if not record:
            continue
        uuid = record.get("sessionId") or transcript.stem
        cwd = record.get("cwd")
        if not uuid or not cwd:
            continue
        found.append({
            "session_uuid": uuid,
            "name": os.path.basename(cwd.rstrip("/\\")) or "main",
            "cwd": cwd,
            "git_branch": record.get("gitBranch") or "",
            "transcript_path": str(transcript),
            "last_active": int(mtime),
            # Nothing in a transcript records whether --remote-control was on, so
            # the recorded value is always false and the `always`/`never` config
            # is what decides at restore time.
            "remote_control": False,
        })

    found.sort(key=lambda s: s["last_active"], reverse=True)

    # One session can leave transcripts under two project slugs — a scratchpad
    # directory beside the repo does exactly this — and restoring the same id
    # twice opens two terminals fighting over one conversation. Newest wins.
    seen, unique = set(), []
    for session in found:
        if session["session_uuid"] in seen:
            continue
        seen.add(session["session_uuid"])
        unique.append(session)

    return unique[:cap]


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_config():
    """Emit the effective configuration as shell assignments for `eval`.

    The shell used to run five near-identical python one-liners to read five keys;
    this is the one place a default or a key name is written.
    """
    path = os.environ.get("AUTOOS_CONFIG_FILE")
    block = {}
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                block = json.load(fh).get("claude_autostart") or {}
        except (OSError, ValueError):
            block = {}

    for key, default in DEFAULTS.items():
        value = block.get(key, default)
        if isinstance(value, bool):
            value = "1" if value else "0"
        text = str(value).replace("'", "'\\''")
        print(f"AUTOOS_CLAUDE_{key.upper()}='{text}'")
    return 0


def config_file_path():
    return Path(os.environ.get("AUTOOS_CONFIG_FILE")
                or Path(__file__).resolve().parents[2] / "autoos.config.json")


def _coerce(text, like):
    if isinstance(like, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        try:
            return int(text)
        except ValueError:
            return like
    return text


def cmd_set():
    """Merge key=value pairs into autoos.config.json's claude_autostart block.

    Read-modify-write, never replace: the file also holds the profile and every
    other answer, and the web UI's first version wiped three keys by assigning a
    fresh object over the top of it.
    """
    path = config_file_path()
    doc = {}
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"cannot update {path}: {exc}", file=sys.stderr)
            return 1
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("version", 1)
    block = doc.setdefault("claude_autostart", {})

    for pair in sys.argv[2:]:
        key, _, value = pair.partition("=")
        if key not in DEFAULTS:
            print(f"unknown setting: {key}", file=sys.stderr)
            return 2
        block[key] = _coerce(value, DEFAULTS[key])

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    print(path)
    return 0


def cmd_probe_live():
    print(count_live_processes())
    return 0


def cmd_snapshot():
    previous = load_state()
    sessions = discover_sessions()

    # Refuse to clobber. An empty result almost always means "the sessions are not
    # up yet", not "the user closed everything"; overwriting erases the record the
    # next restore depends on.
    if not sessions and previous.get("sessions"):
        print(json.dumps(previous, indent=2))
        return 0

    state = {
        "version": STATE_VERSION,
        "captured_at": int(time.time()),
        "captured_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sessions": sessions,
    }
    save_state(state)
    print(json.dumps(state, indent=2))
    return 0


def cmd_plan():
    """One line per recorded session, for the shell to execute.

    START <uuid> <name> <cwd> <rc>
    SKIP  <uuid> <name> <cwd> <reason>

    Both shapes carry the same five fields in the same order, so the reader never
    has to know which kind of line it is holding before it splits.
    """
    override = os.environ.get("AUTOOS_CLAUDE_REMOTE_CONTROL", DEFAULTS["remote_control"])
    for session in load_state().get("sessions", []):
        uuid = session.get("session_uuid") or ""
        name = session.get("name") or "main"
        cwd = session.get("cwd") or str(Path.home())
        if not uuid:
            print(f"SKIP\t-\t{name}\t{cwd}\tno session id was recorded")
            continue
        if override == "always":
            rc = "1"
        elif override == "never":
            rc = "0"
        else:
            rc = "1" if session.get("remote_control") else "0"
        print(f"START\t{uuid}\t{name}\t{cwd}\t{rc}")
    return 0


def cmd_state():
    print(json.dumps(load_state(), indent=2))
    return 0


def cmd_paths():
    print(state_file_path())
    return 0


def describe_age(epoch):
    """How long ago, in words. "never" when there is nothing to describe.

    The age is the actionable fact — a snapshot from four hours ago means the
    timer is not running — so it is what the status screens lead with, rather
    than a timestamp the reader has to subtract from the current time.
    """
    if not epoch:
        return "never"
    minutes = int((time.time() - epoch) / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} days ago"


def cmd_summary():
    """The status screen's facts, one `key<TAB>value` per line.

    Emitted here so the shell does not re-derive timestamps and counts with its
    own inline python, which is how the two drifted apart in the first place.
    """
    state = load_state()
    sessions = state.get("sessions", [])
    captured = state.get("captured_at") or 0
    if captured:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(captured))
        when = f"{stamp} ({describe_age(captured)})"
    else:
        # Not "never (never)": how long ago a snapshot that was never taken
        # happened is not a second fact about it.
        when = "never"

    print(f"last_snapshot\t{when}")
    print(f"tracked\t{len(sessions)}")
    print(f"state_file\t{state_file_path()}")
    return 0


def cmd_list():
    """One recorded session per line, for the shell to render.

    name <TAB> uuid <TAB> cwd <TAB> last-active-iso <TAB> branch
    """
    for session in load_state().get("sessions", []):
        last = session.get("last_active")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "-"
        print("\t".join([
            session.get("name") or "main",
            session.get("session_uuid") or "-",
            session.get("cwd") or "-",
            when,
            session.get("git_branch") or "-",
        ]))
    return 0


def cmd_herdr_pane():
    """Pick the herdr pane a session should land in, reading `pane list` on stdin.

    Preferring the pane already sitting in the session's directory matters because
    `claude --continue` resumes the most recent conversation for the *pane's* cwd,
    not the one we meant. Panes already claimed by this restore are excluded so two
    sessions never land on top of each other.
    """
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    claimed = {p for p in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if p}
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    panes = (payload.get("result") or payload).get("panes") or []

    free = [p for p in panes if (p.get("pane_id") or "") not in claimed]
    for pane in free:
        if want and pane.get("cwd") == want:
            print(pane.get("pane_id") or "")
            return 0
    if free:
        print(free[0].get("pane_id") or "")
    return 0


COMMANDS = {
    "config": cmd_config,
    "set": cmd_set,
    "probe-live": cmd_probe_live,
    "snapshot": cmd_snapshot,
    "plan": cmd_plan,
    "state": cmd_state,
    "paths": cmd_paths,
    "summary": cmd_summary,
    "list": cmd_list,
    "herdr-pane": cmd_herdr_pane,
}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "state"
    handler = COMMANDS.get(name)
    if not handler:
        print(f"unknown command: {name}", file=sys.stderr)
        print(f"usage: claude_sessions.py <{'|'.join(COMMANDS)}>", file=sys.stderr)
        return 2
    return handler()


if __name__ == "__main__":
    sys.exit(main())
