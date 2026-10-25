"""L3 (LESSONS-TO-ALGORITHM): an undeclared model id refusal must NAME the allowed ids.

The real failure this pins: omniroute/mimo-v2.6-flash was launched instead of
mimo/mimo-v2.6-flash (5 failed runs). A refusal that only says "not declared"
teaches the caller nothing about the id it should have typed, and the old
`e.g. omniroute/<pin>` hint even pointed at a double-qualified nonsense id for
an already-qualified pin.

Runs with the stdlib only: `python3 tests/test_spawn_pin_allowed_id.py`.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpawnPinAllowedIdTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("AUTOOS_")}
        self.env.update(AUTOOS_WORKERS_DIR=tmp.name, AUTOOS_ADMISSION_OFF="1")

    @classmethod
    def setUpClass(cls):
        cls.agent = load("pin_agent", "autoos-agent.py")
        cls.cfg = {"providers": {
            "opencode": {"models": {"deepseek-v4.1-flash-free": {}, "glm-5.3-flash-free": {}}},
            "omniroute": {"models": {"l3-implementer": {}}}}}

    def test_undeclared_pin_names_the_declared_id_for_its_mid(self):
        # The mid exists under exactly one provider - the one NOT typed.
        owners = [pid for pid, prov in self.cfg["providers"].items()
                  if "deepseek-v4.1-flash-free" in (prov.get("models") or {})]
        self.assertEqual(owners, ["opencode"])
        self.assertEqual(
            self.agent.allowed_ids_for_pin(self.cfg,
                                           "omniroute/deepseek-v4.1-flash-free"),
            ["opencode/deepseek-v4.1-flash-free"])
        with self.assertRaises(ValueError) as ctx:
            self.agent.resolve_model(self.cfg, 3, False,
                                     "omniroute/deepseek-v4.1-flash-free")
        text = str(ctx.exception)
        self.assertIn("not declared in opencode.jsonc providers", text)
        self.assertIn("opencode/deepseek-v4.1-flash-free", text)

    def test_qualified_pin_refusal_never_prints_the_old_wrong_hint(self):
        with self.assertRaises(ValueError) as ctx:
            self.agent.resolve_model(self.cfg, 3, False, "bogus/none")
        text = str(ctx.exception)
        self.assertIn("not declared in opencode.jsonc providers", text)
        self.assertNotIn("omniroute/bogus/none", text)

    def test_typo_keeps_closest_declared_mid(self):
        allowed = self.agent.allowed_ids_for_pin(
            self.cfg, "omniroute/l3-implementer-flsh")
        self.assertIn("omniroute/l3-implementer", allowed)

    def test_clean_refusal_explains_nonclean_suggestion(self):
        with self.assertRaises(ValueError) as ctx:
            self.agent.resolve_model(self.cfg, 3, True,
                                     "opencode/glm-5.3-flash-free")
        self.assertIn("drop --clean", str(ctx.exception))

    def test_allowed_ids_for_pin_on_a_synthetic_config(self):
        cfg = {"providers": {"p1": {"models": {"m-a": {}}},
                             "p2": {"models": {"m-a": {}, "m-b": {}}}}}
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "px/m-a"),
                         ["p1/m-a", "p2/m-a"])
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "px/zzz"), [])
        # a -clean twin and a #variant tail still match their mid
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "px/m-a-clean#high"),
                         ["p1/m-a", "p2/m-a"])
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "px/m-a#high"),
                         ["p1/m-a", "p2/m-a"])

    def test_declared_pins_still_resolve_and_d2459_is_unchanged(self):
        cfg = {"providers": {**self.cfg["providers"], "mimo": {"models": {"mimo-v2.6-flash": {}}}}}
        for pin in ("mimo/mimo-v2.6-flash", "opencode/glm-5.3-flash-free"):
            with self.subTest(pin=pin):
                self.assertEqual(
                    self.agent.resolve_model(cfg, 3, False, pin), pin)
        # D-2459 keeps its own wording - it already names both allowed ids.
        with self.assertRaisesRegex(
            ValueError, "D-2459.*mimo/mimo-v2.6-flash.*mimo/mimo-v2.6-pro"
        ):
            self.agent.resolve_model(self.agent.load_jsonc(str(ROOT / "opencode.jsonc")),
                                     3, False, "omniroute/mimo-v2.6-flash")

    def test_cli_refusal_names_the_allowed_id(self):
        plan = subprocess.run(
            [sys.executable, str(ROOT / "tools/autoos-agent.py"), "run",
             "--model", "omniroute/mimo-v2.6-flash", "--dry-run",
             "--isolate", "x"],
            cwd=ROOT, capture_output=True, text=True, env=self.env)
        self.assertEqual(plan.returncode, 2, plan.stdout)
        self.assertIn("mimo/mimo-v2.6-flash", plan.stderr)

    def test_free_pool_refusal_appends_the_allowed_ids(self):
        from unittest.mock import patch
        with patch.object(self.agent, "load_jsonc", return_value=self.cfg), patch.dict(os.environ, self.env, clear=True):
            import io
            from contextlib import redirect_stderr, redirect_stdout
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = self.agent.main(["run", "--free", "--model", "opencode/glm-5.3-flash-fre", "--dry-run", "--isolate", "x"])
        from types import SimpleNamespace
        plan = SimpleNamespace(returncode=rc, stdout="", stderr=err.getvalue())
        self.assertEqual(plan.returncode, 2, plan.stdout + plan.stderr)
        self.assertIn("is not in the free pool for", plan.stderr)
        self.assertIn("opencode/glm-5.3-flash-free", plan.stderr)

    def test_free_pool_without_close_mid_points_to_list(self):
        plan = subprocess.run(
            [sys.executable, str(ROOT / "tools/autoos-agent.py"), "run", "--free",
             "--model", "unknown/zzzzzzzz", "--dry-run", "--isolate", "x"],
            cwd=ROOT, capture_output=True, text=True, env=self.env)
        self.assertEqual(plan.returncode, 2, plan.stdout + plan.stderr)
        self.assertIn("see tools/autoos-agent.py list", plan.stderr)

    def test_clean_twins_only_when_requested(self):
        cfg = {"providers": {"p": {"models": {"model-a": {}, "model-a-clean": {}}}}}
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "wrong/model-a"), ["p/model-a"])
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "wrong/model-a", clean=True), ["p/model-a-clean"])

    def test_clean_only_declaration_without_clean_flag(self):
        cfg = {"providers": {"p": {"models": {"model-only-clean": {}}}}}
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "wrong/model-only"),
                         ["p/model-only-clean"])
        self.assertEqual(self.agent.allowed_ids_for_pin(cfg, "wrong/model-only-clean"),
                         ["p/model-only-clean"])


if __name__ == "__main__":
    unittest.main()
