#!/usr/bin/env python3
"""Unit tests for litellm_key() in tools/audit-router.py.

Tests that the function reads the LiteLLM master key from the .env file
when environment variables are empty, and prefers the environment when set.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


def _load_module():
    """Load tools/audit-router.py via an absolute path."""
    here = Path(__file__).resolve().parent
    router_path = here.parent / "tools" / "audit-router.py"
    spec = importlib.util.spec_from_file_location(
        "audit_router", str(router_path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class LitellmKeyTest(unittest.TestCase):
    """litellm_key() reads the key from the environment or a .env file."""

    def setUp(self):
        self.mod = _load_module()

    def _write_env(self, path, content):
        """Write a literal .env file (no shell evaluation)."""
        path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # env var wins
    # ------------------------------------------------------------------
    def test_env_var_wins_over_file(self):
        """When LITELLM_MASTER_KEY is set in the environment, return it."""
        key = "from-env-var"
        os.environ["LITELLM_MASTER_KEY"] = key
        # If AUTOOS_LITELLM_API_KEY is set via inheritance, remove it.
        os.environ.pop("AUTOOS_LITELLM_API_KEY", None)
        try:
            # Write a different key in a temporary .env to confirm env wins.
            with tempfile.TemporaryDirectory() as td:
                env_path = Path(td) / ".env"
                self._write_env(env_path, "LITELLM_MASTER_KEY=from-file\n")
                result = self.mod.litellm_key(env_path=str(env_path))
                self.assertEqual(result, key)
        finally:
            os.environ.pop("LITELLM_MASTER_KEY", None)

    # ------------------------------------------------------------------
    # file used when env is empty
    # ------------------------------------------------------------------
    def test_file_used_when_env_empty(self):
        """When both env vars are empty, read from the .env file."""
        os.environ.pop("LITELLM_MASTER_KEY", None)
        os.environ.pop("AUTOOS_LITELLM_API_KEY", None)
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            self._write_env(env_path, "LITELLM_MASTER_KEY=from-file-key\n")
            result = self.mod.litellm_key(env_path=str(env_path))
            self.assertEqual(result, "from-file-key")

    # ------------------------------------------------------------------
    # REPLACE_WITH_ value is ignored
    # ------------------------------------------------------------------
    def test_replace_with_value_ignored(self):
        """A value starting with REPLACE_WITH_ is treated as unset."""
        os.environ.pop("LITELLM_MASTER_KEY", None)
        os.environ.pop("AUTOOS_LITELLM_API_KEY", None)
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            self._write_env(
                env_path,
                "LITELLM_MASTER_KEY=REPLACE_WITH_YOUR_LITELLM_KEY\n",
            )
            result = self.mod.litellm_key(env_path=str(env_path))
            self.assertEqual(result, "")

    # ------------------------------------------------------------------
    # missing file returns empty string
    # ------------------------------------------------------------------
    def test_missing_file_returns_empty_string(self):
        """When the .env file does not exist, return an empty string."""
        os.environ.pop("LITELLM_MASTER_KEY", None)
        os.environ.pop("AUTOOS_LITELLM_API_KEY", None)
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".does-not-exist"
            result = self.mod.litellm_key(env_path=str(env_path))
            self.assertEqual(result, "")

    # ------------------------------------------------------------------
    # comments and blank lines are ignored
    # ------------------------------------------------------------------
    def test_ignores_comments_and_blank_lines(self):
        """Blank lines and comment lines are skipped."""
        os.environ.pop("LITELLM_MASTER_KEY", None)
        os.environ.pop("AUTOOS_LITELLM_API_KEY", None)
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            self._write_env(
                env_path,
                "\n"
                "# This is a comment\n"
                "  # line with leading whitespace\n"
                "\n"
                "LITELLM_MASTER_KEY=the-real-key\n"
                "# trailing comment\n",
            )
            result = self.mod.litellm_key(env_path=str(env_path))
            self.assertEqual(result, "the-real-key")


if __name__ == "__main__":
    unittest.main()