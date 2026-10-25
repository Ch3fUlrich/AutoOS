#!/usr/bin/env python3
"""AutoOS output token budget and ceiling helpers (D-2694).

Operator decision D-2694: the default max_tokens for every L3 writer and
review-seat call is 128000. Low fixed caps (e.g. 16000) silently eat answers
when reasoning models consume tens of thousands of tokens on hidden thinking
(observed on W6b: mimo-v2.6-pro burned 32006 reasoning tokens and produced 0 output).

Clamping is per-leg ONLY to the model's real output ceiling:
`catalog/ai-registry.json`'s `output_max` for the leg/model.
Unknown output_max defaults to 128000 with a stderr warning.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

DEFAULT_MAX_TOKENS = 128000
REGISTRY_RELPATH = Path("catalog") / "ai-registry.json"
_registry_cache: dict | None = None


def _registry_models(registry: dict | None = None) -> dict:
    """Return the models table from catalog/ai-registry.json (cached) or registry.

    A caller-supplied registry dict is a test seam: one with no (or a junk)
    `models` table means "output_max unknown", which resolve_output_ceiling
    reports as None - never a crash and never a lower cap (D-2694).
    """
    global _registry_cache
    if registry is not None:
        models = registry.get("models") if isinstance(registry, dict) else None
        return models if isinstance(models, dict) else {}
    if _registry_cache is None:
        try:
            path = Path(__file__).resolve().parent.parent / REGISTRY_RELPATH
            doc = json.loads(path.read_text(encoding="utf-8"))
            _registry_cache = doc.get("models") or {}
        except (OSError, ValueError):
            _registry_cache = {}
    return _registry_cache


def resolve_output_ceiling(model_id: str, registry: dict | None = None) -> int | None:
    """Resolve the model's output_max ceiling from catalog/ai-registry.json.

    Returns the positive integer output_max or None if unknown/missing.
    """
    if not isinstance(model_id, str) or not model_id:
        return None
    models = _registry_models(registry)
    # 1. Exact match
    entry = models.get(model_id)
    # 2. Match without provider prefix
    if entry is None and "/" in model_id:
        entry = models.get(model_id.rsplit("/", 1)[-1])
    # 3. Case-insensitive match
    if entry is None:
        lower = model_id.lower()
        sub = model_id.rsplit("/", 1)[-1].lower()
        for k, v in models.items():
            kl = k.lower()
            if kl == lower or kl == sub:
                entry = v
                break
    if isinstance(entry, dict):
        out = entry.get("output_max")
        if isinstance(out, int) and not isinstance(out, bool) and out > 0:
            return out
    return None


def clamp_max_tokens(model_id: str, requested: int | None = None,
                     registry: dict | None = None) -> int:
    """Clamp requested max_tokens to the model's real output ceiling (D-2694).

    Defaults to DEFAULT_MAX_TOKENS (128000). Clamps only to the model's
    output_max ceiling when ceiling is smaller. Does not clamp below the ceiling.
    If output_max is unknown, defaults to 128000 and logs to stderr.
    """
    if requested is None:
        requested = DEFAULT_MAX_TOKENS
    ceiling = resolve_output_ceiling(model_id, registry=registry)
    if ceiling is None:
        print("autoos: unknown output_max for %s; sending default %d"
              % (model_id, DEFAULT_MAX_TOKENS), file=sys.stderr)
        return requested
    return min(requested, ceiling)
