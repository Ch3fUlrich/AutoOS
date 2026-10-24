"""L3 (LESSONS-TO-ALGORITHM): an undeclared model id refusal must NAME the allowed ids.

The real failure this pins: omniroute/mimo-v2.6-flash was launched instead of
mimo/mimo-v2.6-flash (5 failed runs). A refusal that only says "not declared"
teaches the caller nothing about the id it should have typed, and the old
`e.g. omniroute/<pin>` hint even pointed at a double-qualified nonsense id for
an already-qualified pin.

Runs with the stdlib only: `python3 tests/test_spawn_pin_allowed_id.py`.
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpawnPinAllowedIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load("pin_agent", "autoos-agent.py")
        cls.cfg = cls.agent.load_jsonc(str(ROOT / "opencode.jsonc"))

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
        for pin in ("mimo/mimo-v2.6-flash", "opencode/glm-5.3-flash-free"):
            with self.subTest(pin=pin):
                self.assertEqual(
                    self.agent.resolve_model(self.cfg, 3, False, pin), pin)
        # D-2459 keeps its own wording - it already names both allowed ids.
        with self.assertRaisesRegex(
            ValueError, "D-2459.*mimo/mimo-v2.6-flash.*mimo/mimo-v2.6-pro"
        ):
            self.agent.resolve_model(self.cfg, 3, False, "omniroute/mimo-v2.6-flash")

    def test_cli_refusal_names_the_allowed_id(self):
        plan = subprocess.run(
            [sys.executable, str(ROOT / "tools/autoos-agent.py"), "run",
             "--model", "omniroute/deepseek-v4.1-flash-free", "--dry-run",
             "--isolate", "x"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(plan.returncode, 0, plan.stdout)
        self.assertIn("opencode/deepseek-v4.1-flash-free", plan.stderr)

    def test_free_pool_refusal_appends_the_allowed_ids(self):
        plan = subprocess.run(
            [sys.executable, str(ROOT / "tools/autoos-agent.py"), "run", "--free",
             "--model", "opencode/glm-5.3-flash-fre", "--dry-run",
             "--isolate", "x"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(plan.returncode, 2, plan.stdout + plan.stderr)
        self.assertIn("is not in the free pool for", plan.stderr)
        self.assertIn("opencode/glm-5.3-flash-free", plan.stderr)


if __name__ == "__main__":
    unittest.main()
