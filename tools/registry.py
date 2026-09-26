#!/usr/bin/env python3
"""Validator for catalog/ai-registry.json (routing v2 spec section 3.1).

Three subcommands:

    python3 tools/registry.py check    [--registry PATH]
    python3 tools/registry.py validate [--registry PATH]
    python3 tools/registry.py render omniroute [--registry PATH] [--out PATH] [--check]

`check` proves the registry obeys spec 3.1's rules:

    1. every route leg - and every unavailable_legs key - resolves to a
       providers x models pair;
    2. ids are unique within and across providers/models/clients/routes; a
       route may share the id of a model one of its legs serves (a per-model
       fallback group, mapping doc Open choice 11);
    3. a privacy-sensitive route (one whose id ends in "-clean") uses only
       private-safe legs (private_safe(): effective tier exactly paid or
       subscription, provider trains_on_prompts exactly false, model
       trains_on_prompts missing or exactly false) - checked for EVERY leg
       the route lists, including one flagged in unavailable_legs or reached
       through a provider marked available: false (PRIV2, 2026-09-26: the
       OmniRoute gateway does not consult that registry-only flag, and
       combos.json/apply.sh still push the leg verbatim). CLEAN_ROUTE_
       EXEMPTIONS names the one route (t1-orchestrator-clean) deliberately
       exempted from this rule; `check`/`validate` still report it, as an
       info line, never silently;
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

`render omniroute` renders configuration/omniroute/combos.json from a loaded
catalog/ai-registry.json (spec 3.2 phase 1, task A4a; docs/plans/2026-09-25-
registry-mapping.md section 10 documents the mapping and its two intentional
equality exceptions). It never writes configuration/omniroute/combos.json itself
(phase 1 proves equality only - apply.sh/apply.ps1 keep reading the committed file
until phase 2 switches them over): with no flag the render goes to stdout; --out
PATH writes it elsewhere; --check compares a fresh render against --combos
(default: the committed combos.json) and exits 1, naming each differing key, when
they are not semantically equal.

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
DEFAULT_OMNIROUTE_COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"

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


def private_safe(provider_id, model_id, registry) -> tuple:
    """Whether `provider_id`/`model_id` is private-safe (spec 3.1 rule 3, tightened
    by the PRIV finding of 2026-09-26: `autoos-agent.py route --card
    kind=implement,...,privacy=sensitive` chose a FREE pool because only a
    provider's ``trains_on_prompts`` was ever checked -- "Free first, private
    never" needs the pool's tier checked too -- and further tightened by PRIV2,
    2026-09-26: fail CLOSED on anything that is not exactly the safe shape, and
    let a model override its provider's ``tier`` too, not only its
    ``trains_on_prompts``). The single predicate both this module's rule 3
    (-clean routes) and tools/autoos_resolver.py's route-level privacy filter
    use, so the two can never drift apart.

    Safe only when ALL of:
      - the EFFECTIVE tier -- ``models.<id>.tier`` when present, else
        ``providers.<id>.tier`` -- is exactly ``"paid"`` or ``"subscription"``.
        A free pool is never private-safe, even one whose own
        ``trains_on_prompts`` is ``false`` (the found bug: groq/cerebras/
        sambanova free legs, and mistral's own ``mistral-code-latest`` free
        pool, all carry ``trains_on_prompts: false`` at the provider level and
        were wrongly treated as clean). The model-level override is additive
        (PRIV2): a provider whose own tier is ``"free"`` (e.g. ``zen``) can
        still serve one direct-key, paid leg (``deepseek-v4.1-flash``) that
        bills past the pool -- and, symmetrically, a provider whose tier is
        otherwise paid can serve one free leg that is not private-safe. An
        unrecognised or missing effective tier is unsafe, not a pass-through;
      - the provider's ``trains_on_prompts`` is exactly ``False`` (``True`` or
        missing/``None`` both count as training -- unknown is unsafe, matching
        the original rule);
      - the model's ``trains_on_prompts``, when the key is present at all, is
        exactly ``False`` -- a MISSING key is safe (inherit the provider,
        already covered above), but a present key that is anything else
        (``true``, an explicit ``null``, ``1``, ``"no"``, ...) is unsafe. This
        is deliberately fail-closed (PRIV2): only ``dict.get(...) is True``
        let a non-boolean truthy value (``1``, ``"no"``) slip through as
        "safe" before this fix.

    Returns ``(True, None)`` when safe, or ``(False, reason)`` naming which
    check failed. `provider_id`/`model_id` are assumed already resolved (rule
    1 / the resolver's own ``resolve_leg`` already reject anything that does
    not resolve); an id absent from the registry is reported as not safe
    rather than raising, so a caller never needs its own guard before calling
    this.
    """
    provider = _section(registry, "providers").get(provider_id)
    if not isinstance(provider, dict):
        return False, "unknown provider %r" % (provider_id,)
    model = _section(registry, "models").get(model_id)
    if not isinstance(model, dict):
        return False, "unknown model %r" % (model_id,)

    effective_tier = model["tier"] if "tier" in model else provider.get("tier")
    if effective_tier not in ("paid", "subscription"):
        return False, "effective tier %r is not paid or subscription" % (effective_tier,)
    if provider.get("trains_on_prompts") is not False:  # True or missing: unsafe
        return False, "trains on prompts"
    if "trains_on_prompts" in model and model["trains_on_prompts"] is not False:
        return False, "model trains on prompts"
    return True, None


# The one documented, deliberate exception to rule 3 (PRIV2, 2026-09-26):
# t1-orchestrator-clean's only leg is the OpenRouter contributor model, which
# trains by contract (see the meta/muse-spark-1.3-contributor $comment in
# catalog/ai-registry.json). combos.json's own $comment already recorded this
# trade-off on 2026-09-21 ("t1-orchestrator-clean ... no longer means
# trains-nothing - it means paid-only"); tools/autoos-agent.py's spawner
# requires an explicit --allow-training to route a sensitive card there
# (tools/autoos_routing.py select_combo()). A route named here is never
# evaluated by _check_privacy below - it is reported separately, as an
# "info:" line (privacy_exemption_lines()), never silently and never as a
# check_registry() problem. The resolver's own privacy filter
# (tools/autoos_resolver.py filter_routes()) does NOT consult this table: it
# calls private_safe() on every *available* serving leg of *every* route, and
# t1-orchestrator-clean's only leg has no available leg at all, so a
# privacy=sensitive card can never land there regardless.
CLEAN_ROUTE_EXEMPTIONS = {
    "t1-orchestrator-clean": (
        "carries the OpenRouter contributor leg "
        "(openrouter/meta/muse-spark-1.3-contributor) deliberately, since the "
        "2026-09-21 contributor-only block made it paid-only rather than "
        "trains-nothing (combos.json's own $comment); the spawner requires "
        "--allow-training to route a privacy=sensitive card there "
        "(tools/autoos-agent.py, tools/autoos_routing.py select_combo())."
    ),
}


def _check_privacy(registry) -> list:
    problems = []
    for route_id, route in _section(registry, "routes").items():
        if not isinstance(route, dict) or not route_id.endswith(CLEAN_ROUTE_SUFFIX):
            continue
        if route_id in CLEAN_ROUTE_EXEMPTIONS:
            continue  # reported separately by privacy_exemption_lines(), never here
        for leg in dict.fromkeys(route.get("legs") or []):
            try:
                provider_id, model_id = resolve_leg(leg, registry)
            except ValueError:
                continue  # rule 1 already reports an unresolved leg
            # Every leg is checked, an unavailable_legs entry or a
            # provider-wide available:false included (PRIV2, 2026-09-26): the
            # OmniRoute gateway does not consult that registry-only flag, and
            # combos.json/apply.sh push the leg verbatim regardless.
            safe, reason = private_safe(provider_id, model_id, registry)
            if not safe:
                problems.append("privacy: %s leg %s %s" % (route_id, leg, reason))
    return problems


def privacy_exemption_lines(registry) -> list:
    """One ``"info: ..."`` line per routes.<id> in CLEAN_ROUTE_EXEMPTIONS that
    exists in `registry` (PRIV2, 2026-09-26: "an exempt route is reported as
    an info line by check, never silently skipped"). Always printed by
    `check`/`validate`, regardless of whether the registry has any problem;
    never counted in check_registry()'s own return value, so an exemption
    never fails the check."""
    lines = []
    routes = _section(registry, "routes")
    for route_id, reason in CLEAN_ROUTE_EXEMPTIONS.items():
        if route_id in routes:
            lines.append("info: %s exempt from privacy rule 3 - %s" % (route_id, reason))
    return lines


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
# render - omniroute combos.json (spec 3.2 phase 1, task A4a)
# ===========================================================================

# combos.json's own top-level $comment (the ~190-line role-label glossary,
# free-first/fast-skip mechanism, per-family chain descriptions, retry settings,
# verification dates) is pure human documentation: neither apply.sh nor apply.ps1
# ever read a "$comment"/"comment" key (checked both scripts, 2026-09-26). Its
# substance was already redistributed into per-entry providers/models/routes
# $comment fields during the A1/A2 registry migration, not kept as one block -
# docs/plans/2026-09-25-registry-mapping.md section 4 and section 9 ("Where every
# $comment landed") document exactly where each fact went. Reproducing the whole
# block verbatim here would duplicate that already-migrated prose with nothing to
# keep it in sync; the render therefore emits only the spec-3.2 generated-file
# marker, and semantic equality (omniroute_diff, below) excludes the entire
# $comment key, not only this one line - see the mapping doc section 10 for this
# documented exception.
OMNIROUTE_GENERATED_COMMENT = "generated from catalog/ai-registry.json - do not edit"

# combos.json's "retired" array (pre-2026-09-23-rename dead combo ids: tier1,
# tier1-clean, ...) has no registry entry at all - docs/plans/2026-09-25-registry-
# mapping.md section 4: "these are ... dead ids with no recoverable leg/class data
# ... there is nothing to migrate". Reproduced here as a literal, hand-maintained
# constant, the same convention tools/registry-convert.py uses for facts no source
# file carries (PROVIDER_EXTRA, MODEL_EXTRA, COMBO_CLASS, ...): apply.sh/apply.ps1
# still need this exact list once a later phase switches them onto a rendered file.
OMNIROUTE_RETIRED_IDS = [
    "tier1", "tier1-clean",
    "tier2", "tier2-clean",
    "tier3", "tier3-clean",
    "rag",
    "tier1-paid", "tier2-paid", "tier3-paid",
    "tier2-credit", "tier3-credit",
]


def render_omniroute(registry: dict) -> dict:
    """Render configuration/omniroute/combos.json's shape from a loaded
    catalog/ai-registry.json document (spec 3.2 phase 1). Pure: no I/O, no clock,
    no randomness - the same registry always renders the same dict.

    A route becomes a combo iff it has at least one leg (`legs` non-empty): the
    LiteLLM-only routes (t1-orchestrator-paid, t2-worker-paid, t3-driver-paid) and
    the dynamic `auto`/`auto/smart`/`auto/cheap` routes carry `legs: []`
    (tools/registry-convert.py's ROUTE_COMMENT / AUTO_IDS) and have no
    combos.json counterpart at all - mapping doc section 4.

    Legs an operator has since flagged unavailable (routes.<id>.unavailable_legs;
    providers.openrouter.available: false) stay in `legs` unchanged - today's
    committed combos.json already lists those same dead legs (the operator chose
    to flag them in the registry rather than remove them, 2026-09-25/26), so no
    special case is needed for the render to match.
    """
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}

    combos = []
    for route_id in sorted(routes):
        route = routes[route_id]
        if not isinstance(route, dict):
            continue
        legs = route.get("legs") or []
        if not legs:
            continue
        surfaces = route.get("surfaces")
        omniroute_surface = surfaces.get("omniroute") if isinstance(surfaces, dict) else None
        if not isinstance(omniroute_surface, dict) or "context_declared" not in omniroute_surface:
            raise ValueError(
                "routes.%s has legs but no surfaces.omniroute.context_declared - "
                "every omniroute combo needs a declared context string" % route_id)
        combos.append({
            "name": route_id,
            "strategy": route.get("strategy"),
            "context": omniroute_surface["context_declared"],
            "models": list(legs),
        })

    return {
        "$comment": OMNIROUTE_GENERATED_COMMENT,
        "retired": list(OMNIROUTE_RETIRED_IDS),
        "combos": combos,
    }


def render_json(doc) -> str:
    """Serialize a rendered document with today's combos.json formatting: 2-space
    indent, insertion key order kept (no forced sort - render_omniroute already
    builds each combo in name/strategy/context/models order to match), literal
    UTF-8 (no \\uXXXX escapes), one trailing newline."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _canonical_omniroute(doc) -> dict:
    """Normalize a combos.json-shaped dict for semantic-equality comparison:

    - drop "$comment" entirely (see OMNIROUTE_GENERATED_COMMENT's comment above -
      a documented exception, not only its generated-marker line);
    - treat "combos" and "retired" as unordered: apply.sh (`for c in
      data.get("combos", [])`, `current = {c["name"] for c in ...}`) and apply.ps1
      (`foreach ($combo in $combos)`, retired filtered by `-cnotcontains`) both
      look combos up by name and retired ids up by membership, never by array
      position (read both scripts, 2026-09-26) - so array order is not semantic
      data here, unlike the ordered `models` list inside each combo (a fallback
      priority order, which this function leaves untouched).
    """
    if not isinstance(doc, dict):
        return {}
    out = {k: v for k, v in doc.items() if k != "$comment"}
    combos = out.get("combos")
    if isinstance(combos, list):
        # A malformed entry (no name, not a dict) is kept, keyed by its JSON,
        # so it shows up as a difference instead of being dropped (review-b5a4).
        out["combos"] = sorted(
            combos,
            key=lambda c: (c["name"] if isinstance(c, dict) and "name" in c
                           else "~" + json.dumps(c, sort_keys=True)),
        )
    retired = out.get("retired")
    if isinstance(retired, list):
        out["retired"] = sorted(retired, key=str)
    return out


def omniroute_diff(rendered: dict, current: dict) -> list:
    """Return the keys where a fresh render_omniroute() output and today's parsed
    combos.json differ, ignoring $comment and the order of "combos"/"retired"
    (see _canonical_omniroute). Empty means semantically equal - the spec 3.2
    phase-1 gate."""
    a = _canonical_omniroute(rendered)
    b = _canonical_omniroute(current)

    problems = []
    if a.get("retired") != b.get("retired"):
        problems.append("retired")

    def keyed(combos):
        # A malformed entry keys by its JSON, so it is a difference, not a crash.
        return {(c["name"] if isinstance(c, dict) and "name" in c
                 else "malformed:" + json.dumps(c, sort_keys=True)): c
                for c in combos or []}

    a_combos = keyed(a.get("combos"))
    b_combos = keyed(b.get("combos"))
    for name in sorted(set(a_combos) | set(b_combos)):
        if a_combos.get(name) != b_combos.get(name):
            problems.append("combos.%s" % name)

    for key in sorted((set(a) | set(b)) - {"combos", "retired"}):
        if a.get(key) != b.get(key):
            problems.append(key)

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
    for line in privacy_exemption_lines(registry):
        print(line)
    problems = check_registry(registry)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print(_ok_line(registry))
    return 0


def _cmd_validate(args) -> int:
    registry = load(args.registry)
    for line in privacy_exemption_lines(registry):
        print(line)
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


def _cmd_render_omniroute(args) -> int:
    registry_doc = load(args.registry)
    rendered = render_omniroute(registry_doc)

    if args.check:
        combos_path = Path(args.combos)
        if not combos_path.exists():
            print("no such file: %s" % combos_path)
            return 1
        current = load(combos_path)
        problems = omniroute_diff(rendered, current)
        if problems:
            for key in problems:
                print("differs: %s" % key)
            return 1
        print("ok: render omniroute matches %s" % combos_path)
        return 0

    text = render_json(rendered)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("wrote %s" % args.out)
    else:
        sys.stdout.write(text)
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

    render_parser = subparsers.add_parser(
        "render", help="render a generated file from the registry (spec 3.2)")
    render_targets = render_parser.add_subparsers(dest="target", required=True)
    omniroute_parser = render_targets.add_parser(
        "omniroute", help="render configuration/omniroute/combos.json")
    omniroute_parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_PATH),
        help="registry JSON to render from (default: %(default)s)")
    omniroute_parser.add_argument(
        "--out", default=None,
        help="write the render here instead of stdout (never the real combos.json)")
    omniroute_parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the render differs semantically from --combos")
    omniroute_parser.add_argument(
        "--combos", default=str(DEFAULT_OMNIROUTE_COMBOS_PATH),
        help="today's combos.json to compare against, --check only (default: %(default)s)")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "render":
        if args.target == "omniroute":
            return _cmd_render_omniroute(args)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
