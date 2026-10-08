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
       EXEMPTIONS names the one route (l1-orchestrator-clean) deliberately
       exempted from this rule; `check`/`validate` still report it, as an
       info line, never silently;
    4. providers.<id>.api_base and models.<id>.direct.base_url hold only a public
       vendor endpoint. Loopback (127.0.0.1 / localhost / ::1) is allowed ONLY in
       a model's direct.base_url; private IPv4 ranges, single-label hosts,
       .local/.lan/.internal/.vm hosts and any userinfo@ are always rejected;
    5. no key or value anywhere carries a date, except values under keys named
       source/verified/version/unavailable_until and anything inside a
       $comment/comment;
    6. every key the schema marks required is present (a small hand-rolled
       structural walk of catalog/ai-registry.schema.json - no jsonschema
       dependency, matching the rest of this repo's suites, AGENTS.md section 5);
    7. every ``unavailable_until`` value (clients/providers/unavailable_legs,
       brief UNTIL 2026-09-26) parses as an ISO-8601 UTC timestamp -- the
       resolver reads it via unavailable_now(), the renders never do (they
       stay time-independent so the CI drift gates do not move with the date);
    8. an entry with ``unavailable_until`` MUST ALSO carry ``available:
       false`` -- the resolver reads ``available`` to decide whether to
       emit a re-probe note after the until passes (FUP 2026-09-27,
       measured on ``clients.agy`` which had ``unavailable_until`` without
       ``available: false`` and silently lost the re-probe on expiry);
    9. every serving route leg is allowed by policy.leg_rules (ordered fnmatch
       rules, matched case-insensitively, first match wins, no match = allowed;
       leg_rule_for()); a denied leg must be gated (available false) - L0
       ONE-ROUTER 2026-09-27.
    10. every providers.<id>.limits key resolves to one of that provider's
        models and every rpm/rpd/tpm/tpd value is a non-negative int (brief R4,
        2026-09-27); plan_available, when present, is a boolean (MISTRALFIX
        2026-09-28 - rpm 0 and plan_available false are the two recorded shapes
        of "this plan cannot answer", read by plan_dead_reasons());
    11. policy.reviewers is a non-empty ordered list, each entry carrying a
        client that exists in ``clients``, a non-empty model/family, a boolean
        paid and -- when it names one -- a ``leg`` that resolves and whose
        model's ``family`` agrees with the entry's. This is the list the
        resolver walks to answer "who reviews this" (REVROUTE (S2) item 1,
        2026-09-27). ``model`` is the client's own spelling and is deliberately
        not resolved against providers/models; ``leg`` is the gateway path and
        is resolved with the same rule 1 predicate everything else uses.
    12. policy.risk_rules, policy.risk_audit_percent and policy.review_counts
        are shapes tools/autoos_risk.py and the resolver can actually apply
        (RISKTIER-a, operator Q-013 2026-09-28; fields widened by RISKTIER-a2):
        every rule's ``type`` is one classify() implements, it carries the fields
        that type reads and no others — ``exclude`` (a path_glob's second glob)
        and ``min_deleted_lines`` (a diff_deletion's numstat threshold) among them
        — a glob/regex rule has a non-empty ``pattern`` (an added_regex
        one compiles), the audit percent is an int 0-100, and each review count
        is a non-negative int with a boolean ``final``. classify() raises on an
        unknown type rather than ignoring it, so this is what keeps such a rule
        out of the committed registry.
    13. every policy.handoff_caps row is self-consistent -- ``cap_tokens`` equals
        ``round(window * cap_fraction)``, spec 8.3's own formula -- and one row
        matches ``"*"``, the fallback the cap reader uses for a model no other
        row names. ``tools/autoos_context.py`` reads ``cap_tokens`` and never
        recomputes it, so a row whose pair disagrees states two caps at once and
        the lane hands off at the stale one; a registry with no ``"*"`` row gets
        no cap from the policy at all and silently falls back to that tool's own
        hand-maintained DEFAULT_CAPS (brief AUTHORS (S1) item 2, 2026-09-28).
        A *missing* policy.handoff_caps stays rule 6's (the schema marks it
        required); this rule owns what the schema cannot see - the relation
        between the fields and the existence of the default row.

`validate` runs `check` (kept as a separate subcommand so existing callers
keep working; the migration drift gate against the one-shot converter
retired with the old catalogs in task A5e).

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
$comment exception no longer applies to it). Each entry now carries an
optional `effort_ladder` list (derived from the first leg's model-registry
entry, with "none" omitted) that tools/sync-ide-models.py uses to emit
opencode V2 variants. To update the committed file after
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
import fnmatch
import importlib.util
import ipaddress
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
SCHEMA_PATH = ROOT / "catalog" / "ai-registry.schema.json"
DEFAULT_OMNIROUTE_COMBOS_PATH = ROOT / "configuration" / "omniroute" / "combos.json"
SYNC_ROUTER_TIERS_PATH = ROOT / "tools" / "sync-router-tiers.py"
DEFAULT_LITELLM_CONFIG_PATH = ROOT / "configuration" / "litellm" / "config.yaml"
DEFAULT_IDE_MODELS_PATH = ROOT / "catalog" / "ide-models.json"
DEFAULT_TIER_PROFILES_PATH = ROOT / "configuration" / "openhands" / "tier-profiles.json"
DEFAULT_MODELS_DOC_PATH = ROOT / "docs" / "models.md"

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
COMMENT_KEYS = ("$comment", "comment")
# unavailable_until's whole job is to hold a date (rule 7 checks the value
# parses); version is a date by definition. Neither is a rule-5 violation.
# monthly_cap_source is a source like any other: who set the cap, and when.
DATE_EXEMPT_KEYS = ("source", "verified", "version", "unavailable_until", "monthly_cap_source",
                    # the schema's price_source asks for a DATED attribution by
                    # name ("gateway /v1/models 2026-09-28"); rule 5 must not
                    # fight rule-for-field honesty (SB-C2 item 4). price_as_of
                    # is the machine-readable sibling (T1-CREDIT-FIX-6): a bare
                    # YYYY-MM-DD the validator itself checks, not prose.
                    # prompt_cache_source is the same shape for the caching
                    # verdict (AO-PROBE-D657, D-658): the dated run or vendor
                    # document behind it, owned by _check_prompt_cache.
                    "price_source", "price_as_of", "prompt_cache_source",
                    # providers.<id>.privacy.evidence[].accessed is the schema's
                    # own YYYY-MM-DD field (L1-CLEAN 2026-10-01) - a citation
                    # date, not a stale value rule 5 should flag.
                    "accessed")

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


def _parse_until(value):
    """Parse an ISO-8601 UTC ``unavailable_until`` value to an aware datetime.

    Accepts a trailing ``Z`` or an explicit ``+00:00``; anything else (a
    non-string, a naive timestamp, another offset, a calendar that does not
    exist) returns None -- parseability is rule 7's job, the helper never
    raises into a live resolver. This stays permissive on purpose: rule 7 is
    the gate that enforces the schema's ``Z``-only shape, while a resolver
    must degrade gracefully on a hand-edited value rather than crash.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def unavailable_now(entry, now) -> bool:
    """Whether a clients/providers/unavailable_legs `entry` is unavailable at
    `now` (brief UNTIL, 2026-09-26; skill rule R-gateway-12: a 429 with
    retryable:true but a multi-day reset is not soon-retryable -- mark the
    entry unavailable till the reset, and let it come back on its own instead
    of relying on someone hand-undoing an ``available: false``).

    Semantics: an entry with ``unavailable_until`` strictly in the future is
    unavailable; once that instant passes the entry counts as available again
    (the resolver's reason says "re-probe"). An entry with ``available:
    false`` and no until stays unavailable forever -- today's behaviour,
    unchanged. An unparsable until reads as available here and is reported by
    `check` rule 7 instead: a resolver must never crash on a hand-edited
    timestamp, and a bad one must never silently extend an outage.

    `now` is injectable for tests; a naive `now` is assumed UTC. Pure: no
    clock of its own, no I/O. The renders deliberately never call this --
    they stay time-independent so the CI drift gates do not move with the
    date (the gateway handles a quota 429 itself).
    """
    if not isinstance(entry, dict):
        return False
    until = entry.get("unavailable_until")
    if until is not None:
        moment = _parse_until(until)
        if moment is not None:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            return now < moment
        # unparsable: falls through to the plain `available` flag; rule 7
        # reports the value itself.
    return entry.get("available") is False


def load(path) -> dict:
    """Load a registry JSON document. Accepts str or Path."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_lf(path, text) -> None:
    """Write `text` to `path` as UTF-8 with LF line endings.

    A Python 3.8-safe equivalent of ``Path(path).write_text(text,
    encoding="utf-8", newline="\\n")``: ``Path.write_text``'s ``newline``
    keyword is honored on 3.8+, but routing every render write through one
    helper keeps the LF guarantee in one place (and avoids the subtle
    text-mode translation ``open(..., "w")`` would do without an explicit
    ``newline``). Accepts str or Path."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def provider_field_map(providers: dict, field: str) -> dict:
    """{provider id: providers.<id>.<field>} for every entry whose field is
    truthy, in registry order.

    Small reusable loader (task A5c, spec 3.2 phase 2): the generic form of
    the provider-id -> single-field map a consumer used to build by hand
    against the legacy provider catalog (tools/mirror-litellm-env.py's KEY_MAP was
    `{name: entry["litellm_env"] for name, entry in doc["providers"].items()}`
    with no presence filter - harmless against that catalog, where every
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
    """Order key for legacy_models(): the registry is written with every key
    sorted and carries no trace of the legacy models list's hand-curated
    order, so that order cannot be derived -- sort instead, with `openrouter-free`
    pinned first among the openrouter entries so the generated openrouter map
    (which filters this list in order) keeps it first."""
    if mid == "openrouter-free":
        return (1, "")
    if mid.startswith("openrouter-"):
        return (2, mid)
    return (0, mid)


def legacy_models(doc) -> list:
    """Project registry `models` into the legacy llm-models entry
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

    Model matching is exact first, then the unique case-fold match: model keys
    are unique under folding (rule 1b), but a leg carries its provider's own
    wire casing (T1-CLEAN-3: ``huggingface/Qwen/Qwen3.8-27B`` names the
    canonical key ``qwen/qwen3.8-27b``). An ambiguous or absent fold still
    raises. (autoos_resolver.ci_key is the runtime-side precedent.)
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
    models = _section(registry, "models")
    if model_id not in models:
        folded = [k for k in models if k.lower() == model_id.lower()]
        if len(folded) == 1:
            return provider_id, folded[0]
        raise ValueError("leg %r: no model matches %r" % (leg, model_id))
    return provider_id, model_id


def provider_spellings_for(provider_id, registry) -> list:
    """One provider's spellings: its id, then each namespace its own row
    declares. A provider the registry does not carry answers to its own name
    only -- the old hand-read of `omniroute_id`/`model_prefix`, kept in one
    place instead of restated by every consumer. Order is stable and
    de-duplicated; see `provider_alias_map` for why the spellings come from the
    data."""
    entry = ((registry or {}).get("providers") or {}).get(provider_id)
    spellings = [provider_id]
    if isinstance(entry, dict):
        declared = []
        for field in ("omniroute_id", "model_prefix"):
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                declared.append(value.strip())
        declared.extend(_alias_values(entry.get("aliases")))
        spellings.extend(declared)
    return list(dict.fromkeys(spellings))


def provider_alias_map(registry) -> dict:
    """{provider id: [its own id, every namespace its rows may carry]}.

    LANE-PRICE-GAP (2026-10-08): the gateway bills a call-log row under the
    connection id (`providers.<id>.omniroute_id`, e.g. `vertex` for `vertex_ai`)
    and serves its models under `model_prefix` (`ovh` for `ovhcloud`), while the
    registry keys everything -- routes, prices, `provider_prices` -- by provider
    id. Every consumer that prices a row has to cross that gap, and the
    hand-written name lists that did so priced exactly the providers their
    author had measured. All spellings come from the data, so a renamed
    connection is covered the moment its row lands. A provider entry may also
    declare `aliases` (a string or a list) for spellings no field of the schema
    carries; that is the extension point, and adding a name there is a data edit.

    Values are declared spellings verbatim (a model namespace keeps its case).
    `{}` for a registry with no providers section."""
    providers = (registry or {}).get("providers")
    if not isinstance(providers, dict):
        return {}
    return {provider_id: provider_spellings_for(provider_id, registry)
            for provider_id in providers}


def _alias_values(aliases) -> list:
    """A provider's declared `aliases`, spellings only: a string is one
    spelling, a list all of its non-empty strings, anything else none."""
    if isinstance(aliases, str):
        return [aliases.strip()] if aliases.strip() else []
    if isinstance(aliases, list):
        return [value.strip() for value in aliases
                if isinstance(value, str) and value.strip()]
    return []


def provider_id_from_spelling(spelling, registry):
    """The provider id a call-log namespace names, or None.

    The provider id itself answers first (a key is never shadowed by another
    provider's gateway id), then a unique case-insensitive match among every
    declared spelling. Two providers claiming one spelling resolve to nothing:
    either reading would bill one grant at the other's price, and an unpriced
    row is the loud gap the guard already reports (`models_unpriced`)."""
    text = str(spelling or "").strip()
    if not text:
        return None
    providers = (registry or {}).get("providers")
    if isinstance(providers, dict) and text in providers:
        return text
    owners = {}
    for provider_id, spellings in provider_alias_map(registry).items():
        for value in spellings:
            owners.setdefault(value.strip().lower(), set()).add(provider_id)
    candidates = owners.get(text.lower()) or set()
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def leg_price(model_id, provider_id, registry):
    """(price_in, price_out) USD per token for one provider/model leg, or None.

    T1-CREDIT-FIX-6 (D-220): a model id can be served at two prices at once --
    ``gemini-3.8-flash`` is free through AI-Studio (model-level price 0) and
    billed through Vertex AI (a $250 credit grant). The model-level row must
    stay 0 (pricing it would bill the free leg), so the paid provider's price
    lives per provider in ``models.<id>.provider_prices.<provider_id>`` and
    this is the one place that reads it: the provider-scoped entry wins when
    present and positive, else the model-level ``price_in``/``price_out``.
    Anything unparseable or non-positive reads as unpriced (None) -- the
    validator refuses such rows loudly, and the runtime degrades to the same
    gap instead of billing a made-up number.

    LANE-PRICE-GAP (2026-10-08): `provider_id` may be the spelling a call-log
    row carries -- the gateway connection id (``vertex``) or the model namespace
    (``ovh``) rather than the registry key the price is filed under
    (``vertex_ai``, ``ovhcloud``). It is resolved through
    `provider_id_from_spelling` first, so a grant's rows bill at the grant's own
    price instead of reading as free money. An unresolvable spelling is passed on
    unchanged: it then matches no scoped entry and the model-level row decides,
    exactly the old behaviour (never borrow a stranger's price).
    """
    models = (registry or {}).get("models")
    model = models.get(model_id) if isinstance(models, dict) else None
    if not isinstance(model, dict):
        return None
    if provider_id:
        scoped = model.get("provider_prices")
        priced_as = provider_id_from_spelling(provider_id, registry) or provider_id
        entry = scoped.get(priced_as) if isinstance(scoped, dict) else None
        if isinstance(entry, dict):
            try:
                price_in = float(entry.get("price_in"))
                price_out = float(entry.get("price_out"))
            except (TypeError, ValueError):
                price_in = price_out = 0.0
            if price_in > 0.0 and price_out > 0.0:
                return (price_in, price_out)
    try:
        price_in = float(model.get("price_in"))
        price_out = float(model.get("price_out"))
    except (TypeError, ValueError):
        return None
    if price_in > 0.0 and price_out > 0.0:
        return (price_in, price_out)
    return None


def gateway_ref(leg, registry) -> str:
    """The id the OmniRoute gateway catalog actually serves for a registry leg.

    A provider may spell its models differently in the live gateway than in this
    registry: scaleway's free grant serves ``scw/*`` ids (FREEKEYS-1) while the
    registry keeps ``scaleway/*`` because resolve_leg()/usable_legs() and every
    saved route/selection already use that spelling. (antigravity is the
    historical case that motivated this field: its ``agy`` prefix, declared
    2026-09-27, was retired to null 2026-09-30 - AGYCANON - after the live
    catalog was re-measured back to canonical ``antigravity/*`` ids.) This is
    the ONE translation point between the two: a provider declaring
    ``model_prefix`` has each leg rewritten to ``<model_prefix>/<model>``; every
    other leg - and any leg that does not resolve, or is not a string - is
    returned unchanged (rule 1 already reports a malformed leg loudly, and a
    render must not hide one behind a silent rewrite).

    The registry's own leg spelling never moves, so the resolver and
    apply.sh/apply.ps1 (which look a provider connection up by ``omniroute_id``)
    are untouched."""
    if not isinstance(leg, str):
        return leg
    try:
        provider_id, model_id = resolve_leg(leg, registry)
    except ValueError:
        return leg
    provider = _section(registry, "providers").get(provider_id)
    prefix = provider.get("model_prefix") if isinstance(provider, dict) else None
    if not prefix:
        return leg
    return "%s/%s" % (prefix, model_id)


def registry_ref(ref, registry) -> str:
    """The registry leg spelling of a rendered gateway ref - ``gateway_ref()``'s inverse.

    ``combos.json`` carries ``gateway_ref()``'s output, so a consumer that reads
    that file and resolves a leg's provider out of it (sync-router-tiers.py's
    explicit ``--combos`` override onto config.yaml) sees the *gateway* namespace
    ``scw/...``, which is no provider in ``providers`` at all: the leg loses its
    LiteLLM transport and its env key and lands in the mirror as an addressable-
    looking but unsettable ``SCW_API_KEY`` entry (FREEKEYS-2e CI, sync-router-tiers
    unit test). Rewriting the declared ``model_prefix`` back to the provider id
    restores exactly the spelling ``routes.<id>.legs`` uses, so both render paths
    agree.

    Only a namespace that is *not* itself a provider id is rewritten, and only
    when exactly one provider declares it - an ambiguous or unknown namespace is
    returned unchanged, the same fail-loudly-no-invention rule ``gateway_ref()``
    follows (a malformed registry is rule 1's business, not a render's). Pure."""
    if not isinstance(ref, str) or "/" not in ref:
        return ref
    namespace, model_id = ref.split("/", 1)
    providers = _section(registry, "providers")
    if namespace in providers:
        return ref
    owners = [pid for pid, provider in providers.items()
              if isinstance(provider, dict) and provider.get("model_prefix") == namespace]
    if len(owners) != 1:
        return ref
    return "%s/%s" % (owners[0], model_id)


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


def _check_model_key_case(registry) -> list:
    """Rule 1b (L3 hygiene, 2026-10-01): no two models.<key> entries may differ
    only in letter case.

    Two keys a human reads as the same model drift: a price or a trains_on_prompts
    flag is fixed on one and silently missed on the other. The canonical spelling
    is the one the provider's own API uses (for OVH, ``Qwen3.8-27B``); a
    provider that spells the same model differently is a SEPARATE entry only when
    the spelling differs beyond case (groq's ``openai/gpt-oss-120b`` vs the bare
    ``gpt-oss-120b`` both exist), never by case alone."""
    by_fold = {}
    for key in _section(registry, "models"):
        by_fold.setdefault(key.lower(), []).append(key)
    problems = []
    for folded, keys in sorted(by_fold.items()):
        if len(keys) > 1:
            problems.append("model keys differ only in case: %s" % ", ".join(sorted(keys)))
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
        ``providers.<id>.tier`` -- is ``"paid"``, ``"subscription"`` or
        (L1-CLEAN, 2026-10-01) ``"credit"``. A credit leg is admitted because
        its provider now proves no-training with a cited ``privacy`` block
        (L1-CLEAN-4 K2, 2026-10-01: that block is ENFORCED here, via
        ``_privacy_block_problems``, not merely documented) and the resolver's
        own spend guard prices/caps it; a free pool is still
        never private-safe, even one whose own
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
    if effective_tier not in ("paid", "subscription", "credit"):
        return False, "effective tier %r is not paid, subscription or credit" % (effective_tier,)
    if provider.get("trains_on_prompts") is not False:  # True or missing: unsafe
        return False, "trains on prompts"
    if _cited_privacy_block_required(provider, effective_tier):
        # L1-CLEAN-4 K2 (2026-10-01), one predicate per rework F1: the credit
        # half of the admission is the CITED block, not the bare flag - the same
        # predicate check_registry reports with (see
        # ``_cited_privacy_block_required``), so a leg is never clean here and a
        # defect there.
        problems = _privacy_block_problems(provider_id, provider,
                                           effective_tier=effective_tier)
        if problems:
            return False, ("credit tier without cited privacy evidence (%s)"
                           % problems[0].split("privacy evidence: ", 1)[-1])
    if "trains_on_prompts" in model and model["trains_on_prompts"] is not False:
        return False, "model trains on prompts"
    return True, None


# The one documented, deliberate exception to rule 3 (PRIV2, 2026-09-26):
# l1-orchestrator-clean's only leg is the OpenRouter contributor model, which
# trains by contract (see the meta/muse-spark-1.3-contributor $comment in
# catalog/ai-registry.json). combos.json's own $comment already recorded this
# trade-off on 2026-09-21 ("l1-orchestrator-clean ... no longer means
# trains-nothing - it means paid-only"); tools/autoos-agent.py's spawner
# requires an explicit --allow-training to route a sensitive card there
# (tools/autoos_routing.py select_combo()). A route named here is never
# evaluated by _check_privacy below - it is reported separately, as an
# "info:" line (privacy_exemption_lines()), never silently and never as a
# check_registry() problem. The resolver's own privacy filter
# (tools/autoos_resolver.py filter_routes()) does NOT consult this table: it
# calls private_safe() on every *available* serving leg of *every* route, and
# l1-orchestrator-clean's only leg has no available leg at all, so a
# privacy=sensitive card can never land there regardless.
CLEAN_ROUTE_EXEMPTIONS = {
    "l1-orchestrator-clean": (
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


def _cited_privacy_block_required(provider: dict, effective_tier=None) -> bool:
    """The ONE predicate for "must this leg carry a cited ``privacy`` block?"
    (T1-CLEAN-4 rework F1, 2026-10-01): `private_safe()` and
    `_privacy_block_problems()` both ask it, so the gate and the report can
    never read the credit rule differently across the tier-override axis.

    A credit grant is the case that needs the citation, and `credit` can arrive
    from either side of the override, so BOTH tiers are consulted: the
    provider's own ``tier`` and the leg's EFFECTIVE tier
    (``models.<id>.tier`` when present, else the provider's). **Either one
    being credit demands the block** — that is the fail-closed choice, and it
    is the rule both directions follow: a model-level ``tier: "credit"``
    override onto a paid provider cannot borrow that provider's silence, and a
    ``tier: "paid"`` override onto a credit provider cannot escape the citation
    that provider's grant rests on. The requirement only ever widens with the
    override; it never narrows. `effective_tier=None` (a provider-level row read
    on its own, e.g. by the provider walk in `_check_privacy_evidence`) means
    the provider's tier decides.
    """
    return "credit" in (provider.get("tier"), effective_tier)


def _privacy_block_problems(provider_id: str, provider: dict,
                            effective_tier=None) -> list:
    """Rule 3b defects for ONE `providers.<id>` row, as ``"privacy evidence: ..."``
    lines (T1-CLEAN-4 K2, 2026-10-01).

    This is the single reading of "a cited no-training block", shared by
    `_check_privacy_evidence()` (which reports every defect) and
    `private_safe()` (which gates a `credit` leg), so the report and the gate can
    never disagree about what counts as evidence. Which legs are asked for one
    at all is `_cited_privacy_block_required()`'s single predicate, fed the same
    `effective_tier` here and there (rework F1). Two shapes are problems:

      - a present block that is not an object, disagrees with the provider's own
        ``trains_on_prompts``, or (for a no-training claim) carries no evidence,
        or an evidence row without an https ``url``, a non-empty ``quote`` and an
        ISO ``accessed`` date;
      - a credit leg — by its own tier or its provider's — that asserts
        ``trains_on_prompts: false`` with NO block at all: the schema and
        `private_safe()`'s docstring both say a credit leg is admitted *because*
        its grant cites its terms, and until K2 nothing enforced it.

    A row that is neither (no block, no credit grant) returns ``[]``.
    """
    problems = []
    if "privacy" not in provider:
        if (_cited_privacy_block_required(provider, effective_tier)
                and provider.get("trains_on_prompts") is False):
            problems.append(
                "privacy evidence: providers.%s is tier credit with "
                "trains_on_prompts false and no privacy block - a credit leg "
                "admitted to a -clean route must cite the terms that say so"
                % provider_id)
        return problems
    block = provider.get("privacy")
    if not isinstance(block, dict):
        return ["privacy evidence: providers.%s.privacy is not an object"
                % provider_id]
    if block.get("trains_on_prompts") is not provider.get("trains_on_prompts"):
        problems.append(
            "privacy evidence: providers.%s.privacy.trains_on_prompts %r "
            "disagrees with the provider flag %r"
            % (provider_id, block.get("trains_on_prompts"),
               provider.get("trains_on_prompts")))
    if block.get("trains_on_prompts") is not False:
        return problems  # only a no-training claim needs evidence
    evidence = block.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        problems.append("privacy evidence: providers.%s.privacy has no evidence"
                        % provider_id)
        return problems
    for i, row in enumerate(evidence):
        if not isinstance(row, dict):
            problems.append("privacy evidence: providers.%s.privacy.evidence[%d] "
                            "is not an object" % (provider_id, i))
            continue
        url = row.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            problems.append("privacy evidence: providers.%s.privacy.evidence[%d] "
                            "url is not https" % (provider_id, i))
        quote = row.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            problems.append("privacy evidence: providers.%s.privacy.evidence[%d] "
                            "quote is empty" % (provider_id, i))
        accessed = row.get("accessed")
        if not isinstance(accessed, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$",
                                                         accessed):
            problems.append("privacy evidence: providers.%s.privacy.evidence[%d] "
                            "accessed is not YYYY-MM-DD" % (provider_id, i))
    return problems


def _check_privacy_evidence(registry) -> list:
    """Rule 3b (L1-CLEAN, 2026-10-01): a providers.<id>.privacy block must be
    complete and consistent with the provider's own trains_on_prompts flag, and
    a `credit` grant that relies on `false` must carry such a block at all
    (T1-CLEAN-4 K2).

    A provider asking to be trusted on a privacy=sensitive route cannot just
    assert `false` - the block must cite its authority: non-empty evidence,
    each row an https url, a non-empty quote and an ISO accessed date, and
    privacy.trains_on_prompts must equal the provider-level flag exactly. An
    inconsistent or unevidenced block is the defect (the registry's version of
    "never guess false to make a row look clean", spec 3.1).

    A provider row is read on its own tier, then every leg the routes carry is
    re-read with its EFFECTIVE tier, so a model-level ``tier`` override that
    makes a leg credit on a provider whose own row asks for nothing is reported
    too (rework F1 — the same predicate `private_safe()` gates on)."""
    problems = []
    providers = _section(registry, "providers")
    for provider_id, provider in sorted(providers.items()):
        if not isinstance(provider, dict):
            continue
        problems.extend(_privacy_block_problems(provider_id, provider))

    models = _section(registry, "models")
    seen_legs = set()
    for route in _section(registry, "routes").values():
        if not isinstance(route, dict):
            continue
        for leg in dict.fromkeys((route.get("legs") or [])
                                 + list((route.get("unavailable_legs") or {}))):
            try:
                provider_id, model_id = resolve_leg(leg, registry)
            except ValueError:
                continue  # rule 1 already reports an unresolved leg
            if (provider_id, model_id) in seen_legs:
                continue
            seen_legs.add((provider_id, model_id))
            provider = providers.get(provider_id)
            model = models.get(model_id)
            if not isinstance(provider, dict) or not isinstance(model, dict):
                continue
            effective_tier = model.get("tier", provider.get("tier"))
            if effective_tier == provider.get("tier"):
                continue  # the provider walk above already read this row
            problems.extend(_privacy_block_problems(provider_id, provider,
                                                    effective_tier=effective_tier))
    return list(dict.fromkeys(problems))


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
# rule 7 - every unavailable_until parses as ISO-8601 UTC
# ===========================================================================


def _check_until_values(registry) -> list:
    """Every ``unavailable_until`` value anywhere in the registry is an
    ISO-8601 UTC timestamp ending in ``Z``.

    The schema's ``until_tag`` pattern is ``Z``-only, and any offset (even an
    explicit ``+00:00``) or naive value is ambiguous about which clock it
    means. A value that does not parse would quietly read as available forever
    (see unavailable_now), which is exactly the hand-edit-forgot-to-undo
    failure the field exists to remove -- so it fails check loudly, naming the
    dotted path."""
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in COMMENT_KEYS:
                    continue
                child = "%s.%s" % (path, key) if path else key
                if key == "unavailable_until":
                    text = value.strip() if isinstance(value, str) else ""
                    if not text.endswith("Z") or _parse_until(value) is None:
                        problems.append(
                            "bad unavailable_until: %s %r" % (path, value))
                    continue
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, "%s[%d]" % (path, index))

    walk(registry, "")
    return problems


# ===========================================================================
# rule 8 - unavailable_until entries must also carry available: false
# ===========================================================================


def _check_unavailable_until_pairs_available(registry) -> list:
    """Every dict that has ``unavailable_until`` MUST also have ``available:
    false``.

    The resolver's ``_client_reason`` uses the ``available`` flag to detect
    when a self-healed ``unavailable_until`` has passed (``available`` is
    False but the until is past -> emit a re-probe note). Without the flag
    the entry silently switches back to available with no note, which is
    wrong for own-account clients (FUP 2026-09-27, measured on
    ``clients.agy``).

    This rule recursively walks the whole registry so it catches the
    pattern everywhere, not just on the three named surfaces.
    """
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            if "unavailable_until" in node and node.get("available") is not False:
                problems.append("entry with unavailable_until but no available: false: %s" % path)
            for key, value in node.items():
                if key in COMMENT_KEYS:
                    continue
                child = "%s.%s" % (path, key) if path else key
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, "%s[%d]" % (path, index))

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
# constant, the same convention the deleted one-shot converter used for facts no
# source file carries (PROVIDER_EXTRA, MODEL_EXTRA, COMBO_CLASS, ...): apply.sh/
# still need this exact list once a later phase switches them onto a rendered file.
OMNIROUTE_RETIRED_IDS = [
    "tier1", "tier1-clean",
    "tier2", "tier2-clean",
    "tier3", "tier3-clean",
    "rag",
    "tier1-paid", "tier2-paid", "tier3-paid",
    "tier2-credit", "tier3-credit",
    # CLAUDE55 2026-10-05 (operator): the route was renamed opus-5-5 (the 4-6
    # generation is retired upstream); the id retires so apply prunes the live
    # store's orphaned combo instead of leaving it servable.
    "opus-4-6",
    # ORQWEN404 2026-10-06 (operator task 10): the openrouter qwen3.8-27b:free
    # leg measured 404 upstream; its single-leg route is deleted and the id
    # retires so apply prunes the live combo.
    "or-qwen3.8-27b-free",
    # LAYERS 2026-10-07 (operator): t*-ids renamed to l*-ids (descriptive
    # layer names); the old ids retire so apply prunes the live store.
    "t1-orchestrator", "t1-orchestrator-clean", "t1-orchestrator-free-only",
    "t1-orchestrator-paid", "t2-orchestrator", "t2-worker", "t2-worker-clean",
    "t2-worker-free-only", "t2-worker-paid", "t3-driver", "t3-driver-clean",
    "t3-driver-free-only", "t3-driver-paid", "t4-researcher",
    # OVHCODER-DROP 2026-10-08 (hotfix): OVH withdrew Qwen3-Coder-30B-A3B-Instruct
    # upstream (measured 17:34Z central: HTTP 404 "The model
    # Qwen3-Coder-30B-A3B-Instruct does not exist"); its single-leg route is
    # deleted and the id retires so apply prunes the live combo, the same way
    # ORQWEN404 retired or-qwen3.8-27b-free.
    "ovh-qwen3-coder-30b",
]


def render_omniroute(registry: dict) -> dict:
    """Render configuration/omniroute/combos.json's shape from a loaded
    catalog/ai-registry.json document (spec 3.2 phase 1). Pure: no I/O, no clock,
    no randomness - the same registry always renders the same dict.

    A route becomes a combo iff it has at least one servable leg. The
    LiteLLM-only routes (l1-orchestrator-paid, l2-worker-paid, l3-driver-paid)
    and the dynamic `auto`/`auto/smart`/`auto/cheap` routes carry `legs: []`
    (the migration's ROUTE_COMMENT / AUTO_IDS convention) and have no
    combos.json counterpart at all - mapping doc section 4. They are served by
    another router on purpose, not orphaned, so they are never named as omitted.

    Only gateway-servable legs are rendered - see gateway_legs(): a leg the
    registry marks unavailable (routes.<id>.unavailable_legs or its provider's
    available: false), a leg policy.leg_rules denies, and a client_bound leg the
    gateway 403s are all dropped from the combo's `models`.

    The combo's "context" is the route's declared promise CLAMPED to the smallest
    window among those servable legs (clamp_route_context): a priority combo that
    can fall to a 128k leg must not promise 1M.

    "omitted" names the ORPHANED routes, and only those: a route whose `legs`
    is non-empty but whose every leg was dropped by gateway_legs(), so it once
    promised a gateway leg and can serve none now. It is the routes that render
    no combo while still declaring legs, so apply.sh/apply.ps1 can prune a live
    combo the registry stopped serving instead of leaving it in the store
    forever (OR1e). A route with `legs: []` is deliberately legless - the
    *-paid and auto* routes - and is in neither `combos` nor `omitted` (OR1g).
    `omitted` is disjoint from "retired" by construction - `retired` is a
    fixed hand-maintained list of dead ids no route uses any more.
    """
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}

    combos = []
    omitted = []
    for route_id in sorted(routes):
        route = routes[route_id]
        if not isinstance(route, dict):
            continue
        declared_legs = route.get("legs")
        legs = gateway_legs(route, registry)
        if not legs:
            if declared_legs:
                omitted.append(route_id)
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
            "context": clamp_route_context(
                route, registry, omniroute_surface["context_declared"], legs),
            "models": [gateway_ref(leg, registry) for leg in legs],
        })

    return {
        "$comment": OMNIROUTE_GENERATED_COMMENT,
        "retired": list(OMNIROUTE_RETIRED_IDS),
        "omitted": omitted,
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
    - treat "combos", "retired" and "omitted" as unordered: apply.sh (`for c in
      data.get("combos", [])`, `current = {c["name"] for c in ...}`) and apply.ps1
      (`foreach ($combo in $combos)`, retired filtered by `-cnotcontains`) both
      look combos up by name and retired/omitted ids up by membership, never by
      array position (read both scripts, 2026-09-26) - so array order is not
      semantic data here, unlike the ordered `models` list inside each combo (a
      fallback priority order, which this function leaves untouched).
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
    for key in ("retired", "omitted"):
        ids = out.get(key)
        if isinstance(ids, list):
            out[key] = sorted(ids, key=str)
    return out


def omniroute_diff(rendered: dict, current: dict) -> list:
    """Return the keys where a fresh render_omniroute() output and today's parsed
    combos.json differ, ignoring $comment and the order of
    "combos"/"retired"/"omitted" (see _canonical_omniroute). Empty means
    semantically equal - the spec 3.2 phase-1 gate."""
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
    module identifier) - the same importlib-by-path technique this repo uses
    for every other dash-named tool.
    Reused, not copied, so Leg/render_block/locate_blocks/parse_block/
    leading_indent/GATEWAY_ONLY/managed_tiers/litellm_servable_refs/
    provider_maps_from_dict can never
    drift from the tool that still owns configuration/litellm/config.yaml's
    actual managed blocks (task A4b's brief: "reuse ... rather than copying
    it"). A fresh module object every call, deliberately: render_litellm_
    blocks() below mutates its PROVIDER_PREFIX/API_BASE/ENV_KEY globals (Leg
    reads them at construction time, same as tools/sync-router-tiers.py's own
    main() does), and a fresh import per call keeps that mutation from
    leaking between two renders in the same process."""
    spec = importlib.util.spec_from_file_location(
        "autoos_sync_router_tiers", SYNC_ROUTER_TIERS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_litellm_blocks(registry: dict, config_text: str, tiers=None) -> dict:
    """Render the AUTOOS-MANAGED litellm blocks tools/sync-router-tiers.py owns
    (spec 3.2 phase 1, task A4b), sourcing what that tool takes from
    configuration/omniroute/combos.json and the legacy provider catalog
    instead from the loaded registry: each tier's ordered leg list from `routes.<tier>.legs`,
    and each leg's LiteLLM transport (prefix/api_base/env var) from
    `providers.<id>.litellm_prefix`/`litellm_env`/`api_base` - registry field
    names identical to the legacy provider catalog's own (verified during
    migration in docs/plans/2026-09-25-registry-mapping.md section 2), so
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

    Returns {tier: block_text}, one entry per renderable tier. By default the
    set is tools/sync-router-tiers.py's own managed_tiers(registry) - every
    route that declares `legs` and still has at least one LiteLLM-servable leg
    after GATEWAY_ONLY is dropped, in registry order. That replaces the old
    hand-maintained SYNCED_TIERS pair (l2-worker/l3-driver), so a tier becomes
    managed purely by existing in the registry. A tier whose final servable
    ref set is empty (all legs unavailable, policy-denied or client-bound, or
    all gateway-only) gets NO entry and NO block: an empty model list is not a
    valid config, but the registry itself decides which groups exist. Legless
    hand groups (every *-paid group; they declare no `legs`) are never managed
    and never rendered. Each block runs from its "# AUTOOS-MANAGED-START
    <tier>" line to its "# AUTOOS-MANAGED-END <tier>" line inclusive,
    newline-joined with no leading or trailing blank line - the exact slice
    tools/sync-router-tiers.py's own rewrite() replaces.

    Raises ValueError, naming every offending tier at once, when an explicitly
    passed tier has no `routes.<tier>`, or when `config_text` has no managed
    block for a tier that is actually rendered (a missing/duplicate/mismatched
    marker - the same cases tools/sync-router-tiers.py itself refuses via its
    own ConfigError, surfaced here as ValueError so a caller needs only one
    exception type)."""
    sync = _load_sync_router_tiers()
    tiers = tuple(tiers) if tiers is not None else sync.managed_tiers(registry)

    providers = _section(registry, "providers")
    sync.PROVIDER_PREFIX, sync.API_BASE, sync.ENV_KEY = sync.provider_maps_from_dict(providers)

    routes = _section(registry, "routes")
    missing_routes = [t for t in tiers if not isinstance(routes.get(t), dict)]
    if missing_routes:
        raise ValueError("registry has no routes.<id> for tier(s): %s" % ", ".join(missing_routes))

    # Only tiers with a non-empty final servable ref set get a block; a route
    # that drops to nothing is silently omitted (the registry decides which
    # groups exist), not an error.
    refs_by_tier = {}
    for tier in tiers:
        refs = sync.litellm_servable_refs(routes[tier], registry)
        if refs:
            refs_by_tier[tier] = refs

    lines = config_text.splitlines()
    try:
        blocks = sync.locate_blocks(lines)
    except sync.ConfigError as exc:
        raise ValueError(str(exc)) from exc
    missing_blocks = [t for t in refs_by_tier if t not in blocks]
    if missing_blocks:
        raise ValueError("config.yaml has no managed block for: %s" % ", ".join(missing_blocks))

    # K6/F3 (T1-CLEAN-4 rework, 2026-10-01): render_block() reads the skip names
    # out of the sync tool's module state, so this call owns that table for the
    # whole render — set from the tiers it actually emits, cleared of everything
    # else, so a second call in one process cannot inherit the first one's
    # `# litellm-skip:` lines into an unrelated block.
    sync.SKIPPED_BY_TIER.clear()
    sync.SKIPPED_BY_TIER.update(
        {tier: sync.skipped_refs(routes[tier], registry)
         for tier in refs_by_tier})

    rendered = {}
    for tier in refs_by_tier:
        start, end = blocks[tier]
        indent = sync.leading_indent(lines[start])
        extras = sync.parse_block(lines, start, end)
        # K6 (T1-CLEAN-4, 2026-10-01): a leg whose provider declares an auth
        # LiteLLM cannot express is dropped by litellm_servable_refs() and NAMED
        # here, in the block itself, so the next reader does not have to diff the
        # registry to find out why the leg is missing. sync.render_block() owns
        # the line (via SKIPPED_BY_TIER), so this tool's render and
        # tools/sync-router-tiers.py's own rewrite cannot drift apart.
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
    differs, byte for byte, from `config_text`'s own current managed block, plus
    any managed block `config_text` carries that the render no longer produces
    (a stale tier: every leg died or the route was removed). Empty means every
    rendered tier matches exactly AND no dead group is left behind - the spec
    3.2 phase-1 gate for configuration/litellm/config.yaml (task A4b). Walking
    only `rendered` was one-directional: a stale block stayed in the file, still
    served by LiteLLM, with this gate green (PROV review), which is exactly the
    drift tools/sync-router-tiers.py now prunes on rewrite."""
    problems = []
    for tier in sorted(rendered):
        try:
            current = litellm_block_text(config_text, tier)
        except ValueError:
            problems.append(tier)
            continue
        if rendered[tier] != current:
            problems.append(tier)
    sync = _load_sync_router_tiers()
    try:
        blocks = sync.locate_blocks(config_text.splitlines())
    except sync.ConfigError as exc:
        raise ValueError(str(exc)) from exc
    for tier in blocks:
        if tier not in rendered and tier not in problems:
            problems.append(tier)
    return sorted(set(problems))


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
# alphabetical - the registry is written with every key sorted); this is
# therefore a literal, hand-copied constant, the same convention
# this module's own OMNIROUTE_RETIRED_IDS and the migration's
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
    "l1-orchestrator", "l1-orchestrator-clean", "l1-orchestrator-paid",
    "l1-orchestrator-free-only",
    "l2-worker", "l2-worker-clean", "l2-worker-paid", "l2-worker-free-only",
    "l2-orchestrator",
    "l3-driver", "l3-driver-clean", "l3-driver-paid", "l3-driver-free-only",
    "spark-1.3-contributor",
    "opus-5-5",
    "t4-rag",
    "l2-researcher",
    "gemini-3.8-flash",
    "deepseek-v4.1-flash",
    # FREEWIRE 2026-09-30: pinned single-provider free combos for the
    # probe-passed free legs (L1-backlog/ws-free-probe-20260930). Listed here
    # because render_ide() requires this constant to name every route id (a
    # route added without it raises rather than silently mis-ordering).
    "hf-glm-5.2", "hf-qwen3.8-27b",
    "or-nemotron-3-super-free",
    # ORQWEN404 2026-10-06: or-qwen3.8-27b-free left IDE_MODEL_ORDER with its
    # route (openrouter qwen3.8-27b:free measured 404 upstream).
    "or-north-mini-code-free", "or-laguna-s-2.1-free",
    "groq-qwen3.8-27b",
    # TORDER 2026-10-01: pinned single-provider credit combos (ovh x3 + vertex).
    # Listed here because render_ide() requires this constant to name every
    # route id (a route added without it raises rather than silently mis-ordering).
    # OVHCODER-DROP 2026-10-08: ovh-qwen3-coder-30b left IDE_MODEL_ORDER with its
    # route (OVH withdrew Qwen3-Coder-30B-A3B-Instruct upstream, live 404), so
    # TORDER's ovh x3 is now ovh x2.
    "ovh-qwen3.8-27b", "ovh-gpt-oss-120b",
    "vertex-gemini-3.8-flash",
    # FAIK 2026-10-07: pinned single-provider credit singles (operator: usable
    # within the $6 grant; legs gated until per-token prices land).
    "faik-gpt-6-sol",
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

    Each entry's `effort_ladder` is derived from the first GATEWAY-SERVABLE leg's
    model-registry entry (via resolve_leg), filtered to omit "none" - never the
    first declared leg, which may be gated (see clamp_route_context's note).
    `reasoning_effort` is carried only when that served head's ladder contains it.
    Routes with no servable leg or a leg whose model has no effort_ladder get no
    effort_ladder field.

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
    servable = servable_route_ids(registry)
    for route_id in IDE_MODEL_ORDER:
        route = routes[route_id]
        if not isinstance(route, dict):
            raise ValueError("routes.%s is not an object" % route_id)
        if (route.get("legs") or []) and route_id not in servable:
            # OR1d: a route that declares legs but can serve none through
            # either gateway is offered by no declaration - the same rule
            # render_omniroute() applies to combos.json. A deliberately
            # legless route (legs: []) is NOT dropped: it never promised a
            # gateway leg.
            continue
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

        # The ladder and the default effort describe the leg that will ANSWER.
        # routes.<id>.legs[0] may be client-bound, gated or provider-off, so the
        # head is the first entry of the servable list, not the declared one
        # (finding 8: t1's picker offered spark's "minimal..xhigh" ladder while
        # only gemini - "low/medium/high" - could serve it).
        servable_legs = gateway_legs(route, registry)
        head_legs = servable_legs or (route.get("legs") or [])

        head_ladder = None
        if head_legs:
            try:
                _pid, head_model_id = resolve_leg(head_legs[0], registry)
            except ValueError:
                raise ValueError(
                    "routes.%s: served head leg %r cannot be resolved"
                    % (route_id, head_legs[0]))
            model_entry = _section(registry, "models").get(head_model_id)
            if isinstance(model_entry, dict):
                ladder = model_entry.get("effort_ladder")
                if isinstance(ladder, list):
                    # A non-string rung is a data error - raise immediately.
                    for rung in ladder:
                        if not isinstance(rung, str):
                            raise ValueError(
                                "routes.%s: non-string rung %r in model %s effort_ladder"
                                % (route_id, rung, head_model_id))
                    # Omit "none" so opencode gets only meaningful levels
                    filtered = [e for e in ladder if e != "none"]
                    if filtered:
                        head_ladder = filtered

        effort = canonical.get("effort_default")
        model = {
            "id": route_id,
            "name": canonical.get("display_name"),
            "context": clamp_route_context(
                route, registry, canonical.get("context"), servable_legs),
            "output": canonical.get("output"),
        }
        if effort is not None and (not head_ladder or effort in head_ladder):
            # An effort the served head does not carry is dropped, never
            # forwarded - the picker would send a level the leg rejects.
            model["reasoning_effort"] = effort
        if head_ladder:
            model["effort_ladder"] = head_ladder
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
    # LAYERS 2026-10-07 (operator): t*-ids renamed to l*-ids.
    "omniroute-t1-orchestrator", "omniroute-t1-orchestrator-clean",
    "omniroute-t1-orchestrator-free-only", "omniroute-t1-orchestrator-paid",
    "omniroute-t2-orchestrator", "omniroute-t2-worker",
    "omniroute-t2-worker-clean", "omniroute-t2-worker-free-only",
    "omniroute-t2-worker-paid", "omniroute-t3-driver",
    "omniroute-t3-driver-clean", "omniroute-t3-driver-free-only",
    "omniroute-t3-driver-paid", "omniroute-t4-researcher",
    "litellm-t1-orchestrator", "litellm-t1-orchestrator-free-only",
    "litellm-t1-orchestrator-paid", "litellm-t2-orchestrator",
    "litellm-t2-worker", "litellm-t2-worker-clean",
    "litellm-t2-worker-free-only", "litellm-t2-worker-paid",
    "litellm-t3-driver", "litellm-t3-driver-clean",
    "litellm-t3-driver-free-only", "litellm-t3-driver-paid",
    "litellm-t4-researcher",
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
    "omniroute-l1-orchestrator",
    "omniroute-l2-worker",
    "omniroute-l3-driver",
    "omniroute-l2-orchestrator",
    "omniroute-l2-worker-clean",
    "omniroute-l3-driver-clean",
    "omniroute-t4-rag",
    "omniroute-opus-5-5",
    "omniroute-gemini-3.8-flash",
    "omniroute-l2-worker-free-only",
    "omniroute-deepseek-v4.1-flash",
    "omniroute-l3-driver-free-only",
    "omniroute-l1-orchestrator-clean",
    "omniroute-spark-1.3-contributor",
    "openrouter-muse-spark-1.3-contributor",
    "litellm-l1-orchestrator",
    "litellm-l2-worker",
    "litellm-l3-driver",
    "litellm-l2-worker-free-only",
    "litellm-l3-driver-free-only",
    "litellm-l1-orchestrator-free-only",
    "omniroute-l1-orchestrator-free-only",
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
    servable = servable_route_ids(registry)
    for tier_id in OPENHANDS_TIER_ORDER:
        target = wanted[tier_id]
        if target is None:
            route_id = OPENHANDS_DIRECT_PROFILE_IDS[tier_id]
            if (routes[route_id].get("legs") or []) and route_id not in servable:
                # OR1d: same "declared legs, none servable -> no declaration"
                # rule as render_ide(); a legs: [] route is kept.
                continue
            profile = routes[route_id]["surfaces"]["openhands"]["direct_profile"]
            tier = {"id": tier_id, "gateway": profile.get("gateway")}
            if "model" in profile:
                tier["model"] = profile["model"]
            if "base_url" in profile:
                tier["base_url"] = profile["base_url"]
            tier["max_input_tokens"] = clamp_route_context(
                routes[route_id], registry, profile.get("max_input_tokens"))
            tier["max_output_tokens"] = profile.get("max_output_tokens")
            tier["reasoning"] = bool(profile.get("reasoning"))
            tiers.append(tier)
            continue

        gw, route_id = target
        if (routes[route_id].get("legs") or []) and route_id not in servable:
            # OR1d: a route that declares legs but has no gateway-servable leg
            # gets no tier here either.
            continue
        profile = routes[route_id]["surfaces"][gw]["openhands_profile"]
        tier = {"id": tier_id}
        if gw == "litellm":
            tier["gateway"] = "litellm"
        tier["model"] = profile.get("model", "openai/%s" % route_id)
        tier["max_input_tokens"] = clamp_route_context(
            routes[route_id], registry, profile.get("max_input_tokens"))
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
            return str(clamp_route_context(route, registry, surface["context_declared"]))

    for gw in MODELS_DOC_CONTEXT_SURFACES:
        surface = surfaces.get(gw)
        if isinstance(surface, dict) and surface.get("context") is not None:
            return "{:,}".format(
                clamp_route_context(route, registry, surface["context"]))

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
    false. Both flags are live today: cxa was switched off in 2026-09-26,
    and DSMAX (operator 2026-09-27T15:05:54Z) switched openrouter off at the
    provider level too (a re-probe hit 401 'insufficient credits' even for
    BYOK), on top of its per-leg unavailable_legs entries - see
    providers.openrouter's $comment. Both flags are registry-only signals the
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


def gateway_legs(route: dict, registry: dict) -> list:
    """routes.<id>.legs in order, minus every leg a gateway cannot serve:

      - _leg_is_unavailable(leg, route, registry): the route-level
        unavailable_legs[leg].available or the leg's provider available is
        false (the immutable flags - a bare unavailable_until never gates,
        see _leg_is_unavailable);
      - leg_denied(leg, registry): policy.leg_rules' first matching rule has
        allow false;
      - the resolved model carries client_bound: the leg only answers inside
        its own client (opencode-zen's muse-spark-1.3-contributor-free), so a
        gateway request 403s.

    Pure and time-independent: the same (route, registry) always returns the
    same list, no clock. A leg that resolve_leg cannot resolve is kept, not
    dropped - rule 1 already reports it loudly, and a render must not hide a
    malformed leg behind a silent filter.

    render_omniroute() renders only this list: a route with no gateway-servable
    leg gets no combo at all (a combo that serves nothing is worse than an
    absent one), the same shape a `legs: []` route already has.
    render_litellm_blocks() renders only this list too, but a synced tier with
    no servable leg is a hard ValueError - an empty model list in config.yaml
    is not a valid block."""
    legs = route.get("legs") or []
    out = []
    for leg in legs:
        if not isinstance(leg, str):
            out.append(leg)
            continue
        if _leg_is_unavailable(leg, route, registry) or leg_denied(leg, registry):
            continue
        try:
            _, model_id = resolve_leg(leg, registry)
        except ValueError:
            out.append(leg)
            continue
        model = _section(registry, "models").get(model_id)
        if isinstance(model, dict) and model.get("client_bound"):
            continue
        out.append(leg)
    return out


def provider_plan_limits(provider_id: str, model_id: str, registry: dict):
    """The measured `providers.<provider_id>.limits.<model_id>` row, or None.

    Brief R4 (2026-09-27) keyed that table by the provider's own model spelling,
    which is exactly `model_id` as `resolve_leg` returns it. None means "no
    measurement on record" -- never a deny: an unmeasured leg stays plannable.
    """
    provider = (_section(registry, "providers").get(provider_id) or {})
    if not isinstance(provider, dict):
        return None
    limits = provider.get("limits")
    if not isinstance(limits, dict):
        return None
    entry = limits.get(model_id)
    return entry if isinstance(entry, dict) else None


def plan_dead_reasons(provider_id: str, model_id: str, registry: dict) -> list:
    """MISTRALFIX (2026-09-28) -- why this plan cannot answer `provider_id`'s
    `model_id` at all; [] means the plan carries no veto.

    Measured directly against api.mistral.ai with the operator's key: the
    x-ratelimit headers said four of its models get **0 requests/minute** on
    this plan (HTTP 429 on every call, 51/51 in the gateway's own 7-day log)
    and a fifth answers 403 because the plan does not carry it. Before this
    predicate nothing read `rpm`, so the resolver planned such a leg as if it
    served and the fall-through burned the request.

      - ``rpm: 0`` -> ``plan: 0 rpm`` (a zero request quota);
      - ``plan_available: false`` -> ``plan: plan_available false`` (not on the
        plan; the 403 shape). Only an explicit boolean false denies -- a string,
        a null or a missing key is "not measured", and rule 10 rejects a
        non-boolean rather than let one read as truthy here.

    A small-but-real quota (rpm 10, rpm 125) is NOT dead: this is about a model
    that cannot answer, not one that answers slowly. Time-independent and pure,
    like every other predicate here -- it reads the recorded plan, no clock and
    no counters.
    """
    entry = provider_plan_limits(provider_id, model_id, registry)
    if entry is None:
        return []
    reasons = []
    if entry.get("rpm") == 0:
        reasons.append("plan: 0 rpm")
    if entry.get("plan_available") is False:
        reasons.append("plan: plan_available false")
    return reasons


# A declared context is a PROMISE the gateway hands the client ("a request this
# big gets answered"), and a priority combo keeps falling to its next leg while
# that promise stands. So the promise may only be as big as the smallest window
# among the legs it can actually be served by — PROVFIX3 finding 1: t1 promised
# 1M while gemini-3.8-flash (131072 advertised) is a servable leg of it, so any
# long conversation that fell to gemini would 400. docs/models.md states the same
# rule for the human reader ("a 200k model in a 1M-declared route would 400").
#
# combos.json spells a window as a ladder label. The floors are decimal on
# purpose: both spellings of a rung (131072 binary, 128000 decimal) clear the
# "128k" floor, so a leg recorded either way keeps the label.
CONTEXT_LADDER = [
    ("1M", 1_000_000), ("512k", 512_000), ("256k", 256_000), ("200k", 200_000),
    ("128k", 128_000), ("64k", 64_000), ("32k", 32_000), ("16k", 16_000),
    ("8k", 8_000), ("4k", 4_000), ("2k", 2_000), ("1k", 1_000),
]

# A leg's effort ladder must describe the leg that will answer, so an
# effort_default the served head does not carry is dropped, never forwarded (see
# render_ide()).


def context_label_to_tokens(label):
    """The numeric floor of a combos.json context label ("128k" -> 128000), or
    None when `label` is not a ladder string (an unknown spelling is left
    alone by the clamps rather than guessed at)."""
    if not isinstance(label, str):
        return None
    text = label.strip()
    for rung, floor in CONTEXT_LADDER:
        if text.lower() == rung.lower():
            return floor
    return None


def context_tokens_to_label(tokens):
    """The largest ladder label that `tokens` can actually serve (131072 ->
    "128k"). Below the whole ladder, the honest "Nk" of the number itself."""
    for rung, floor in CONTEXT_LADDER:
        if tokens >= floor:
            return rung
    return "%dk" % max(1, int(tokens) // 1000)


def leg_advertised_context(leg, registry: dict):
    """A leg's model's `context_advertised` as an int, or None when the leg does
    not resolve, has no model entry, or the entry records no window. None means
    "no evidence", and no evidence never clamps (an unrecorded window is not a
    small model)."""
    if not isinstance(leg, str):
        return None
    try:
        _provider_id, model_id = resolve_leg(leg, registry)
    except ValueError:
        return None
    model = _section(registry, "models").get(model_id)
    if not isinstance(model, dict):
        return None
    value = model.get("context_advertised")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def route_context_cap(route: dict, registry: dict, legs=None):
    """The smallest advertised window among `route`'s gateway-servable legs, or
    None when none of them records one. `legs` may be a precomputed
    gateway_legs() list (the renders all have one)."""
    legs = gateway_legs(route, registry) if legs is None else legs
    caps = [cap for cap in (leg_advertised_context(leg, registry) for leg in legs)
            if cap is not None]
    return min(caps) if caps else None


def clamp_route_context(route: dict, registry: dict, value, legs=None):
    """Clamp a declared context promise to what the route's servable legs can
    all take. Accepts and returns the shape it was given: a combos.json label
    string in, a label string out ("1M" -> "128k"); a numeric surface context in,
    a number out (1000000 -> 131072). Anything it cannot reason about
    (None, an unknown label, a route with no recorded leg window) is returned
    unchanged — this narrows promises, it does not invent them."""
    cap = route_context_cap(route, registry, legs)
    if cap is None or value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        tokens = context_label_to_tokens(value)
        if tokens is None or tokens <= cap:
            return value
        return context_tokens_to_label(cap)
    if isinstance(value, (int, float)):
        return value if value <= cap else cap
    return value


def servable_route_ids(registry: dict) -> set:
    """The route ids with at least one gateway-servable leg: exactly
    `routes.<id>` where gateway_legs() is non-empty.

    OR1d (ONE-ROUTER step 1d): "a route whose gateway_legs is empty is offered
    by no declaration". render_omniroute() and render_litellm_blocks() already
    enforce this from gateway_legs() directly; render_ide() and
    render_openhands() use this set to apply the SAME rule to their own
    declarations (catalog/ide-models.json, configuration/openhands/
    tier-profiles.json).

    Membership is deliberately gateway-servability, not "has any legs": a
    route that declares no legs at all (the LiteLLM-only *-paid and the
    dynamic auto* routes) is NOT a member either. The renders keep such a
    route's declaration on purpose - it never promised a gateway leg - so the
    filter they apply is "declared legs AND not servable", never this alone.

    Pure and time-independent, like gateway_legs(): see its own contract."""
    routes = registry.get("routes")
    routes = routes if isinstance(routes, dict) else {}
    return {
        route_id for route_id, route in routes.items()
        if isinstance(route, dict) and gateway_legs(route, registry)
    }


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
# rule 9 - leg_rules policy gates (briefs/common.md Claude budget)
# ===========================================================================


def _canonical_leg_spelling(leg: str, registry: dict) -> str:
    """The leg re-spelled with its provider's canonical `omniroute_id` prefix
    (the gateway's spelling), or the raw string when it does not resolve.
    resolve_leg() accepts EITHER the providers key or any provider's
    omniroute_id, so policy.leg_rules written against one spelling must still
    gate a leg written with the other (PROV finding 3: a leg written
    `cheapinference/glm-4.5-air` previously slipped past `cheaperinference/*`)."""
    if not isinstance(leg, str):
        return leg
    try:
        provider_id, model_id = resolve_leg(leg, registry)
    except ValueError:
        return leg
    provider = _section(registry, "providers").get(provider_id)
    omni = provider.get("omniroute_id") if isinstance(provider, dict) else None
    return "%s/%s" % (omni or provider_id, model_id)


def leg_rule_for(leg: str, registry: dict):
    """The first policy.leg_rules entry whose fnmatch `match` pattern matches
    `leg`, or None when no rule matches (no match = allowed). The single
    matcher: _check_leg_rules() and the renders (OR1) both call it, and
    autoos_resolver/probe_common import leg_denied/leg_rule_for from here, so
    this is the only place the comparison's semantics are decided.

    Both valid spellings of a leg are tested - the raw string and its canonical
    `omniroute_id` re-spelling (PROV finding 3) - so a rule matches whichever
    spelling the leg was written with. Rule order still decides first match.

    Matching is case-INSENSITIVE (DSAMEND2, Muse review of DSAMEND): pattern and
    leg are both casefolded before the fnmatch. `fnmatchcase` binds a rule to
    one casing only, so `*deepseek*pro*` left `samba/DeepSeek-V4-Pro` matching
    nothing at all and therefore allowed - the operator rule is 'never DeepSeek
    Pro under ANY provider id', and a deny an id's capitalisation can escape is
    no deny. The fold changes no committed verdict for any leg the registry
    names (LegRulesTests.test_no_committed_verdict_changes_when_matching_folds_case
    sweeps them); it only closes spellings that matched nothing before. The
    canonical re-spelling still resolves case-sensitively via resolve_leg, which
    is the providers catalog's own contract, not this matcher's."""
    rules = _section(registry, "policy").get("leg_rules")
    candidates = [leg]
    canonical = _canonical_leg_spelling(leg, registry)
    if canonical not in candidates:
        candidates.append(canonical)
    folded = [c.casefold() if isinstance(c, str) else c for c in candidates]
    for rule in rules if isinstance(rules, list) else []:
        if not (isinstance(rule, dict) and isinstance(rule.get("match"), str)):
            continue
        pattern = rule["match"].casefold()
        for candidate in dict.fromkeys(folded):
            if fnmatch.fnmatchcase(candidate, pattern):
                return rule
    return None


# ---------------------------------------------------------------------------
# policy.claude_budget (D-102 CLAUDEBUDGET, S2 item 1, 2026-09-28)
# ---------------------------------------------------------------------------

CLAUDE_BUDGET_MODES = ("normal", "budget")
CLAUDE_BUDGET_DEFAULT_BELOW = 0.25
_CLAUDE_BUDGET_KEYS = ("mode", "weekly_share_left", "budget_below", "source")


def claude_budget(registry) -> dict:
    """The shipped Claude-budget state, as one resolved dict.

    `policy.claude_budget` is the single home for "how much of this week's
    Claude allowance is left" (operator D-102: Claude only orchestrates and
    gives finals; writers, researchers and first reviewers use free/cheap legs).
    The resolver and every CLI reader call this instead of reading the JSON, so
    the ON/OFF rule exists exactly once:

        on = mode == "budget" OR weekly_share_left < budget_below

    `weekly_share_left` may be null (nobody has measured it yet), which is only
    decisive through `mode`. A registry with no `claude_budget` key at all is
    OFF -- an older catalog keeps routing exactly as it did before the field,
    and a missing measurement never silently restricts the routes.
    """
    entry = _section(registry, "policy").get("claude_budget")
    entry = entry if isinstance(entry, dict) else {}
    mode = entry.get("mode") if entry.get("mode") in CLAUDE_BUDGET_MODES \
        else "normal"
    share = entry.get("weekly_share_left")
    share = share if isinstance(share, (int, float)) and \
        not isinstance(share, bool) else None
    below = entry.get("budget_below")
    below = float(below) if isinstance(below, (int, float)) and \
        not isinstance(below, bool) else CLAUDE_BUDGET_DEFAULT_BELOW
    return {
        "on": mode == "budget" or (share is not None and share < below),
        "mode": mode,
        "weekly_share_left": share,
        "budget_below": below,
        "source": entry.get("source"),
    }


def _check_claude_budget(registry) -> list:
    """rule 12 - policy.claude_budget is shaped like the resolver reads it.

    Every field is checked rather than defaulted, because each one fails in the
    direction the operator is trying to avoid: an unknown key (a typo'd
    `budget_bellow`) silently means "no threshold", a share of 1.5 means
    "nothing is left to ration" -- and both keep planning Claude work after the
    weekly allowance is gone. `source` must be a string for the same reason the
    dated-value rule exists: a number or a list is not an attribution.
    """
    entry = _section(registry, "policy").get("claude_budget")
    if entry is None:
        return []
    if not isinstance(entry, dict):
        return ["claude_budget: policy.claude_budget must be an object"]
    problems = []
    for field in sorted(entry):
        if field not in _CLAUDE_BUDGET_KEYS and field != "$comment":
            problems.append(
                "claude_budget: policy.claude_budget.%s unknown key "
                "(allowed: %s)" % (field, ", ".join(_CLAUDE_BUDGET_KEYS)))
    if "mode" not in entry:
        problems.append("claude_budget: policy.claude_budget.mode is missing")
    elif entry["mode"] not in CLAUDE_BUDGET_MODES:
        problems.append(
            "claude_budget: policy.claude_budget.mode %r is not one of %s"
            % (entry["mode"], ", ".join(CLAUDE_BUDGET_MODES)))

    def _number(value):
        # A bool is an int in Python, and `True < 0.25` would read as a share of
        # 1 -- the one shape that turns budget mode off by accident.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    if "weekly_share_left" in entry and entry["weekly_share_left"] is not None:
        share = _number(entry["weekly_share_left"])
        if share is None:
            problems.append(
                "claude_budget: policy.claude_budget.weekly_share_left must be "
                "a number 0..1 or null (got %r)"
                % (entry["weekly_share_left"],))
        elif not 0.0 <= share <= 1.0:
            problems.append(
                "claude_budget: policy.claude_budget.weekly_share_left %s is "
                "outside 0..1 - a share above 1 would read as \"nothing left to "
                "ration\"" % (entry["weekly_share_left"],))
    if "budget_below" not in entry:
        problems.append("claude_budget: policy.claude_budget.budget_below is "
                        "missing")
    else:
        below = _number(entry["budget_below"])
        if below is None:
            problems.append(
                "claude_budget: policy.claude_budget.budget_below must be a "
                "number 0..1 (got %r) - claude_budget() only falls back to %s "
                "when the key is absent, so a string here is a threshold that "
                "never applies"
                % (entry["budget_below"], CLAUDE_BUDGET_DEFAULT_BELOW))
        elif not 0.0 <= below <= 1.0:
            problems.append(
                "claude_budget: policy.claude_budget.budget_below %s is outside "
                "0..1" % (entry["budget_below"],))
    if "source" not in entry:
        problems.append("claude_budget: policy.claude_budget.source is missing "
                        "- name the decision this value came from")
    elif not isinstance(entry["source"], str) or not entry["source"].strip():
        problems.append(
            "claude_budget: policy.claude_budget.source must be a non-empty "
            "string (got %r)" % (entry["source"],))
    return problems


def leg_denied(leg: str, registry: dict) -> bool:
    """Whether policy.leg_rules denies `leg` (first matching rule has allow false)."""
    rule = leg_rule_for(leg, registry)
    return rule is not None and rule.get("allow") is not True


def _check_leg_rules(registry) -> list:
    """rule 9 - every serving route leg is allowed by policy.leg_rules.

    The rules encode the operator's budget and gateway-fitness decisions
    (briefs/common.md 'Claude budget'; L0 ONE-ROUTER 2026-09-27 measured the
    live gateway serving legs they forbid). A denied leg passes only while it
    is gated the same way the renders read gating - _leg_is_unavailable():
    routes.<id>.unavailable_legs[leg].available false, or its provider's
    available false. An entry with available true (or only a past
    unavailable_until) does not gate it: the check stays clock-free."""
    problems = []
    for route_id, route in sorted(_section(registry, "routes").items()):
        if not isinstance(route, dict):
            continue
        for leg in dict.fromkeys(route.get("legs") or []):
            if not isinstance(leg, str):
                continue  # rule 1 reports it as unresolved
            rule = leg_rule_for(leg, registry)
            if rule is None or rule.get("allow") is True:
                continue
            if _leg_is_unavailable(leg, route, registry):
                continue
            problems.append("leg_rules: routes.%s leg %s denied by %s (%s) - gate it in "
                            "routes.%s.unavailable_legs or change the rule"
                            % (route_id, leg, rule.get("id", "(unnamed)"),
                               rule.get("reason", ""), route_id))
    return problems


# The allowed keys of a providers.<id>.limits.<model> entry, exactly the
# properties of catalog/ai-registry.schema.json's $defs.provider_limits
# (rpm/rpd/tpm/tpd + the MISTRALFIX plan_available flag + the D20 source tag).
# Kept as a module constant next to _check_provider_limits rather than read from
# the schema at check time.
_LIMITS_ENTRY_KEYS = ("rpm", "rpd", "tpm", "tpd", "plan_available", "source")


def _check_provider_limits(registry) -> list:
    """rule 10 - every provider limits key resolves and values are non-negative
    ints (brief R4, 2026-09-27).

    providers.<id>.limits is keyed by the provider's own model spelling (the
    part of a leg after its '<provider>/' prefix). Reusing resolve_leg -- the
    same one-leg rule the validator and the resolver share -- means an unknown
    model spelling fails closed here exactly as it would at route time. The
    rpm/rpd/tpm/tpd values are each optional, but when present must be
    non-negative ints; the resolver reads tpm as a request-size filter (brief
    R4) and, since MISTRALFIX (2026-09-28), rpm and plan_available as a
    cannot-serve gate via plan_dead_reasons().

    Review R4FIX (2026-09-27): a limits entry may carry only the keys of the
    schema's provider_limits def -- an unknown key (measured: a 'tmp' key
    passed this check) is a malformed entry and is reported naming the
    provider, the model and the key.

    MISTRALFIX: `plan_available` must be a boolean. The gate tests
    `is False`, and a recorded string "false" would read truthy there -- so the
    type is checked here, where the malformed value can still be named.
    """
    problems = []
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict):
            continue
        limits = provider.get("limits")
        if not isinstance(limits, dict):
            continue
        for key, entry in sorted(limits.items()):
            label = "providers.%s.limits.%s" % (provider_id, key)
            try:
                resolve_leg(provider_id + "/" + key, registry)
            except ValueError:
                problems.append(
                    "limits: %s key %r does not resolve to a %s model"
                    % (label, key, provider_id))
            if not isinstance(entry, dict):
                problems.append("limits: %s is not an object" % label)
                continue
            for field in sorted(entry):
                if field not in _LIMITS_ENTRY_KEYS:
                    problems.append(
                        "limits: %s.%s unknown key %r (allowed: %s)"
                        % (label, field, field, ", ".join(_LIMITS_ENTRY_KEYS)))
            for field in ("rpm", "rpd", "tpm", "tpd"):
                if field not in entry:
                    continue
                value = entry[field]
                if not isinstance(value, int) or isinstance(value, bool) \
                        or value < 0:
                    problems.append(
                        "limits: %s.%s must be a non-negative int (got %r)"
                        % (label, field, value))
            if "plan_available" in entry \
                    and not isinstance(entry["plan_available"], bool):
                problems.append(
                    "limits: %s.plan_available must be a boolean (got %r) - "
                    "plan_dead_reasons() tests `is False`, so a string would "
                    "read as available" % (label, entry["plan_available"]))
    return problems


# The fields of one policy.reviewers entry whose TYPE this check owns; presence
# is rule 6's job (the schema marks all five required), so a missing field is
# reported once, by the rule that can name it as "missing: ... (required key)".
_REVIEWER_STR_FIELDS = ("client", "model", "family")


def _check_reviewers(registry) -> list:
    """rule 11 - policy.reviewers is a non-empty ordered list of reviewers the
    resolver can actually walk (brief REVROUTE (S2) item 1, 2026-09-27).

    The list is the one home for "who may review": the resolver reads it instead
    of a family table of its own, so an entry that names a client nobody has is
    a reviewer that can never be spawned -- and the review silently degrades to
    whoever else is left. That is why the client half is checked against
    ``clients`` here.

    The ``model`` half is deliberately NOT resolved against providers/models.
    Like ``policy.free_client_models``, it is the client's own spelling (a
    gateway combo name, or Haiku's client-side alias), not a route leg -- and a
    reviewer is picked per client, so a leg-shaped check would reject valid
    entries. The check stays clock-free: ``source`` is exempt from rule 5, and
    nothing here reads ``available``/``unavailable_until`` (that is the
    resolver's job at plan time).
    """
    problems = []
    reviewers = _section(registry, "policy").get("reviewers")
    if reviewers is None:
        # rule 6 does not mark policy.reviewers required (the schema's policy
        # `required` list is the spec's, and this file predates the field), so
        # an absent list is reported here rather than reading as "clean".
        return ["reviewers: policy.reviewers is missing - the resolver has no "
                "reviewer preference list to walk"]
    if not isinstance(reviewers, list) or not reviewers:
        return ["reviewers: policy.reviewers must be a non-empty ordered list"]

    known_clients = _section(registry, "clients")
    for index, entry in enumerate(reviewers):
        label = "policy.reviewers[%d]" % index
        if not isinstance(entry, dict):
            problems.append("reviewers: %s is not an object" % label)
            continue
        for field in _REVIEWER_STR_FIELDS:
            if field not in entry:
                continue
            value = entry[field]
            if not isinstance(value, str) or not value:
                problems.append("reviewers: %s.%s must be a non-empty string "
                                "(got %r)" % (label, field, value))
        if "paid" in entry and not isinstance(entry["paid"], bool):
            problems.append("reviewers: %s.paid must be a boolean (got %r)"
                            % (label, entry["paid"]))
        if "first_pass_only" in entry and not isinstance(entry["first_pass_only"], bool):
            problems.append("reviewers: %s.first_pass_only must be a boolean "
                            "(got %r)" % (label, entry["first_pass_only"]))
        client = entry.get("client")
        if isinstance(client, str) and client and client not in known_clients:
            problems.append("reviewers: %s.client %r is not a registry client "
                            "(known: %s)"
                            % (label, client, ", ".join(sorted(known_clients))))

        # A `leg` is the one link from the reviewer to a provider, and the
        # resolver reads availability and the training test off it -- so a leg
        # that does not resolve, or carries a family that disagrees with its own
        # model's, would silently disable the different-family rule (the check
        # compares the entry's spelling, the resolver compares this one).
        leg = entry.get("leg")
        if isinstance(leg, str) and leg:
            try:
                provider_id, model_id = resolve_leg(leg, registry)
            except ValueError as exc:
                problems.append("reviewers: %s.leg %s does not resolve: %s"
                                % (label, leg, exc))
                continue
            model_family = (_section(registry, "models").get(model_id) or {}).get("family")
            entry_family = entry.get("family")
            if model_family and entry_family and model_family != entry_family:
                problems.append(
                    "reviewers: %s.family %r disagrees with models.%s.family %r "
                    "(leg %s)" % (label, entry_family, model_id, model_family, leg))
    return problems


# ===========================================================================
# rule 12 - the risk policy is a policy tools/autoos_risk.py can actually apply
# ===========================================================================

# The rule types `autoos_risk.classify()` implements. A rule of any other type
# is data without a reader: the registry would say "high risk" about a shape no
# code looks at, and the diff would be classified as if the rule did not exist.
_RISK_RULE_TYPES = ("path_glob", "diff_deletion", "added_regex", "registry_policy")
# Rule types whose whole match is their pattern, so a missing one matches nothing.
_RISK_PATTERN_TYPES = ("path_glob", "added_regex")
# Every field `classify()` reads for a given type, beyond the shared three. A
# field outside its type's set is inert: `paths` on a path_glob looks like a
# scope and is not one, and a stray key says nothing at all.
# (RISKTIER-a2: `exclude` is the second glob a path_glob reads — the tracked
# `.env.example` template a `**/.env.*` rule must not catch — and
# `min_deleted_lines` is the numstat threshold a diff_deletion reads.)
_RISK_RULE_FIELDS = {
    "path_glob": {"pattern", "exclude"},
    "diff_deletion": {"min_deleted_lines"},
    "added_regex": {"pattern", "paths"},
    "registry_policy": set(),
}
# Spec 4: the only two risk classes a card can carry.
_RISK_CLASSES = ("normal", "high")


def _check_risk_policy(registry) -> list:
    """rule 12 - policy.risk_rules / risk_audit_percent / review_counts are the
    shapes the classifier and the resolver read (RISKTIER-a, operator Q-013
    2026-09-28).

    `autoos_risk.classify()` raises on an unknown rule type rather than skipping
    it, and this check is what keeps such a rule out of the committed registry in
    the first place. The same reasoning covers a pattern-less glob (matches
    nothing, silently), a regex that does not compile (raises at classify time on
    someone else's machine), an audit percent outside 0-100 (a sample that is
    either never drawn or always), and a review count that is negative or
    non-integer (the resolver would ask for a fraction of a reviewer).

    Like rule 11, this stays clock-free and never reads availability: the audit
    draw is a property of a sha the caller supplies, not of the registry.
    """
    problems = []
    policy = _section(registry, "policy")

    rules = policy.get("risk_rules")
    if rules is None:
        problems.append("risk_rules: policy.risk_rules is missing - the classifier "
                        "has no rules to apply to a diff")
    elif not isinstance(rules, list):
        problems.append("risk_rules: policy.risk_rules must be a list")
    else:
        for index, rule in enumerate(rules):
            label = "policy.risk_rules[%d]" % index
            if not isinstance(rule, dict):
                problems.append("risk_rules: %s is not an object" % label)
                continue
            rule_type = rule.get("type")
            if rule_type not in _RISK_RULE_TYPES:
                problems.append(
                    "risk_rules: %s has unknown type %r - tools/autoos_risk.py "
                    "classify() applies %s and raises on anything else"
                    % (label, rule_type, ", ".join(_RISK_RULE_TYPES)))
            else:
                allowed = ({"type", "reason", "source"}
                           | _RISK_RULE_FIELDS[rule_type])
                for field in sorted(set(rule) - allowed):
                    problems.append(
                        "risk_rules: %s.%s is not read by a %s rule - classify() "
                        "ignores it, so the rule does not say what it looks like "
                        "it says (a %s rule carries %s)"
                        % (label, field, rule_type, rule_type,
                           ", ".join(sorted(_RISK_RULE_FIELDS[rule_type]))
                           or "no further fields"))
            for field in ("reason", "source"):
                value = rule.get(field)
                if not isinstance(value, str) or not value:
                    problems.append("risk_rules: %s.%s must be a non-empty string"
                                    % (label, field))
            pattern = rule.get("pattern")
            if rule_type in _RISK_PATTERN_TYPES and (
                    not isinstance(pattern, str) or not pattern):
                problems.append("risk_rules: %s (%s) needs a non-empty pattern - "
                                "without one it matches nothing" % (label, rule_type))
            if rule_type == "added_regex" and isinstance(pattern, str) and pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append("risk_rules: %s pattern %r does not compile (%s)"
                                    % (label, pattern, exc))
            if "paths" in rule and (not isinstance(rule["paths"], str)
                                    or not rule["paths"]):
                problems.append("risk_rules: %s.paths must be a non-empty string "
                                "(added_regex only)" % label)
            if "exclude" in rule and (not isinstance(rule["exclude"], str)
                                      or not rule["exclude"]):
                problems.append("risk_rules: %s.exclude must be a non-empty glob "
                                "(path_glob only) - an empty one excludes nothing, "
                                "which is the opposite of why the field is there"
                                % label)
            threshold = rule.get("min_deleted_lines")
            if "min_deleted_lines" in rule and (
                    isinstance(threshold, bool)
                    or not isinstance(threshold, int)
                    or threshold < 0):
                problems.append("risk_rules: %s.min_deleted_lines must be an int "
                                ">= 0, got %r - classify() compares it against the "
                                "diff's own deleted-line count" % (label, threshold))

    percent = policy.get("risk_audit_percent")
    if percent is None:
        problems.append("risk_audit_percent: policy.risk_audit_percent is missing - "
                        "the audit would fall back to the code's default instead of "
                        "the operator's sampling rate")
    else:
        entry = percent if isinstance(percent, dict) else {}
        value = entry.get("value", percent) if isinstance(percent, dict) else percent
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            problems.append("risk_audit_percent: policy.risk_audit_percent must be an "
                            "int 0-100, got %r" % (value,))
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            problems.append("risk_audit_percent: policy.risk_audit_percent.source must "
                            "be a non-empty string (D20)")

    counts = policy.get("review_counts")
    if counts is None:
        problems.append("review_counts: policy.review_counts is missing - the resolver "
                        "would review by its own hard-coded counts, not by policy")
    elif not isinstance(counts, dict):
        problems.append("review_counts: policy.review_counts must be an object keyed "
                        "by risk class")
    else:
        for risk_class in sorted(set(counts) - set(_RISK_CLASSES) - {"$comment"}):
            problems.append("review_counts: policy.review_counts.%s is not a risk "
                            "class (spec 4: %s)" % (risk_class, " | ".join(_RISK_CLASSES)))
        for risk_class in _RISK_CLASSES:
            entry = counts.get(risk_class)
            label = "policy.review_counts.%s" % risk_class
            if not isinstance(entry, dict):
                problems.append("review_counts: %s must be an object" % label)
                continue
            cross_family = entry.get("cross_family")
            if (isinstance(cross_family, bool) or not isinstance(cross_family, int)
                    or cross_family < 0):
                problems.append("review_counts: %s.cross_family must be an int >= 0, "
                                "got %r" % (label, cross_family))
            if not isinstance(entry.get("final"), bool):
                problems.append("review_counts: %s.final must be a boolean, got %r"
                                % (label, entry.get("final")))
            source = entry.get("source")
            if not isinstance(source, str) or not source:
                problems.append("review_counts: %s.source must be a non-empty string "
                                "(D20)" % label)
    return problems


# ===========================================================================
# rule 13 - policy.handoff_caps: the cap a lane stops at
# ===========================================================================

_CAP_NUMBER_FIELDS = ("window", "cap_fraction", "cap_tokens")


def _cap_row_problem(label, entry):
    """One ``policy.handoff_caps`` row's problem line, or None when it holds.

    The invariant is spec 8.3's own formula: the row states ``window`` and
    ``cap_fraction`` *and* the derived ``cap_tokens``, and the two can disagree.
    They disagree silently, because ``tools/autoos_context.py`` reads
    ``cap_tokens`` and never recomputes it from the other pair -- so the hand
    edit that moved Sonnet's row to 250k / 0.25 (routing-00 D-085, 2026-09-28)
    had to change three fields to agree, and nothing read the pair to confirm it
    did.

    The line names the row and prints every number it compared, so the reader
    does not have to open the registry to know which of the three to fix.
    """
    if not isinstance(entry, dict):
        return ("handoff_caps: %s is not an object (got %r)" % (label, entry))
    for field in _CAP_NUMBER_FIELDS:
        value = entry.get(field)
        # bool is an int in Python, and `true` as a cap or a fraction is a
        # mistake, not a number to multiply.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return ("handoff_caps: %s.%s must be a number (got %r) - the cap is "
                    "round(window * cap_fraction), and %s has to be a number for "
                    "that to mean anything" % (label, field, value, field))
    window, fraction, cap = (entry["window"], entry["cap_fraction"],
                             entry["cap_tokens"])
    expected = round(window * fraction)
    if cap != expected:
        return ("handoff_caps: %s.cap_tokens %s != round(window %s * "
                "cap_fraction %s) = %s - autoos_context reads cap_tokens and "
                "never recomputes it, so this row states two caps at once"
                % (label, cap, window, fraction, expected))
    return None


def _check_handoff_caps(registry) -> list:
    """rule 13 - every hand-off cap row is self-consistent and a default exists
    (brief AUTHORS (S1) item 2, 2026-09-28).

    Two facts, both of which the schema is silent on because each is about the
    *relation* between fields rather than their types:

      - ``cap_tokens == round(window * cap_fraction)``, per row;
      - at least one row carries ``"*"`` in its ``match`` list. That row is the
        only fallback ``tools/autoos_context.py`` can use for a model no other
        row names, so without it a cap is not read from this registry at all --
        ``load_caps`` falls through to its own hand-maintained ``DEFAULT_CAPS``
        and reports ``source='default'``, and the policy nobody checks is the
        policy that drifts.

    A *missing* section is not this rule's: the schema marks
    ``policy.handoff_caps`` required, so rule 6 names it once. A present section
    of the wrong shape is here, because nothing else multiplies it. Clock-free
    and pure, like every other rule.
    """
    caps = _section(registry, "policy").get("handoff_caps")
    if caps is None:
        return []
    if not isinstance(caps, dict):
        return ["handoff_caps: policy.handoff_caps must be an object keyed by "
                "orchestrator-model class (got %r)" % (caps,)]
    problems = [_cap_row_problem("policy.handoff_caps.%s" % key, entry)
                for key, entry in caps.items()]
    problems = [problem for problem in problems if problem]
    if not any(isinstance(entry, dict) and "*" in (entry.get("match") or [])
               for entry in caps.values()):
        problems.append('handoff_caps: policy.handoff_caps has no row matching '
                        '"*" - a model no other row names gets no cap from this '
                        'registry and falls through to autoos_context '
                        'DEFAULT_CAPS (hand-maintained, not policy)')
    return problems


# ===========================================================================
# check / validate
# ===========================================================================


def _check_monthly_caps(registry) -> list:
    """providers.<id>.monthly_cap_usd, when present, is a positive number with a
    non-empty monthly_cap_source (WS-DSCALL, 2026-09-28).

    A paid caller (deepseek_call.py) refuses at or above the cap, so a zero,
    negative, string or boolean cap would either block every call or none; a
    bool is rejected explicitly because it is an int to Python.
    """
    problems = []
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict) or "monthly_cap_usd" not in provider:
            continue
        cap = provider["monthly_cap_usd"]
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
            problems.append("providers.%s.monthly_cap_usd must be a positive number, got %r"
                            % (provider_id, cap))
        source = provider.get("monthly_cap_source")
        if not isinstance(source, str) or not source.strip():
            problems.append("providers.%s.monthly_cap_usd has no monthly_cap_source"
                            % provider_id)
    return problems


def _check_credit_guards(registry, today=None) -> list:
    """Every `credit`-tier provider carries a complete spend guard (brief FREEKEYS-1,
    D-132/D-141): `credit_usd` is the operator's grant, `monthly_cap_usd` equals it (a
    caller REFUSES at 100 % of the grant) and `monthly_warn_fraction` is a fraction in
    (0, 1) naming where it WARNS first (0.8 = 80 %).

    The trio is checked as data, not prose: a credit row with no cap would be spent
    without a limit, a cap that is not the grant silently raises or lowers the refuse
    line below what the operator actually funded, and a warn fraction outside (0, 1)
    warns at or after the refusal (0.0 warns on every call, 1.0 never warns) - all
    three read as "configured" while doing nothing.
    """
    problems = []
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict) or provider.get("tier") != "credit":
            continue
        credit = provider.get("credit_usd")
        if isinstance(credit, bool) or not isinstance(credit, (int, float)) or credit <= 0:
            problems.append("providers.%s: tier credit needs a positive credit_usd, got %r"
                            % (provider_id, credit))
        cap = provider.get("monthly_cap_usd")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
            problems.append("providers.%s: tier credit needs monthly_cap_usd (the refuse "
                            "line), got %r" % (provider_id, cap))
        elif not isinstance(credit, bool) and isinstance(credit, (int, float)) and cap != credit:
            problems.append("providers.%s: monthly_cap_usd %r != credit_usd %r - the guard "
                            "must refuse at 100%% of the grant the operator funded"
                            % (provider_id, cap, credit))
        fraction = provider.get("monthly_warn_fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) \
                or not 0 < fraction < 1:
            problems.append("providers.%s: tier credit needs a monthly_warn_fraction in "
                            "(0, 1) (0.8 = warn at 80%% of the grant), got %r"
                            % (provider_id, fraction))
        # T1-CREDIT-FIX-5 M3: the optional reserve below the grant. It must be a
        # non-negative number strictly below credit_usd -- a margin that reaches
        # the grant would refuse every leg, and one at or above it makes the
        # hard stop the whole point of the guard disappear.
        margin = provider.get("credit_hard_stop_margin_usd")
        if margin is not None:
            if isinstance(margin, bool) or not isinstance(margin, (int, float)) \
                    or not math.isfinite(margin) or margin < 0:
                problems.append("providers.%s: credit_hard_stop_margin_usd must be a "
                                "finite number >= 0, got %r" % (provider_id, margin))
            elif not isinstance(credit, bool) and isinstance(credit, (int, float)) \
                    and margin >= credit:
                problems.append("providers.%s: credit_hard_stop_margin_usd %r must be "
                                "less than credit_usd %r" % (provider_id, margin, credit))
    # A warn fraction without a cap is a number nothing reads.
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if isinstance(provider, dict) and "monthly_warn_fraction" in provider \
                and "monthly_cap_usd" not in provider:
            problems.append("providers.%s: monthly_warn_fraction without monthly_cap_usd"
                            % provider_id)
    # T1-CREDIT-FIX: the dated manual spend fallback. `credit_spent_usd` is the
    # operator's dated reading of what a grant already billed, read when the
    # gateway call-log ledger cannot be reached (those rows priced client-side
    # are the only spend ledger in the repo -- no separate store exists, so no
    # equivalent field predates this one). A figure with no date cannot age and
    # a date with no figure judges nothing, so the pair is all-or-nothing; a
    # negative or non-numeric figure, or an empty date, would silently mistime
    # the fallback, so both are flagged here rather than trusted.
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict):
            continue
        figure = provider.get("credit_spent_usd")
        as_of = provider.get("credit_spent_as_of")
        if figure is None and as_of is None:
            continue
        if figure is None or as_of is None:
            problems.append("providers.%s: credit_spent_usd and credit_spent_as_of "
                            "go together (a dated manual spend needs both a "
                            "figure and its date), got %r and %r"
                            % (provider_id, figure, as_of))
            continue
        if isinstance(figure, bool) or not isinstance(figure, (int, float)) \
                or figure < 0 or not math.isfinite(figure):
            problems.append("providers.%s: credit_spent_usd must be a finite number >= 0, "
                            "got %r" % (provider_id, figure))
        if not isinstance(as_of, str) or not as_of.strip():
            problems.append("providers.%s: credit_spent_as_of must be a non-empty "
                            "date (YYYY-MM-DD), got %r" % (provider_id, as_of))
        else:
            # Validate YYYY-MM-DD format
            try:
                datetime.strptime(as_of.strip(), "%Y-%m-%d")
            except ValueError:
                problems.append("providers.%s: credit_spent_as_of must be a valid "
                                "date YYYY-MM-DD, got %r" % (provider_id, as_of))
    # T1-CREDIT-FIX-7 R2: the optional grant start. `credit_started` (YYYY-MM-DD)
    # is the date the grant started billing, so the guard can measure the WHOLE
    # grant instead of month-to-date. Absent is fine (the window stays
    # month-to-date, loudly); present must be a real calendar date, not in the
    # future -- a grant that starts tomorrow has no measured spend yet.
    # T1-CREDIT-FIX-8 C5: `today` is injectable (a date or datetime) so tests
    # pin the future rule without a clock; without it the wall clock is read.
    if today is None:
        ref_today = datetime.now(timezone.utc).date()
    elif isinstance(today, datetime):
        ref_today = (today.astimezone(timezone.utc).date()
                     if today.tzinfo is not None else today.date())
    else:
        ref_today = today
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict) or "credit_started" not in provider:
            continue
        raw = provider.get("credit_started")
        if not isinstance(raw, str) or not raw.strip():
            problems.append("providers.%s: credit_started must be a date "
                            "YYYY-MM-DD, got %r" % (provider_id, raw))
            continue
        try:
            parsed = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
        except ValueError:
            problems.append("providers.%s: credit_started must be a valid "
                            "calendar date YYYY-MM-DD, got %r"
                            % (provider_id, raw))
            continue
        if parsed > ref_today:
            problems.append("providers.%s: credit_started %r is in the future - "
                            "a grant that starts tomorrow has no measured "
                            "spend yet" % (provider_id, raw))
    # No evasion (brief FREEKEYS-1b item 4): the grant is the fact and `tier` is the
    # label every reader branches on, so a row that keeps `credit_usd` and calls
    # itself `free` silently un-limits the money, drops out of the leg filter's
    # guard, and re-admits the provider to the `-clean` sweeps. A real downgrade has
    # to move the grant out of the row, which is the edit a reviewer can see.
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict) or provider.get("tier") == "credit":
            continue
        credit = provider.get("credit_usd")
        if credit:
            cap = provider.get("monthly_cap_usd")
            problems.append("providers.%s: tier %r carries credit_usd %r - a funded grant "
                            "stays tier credit (its refuse line monthly_cap_usd %r is "
                            "checked only there); move the money out of the row to "
                            "downgrade it" % (provider_id, provider.get("tier"),
                                              credit, cap))
    return problems


def _provider_model_ids(model_id, model, provider_id, provider) -> list:
    """The namespaces `model` is spelled under for `provider_id`, if any.

    A model belongs to a provider in one of three spellings, all of them read from
    the data instead of a name list: its `display_name` carries a gateway namespace
    (``deepinfra/google/gemini-2.5-flash``), its own id does (``meta/muse-...``), or
    it is namespaced by the provider with a hyphen (``morph-dsv4flash``, served as
    ``morph/morph-dsv4flash``). Returns every namespace among the provider's id,
    ``omniroute_id`` and declared ``model_prefix`` that one of those spellings starts
    with -- more than one is normal (a provider id and its prefix are often equal).
    """
    shown = str(model.get("display_name") or "")
    names = {"pid": provider_id, "omni": provider.get("omniroute_id"),
             "prefix": provider.get("model_prefix")}
    out = []
    for kind, namespace in names.items():
        if not namespace:
            continue
        if (shown.startswith(namespace + "/") or model_id.startswith(namespace + "/")
                or (kind != "prefix" and model_id.startswith(namespace + "-"))):
            out.append(namespace)
    return out


def _check_model_prefix(registry) -> list:
    """``model_prefix`` must name a namespace the provider's models really carry.

    The field is what `gateway_ref()` rewrites a registry leg to at render time and
    what a consumer strips to recover the model id, so a row whose models are served
    under a namespace and whose prefix is null leaves the stripping unresolvable
    (brief FREEKEYS-1b item 1 / rev-freekeys1 finding 1: `morph`, `deepinfra` and
    `nebius` serve ``morph/*``, ``deepinfra/*`` and ``nebius/*`` per the live
    gateway's ``GET /v1/models``, and both checks below fail closed on exactly that
    shape). A prefix that is not the namespace its own models sit under is worse
    than none: the render silently asks the gateway for models it does not have.

    A provider with no registered model rows is never judged -- most declared
    prefixes today name a connection nobody has registered a model against yet.
    """
    problems = []
    models = _section(registry, "models")
    for provider_id, provider in sorted(_section(registry, "providers").items()):
        if not isinstance(provider, dict):
            continue
        declared = provider.get("model_prefix")
        served = []  # (model_id, namespaces it is served under)
        for model_id, model in sorted(models.items()):
            if not isinstance(model, dict):
                continue
            namespaces = _provider_model_ids(model_id, model, provider_id, provider)
            if namespaces:
                served.append((model_id, namespaces))
        if not served:
            continue
        own = {provider_id, provider.get("omniroute_id")}
        if declared is None:
            # Only a gateway namespace (a `display_name` spelled `<ns>/<id>`) proves
            # the provider's models are served prefixed; a bare `<pid>-<model>` id is
            # how this registry spells them, which is not a claim about the gateway.
            namespaced = [(model_id, namespace)
                          for model_id, spaces in served for namespace in spaces
                          if namespace in own
                          and str(models[model_id].get("display_name") or "")
                          .startswith(namespace + "/")]
            if namespaced:
                model_id, namespace = namespaced[0]
                problems.append("providers.%s.model_prefix is null while %s is served "
                                "under '%s/': set model_prefix to '%s'"
                                % (provider_id, model_id, namespace, namespace))
            continue
        for model_id, spaces in served:
            if declared in spaces:
                continue
            problems.append("providers.%s.model_prefix is '%s' but %s is spelled under "
                            "'%s': a prefix must name the namespace the gateway serves"
                            % (provider_id, declared, model_id, "', '".join(spaces)))
    return problems


def _check_provider_prices(registry) -> list:
    """models.<id>.provider_prices, when present, prices one provider's leg of
    a model the model-level row cannot price (T1-CREDIT-FIX-6, D-220): the
    entry is a non-empty object keyed by provider id, each value carrying a
    positive price_in/price_out, a non-empty price_source and a real calendar
    price_as_of date (YYYY-MM-DD). A zero price is rejected, not defaulted --
    it is the exact state the resolver refuses the leg for.
    """
    problems = []
    providers = _section(registry, "providers")
    for model_id, model in sorted(_section(registry, "models").items()):
        if not isinstance(model, dict) or "provider_prices" not in model:
            continue
        label = "models.%s.provider_prices" % model_id
        scoped = model["provider_prices"]
        if not isinstance(scoped, dict) or not scoped:
            problems.append("%s must be a non-empty object" % label)
            continue
        for provider_id, entry in sorted(scoped.items()):
            entry_label = "%s.%s" % (label, provider_id)
            if provider_id not in providers:
                problems.append("%s: unknown provider %r"
                                % (label, provider_id))
            if not isinstance(entry, dict):
                problems.append("%s must be an object" % entry_label)
                continue
            for key in ("price_in", "price_out"):
                value = entry.get(key)
                if isinstance(value, bool):
                    problems.append("%s.%s must be a number > 0, got %r"
                                    % (entry_label, key, value))
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    number = 0.0
                if not number > 0.0:
                    problems.append("%s.%s must be a number > 0, got %r"
                                    % (entry_label, key, value))
            source = entry.get("price_source")
            if not isinstance(source, str) or not source.strip():
                problems.append("%s.price_source must be a non-empty string"
                                % entry_label)
            as_of = entry.get("price_as_of")
            if not isinstance(as_of, str) or not DATE_RE.fullmatch(as_of):
                problems.append("%s.price_as_of must be YYYY-MM-DD, got %r"
                                % (entry_label, as_of))
                continue
            try:
                datetime.strptime(as_of, "%Y-%m-%d")
            except ValueError:
                problems.append("%s.price_as_of must be YYYY-MM-DD, got %r"
                                % (entry_label, as_of))
    return problems


PROMPT_CACHE_VALUES = ("true", "documented", "false", "unknown")


def _check_prompt_cache(registry) -> list:
    """models.<id>.prompt_cache, when present, is one of the four strings
    "true" / "documented" / "false" / "unknown" (AO-PROBE-D657, D-657/D-658
    2026-10-08): the prompt-caching verdict of a leg -- `true` measured by
    `tools/probe-free.py --cache` (the second same-prefix call reports cached
    tokens > 0), `false` measured a reported 0, `documented` the vendor's own
    docs attest prefix caching with no measured hit (Google implicit caching),
    `unknown` nothing has answered for it. Absence means the same as unknown;
    a failed or unreported call is never written as `false`.

    The value set is owned here because readers of a flag disagree about a
    malformed one: `value is True` says no to the string "false" while a plain
    truthiness test says yes, so the same leg could be gated twice in opposite
    directions. Strings are the whole set because the probe's TSV cell, the
    vendor's claim and the measured hit are three spellings of one fact a
    boolean could only hold two of. A bad value is named here, where the model
    id is still attached to it.
    """
    problems = []
    for model_id, model in sorted(_section(registry, "models").items()):
        if not isinstance(model, dict) or "prompt_cache" not in model:
            continue
        value = model["prompt_cache"]
        if not isinstance(value, str) or value not in PROMPT_CACHE_VALUES:
            problems.append(
                "models.%s.prompt_cache must be one of %s (got %r) - "
                "an unmeasured leg is \"unknown\", never true/false/null "
                "(probe D-657 writes the string its TSV cell carries)"
                % (model_id, ", ".join(repr(v) for v in PROMPT_CACHE_VALUES),
                   value))
        if "prompt_cache_source" in model:
            source = model["prompt_cache_source"]
            if not isinstance(source, str) or not source.strip():
                problems.append(
                    "models.%s.prompt_cache_source must be a non-empty string "
                    "naming the probe run or vendor document behind "
                    "prompt_cache (got %r)" % (model_id, source))
    return problems


def _check_paid_local_cap(registry) -> list:
    """policy.paid_local_cap_usd, when present, is a number > 0.

    T1-CREDIT-FIX-10 M2 (D-240): the USD cap an UNMEASURED paid leg is held
    to (default 20 when absent -- absence is fine, not a problem). A present
    but non-positive/non-numeric value is flagged loudly; the runtime reads
    the default rather than billing against a made-up number.
    """
    problems = []
    policy = _section(registry, "policy")
    if "paid_local_cap_usd" not in policy:
        return problems
    value = policy["paid_local_cap_usd"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not value > 0:
        problems.append("policy.paid_local_cap_usd must be a number > 0, "
                        "got %r" % (value,))
    return problems


def check_registry(registry, today=None) -> list:
    """Return every spec 3.1 problem, in rule order; empty means the registry is clean."""
    problems = []
    problems.extend(_check_legs(registry))
    problems.extend(_check_unique_ids(registry))
    problems.extend(_check_model_key_case(registry))
    problems.extend(_check_privacy(registry))
    problems.extend(_check_privacy_evidence(registry))
    problems.extend(_check_private_hosts(registry))
    problems.extend(_check_dated_values(registry))
    problems.extend(_check_required_keys(registry))
    problems.extend(_check_until_values(registry))
    problems.extend(_check_unavailable_until_pairs_available(registry))
    problems.extend(_check_leg_rules(registry))
    problems.extend(_check_provider_limits(registry))
    problems.extend(_check_monthly_caps(registry))
    problems.extend(_check_provider_prices(registry))
    problems.extend(_check_prompt_cache(registry))
    problems.extend(_check_paid_local_cap(registry))
    problems.extend(_check_credit_guards(registry, today))
    problems.extend(_check_model_prefix(registry))
    problems.extend(_check_reviewers(registry))
    problems.extend(_check_claude_budget(registry))
    problems.extend(_check_risk_policy(registry))
    problems.extend(_check_handoff_caps(registry))
    return problems


def _ok_line(registry) -> str:
    return "ok: registry %s, %d routes, %d models, %d providers" % (
        registry.get("version"),
        len(registry.get("routes", {})),
        len(registry.get("models", {})),
        len(registry.get("providers", {})),
    )


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
        _write_lf(args.out, text)
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
        _write_lf(args.out, text)
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
        _write_lf(args.out, text)
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
        _write_lf(args.out, text)
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
        _write_lf(args.out, rendered)
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
        ("validate", "validate the registry (same rules as check)"),
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
