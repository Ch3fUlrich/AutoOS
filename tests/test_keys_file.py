"""Tests for tools/keys_file.py: the one reader for a flat AutoOS keys file.

Two shapes exist and both are real: `name=value` (the api_keys.conf the
installers read) and `name: value` (configuration/api-keys.yml, the file the
browser page and tools/mirror-litellm-env.py write). Before asm-b1 each
consumer parsed only one of them, so handing the conf reader a .yml produced an
empty dict and a keyless config that looked like a machine with no keys.

Run from the repo root:

    python3 -m pytest -q tests/test_keys_file.py
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "tools" / "keys_file.py"


def load_module():
    spec = importlib.util.spec_from_file_location("keys_file", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


def read_text(text):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "keys"
        path.write_text(text, encoding="utf-8")
        return module.read_keys(path)


class ReadKeysTests(unittest.TestCase):
    def test_conf_shape(self):
        self.assertEqual(read_text("muse=DUMMY1\ndeepseek=DUMMY2\n"),
                         {"muse": "DUMMY1", "deepseek": "DUMMY2"})

    def test_yaml_shape(self):
        self.assertEqual(read_text("muse: DUMMY1\nopenrouter: DUMMY2\n"),
                         {"muse": "DUMMY1", "openrouter": "DUMMY2"})

    def test_both_shapes_in_one_file(self):
        self.assertEqual(read_text("a=1\nb: 2\n"), {"a": "1", "b": "2"})

    def test_comments_and_blank_lines_are_not_keys(self):
        self.assertEqual(read_text("# a: x\n\n  # b=1\nc=2\n"), {"c": "2"})

    def test_quotes_are_stripped(self):
        self.assertEqual(read_text("a: \"X\"\nb: 'Y'\n"), {"a": "X", "b": "Y"})

    def test_a_placeholder_value_is_never_a_key(self):
        # api-keys.example.yml and api_keys.conf.example carry REPLACE_WITH_...
        # placeholders. They are truthy, and writing one into a client config
        # produces 401s nobody can explain (the comment at the installers'
        # call sites has said this since the day it was measured).
        self.assertEqual(read_text("openrouter: REPLACE_WITH_YOUR_KEY\nmuse=REPLACE\nx: 1\n"),
                         {"x": "1"})

    def test_the_first_occurrence_of_a_key_wins(self):
        self.assertEqual(read_text("a=1\na=2\n"), {"a": "1"})

    def test_key_case_is_kept(self):
        # catalog/ai-registry.json spells SambaNova with capitals and
        # tools/mirror-litellm-env.py looks it up case-sensitively; lowercasing
        # here would silently drop that provider from the generated .env.
        self.assertEqual(read_text("sambaNovaCloud: DUMMY\n"), {"sambaNovaCloud": "DUMMY"})

    def test_the_separator_is_whichever_comes_first(self):
        # A value may itself contain the other separator.
        self.assertEqual(read_text("url: http://x/?a=b\n"), {"url": "http://x/?a=b"})
        self.assertEqual(read_text("eq=a:b\n"), {"eq": "a:b"})

    def test_a_missing_file_reads_as_no_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(module.read_keys(Path(tmp) / "absent"), {})

    def test_a_bom_is_not_part_of_the_first_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys"
            path.write_bytes("﻿muse=DUMMY1\n".encode("utf-8"))
            self.assertEqual(module.read_keys(path), {"muse": "DUMMY1"})


class SourceTests(unittest.TestCase):
    def test_no_installer_reimplements_the_parse(self):
        # Every keys read inside install.sh goes through here, so none can
        # drift back into understanding only one of the two shapes.
        # tools/mirror-litellm-env.py still has its own reader: it also parses
        # a .env and must keep failing (exit 2) on an unreadable --keys file,
        # which this helper deliberately does not do. Migrating it is a change
        # with its own consequences, not a deletion — see CHANGELOG.
        text = (ROOT / "lib" / "linux" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("keys_file", text, "install.sh does not use the shared keys reader")
        # The two shapes, spelled out by a caller instead of by this module:
        # precisely how a `.conf` reader came to be handed a `.yml`.
        for pattern in ('"=" in line', "'=' in line", 'startswith("omniroute:")',
                       "startswith('omniroute:')"):
            self.assertNotIn(pattern, text,
                             "install.sh parses a keys line itself again: " + pattern)


class CliTests(unittest.TestCase):
    def test_the_cli_prints_one_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys"
            path.write_text("omniroute: DUMMY1\nother=REPLACE_X\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE), str(path), "omniroute"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "DUMMY1")
            result = subprocess.run(
                [sys.executable, str(MODULE), str(path), "other"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "")

    def test_the_cli_never_fails_a_script_over_a_missing_file(self):
        # setup.sh runs under `set -euo pipefail`: a non-zero exit here would
        # abort the installer on a machine that simply has no keys.
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(MODULE), str(Path(tmp) / "absent"), "omniroute"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
