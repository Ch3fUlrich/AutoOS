#!/usr/bin/env python3
"""Static guarantees and least-privilege verification for Omnigraph gateway policy.

NOTE: The REAL HTTP 200/403 acceptance happens at the deploy window against
omnigraph-server (prox); this test is the static guarantee that only read and
invoke_query actions are granted to the gateway actor, and that no write-class
actions (or any unlisted actions) are ever permitted.

Known limits:
`version: 010` reads as 10 (PyYAML reads octal 8);
`[read, true]` gives the string 'true' (PyYAML a boolean);
`[read,,change]` and a non-breaking space after `actions:` are accepted by the fallback but are PyYAML errors;
indentation is not tracked (a nested or sibling `actions:` line is seen as a grant of the current rule);
all of these err toward SEEING more grants, never hiding one.
"""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUSTER_DIR = ROOT / "infra" / "mcp-servers" / "cluster"
GATEWAY_POLICY_PATH = CLUSTER_DIR / "gateway.policy.yaml"
CLUSTER_SCHEMA_PATH = CLUSTER_DIR / "cluster.schema.json"
MEMORY_POLICY_PATH = CLUSTER_DIR / "memory.policy.yaml"
PROJECT_GRAPHS_POLICY_PATH = CLUSTER_DIR / "project-graphs.policy.yaml"

WRITE_CLASS_ACTIONS = [
    "change", "schema_apply", "branch_create", "branch_delete", "branch_merge",
    "export", "load", "mutate", "admin",
]

try:
    import yaml

    class _UniqueKeySafeLoader(yaml.SafeLoader):
        """SafeLoader subclass that raises ValueError on duplicate mapping keys."""

        def construct_mapping(self, node, deep=False):
            if isinstance(node, yaml.MappingNode):
                self.flatten_mapping(node)
            seen_keys = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in seen_keys:
                    line = key_node.start_mark.line + 1 if hasattr(key_node, "start_mark") else "unknown"
                    raise ValueError(
                        f"line {line}: duplicate mapping key '{key}'; "
                        "file must be read with PyYAML or reduced to the supported shape"
                    )
                seen_keys.add(key)
            return super().construct_mapping(node, deep=deep)
except ImportError:
    yaml = None
    _UniqueKeySafeLoader = None


def _read_fallback(text: str) -> dict:
    """Strict standard-library fallback parser for Omnigraph *.policy.yaml files.

    Used when PyYAML is not installed. Strictly accepts ONLY fixed-shape constructs.
    Any other construct raises ValueError naming the line number and stating
    that the file must be read with PyYAML or reduced to the supported shape.

    Known limits:
    `version: 010` reads as 10 (PyYAML reads octal 8);
    `[read, true]` gives the string 'true' (PyYAML a boolean);
    `[read,,change]` and a non-breaking space after `actions:` are accepted by the fallback but are PyYAML errors;
    indentation is not tracked (a nested or sibling `actions:` line is seen as a grant of the current rule);
    all of these err toward SEEING more grants, never hiding one.
    """
    result = {"version": None, "groups": {}, "rules": []}
    seen_sections = set()
    seen_rule_ids = set()
    section = None
    current_rule = None
    rule_keys = set()
    allow_keys = set()
    in_allow = False

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        def _err(msg: str):
            raise ValueError(f"line {lineno}: {msg}; file must be read with PyYAML or reduced to the supported shape")

        if "\t" in raw_line:
            _err("tabs are not permitted")

        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        code_part = stripped.split("#", 1)[0].strip()
        if not code_part:
            continue

        if re.search(r"[*&!]", code_part):
            _err("anchors, aliases, and tags are not permitted")

        if code_part.count("[") != code_part.count("]") or code_part.count("{") != code_part.count("}"):
            _err("multi-line flow sequence or mapping is not permitted")

        if code_part.startswith("- ") and not code_part.startswith("- id:"):
            _err("block sequence item is not permitted")

        if code_part.startswith("version:"):
            m = re.match(r"^version:\s*(\d+)$", code_part)
            if not m:
                _err("invalid version scalar")
            if "version" in seen_sections:
                _err("duplicate 'version' key")
            seen_sections.add("version")
            result["version"] = int(m.group(1))
            section = None
            continue

        if code_part == "groups:":
            if "groups" in seen_sections:
                _err("duplicate 'groups' section")
            seen_sections.add("groups")
            section = "groups"
            continue

        if code_part == "rules:":
            if "rules" in seen_sections:
                _err("duplicate 'rules' section")
            seen_sections.add("rules")
            section = "rules"
            current_rule = None
            rule_keys = set()
            allow_keys = set()
            in_allow = False
            continue

        if section == "groups":
            m = re.match(r"^([A-Za-z0-9_-]+):\s*\[(.*)\]$", code_part)
            if not m:
                _err("unrecognized line in groups")
            group_name = m.group(1)
            items_str = m.group(2).strip()
            if group_name in result["groups"]:
                _err(f"duplicate group key '{group_name}'")
            items = []
            if items_str:
                items = [x.strip() for x in items_str.split(",") if x.strip()]
                for item in items:
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", item):
                        _err(f"invalid actor name '{item}'")
            result["groups"][group_name] = items
            continue

        if section == "rules":
            if code_part.startswith("- id:"):
                m = re.match(r"^-\s+id:\s*([A-Za-z0-9_-]+)$", code_part)
                if not m:
                    _err("invalid rule id")
                rule_id = m.group(1)
                if rule_id in seen_rule_ids:
                    _err(f"duplicate rule id '{rule_id}'")
                seen_rule_ids.add(rule_id)
                current_rule = {"id": rule_id, "allow": {"actors": {}, "actions": []}}
                result["rules"].append(current_rule)
                rule_keys = set()
                allow_keys = set()
                in_allow = False
                continue

            if current_rule is not None:
                if code_part == "allow:":
                    if "allow" in rule_keys:
                        _err(f"duplicate 'allow:' key under rule '{current_rule['id']}'")
                    rule_keys.add("allow")
                    in_allow = True
                    continue

                if in_allow:
                    if code_part.startswith("actors:"):
                        if "actors" in allow_keys:
                            _err(f"duplicate 'actors:' key under rule '{current_rule['id']}'")
                        m = re.match(r"^actors:\s*\{\s*([A-Za-z0-9_-]+):\s*([A-Za-z0-9_-]+)\s*\}$", code_part)
                        if not m:
                            _err("invalid actors format")
                        current_rule["allow"]["actors"] = {m.group(1): m.group(2)}
                        allow_keys.add("actors")
                        continue

                    if code_part.startswith("actions:"):
                        if "actions" in allow_keys:
                            _err(f"duplicate 'actions:' key under rule '{current_rule['id']}'")
                        m = re.match(r"^actions:\s*\[(.*)\]$", code_part)
                        if not m:
                            _err("invalid actions format")
                        actions_str = m.group(1).strip()
                        actions = []
                        if actions_str:
                            actions = [x.strip() for x in actions_str.split(",") if x.strip()]
                            for act in actions:
                                if not re.fullmatch(r"[A-Za-z0-9_-]+", act):
                                    _err(f"invalid action name '{act}'")
                        current_rule["allow"]["actions"] = actions
                        allow_keys.add("actions")
                        continue

            _err(f"unrecognized line '{code_part}' in rules")

        _err(f"unrecognized line '{code_part}' outside section")

    return result


def parse_policy_yaml(text: str) -> dict:
    """Strict parser for Omnigraph *.policy.yaml files."""
    if yaml is not None and _UniqueKeySafeLoader is not None:
        return yaml.load(text, Loader=_UniqueKeySafeLoader)
    return _read_fallback(text)


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
    KG_EXPORT_ACTIONS = {"read", "export", "invoke_query"}
    WORKSTATION_ACTIONS = {
        "read", "export", "invoke_query", "change", "branch_create", "branch_merge",
    }
    KNOWN_ACTIONS = sorted(set(WRITE_CLASS_ACTIONS) | {
        "read", "invoke_query", "branch_create", "branch_merge",
    })
    UNLISTED_ACTIONS = [
        "admin", "delete", "drop_graph", "graph_create", "graph_delete", "cluster_restart",
    ]

    def setUp(self):
        self.assertTrue(GATEWAY_POLICY_PATH.exists(), f"Missing {GATEWAY_POLICY_PATH}")
        self.raw_text = GATEWAY_POLICY_PATH.read_text(encoding="utf-8")
        self.bundle = parse_policy_yaml(self.raw_text)

    def test_docstring_states_deploy_window_acceptance(self):
        """Docstring must state that real 200/403 acceptance happens at deploy window against omnigraph-server."""
        doc = sys.modules[__name__].__doc__ or ""
        for expected in ("deploy window", "omnigraph-server", "static guarantee"):
            self.assertIn(expected, doc)

    def test_header_comments_contract(self):
        """Header comments must state purpose, token mapping contract, and applies_to."""
        for expected in ("AI gateway", "bearer token mapping lives on the server by actor NAME, never here", "applies_to"):
            self.assertIn(expected, self.raw_text)

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
        props = schema.get("properties", {}).get("policies", {})
        self.assertEqual(props.get("type"), "object")
        add_props = props.get("additionalProperties", {})
        self.assertEqual(add_props.get("required"), ["file"])
        self.assertEqual(add_props.get("properties", {}).get("file", {}).get("type"), "string")
        self.assertEqual(add_props.get("properties", {}).get("applies_to", {}).get("type"), "array")

    def test_gateway_allowed_read_and_invoke_query(self):
        """gateway actor is explicitly allowed read and invoke_query."""
        self.assertTrue(evaluate_permission(self.bundle, "gateway", "read"), "gateway must be allowed 'read'")
        self.assertTrue(evaluate_permission(self.bundle, "gateway", "invoke_query"), "gateway must be allowed 'invoke_query'")

    def _assert_policy_refuses_all_writes(self, bundle: dict, actor: str = "gateway"):
        """Helper to assert that an actor is denied all write actions and unlisted actions."""
        for action in WRITE_CLASS_ACTIONS:
            self.assertFalse(evaluate_permission(bundle, actor, action), f"Actor {actor} must be DENIED write action '{action}'")
        groups = bundle.get("groups", {})
        gateway_groups = {g for g, members in groups.items() if actor in members}
        for rule in bundle.get("rules", []):
            allow = rule.get("allow", {})
            actors_spec = allow.get("actors", {})
            rule_group = actors_spec.get("group") if isinstance(actors_spec, dict) else None
            if rule_group in gateway_groups or actors_spec == actor:
                actions = set(allow.get("actions", []))
                write_overlap = actions.intersection(WRITE_CLASS_ACTIONS)
                self.assertEqual(write_overlap, set(), f"Rule {rule.get('id')} grants write action(s) {write_overlap} to {actor}")

    def test_gateway_denied_all_write_class_actions(self):
        """gateway actor is denied every write-class action."""
        self._assert_policy_refuses_all_writes(self.bundle, "gateway")

    def test_gateway_denied_unlisted_actions_default_deny(self):
        """gateway actor is denied any action not listed (default deny)."""
        unlisted = ["delete", "drop_graph", "execute", "mutate_schema", "cluster_restart", "nonexistent_action", "sudo"]
        for action in unlisted:
            self.assertFalse(
                evaluate_permission(self.bundle, "gateway", action),
                f"gateway must be DENIED unlisted action '{action}' under default deny",
            )

    def test_unknown_actor_denied_everything(self):
        """An unknown actor is denied all actions including read and invoke_query."""
        for actor in ["unknown", "alice", "bob", "anonymous", "evil"]:
            self.assertFalse(evaluate_permission(self.bundle, actor, "read"), f"Unknown actor '{actor}' must be denied read")
            self.assertFalse(evaluate_permission(self.bundle, actor, "invoke_query"), f"Unknown actor '{actor}' must be denied invoke_query")
            for action in WRITE_CLASS_ACTIONS:
                self.assertFalse(evaluate_permission(self.bundle, actor, action), f"Unknown actor '{actor}' must be denied '{action}'")

    def test_gateway_does_not_appear_in_write_groups_across_all_tracked_policies(self):
        """Across ALL tracked *.policy.yaml files, gateway must never appear in any write-granting group."""
        tracked_policies = sorted(CLUSTER_DIR.glob("*.policy.yaml"))
        self.assertGreater(len(tracked_policies), 0, "No *.policy.yaml files found")
        for policy_path in tracked_policies:
            bundle = parse_policy_yaml(policy_path.read_text(encoding="utf-8"))
            gateway_groups = {g for g, members in bundle.get("groups", {}).items() if "gateway" in members}
            for rule in bundle.get("rules", []):
                allow = rule.get("allow", {})
                actors_spec = allow.get("actors", {})
                rule_group = actors_spec.get("group") if isinstance(actors_spec, dict) else None
                write_overlap = set(allow.get("actions", [])).intersection(WRITE_CLASS_ACTIONS)
                if write_overlap:
                    self.assertNotIn(
                        rule_group, gateway_groups,
                        f"In {policy_path.name}, rule '{rule.get('id')}' grants write actions {write_overlap} to group '{rule_group}' which contains 'gateway'",
                    )
                    if isinstance(actors_spec, dict):
                        self.assertNotEqual(
                            actors_spec.get("actor"), "gateway",
                            f"In {policy_path.name}, rule '{rule.get('id')}' grants write actions {write_overlap} directly to actor 'gateway'",
                        )

    def test_no_token_looking_strings(self):
        """The bundle file must not contain token-looking strings (long hex/base64, Bearer secrets)."""
        patterns = [
            (r"Bearer\s+[A-Za-z0-9_\-\.]{8,}", "found Bearer token pattern"),
            (r"\b[0-9a-fA-F]{32,}\b", "found long hex string"),
            (r"\b[A-Za-z0-9+/]{32,}={0,2}\b", "found long base64 string"),
            (r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})", "found api token prefix"),
        ]
        for pat, desc in patterns:
            self.assertIsNone(re.search(pat, self.raw_text), f"{desc} in gateway.policy.yaml")

    def test_mutation_proof_adding_change_fails(self):
        """Mutation proof: adding 'change' to the gateway rule causes the test to fail."""
        mutated = copy.deepcopy(self.bundle)
        for rule in mutated.get("rules", []):
            if rule.get("id") == "gateway-read-only":
                rule["allow"]["actions"].append("change")

        self.assertTrue(evaluate_permission(mutated, "gateway", "change"), "evaluator should report change as permitted in mutant")
        with self.assertRaises(AssertionError):
            self._assert_policy_refuses_all_writes(mutated, "gateway")

    def test_fallback_reader_parses_real_gateway_policy(self):
        """(1) The fallback reader parses the real gateway.policy.yaml to the expected structure."""
        fallback_parsed = _read_fallback(self.raw_text)
        expected = {
            "version": 1,
            "groups": {"gateway-readers": ["gateway"]},
            "rules": [{
                "id": "gateway-read-only",
                "allow": {"actors": {"group": "gateway-readers"}, "actions": ["read", "invoke_query"]},
            }],
        }
        self.assertEqual(fallback_parsed, expected)
        if _UniqueKeySafeLoader is not None:
            self.assertEqual(fallback_parsed, yaml.load(self.raw_text, Loader=_UniqueKeySafeLoader))
        for policy_file in sorted(CLUSTER_DIR.glob("*.policy.yaml")):
            parsed = _read_fallback(policy_file.read_text(encoding="utf-8"))
            self.assertEqual(parsed.get("version"), 1)
            self.assertIn("groups", parsed)
            self.assertIn("rules", parsed)

    def test_block_style_actions_raises_value_error(self):
        """(2) A block-style actions list raises ValueError; PyYAML parses it containing change."""
        block_style = (
            "version: 1\ngroups:\n  gateway-readers: [gateway]\nrules:\n"
            "  - id: gateway-read-only\n    allow:\n      actors: { group: gateway-readers }\n"
            "      actions:\n        - read\n        - change\n"
        )
        fragment = "actions:\n  - read\n  - change\n"
        for text in (block_style, fragment):
            with self.subTest(case="fallback", text=text):
                with self.assertRaises(ValueError) as ctx:
                    _read_fallback(text)
                self.assertRegex(str(ctx.exception), r"line \d+")
                self.assertIn("must be read with PyYAML or reduced to the supported shape", str(ctx.exception))

        if _UniqueKeySafeLoader is not None:
            pyyaml_parsed = yaml.load(block_style, Loader=_UniqueKeySafeLoader)
            self.assertIn("change", pyyaml_parsed["rules"][0]["allow"]["actions"])
            frag_parsed = yaml.load(fragment, Loader=_UniqueKeySafeLoader)
            self.assertIn("change", frag_parsed.get("actions", []))

    def test_unsupported_constructs_raise_value_error(self):
        """(3) Anchors/aliases, tags, multi-line flow, tabs, and duplicate group keys each raise ValueError."""
        cases = [
            ("anchor_alias", "version: 1\ngroups:\n  g1: &grp [gateway]\n  g2: *grp\nrules: []\n"),
            ("tag", "version: 1\ngroups:\n  g1: !custom [gateway]\nrules: []\n"),
            ("multiline_flow", "version: 1\ngroups:\n  gateway-readers: [\n    gateway\n  ]\nrules: []\n"),
            ("tab", "version: 1\ngroups:\n\tgateway-readers: [gateway]\nrules: []\n"),
            ("duplicate_group", "version: 1\ngroups:\n  operators: [default]\n  operators: [gateway]\nrules: []\n"),
        ]
        for name, text in cases:
            with self.subTest(construct=name):
                with self.assertRaises(ValueError) as ctx:
                    _read_fallback(text)
                self.assertRegex(str(ctx.exception), r"line \d+")
                self.assertIn("must be read with PyYAML or reduced to the supported shape", str(ctx.exception))

    def test_duplicate_and_syntax_checks_raise_value_error(self):
        """Table-driven verification of duplicate keys/sections, duplicate rule IDs, and invalid actions."""
        cases = [
            ("duplicate_actors_key", "version: 1\ngroups:\n  g: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g }\n      actors: { actor: gateway }\n      actions: [read]\n"),
            ("duplicate_allow_key", "version: 1\ngroups:\n  g: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g }\n      actions: [read]\n    allow:\n      actors: { group: g }\n      actions: [invoke_query]\n"),
            ("duplicate_rule_id", "version: 1\ngroups:\n  g: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g }\n      actions: [read]\n  - id: r1\n    allow:\n      actors: { group: g }\n      actions: [invoke_query]\n"),
            ("duplicate_version_section", "version: 1\nversion: 1\ngroups:\n  g: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g }\n      actions: [read]\n"),
            ("duplicate_groups_section", "version: 1\ngroups:\n  g1: [gateway]\ngroups:\n  g2: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g1 }\n      actions: [read]\n"),
            ("duplicate_rules_section", "version: 1\ngroups:\n  g: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g }\n      actions: [read]\nrules:\n  - id: r2\n    allow:\n      actors: { group: g }\n      actions: [read]\n"),
            ("invalid_action_name", "version: 1\ngroups:\n  g: [gateway]\nrules:\n  - id: r1\n    allow:\n      actors: { group: g }\n      actions: [read.write]\n"),
        ]
        dup_key_cases = {
            "duplicate_actors_key", "duplicate_allow_key", "duplicate_version_section",
            "duplicate_groups_section", "duplicate_rules_section",
        }
        for name, text in cases:
            with self.subTest(fallback=name):
                with self.assertRaises(ValueError):
                    _read_fallback(text)

        if _UniqueKeySafeLoader is None:
            self.skipTest("PyYAML is not installed; skipping PyYAML duplicate-key tests")

        for name, text in cases:
            if name in dup_key_cases:
                with self.subTest(pyyaml=name):
                    with self.assertRaises(ValueError):
                        parse_policy_yaml(text)

    def test_mutation_cross_bundle_check_cannot_silently_pass_with_block_style(self):
        """(4) A policy granting write rule in block style cannot silently pass cross-bundle check."""
        block_write_policy = (
            "version: 1\ngroups:\n  gateway-writers: [gateway]\nrules:\n"
            "  - id: gateway-block-write\n    allow:\n      actors: { group: gateway-writers }\n"
            "      actions:\n        - change\n"
        )
        with self.assertRaises(ValueError) as ctx:
            bundle = _read_fallback(block_write_policy)
            self._assert_policy_refuses_all_writes(bundle, "gateway")
        self.assertIn("must be read with PyYAML or reduced to the supported shape", str(ctx.exception))

        if _UniqueKeySafeLoader is not None:
            pyyaml_bundle = parse_policy_yaml(block_write_policy)
            with self.assertRaises(AssertionError):
                self._assert_policy_refuses_all_writes(pyyaml_bundle, "gateway")

    def test_duplicate_actions_key_raises_on_both_paths(self):
        """Addition (a): Duplicate 'actions:' key under one rule raises ValueError on BOTH paths."""
        duplicate_actions_policy = (
            "version: 1\ngroups:\n  gateway-readers: [gateway]\nrules:\n"
            "  - id: gateway-read-only\n    allow:\n      actors: { group: gateway-readers }\n"
            "      actions: [read]\n      actions: [change]\n"
        )
        with self.assertRaises(ValueError) as ctx_fallback:
            _read_fallback(duplicate_actions_policy)
        self.assertIn("actions", str(ctx_fallback.exception).lower())
        self.assertIn("duplicate", str(ctx_fallback.exception).lower())
        self.assertRegex(str(ctx_fallback.exception), r"line \d+")

        if _UniqueKeySafeLoader is None:
            self.skipTest("PyYAML is not installed; skipping PyYAML duplicate key test")

        with self.assertRaises(ValueError) as ctx_yaml:
            parse_policy_yaml(duplicate_actions_policy)
        self.assertIn("actions", str(ctx_yaml.exception).lower())
        self.assertIn("duplicate", str(ctx_yaml.exception).lower())

    def test_cluster_policy_grants_export_and_workstation_groups_exactly(self):
        """memory + project-graphs bundles carry the live-cluster actors: both new groups
        exist and each grants EXACTLY its action set — the workstation gets no admin,
        schema_apply, branch_delete or graph create/delete; the export actor stays read-only."""
        bundles = {
            MEMORY_POLICY_PATH: parse_policy_yaml(MEMORY_POLICY_PATH.read_text(encoding="utf-8")),
            PROJECT_GRAPHS_POLICY_PATH: parse_policy_yaml(
                PROJECT_GRAPHS_POLICY_PATH.read_text(encoding="utf-8")
            ),
        }
        for path, bundle in bundles.items():
            self.assertEqual(
                bundle.get("groups", {}).get("plangraph-kg-export"), ["plangraph-kg-export"],
                f"{path.name} must define the plangraph-kg-export group",
            )

        def granted(bundle, actor):
            return {a for a in self.KNOWN_ACTIONS + self.UNLISTED_ACTIONS
                    if evaluate_permission(bundle, actor, a)}

        projects = bundles[PROJECT_GRAPHS_POLICY_PATH]
        self.assertEqual(projects.get("groups", {}).get("workstation"), ["workstation"],
                         "project-graphs.policy.yaml must define the workstation group")

        for path, bundle in bundles.items():
            self.assertEqual(
                granted(bundle, "plangraph-kg-export"), self.KG_EXPORT_ACTIONS,
                f"{path.name}: plangraph-kg-export actor grants exactly read/export/invoke_query",
            )
        self.assertEqual(
            granted(projects, "workstation"), self.WORKSTATION_ACTIONS,
            "project-graphs.policy.yaml: workstation actor grants exactly the read/write set",
        )
        for action in ["admin", "schema_apply", "branch_delete"] + self.UNLISTED_ACTIONS:
            self.assertFalse(evaluate_permission(projects, "workstation", action),
                             f"workstation must be DENIED '{action}'")
            self.assertFalse(evaluate_permission(projects, "plangraph-kg-export", action),
                             f"plangraph-kg-export must be DENIED '{action}'")

        # The standard-library fallback reader must see the same grants, or the
        # cross-bundle write checks above would be blind to these rules.
        for path, bundle in bundles.items():
            self.assertEqual(_read_fallback(path.read_text(encoding="utf-8")), bundle,
                             f"{path.name} must parse identically on both readers")

    def test_memory_policy_declares_no_workstation_and_export_actor_holds_no_write_action(self):
        """POLICY-SYNC-PIN: memory.policy.yaml declares no `workstation` group and no rule
        whose actors reference the `workstation` group or actor, and plangraph-kg-export's
        granted set carries no write action in either bundle. Both readers are checked, so
        a grant in a shape one of them cannot parse still trips the test rather than passing."""
        pinned_write_actions = ["change", "branch_create", "branch_merge", "branch_delete",
                                "schema_apply", "admin", "load"]

        def actor_terms(actors_spec) -> list:
            """Every group/actor name a rule's `actors:` value can reference, any shape."""
            if isinstance(actors_spec, dict):
                return [str(v) for v in actors_spec.values()]
            if isinstance(actors_spec, list):
                return [str(v) for v in actors_spec]
            return [str(actors_spec)]

        for path in (MEMORY_POLICY_PATH, PROJECT_GRAPHS_POLICY_PATH):
            text = path.read_text(encoding="utf-8")
            readers = [("fallback", _read_fallback(text))]
            if _UniqueKeySafeLoader is not None:
                readers.append(("pyyaml", parse_policy_yaml(text)))

            for reader, bundle in readers:
                tag = f"{path.name} [{reader} reader]"
                if path is MEMORY_POLICY_PATH:
                    self.assertNotIn(
                        "workstation", bundle.get("groups", {}),
                        f"{tag}: memory.policy.yaml must declare no workstation group",
                    )
                    for rule in bundle.get("rules", []):
                        self.assertNotIn(
                            "workstation", actor_terms(rule.get("allow", {}).get("actors", {})),
                            f"{tag}: rule '{rule.get('id')}' must not reference the workstation group or actor",
                        )
                granted = {a for a in self.KNOWN_ACTIONS + self.UNLISTED_ACTIONS + pinned_write_actions
                           if evaluate_permission(bundle, "plangraph-kg-export", a)}
                self.assertEqual(
                    granted.intersection(pinned_write_actions), set(),
                    f"{tag}: plangraph-kg-export must be DENIED write action(s) "
                    f"{sorted(granted.intersection(pinned_write_actions))}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
