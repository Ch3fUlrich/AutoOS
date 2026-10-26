#!/usr/bin/env python3
"""api-keys.yml lookup helpers: key_files, client_key, zen_key (AO-SPAWNER-REMOVE S1a).

Moved unchanged out of tools/autoos-agent.py so later slices can delete the
spawner without breaking their callers. autoos-agent.py re-exports every name.
The gateway-key rule itself stays in tools/autoos_gateway_key.py; this module is
only the per-checkout keys-file discovery the two readers share.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def key_files(root: str) -> list:
    """This checkout's api-keys.yml, then the main checkout's.

    The file is git-ignored, so a lane worktree has none (measured 2026-09-25:
    exit 3 "No OmniRoute client key", and linking it in was refused).
    """
    roots = [root]
    r = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode == 0 and r.stdout.strip():
        main = os.path.dirname(r.stdout.strip())
        if os.path.realpath(main) != os.path.realpath(root):
            roots.append(main)
    return [os.path.join(r_, "configuration", "api-keys.yml") for r_ in roots]


def client_key(root: str) -> str | None:
    """The gateway client key, or None when no source has one.

    Resolution is tools/autoos_gateway_key.py's (the one rule): env
    AUTOOS_OMNIROUTE_KEY wins, then the gateway-named api-keys.yml field
    (`omniroute_server` / `omniroute_<host>`), then the legacy field with a
    deprecation line. Each checkout's file is tried in key_files() order;
    a missing key raises inside the helper and means "try the next file".
    """
    if (os.environ.get("AUTOOS_OMNIROUTE_KEY") or "").strip():
        return os.environ["AUTOOS_OMNIROUTE_KEY"]
    try:
        from autoos_gateway_key import resolve_client_key
    except ImportError:
        return None
    for path in key_files(root):
        if not os.path.isfile(path):
            continue
        try:
            return resolve_client_key(os.environ, Path(path))
        except KeyError:
            continue
    return None


# D-1214: the api-keys.yml field name the OpenCode Zen key lives under
# (moved with zen_key; autoos-agent.py keeps ZEN_KEY_ENV/ZEN_MISSING_LINE).
ZEN_KEYS_FIELD = "zen"


def zen_key(root: str) -> str | None:
    """The OpenCode Zen key, or None when no checkout's api-keys.yml holds one.

    `client_key`'s shape: each checkout's file in `key_files()` order, the field
    by NAME only, parsed by the repo's own keys reader (tools/keys_file.py) — the
    reader is what makes `REPLACE_WITH_ZEN_KEY` and an empty field read as no key
    rather than a truthy one. The value is returned or None; never printed,
    logged or recorded.
    """
    try:
        from keys_file import read_keys
    except ImportError:
        return None
    for path in key_files(root):
        if not os.path.isfile(path):
            continue
        value = read_keys(path).get(ZEN_KEYS_FIELD)
        if value:
            return value
    return None
