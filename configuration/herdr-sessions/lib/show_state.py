#!/usr/bin/env python3
"""Pretty-print the recorded session state. Usage: show_state.py STATE_FILE"""
import json, sys

try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"  (unreadable state file: {exc})")
    sys.exit(0)

print("  captured:", d.get("captured_at_iso"))
for s in d.get("sessions", []):
    name = str(s.get("name"))
    pane = str(s.get("pane_id"))
    rc = str(s.get("remote_control"))
    src = s.get("uuid_source", "")
    print(f"    {name:<14} {pane:<8} rc={rc:<5} {s.get('cwd')}  [{src}]")
