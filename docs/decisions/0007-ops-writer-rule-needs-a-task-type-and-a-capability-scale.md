# 0007 — The `task_type=ops` writer rule stays unimplemented: the resolver has no task type, no R-scale, and no capability figure

Status: proposed, **not implemented** · 2026-10-08 · evidence: facts re-verified against the code at this branch

> Number note: the `unattended-orchestration` skill's "ADR 0007
> (`0007-herdr-as-unattended-backend.md`)" pointer is the **upstream agent-skills**
> sequence, whose 0001–0005 titles differ from this directory's files too. This record
> takes 0007 as this repo's own next number; the skill pointers are stale in both
> directions and are not changed here.

## Context

`writer-ops.md` §1 (the ops-writer proposal, fed into spec `combo-v2.md` §8 as
AO-DENYLEGS open item 3) asks the resolver to route on:

- `task_type = ops` (Ansible/YAML/compose/systemd/playbook; anything touching secrets,
  mail, cron or production hosts) is **≥ R2**;
- an R2 ops writer runs the **qoder** client's `qwen3.8-max` first, then
  `vertex/gemini-3.8-flash` at the high rung;
- free sub-40 models (`nemotron-3-super`, `laguna`, `north-mini-code`, `gpt-oss`) are
  allowed at **R0–R1 only**;
- R3 work adds all four review lenses, and a model with ≥ 2 rejections in 7 days on a
  task_type leaves that task_type's writer chain.

The lane was told: add it if it is not already in the data, otherwise write the gap
down. It is not in the data, and it cannot be added by editing data alone.

## Facts, verified 2026-10-08 on this branch

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 | the resolver reads a `task_type` card field | **refuted**: the card vocabulary is `_CARD_FIELDS = ("spec", "kind")`; v2 kinds are `implement/debug/review/plan/bulk/research/final`, v1 adds only `role/complexity/ctx/spend` | `tools/autoos_resolver.py:80`, `tools/autoos_routing.py` `CARD_V2_VALUES`/`CARD_V1_ONLY` |
| F2 | there is an `R0–R4` scale to gate on | **refuted**: the buckets are *size* buckets `S0–S4`, computed from added lines plus brief/verify tokens against `policy.bucket_table` | `tools/autoos_resolver.py:69-73`, `policy.bucket_table` in `catalog/ai-registry.json` |
| F3 | `risk` is a pre-spawn capability judgement | **refuted**: `risk` is `normal|high`, derived from the **diff after the work** (`classify(files, added_lines, registry, …)`), and the audit samples already-written work | `tools/autoos_risk.py:420`, `policy.risk_rules`, `policy.risk_audit_percent` |
| F4 | the registry carries a per-model intelligence figure (the "II 45 / II 41" the rule quotes) | **refuted**: `models.<id>` has `reasoning`, `tool_calls`, `context_*`, `price_*`, `tier` (free/paid/subscription) — no capability number, and no `tier: trial` either | `catalog/ai-registry.json` `models`, schema checks in `tools/registry.py` |
| F5 | a writer list exists the way a reviewer list does | **refuted**: `policy` has `reviewers`, no `writers` key | `policy` keys in `catalog/ai-registry.json` |
| F6 | a chain can name a qoder seat as a leg | **refuted**: `qoder` is a **spawner client**, and the schema refuses a `leg` on a row for a spawner-only client; such a row is reached by `reviewer_for`/`--client`, never by walking `routes.<id>.legs` | `tools/registry.py` reviewers schema check, `policy.reviewers` rows that carry `client: qoder` with no `leg` |
| F7 | the sub-40 free models are identifiable by price | **partly**: they are identifiable as *free* legs, and `policy.free_client_models` names the clients' own free rows — but "sub-40" is a capability cut, and F4 means there is no capability data to cut on | `policy.free_client_models`, the `:free` leg spellings in `configuration/omniroute/combos.json` |

## Decision

**Do not implement the rule in this lane.** Every clause of it needs an input the
resolver does not have: a task *type* (F1), a capability *scale* decided before the run
(F2, F3), and a capability *figure* per model (F4, F7). Adding them is three schema
changes, a new pre-spawn judgement, and a writer list object that does not exist — a
spec item, not an open item. A data-only workaround (mapping "ops" onto `kind=implement`
and R2 onto `S3`) would re-declare existing cards under new names and make the routing
table lie about what it keys on.

What the lane *did* do with the parts that are expressible today:

- the qoder `Qwen3.8-Max` seat that open item 2 needed as a reviewer carrier is declared
  as a `policy.reviewers` row with no `leg` (the shape F6 allows), so a run can reach it
  on the last-resort pass;
- the free sub-40 rows stay where they already were — free legs are never promoted into
  an L3/credit chain by these rules, and `l2-orchestrator`/`l1-orchestrator` carry the
  ≥ 600k seats.

## What it would take (the follow-up, sized)

1. `task_type` as a real card field (`ops|code|docs|infra|…`), validated in
   `CARD_V2_VALUES`, defaulted from `paths` only as a *hint*, never as a verdict.
2. A capability number per model row (the II the spec quotes), with `price_source`-style
   provenance and a probe that produces it — otherwise F4 just moves the gap.
3. A pre-spawn `R0–R4` judgement, separate from `S0–S4` (size) and from the diff-derived
   `normal|high` risk (F3): ops work is R2 *before* anyone edits a file.
4. `policy.writers` keyed by `task_type` × R, shaped like `policy.reviewers` (F5), with
   the client-vs-leg rule of F6 respected: a spawner-client seat is named by client, not
   chained as a gateway leg.
5. The ledger half of the proposal (≥ 2 rejections in 7 days ⇒ demotion) needs the
   writer's served model recorded per run — the run records exist, so this is the part
   that could be built first.

## Consequences

- The ops rule is **not** enforced by anything in this repository. An ops card routed
  today gets the same `kind`/size-based chain as any other implement card; the guard
  against a weak writer on ops work is the brief template (`writer-ops.md` §2) and the
  sandbox linters (§4), not the router.
- The gap is stated where an operator will read it: this record, the dated
  `$comment`s in `catalog/ai-registry.json`, and `CHANGELOG.md`. The lane also wrote
  `logs/briefs/denylegs-gaps.md` — a gitignored host log, so it is not the tracked
  statement of record; this file is.
- A future lane that implements 1–4 should supersede this record rather than argue with
  it: the facts above are about the data as it stands on 2026-10-08.
