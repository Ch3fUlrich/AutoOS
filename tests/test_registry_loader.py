#!/usr/bin/env python3
"""Tests for tools/registry_loader.py - the one importlib-by-path loader for
tools/registry.py that the registry-consuming tools share.

tools/registry.py cannot be a plain `import registry` in every context (tools/
is not a package and is not always on sys.path when a tool is loaded by path),
so each consumer used to carry its own identical copy of the importlib dance.
This pins the single home: one module, `load_registry_tool(path=None)`, that
returns a fresh module per call, plus the four tools actually importing it.

Run directly:

    python3 tests/test_registry_loader.py
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
LOADER = TOOLS / "registry_loader.py"
REGISTRY = TOOLS / "registry.py"

# The four tools that each used to carry their own _load_registry_tool() copy.
CONSUMERS = (
    "sync-ide-models.py",
    "audit-router.py",
    "mirror-litellm-env.py",
    "sync-openhands-profiles.py",
)


def _load_by_path(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegistryLoaderTests(unittest.TestCase):
    """The shared loader itself, loaded by path exactly as the tools load it."""

    def setUp(self):
        self.loader = _load_by_path(LOADER, "autoos_registry_loader_test")

    def test_it_returns_the_same_module_registry_py_defines(self):
        registry = self.loader.load_registry_tool()
        # render_ide()/gateway_legs() are the two entry points the consumers
        # call; a wrong file (or a name collision) would not carry them.
        self.assertTrue(callable(getattr(registry, "render_ide", None)))
        self.assertTrue(callable(getattr(registry, "gateway_legs", None)))

    def test_the_default_path_is_tools_registry_py(self):
        self.assertEqual(self.loader.REGISTRY_TOOL_PATH.resolve(), REGISTRY.resolve())
        default = self.loader.load_registry_tool()
        explicit = self.loader.load_registry_tool(REGISTRY)
        self.assertEqual(default.__name__, explicit.__name__)

    def test_every_call_returns_a_fresh_module(self):
        # exec_module() builds a module but never registers it in sys.modules:
        # no call can leak state into (or read state from) another.
        first = self.loader.load_registry_tool()
        second = self.loader.load_registry_tool()
        self.assertIsNot(first, second)
        self.assertNotIn("autoos_registry", sys.modules)


class ConsumersShareTheOneLoaderTests(unittest.TestCase):
    """Each of the four tools imports the shared loader instead of defining
    its own."""

    def test_the_registry_model_reads_heredoc_uses_the_shared_loader(self):
        # REVFIX review 8: tests/linux/05-registry-model-reads.sh carried its
        # own spec_from_file_location("autoos_registry", ...) copy. CONSUMERS
        # only names Python tools, so a .sh copy passed the guard; pin the
        # heredoc directly.
        sh = (ROOT / "tests" / "linux" / "05-registry-model-reads.sh").read_text(
            encoding="utf-8")
        self.assertNotIn('spec_from_file_location("autoos_registry"', sh)
        self.assertIn("from registry_loader import load_registry_tool", sh)

    def test_no_consumer_still_defines_its_own_loader(self):
        for name in CONSUMERS:
            module = _load_by_path(TOOLS / name, "autoos_consumer_" + name)
            with self.subTest(tool=name):
                self.assertFalse(hasattr(module, "_load_registry_tool"),
                                 "%s still carries its own loader" % name)
                shared = getattr(module, "load_registry_tool", None)
                self.assertTrue(callable(shared), "%s does not import the loader" % name)
                # __module__ proves the binding is registry_loader's function,
                # not a same-named local redefinition.
                self.assertEqual(shared.__module__, "registry_loader", name)
                self.assertTrue(callable(getattr(shared(), "render_ide", None)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
