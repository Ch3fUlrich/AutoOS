"""Incremental transcript identity/boundary scan for the context guard."""
import json
import re
from pathlib import Path


def scan(path, previous):
    """Return a persisted byte cursor, fleet identity, and latest boundary.

    Identity uses transcript metadata, not FLEET_SESSION_NAME (which is not
    inherited by fleet sessions). Only complete appended lines are consumed.
    The initial scan streams once; subsequent tool calls seek to the cursor.
    """
    p = Path(path)
    stat = p.stat()
    identity = [str(p.resolve()), stat.st_dev, stat.st_ino]
    state = dict(previous or {})
    offset = state.get("offset", 0)
    if (state.get("identity") != identity or not isinstance(offset, int)
            or offset < 0 or offset > stat.st_size):
        state = {}
        offset = 0
    with p.open("rb") as fh:
        fh.seek(offset)
        while True:
            line = fh.readline()
            if not line or not line.endswith(b"\n"):
                break
            offset = fh.tell()
            # Avoid JSON decoding ordinary tool/assistant records.
            if not any(key in line for key in
                       (b'compact_boundary', b'agent-name', b'custom-title')):
                continue
            try:
                obj = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(obj, dict):
                continue
            field = {"agent-name": "agentName", "custom-title": "customTitle"}.get(obj.get("type"))
            name = obj.get(field) if field else None
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-\d+", name):
                state["fleet"] = True
            if obj.get("subtype") == "compact_boundary":
                state["boundary"] = offset
    state.update(identity=identity, offset=offset)
    return state
