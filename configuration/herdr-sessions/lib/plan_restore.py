#!/usr/bin/env python3
"""Decide what to relaunch, emitting TSV the shell driver can read.

    START<TAB>name<TAB>pane<TAB>uuid<TAB>rc<TAB>-
    SKIP <TAB>name<TAB>pane<TAB>reason<TAB>-<TAB>-

Skips are deliberate and each one is safer than the alternative:
  * no UUID          -- we would have to guess a conversation
  * pane gone        -- the layout changed under us
  * cwd mismatch     -- restoring into the wrong project directory is worse
                        than not restoring at all
  * already running  -- keeps the whole operation idempotent

Usage: plan_restore.py STATE_FILE '<pane list json>' '<agent list json>' REMOTE_CONTROL
"""
import json, sys


def as_list(raw, key):
    try:
        d = json.loads(raw)
        r = d.get("result") or d
        val = r.get(key)
        if val is None:
            val = r if isinstance(r, list) else []
        return val
    except Exception:
        return []


def main():
    state_file, panes_raw, agents_raw = sys.argv[1], sys.argv[2], sys.argv[3]
    rc_mode = sys.argv[4] if len(sys.argv) > 4 else "snapshot"

    state = json.load(open(state_file))
    panes = {p.get("pane_id"): p for p in as_list(panes_raw, "panes")}
    busy = {a.get("pane_id") for a in as_list(agents_raw, "agents")
            if (a.get("agent") or "") == "claude"}

    for s in state.get("sessions", []):
        name, pane = s.get("name"), s.get("pane_id")
        cwd, uuid = s.get("cwd"), s.get("session_uuid")

        def skip(reason):
            print(f"SKIP\t{name}\t{pane}\t{reason}\t-\t-")

        if not uuid:
            skip("no session uuid recorded"); continue
        if pane not in panes:
            skip("pane no longer exists"); continue
        if panes[pane].get("cwd") != cwd:
            skip(f"pane cwd is {panes[pane].get('cwd')}, expected {cwd}"); continue
        if pane in busy:
            skip("claude already running"); continue

        if rc_mode == "always":
            rc = "1"
        elif rc_mode == "never":
            rc = "0"
        else:
            rc = "1" if s.get("remote_control") else "0"
        print(f"START\t{name}\t{pane}\t{uuid}\t{rc}\t-")


if __name__ == "__main__":
    main()
