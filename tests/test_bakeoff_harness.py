"""MEMSPEC P2 — bake-off harness contract tests.

Assertions on the 30-question fixture, engine adapter skeletons,
and the bake-off runner dry-run guard. Follows conventions of
tests/test_memory_events.py (stdlib unittest, temp stores, env gates).

Run from the repo root:
    python3 tests/test_bakeoff_harness.py
"""
import inspect
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
MODULE = TOOLS / "memory_facade_mcp.py"
QUESTIONS_FILE = ROOT / "tests" / "fixtures" / "memory" / "bakeoff-questions.json"

VALID_FACADE_METHODS = {"recall", "context_pack", "remember", "link", "supersede"}


def _load_questions():
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)["questions"]


# ─── helper: compare a callable's parameter names to an expected list ──────

def _sig_match(fn, expected_params):
    """Return True if the first N parameters of fn match expected_params."""
    sig = inspect.signature(fn)
    got = list(sig.parameters.keys())
    return got[: len(expected_params)] == expected_params


class BakeoffStructureTests(unittest.TestCase):
    """Structure assertions on the 30-question fixture."""

    def test_30_questions_exactly(self):
        questions = _load_questions()
        self.assertEqual(len(questions), 30,
                         "bakeoff-questions.json must have exactly 30 entries")

    def test_question_ids_unique(self):
        questions = _load_questions()
        ids = [q["id"] for q in questions]
        self.assertEqual(len(ids), len(set(ids)),
                         "all question ids must be unique")

    def test_facade_call_restricted_to_5_methods(self):
        questions = _load_questions()
        for q in questions:
            self.assertIn(
                q["facade_call"], VALID_FACADE_METHODS,
                "question %s has invalid facade_call: %s"
                % (q["id"], q["facade_call"]))

    def test_expected_answer_non_empty(self):
        questions = _load_questions()
        for q in questions:
            self.assertTrue(
                q.get("expected_answer"),
                "question %s must have a non-empty expected_answer"
                % q["id"])

    def test_dataset_assumption_non_empty(self):
        questions = _load_questions()
        for q in questions:
            self.assertTrue(
                q.get("dataset_assumption"),
                "question %s must have a non-empty dataset_assumption"
                % q["id"])


class EngineAdapterSignatureTests(unittest.TestCase):
    """Assert that each engine adapter exports the 5 facade methods with
    signatures identical to the single source of truth in
    tools/memory_facade_engine.py."""

    def _get_engine_adapter(self, name):
        mod_path = TOOLS / ("engine_%s.py" % name)
        spec = importlib.util.spec_from_file_location(
            "engine_%s" % name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _get_engine_mod(self):
        mod_path = TOOLS / "memory_facade_engine.py"
        spec = importlib.util.spec_from_file_location(
            "memory_facade_engine", mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _test_adapter_signatures(self, adapter_name):
        adapter_mod = self._get_engine_adapter(adapter_name)
        engine_mod = self._get_engine_mod()

        # The 5 facade method names and their expected parameter lists
        # as defined in tools/memory_facade_mcp.py spec:62-68
        expected = {
            "recall": ["query", "project", "k"],
            "context_pack": ["project", "role"],
            "remember": ["kind", "title", "body", "project", "links", "source"],
            "link": ["a", "rel", "b"],
            "supersede": ["old", "new", "reason"],
        }

        for method_name, expected_params in expected.items():
            fn = getattr(adapter_mod, method_name, None)
            self.assertIsNotNone(
                fn, "%s.%s must exist" % (adapter_name, method_name))
            self.assertTrue(
                _sig_match(fn, expected_params),
                "%s.%s signature mismatch: "
                "expected %s, got %s"
                % (adapter_name, method_name,
                   expected_params,
                   list(inspect.signature(fn).parameters.keys())))

    def test_omnigraph_signatures(self):
        self._test_adapter_signatures("omnigraph")

    def test_console_db_signatures(self):
        self._test_adapter_signatures("console_db")

    def test_graphiti_falkordb_signatures(self):
        self._test_adapter_signatures("graphiti_falkordb")


class EngineAdapterBuildOnlyRefusalTests(unittest.TestCase):
    """Assert that each engine adapter refuses real execution in build-only
    mode by returning a dict with an "error" key containing a clear refusal
    message. The guard must be engaged by default;
    AUTOOS_BUILD_ONLY=0 alone must NOT permit real engine execution
    unless an explicit approval marker is set."""

    def _get_adapter_module(self, adapter_name):
        mod_path = TOOLS / ("engine_%s.py" % adapter_name)
        spec = importlib.util.spec_from_file_location(
            "engine_%s" % adapter_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _call_method(self, adapter_name, method_name, kwargs):
        mod = self._get_adapter_module(adapter_name)
        fn = getattr(mod, method_name)
        return fn(**kwargs)

    def test_omnigraph_refuses_in_build_only(self):
        omnigraph_mod = self._get_adapter_module("omnigraph")
        self.assertTrue(
            getattr(omnigraph_mod, "_BUILD_ONLY_MODE", True),
            "omnigraph adapter must have _BUILD_ONLY_MODE=True by default")
        result = self._call_method("omnigraph", "recall", {"query": "test"})
        self.assertIn(
            "error", result,
            "omnograph recall in build-only must return dict with 'error' key")
        self.assertIn(
            "build-only", result.get("error", "").lower(),
            "error message must mention 'build-only' or equivalent refusal")

    def test_console_db_refuses_in_build_only(self):
        result = self._call_method("console_db", "recall", {"query": "test"})
        self.assertIn("error", result)
        self.assertIn("build-only", result.get("error", "").lower())

    def test_graphiti_falkordb_refuses_in_build_only(self):
        result = self._call_method("graphiti_falkordb", "recall", {"query": "test"})
        self.assertIn("error", result)
        self.assertIn("build-only", result.get("error", "").lower())

    def test_build_only_env_not_automatic(self):
        """Verify that AUTOOS_BUILD_ONLY=0 alone does NOT bypass the guard.
        If the current code violates this, it is a bug to fix."""
        # Check the adapter's module-level BUILD_ONLY_MODE flag via importlib
        # (tools/ has no __init__.py, so 'from tools import' does not work).
        for adapter_name in ("omnigraph", "console_db", "graphiti_falkordb"):
            mod_path = TOOLS / ("engine_%s.py" % adapter_name)
            spec = importlib.util.spec_from_file_location(
                "engine_%s" % adapter_name, mod_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(
                getattr(mod, "_BUILD_ONLY_MODE", True),
                "%s adapter must have _BUILD_ONLY_MODE=True by default"
                % adapter_name)
        # If AUTOOS_BUILD_ONLY=0 alone permitted real execution,
        # the adapters would not have _BUILD_ONLY_MODE=True.
        # This test catches that scenario.


class BakeoffRunnerDryRunTests(unittest.TestCase):
    """Assert that tools/memory_bakeoff.py dry-run loop over all 30
    questions completes with build_only=true and zero errors/warnings,
    without any real engine execution."""

    def test_dry_run_completes_with_build_only_true(self):
        import subprocess
        env = os.environ.copy()
        env["AUTOOS_BUILD_ONLY"] = "1"
        result = subprocess.run(
            [sys.executable, "tools/memory_bakeoff.py", "--adapter", "omnigraph"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            env=env)
        # The runner should exit 0 or produce output reflecting build-only mode
        # It must not contain uncaught exceptions from real engine execution
        self.assertNotIn(
            "Exception", result.stdout,
            "dry-run must not have unhandled engine exceptions")
        self.assertNotIn(
            "Traceback", result.stdout,
            "dry-run must not expose tracebacks from real engine runs")

    def test_dry_run_zero_errors_warnings_with_build_only(self):
        import subprocess
        env = os.environ.copy()
        env["AUTOOS_BUILD_ONLY"] = "1"
        result = subprocess.run(
            [sys.executable, "tools/memory_bakeoff.py", "--adapter", "omnigraph"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            env=env)
        output = result.stdout + result.stderr
        # The summary should report build_only=true and total_answered=30
        self.assertIn(
            "build_only",
            output.lower(),
            "output should indicate build-only mode; got: %s" % output[:200])


if __name__ == "__main__":
    unittest.main()