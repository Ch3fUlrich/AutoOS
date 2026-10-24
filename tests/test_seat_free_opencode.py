"""D-2449/D-2459: explicit seats must preserve their served model path."""
import importlib.util
import json
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


class SeatFreeOpencodeTests(unittest.TestCase):
    def test_seat_pin_preserves_served_model(self):
        for pin, family, served in [
            ("opencode/deepseek-v4.1-flash-free", "deepseek", "deepseek-v4.1-flash-free"),
            ("opencode/glm-5.3-flash-free", "zhipu", "glm-5.3-flash-free"),
            ("mimo/mimo-v2.6-flash", "xiaomi", "mimo/mimo-v2.6-flash"),
            ("mimo/mimo-v2.6-pro", "xiaomi", "mimo/mimo-v2.6-pro"),
        ]:
            with self.subTest(pin=pin, family=family, served=served):
                agent = load("seat_agent", "autoos-agent.py")
                cfg = agent.load_jsonc(str(ROOT / "opencode.jsonc"))
                self.assertEqual(agent.resolve_model(cfg, 3, False, pin), pin)
                provider, mid = pin.split("/", 1)
                self.assertEqual(cfg["providers"][provider]["models"][mid]["modelID"], served)
                registry = json.loads((ROOT / "catalog/ai-registry.json").read_text())
                self.assertEqual(registry["models"][mid]["family"], family)
                plan = subprocess.run([sys.executable, str(ROOT / "tools/autoos-agent.py"),
                                       "run", "--tier", "3", "--model", pin, "--dry-run",
                                       "--isolate", "--read-only", "Review seat declarations"],
                                      cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(plan.returncode, 0, plan.stderr)
                self.assertIn("--model " + pin, plan.stdout)

    def test_combo_mimo_pin_refused(self):
        for pin in ["omniroute/mimo-v2.6-flash", "omniroute/mimo-v2.6-pro",
                    "mimo-v2.6-flash", "mimo-v2.6-pro"]:
            with self.subTest(pin=pin):
                agent = load("seat_agent", "autoos-agent.py")
                cfg = agent.load_jsonc(str(ROOT / "opencode.jsonc"))
                with self.assertRaisesRegex(
                    ValueError, "D-2459.*mimo/mimo-v2.6-flash.*mimo/mimo-v2.6-pro"
                ):
                    agent.resolve_model(cfg, 3, False, pin)

    def test_unknown_zen_pin_still_refused(self):
        agent = load("seat_agent", "autoos-agent.py")
        cfg = agent.load_jsonc(str(ROOT / "opencode.jsonc"))
        with self.assertRaisesRegex(ValueError, "not declared in opencode.jsonc providers"):
            agent.resolve_model(cfg, 3, False, "opencode/undeclared-seat")

    def test_mimo_evidence_rejects_combo_served_id(self):
        evidence = load("seat_evidence", "seat-model-evidence.py")
        self.assertIsNone(evidence._served_model({"provider": "mimo", "model": "mimo-v2.6-flash"}))


if __name__ == "__main__":
    unittest.main()
