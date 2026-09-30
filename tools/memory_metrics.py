#!/usr/bin/env python3
"""7 bake-off metrics scaffold (spec §9 table).

Each metric has: name, definition, unit, one-line "why", how it will be measured,
and status `not_measured`. In build-only mode these are skeletons — real
measurement happens in the engine phase after the P1 host gate.

Structural note: this file is the single source of truth for metric definitions.
The bake-off brief (docs/plans/2026-09-30-memspec-bakeoff-brief.md) references
these names verbatim.
"""

# spec §9: 7 metrics for the MEMSPEC bake-off
METRICS = [
    {
        "name": "answer_correctness",
        "definition": "Fraction of recall queries where the expected answer entity id appears in the top-k results.",
        "unit": "ratio (0–1)",
        "why": "validates that the facade read path returns correct facts for given queries",
        "how": "run all 30 bake-off questions via recall; count how many have the expected answer id in the returned lines or entity mode",
        "status": "not_measured",
    },
    {
        "name": "tokens_per_recall",
        "definition": "Token count of the compact lines returned by a single recall call.",
        "unit": "tokens",
        "why": "enforces the spec:64–65 budget of ≤600 tokens per recall response",
        "how": "call recall for each of the 30 questions; record estimate_tokens of the lines array; verify ≤600",
        "status": "not_measured",
    },
    {
        "name": "write_then_visible_latency",
        "definition": "Wall-clock time from a remember() call returning successfully to the new entity being reachable via recall under the same project.",
        "unit": "seconds",
        "why": "measure §5 write latency; how quickly a written fact becomes readable",
        "how": "time remember→recall round-trip for each of 30 questions; p95 < 5s target",
        "status": "not_measured",
    },
    {
        "name": "duplicate_rate_after_week",
        "definition": "Fraction of canonical ids that would be duplicates after a week of continuous writes (same title, varying body).",
        "unit": "ratio (0–1)",
        "why": "tests the alias ladder and merge behaviour under sustained write load",
        "how": "not measurable in build-only stub mode; requires real engine running over time",
        "status": "not_measured",
    },
    {
        "name": "p95_context_pack_latency",
        "definition": "p95 token-counted latency of context_pack() calls across all 30 questions' projects.",
        "unit": "tokens (estimated via estimate_tokens)",
        "why": "ensures session-start bundle stays within the 1.5k token budget (spec:90)",
        "how": "call context_pack for each project used in the 30 Qs; measure tokens; compute p95; verify ≤1500",
        "status": "not_measured",
    },
    {
        "name": "ram_fit_machine",
        "definition": "Whether the full graph (nodes + edges + alias) fits within the target machine's RAM limit.",
        "unit": "bool (fits / does_not_fit)",
        "why": "production deployments must not OOM; graph must fit on the target machine",
        "how": "count total bytes of node bodies + edge data + alias mappings; compare to machine RAM limit",
        "status": "not_measured",
    },
    {
        "name": "ops_incidents",
        "definition": "Count of restart/stuck-index/page-out incidents during a bake-off run.",
        "unit": "integer count",
        "why": "operational stability: restarts, failed indexes, or 3am-page-out events degrade confidence",
        "how": "not measurable in build-only stub mode; requires real engine deployment with monitoring",
        "status": "not_measured",
    },
]