#!/usr/bin/env python3
"""Static guarantees and least-privilege verification for Omnigraph gateway policy.

NOTE: The REAL HTTP 200/403 acceptance happens at the deploy window against
omnigraph-server (prox); this test is the static guarantee that only read and
invoke_query actions are granted to the gateway actor, and that no write-class
actions (or any unlisted actions) are ever permitted.
"""
import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUSTER_DIR = ROOT / "infra" / "mcp-servers" / "cluster"
GATEWAY_POLICY_PATH = CLUSTER_DIR / "gateway.policy.yaml"
CLUSTER_SCHEMA_PATH = CLUSTER_DIR / "cluster.schema.json"

WRITE_CLASS_ACTIONS = [
    "change",
    "schema_apply",
    "branch_create",
    "branch_delete",
    "branch_merge",
    "export",
    "load",
    "mutate",
    "admin",
]


def parse_policy_yaml(text: str) -> dict:
    """Strict standard-library parser for Omnigraph *.policy.yaml files.

    PyYAML is optional and may be absent in CI environments. This parser
    strictly handles the fixed schema used by cluster policy bundles:
      - version: <int>
      - groups: { <group>: [<actors>...] }
      - rules: [{ id: <id>, allow: { actors: { group: <g> }, actions: [<act>...] } }]
    """
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        pass

    result = {"version": None, "groups": {}, "rules": []}
    section = None
    current_rule = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("version:"):
            val = line.split(":", 1)[1].strip()
            if "#" in val:
                val = val.split("#", 1)[0].strip()
            result["version"] = int(val)
            section = None
            continue
        elif line == "groups:":
            section = "groups"
            continue
        elif line == "rules:":
            section = "rules"
            continue

        if section == "groups":
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                if "#" in val:
                    val = val.split("#", 1)[0].strip()
                else:
                    val = val.strip()
                if val.startswith("[") and val.endswith("]"):
                    items = [x.strip() for x in val[1:-1].split(",") if x.strip()]
                    result["groups"][key] = items
                elif not val:
                    result["groups"][key] = []
        elif section == "rules":
            if line.startswith("- id:"):
                rule_id = line.split(":", 1)[1].strip()
                if "#" in rule_id:
                    rule_id = rule_id.split("#", 1)[0].strip()
                current_rule = {"id": rule_id, "allow": {"actors": {}, "actions": []}}
                result["rules"].append(current_rule)
            elif current_rule is not None:
                if line.startswith("allow:"):
                    continue
                elif "actors:" in line:
                    val = line.split("actors:", 1)[1].strip()
                    if "#" in val:
                        val = val.split("#", 1)[0].strip()
                    if val.startswith("{") and val.endswith("}"):
                        inner = val[1:-1].strip()
                        actors_dict = {}
                        for part in inner.split(","):
                            if ":" in part:
                                k, v = part.split(":", 1)
                                actors_dict[k.strip()] = v.strip()
                        current_rule["allow"]["actors"] = actors_dict
                    elif val.startswith("[") and val.endswith("]"):
                        actors_list = [x.strip() for x in val[1:-1].split(",") if x.strip()]
                        current_rule["allow"]["actors"] = actors_list
                elif "actions:" in line:
                    val = line.split("actions:", 1)[1].strip()
                    if "#" in val:
                        val = val.split("#", 1)[0].strip()
                    if val.startswith("[") and val.endswith("]"):
                        actions = [x.strip() for x in val[1:-1].split(",") if x.strip()]
                        current_rule["allow"]["actions"] = actions

    return result


def evaluate_permission(bundle: dict, actor: str, action: str) -> bool:
    """Reference evaluator: actor -> groups -> rules -> allowed actions.

    Default deny: returns True ONLY if an allow rule explicitly grants `action`
    to `actor` directly or via a group `actor` belongs to.
    """
    groups = bundle.get("groups", {})
    actor_groups = {g for g, members in groups.items() if actor in members}

    allowed_actions = set()
    for rule in bundle.get("rules", []):
        allow = rule.get("allow", {})
        rule_actors = allow.get("actors", {})
        matched = False

        if isinstance(rule_actors, dict):
            if "group" in rule_actors and rule_actors["group"] in actor_groups:
                matched = True
            if "actor" in rule_actors and rule_actors["actor"] == actor:
                matched = True
        elif isinstance(rule_actors, list):
            if actor in rule_actors:
                matched = True

        if matched:
            for act in allow.get("actions", []):
                allowed_actions.add(act)

    return action in allowed_actions


class GatewayPolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(GATEWAY_POLICY_PATH.exists(), f"Missing {GATEWAY_POLICY_PATH}")
        self.raw_text = GATEWAY_POLICY_PATH.read_text(encoding="utf-8")
        self.bundle = parse_policy_yaml(self.raw_text)

    def test_docstring_states_deploy_window_acceptance(self):
        """Docstring must state that real 200/403 acceptance happens at deploy window against omnigraph-server."""
        doc = sys.modules[__name__].__doc__ or ""
        self.assertIn("deploy window", doc)
        self.assertIn("omnigraph-server", doc)
        self.assertIn("static guarantee", doc)

    def test_header_comments_contract(self):
        """Header comments must state purpose, token mapping contract, and applies_to."""
        self.assertIn("AI gateway", self.raw_text)
        self.assertIn("bearer token mapping lives on the server by actor NAME, never here", self.raw_text)
        self.assertIn("applies_to", self.raw_text)

    def test_required_keys_and_structure(self):
        """Policy bundle must have required keys: version 1, groups, and rules."""
        self.assertEqual(self.bundle.get("version"), 1)
        self.assertIn("groups", self.bundle)
        self.assertIn("gateway-readers", self.bundle["groups"])
        self.assertEqual(self.bundle["groups"]["gateway-readers"], ["gateway"])

        self.assertIn("rules", self.bundle)
        self.assertEqual(len(self.bundle["rules"]), 1)
        rule = self.bundle["rules"][0]
        self.assertEqual(rule.get("id"), "gateway-read-only")
        allow = rule.get("allow", {})
        self.assertEqual(allow.get("actors"), {"group": "gateway-readers"})
        self.assertEqual(sorted(allow.get("actions", [])), ["invoke_query", "read"])

    def test_cluster_schema_policies_structure(self):
        """cluster.schema.json policies structure must describe policy bundles."""
        self.assertTrue(CLUSTER_SCHEMA_PATH.exists(), f"Missing {CLUSTER_SCHEMA_PATH}")
        schema = json.loads(CLUSTER_SCHEMA_PATH.read_text(encoding="utf-8"))
        props = schema.get("properties", {})
        self.assertIn("policies", props)
        policies_prop = props["policies"]
        self.assertEqual(policies_prop.get("type"), "object")
        add_props = policies_prop.get("additionalProperties", {})
        self.assertEqual(add_props.get("required"), ["file"])
        file_prop = add_props.get("properties", {}).get("file", {})
        self.assertEqual(file_prop.get("type"), "string")
        applies_prop = add_props.get("properties", {}).get("applies_to", {})
        self.assertEqual(applies_prop.get("type"), "array")

    def test_gateway_allowed_read_and_invoke_query(self):
        """gateway actor is explicitly allowed read and invoke_query."""
        self.assertTrue(
            evaluate_permission(self.bundle, "gateway", "read"),
            "gateway must be allowed 'read'"
        )
        self.assertTrue(
            evaluate_permission(self.bundle, "gateway", "invoke_query"),
            "gateway must be allowed 'invoke_query'"
        )

    def _assert_policy_refuses_all_writes(self, bundle: dict, actor: str = "gateway"):
        """Helper to assert that an actor is denied all write actions and unlisted actions."""
        for action in WRITE_CLASS_ACTIONS:
            self.assertFalse(
                evaluate_permission(bundle, actor, action),
                f"Actor {actor} must be DENIED write action '{action}'"
            )
        # Check rule grant directly
        groups = bundle.get("groups", {})
        gateway_groups = {g for g, members in groups.items() if actor in members}
        for rule in bundle.get("rules", []):
            allow = rule.get("allow", {})
            actors_spec = allow.get("actors", {})
            rule_group = actors_spec.get("group") if isinstance(actors_spec, dict) else None
            if rule_group in gateway_groups or actors_spec == actor:
                actions = set(allow.get("actions", []))
                write_overlap = actions.intersection(WRITE_CLASS_ACTIONS)
                self.assertEqual(
                    write_overlap, set(),
                    f"Rule {rule.get('id')} grants write action(s) {write_overlap} to {actor}"
                )

    def test_gateway_denied_all_write_class_actions(self):
        """gateway actor is denied every write-class action."""
        self._assert_policy_refuses_all_writes(self.bundle, "gateway")

    def test_gateway_denied_unlisted_actions_default_deny(self):
        """gateway actor is denied any action not listed (default deny)."""
        unlisted_actions = [
            "delete",
            "drop_graph",
            "execute",
            "mutate_schema",
            "cluster_restart",
            "nonexistent_action",
            "sudo",
        ]
        for action in unlisted_actions:
            self.assertFalse(
                evaluate_permission(self.bundle, "gateway", action),
                f"gateway must be DENIED unlisted action '{action}' under default deny"
            )

    def test_unknown_actor_denied_everything(self):
        """An unknown actor is denied all actions including read and invoke_query."""
        unknown_actors = ["unknown", "alice", "bob", "anonymous", "evil"]
        for actor in unknown_actors:
            self.assertFalse(
                evaluate_permission(self.bundle, actor, "read"),
                f"Unknown actor '{actor}' must be denied read"
            )
            self.assertFalse(
                evaluate_permission(self.bundle, actor, "invoke_query"),
                f"Unknown actor '{actor}' must be denied invoke_query"
            )
            for action in WRITE_CLASS_ACTIONS:
                self.assertFalse(
                    evaluate_permission(self.bundle, actor, action),
                    f"Unknown actor '{actor}' must be denied '{action}'"
                )

    def test_gateway_does_not_appear_in_write_groups_across_all_tracked_policies(self):
        """Across ALL tracked *.policy.yaml files, gateway must never appear in any write-granting group."""
        tracked_policies = sorted(CLUSTER_DIR.glob("*.policy.yaml"))
        self.assertGreater(len(tracked_policies), 0, "No *.policy.yaml files found")

        for policy_path in tracked_policies:
            text = policy_path.read_text(encoding="utf-8")
            bundle = parse_policy_yaml(text)
            groups = bundle.get("groups", {})
            gateway_groups = {g for g, members in groups.items() if "gateway" in members}

            for rule in bundle.get("rules", []):
                allow = rule.get("allow", {})
                actors_spec = allow.get("actors", {})
                rule_group = actors_spec.get("group") if isinstance(actors_spec, dict) else None
                actions = set(allow.get("actions", []))
                write_overlap = actions.intersection(WRITE_CLASS_ACTIONS)

                if write_overlap:
                    # If this rule grants write actions, gateway must NOT be targeted
                    self.assertNotIn(
                        rule_group, gateway_groups,
                        f"In {policy_path.name}, rule '{rule.get('id')}' grants write actions "
                        f"{write_overlap} to group '{rule_group}' which contains 'gateway'"
                    )
                    if isinstance(actors_spec, dict):
                        self.assertNotEqual(
                            actors_spec.get("actor"), "gateway",
                            f"In {policy_path.name}, rule '{rule.get('id')}' grants write actions "
                            f"{write_overlap} directly to actor 'gateway'"
                        )

    def test_no_token_looking_strings(self):
        """The bundle file must not contain token-looking strings (long hex/base64, Bearer secrets)."""
        content = self.raw_text
        # No Bearer token string (e.g. Bearer <secret>)
        self.assertIsNone(
            re.search(r"Bearer\s+[A-Za-z0-9_\-\.]{8,}", content),
            "found Bearer token pattern in gateway.policy.yaml"
        )
        # No long hex string (32+ chars)
        self.assertIsNone(
            re.search(r"\b[0-9a-fA-F]{32,}\b", content),
            "found long hex string in gateway.policy.yaml"
        )
        # No long base64 string (32+ chars)
        self.assertIsNone(
            re.search(r"\b[A-Za-z0-9+/]{32,}={0,2}\b", content),
            "found long base64 string in gateway.policy.yaml"
        )
        # No common token prefixes
        self.assertIsNone(
            re.search(r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})", content),
            "found api token prefix in gateway.policy.yaml"
        )

    def test_mutation_proof_adding_change_fails(self):
        """Mutation proof: adding 'change' to the gateway rule causes the test to fail."""
        mutated = copy.deepcopy(self.bundle)
        for rule in mutated.get("rules", []):
            if rule.get("id") == "gateway-read-only":
                rule["allow"]["actions"].append("change")

        # 1. Reference evaluator reflects that 'change' is allowed
        self.assertTrue(
            evaluate_permission(mutated, "gateway", "change"),
            "evaluator should report change as permitted in mutant"
        )
        # 2. Rejection assertion MUST fail
        with self.assertRaises(AssertionError):
            self._assert_policy_refuses_all_writes(mutated, "gateway")


if __name__ == "__main__":
    unittest.main(verbosity=2)
