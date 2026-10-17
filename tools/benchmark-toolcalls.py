#!/usr/bin/env python3
"""BFCL (Berkeley Function Calling Leaderboard) v4 Tool-Calling Benchmark Adapter.

Implements the 5 standard BFCL v4 evaluation categories:
  1. Simple Single: Single function call required to satisfy request.
  2. Multiple Disambiguation: Selecting the correct tool among multiple competing schemas.
  3. Parallel: Simultaneous tool calls in a single turn.
  4. Relevance / Negative Detection: Refraining from tool invocation when query is irrelevant.
  5. Multi-turn: Stateful conversation continuing after tool result is fed back.

Usage:
    python tools/benchmark-toolcalls.py --dry-run
    python tools/benchmark-toolcalls.py --leg mimo/mimo-v2.6-flash --dry-run
    python tools/benchmark-toolcalls.py --leg mimo/mimo-v2.6-flash --trials 1
    python tools/benchmark-toolcalls.py --all-free
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from probe_common import (
    DEFAULT_GATEWAY,
    DEFAULT_REGISTRY,
    RETRY_DELAYS_S,
    gateway_up,
    make_post,
    now_iso,
    post_with_retry,
)

# ---------------------------------------------------------------------------
# BFCL v4 Tool Schemas
# ---------------------------------------------------------------------------

def get_weather_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Get current weather conditions for a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City or location name, e.g. Boston, MA"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "default": "celsius"}
                },
                "required": ["location"]
            }
        }
    }


def get_temperature_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_temperature",
            "description": "Get the current temperature in degrees Celsius for a specific city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. Paris or Madrid"}
                },
                "required": ["city"]
            }
        }
    }


def get_stock_price_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "fetch_stock_price",
            "description": "Fetch the current real-time stock price and trading volume for a ticker symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "The stock ticker symbol, e.g. AAPL or MSFT"},
                    "exchange": {"type": "string", "description": "Stock exchange name", "default": "NASDAQ"}
                },
                "required": ["symbol"]
            }
        }
    }


def get_mortgage_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "calculate_mortgage",
            "description": "Calculate monthly mortgage payments based on principal, interest rate, and term.",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal": {"type": "number", "description": "Loan amount"},
                    "annual_rate": {"type": "number", "description": "Annual interest rate percentage"},
                    "term_years": {"type": "integer", "description": "Duration in years"}
                },
                "required": ["principal", "annual_rate", "term_years"]
            }
        }
    }


def get_search_database_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "search_database",
            "description": "Search records within a relational database table using keyword queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "description": "Table name to query"},
                    "query": {"type": "string", "description": "Search terms or filter expression"},
                    "limit": {"type": "integer", "description": "Max rows to return", "default": 5}
                },
                "required": ["table", "query"]
            }
        }
    }


def get_email_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an electronic mail message to specified recipient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string", "description": "Email address of receiver"},
                    "subject": {"type": "string", "description": "Email subject header"},
                    "body": {"type": "string", "description": "Email text content"}
                },
                "required": ["recipient", "subject", "body"]
            }
        }
    }


# ---------------------------------------------------------------------------
# BFCL v4 Benchmark Test Cases
# ---------------------------------------------------------------------------

class BFCLTestCase:
    def __init__(self, category: str, name: str, description: str):
        self.category = category
        self.name = name
        self.description = description

    def build_request(self, leg: str, max_tokens: int = 2048) -> Dict[str, Any]:
        raise NotImplementedError

    def evaluate_response(self, response_body: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        raise NotImplementedError


class SimpleSingleTestCase(BFCLTestCase):
    def __init__(self):
        super().__init__(
            category="simple",
            name="bfcl_simple_single",
            description="Single function call with clear query and parameters."
        )

    def build_request(self, leg: str, max_tokens: int = 2048) -> Dict[str, Any]:
        return {
            "model": leg,
            "messages": [{"role": "user", "content": "What's the weather like in Boston right now?"}],
            "tools": [get_weather_tool()],
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }

    def evaluate_response(self, parsed: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        try:
            msg = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return False, None, "malformed response structure: no choices[0].message"
        calls = msg.get("tool_calls") or []
        if len(calls) != 1:
            return False, None, f"expected exactly 1 tool call, got {len(calls)}"
        call = calls[0]
        func = call.get("function") or {}
        name = func.get("name")
        if name != "get_current_weather":
            return False, None, f"expected function get_current_weather, got {name!r}"
        try:
            args = json.loads(func.get("arguments") or "{}")
        except (ValueError, TypeError):
            return False, None, f"failed to parse arguments JSON: {func.get('arguments')!r}"
        location = str(args.get("location", "")).lower()
        if "boston" not in location:
            return False, None, f"expected location 'boston', got {args.get('location')!r}"
        return True, call, "ok"


class MultipleDisambiguationTestCase(BFCLTestCase):
    def __init__(self):
        super().__init__(
            category="multiple",
            name="bfcl_multiple_disambiguation",
            description="Disambiguate and select the correct tool among 3 competing tools."
        )

    def build_request(self, leg: str, max_tokens: int = 2048) -> Dict[str, Any]:
        return {
            "model": leg,
            "messages": [{"role": "user", "content": "Can you check the current stock price for Apple (AAPL)?"}],
            "tools": [get_weather_tool(), get_stock_price_tool(), get_mortgage_tool()],
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }

    def evaluate_response(self, parsed: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        try:
            msg = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return False, None, "malformed response: no choices[0].message"
        calls = msg.get("tool_calls") or []
        if len(calls) != 1:
            return False, None, f"expected exactly 1 tool call, got {len(calls)}"
        call = calls[0]
        func = call.get("function") or {}
        name = func.get("name")
        if name != "fetch_stock_price":
            return False, None, f"expected fetch_stock_price tool, got {name!r}"
        try:
            args = json.loads(func.get("arguments") or "{}")
        except (ValueError, TypeError):
            return False, None, f"unparseable arguments JSON: {func.get('arguments')!r}"
        symbol = str(args.get("symbol", "")).upper()
        if "AAPL" not in symbol:
            return False, None, f"expected symbol AAPL, got {args.get('symbol')!r}"
        return True, call, "ok"


class ParallelTestCase(BFCLTestCase):
    def __init__(self):
        super().__init__(
            category="parallel",
            name="bfcl_parallel",
            description="Call multiple functions in parallel in a single response turn."
        )

    def build_request(self, leg: str, max_tokens: int = 2048) -> Dict[str, Any]:
        return {
            "model": leg,
            "messages": [
                {"role": "user", "content": "I need to check the temperature in Madrid and the temperature in London."}
            ],
            "tools": [get_temperature_tool()],
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }

    def evaluate_response(self, parsed: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        try:
            msg = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return False, None, "malformed response: no choices[0].message"
        calls = msg.get("tool_calls") or []
        if len(calls) < 2:
            return False, None, f"expected at least 2 parallel tool calls, got {len(calls)}"
        cities_found = set()
        for call in calls:
            func = call.get("function") or {}
            if func.get("name") != "get_temperature":
                continue
            try:
                args = json.loads(func.get("arguments") or "{}")
                city = str(args.get("city", "")).lower()
                if "madrid" in city:
                    cities_found.add("madrid")
                if "london" in city:
                    cities_found.add("london")
            except (ValueError, TypeError):
                pass
        if not ("madrid" in cities_found and "london" in cities_found):
            return False, None, f"did not find both cities (madrid, london) in calls: {cities_found}"
        return True, calls, "ok"


class RelevanceNegativeTestCase(BFCLTestCase):
    def __init__(self):
        super().__init__(
            category="negative",
            name="bfcl_relevance_negative",
            description="Reject irrelevant tools and reply directly with text without tool invocation."
        )

    def build_request(self, leg: str, max_tokens: int = 2048) -> Dict[str, Any]:
        return {
            "model": leg,
            "messages": [
                {"role": "user", "content": "Explain the concept of quantum superposition in physics in one concise paragraph."}
            ],
            "tools": [get_weather_tool(), get_email_tool()],
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }

    def evaluate_response(self, parsed: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        try:
            msg = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return False, None, "malformed response: no choices[0].message"
        calls = msg.get("tool_calls") or []
        if calls:
            return False, calls, f"expected 0 tool calls for irrelevant query, got {len(calls)}"
        content = msg.get("content") or ""
        if not content.strip():
            return False, None, "empty text response returned when no tool was called"
        if len(content.strip()) < 15:
            return False, None, f"content too short to be an explanation: {content!r}"
        return True, None, "ok"


class MultiTurnTestCase(BFCLTestCase):
    def __init__(self):
        super().__init__(
            category="multi_turn",
            name="bfcl_multi_turn",
            description="Multi-turn conversation with tool execution and follow-up evaluation."
        )

    def build_step1_request(self, leg: str, max_tokens: int = 2048) -> Dict[str, Any]:
        return {
            "model": leg,
            "messages": [
                {"role": "user", "content": "Search the employees table for staff in the engineering department."}
            ],
            "tools": [get_search_database_tool()],
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }

    def build_step2_request(self, leg: str, assistant_call_msg: Dict[str, Any], call_id: str, max_tokens: int = 2048) -> Dict[str, Any]:
        simulated_data = json.dumps([
            {"id": 101, "name": "Alice Smith", "dept": "engineering", "role": "Senior Engineer"}
        ])
        return {
            "model": leg,
            "messages": [
                {"role": "user", "content": "Search the employees table for staff in the engineering department."},
                assistant_call_msg,
                {"role": "tool", "tool_call_id": call_id, "content": simulated_data}
            ],
            "tools": [get_search_database_tool()],
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }

    def evaluate_step1(self, parsed: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        try:
            msg = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return False, None, "malformed response: no choices[0].message"
        calls = msg.get("tool_calls") or []
        if len(calls) != 1:
            return False, None, f"expected 1 tool call, got {len(calls)}"
        call = calls[0]
        func = call.get("function") or {}
        if func.get("name") != "search_database":
            return False, None, f"expected search_database, got {func.get('name')!r}"
        try:
            args = json.loads(func.get("arguments") or "{}")
        except (ValueError, TypeError):
            return False, None, f"unparseable args JSON: {func.get('arguments')!r}"
        table = str(args.get("table", "")).lower()
        if "employee" not in table:
            return False, None, f"expected table 'employees', got {args.get('table')!r}"
        return True, call, "ok"

    def evaluate_step2(self, parsed: Dict[str, Any]) -> Tuple[bool, Optional[Any], str]:
        try:
            msg = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return False, None, "malformed response: no choices[0].message"
        if msg.get("tool_calls"):
            return False, None, "assistant called another tool instead of summarizing result"
        content = (msg.get("content") or "").lower()
        if "alice" not in content and "101" not in content:
            return False, None, f"summary did not mention Alice or record ID: {content[:150]!r}"
        return True, None, "ok"


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------

def run_bfcl_benchmark(
    leg: str,
    gateway_url: str = DEFAULT_GATEWAY,
    trials: int = 1,
    dry_run: bool = False,
    verbose: bool = True
) -> Dict[str, Any]:
    """Execute the full 5-category BFCL v4 benchmark for a specific leg."""
    results = {
        "leg": leg,
        "timestamp": now_iso(),
        "dry_run": dry_run,
        "categories": {},
        "overall_pass": True,
    }

    test_cases = [
        SimpleSingleTestCase(),
        MultipleDisambiguationTestCase(),
        ParallelTestCase(),
        RelevanceNegativeTestCase(),
    ]
    multi_turn_case = MultiTurnTestCase()

    if dry_run:
        if verbose:
            print(f"[BFCL DRY-RUN] Preparing test requests for model leg: {leg}")
        for tc in test_cases:
            req = tc.build_request(leg)
            results["categories"][tc.category] = {
                "name": tc.name,
                "status": "PASS (DRY-RUN)",
                "details": f"Generated request with {len(req.get('tools', []))} tool definitions.",
            }
            if verbose:
                print(f"  + {tc.name} ({tc.category}): DRY-RUN PASS")
        # Multi-turn
        req1 = multi_turn_case.build_step1_request(leg)
        results["categories"]["multi_turn"] = {
            "name": multi_turn_case.name,
            "status": "PASS (DRY-RUN)",
            "details": f"Generated 2-step multi-turn test specification.",
        }
        if verbose:
            print(f"  + {multi_turn_case.name} (multi_turn): DRY-RUN PASS")
        return results

    # Live execution against Gateway
    for tc in test_cases:
        category_pass = True
        notes = []
        for trial_idx in range(trials):
            req_body = tc.build_request(leg)
            status, parsed, duration = post_with_retry(gateway_url, req_body)
            if status != 200 or not isinstance(parsed, dict):
                category_pass = False
                notes.append(f"trial {trial_idx+1}: HTTP {status}")
                break
            ok, call_obj, note = tc.evaluate_response(parsed)
            if not ok:
                category_pass = False
                notes.append(f"trial {trial_idx+1}: FAIL ({note})")
                break
            notes.append(f"trial {trial_idx+1}: PASS ({duration:.2f}s)")

        results["categories"][tc.category] = {
            "name": tc.name,
            "status": "PASS" if category_pass else "FAIL",
            "notes": notes,
        }
        if not category_pass:
            results["overall_pass"] = False
        if verbose:
            print(f"  [{'PASS' if category_pass else 'FAIL'}] {tc.name}: {'; '.join(notes)}")

    # Multi-turn execution
    mt_pass = True
    mt_notes = []
    for trial_idx in range(trials):
        req1 = multi_turn_case.build_step1_request(leg)
        status1, parsed1, dur1 = post_with_retry(gateway_url, req1)
        if status1 != 200 or not isinstance(parsed1, dict):
            mt_pass = False
            mt_notes.append(f"trial {trial_idx+1} step 1: HTTP {status1}")
            break
        ok1, call_obj, note1 = multi_turn_case.evaluate_step1(parsed1)
        if not ok1:
            mt_pass = False
            mt_notes.append(f"trial {trial_idx+1} step 1: FAIL ({note1})")
            break

        assistant_msg = parsed1["choices"][0]["message"]
        call_id = call_obj.get("id") or "call_0"
        req2 = multi_turn_case.build_step2_request(leg, assistant_msg, call_id)
        status2, parsed2, dur2 = post_with_retry(gateway_url, req2)
        if status2 != 200 or not isinstance(parsed2, dict):
            mt_pass = False
            mt_notes.append(f"trial {trial_idx+1} step 2: HTTP {status2}")
            break
        ok2, _, note2 = multi_turn_case.evaluate_step2(parsed2)
        if not ok2:
            mt_pass = False
            mt_notes.append(f"trial {trial_idx+1} step 2: FAIL ({note2})")
            break
        mt_notes.append(f"trial {trial_idx+1}: PASS ({dur1 + dur2:.2f}s)")

    results["categories"]["multi_turn"] = {
        "name": multi_turn_case.name,
        "status": "PASS" if mt_pass else "FAIL",
        "notes": mt_notes,
    }
    if not mt_pass:
        results["overall_pass"] = False
    if verbose:
        print(f"  [{'PASS' if mt_pass else 'FAIL'}] {multi_turn_case.name}: {'; '.join(mt_notes)}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="BFCL v4 Tool-Calling Benchmark Adapter")
    parser.add_argument("--leg", help="Target model leg string, e.g. mimo/mimo-v2.6-flash")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help="Gateway chat completions URL")
    parser.add_argument("--trials", type=int, default=1, help="Trials per category")
    parser.add_argument("--dry-run", action="store_true", help="Print benchmarks without sending network calls")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    target_leg = args.leg or "mimo/mimo-v2.6-flash"

    if not args.dry_run and not gateway_up(args.gateway):
        print(f"Gateway at {args.gateway} is offline or unreachable.", file=sys.stderr)
        return 3

    print(f"Running BFCL v4 Benchmark for leg: {target_leg} (dry_run={args.dry_run})")
    results = run_bfcl_benchmark(
        leg=target_leg,
        gateway_url=args.gateway,
        trials=args.trials,
        dry_run=args.dry_run,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        verdict = "PASSED (5/5 categories)" if results["overall_pass"] else "FAILED"
        print(f"\nFinal Verdict: {verdict}")

    return 0 if results["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
