#!/usr/bin/env python3
"""One-shot converter: today's model/provider/route catalogs -> catalog/ai-registry.json.

Spec docs/plans/2026-09-25-routing-v2-spec.md section 3 (registry) and section 3.2
(migration, phase 1): add the registry alongside the old files, prove every old entry
maps to a registry path (docs/plans/2026-09-25-registry-mapping.md), and prove the
data is equal - before anything switches to reading the registry or the old files are
deleted (that is task A4/A5, not this one).

Reads (never writes):
    catalog/llm-models.json
    catalog/providers.json
    catalog/ide-models.json
    configuration/omniroute/combos.json
    configuration/openhands/tier-profiles.json
    .agents/skills/unattended-orchestration/provider-windows.json
    tools/autoos_resolver.py   (DEFAULT_BUCKET_TABLE - the resolver's own data, not a
                                 file this converter is migrating)
    tools/autoos_clients.py    (CLIENTS - binary/gateway/auth per client)

Writes:
    catalog/ai-registry.json  (sorted keys, 2-space indent, trailing newline)

Deterministic and stdlib-only: two runs against the same source files produce
byte-identical output (AGENTS.md rule 3 - safe to run twice; a second run with no
source change reports "skipped" in spirit by simply changing nothing).

Values the old files do not carry at all (family, tool_calls, trains_on_prompts, tier,
clients.*, effort_ladder, routes.*.class, ...) come from small explicit tables below,
each with a comment naming its source or its reasoning where no source exists.
Conservative unknowns, per the brief and D8:
    - tool_calls defaults to "unproven" for every model: nothing in this repo has
      measured tool-calling for any of these legs yet.
    - context_usable is 50% of context_advertised, source "default", until a probe
      measures the model (D8; overlay-measured entries would carry source "probe").

Operator decision 2026-09-25 (docs/plans/2026-09-25-routing-v2-plan.md "Operator
steps": OpenRouter is not topped up): every openrouter provider and every openrouter
leg is kept in the registry - unchanged, in the same order, so the data mirrors
today's files exactly for the phase-1 equality gate - but marked available: false
with a $comment citing this decision (see mark_openrouter_unavailable()).

Usage:
    python3 tools/registry-convert.py [--check]

    (default)   write catalog/ai-registry.json
    --check     change nothing; exit 1 if a fresh render would differ
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
CONFIG = ROOT / "configuration"
UNATTENDED_SKILL = ROOT / ".agents" / "skills" / "unattended-orchestration"

LLM_MODELS_PATH = CATALOG / "llm-models.json"
PROVIDERS_PATH = CATALOG / "providers.json"
IDE_MODELS_PATH = CATALOG / "ide-models.json"
COMBOS_PATH = CONFIG / "omniroute" / "combos.json"
TIER_PROFILES_PATH = CONFIG / "openhands" / "tier-profiles.json"
PROVIDER_WINDOWS_PATH = UNATTENDED_SKILL / "provider-windows.json"
OUTPUT_PATH = CATALOG / "ai-registry.json"

REGISTRY_VERSION = "2026-09-25"
D8_USABLE_FRACTION = 0.5  # spec D8: 50% of advertised until a probe measures it.
BUCKETS = ("S0", "S1", "S2", "S3", "S4")

sys.path.insert(0, str(ROOT / "tools"))
import autoos_clients   # noqa: E402  CLIENTS: binary/gateway/auth per client
import autoos_resolver  # noqa: E402  DEFAULT_BUCKET_TABLE - the resolver's own data


def _load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


OPENROUTER_DOWN_COMMENT = (
    "Operator decision 2026-09-25 (docs/plans/2026-09-25-routing-v2-plan.md, "
    "'Operator steps': OpenRouter credits are exhausted and not topped up). Kept in "
    "the registry unchanged - same provider/model/leg data, same order - so it still "
    "mirrors today's files for the spec section 3.2 phase-1 equality gate; marked "
    "available: false so a caller does not route onto a dead leg."
)


# ===========================================================================
# providers
# ===========================================================================

# Facts catalog/providers.json does not carry at all. trains_on_prompts is
# provider-level (spec 3.1), which cannot express a provider that hosts both a
# training and a non-training model - see docs/plans/2026-09-25-registry-mapping.md
# "Open choices" for the two known exceptions (openrouter, zen).
PROVIDER_EXTRA = {
    "groq": {
        "trains_on_prompts": False, "tier": "free",
    },
    "google_ai_studio": {
        "trains_on_prompts": True, "tier": "free",
        "comment": "Measured, not a default: docs/models.md 'Proven effort ladders & costs' "
                   "records 'free head trains (AI Studio); paid twin per terms' for "
                   "gemini-3.8-flash. The paid twin is reached through the openrouter "
                   "provider (trains_on_prompts=false there), never through this one.",
    },
    "mistral": {
        "trains_on_prompts": False, "tier": "paid",
    },
    "cerebras": {
        "trains_on_prompts": False, "tier": "free",
    },
    "deepseek": {
        "trains_on_prompts": False, "tier": "paid",
    },
    "meta": {
        "trains_on_prompts": True, "tier": "paid",
        "comment": "Its only leg (models.muse-spark, llm-models.json's own 'muse-spark' "
                   "entry) is the contributor model, which trains by contract "
                   "(configuration/omniroute/combos.json $comment) - unlike openrouter/zen "
                   "it hosts no non-training sibling, so the provider-level flag is exact "
                   "here, not a simplification.",
    },
    "openrouter": {
        "trains_on_prompts": False, "tier": "paid",
        "comment": "KNOWN EXCEPTION (provider-level trains_on_prompts cannot express this): "
                   "the meta/muse-spark-1.3-contributor leg reached through this provider "
                   "trains by contract; combos.json's own $comment documents the "
                   "2026-09-21 operator downgrade this caused - 't1-orchestrator-clean ... "
                   "no longer means trains-nothing - it means paid-only'. Every other leg "
                   "reached through openrouter (deepseek, mistral, google twins) does not "
                   "train.",
    },
    "zen": {
        "trains_on_prompts": False, "tier": "free",
        "comment": "KNOWN EXCEPTION: the free 'muse-spark-1.3-contributor-free' promo leg "
                   "trains by contract like its contributor siblings, but no -clean combo "
                   "ever references it - only the paid opencode-zen/deepseek-v4.1-flash leg "
                   "does, which does not train (see models.'muse-spark-1.3-contributor-free').",
    },
    "cohere": {
        "trains_on_prompts": False, "tier": "free",
        "comment": "tier=free reflects tier-profiles.json's own description ('t4-rag cohere "
                   "RAG ... trial keys') - a temporary free trial, not a published free tier.",
    },
    "cheapinference": {
        "trains_on_prompts": False, "tier": "paid",
    },
    "SambaNova": {
        "trains_on_prompts": False, "tier": "free",
    },
    "cloudflare_workers_ai": {
        "trains_on_prompts": False, "tier": "free",
        "comment": "Unverified default: not registered with any Account ID yet "
                   "(combos.json $comment) and no combo references this provider.",
    },
    "hugging_face": {
        "trains_on_prompts": False, "tier": "free",
        "comment": "Unverified default: no combo references this provider.",
    },
    "devin": {
        "trains_on_prompts": False, "tier": "subscription",
        "comment": "Unverified default for trains_on_prompts: no combo references this "
                   "provider. tier=subscription: Devin is sold as a subscription product.",
    },
    "omniroute": {
        "trains_on_prompts": False, "tier": "paid",
        "comment": "Not a model provider: providers.json's own $comment calls this 'the "
                   "client key apps send', with omniroute_id null. tier/trains_on_prompts "
                   "are placeholders - nothing routes through it as a leg.",
    },
}

# Providers combos.json legs reference that catalog/providers.json does not carry at
# all: OAuth/subscription bridges with no api-keys.yml entry (no key to mirror into
# litellm/.env). litellm_env is null - tools/sync-router-tiers.py's own GATEWAY_ONLY
# set already treats {"antigravity", "cc"} as "no LiteLLM transport".
EXTRA_PROVIDERS = {
    "antigravity": {
        "omniroute_id": "antigravity", "litellm_env": None, "litellm_prefix": None,
        "api_base": None, "provider_data": None,
        "trains_on_prompts": True, "tier": "free",
        "comment": "Google Antigravity OAuth bridge (agy CLI): hosts both Gemini and Claude "
                   "legs (gemini-3.7-flash-high/medium, claude-opus-4-6-thinking). No "
                   "published data-training policy found for this consumer surface "
                   "(checked docs/ and .agents/skills/unattended-orchestration/, "
                   "2026-09-25); trains_on_prompts defaults conservatively to true "
                   "(unmeasured, D8's 'assume the less private option' pattern). Never "
                   "referenced by a -clean route today, so this default changes no filter "
                   "outcome.",
    },
    "cc": {
        "omniroute_id": "cc", "litellm_env": None, "litellm_prefix": None,
        "api_base": None, "provider_data": None,
        "trains_on_prompts": False, "tier": "subscription",
        "comment": "Claude Code subscription overflow leg (opus-4-6, t2-orchestrator). "
                   "docs/models.md: 'subscription terms, never API-logged' / '$0 marginal "
                   "(seat-metered)'; Anthropic's public consumer/subscription policy is "
                   "not to train on these conversations by default. audit-router.py bans "
                   "any other anthropic/claude API reference, so this is the only Claude "
                   "provider entry the registry needs.",
    },
}

# provider-windows.json -> providers.<id>.windows (spec 6.4). Off-peak ranges are
# the hand-computed complement of DeepSeek's published peak hours (not derived at
# runtime - simpler to review and just as deterministic). ADR 0006 C5 notes the
# source is a *partial* match: the "excluding Chinese public holidays" rule is not
# captured here either, carried forward so that gap is not lost.
_DEEPSEEK_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
_DEEPSEEK_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_DEEPSEEK_VERIFIED = "2026-09-18"
PROVIDER_WINDOWS = {
    "deepseek": {
        "windows": [
            {"days": _DEEPSEEK_WEEKDAYS, "utc_from": "01:00", "utc_to": "04:00",
             "price_factor": 1.0, "kind": "price", "source": _DEEPSEEK_SOURCE,
             "verified": _DEEPSEEK_VERIFIED},
            {"days": _DEEPSEEK_WEEKDAYS, "utc_from": "06:00", "utc_to": "10:00",
             "price_factor": 1.0, "kind": "price", "source": _DEEPSEEK_SOURCE,
             "verified": _DEEPSEEK_VERIFIED},
            {"days": _DEEPSEEK_WEEKDAYS, "utc_from": "00:00", "utc_to": "01:00",
             "price_factor": 0.5, "kind": "price", "source": _DEEPSEEK_SOURCE,
             "verified": _DEEPSEEK_VERIFIED},
            {"days": _DEEPSEEK_WEEKDAYS, "utc_from": "04:00", "utc_to": "06:00",
             "price_factor": 0.5, "kind": "price", "source": _DEEPSEEK_SOURCE,
             "verified": _DEEPSEEK_VERIFIED},
            {"days": _DEEPSEEK_WEEKDAYS, "utc_from": "10:00", "utc_to": "23:59",
             "price_factor": 0.5, "kind": "price", "source": _DEEPSEEK_SOURCE,
             "verified": _DEEPSEEK_VERIFIED},
            {"days": ["sat", "sun"], "utc_from": "00:00", "utc_to": "23:59",
             "price_factor": 0.5, "kind": "price", "source": _DEEPSEEK_SOURCE,
             "verified": _DEEPSEEK_VERIFIED},
        ],
        "comment": "peak_utc/peak_days from provider-windows.json, half price everywhere "
                   "else per its offpeak_note; the three off-peak weekday ranges and the "
                   "full weekend range are this converter's hand-computed complement of "
                   "the two peak ranges (00:00-23:59 used for 'through end of day' - the "
                   "registry's utc_to pattern has no 24:00). ADR 0006 finding C5: this "
                   "source is only a *partial* match for DeepSeek's real pricing - the "
                   "'excluding Chinese public holidays' rule is not captured here either.",
    },
    "meta": {
        "windows": [
            {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
             "utc_from": "12:00", "utc_to": "21:00", "price_factor": 1.0, "kind": "load",
             "note": "Muse Spark; same pattern as Claude per the operator",
             "source": "operator observation, 2026-09-18 (no published schedule): "
                       "high load 14:00-23:00 CEST",
             "verified": None},
        ],
    },
    # provider-windows.json's "anthropic" key has no matching providers.json id: the
    # registry's only Claude-related provider is "cc" (the subscription bridge) -
    # audit-router.py bans any direct anthropic API reference, so there is nothing
    # else for a load window to describe. Folded in here rather than dropped.
    "cc": {
        "windows": [
            {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
             "utc_from": "12:00", "utc_to": "21:00", "price_factor": 1.0, "kind": "load",
             "note": "quiet 21:00-12:00 UTC; throttling risk while busy",
             "source": "operator observation, 2026-09-18 (no published schedule): "
                       "high load 14:00-23:00 CEST",
             "verified": None},
        ],
        "comment": "windows folded in from provider-windows.json's 'anthropic' key - see "
                   "the module docstring in tools/registry-convert.py and the mapping "
                   "doc's Open choices.",
    },
}


def build_providers(providers_doc, provider_windows_doc):
    del provider_windows_doc  # windows come from the PROVIDER_WINDOWS table above,
    # hand-verified against provider-windows.json rather than re-parsed from it -
    # see PROVIDER_WINDOWS's own comment.
    providers = {}
    for pid, entry in providers_doc["providers"].items():
        extra = PROVIDER_EXTRA[pid]
        out = {
            "id": pid,
            "omniroute_id": entry.get("omniroute_id"),
            "litellm_env": entry.get("litellm_env"),
            "litellm_prefix": entry.get("litellm_prefix"),
            "api_base": entry.get("api_base"),
            "provider_data": entry.get("provider_data"),
            "trains_on_prompts": extra["trains_on_prompts"],
            "tier": extra["tier"],
        }
        if pid in PROVIDER_WINDOWS:
            out["windows"] = PROVIDER_WINDOWS[pid]["windows"]
        comments = [c for c in (extra.get("comment"), PROVIDER_WINDOWS.get(pid, {}).get("comment")) if c]
        if comments:
            out["$comment"] = comments if len(comments) > 1 else comments[0]
        providers[pid] = out

    for pid, extra in EXTRA_PROVIDERS.items():
        out = {k: v for k, v in extra.items() if k != "comment"}
        out["id"] = pid
        if pid in PROVIDER_WINDOWS:
            out["windows"] = PROVIDER_WINDOWS[pid]["windows"]
        comments = [c for c in (extra.get("comment"), PROVIDER_WINDOWS.get(pid, {}).get("comment")) if c]
        if comments:
            out["$comment"] = comments if len(comments) > 1 else comments[0]
        providers[pid] = out

    return providers


# Single legs that are down while their provider still serves other models.
# Kept in place (same order, same data) and flagged like the OpenRouter legs.
UNAVAILABLE_LEGS = {
    "opencode-zen/deepseek-v4.1-flash": (
        "Operator decision 2026-09-26: the tool-calling probe "
        "(tools/probe-toolcalls.py) got 402 payment required / 429 on this leg; "
        "marked available: false so the strict tool_calls filter and callers skip it."),
}


def mark_legs_unavailable(registry, legs=None):
    """Flag each leg of UNAVAILABLE_LEGS in every route that lists it, beside
    any unavailable_legs entry already there."""
    for leg, comment in (UNAVAILABLE_LEGS if legs is None else legs).items():
        for route in registry["routes"].values():
            if leg in route["legs"]:
                route.setdefault("unavailable_legs", {})[leg] = {
                    "available": False, "$comment": comment}


def mark_openrouter_unavailable(registry):
    """Operator decision 2026-09-25 (see OPENROUTER_DOWN_COMMENT): flag the provider
    and every leg reached through it, without deleting or reordering anything."""
    provider = registry["providers"]["openrouter"]
    provider["available"] = False
    existing = provider.get("$comment")
    comments = (existing if isinstance(existing, list) else [existing] if existing else [])
    comments.append(OPENROUTER_DOWN_COMMENT)
    provider["$comment"] = comments if len(comments) > 1 else comments[0]

    for route in registry["routes"].values():
        openrouter_legs = [leg for leg in route["legs"] if leg.split("/", 1)[0] == "openrouter"]
        if openrouter_legs:
            route["unavailable_legs"] = {
                leg: {"available": False, "$comment": OPENROUTER_DOWN_COMMENT}
                for leg in openrouter_legs
            }


# ===========================================================================
# models
# ===========================================================================

# Values catalog/llm-models.json does not carry: family (upstream vendor lineage),
# effort_ladder (only known for reasoning models with a measured or public ladder;
# [] otherwise). tool_calls is "unproven" for every model in this migration (D8-style
# conservative unknown - see module docstring), so it is not repeated per row here.
MODEL_EXTRA = {
    "muse-spark": {
        "family": "meta",
        "effort_ladder": ["minimal", "low", "medium", "high", "xhigh", "max"],
        "comment": "Ladder from docs/models.md 'Proven effort ladders & costs': "
                   "'minimal, low, medium, high, xhigh, max (advertised; max = heaviest)'.",
    },
    "deepseek-v4-flash": {"family": "deepseek", "effort_ladder": []},
    "openrouter-free": {"family": "openrouter", "effort_ladder": []},
    "openrouter-nemotron-ultra": {
        "family": "nvidia", "effort_ladder": ["low", "medium", "high"],
        "comment": "reasoning=true but no measured ladder; conservative three-rung "
                   "default (see mapping doc Open choices).",
    },
    "openrouter-nemotron-super": {
        "family": "nvidia", "effort_ladder": ["low", "medium", "high"],
        "comment": "Same conservative default as openrouter-nemotron-ultra.",
    },
    "openrouter-nemotron-lightning": {"family": "nvidia", "effort_ladder": []},
    "openrouter-nemotron-nano-omni": {
        "family": "nvidia", "effort_ladder": ["low", "medium", "high"],
        "comment": "Same conservative default as openrouter-nemotron-ultra.",
    },
    "openrouter-laguna": {"family": "poolside", "effort_ladder": []},
    "openrouter-laguna-xs": {"family": "poolside", "effort_ladder": []},
    "openrouter-north-mini-code": {"family": "cohere", "effort_ladder": []},
    "openrouter-nex-pro": {"family": "nex-agi", "effort_ladder": []},
    "openrouter-nex-mini": {"family": "nex-agi", "effort_ladder": []},
    "openrouter-inkling": {"family": "thinkingmachines", "effort_ladder": []},
    "openrouter-inkling-small": {"family": "thinkingmachines", "effort_ladder": []},
    "openrouter-dots3-note": {
        "family": "dots-studio", "effort_ladder": ["low", "medium", "high"],
        "comment": "Same conservative default as openrouter-nemotron-ultra.",
    },
    "openrouter-ling-fin": {"family": "inclusionai", "effort_ladder": []},
    "openrouter-ling-sante": {"family": "inclusionai", "effort_ladder": []},
    "openrouter-ling-vl": {"family": "inclusionai", "effort_ladder": []},
    "ollama-qwen2.5-coder": {
        "family": "qwen", "effort_ladder": [],
        "comment": "family is the underlying model's lineage (Qwen), not the hosting "
                   "provider (Ollama, direct.provider).",
    },
}

_UNPRICED = (
    "vendor price not sourced in this migration (no figure in the old catalogs or "
    "docs/models.md); recorded as 0.0, not a real free-tier guarantee - do not use "
    "for cost math until priced (registry.py probe / promote, task A2's follow-ups)."
)
_DEEPSEEK_FLASH_PRICE_NOTE = (
    "price_in/price_out approximate: reused from models.'deepseek-v4-flash' "
    "(llm-models.json), the closest priced sibling in this family - no published "
    "price found specifically for this snapshot in this migration."
)

# Models referenced only by configuration/omniroute/combos.json legs, with no
# catalog/llm-models.json entry of their own. Keyed by the exact model-id spelling
# the leg uses after its first '/' (providers spell the same real model differently;
# see docs/plans/2026-09-25-registry-mapping.md Open choices). context/output are
# taken from the matching catalog/ide-models.json pinned route where one exists, else
# from the tier (t2-worker/t3-driver/t4-rag) that leads with this leg, noted per row.
EXTRA_MODELS = {
    "muse-spark-1.3-contributor-free": {
        "family": "meta", "context_advertised": 1048576, "output_max": 131072,
        "reasoning": True, "effort_ladder": ["minimal", "low", "medium", "high", "xhigh", "max"],
        "price_in": 0.0, "price_out": 0.0, "client_bound": "opencode",
        "comment": "The Zen free contributor promo leg. Same real model as "
                   "models.muse-spark (llm-models.json) and "
                   "models.'meta/muse-spark-1.3-contributor', free while the promo "
                   "runs. client_bound follows spec 3.1's own example ('opencode for "
                   "Zen free legs'): combos.json documents this leg 403ing proxied "
                   "traffic ('OpenCode's free tier can only be used from within "
                   "OpenCode').",
    },
    "meta/muse-spark-1.3-contributor": {
        "family": "meta", "context_advertised": 1048576, "output_max": 131072,
        "reasoning": True, "effort_ladder": ["minimal", "low", "medium", "high", "xhigh", "max"],
        "price_in": 1e-7, "price_out": 2e-7, "price_cache_read": 2e-9,
        "comment": "The OpenRouter contributor (paid) leg of the same real model as "
                   "models.muse-spark; price reused from that llm-models.json entry - "
                   "identical commercial arrangement, different provider path.",
    },
    "gemini-3.8-flash": {
        "family": "google", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "Native Gemini free head. context/output from catalog/ide-models.json's "
                   "own 'gemini-3.8-flash' pinned route. Ladder from docs/models.md "
                   "('low, medium, high only').",
    },
    "gemini-3.7-flash-high": {
        "family": "google", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "Antigravity OAuth pool leg inside the t2-worker combo, no pinned "
                   "route of its own; context/output are t2-worker's declared numbers "
                   "used as a proxy.",
    },
    "gemini-3.7-flash-medium": {
        "family": "google", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "Antigravity OAuth pool leg inside the t2-worker-free-only combo; "
                   "same proxy-numbers note as gemini-3.7-flash-high.",
    },
    "openai/gpt-oss-120b": {
        "family": "openai-oss", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "groq's vendor-prefixed spelling of GPT-OSS-120B (free tier). "
                   "GPT-OSS is publicly documented as a reasoning model with a "
                   "low/medium/high effort control; context/output are t2-worker's "
                   "declared numbers.",
    },
    "gpt-oss-120b": {
        "family": "openai-oss", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "cerebras's and sambanova's shared bare spelling of the same free "
                   "GPT-OSS-120B model as 'openai/gpt-oss-120b' (groq's spelling); one "
                   "entry covers both providers' legs.",
    },
    "glm-4.5-air": {
        "family": "zhipu", "context_advertised": 131072, "output_max": 32768,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "cheaperinference resale leg (own billing, NOT a free tier). " + _UNPRICED,
    },
    "kimi-k3": {
        "family": "moonshot", "context_advertised": 131072, "output_max": 32768,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "cheaperinference resale leg (own billing, NOT a free tier). " + _UNPRICED,
    },
    "deepseek/deepseek-v4.1-flash": {
        "family": "deepseek", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["none", "low", "high", "max"],
        "price_in": 3e-7, "price_out": 1.2e-6, "price_cache_read": 6e-9,
        "comment": "openrouter's vendor-prefixed spelling. Ladder from docs/models.md "
                   "('none, low, high, max only'). " + _DEEPSEEK_FLASH_PRICE_NOTE,
    },
    "deepseek-flash": {
        "family": "deepseek", "context_advertised": 131072, "output_max": 32768,
        "reasoning": False, "effort_ladder": [],
        "price_in": 3e-7, "price_out": 1.2e-6, "price_cache_read": 6e-9,
        "comment": "deepseek-direct leg. docs/models.md is explicit that this is a "
                   "*different snapshot* from deepseek-v4.1-flash ('no v4-flash or "
                   "deepseek-flash legs' in the v4.1-flash pinned route) - reasoning/"
                   "ladder are not assumed from that sibling. " + _DEEPSEEK_FLASH_PRICE_NOTE,
    },
    "deepseek-v4.1-flash": {
        "family": "deepseek", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["none", "low", "high", "max"],
        "price_in": 3e-7, "price_out": 1.2e-6, "price_cache_read": 6e-9,
        "comment": "opencode-zen's bare spelling of the same real model as "
                   "'deepseek/deepseek-v4.1-flash' (openrouter's spelling); the paid "
                   "zen leg (used in t2-worker-clean/t3-driver-clean). Ladder from "
                   "docs/models.md. " + _DEEPSEEK_FLASH_PRICE_NOTE,
    },
    "mistral-small-latest": {
        "family": "mistral", "context_advertised": 131072, "output_max": 32768,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "mistral direct, keyed/billed account (docs/models.md: 'same key "
                   "bills past the free pool'). output_max uses t2-worker-clean's "
                   "32768 (its larger use site) as the model's own ceiling; "
                   "t3-driver's surface still declares its own clamped 16384. " + _UNPRICED,
    },
    "claude-opus-4-6-thinking": {
        "family": "anthropic", "context_advertised": 200000, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "Antigravity OAuth pool leg. price is sourced, not a placeholder: "
                   "docs/models.md 'Claude family ... $0 marginal (seat-metered)'. "
                   "Ladder is a conservative default (no measured Claude ladder here).",
    },
    "claude-opus-4-6": {
        "family": "anthropic", "context_advertised": 200000, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "cc (Claude Code subscription) overflow leg; same sourced $0 "
                   "marginal cost and conservative ladder as claude-opus-4-6-thinking.",
    },
    "mistral-code-latest": {
        "family": "mistral", "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "trains_on_prompts": True,
        "comment": "Leads t3-driver; same-key free pool then billed past it "
                   "(docs/models.md). " + _UNPRICED + " Model-level "
                   "trains_on_prompts override (PRIV brief, 2026-09-26): this "
                   "free pool trains, but the mistral provider is otherwise "
                   "paid/non-training (mistral-small-latest does not train) - "
                   "tests/run-tests.sh's own combos.json rule already treats "
                   "'mistral/mistral-code' as a free leg that must never "
                   "appear in a -clean combo ('*-clean = paid legs only: no "
                   "free pool may train on private prompts').",
    },
    "qwen/qwen3.8-27b": {
        "family": "qwen", "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "groq's vendor-prefixed spelling; true free tier (t3-driver-free-only).",
    },
    "qwen-3.8-27b": {
        "family": "qwen", "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "cerebras's bare spelling; docs/models.md: 'qwen3.8 free (groq) -> "
                   "cerebras credit overflow' - this is the PAID overflow leg, not a "
                   "free tier. " + _UNPRICED,
    },
    "minimax-m2.7": {
        "family": "minimax", "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "cheaperinference resale leg inside t3-driver (own billing, NOT a "
                   "free tier). " + _UNPRICED,
    },
    "command-a-03-2025": {
        "family": "cohere", "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "t4-rag head; tier-profiles.json calls its keys 'trial keys' - a "
                   "temporary free trial, not a published free tier. " + _UNPRICED,
    },
    "command-r-plus-08-2024": {
        "family": "cohere", "context_advertised": 131072, "output_max": 16384,
        "reasoning": False, "effort_ladder": [],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "t4-rag overflow leg; same trial-key note as command-a-03-2025. " + _UNPRICED,
    },
    "google/gemini-3.8-flash": {
        "family": "google", "context_advertised": 131072, "output_max": 32768,
        "reasoning": True, "effort_ladder": ["low", "medium", "high"],
        "price_in": 0.0, "price_out": 0.0,
        "comment": "openrouter's paid twin of the native gemini-3.8-flash free head "
                   "(docs/models.md: the bare 'openrouter/gemini-3.8-flash' spelling "
                   "400s - only this google-scoped spelling ships). " + _UNPRICED,
    },
}


def _context_usable(advertised: int) -> dict:
    return {"tokens": int(round(advertised * D8_USABLE_FRACTION)), "source": "default"}


def _model_from_llm_entry(entry: dict) -> dict:
    mid = entry["id"]
    extra = MODEL_EXTRA[mid]
    out = {
        "id": mid,
        "family": extra["family"],
        "context_advertised": entry["context"],
        "context_usable": _context_usable(entry["context"]),
        "output_max": entry["output"],
        "reasoning": bool(entry.get("reasoning", False)),
        "effort_ladder": list(extra["effort_ladder"]),
        "tool_calls": "unproven",
        "price_in": entry["input_price"],
        "price_out": entry["output_price"],
    }
    if "cache_read_price" in entry:
        out["price_cache_read"] = entry["cache_read_price"]
    if "paid_input_price" in entry:
        out["paid_price_in"] = entry["paid_input_price"]
    if "paid_output_price" in entry:
        out["paid_price_out"] = entry["paid_output_price"]
    if "name" in entry:
        out["display_name"] = entry["name"]
    if "default_for" in entry:
        out["default_for"] = entry["default_for"]
    if "direct" in entry:
        out["direct"] = dict(entry["direct"])
    elif "openrouter_id" in entry:
        out["direct"] = {"provider": "openrouter", "model": entry["openrouter_id"]}
    if extra.get("comment"):
        out["$comment"] = extra["comment"]
    return out


def build_models(llm_models_doc: dict) -> dict:
    models = {}
    for entry in llm_models_doc["models"]:
        model = _model_from_llm_entry(entry)
        models[model["id"]] = model

    for mid, extra in EXTRA_MODELS.items():
        model = {"id": mid, "tool_calls": "unproven"}
        model.update({k: v for k, v in extra.items() if k != "comment"})
        model["context_usable"] = _context_usable(extra["context_advertised"])
        if extra.get("comment"):
            model["$comment"] = extra["comment"]
        models[mid] = model

    return models


# ===========================================================================
# routes
# ===========================================================================

# routes.<id>.class (free/cheap/mid/frontier) has no field in the old files at all;
# assigned by role + pricing, not by the t1/t2/t3 naming (spec keeps class and role
# orthogonal - see docs/plans/2026-09-25-registry-mapping.md Open choices).
COMBO_CLASS = {
    "t1-orchestrator": "cheap", "t1-orchestrator-clean": "cheap",
    "t1-orchestrator-free-only": "free", "t1-orchestrator-paid": "cheap",
    "spark-1.3-contributor": "cheap",
    "t2-worker": "mid", "t2-worker-clean": "mid",
    "t2-worker-free-only": "free", "t2-worker-paid": "mid",
    "t2-orchestrator": "frontier",
    "t3-driver": "cheap", "t3-driver-clean": "cheap",
    "t3-driver-free-only": "free", "t3-driver-paid": "cheap",
    "t4-rag": "cheap",
    "gemini-3.8-flash": "cheap",
    "deepseek-v4.1-flash": "cheap",
    "opus-4-6": "frontier",
    "auto": "mid", "auto/smart": "mid", "auto/cheap": "cheap",
}

AUTO_IDS = frozenset({"auto", "auto/smart", "auto/cheap"})
DEFAULT_STRATEGY = "priority"

ROUTE_COMMENT = {
    "t1-orchestrator-paid": (
        "LiteLLM-only fallback chain with no OmniRoute equivalent "
        "(tools/sync-router-tiers.py SYNCED_TIERS comment). Its legs are hand-curated "
        "in configuration/litellm/config.yaml, which is not one of the old files this "
        "converter reads (out of scope for this migration) - legs stays empty until a "
        "later phase folds config.yaml in."
    ),
    "t2-worker-paid": (
        "LiteLLM-only, config.yaml out of scope for this migration - see "
        "routes.'t1-orchestrator-paid' for the full note. legs stays empty."
    ),
    "t3-driver-paid": (
        "LiteLLM-only, config.yaml out of scope for this migration - see "
        "routes.'t1-orchestrator-paid' for the full note. legs stays empty."
    ),
    "auto": (
        "OmniRoute's own built-in dynamic 'auto' strategy across every registered "
        "connection (catalog/ide-models.json: 'bootstrap: auto balanced (no combo "
        "needed)') - not a static combo, so it has no legs to enumerate."
    ),
    "auto/smart": "Dynamic strategy, no static legs - see routes.auto for the full note.",
    "auto/cheap": "Dynamic strategy, no static legs - see routes.auto for the full note.",
}


def _strip_openhands_profile(entry: dict) -> dict:
    out = {
        "max_input_tokens": entry["max_input_tokens"],
        "max_output_tokens": entry["max_output_tokens"],
        "reasoning": entry["reasoning"],
    }
    if entry.get("gateway"):
        out["gateway"] = entry["gateway"]
        if "base_url" in entry:
            out["base_url"] = entry["base_url"]
        if "model" in entry:
            out["model"] = entry["model"]
    return out


def _build_surfaces(ide_entry: dict, combo, tier_index: dict) -> dict:
    surfaces = {}
    for gateway, clients in ide_entry.get("surfaces", {}).items():
        surf = {
            "clients": list(clients),
            "display_name": ide_entry["name"],
            "context": ide_entry["context"],
            "output": ide_entry["output"],
        }
        if ide_entry.get("reasoning_effort"):
            surf["effort_default"] = ide_entry["reasoning_effort"]
        if gateway == "omniroute" and combo is not None:
            # combos.json's own coarse display string ("1M"/"128k"/"200k") - a picker
            # convention (docs/models.md), not the model's real window.
            surf["context_declared"] = combo["context"]
        profile_id = "%s-%s" % (gateway, ide_entry["id"])
        if profile_id in tier_index:
            surf["openhands_profile"] = _strip_openhands_profile(tier_index[profile_id])
        surfaces[gateway] = surf
    return surfaces


def build_routes(combos_doc: dict, ide_models_doc: dict, tier_profiles_doc: dict) -> dict:
    combos_by_name = {c["name"]: c for c in combos_doc["combos"]}
    tier_index = {t["id"]: t for t in tier_profiles_doc["tiers"]}

    routes = {}
    for ide_entry in ide_models_doc["models"]:
        rid = ide_entry["id"]
        combo = combos_by_name.get(rid)
        legs = list(combo["models"]) if combo else []
        if rid in AUTO_IDS:
            strategy = "auto"
        elif combo is not None:
            strategy = combo["strategy"]
        else:
            strategy = DEFAULT_STRATEGY

        route = {
            "id": rid,
            "class": COMBO_CLASS[rid],
            "strategy": strategy,
            "legs": legs,
            "surfaces": _build_surfaces(ide_entry, combo, tier_index),
        }
        if rid in ROUTE_COMMENT:
            route["$comment"] = ROUTE_COMMENT[rid]
        if rid == "spark-1.3-contributor":
            # An OpenHands-only profile with no ide-models.json surface counterpart:
            # direct OpenRouter access (full effort ladder, needs its own key) -
            # tier-profiles.json's one entry with an explicit "gateway".
            direct = tier_index["openrouter-muse-spark-1.3-contributor"]
            route["surfaces"]["openhands"] = {
                "direct_profile": _strip_openhands_profile(direct),
            }
        routes[rid] = route

    return routes


# ===========================================================================
# clients
# ===========================================================================

# gateway_mode/skills_dir/supports_effort have no field in tools/autoos_clients.py at
# all (that module is about building a CLI invocation, not the registry's shape).
# gateway_mode: "native" for opencode (its own opencode.jsonc provider config talks
# to OmniRoute directly - autoos_clients.py builds its argv separately from the
# other gateway clients); "omniroute-run" for the three clients autoos_clients.py
# wraps with the `omniroute run <client> --` CLI; "none" for the three clients that
# use their own account/subscription, never the gateway.
CLIENT_EXTRA = {
    "opencode": {
        "gateway_mode": "native", "skills_dir": ".agents/skills",
        "supports_effort": True, "signin_check": "env:AUTOOS_OMNIROUTE_KEY",
    },
    "claude": {
        "gateway_mode": "none", "skills_dir": ".claude/skills",
        "supports_effort": False, "signin_check": None,
        "comment": "No signin probe exists in tools/autoos_clients.py yet (auth is "
                   "interactive 'claude login'); signin_check stays null until one does. "
                   "supports_effort=false: no effort/reasoning flag in CLIENTS['claude'].modes.",
    },
    "qwen": {
        "gateway_mode": "omniroute-run", "skills_dir": None,
        "supports_effort": True, "signin_check": "env:AUTOOS_OMNIROUTE_KEY",
        "comment": "skills_dir unmeasured: plan.md Phase C5 spikes each client's native "
                   "skills directory; not done as of this migration.",
    },
    "gemini": {
        "gateway_mode": "omniroute-run", "skills_dir": None,
        "supports_effort": True, "signin_check": "env:AUTOOS_OMNIROUTE_KEY",
        "comment": "skills_dir unmeasured - see clients.qwen's comment.",
    },
    "codex": {
        "gateway_mode": "omniroute-run", "skills_dir": None,
        "supports_effort": True, "signin_check": "env:AUTOOS_OMNIROUTE_KEY",
        "comment": "skills_dir unmeasured - see clients.qwen's comment.",
    },
    "agy": {
        "gateway_mode": "none", "skills_dir": None,
        "supports_effort": False, "signin_check": None,
        "comment": "skills_dir unmeasured - see clients.qwen's comment. signin_check is "
                   "filled from tools/autoos_clients.py's own signin_probe below, not "
                   "this table.",
    },
    "qoder": {
        "gateway_mode": "none", "skills_dir": None,
        "supports_effort": False, "signin_check": None,
        "comment": "Own-account auth (qodercli login) but tools/autoos_clients.py "
                   "defines no signin_probe for it (only agy has one) - a real gap, not "
                   "fixed by this migration. skills_dir unmeasured - see clients.qwen's "
                   "comment.",
    },
}


def build_clients() -> dict:
    clients = {}
    for name, client in autoos_clients.CLIENTS.items():
        extra = CLIENT_EXTRA[name]
        signin_check = extra["signin_check"]
        if client.signin_probe:
            signin_check = "%s %s" % (client.binary, " ".join(client.signin_probe))
        entry = {
            "id": name,
            "binary": client.binary,
            "gateway_mode": extra["gateway_mode"],
            "skills_dir": extra["skills_dir"],
            "supports_effort": extra["supports_effort"],
            "signin_check": signin_check,
        }
        if extra.get("comment"):
            entry["$comment"] = extra["comment"]
        clients[name] = entry
    return clients


# ===========================================================================
# policy
# ===========================================================================

def _sn(value, source):
    return {"value": value, "source": source}


def _bp(alpha, beta, source):
    return {"alpha": alpha, "beta": beta, "source": source}


def _bucket_table_policy() -> dict:
    """policy.bucket_table = {source, table}; `table` is the exact JSON mirror of
    tools/autoos_resolver.DEFAULT_BUCKET_TABLE, wrapped once so the whole table
    carries one D20 source tag without breaking the resolver-parity test (D20 wants
    a source on every policy number; the bucket table is pinned data the resolver
    loads directly, so it cannot also carry a per-value wrapper - see the mapping
    doc's Open choices). JSON has no boolean keys: `tests` uses the strings
    "true"/"false" - a loader must convert them back to real booleans before calling
    autoos_resolver.points()/bucket() with this table.
    """
    table = autoos_resolver.DEFAULT_BUCKET_TABLE
    return {
        "source": "default",
        "table": {
            "features": {
                name: [dict(band) for band in bands]
                for name, bands in table["features"].items()
            },
            "spec": dict(table["spec"]),
            "tests": {"true": table["tests"][True], "false": table["tests"][False]},
            "kind": dict(table["kind"]),
            "buckets": [dict(b) for b in table["buckets"]],
        },
    }


def build_policy() -> dict:
    return {
        "modes": {
            "cost-first": {"theta": _sn(0.60, "default"), "lambda": _sn(0.0, "default")},
            "balanced": {"theta": _sn(0.80, "default"), "lambda": _sn(0.01, "default")},
            "quality-first": {"theta": _sn(0.95, "default"), "lambda": _sn(0.05, "default")},
        },
        "bucket_table": _bucket_table_policy(),
        "effort_rules": {
            "rows": [
                {"condition": "leg is not a reasoning model", "effort": None},
                {"condition": "kind in (plan, debug)", "effort": "high"},
                {"condition": "bucket == S4 and class == frontier", "effort": "top_rung"},
                {"condition": "bucket in (S3, S4)", "effort": "high"},
                {"condition": "bucket in (S1, S2)", "effort": "medium"},
                {"condition": "bucket == S0 or kind == bulk", "effort": "low"},
            ],
            "max_tokens_floor": {
                "default": _sn(48000, "default"),
                "high_and_above": _sn(64000, "default"),
            },
        },
        "handoff_caps": {
            "claude-opus-1m": {"cap_tokens": 400000, "cap_fraction": 0.40, "source": "2026-09-25"},
            "muse-spark-1m": {"cap_tokens": 300000, "cap_fraction": 0.30, "source": "2026-09-25"},
            "gemini-1m": {"cap_tokens": 200000, "cap_fraction": 0.20, "source": "2026-09-25"},
            "200k-class": {"cap_tokens": 150000, "cap_fraction": 0.75, "source": "2026-09-25"},
        },
        "risk_rules": [
            {"type": "path_glob", "pattern": "configuration/api-keys.yml",
             "reason": "secrets handling", "source": "default"},
            {"type": "path_glob", "pattern": "configuration/**/*.env",
             "reason": "secrets handling", "source": "default"},
            {"type": "path_glob", "pattern": "**/*.vault.yml",
             "reason": "secrets handling", "source": "default"},
            {"type": "path_glob", "pattern": "setup.sh",
             "reason": "user PATH/profile/config writers", "source": "default"},
            {"type": "path_glob", "pattern": "setup.ps1",
             "reason": "user PATH/profile/config writers", "source": "default"},
            {"type": "path_glob", "pattern": "lib/**/*.psm1",
             "reason": "user PATH/profile/config writers", "source": "default"},
            {"type": "path_glob", "pattern": "lib/**/*.sh",
             "reason": "user PATH/profile/config writers", "source": "default"},
            {"type": "diff_deletion", "reason": "deletion paths", "source": "default"},
            {"type": "path_glob", "pattern": ".github/workflows/**",
             "reason": "CI workflows", "source": "default"},
            {"type": "path_glob", "pattern": "**/*settings*.json",
             "reason": "security settings", "source": "default"},
        ],
        "seed_priors": {
            "free": {b: (_bp(2, 1, "2026-09-25") if b in ("S0", "S1") else _bp(1, 5, "2026-09-25"))
                     for b in BUCKETS},
            "cheap": {b: (_bp(2, 1, "2026-09-25") if b in ("S0", "S1") else _bp(1, 5, "2026-09-25"))
                      for b in BUCKETS},
            "mid": {b: _bp(1, 1, "default") for b in BUCKETS},
            "frontier": {b: _bp(1, 1, "default") for b in BUCKETS},
        },
        "latency_seed": {
            "free": {"minutes": 5, "source": "default"},
            "cheap": {"minutes": 8, "source": "default"},
            "mid": {"minutes": 15, "source": "default"},
            "frontier": {"minutes": 30, "source": "default"},
        },
        "verify_tokens": {
            b: {"tokens": t, "source": "default"}
            for b, t in zip(BUCKETS, (2000, 4000, 8000, 16000, 32000))
        },
        "brief_tokens": {
            b: {"tokens": t, "source": "default"}
            for b, t in zip(BUCKETS, (500, 800, 1200, 2000, 3000))
        },
    }


# ===========================================================================
# main
# ===========================================================================

def build_registry() -> dict:
    llm_models_doc = _load(LLM_MODELS_PATH)
    providers_doc = _load(PROVIDERS_PATH)
    ide_models_doc = _load(IDE_MODELS_PATH)
    combos_doc = _load(COMBOS_PATH)
    tier_profiles_doc = _load(TIER_PROFILES_PATH)
    provider_windows_doc = _load(PROVIDER_WINDOWS_PATH)

    registry = {
        "version": REGISTRY_VERSION,
        "providers": build_providers(providers_doc, provider_windows_doc),
        "models": build_models(llm_models_doc),
        "routes": build_routes(combos_doc, ide_models_doc, tier_profiles_doc),
        "clients": build_clients(),
        "policy": build_policy(),
    }
    mark_openrouter_unavailable(registry)
    mark_legs_unavailable(registry)
    return registry


def render(registry: dict) -> str:
    return json.dumps(registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                         help="change nothing; exit 1 if a fresh render would differ")
    args = parser.parse_args(argv)

    text = render(build_registry())

    if args.check:
        current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else None
        if current != text:
            sys.stderr.write("%s is stale; run: python3 tools/registry-convert.py\n" % OUTPUT_PATH)
            return 1
        return 0

    OUTPUT_PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
