import importlib.util
import json
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_toolcalls",
    Path(__file__).resolve().parents[1] / "tools" / "benchmark-toolcalls.py",
)
btc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(btc)


class TestBFCLTestCases(unittest.TestCase):
    def test_simple_single_pass(self):
        tc = btc.SimpleSingleTestCase()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_current_weather",
                            "arguments": json.dumps({"location": "Boston, MA", "unit": "celsius"}),
                        }
                    }]
                }
            }]
        }
        ok, call, note = tc.evaluate_response(resp)
        self.assertTrue(ok)
        self.assertEqual(note, "ok")
        self.assertEqual(call["id"], "call_1")

    def test_simple_single_fail_wrong_tool(self):
        tc = btc.SimpleSingleTestCase()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "wrong_tool",
                            "arguments": json.dumps({"location": "Boston"}),
                        }
                    }]
                }
            }]
        }
        ok, _, note = tc.evaluate_response(resp)
        self.assertFalse(ok)
        self.assertIn("expected function get_current_weather", note)

    def test_multiple_disambiguation_pass(self):
        tc = btc.MultipleDisambiguationTestCase()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_stock",
                        "type": "function",
                        "function": {
                            "name": "fetch_stock_price",
                            "arguments": json.dumps({"symbol": "AAPL"}),
                        }
                    }]
                }
            }]
        }
        ok, call, note = tc.evaluate_response(resp)
        self.assertTrue(ok)
        self.assertEqual(note, "ok")

    def test_parallel_pass(self):
        tc = btc.ParallelTestCase()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_temperature", "arguments": json.dumps({"city": "Madrid"})},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "get_temperature", "arguments": json.dumps({"city": "London"})},
                        }
                    ]
                }
            }]
        }
        ok, calls, note = tc.evaluate_response(resp)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)

    def test_parallel_fail_only_one(self):
        tc = btc.ParallelTestCase()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_temperature", "arguments": json.dumps({"city": "Madrid"})},
                        }
                    ]
                }
            }]
        }
        ok, _, note = tc.evaluate_response(resp)
        self.assertFalse(ok)
        self.assertIn("expected at least 2 parallel tool calls", note)

    def test_relevance_negative_pass(self):
        tc = btc.RelevanceNegativeTestCase()
        resp = {
            "choices": [{
                "message": {
                    "content": "Quantum superposition is a fundamental principle of quantum mechanics where a system can exist in multiple states simultaneously."
                }
            }]
        }
        ok, _, note = tc.evaluate_response(resp)
        self.assertTrue(ok)
        self.assertEqual(note, "ok")

    def test_relevance_negative_fail_when_tool_called(self):
        tc = btc.RelevanceNegativeTestCase()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_bad",
                        "type": "function",
                        "function": {"name": "send_email", "arguments": "{}"},
                    }]
                }
            }]
        }
        ok, _, note = tc.evaluate_response(resp)
        self.assertFalse(ok)
        self.assertIn("expected 0 tool calls", note)

    def test_multi_turn_steps(self):
        tc = btc.MultiTurnTestCase()
        # Step 1
        resp1 = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "db_call_1",
                        "type": "function",
                        "function": {"name": "search_database", "arguments": json.dumps({"table": "employees", "query": "engineering"})},
                    }]
                }
            }]
        }
        ok1, call_obj, note1 = tc.evaluate_step1(resp1)
        self.assertTrue(ok1)
        self.assertEqual(call_obj["id"], "db_call_1")

        # Step 2
        resp2 = {
            "choices": [{
                "message": {
                    "content": "We found Alice Smith (ID 101) as a Senior Engineer in the engineering department."
                }
            }]
        }
        ok2, _, note2 = tc.evaluate_step2(resp2)
        self.assertTrue(ok2)
        self.assertEqual(note2, "ok")

    def test_dry_run_benchmark(self):
        results = btc.run_bfcl_benchmark("mimo/mimo-v2.6-flash", dry_run=True, verbose=False)
        self.assertTrue(results["overall_pass"])
        self.assertEqual(len(results["categories"]), 5)
        for cat in ("simple", "multiple", "parallel", "negative", "multi_turn"):
            self.assertIn(cat, results["categories"])
            self.assertEqual(results["categories"][cat]["status"], "PASS (DRY-RUN)")


if __name__ == "__main__":
    unittest.main()
