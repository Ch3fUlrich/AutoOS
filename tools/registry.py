#!/usr/bin/env python3
"""Validator for catalog/ai-registry.json (routing v2 spec section 3.1).

Two subcommands:

    python3 tools/registry.py check    [--registry PATH]
    python3 tools/registry.py validate [--registry PATH]

`check` proves the registry obeys spec 3.1's rules:

    1. every route leg - and every unavailable_legs key - resolves to a
       providers x models pair;
    2. ids are unique within and across providers/models/clients/routes; a
       route may share the id of a model one of its legs serves (a per-model
       fallback group, mapping doc Open choice 11);
    3. a privacy-sensitive route (one whose id ends in "-clean") only uses
       available legs whose provider has trains_on_prompts: false (a missing
       or null value counts as training). A leg listed in
       routes.<id>.unavailable_legs, or reached through a provider marked
       available: false, is not checked; there is no per-route exemption;
    4. providers.<id>.api_base and models.<id>.direct.base_url hold only a public
       vendor endpoint. Loopback (127.0.0.1 / localhost / ::1) is allowed ONLY in
       a model's direct.base_url; private IPv4 ranges, single-label hosts,
       .local/.lan/.internal/.vm hosts and any userinfo@ are always rejected;
    5. no key or value anywhere carries a date, except values under keys named
       source/verified/version and anything inside a $comment/comment;
    6. every key the schema marks required is present (a small hand-rolled
       structural walk of catalog/ai-registry.schema.json - no jsonschema
       dependency, matching the rest of this repo's suites, AGENTS.md section 5).

`validate` runs `check`, then re-renders the registry in-process from
tools/registry-convert.py's build_registry() and compares parsed JSON for
equality - a committed file that no longer matches its sources is drift
(spec 3.2 phase-1 gate).

Stdlib only. Path-independent: everything is anchored on the repository root
derived from this file's own location.
"""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
SCHEMA_PATH = ROOT / "catalog" / "ai-registry.schema.json"
CONVERTER_PATH = ROOT / "tools" / "registry-convert.py"

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
COMMENT_KEYS = ("$comment", "comment")
DATE_EXEMPT_KEYS = ("source", "verified", "version")
LOOPBACK_NAMES = ("localhost",)
PRIVATE_HOST_SUFFIXES = (".local", ".lan", ".internal", ".vm")
CLEAN_ROUTE_SUFFIX = "-clean"
ID_SECTIONS = ("providers", "models", "clients", "routes")


# ===========================================================================
# loading
# ===========================================================================


def _section(registry, name) -> dict:
    """A top-level section as a dict; a null or wrong-typed one reads as empty
    (rule 6 reports it) instead of crashing a later rule."""
    value = registry.get(name)
    return value if isinstance(value, dict) else {}


def load(path) -> dict:
    """Load a registry JSON document. Accepts str or Path."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# rule 1 - leg resolution
# ===========================================================================


def resolve_leg(leg, registry) -> tuple:
    """Split a `provider/model` leg at its FIRST '/' and return canonical ids.

    The prefix may be a providers key or any provider's omniroute_id; the rest
    must be a models key. Anything else raises ValueError naming the leg.
    """
    if not isinstance(leg, str):
        raise ValueError("leg %r is not a provider/model string" % (leg,))
    prefix, sep, model_id = leg.partition("/")
    if not sep:
        raise ValueError("leg %r is not a provider/model string" % (leg,))

    providers = _section(registry, "providers")
    provider_id = prefix if prefix in providers else None
    if provider_id is None:
        for candidate_id, provider in providers.items():
            if isinstance(provider, dict) and provider.get("omniroute_id") == prefix:
                provider_id = candidate_id
                break
    if provider_id is None:
        raise ValueError("leg %r: no provider matches prefix %r" % (leg, prefix))
    if model_id not in _section(registry, "models"):
        raise ValueError("leg %r: no model matches %r" % (leg, model_id))
    return provider_id, model_id


def _check_legs(registry) -> list:
    problems = []
    for route_id, route in _section(registry, "routes").items():
        if not isinstance(route, dict):
            continue
        # unavailable legs must still name real providers and models
        legs = list(route.get("legs") or []) + list(route.get("unavailable_legs") or {})
        for leg in dict.fromkeys(legs):
            try:
                resolve_leg(leg, registry)
            except ValueError:
                problems.append("unresolved leg: routes.%s leg %s" % (route_id, leg))
    return problems


# ===========================================================================
# rule 2 - id uniqueness
# ===========================================================================


def _served_models(route, registry) -> set:
    models = set()
    for leg in route.get("legs") or []:
        try:
            models.add(resolve_leg(leg, registry)[1])
        except ValueError:
            pass  # rule 1 reports it
    return models


def _check_unique_ids(registry) -> list:
    seen = {}
    for section in ID_SECTIONS:
        entries = _section(registry, section)
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            value = entry.get("id")
            if value is None:
                continue
            if section == "routes" and value in _served_models(entry, registry):
                continue  # a per-model fallback group may carry its model's id
            seen.setdefault(value, []).append("%s.%s" % (section, key))

    problems = []
    for value, where in seen.items():
        if len(where) > 1:
            problems.append("duplicate id: %s (%s)" % (value, ", ".join(where)))
    return problems


# ===========================================================================
# rule 3 - privacy-sensitive routes
# ===========================================================================


def _check_privacy(registry) -> list:
    problems = []
    for route_id, route in _section(registry, "routes").items():
        if not isinstance(route, dict) or not route_id.endswith(CLEAN_ROUTE_SUFFIX):
            continue
        unavailable = route.get("unavailable_legs") or {}
        for leg in route.get("legs") or []:
            if leg in unavailable:
                continue
            try:
                provider_id, _ = resolve_leg(leg, registry)
            except ValueError:
                continue  # rule 1 already reports an unresolved leg
            provider = _section(registry, "providers").get(provider_id, {})
            if not isinstance(provider, dict):
                continue
            if provider.get("available") is False:
                continue
            if provider.get("trains_on_prompts") is not False:  # unknown = unsafe
                problems.append("privacy: %s leg %s trains on prompts" % (route_id, leg))
    return problems


# ===========================================================================
# rule 4 - public hosts only
# ===========================================================================


def _split_host(value: str) -> tuple:
    """Return (host, has_userinfo, scheme) from a URL or bare host."""
    rest = value
    scheme = ""
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    has_userinfo = "@" in authority
    hostport = authority.rsplit("@", 1)[-1]
    if hostport.startswith("["):  # bracketed IPv6, with an optional :port
        host = hostport[1:].split("]", 1)[0]
    elif hostport.count(":") == 1:  # host:port (a bare IPv6 has 2+ colons)
        host, _ = hostport.rsplit(":", 1)
    else:
        host = hostport
    return host.lower(), has_userinfo, scheme.lower()


def _is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_host(host: str) -> bool:
    if host.endswith(PRIVATE_HOST_SUFFIXES):
        return True
    if "." not in host and ":" not in host:  # single-label intranet name
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_link_local


def _private_host_reason(value: str, allow_loopback: bool):
    """Return a short reason string when `value` is not a public https endpoint."""
    host, has_userinfo, scheme = _split_host(value)
    if has_userinfo:
        return "userinfo in URL"
    if _is_loopback(host):
        return None if allow_loopback else "loopback host"
    if _is_private_host(host):
        return "private host"
    if scheme != "https":
        return "not https"
    return None


def _check_private_hosts(registry) -> list:
    problems = []
    for provider_id, provider in _section(registry, "providers").items():
        if not isinstance(provider, dict):
            continue
        value = provider.get("api_base")
        if value and _private_host_reason(value, allow_loopback=False):
            problems.append("private host: providers.%s.api_base %s" % (provider_id, value))

    for model_id, model in _section(registry, "models").items():
        if not isinstance(model, dict):
            continue
        direct = model.get("direct")
        if not isinstance(direct, dict):
            continue
        value = direct.get("base_url")
        if value and _private_host_reason(value, allow_loopback=True):
            problems.append("private host: models.%s.direct.base_url %s" % (model_id, value))
    return problems


# ===========================================================================
# rule 5 - no dated values
# ===========================================================================


def _check_dated_values(registry) -> list:
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in COMMENT_KEYS or key in DATE_EXEMPT_KEYS:
                    continue
                child = "%s.%s" % (path, key) if path else key
                if DATE_RE.search(str(key)):
                    problems.append("dated key: %s" % child)
                if isinstance(value, str):
                    if DATE_RE.search(value):
                        problems.append("dated value: %s" % child)
                else:
                    walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                child = "%s[%d]" % (path, index)
                if isinstance(value, str):
                    if DATE_RE.search(value):
                        problems.append("dated value: %s" % child)
                else:
                    walk(value, child)

    walk(registry, "")
    return problems


# ===========================================================================
# rule 6 - schema-required keys
# ===========================================================================


def _resolve_ref(ref: str, root: dict) -> dict:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return {}
    node = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _walk_required(node, schema, root, label, problems) -> None:
    """Check presence of `required` keys, recursing through properties/items/refs.

    Deliberately not a real JSON Schema validator: no type checks, no anyOf/oneOf
    branch selection. It answers one question - is every key the schema marks
    required actually there - which is the rule registry.py check owns (spec 3.1).
    """
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        _walk_required(node, _resolve_ref(schema["$ref"], root), root, label, problems)
        return
    for sub in schema.get("allOf", []):
        _walk_required(node, sub, root, label, problems)
    if "anyOf" in schema or "oneOf" in schema:
        return

    if isinstance(node, dict):
        for key in schema.get("required", []):
            if key not in node:
                problems.append("missing: %s.%s (required key)" % (label, key))
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties")
        for key, value in node.items():
            child = "%s.%s" % (label, key)
            if key in properties:
                _walk_required(value, properties[key], root, child, problems)
            elif isinstance(additional, dict):
                _walk_required(value, additional, root, child, problems)
    elif isinstance(node, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(node):
                _walk_required(value, items, root, "%s[%d]" % (label, index), problems)


def _check_required_keys(registry, schema=None) -> list:
    if schema is None:
        schema = load(SCHEMA_PATH)
    problems = []
    _walk_required(registry, schema, schema, "", problems)
    return problems


# ===========================================================================
# check / validate
# ===========================================================================


def check_registry(registry) -> list:
    """Return every spec 3.1 problem, in rule order; empty means the registry is clean."""
    problems = []
    problems.extend(_check_legs(registry))
    problems.extend(_check_unique_ids(registry))
    problems.extend(_check_privacy(registry))
    problems.extend(_check_private_hosts(registry))
    problems.extend(_check_dated_values(registry))
    problems.extend(_check_required_keys(registry))
    return problems


def _ok_line(registry) -> str:
    return "ok: registry %s, %d routes, %d models, %d providers" % (
        registry.get("version"),
        len(registry.get("routes", {})),
        len(registry.get("models", {})),
        len(registry.get("providers", {})),
    )


def _build_fresh_registry() -> dict:
    spec = importlib.util.spec_from_file_location("autoos_registry_convert", CONVERTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_registry()


def _cmd_check(args) -> int:
    registry = load(args.registry)
    problems = check_registry(registry)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print(_ok_line(registry))
    return 0


def _cmd_validate(args) -> int:
    registry = load(args.registry)
    problems = check_registry(registry)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    fresh = _build_fresh_registry()
    if fresh != registry:
        print("drift: %s differs from a fresh registry-convert.py render" % args.registry)
        return 1
    print(_ok_line(registry))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="registry.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "validate the registry against spec 3.1"),
        ("validate", "check + confirm no drift from registry-convert.py"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH),
                         help="registry JSON to inspect (default: %(default)s)")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "validate":
        return _cmd_validate(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
