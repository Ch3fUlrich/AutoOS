#!/usr/bin/env python3
"""The one importlib-by-path loader for tools/registry.py.

tools/registry.py is not importable by name in every context: it lives in
tools/, which is not a package and is not on sys.path when a tool is loaded by
path (the test suites and tools/registry.py's own _load_sync_router_tiers() do
just that). tools/sync-ide-models.py, tools/audit-router.py,
tools/mirror-litellm-env.py and tools/sync-openhands-profiles.py each solved it
with an identical local `_load_registry_tool()`; this is that function's one
home now, so a change to how tools/registry.py is located happens once.

    from registry_loader import load_registry_tool

    registry = load_registry_tool()        # tools/registry.py
    registry = load_registry_tool(path)    # an explicit copy (tests)

A consumer imports this module from a guarded sys.path entry, since when it is
loaded by path the importer has not put tools/ on sys.path for it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REGISTRY_TOOL_PATH = Path(__file__).resolve().parent / "registry.py"


def load_registry_tool(path=None):
    """Import tools/registry.py by path and return the freshly executed module.

    A new module object every call - exec_module() never registers it in
    sys.modules - so no call can read another's state. `path` overrides the
    default, for tests that need a stand-in registry tool.
    """
    target = Path(path) if path is not None else REGISTRY_TOOL_PATH
    spec = importlib.util.spec_from_file_location("autoos_registry", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
