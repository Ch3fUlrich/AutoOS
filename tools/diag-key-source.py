#!/usr/bin/env python3
"""Diagnose a 401 from the gateway without ever touching a secret value.

Reports only metadata: which source holds a key (environment vs each
key file), the length of each candidate (never any character of it),
and each key file's mtime. Nothing secret is printed, logged or written.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from probe_common import load_agent_module  # noqa: E402


def main() -> int:
    agent = load_agent_module()
    env_key = os.environ.get(agent.ENV_KEY) if hasattr(agent, "ENV_KEY") else None
    # Fall back to the literal only inside this file (never on a shell line).
    if env_key is None and not hasattr(agent, "ENV_KEY"):
        import re  # noqa: F401
        env_key = os.environ.get("AUTOOS_OMNIROUTE_KEY")
    print("env_present=%s env_len=%s" % (
        bool(env_key), len(env_key) if env_key else 0))
    for path in agent.key_files(ROOT):
        try:
            st = os.stat(path)
            mtime = st.st_mtime_ns
            size = st.st_size
        except OSError:
            print("file=%s present=no" % path)
            continue
        val = None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    m = agent.OMNIROUTE_KEY_RE.match(line) if hasattr(
                        agent, "OMNIROUTE_KEY_RE") else None
                    if m is None:
                        import re as _re
                        m = _re.match(r"^omniroute\s*:\s*(.+?)\s*$", line)
                    if m:
                        v = m.group(1).strip("\"'")
                        val = None if v.startswith("REPLACE_WITH_") else v
                        break
        except OSError:
            val = None
        print("file=%s present=yes size=%d mtime_ns=%d has_key=%s key_len=%d" % (
            path, size, mtime, val is not None, len(val) if val else 0))
    resolved = agent.client_key(ROOT)
    print("resolved_len=%d resolved_source=%s" % (
        len(resolved) if resolved else 0,
        "env" if env_key else "file" if resolved else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
