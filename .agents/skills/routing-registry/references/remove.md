# REMOVE — retirement is a render problem, not a delete problem

Removing routing state is where silent drift comes from: the registry and the
renders disagree, a retired id gets merged back, or a dead leg keeps its gate.
Three shapes, and for each one the same closing move: **every consumer
re-rendered and every removal reasoned and dated.**

## 1. Retire a combo (route)

Precedent: `OVHCODER-DROP` 2026-10-08, `DIRETIRED` 2026-10-13.

1. Delete `routes.<id>` from `catalog/ai-registry.json` and move the id into
   `OMNIROUTE_RETIRED_IDS` in `tools/registry.py` — that is what makes apply
   prune the live combo and what renders the id under `combos.json` `retired`.
2. Decide the model row's fate:
   - **Upstream withdrew the model** (live 404): keep the model row for pricing
     history with a dated `unavailable` note in its `$comment`; the row is
     declared, not servable.
   - **Provider/feature retired entirely**: delete the rows too (precedent:
     `DIRETIRED` deleted provider, model rows and the opencode pin together).
3. Remove client pins and hand entries — an opencode pin or an `opencode.jsonc`
   hand entry naming the dead id is a consumer, not a comment.
4. Re-render every consumer and check:
   `python tools/registry.py render omniroute --check`, `render litellm|ide|
   openhands|models-doc --check`, `python tools/sync-ide-models.py --check`.
5. Never merge a retired id back. The tests pin retirements as "retired, not
   merged" — a route resurrected by a lane merge is exactly the defect those
   pins exist to catch (`OVHCODER-DROP-RESTORE` 2026-10-08). If the id must
   return, it returns as a NEW dated decision on top of the retirement.

Note the set semantics: `omitted` = declared but deliberately rendering no
combo; `retired` = gone, id reserved. A deleted route belongs to **no** set.

## 2. Remove a leg from a route

Two different removals — do not conflate them:

- **Measured dead** (upstream 404, provider withdrew it): the leg leaves
  `routes.<id>.legs` **and** its `unavailable_legs` gate together. There is
  nothing left to gate (precedent: the OVH coder legs left `l2-worker`/`l3-driver`
  legs *and* gates in one move).
- **Declared but unservable** (no credits, policy gate): the leg stays
  declared and stays gated in `routes.<id>.unavailable_legs`, so the route
  reads `omitted`, not legless (precedent: `l1-orchestrator-clean`'s gated paid
  leg). `providers.<id>.available` reflects the **provider**, not the model.

Either way: dated reason in the route's `$comment` (decision id) and a ledger
record — `python tools/probe-ledger.py record --leg <provider/model> --status
failed --reason <text>` — and re-render every surface (shape 1, step 4).

## 3. Remove or suspend a provider

1. Its legs leave **every** route first (grep the registry for the provider
   prefix; routes and `unavailable_legs` both).
2. Then either delete the provider row (and its models, pins and hand entries)
   or mark it `available: false` for a suspension — with a dated revisit note.
3. **Never** set `available: false` to gate individual models when the provider
   has servable ones: that drops the servable ones too. The openrouter rule is
   the standing example — paid legs are gated per-leg while the provider stays
   `available` so the `:free` legs keep routing.

## 4. Close every removal the same way

- [ ] Reason + date in the `$comment` (the decision id is the citation).
- [ ] Ledger record for any leg that moved (`probe-ledger.py record`).
- [ ] All five render surfaces + `sync-ide-models.py --check` green.
- [ ] `python tools/combo-contract.py`, `python tools/registry.py check`,
      `validate`, `python tools/audit-router.py --offline` green.
- [ ] `bash configuration/omniroute/apply.sh --dry-run` shows the pruning you
      expect; then GO and apply (`--go <ref> --go-sha <sha>`) so the live combo
      is pruned too — see [add-modify](add-modify.md#go).
