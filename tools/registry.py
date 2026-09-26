#!/usr/bin/env python3
"""Validator for catalog/ai-registry.json (routing v2 spec section 3.1).

Three subcommands:

    python3 tools/registry.py check    [--registry PATH]
    python3 tools/registry.py validate [--registry PATH]
    python3 tools/registry.py render omniroute [--registry PATH] [--out PATH] [--check]
    python3 tools/registry.py render litellm   [--registry PATH] [--config PATH] [--check] [--out PATH]
    python3 tools/registry.py render ide       [--registry PATH] [--ide-models PATH] [--check] [--out PATH]

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

`render litellm` renders the AUTOOS-MANAGED tier blocks of configuration/litellm/
config.yaml (the ones tools/sync-router-tiers.py owns) from a loaded catalog/
ai-registry.json, reusing that tool's own leg->entry logic instead of copying it
(spec 3.2 phase 1, task A4b; docs/plans/2026-09-25-registry-mapping.md section 11
documents the mapping - byte-for-byte equal to today's blocks, no documented
exception needed). It never writes configuration/litellm/config.yaml itself: with
no flag the render goes to stdout; --out PATH writes it elsewhere; --check compares
a fresh render against --config's own current managed blocks (default: the
committed config.yaml) and exits 1, naming each differing tier, when they are not
byte-for-byte equal.

`render ide` renders catalog/ide-models.json - the single source that feeds
opencode.jsonc's AUTOOS-MANAGED blocks and Zed's own model lists (both via
tools/sync-ide-models.py) - from a loaded catalog/ai-registry.json (spec 3.2
phase 1, task A4c; docs/plans/2026-09-25-registry-mapping.md section 12
documents the mapping and its one intentional equality exception, the same
$comment exception render omniroute above uses). It never writes catalog/
ide-models.json itself: with no flag the render goes to stdout; --out PATH
writes it elsewhere; --check compares a fresh render against --ide-models
(default: the committed ide-models.json) and exits 1, naming each differing
model, when they are not semantically equal. tools/sync-ide-models.py's
default (unflagged) run now sources its model list from this same render
instead of reading catalog/ide-models.json directly - its own --catalog flag
still reads that file's shape for anyone who passes it explicitly.

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
SYNC_ROUTER_TIERS_PATH = ROOT / "tools" / "sync-router-tiers.py"
DEFAULT_LITELLM_CONFIG_PATH = ROOT / "configuration" / "litellm" / "config.yaml"
DEFAULT_IDE_MODELS_PATH = ROOT / "catalog" / "ide-models.json"

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
# render - litellm config.yaml managed blocks (spec 3.2 phase 1, task A4b)
# ===========================================================================


def _load_sync_router_tiers():
    """Import tools/sync-router-tiers.py by path (its name is not a valid
    module identifier) - the same importlib-by-path technique
    _build_fresh_registry() below already uses for tools/registry-convert.py.
    Reused, not copied, so Leg/render_block/locate_blocks/parse_block/
    leading_indent/GATEWAY_ONLY/SYNCED_TIERS/provider_maps_from_dict can never
    drift from the tool that still owns configuration/litellm/config.yaml's
    actual managed blocks (task A4b's brief: "reuse ... rather than copying
    it"). A fresh module object every call, deliberately: render_litellm_
    blocks() below mutates its PROVIDER_PREFIX/API_BASE/ENV_KEY globals (Leg
    reads them at construction time, same as tools/sync-router-tiers.py's own
    main() does), and a fresh import per call keeps that mutation from
    leaking between two renders in the same process - the same isolation
    _build_fresh_registry() gets from re-importing tools/registry-convert.py
    every time it is called."""
    spec = importlib.util.spec_from_file_location(
        "autoos_sync_router_tiers", SYNC_ROUTER_TIERS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_litellm_blocks(registry: dict, config_text: str, tiers=None) -> dict:
    """Render the AUTOOS-MANAGED litellm blocks tools/sync-router-tiers.py owns
    (spec 3.2 phase 1, task A4b), sourcing what that tool takes from
    configuration/omniroute/combos.json and catalog/providers.json instead from
    the loaded registry: each tier's ordered leg list from `routes.<tier>.legs`,
    and each leg's LiteLLM transport (prefix/api_base/env var) from
    `providers.<id>.litellm_prefix`/`litellm_env`/`api_base` - registry field
    names verified identical to catalog/providers.json's own in docs/plans/
    2026-09-25-registry-mapping.md section 2, so
    tools.sync_router_tiers.provider_maps_from_dict() reads either shape the
    same way (that function was factored out of provider_maps() for exactly
    this reuse).

    `config_text` is still needed, exactly as tools/sync-router-tiers.py's own
    rewrite() needs it: to locate each tier's existing "# AUTOOS-MANAGED-START/
    END <tier>" block (its indent included) and to carry across any hand-tuned
    litellm_params line a leg already has - an rpm cap and its comment, for
    example - that is NOT a registry field (no rpm/rate-limit key exists
    anywhere in catalog/ai-registry.json or its schema) and was never derived
    from combos.json either: tools/sync-router-tiers.py has only ever preserved
    such a line across a resync, never generated it. Passing today's own
    config_text back in therefore reproduces it exactly, with no documented
    equality exception needed here (contrast render_omniroute()'s $comment/
    array-order exceptions above) - docs/plans/2026-09-25-registry-mapping.md
    section 11 has the full accounting.

    Pure with respect to I/O and the clock: the SAME (registry, config_text)
    pair always renders the SAME {tier: block_text}; no file is opened inside
    this function (the caller reads catalog/ai-registry.json and config.yaml,
    exactly like render_omniroute(registry) leaves combos.json's own read to
    its caller).

    Returns {tier: block_text}, one entry per synced tier (tools/sync-router-
    tiers.py's own SYNCED_TIERS by default - currently t2-worker/t3-driver;
    t1-orchestrator and every *-paid/*-free-only group are hand-curated, not
    sync-managed, same as today), each block running from its "# AUTOOS-
    MANAGED-START <tier>" line to its "# AUTOOS-MANAGED-END <tier>" line
    inclusive, newline-joined with no leading or trailing blank line - the
    exact slice tools/sync-router-tiers.py's own rewrite() replaces.

    Raises ValueError, naming every offending tier at once, when the registry
    has no `routes.<tier>` for a tier being rendered, or when `config_text` has
    no managed block for one (a missing/duplicate/mismatched marker - the same
    cases tools/sync-router-tiers.py itself refuses via its own ConfigError,
    surfaced here as ValueError so a caller needs only one exception type)."""
    sync = _load_sync_router_tiers()
    tiers = tuple(tiers) if tiers is not None else sync.SYNCED_TIERS

    providers = _section(registry, "providers")
    sync.PROVIDER_PREFIX, sync.API_BASE, sync.ENV_KEY = sync.provider_maps_from_dict(providers)

    routes = _section(registry, "routes")
    missing_routes = [t for t in tiers if not isinstance(routes.get(t), dict)]
    if missing_routes:
        raise ValueError("registry has no routes.<id> for tier(s): %s" % ", ".join(missing_routes))

    refs_by_tier = {}
    for tier in tiers:
        legs = routes[tier].get("legs") or []
        refs_by_tier[tier] = [
            leg for leg in legs
            if isinstance(leg, str) and leg.split("/", 1)[0] not in sync.GATEWAY_ONLY
        ]

    lines = config_text.splitlines()
    try:
        blocks = sync.locate_blocks(lines)
    except sync.ConfigError as exc:
        raise ValueError(str(exc)) from exc
    missing_blocks = [t for t in tiers if t not in blocks]
    if missing_blocks:
        raise ValueError("config.yaml has no managed block for: %s" % ", ".join(missing_blocks))

    rendered = {}
    for tier in tiers:
        start, end = blocks[tier]
        indent = sync.leading_indent(lines[start])
        extras = sync.parse_block(lines, start, end)
        try:
            block_lines = sync.render_block(tier, refs_by_tier[tier], indent, extras)
        except sync.ConfigError as exc:
            raise ValueError(str(exc)) from exc
        rendered[tier] = "\n".join(block_lines)
    return rendered


def litellm_block_text(config_text: str, tier: str) -> str:
    """The current, as-committed text of one tier's managed block ("# AUTOOS-
    MANAGED-START/END <tier>" lines inclusive), for comparing against
    render_litellm_blocks()'s output. Raises ValueError when `config_text` has
    no such block (mirrors render_litellm_blocks()'s own exception type)."""
    sync = _load_sync_router_tiers()
    lines = config_text.splitlines()
    try:
        blocks = sync.locate_blocks(lines)
    except sync.ConfigError as exc:
        raise ValueError(str(exc)) from exc
    if tier not in blocks:
        raise ValueError("config.yaml has no managed block for: %s" % tier)
    start, end = blocks[tier]
    return "\n".join(lines[start : end + 1])


def litellm_diff(rendered: dict, config_text: str) -> list:
    """Return the tiers (sorted) where a fresh render_litellm_blocks() output
    differs, byte for byte, from `config_text`'s own current managed block.
    Empty means every rendered tier matches exactly - the spec 3.2 phase-1
    gate for configuration/litellm/config.yaml (task A4b)."""
    problems = []
    for tier in sorted(rendered):
        try:
            current = litellm_block_text(config_text, tier)
        except ValueError:
            problems.append(tier)
            continue
        if rendered[tier] != current:
            problems.append(tier)
    return problems


# ===========================================================================
# render - catalog/ide-models.json (spec 3.2 phase 1, task A4c)
# ===========================================================================

# ide-models.json's own per-model shape has one flat entry per route id, not one
# per (route, gateway) - display_name/context/output/reasoning_effort were copied
# unchanged into every gateway the id lists during the A1/A2 migration (mapping
# doc section 3: "there is one name per id, not per gateway, in the old file").
# Reversing that: for each route, this is the gateway-lookup priority order used
# to pick ONE canonical value when more than one of routes.<id>.surfaces.<gateway>
# carries these fields - omniroute first only because it is present for every
# route that has either (litellm-only routes fall through to litellm). Every
# existing route's omniroute/litellm surfaces already agree on these four fields
# (verified 2026-09-26 against the committed registry - no route disagrees), so
# this ordering is never exercised as a real tie-break today; it only fixes a
# deterministic choice if that were ever to change. "openhands" is deliberately
# excluded - it is never a key of ide-models.json's own `surfaces` field (that
# file's surfaces map is gateway -> [opencode, zed, openhands] client lists, not
# a third top-level gateway of its own); spark-1.3-contributor's standalone
# surfaces.openhands.direct_profile (mapping doc section 6) is tier-profiles.json's
# concern, not this render's.
IDE_GATEWAYS = ("omniroute", "litellm")

# catalog/ide-models.json's own top-level $comment (the surfaces/gateway glossary,
# the operator's 2026-09-25 "1M tier is 1000000 everywhere" ruling, the opencode
# `variants` warning) is pure human documentation, redistributed into docs/plans/
# 2026-09-25-registry-mapping.md section 3 and section 9 during the A1/A2
# migration - the same "derived, not reproduced" treatment render_omniroute() gives
# combos.json's $comment (mapping doc section 10, exception 1). This render emits
# only the spec-3.2 marker line; ide_diff() ignores the whole "$comment" key.
IDE_GENERATED_COMMENT = "generated from catalog/ai-registry.json - do not edit"

# catalog/ide-models.json's own comment: "List order = picker order on every
# surface" - unlike combos.json's `combos`/`retired` arrays (mapping doc section
# 10, exception 2, proven insignificant by reading apply.sh/apply.ps1), nothing
# in this repository establishes that opencode.jsonc's / Zed's menu order is
# insignificant, so render_ide() reproduces today's hand-curated order exactly
# rather than adding an equality exception for it. No registry field carries this
# order (routes is a dict, and catalog/ai-registry.json's own routes keys are
# alphabetical - tools/registry-convert.py's build_registry() sorts every key on
# write); this is therefore a literal, hand-copied constant, the same convention
# tools/registry.py's own OMNIROUTE_RETIRED_IDS and tools/registry-convert.py's
# PROVIDER_EXTRA/MODEL_EXTRA/COMBO_CLASS tables use for facts no source field
# carries. A route added or removed without updating this list is never silently
# mis-ordered or dropped - render_ide() raises, naming every id the set disagrees
# on, the same "never mask a real gap" contract render_omniroute() gives a leg
# with no surfaces.omniroute.context_declared. Follow-up: a future
# `routes.<id>.surfaces.ide_order` (or similar) registry field would let this
# render drop the constant; not added here since spec 3.1 does not list one and
# task A4c's brief is "keep today's value via the render" for exactly this kind
# of gap (as A4b did for the litellm rpm lines).
IDE_MODEL_ORDER = (
    "t1-orchestrator", "t1-orchestrator-clean", "t1-orchestrator-paid",
    "t1-orchestrator-free-only",
    "t2-worker", "t2-worker-clean", "t2-worker-paid", "t2-worker-free-only",
    "t2-orchestrator",
    "t3-driver", "t3-driver-clean", "t3-driver-paid", "t3-driver-free-only",
    "spark-1.3-contributor",
    "opus-4-6",
    "t4-rag",
    "gemini-3.8-flash",
    "deepseek-v4.1-flash",
    "auto/smart", "auto", "auto/cheap",
)


def render_ide(registry: dict) -> dict:
    """Render catalog/ide-models.json's shape from a loaded catalog/ai-registry.json
    document (spec 3.2 phase 1, task A4c). Pure: no I/O, no clock, no randomness -
    the same registry always renders the same dict.

    Every route in `registry["routes"]` gets one flat entry - unlike
    render_omniroute() above, there is no legs-non-empty filter here: mapping doc
    section 3, "every ide-models id gets a route even when combos.json has no
    matching combo" (the LiteLLM-only *-paid routes, the dynamic auto* routes).
    Each entry's id/name/context/output/reasoning_effort come from the first
    gateway present in IDE_GATEWAYS priority order among
    `routes.<id>.surfaces.<gateway>` (a dict carrying "clients" - excludes a
    standalone surfaces.openhands entry, which is not this render's concern);
    `surfaces` is rebuilt as {gateway: clients} for every such gateway present.

    Raises ValueError, naming every offending id at once, when `registry["routes"]`
    and IDE_MODEL_ORDER disagree on which ids exist (a route added/removed without
    updating that constant - see its own comment above), or when a listed route has
    no omniroute/litellm surface with a "clients" list at all.
    """
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}

    wanted = set(IDE_MODEL_ORDER)
    have = set(routes)
    missing = sorted(wanted - have)
    extra = sorted(have - wanted)
    if missing or extra:
        raise ValueError(
            "IDE_MODEL_ORDER is out of sync with registry routes - missing: %s; "
            "unexpected: %s" % (", ".join(missing) or "none", ", ".join(extra) or "none"))

    models = []
    for route_id in IDE_MODEL_ORDER:
        route = routes[route_id]
        if not isinstance(route, dict):
            raise ValueError("routes.%s is not an object" % route_id)
        surfaces = route.get("surfaces")
        surfaces = surfaces if isinstance(surfaces, dict) else {}
        gateways = {
            gw: surfaces[gw] for gw in IDE_GATEWAYS
            if isinstance(surfaces.get(gw), dict) and "clients" in surfaces[gw]
        }
        if not gateways:
            raise ValueError(
                "routes.%s has no omniroute/litellm surface with a clients list" % route_id)
        canonical = gateways[next(gw for gw in IDE_GATEWAYS if gw in gateways)]

        model = {
            "id": route_id,
            "name": canonical.get("display_name"),
            "context": canonical.get("context"),
            "output": canonical.get("output"),
        }
        effort = canonical.get("effort_default")
        if effort is not None:
            model["reasoning_effort"] = effort
        model["surfaces"] = {gw: list(gateways[gw].get("clients") or []) for gw in IDE_GATEWAYS if gw in gateways}
        models.append(model)

    return {"$comment": IDE_GENERATED_COMMENT, "models": models}


def _canonical_ide(doc) -> dict:
    """Normalize an ide-models.json-shaped dict for semantic-equality comparison:
    drop "$comment" entirely (see IDE_GENERATED_COMMENT's comment above). Unlike
    _canonical_omniroute(), "models" stays an ordered list - order is semantic
    here (see IDE_MODEL_ORDER's comment)."""
    if not isinstance(doc, dict):
        return {}
    return {k: v for k, v in doc.items() if k != "$comment"}


def ide_diff(rendered: dict, current: dict) -> list:
    """Return the keys where a fresh render_ide() output and today's parsed
    catalog/ide-models.json differ, ignoring $comment (see _canonical_ide).
    Reports "models.<id>" for a content difference and "models[] order" separately
    when the two lists cover the same ids in a different order - empty means
    semantically equal, the spec 3.2 phase-1 gate for catalog/ide-models.json."""
    a = _canonical_ide(rendered)
    b = _canonical_ide(current)

    problems = []
    a_models = {m["id"]: m for m in a.get("models") or [] if isinstance(m, dict) and "id" in m}
    b_models = {m["id"]: m for m in b.get("models") or [] if isinstance(m, dict) and "id" in m}
    for model_id in sorted(set(a_models) | set(b_models)):
        if a_models.get(model_id) != b_models.get(model_id):
            problems.append("models.%s" % model_id)

    a_order = [m.get("id") for m in a.get("models") or []]
    b_order = [m.get("id") for m in b.get("models") or []]
    if not problems and a_order != b_order:
        problems.append("models[] order")

    for key in sorted((set(a) | set(b)) - {"models"}):
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


def _cmd_render_litellm(args) -> int:
    registry_doc = load(args.registry)
    config_path = Path(args.config)
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        print("cannot read %s: %s" % (config_path, exc))
        return 1

    try:
        rendered = render_litellm_blocks(registry_doc, config_text)
    except ValueError as exc:
        print(str(exc))
        return 1

    if args.check:
        problems = litellm_diff(rendered, config_text)
        if problems:
            for tier in problems:
                print("differs: %s" % tier)
            return 1
        print("ok: render litellm matches %s" % config_path)
        return 0

    text = "\n\n".join(rendered[tier] for tier in sorted(rendered)) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("wrote %s" % args.out)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_render_ide(args) -> int:
    registry_doc = load(args.registry)
    try:
        rendered = render_ide(registry_doc)
    except ValueError as exc:
        print(str(exc))
        return 1

    if args.check:
        ide_models_path = Path(args.ide_models)
        if not ide_models_path.exists():
            print("no such file: %s" % ide_models_path)
            return 1
        current = load(ide_models_path)
        problems = ide_diff(rendered, current)
        if problems:
            for key in problems:
                print("differs: %s" % key)
            return 1
        print("ok: render ide matches %s" % ide_models_path)
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

    litellm_parser = render_targets.add_parser(
        "litellm",
        help="render the AUTOOS-MANAGED tier blocks of configuration/litellm/config.yaml")
    litellm_parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_PATH),
        help="registry JSON to render from (default: %(default)s)")
    litellm_parser.add_argument(
        "--config", default=str(DEFAULT_LITELLM_CONFIG_PATH),
        help="today's config.yaml to locate blocks in / compare against "
             "(default: %(default)s)")
    litellm_parser.add_argument(
        "--out", default=None,
        help="write the render here instead of stdout (never the real config.yaml)")
    litellm_parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if a managed block differs byte-for-byte from --config")

    ide_parser = render_targets.add_parser(
        "ide", help="render catalog/ide-models.json (opencode/Zed/OpenHands model lists)")
    ide_parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_PATH),
        help="registry JSON to render from (default: %(default)s)")
    ide_parser.add_argument(
        "--out", default=None,
        help="write the render here instead of stdout (never the real ide-models.json)")
    ide_parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the render differs semantically from --ide-models")
    ide_parser.add_argument(
        "--ide-models", dest="ide_models", default=str(DEFAULT_IDE_MODELS_PATH),
        help="today's ide-models.json to compare against, --check only (default: %(default)s)")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "render":
        if args.target == "omniroute":
            return _cmd_render_omniroute(args)
        if args.target == "litellm":
            return _cmd_render_litellm(args)
        if args.target == "ide":
            return _cmd_render_ide(args)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
