#!/usr/bin/env python3
"""Collect the Claude Code sessions currently live in Herdr.

Emits the JSON the restore step consumes. Kept out of the shell driver because
the matching (process cmdline <-> herdr pane <-> transcript) is where the real
subtlety lives, and shell is a poor place for it.

Usage: collect_sessions.py '<herdr agent list json>'
"""
import glob, json, os, re, subprocess, sys, time

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def claude_home():
    """CLAUDE_HOME is the one test seam for every function below that reads
    Claude Code's own state (jobs/, sessions/, projects/) -- set it to a
    fixture directory and none of them touch a real ~/.claude."""
    return os.environ.get("CLAUDE_HOME") or os.path.expanduser("~/.claude")


def _load_json(path):
    """Best-effort JSON load. A malformed, empty, truncated or unreadable file
    is data from a process that may have been mid-write when we looked, not a
    reason to crash a snapshot -- it is simply ignored, same as "not there"."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def bg_ids():
    """Session ids that belong to a background job, so a pane's own transcript
    is never confused with one it happens to share a directory with.

    Union of: sessionId + resumeSessionId of every jobs/*/state.json (a job's
    `state` field is NOT liveness -- a stopped/done job stays in the directory
    and its id must keep being excluded, see AGENTS.md-adjacent lesson in the
    proposal doc), and sessionId of every sessions/*.json whose kind is "bg".
    Malformed or keyless files are ignored, never raised.
    """
    home = claude_home()
    ids = set()
    for path in glob.glob(os.path.join(home, "jobs", "*", "state.json")):
        d = _load_json(path)
        if not d:
            continue
        for key in ("sessionId", "resumeSessionId"):
            v = d.get(key)
            if v:
                ids.add(v)
    for path in glob.glob(os.path.join(home, "sessions", "*.json")):
        d = _load_json(path)
        if not d or d.get("kind") != "bg":
            continue
        v = d.get("sessionId")
        if v:
            ids.add(v)
    return ids


BG_ARGV1 = {"daemon", "bg-pty-host", "bg-spare"}


def registry_uuid(pid, proc_root="/proc"):
    """The session registry entry for a live pid (sessions/<pid>.json),
    trusted only when its recorded `procStart` matches this pid's real start
    time (field 22, "starttime", of /proc/<pid>/stat) -- pids get reused, and
    a stale entry from a different, earlier process must never be attributed
    to today's one. `proc_root` is injectable so tests never touch the real
    /proc.

    Returns (sessionId, kind) from the entry, or (None, None) when there is
    no entry, it is malformed, or the procStart check fails.
    """
    home = claude_home()
    entry = _load_json(os.path.join(home, "sessions", "%s.json" % pid))
    if not entry:
        return None, None
    recorded_start = entry.get("procStart")
    if recorded_start is None:
        return None, None
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8") as fh:
            stat = fh.read()
        # comm (field 2) is parenthesised and may itself contain ')' or
        # spaces, so the kernel format guarantees only that the LAST ')'
        # closes it. Field 22 (starttime) is then 19 fields in past that
        # split (field 3 = index 0 .. field 22 = index 19).
        fields = stat.rsplit(")", 1)[1].split()
        proc_start = int(fields[19])
    except (OSError, IndexError, ValueError):
        return None, None
    if proc_start != recorded_start:
        return None, None
    return entry.get("sessionId"), entry.get("kind")


def is_bg_argv(argv):
    """True when argv itself says this is a background-flavoured process,
    with no I/O needed at all: `claude daemon`, `claude bg-pty-host`,
    `claude bg-spare`, ..."""
    return len(argv) > 1 and argv[1] in BG_ARGV1


def is_bg_process(pid, argv, proc_root="/proc"):
    """True when this pid must never be treated as a candidate interactive
    session: either its argv says so directly, or the session registry
    (trusted via registry_uuid's procStart check) says kind="bg". Checked
    BEFORE the cwd fallback in main() -- a bg orchestrator sharing a pane's
    cwd must never turn a real single-session directory into an ambiguous
    one, and must never have its own uuid attributed to that pane."""
    if is_bg_argv(argv):
        return True
    _, kind = registry_uuid(pid, proc_root=proc_root)
    return kind == "bg"


def clean_title(title):
    """Herdr's `terminal_title_stripped` is not as stripped as it sounds: a busy
    agent renders as "◐ main", so keying the map on it raw fails to match
    `-n main` for exactly as long as the session is working -- which is when a
    snapshot most wants to record it."""
    return re.sub(r"^[^\w]+", "", title or "").strip()


def claude_agents(raw):
    try:
        d = json.loads(raw)
        lst = (d.get("result") or d).get("agents") or []
    except Exception:
        lst = []
    return [a for a in lst if (a.get("agent") or "") == "claude"]


def proc_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().decode(errors="replace").split("\0")
    except OSError:
        return []


def newest_transcript(cwd, exclude=frozenset()):
    """The newest transcript in cwd's project directory whose id is not in
    `exclude` (background sessions' ids -- see bg_ids()). Claude Code's
    transcript dir is the cwd with non-alphanumerics -> '-'.

    Returns (uuid, source):
      (uuid, "newest-transcript")    a usable transcript was found
      (None, "only-bg-transcripts")  transcripts exist but all are excluded --
                                      never guess one of them
      (None, None)                   no transcripts at all for this cwd
    """
    home = claude_home()
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    files = glob.glob(os.path.join(home, "projects", slug, "*.jsonl"))
    if not files:
        return None, None
    candidates = [f for f in files
                  if os.path.splitext(os.path.basename(f))[0] not in exclude]
    if not candidates:
        return None, "only-bg-transcripts"
    newest = max(candidates, key=os.path.getmtime)
    return os.path.splitext(os.path.basename(newest))[0], "newest-transcript"


def main():
    agents = claude_agents(sys.argv[1] if len(sys.argv) > 1 else "")
    by_name = {clean_title(a.get("terminal_title_stripped")): a for a in agents}
    by_cwd = {}
    for a in agents:
        by_cwd.setdefault(a.get("cwd") or "", []).append(a)
    try:
        pids = subprocess.run(["pgrep", "-x", "claude"],
                              capture_output=True, text=True).stdout.split()
    except Exception:
        pids = []

    sessions, per_cwd = [], {}
    for pid in pids:
        argv = cmdline(pid)
        if not argv:
            continue
        name = uuid = None
        rc = False
        for i, tok in enumerate(argv):
            nxt = argv[i + 1] if i + 1 < len(argv) else ""
            if tok in ("-n", "--name"):
                name = nxt or name
            elif tok in ("-r", "--resume") and UUID_RE.fullmatch(nxt or ""):
                uuid = nxt
            elif tok in ("--rc", "--remote-control"):
                rc = True
        # A background orchestrator (herdr's `claude daemon`/`bg-pty-host`/
        # `bg-spare` helpers, or a live pid the session registry itself marks
        # kind=bg) is never a restorable pane session. Filtered BEFORE the cwd
        # fallback below: matching it there would either falsely turn a real
        # single-session directory into "ambiguous" (skipping a legitimate
        # restore) or attribute the bg process's own uuid to that pane.
        if is_bg_process(pid, argv):
            continue
        a = by_name.get(name) if name else None
        if a is None:
            # A session started by hand carries no -n at all (a system-scope
            # host's is `claude --remote-control --resume <name>`), so there is
            # no name to match on and the pane would go unrecorded. Match on
            # the process cwd instead -- but only when exactly one agent sits
            # there, since two in one directory cannot be told apart this way
            # and a wrong match would resume the wrong conversation.
            here = by_cwd.get(proc_cwd(pid), [])
            if len(here) == 1:
                a = here[0]
                name = name or clean_title(a.get("terminal_title_stripped"))
        if not a or not name:
            continue
        cwd = a.get("cwd") or ""
        per_cwd[cwd] = per_cwd.get(cwd, 0) + 1
        sessions.append({"name": name, "pane_id": a.get("pane_id"), "cwd": cwd,
                         "session_uuid": uuid, "remote_control": rc, "pid": int(pid)})

    # A session started without --resume carries no UUID. The live pid's own
    # session-registry entry is authoritative when it checks out (registry_uuid
    # validates procStart against /proc, so a reused pid's stale entry is never
    # trusted) -- it wins even when the directory also holds a newer transcript.
    # Failing that, fall back to the newest transcript for its directory, but
    # ONLY when that directory holds a single session -- otherwise we cannot
    # tell which transcript belongs to which pane, and a wrong guess resumes
    # the wrong conversation. A background job's own transcript in that same
    # directory is excluded first (bg_ids()): it can be newer than the
    # interactive pane's own and would otherwise win by mtime.
    excluded_ids = bg_ids()
    for s in sessions:
        if s["session_uuid"]:
            s["uuid_source"] = "cmdline"
            continue
        reg_uuid, reg_kind = registry_uuid(s["pid"])
        if reg_uuid and reg_kind == "interactive":
            s["session_uuid"] = reg_uuid
            s["uuid_source"] = "registry"
        elif per_cwd.get(s["cwd"], 0) == 1:
            uuid, source = newest_transcript(s["cwd"], exclude=excluded_ids)
            s["session_uuid"] = uuid
            s["uuid_source"] = source or "ambiguous-skipped"
        else:
            s["uuid_source"] = "ambiguous-skipped"

    json.dump({"captured_at": int(time.time()),
               "captured_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "sessions": sessions}, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
