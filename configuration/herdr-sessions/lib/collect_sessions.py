#!/usr/bin/env python3
"""Collect the Claude Code sessions currently live in Herdr.

Emits the JSON the restore step consumes. Kept out of the shell driver because
the matching (process cmdline <-> herdr pane <-> transcript) is where the real
subtlety lives, and shell is a poor place for it.

Usage: collect_sessions.py '<herdr agent list json>'
"""
import glob, json, os, re, subprocess, sys, time

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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


def newest_transcript(cwd):
    """Claude Code's transcript dir is the cwd with non-alphanumerics -> '-'."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    files = glob.glob(os.path.expanduser(f"~/.claude/projects/{slug}/*.jsonl"))
    if not files:
        return None
    return os.path.splitext(os.path.basename(max(files, key=os.path.getmtime)))[0]


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

    # A session started without --resume carries no UUID. Fall back to the newest
    # transcript for its directory, but ONLY when that directory holds a single
    # session -- otherwise we cannot tell which transcript belongs to which pane,
    # and a wrong guess resumes the wrong conversation.
    for s in sessions:
        if s["session_uuid"]:
            s["uuid_source"] = "cmdline"
        elif per_cwd.get(s["cwd"], 0) == 1:
            s["session_uuid"] = newest_transcript(s["cwd"])
            s["uuid_source"] = "newest-transcript"
        else:
            s["uuid_source"] = "ambiguous-skipped"

    json.dump({"captured_at": int(time.time()),
               "captured_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "sessions": sessions}, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
