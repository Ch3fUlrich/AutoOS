#!/usr/bin/env python3
"""Validator for catalog/ai-registry.json (routing v2 spec section 3.1).

Three subcommands:

    python3 tools/registry.py check    [--registry PATH]
    python3 tools/registry.py validate [--registry PATH]
    python3 tools/registry.py render omniroute [--registry PATH] [--out PATH] [--check]
    python3 tools/registry.py render litellm   [--registry PATH] [--config PATH] [--check] [--out PATH]
    python3 tools/registry.py render ide       [--registry PATH] [--ide-models PATH] [--check] [--out PATH]
    python3 tools/registry.py render openhands   [--registry PATH] [--tier-profiles PATH] [--check] [--out PATH]
    python3 tools/registry.py render models-doc  [--registry PATH] [--docs PATH] [--check] [--out PATH]

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

`render ide` renders catalog/ide-models.json - the generated client list
(rendered from catalog/ai-registry.json - do not edit) that feeds
opencode.jsonc's AUTOOS-MANAGED blocks and Zed's own model lists (both via
tools/sync-ide-models.py) - from a loaded catalog/ai-registry.json (spec 3.2
phase 1, task A4c; docs/plans/2026-09-25-registry-mapping.md section 12
documents the mapping; task A5f made the committed file byte-exact, so the old
$comment exception no longer applies to it). To update the committed file after
a registry change: `python3 tools/registry.py render ide
--out catalog/ide-models.json`; with no flag the render goes to stdout; --out PATH
writes it elsewhere; --check compares a fresh render against --ide-models
(default: the committed ide-models.json) and exits 1, naming each differing
model, when they are not semantically equal. tools/sync-ide-models.py's
default (unflagged) run now sources its model list from this same render
instead of reading catalog/ide-models.json directly - its own --catalog flag
still reads that file's shape for anyone who passes it explicitly.

`render openhands` renders configuration/openhands/tier-profiles.json - the
single source tools/sync-openhands-profiles.py projects into real OpenHands
LLM profiles, and (via tools/sync-ide-models.py's own rewrite_tier_profiles())
the max_input_tokens/max_output_tokens the `render ide` render above already
keeps in sync there and in configuration/openhands/config.toml - from a
loaded catalog/ai-registry.json (spec 3.2 phase 1, task A4d; docs/plans/
2026-09-25-registry-mapping.md section 13 documents the mapping). It never
writes configuration/openhands/tier-profiles.json itself: with no flag the
render goes to stdout; --out PATH writes it elsewhere; --check compares a
fresh render against --tier-profiles (default: the committed tier-
profiles.json) and exits 1, naming each differing tier, when they are not
semantically equal. tools/sync-openhands-profiles.py's default (unflagged)
run now sources its spec from this same render instead of reading
configuration/openhands/tier-profiles.json directly - its own --spec flag
still reads that file's shape for anyone who passes it explicitly.

`render models-doc` renders docs/models.md's own "Tier mapping" table between
a pair of AUTOOS-MANAGED markers this task defines (spec 3.2 phase 1, task
A4e; docs/plans/2026-09-25-registry-mapping.md section 14 documents the
mapping) from a loaded catalog/ai-registry.json - every cell (route id,
class, context promise, ordered legs with an unavailable one struck through)
read from a registry field at render time, never a hand-copied constant of
the table's text. It never writes docs/models.md itself, and unlike every
other render target above there is no --write either (none of them have
one): with no flag the render goes to stdout; --out PATH writes it
elsewhere; --check compares a fresh render against --docs's own models-doc
block (default: the committed docs/models.md) and exits 1, naming each
differing route row, when they are not semantically equal. To update the
committed file after a registry change: run `python3 tools/registry.py
render models-doc --check` to see it fail, run `python3 tools/registry.py
render models-doc` and replace the text between docs/models.md's own
`<!-- AUTOOS-MANAGED-START models-doc -->` / `<!-- AUTOOS-MANAGED-END
models-doc -->` markers with its output, then re-run --check to confirm.

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
DEFAULT_TIER_PROFILES_PATH = ROOT / "configuration" / "openhands" / "tier-profiles.json"
DEFAULT_MODELS_DOC_PATH = ROOT / "docs" / "models.md"

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


def provider_field_map(providers: dict, field: str) -> dict:
    """{provider id: providers.<id>.<field>} for every entry whose field is
    truthy, in registry order.

    Small reusable loader (task A5c, spec 3.2 phase 2): the generic form of
    the provider-id -> single-field map a consumer used to build by hand
    against catalog/providers.json (tools/mirror-litellm-env.py's KEY_MAP was
    `{name: entry["litellm_env"] for name, entry in doc["providers"].items()}`
    with no presence filter - harmless against the old catalog, where every
    entry already had a real litellm_env, but wrong against this registry,
    which also carries OAuth/subscription-bridge providers (`cc`,
    `antigravity`) whose litellm_env is null). Falsy values (missing, None,
    "", 0) are dropped rather than kept as a null/empty entry, and a
    non-dict provider value is skipped defensively, matching
    provider_maps_from_dict()'s own (tools/sync-router-tiers.py) presence
    checks for its three fields."""
    return {
        name: entry[field]
        for name, entry in providers.items()
        if isinstance(entry, dict) and entry.get(field)
    }


def _legacy_sort_key(mid: str) -> tuple:
    """Order key for legacy_models(): the registry is alphabetical
    (tools/registry-convert.py's build_registry() sorts every key on write)
    and carries no trace of catalog/llm-models.json's hand-curated order, so
    that order cannot be derived -- sort instead, with `openrouter-free`
    pinned first among the openrouter entries so the generated openrouter map
    (which filters this list in order) keeps it first."""
    if mid == "openrouter-free":
        return (1, "")
    if mid.startswith("openrouter-"):
        return (2, mid)
    return (0, mid)


def legacy_models(doc) -> list:
    """Project registry `models` into the old catalog/llm-models.json entry
    shape (A5dfix: the one home for the legacy model projection every
    installer read calls instead of carrying its own copy).

    Selected, never listed: the old file's 19 entries are exactly the models
    carrying a `direct` block, so anything with one is projected and anything
    without one is skipped. Each entry maps registry fields to the old shape
    (mapping doc section 1): display_name->name, context_advertised->context,
    output_max->output, price_in/out->input/output_price,
    price_cache_read->cache_read_price, paid_price_in/out->paid_input/
    paid_output_price, a direct block with provider "openrouter" becoming
    openrouter_id (from direct.model) instead of direct -- plus default_for,
    which models.muse-spark (muse_key) and models.ollama-qwen2.5-coder
    (fallback) carry. `reasoning` is present only when truthy, matching the
    old file (which omits it otherwise); every other absent optional stays
    absent rather than becoming an explicit null.

    Pure: no I/O, no clock. `doc` is a loaded catalog/ai-registry.json
    document (or a {"models": ...} mapping holding the same entries)."""
    models = doc.get("models") if isinstance(doc, dict) else {}
    models = models if isinstance(models, dict) else {}
    out = []
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        direct = entry.get("direct")
        if not isinstance(direct, dict):
            continue
        model = {"id": mid, "name": entry.get("display_name", mid)}
        direct = dict(direct)
        if direct.get("provider") == "openrouter":
            model["openrouter_id"] = direct.get("model")
        elif direct:
            model["direct"] = direct
        model["context"] = entry.get("context_advertised")
        model["output"] = entry.get("output_max")
        if entry.get("reasoning"):
            model["reasoning"] = True
        model["input_price"] = entry.get("price_in")
        model["output_price"] = entry.get("price_out")
        if entry.get("price_cache_read") is not None:
            model["cache_read_price"] = entry["price_cache_read"]
        if entry.get("paid_price_in") is not None:
            model["paid_input_price"] = entry["paid_price_in"]
        if entry.get("paid_price_out") is not None:
            model["paid_output_price"] = entry["paid_price_out"]
        if entry.get("default_for") is not None:
            model["default_for"] = entry["default_for"]
        out.append(model)
    out.sort(key=lambda model: _legacy_sort_key(model["id"]))
    return out


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
    "cheaperinference/kimi-k3", "cheaperinference/glm-5.2",
    "samba/gpt-oss-120b", "samba/MiniMax-M3",
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
# render - openhands tier-profiles.json (spec 3.2 phase 1, task A4d)
# ===========================================================================

# configuration/openhands/tier-profiles.json's own top-level $comment (rename
# history, the openai/-prefix-stripping measurement, the push-order rationale,
# retired_ids provenance) is pure human documentation, redistributed into
# docs/plans/2026-09-25-registry-mapping.md section 6 and section 13 during
# the A1-A3 migration - the same "derived, not reproduced" treatment
# render_omniroute()/render_ide() give their own $comment (mapping doc
# sections 10 and 12). This render emits only the spec-3.2 marker line;
# openhands_diff() ignores the whole "$comment" key.
OPENHANDS_GENERATED_COMMENT = "generated from catalog/ai-registry.json - do not edit"

# gateway_base_url/litellm_base_url are container-side constants
# (host.docker.internal) shared by every omniroute-*/litellm-* profile - a
# Docker Desktop DNS convention, not a per-model registry fact (mapping doc
# section 6: "out of scope for models/routes/providers"). Reproduced here as
# literal constants, the same convention OMNIROUTE_RETIRED_IDS/IDE_MODEL_ORDER
# use for a fact no registry field carries.
OPENHANDS_GATEWAY_BASE_URL = "http://host.docker.internal:20128/v1"
OPENHANDS_LITELLM_BASE_URL = "http://host.docker.internal:4000/v1"

# tier-profiles.json's own "retired_ids" (profile ids the spec listed before
# the 2026-09-23 rename) has no registry entry at all - same category as
# OMNIROUTE_RETIRED_IDS (mapping doc section 4): "there is nothing to
# migrate". Order carries no semantics here either (tools/sync-openhands-
# profiles.py's push_profiles() immediately does `frozenset(legacy_owned)`),
# so openhands_diff() below compares it as a set, not an ordered list.
OPENHANDS_RETIRED_IDS = [
    "omniroute-tier1", "omniroute-tier1-clean",
    "omniroute-tier2", "omniroute-tier2-clean", "omniroute-tier2-credit",
    "omniroute-tier3", "omniroute-tier3-clean", "omniroute-tier3-credit",
    "omniroute-rag",
    "litellm-tier1", "litellm-tier2", "litellm-tier3",
]

# The one standalone routes.<id>.surfaces.openhands.direct_profile tier
# (mapping doc section 6: today only spark-1.3-contributor) is named after
# the OpenRouter leg's own bare model spelling ("muse-spark-1.3-contributor"),
# not the route id ("spark-1.3-contributor") - so, unlike every gateway tier
# below, its tiers[].id cannot be computed as "<gateway>-<route id>". A
# literal, hand-maintained {tier id: route id} table, the same convention as
# OPENHANDS_RETIRED_IDS/OPENHANDS_TIER_ORDER for a fact no rule derives.
OPENHANDS_DIRECT_PROFILE_IDS = {
    "openrouter-muse-spark-1.3-contributor": "spark-1.3-contributor",
}

# tiers[] push order: tier-profiles.json's own $comment, "ORDER IS THE PUSH
# PRIORITY (reordered 2026-09-25) ... a human decision" - not derivable from
# any registry field (routes is a dict, and catalog/ai-registry.json's own
# keys are alphabetical). A literal, hand-copied constant, the same
# convention OMNIROUTE_RETIRED_IDS/IDE_MODEL_ORDER use; render_openhands()
# raises, naming every id at once, if a future openhands profile is added or
# removed without updating this list - never a silent reorder or drop.
OPENHANDS_TIER_ORDER = (
    "omniroute-t1-orchestrator",
    "omniroute-t2-worker",
    "omniroute-t3-driver",
    "omniroute-t2-orchestrator",
    "omniroute-t2-worker-clean",
    "omniroute-t3-driver-clean",
    "omniroute-t4-rag",
    "omniroute-opus-4-6",
    "omniroute-gemini-3.8-flash",
    "omniroute-t2-worker-free-only",
    "omniroute-deepseek-v4.1-flash",
    "omniroute-t3-driver-free-only",
    "omniroute-t1-orchestrator-clean",
    "omniroute-spark-1.3-contributor",
    "openrouter-muse-spark-1.3-contributor",
    "litellm-t1-orchestrator",
    "litellm-t2-worker",
    "litellm-t3-driver",
    "litellm-t2-worker-free-only",
    "litellm-t3-driver-free-only",
    "litellm-t1-orchestrator-free-only",
    "omniroute-t1-orchestrator-free-only",
    # NOTE (Q1, 2026-09-26 16:4xZ "Claude budget" revision): the 4 new pinned
    # single-leg credit routes (cheaperinference/kimi-k3, cheaperinference/
    # glm-5.2, samba/gpt-oss-120b, samba/MiniMax-M3) deliberately carry NO
    # openhands_profile and are NOT listed here. tools/sync-openhands-
    # profiles.py writes each tier to profiles/<tier id>.json as a literal
    # filesystem path (measured: FileNotFoundError - it does not mkdir a
    # nested directory), and an id of the form "omniroute-cheaperinference/
    # kimi-k3" would create one; a route id containing "/" cannot safely get
    # an OpenHands profile under that tool's current (unfixed) file-writing
    # scheme. tools/audit-router.py --offline therefore never reports these 4
    # combos as missing a tier-profiles.json entry: openhands_route_names()/
    # tier_profile_drift() restrict the check to openhands-served routes, so
    # these opencode/zed-only pinned routes are out of scope - a known,
    # deliberate gap (see this lane's REPORT), not something to route around
    # by picking a different, unrelated tier id.
)

OPENHANDS_GATEWAYS = ("omniroute", "litellm")


def _openhands_tier_membership(registry: dict) -> dict:
    """{tier id: (gateway, route id)} for every routes.<id>.surfaces.<gw>.
    openhands_profile in the registry, plus {tier id: None} for every
    surfaces.openhands.direct_profile (looked up in OPENHANDS_DIRECT_PROFILE_
    IDS) - the ground truth render_openhands() checks OPENHANDS_TIER_ORDER
    against. Raises ValueError, naming the route, when a direct_profile
    route has no entry in that table."""
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}

    wanted = {}
    for route_id in sorted(routes):
        route = routes[route_id]
        if not isinstance(route, dict):
            continue
        surfaces = route.get("surfaces")
        surfaces = surfaces if isinstance(surfaces, dict) else {}
        for gw in OPENHANDS_GATEWAYS:
            surface = surfaces.get(gw)
            if isinstance(surface, dict) and isinstance(surface.get("openhands_profile"), dict):
                wanted["%s-%s" % (gw, route_id)] = (gw, route_id)
        openhands_surface = surfaces.get("openhands")
        if isinstance(openhands_surface, dict) and isinstance(openhands_surface.get("direct_profile"), dict):
            tier_id = next(
                (tid for tid, rid in OPENHANDS_DIRECT_PROFILE_IDS.items() if rid == route_id), None)
            if tier_id is None:
                raise ValueError(
                    "routes.%s has a surfaces.openhands.direct_profile with no entry in "
                    "OPENHANDS_DIRECT_PROFILE_IDS" % route_id)
            wanted[tier_id] = None
    return wanted


def render_openhands(registry: dict) -> dict:
    """Render configuration/openhands/tier-profiles.json's shape from a loaded
    catalog/ai-registry.json document (spec 3.2 phase 1, task A4d). Pure: no
    I/O, no clock, no randomness - the same registry always renders the same
    dict.

    Every routes.<id>.surfaces.<omniroute|litellm>.openhands_profile becomes
    one tier (id "<gateway>-<route id>"; "gateway" itself is only present for
    a litellm tier - an omniroute one carries none, matching today's file),
    plus one tier per standalone surfaces.openhands.direct_profile (mapping
    doc section 6: today only spark-1.3-contributor, named via OPENHANDS_
    DIRECT_PROFILE_IDS). OPENHANDS_TIER_ORDER fixes the push-priority order
    (a human decision, not a registry field - see its own comment above);
    render_openhands() raises, naming every id at once, when the registry and
    that constant disagree on which profiles exist.
    """
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}

    wanted = _openhands_tier_membership(registry)
    have = set(OPENHANDS_TIER_ORDER)
    missing = sorted(set(wanted) - have)
    extra = sorted(have - set(wanted))
    if missing or extra:
        raise ValueError(
            "OPENHANDS_TIER_ORDER is out of sync with the registry's openhands profiles - "
            "missing: %s; unexpected: %s" % (", ".join(missing) or "none", ", ".join(extra) or "none"))

    tiers = []
    for tier_id in OPENHANDS_TIER_ORDER:
        target = wanted[tier_id]
        if target is None:
            route_id = OPENHANDS_DIRECT_PROFILE_IDS[tier_id]
            profile = routes[route_id]["surfaces"]["openhands"]["direct_profile"]
            tier = {"id": tier_id, "gateway": profile.get("gateway")}
            if "model" in profile:
                tier["model"] = profile["model"]
            if "base_url" in profile:
                tier["base_url"] = profile["base_url"]
            tier["max_input_tokens"] = profile.get("max_input_tokens")
            tier["max_output_tokens"] = profile.get("max_output_tokens")
            tier["reasoning"] = bool(profile.get("reasoning"))
            tiers.append(tier)
            continue

        gw, route_id = target
        profile = routes[route_id]["surfaces"][gw]["openhands_profile"]
        tier = {"id": tier_id}
        if gw == "litellm":
            tier["gateway"] = "litellm"
        tier["model"] = profile.get("model", "openai/%s" % route_id)
        tier["max_input_tokens"] = profile.get("max_input_tokens")
        tier["max_output_tokens"] = profile.get("max_output_tokens")
        tier["reasoning"] = bool(profile.get("reasoning"))
        tiers.append(tier)

    return {
        "$comment": OPENHANDS_GENERATED_COMMENT,
        "gateway_base_url": OPENHANDS_GATEWAY_BASE_URL,
        "litellm_base_url": OPENHANDS_LITELLM_BASE_URL,
        "retired_ids": list(OPENHANDS_RETIRED_IDS),
        "tiers": tiers,
    }


def _canonical_openhands(doc) -> dict:
    """Normalize a tier-profiles.json-shaped dict for semantic-equality
    comparison: drop "$comment" entirely (see OPENHANDS_GENERATED_COMMENT's
    comment above) and treat "retired_ids" as unordered (push_profiles()
    immediately turns it into a frozenset - see OPENHANDS_RETIRED_IDS's own
    comment). Unlike _canonical_ide(), "tiers" stays an ordered list - order
    is semantic here (OPENHANDS_TIER_ORDER's comment)."""
    if not isinstance(doc, dict):
        return {}
    out = {k: v for k, v in doc.items() if k != "$comment"}
    retired = out.get("retired_ids")
    if isinstance(retired, list):
        out["retired_ids"] = sorted(retired, key=str)
    return out


def openhands_diff(rendered: dict, current: dict) -> list:
    """Return the keys where a fresh render_openhands() output and today's
    parsed tier-profiles.json differ, ignoring $comment and the order of
    "retired_ids" (see _canonical_openhands). Reports "tiers.<id>" for a
    content difference and "tiers[] order" separately when the two lists
    cover the same ids in a different order - empty means semantically equal,
    the spec 3.2 phase-1 gate for configuration/openhands/tier-profiles.json."""
    a = _canonical_openhands(rendered)
    b = _canonical_openhands(current)

    problems = []
    a_tiers = {t["id"]: t for t in a.get("tiers") or [] if isinstance(t, dict) and "id" in t}
    b_tiers = {t["id"]: t for t in b.get("tiers") or [] if isinstance(t, dict) and "id" in t}
    for tier_id in sorted(set(a_tiers) | set(b_tiers)):
        if a_tiers.get(tier_id) != b_tiers.get(tier_id):
            problems.append("tiers.%s" % tier_id)

    a_order = [t.get("id") for t in a.get("tiers") or []]
    b_order = [t.get("id") for t in b.get("tiers") or []]
    if not problems and a_order != b_order:
        problems.append("tiers[] order")

    for key in sorted((set(a) | set(b)) - {"tiers"}):
        if a.get(key) != b.get(key):
            problems.append(key)

    return problems


# ===========================================================================
# render - docs/models.md "Tier mapping" table (spec 3.2 phase 1, task A4e)
# ===========================================================================

# docs/models.md carried no AUTOOS-MANAGED markers before task A4e. This
# defines the Markdown-comment convention for its own generated block,
# mirroring the "#" (sync-router-tiers.py) and "//" (sync-ide-models.py)
# forms other generated files already use.
MODELS_DOC_MARKER_NAME = "models-doc"
MODELS_DOC_START_LINE = "<!-- AUTOOS-MANAGED-START %s -->" % MODELS_DOC_MARKER_NAME
MODELS_DOC_END_LINE = "<!-- AUTOOS-MANAGED-END %s -->" % MODELS_DOC_MARKER_NAME

# Same "generated from catalog/ai-registry.json - do not edit" notice every
# other render_* function's own $comment carries (spec 3.2: "Generated files
# start with a ... line where the format allows comments" - Markdown does,
# via a line of prose rather than a JSON/YAML comment key).
MODELS_DOC_GENERATED_NOTICE = (
    "_Generated from `catalog/ai-registry.json` — do not edit by hand. "
    "Run `python3 tools/registry.py render models-doc --check` after a "
    "registry change; if it fails, run `python3 tools/registry.py render "
    "models-doc` and replace the text between the two "
    "`AUTOOS-MANAGED-START/END models-doc` markers below with its output._"
)

# Column order for the rendered table. Every cell is read from a registry
# field at render time (see render_models_doc()'s own docstring) - unlike the
# rejected first attempt at this task (commit 6a61052, never merged; see the
# task brief), no column's text is a hand-copied constant.
MODELS_DOC_HEADER = ("Route", "Class", "Context", "Legs")

# Which of routes.<id>.surfaces.<gateway> carries the route's own "context
# promise" (mapping doc sections 4/10: combos.json's display convention,
# "1M"/"128k"/"200k", distinct from the model's real window) - omniroute
# first, matching IDE_GATEWAYS'/OPENHANDS_GATEWAYS' own priority order above,
# since every route that lists both surfaces carries the same figure on both
# (verified 2026-09-26: zero disagreements across the 8 routes that list
# both), so the pick is never actually exercised as a tie-break by real data.
MODELS_DOC_CONTEXT_SURFACES = ("omniroute", "litellm")


def _route_context_promise(route: dict, registry: dict) -> str:
    """The "Context" cell for one route (task A4e): the first of
    MODELS_DOC_CONTEXT_SURFACES's surfaces that carries "context_declared"
    wins, verbatim (spec 3.1's own declared-context string, e.g. "1M"); else
    the first such surface's own numeric "context" field (spec 3.1: "context/
    output declared"), comma-grouped for readability; else - no
    omniroute/litellm surface on this route carries either field at all, not
    the case for any route today - the first leg's resolved model's
    "context_advertised", else its "context_usable.tokens", both suffixed
    "(leg model)"/"(leg model, usable)" so a reader can tell this figure
    describes a leg's model rather than the route's own declared surface;
    "n/a" only if none of the above resolves (a route with no surface context
    figure and no resolvable leg model - not the case for any route today
    either, but never silently blank)."""
    surfaces = route.get("surfaces")
    surfaces = surfaces if isinstance(surfaces, dict) else {}

    for gw in MODELS_DOC_CONTEXT_SURFACES:
        surface = surfaces.get(gw)
        if isinstance(surface, dict) and surface.get("context_declared") is not None:
            return str(surface["context_declared"])

    for gw in MODELS_DOC_CONTEXT_SURFACES:
        surface = surfaces.get(gw)
        if isinstance(surface, dict) and surface.get("context") is not None:
            return "{:,}".format(surface["context"])

    for leg in route.get("legs") or []:
        try:
            _, model_id = resolve_leg(leg, registry)
        except ValueError:
            continue
        model = _section(registry, "models").get(model_id)
        if not isinstance(model, dict):
            continue
        if model.get("context_advertised") is not None:
            return "{:,} (leg model)".format(model["context_advertised"])
        usable = model.get("context_usable")
        if isinstance(usable, dict) and usable.get("tokens") is not None:
            return "{:,} (leg model, usable)".format(usable["tokens"])

    return "n/a"


def _leg_is_unavailable(leg: str, route: dict, registry: dict) -> bool:
    """True when either of spec 3.1's two operator-facing unavailability
    flags marks `leg` down: routes.<id>.unavailable_legs[leg].available is
    false, or the leg's own provider carries providers.<id>.available:
    false (today only cxa - openrouter's own blanket flag was lifted
    2026-09-26; per the 16:4xZ revision OpenRouter is BYOK with no shared
    credit, and its still-dead legs stay
    flagged individually via their own unavailable_legs entry instead - see
    providers.openrouter's $comment). Both flags are registry-only signals the
    OmniRoute gateway itself never consults (mapping doc's "PRIV2" note) -
    this render surfaces them for a human reader, it does not change what a
    caller is served."""
    unavailable_legs = route.get("unavailable_legs")
    if isinstance(unavailable_legs, dict):
        entry = unavailable_legs.get(leg)
        if isinstance(entry, dict) and entry.get("available") is False:
            return True
    try:
        provider_id, _ = resolve_leg(leg, registry)
    except ValueError:
        return False
    provider = _section(registry, "providers").get(provider_id)
    return isinstance(provider, dict) and provider.get("available") is False


def _leg_cell_text(leg: str, route: dict, registry: dict) -> str:
    """One leg's "provider `model`" display text - routes.<id>.legs entries
    are literal "<provider>/<model...>" strings, split at the FIRST "/", the
    same parsing resolve_leg() itself does. Struck through and suffixed
    "(unavailable)" when _leg_is_unavailable() says so - the render
    reproduces the fact rather than dropping or silently hiding it, the same
    contract every other render_* function above gives an operator-flagged
    dead leg."""
    prefix, sep, rest = leg.partition("/")
    text = "%s `%s`" % (prefix, rest) if sep else "`%s`" % leg
    if _leg_is_unavailable(leg, route, registry):
        return "~~%s~~ (unavailable)" % text
    return text


def _route_legs_cell(route: dict, registry: dict) -> str:
    """The "Legs" cell: routes.<id>.legs, in order, joined "->" - a
    strategy: "priority" fallback chain, so order is real semantic data (same
    reasoning render_omniroute()'s own "legs" -> "models" mapping gives it).
    "(none)" for the LiteLLM-only *-paid routes and the dynamic auto* routes,
    whose legs are genuinely `[]` (mapping doc section 4/10)."""
    legs = route.get("legs") or []
    if not legs:
        return "(none)"
    return " → ".join(_leg_cell_text(leg, route, registry) for leg in legs)


def render_models_doc(registry: dict) -> str:
    """Render the content of docs/models.md's AUTOOS-MANAGED "models-doc"
    block (spec 3.2 phase 1, task A4e; docs/plans/2026-09-25-registry-
    mapping.md section 14 documents the mapping). Pure: no I/O, no clock, no
    randomness - the same registry always renders the same text.

    One table row per `registry["routes"]` entry, sorted by id - unlike
    render_ide()'s IDE_MODEL_ORDER / render_openhands()'s OPENHANDS_TIER_
    ORDER, nothing in this repository says the doc table's row order is
    semantic (it is new with this task - there is no "today's hand-curated
    order" to preserve), so no hand-copied order constant is needed here.
    Every column is read from the registry at render time:

      - Route:   routes.<id>.id (the dict key)
      - Class:   routes.<id>.class
      - Context: the route's own declared context promise, see
                 _route_context_promise()
      - Legs:    routes.<id>.legs, in order; a leg routes.<id>.
                 unavailable_legs or providers.<id>.available marks down is
                 struck through, see _route_legs_cell()/_leg_is_unavailable()

    Never writes docs/models.md itself - like every other render_* function,
    this proves/produces the block's content only; a human (or --check
    failing) decides when to paste it between the committed markers.
    """
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}

    lines = [
        MODELS_DOC_GENERATED_NOTICE,
        "",
        "| %s |" % " | ".join(MODELS_DOC_HEADER),
        "|%s|" % "|".join("---" for _ in MODELS_DOC_HEADER),
    ]
    for route_id in sorted(routes):
        route = routes[route_id]
        if not isinstance(route, dict):
            continue
        cells = (
            "`%s`" % route_id,
            str(route.get("class", "")),
            _route_context_promise(route, registry),
            _route_legs_cell(route, registry),
        )
        lines.append("| %s |" % " | ".join(cells))
    return "\n".join(lines) + "\n"


def _locate_managed_block(text: str, name: str) -> tuple:
    """(start, end) 0-based line indices of the "<!-- AUTOOS-MANAGED-START
    name -->" / "<!-- AUTOOS-MANAGED-END name -->" marker pair inside `text`
    (the marker lines themselves; the block's own content runs
    start+1..end, exclusive of `end`). Mirrors tools/sync-router-tiers.py's
    own locate_blocks() / tools/sync-ide-models.py's own locate_blocks()
    "never mask a gap" contract, applied to docs/models.md's `<!--
    AUTOOS-MANAGED-START/END -->` Markdown-comment marker syntax. Raises
    ValueError, never a partial match, on a missing, duplicated or unmatched
    marker."""
    start_line = "<!-- AUTOOS-MANAGED-START %s -->" % name
    end_line = "<!-- AUTOOS-MANAGED-END %s -->" % name
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == start_line:
            if start is not None:
                raise ValueError("duplicate %s" % start_line)
            start = i
        elif stripped == end_line:
            if start is None:
                raise ValueError("%s with no matching start marker" % end_line)
            end = i
            break
    if start is None:
        raise ValueError("no %s found" % start_line)
    if end is None:
        raise ValueError("%s has no matching %s" % (start_line, end_line))
    return start, end


def models_doc_block_text(docs_text: str) -> str:
    """The text strictly between docs/models.md's models-doc markers (marker
    lines excluded), no trailing newline - mirrors litellm_block_text()'s
    single-named-block convention above. Raises ValueError (via
    _locate_managed_block()) on a missing/malformed marker pair."""
    start, end = _locate_managed_block(docs_text, MODELS_DOC_MARKER_NAME)
    lines = docs_text.splitlines()
    return "\n".join(lines[start + 1:end])


_MODELS_DOC_ROW_ID_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|")


def _models_doc_rows(block_text: str) -> dict:
    """{route id: row text} for every data row of a models-doc block's table
    - a row whose first cell is a backtick-quoted id; the header/separator
    lines above it carry no backticks and are excluded. Used by
    models_doc_diff() below for per-route --check diagnostics; the render
    itself (render_models_doc()) builds rows directly from the registry, not
    by parsing its own output."""
    rows = {}
    for line in block_text.splitlines():
        match = _MODELS_DOC_ROW_ID_RE.match(line)
        if match:
            rows[match.group(1)] = line
    return rows


def _models_doc_preamble(block_text: str) -> str:
    """Every line of `block_text` above its first data row (the generated
    notice, the blank line, the table header and separator) - used by
    models_doc_diff() to report a non-route difference (a changed notice or
    header) separately from a per-route one."""
    lines = block_text.splitlines()
    for i, line in enumerate(lines):
        if _MODELS_DOC_ROW_ID_RE.match(line):
            return "\n".join(lines[:i])
    return block_text


def models_doc_diff(rendered_block: str, current_block: str) -> list:
    """Return the problems where a fresh render_models_doc() output and
    docs/models.md's committed "models-doc" block differ (both taken as the
    block's own content, marker lines excluded - see models_doc_block_text()):
    "routes.<id>" for a route whose row differs, "routes[] membership: ..."
    when the two blocks list different route ids outright, "preamble" when
    the notice/header/separator lines above the first data row differ. []
    means the two blocks are semantically equal - the spec 3.2 phase-1 gate
    for docs/models.md, and the task's own acceptance test: a registry field
    changed (e.g. a leg newly marked unavailable) with the doc not re-
    rendered names that route here, it never passes silently."""
    rendered_block = rendered_block.rstrip("\n")
    current_block = current_block.rstrip("\n")
    if rendered_block == current_block:
        return []

    a_rows = _models_doc_rows(rendered_block)
    b_rows = _models_doc_rows(current_block)

    problems = []
    missing = sorted(set(a_rows) - set(b_rows))
    extra = sorted(set(b_rows) - set(a_rows))
    if missing or extra:
        problems.append(
            "routes[] membership: missing %s; unexpected %s"
            % (", ".join(missing) or "none", ", ".join(extra) or "none"))
    for route_id in sorted(set(a_rows) & set(b_rows)):
        if a_rows[route_id] != b_rows[route_id]:
            problems.append("routes.%s" % route_id)

    if _models_doc_preamble(rendered_block) != _models_doc_preamble(current_block):
        problems.append("preamble")

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


def strip_comments(doc):
    """A copy of `doc` with every "$comment" key removed at any depth.

    Registry prose is hand-edited after migration, so `validate`'s "no drift
    from a fresh registry-convert.py render" comparison strips it from both
    sides first; structure, routes, legs, models and providers still match
    exactly. Never mutates its input."""
    if isinstance(doc, dict):
        return {key: strip_comments(value)
                for key, value in doc.items() if key != "$comment"}
    if isinstance(doc, list):
        return [strip_comments(value) for value in doc]
    return doc


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
    if strip_comments(fresh) != strip_comments(registry):
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


def _cmd_render_openhands(args) -> int:
    registry_doc = load(args.registry)
    try:
        rendered = render_openhands(registry_doc)
    except ValueError as exc:
        print(str(exc))
        return 1

    if args.check:
        tier_profiles_path = Path(args.tier_profiles)
        if not tier_profiles_path.exists():
            print("no such file: %s" % tier_profiles_path)
            return 1
        current = load(tier_profiles_path)
        problems = openhands_diff(rendered, current)
        if problems:
            for key in problems:
                print("differs: %s" % key)
            return 1
        print("ok: render openhands matches %s" % tier_profiles_path)
        return 0

    text = render_json(rendered)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("wrote %s" % args.out)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_render_models_doc(args) -> int:
    registry_doc = load(args.registry)
    rendered = render_models_doc(registry_doc)

    if args.check:
        docs_path = Path(args.docs)
        if not docs_path.exists():
            print("no such file: %s" % docs_path)
            return 1
        docs_text = docs_path.read_text(encoding="utf-8")
        try:
            current = models_doc_block_text(docs_text)
        except ValueError as exc:
            print(str(exc))
            return 1
        problems = models_doc_diff(rendered, current)
        if problems:
            for key in problems:
                print("differs: %s" % key)
            return 1
        print("ok: render models-doc matches %s" % docs_path)
        return 0

    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print("wrote %s" % args.out)
    else:
        sys.stdout.write(rendered)
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
        help="write the render here instead of stdout (to update the committed file: --out catalog/ide-models.json)")
    ide_parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the render differs semantically from --ide-models")
    ide_parser.add_argument(
        "--ide-models", dest="ide_models", default=str(DEFAULT_IDE_MODELS_PATH),
        help="today's ide-models.json to compare against, --check only (default: %(default)s)")

    openhands_parser = render_targets.add_parser(
        "openhands", help="render configuration/openhands/tier-profiles.json")
    openhands_parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_PATH),
        help="registry JSON to render from (default: %(default)s)")
    openhands_parser.add_argument(
        "--out", default=None,
        help="write the render here instead of stdout (never the real tier-profiles.json)")
    openhands_parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the render differs semantically from --tier-profiles")
    openhands_parser.add_argument(
        "--tier-profiles", dest="tier_profiles", default=str(DEFAULT_TIER_PROFILES_PATH),
        help="today's tier-profiles.json to compare against, --check only (default: %(default)s)")

    models_doc_parser = render_targets.add_parser(
        "models-doc", help="render docs/models.md's AUTOOS-MANAGED models-doc table")
    models_doc_parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_PATH),
        help="registry JSON to render from (default: %(default)s)")
    models_doc_parser.add_argument(
        "--out", default=None,
        help="write the render here instead of stdout (never the real docs/models.md - "
             "there is no --write; paste the output between the committed markers by hand, "
             "or --check to confirm they already agree)")
    models_doc_parser.add_argument(
        "--check", action="store_true",
        help="exit 1, naming each differing route row, if the render differs from --docs's "
             "own models-doc block")
    models_doc_parser.add_argument(
        "--docs", default=str(DEFAULT_MODELS_DOC_PATH),
        help="today's docs/models.md to compare against, --check only (default: %(default)s)")

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
        if args.target == "openhands":
            return _cmd_render_openhands(args)
        if args.target == "models-doc":
            return _cmd_render_models_doc(args)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
