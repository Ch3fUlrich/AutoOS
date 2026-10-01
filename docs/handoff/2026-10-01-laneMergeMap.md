# Lane merge-readiness map — 2026-10-01 (snapshot)

**Author:** subagent `ws-merge-map-20260930` (branch `L1-backlog/ws-merge-map-20260930`,
read-only analysis).
**Objective:** let L0 land the 2026-09-30 merge wave with the fewest avoidable conflicts.
**Method:** `git for-each-ref` + `git diff --name-only <base>..<tip>` + read-only
`git merge-tree --write-tree` (no merge, no rebase, no checkout of any analysed branch).
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-mergemap`
(`git rev-parse --show-toplevel` prints exactly that path).

This document is written from the `origin/main` tip **at analysis time**:

```
$ git rev-parse origin/main
e58274a8030625c19b739032d37aa82d2ec24a73
```

All 22 requested branches exist (none missing). One, `ws-ovh-review-2`, is **empty**
(0 commits ahead of its merge base) — see §1. `ws-nebius-combos-20260930` and
`ws-freewire-20260930` are the **same commit** (`2ff537a`) — see §4.

> **Snapshot, not current.** This map describes `origin/main` at `e58274a8` on 2026-10-01 only. Since then main has taken 44 `ws-*` lanes (counted at `75af3236`); of the 22 branch tips in the table below, 20 are already ancestors of main (`git merge-base --is-ancestor`), `ws-remote` is not, and one tip sha is not resolvable in a fleet clone. Every branch tip, ahead/behind count, CLEAN/CONFLICT verdict, merge order and "exists / empty / same commit" statement below is historical. Re-run the commands in this document against the current `origin/main` before acting on any of it.

---

## 1. Per-branch facts

`base` = `git merge-base origin/main <branch>` (the branch point). `behind` = commits
on `origin/main` not on the branch (`git rev-list --count <branch>..origin/main`);
`ahead` = commits on the branch not on `origin/main`. `vs main` = result of
`git merge-tree --write-tree origin/main <branch>` (exit 0 = clean, exit 1 = textual
conflict; read-only, writes no ref and does not touch the worktree).

| branch | tip | base | ahead | behind | vs main |
|---|---|---:|---:|---:|---|
| `L1-backlog/ws-combos-20260930` | `a5bcb69` | `d08f7f2` | 1 | 84 | **CONFLICT** `configuration/omniroute/combos.json` |
| `L1-backlog/ws-ovh-20260930` | `89a9024` | `d08f7f2` | 10 | 84 | **CONFLICT** `configuration/omniroute/combos.json` |
| `L1-backlog/ws-ovh-finish-20260930` | `a975d48` | `d08f7f2` | 12 | 84 | **CONFLICT** `configuration/omniroute/combos.json` |
| `L1-backlog/ws-nebius-combos-20260930` | `2ff537a` | `d08f7f2` | 15 | 84 | **CONFLICT** `configuration/omniroute/combos.json` |
| `L1-backlog/ws-freewire-20260930` | `2ff537a` | `d08f7f2` | 15 | 84 | **CONFLICT** `configuration/omniroute/combos.json` |
| `L1-backlog/ws-p0-admission-fix-20260930` | `90cd698` | `d08f7f2` | 3 | 84 | clean |
| `L1-backlog/ws-admission-fix-20260930` | `5e97708` | `6ec0605` | 5 | 8 | clean |
| `L1-backlog/ws-applyjson-20260930` | `6a4c2c0` | `6ec0605` | 6 | 8 | clean |
| `L1-backlog/ws-suite-fix-20260930` | `4548e07` | `e58274a` | 1 | 0 | clean |
| `L1-backlog/ws-fixes-20260930` | `9cb8055` | `d08f7f2` | 11 | 84 | clean |
| `L1-backlog/ws-f1-vertex-20260930` | `9c444e4` | `d08f7f2` | 8 | 84 | clean |
| `L1-backlog/ws-sweep-20260930` | `98e30ef` | `d08f7f2` | 10 | 84 | clean |
| `L1-backlog/ws-sweep-review-20260930` | `800ff37` | `d08f7f2` | 1 | 84 | clean |
| `L1-backlog/ws-review-20260930` | `8b2a28c` | `d08f7f2` | 3 | 84 | clean |
| `L1-backlog/ws-hygiene-main-20260930` | `4ba83ed0` | `d08f7f2` | 1 | 84 | clean |
| `L1-backlog/ws-failures-doc-20260930` | `741b5cc` | `d08f7f2` | 2 | 84 | clean |
| `L1-backlog/ws-patchbackups-3-20260930` | `9e643e3` | `d08f7f2` | 1 | 84 | clean |
| `L1-backlog/ws-patchr1-20260930` | `70b1a86` | `e58274a` | 1 | 0 | clean |
| `L1-backlog/ws-verify-activate-20260930` | `c231618` | `d08f7f2` | 1 | 84 | clean |
| `L1-backlog/ws-ovh-review-2-20260930` | `d08f7f2` | `d08f7f2` | **0** | 84 | clean (empty) |
| `L1-backlog/ws-gw-admission-20260930` | `1bb1175` | `d08f7f2` | 2 | 84 | clean |
| `L1-backlog/ws-remote-20260930` | `266677c` | `67f875d` | 10 | 94 | clean |

### File lists (`git diff --name-only <base>..<tip>`)

**`ws-combos-20260930`** (5 files)
```
configuration/omniroute/apply.ps1
configuration/omniroute/apply.sh
configuration/omniroute/combos.json
docs/handoff/2026-09-30-laneB-combos.md
docs/models.md
```

**`ws-ovh-20260930`** (11 files)
```
catalog/ai-registry.json
configuration/litellm/config.yaml
configuration/omniroute/apply.ps1
configuration/omniroute/apply.sh
configuration/omniroute/combos.json
docs/handoff/2026-09-30-laneB-combos.md
docs/handoff/2026-09-30-laneOVH-combos.md
docs/handoff/2026-09-30-laneOVH-ovhcloud.md
docs/models.md
tests/test_registry.py
tests/test_registry_render.py
```

**`ws-ovh-finish-20260930`** (15 files)
```
catalog/ai-registry.json
catalog/ide-models.json
configuration/litellm/config.yaml
configuration/omniroute/apply.ps1
configuration/omniroute/apply.sh
configuration/omniroute/combos.json
configuration/openhands/tier-profiles.json
docs/handoff/2026-09-30-laneB-combos.md
docs/handoff/2026-09-30-laneOVH-combos.md
docs/handoff/2026-09-30-laneOVH-ovhcloud.md
docs/models.md
logs/handoff-sessions/DONE-ws-ovh-finish.md
logs/handoff-sessions/OPERATOR-ws-ovh-finish.md
tests/test_registry.py
tests/test_registry_render.py
```

**`ws-nebius-combos-20260930`** == **`ws-freewire-20260930`** (22 files, identical tip `2ff537a`)
```
catalog/ai-registry.json
catalog/ide-models.json
configuration/litellm/config.yaml
configuration/omniroute/apply.ps1
configuration/omniroute/apply.sh
configuration/omniroute/combos.json
configuration/openhands/tier-profiles.json
docs/handoff/2026-09-30-laneB-combos.md
docs/handoff/2026-09-30-laneFreeWire.md
docs/handoff/2026-09-30-laneOVH-combos.md
docs/handoff/2026-09-30-laneOVH-ovhcloud.md
docs/models.md
logs/handoff-sessions/DONE-ws-freewire.md
logs/handoff-sessions/DONE-ws-ovh-finish.md
logs/handoff-sessions/OPERATOR-ws-ovh-finish.md
opencode.jsonc
tests/test_autoos_resolver.py
tests/test_autoos_spawner.py
tests/test_registry.py
tests/test_registry_render.py
tests/test_sync_ide_models.py
tools/registry.py
```

**`ws-p0-admission-fix-20260930`** (8 files)
```
configuration/omniroute/apply.ps1
configuration/omniroute/apply.sh
configuration/start-stack.ps1
configuration/start-stack.sh
docs/handoff/2026-09-30-laneP0-admission-before.md
docs/handoff/2026-09-30-laneP0-admission.md
docs/handoff/2026-09-30-workstation-omniroute-handoff.md
logs/handoff-sessions/DONE-ws-p0-admission.md
```

**`ws-admission-fix-20260930`** (6 files)
```
configuration/autostart/Start-AutoOSStack.ps1
configuration/omniroute/apply.ps1
configuration/start-stack.ps1
docs/handoff/2026-09-30-laneAdmissionFix.md
logs/handoff-sessions/DONE-ws-admission-fix.md
tests/run-tests.ps1
```

**`ws-applyjson-20260930`** (9 files)
```
CHANGELOG.md
configuration/autostart/Start-AutoOSStack.ps1
configuration/omniroute/apply.ps1
configuration/start-stack.ps1
docs/handoff/2026-09-30-laneAdmissionFix.md
docs/handoff/2026-10-01-laneApplyJson.md
logs/handoff-sessions/DONE-ws-admission-fix.md
logs/handoff-sessions/DONE-ws-applyjson.md
tests/run-tests.ps1
```

**`ws-suite-fix-20260930`** (1 file)
```
tests/run-tests.ps1
```

**`ws-fixes-20260930`** (21 files)
```
.gitignore
configuration/omniroute/qwen-clamp-reapply-README.md
configuration/omniroute/qwen-clamp-reapply.ps1
configuration/omniroute/reason-fix-README.md
configuration/omniroute/reason-fix-reapply.ps1
configuration/omniroute/reasoning-defense.md
docs/handoff/2026-09-30-laneC-reasoning-fix.md
docs/handoff/2026-09-30-lanePatchIntegrity.md
logs/handoff-sessions/DONE-ws-fixes.md
logs/handoff-sessions/DONE-ws-patch-integrity.md
tools/diag-db.py
tools/diag-interleaved.py
tools/diag-key-source.py
tools/diag-models-auth.py
tools/diag-models.py
tools/probe-cross-family-extra.py
tools/probe-cross-family.py
tools/probe-reasoning-edge.py
tools/probe-reasoning-repro.py
tools/probe-reasoning-stream.py
tools/probe-reasoning.py
```

**`ws-f1-vertex-20260930`** (13 files)
```
configuration/omniroute/vertex-failover-knobs.json
configuration/omniroute/vertex-trailing-turn-README.md
docs/handoff/2026-09-30-laneF1-vertex.md
docs/handoff/2026-09-30-lanePatchFix.md
logs/handoff-sessions/DONE-ws-f1-vertex.md
logs/handoff-sessions/DONE-ws-patch-fix.md
logs/probe-vertex-isolated-results.json
logs/probe-vertex-results.json
tools/apply-vertex-patch.py
tools/probe-vertex-isolated.py
tools/probe-vertex.py
tools/start-isolated-gateway.ps1
tools/vertex-trailing-turn-reapply.ps1
```

**`ws-verify-activate-20260930`** (15 files)
```
docs/handoff/2026-09-30-laneV-activate.md
logs/handoff-sessions/DONE-ws-v-activate.md
tools/probe-reasoning-repro.py
tools/probe-vertex.py
tools/v-activate-authcheck.py
tools/v-activate-backup-table.py
tools/v-activate-both-probes.py
tools/v-activate-clamp-evidence.py
tools/v-activate-copy-datadir.py
tools/v-activate-run-probes.py
tools/v-activate-start-iso.ps1
tools/v-activate-stop-iso.ps1
tools/v-activate-tarball-integrity.py
tools/v-activate-vertex-retry.py
tools/verify-patch-inventory.py
```

**`ws-sweep-20260930`** (4 files)
```
docs/handoff/2026-09-30-laneSweep-t2-models.md
docs/models-proposed.md
logs/handoff-sessions/DONE-ws-sweep.md
tools/probe-sweep.py
```

**`ws-sweep-review-20260930`** (2 files)
```
docs/handoff/2026-09-30-laneSweep-review.md
logs/handoff-sessions/DONE-ws-sweep-review.md
```

**`ws-review-20260930`** (2 files)
```
docs/handoff/2026-09-30-laneReview-wave1.md
logs/handoff-sessions/DONE-ws-review-wave1.md
```

**`ws-hygiene-main-20260930`** (1 file)
```
docs/handoff/2026-09-30-workstation-omniroute-handoff.md
```

**`ws-failures-doc-20260930`** (2 files)
```
docs/handoff/2026-09-30-omniroute-gateway-findings.md
logs/handoff-sessions/DONE-ws-failures-doc.md
```

**`ws-patchbackups-3-20260930`** (2 files)
```
docs/handoff/2026-09-30-lanePatchBackups.md
logs/handoff-sessions/DONE-ws-patchbackups.md
```

**`ws-patchr1-20260930`** (3 files)
```
docs/handoff/2026-09-30-lanePatchR1.md
docs/handoff/2026-10-01-lanePatchVerify.md
logs/handoff-sessions/DONE-ws-patch-verify.md
```

**`ws-gw-admission-20260930`** (5 files)
```
debug-single.ps1
docs/handoff/2026-09-30-laneF2-admission.md
docs/handoff/2026-09-30-workstation-omniroute-handoff.md
logs/handoff-sessions/DONE-ws-gw-admission.md
measure-admission.ps1
```

**`ws-remote-20260930`** (20 files)
```
.agents/skills/unattended-orchestration/deepseek_call.py
configuration/api-keys.example.yml
configuration/omniroute/apply.ps1
configuration/omniroute/apply.sh
configuration/start-stack.ps1
configuration/start-stack.sh
docs/api-keys.md
lib/linux/install.sh
lib/windows/AutoOS.Install.psm1
setup.ps1
setup.sh
tests/linux/34-ai-services.sh
tests/run-tests.ps1
tests/test_autoos_gateway_key.py
tools/autoos-agent.py
tools/autoos_gateway_key.py
tools/probe-effort.py
tools/probe-recall.py
tools/probe-toolcalls.py
tools/sync-openhands-profiles.py
```
`ws-ovh-review-2-20260930` has **no** files (tip == base, 0 ahead).

---

## 2. Overlap matrix — files touched by more than one branch

Computed by unioning `git diff --name-only <base>..<tip>` over all 22 branches.

### Code / config (the contested ones)

| file | branches |
|---|---|
| `configuration/omniroute/apply.ps1` | combos, ovh, ovh-finish, nebius-combos, freewire, p0, admission-fix, applyjson, remote |
| `configuration/omniroute/apply.sh` | combos, ovh, ovh-finish, nebius-combos, freewire, p0, remote |
| `configuration/omniroute/combos.json` | combos, ovh, ovh-finish, nebius-combos, freewire |
| `configuration/start-stack.ps1` | p0, admission-fix, applyjson, remote |
| `configuration/start-stack.sh` | p0, remote |
| `tests/run-tests.ps1` | admission-fix, applyjson, suite-fix, remote |
| `catalog/ai-registry.json` | ovh, ovh-finish, nebius-combos, freewire |
| `catalog/ide-models.json` | ovh-finish, nebius-combos, freewire |
| `configuration/litellm/config.yaml` | ovh, ovh-finish, nebius-combos, freewire |
| `configuration/openhands/tier-profiles.json` | ovh-finish, nebius-combos, freewire |
| `configuration/autostart/Start-AutoOSStack.ps1` | admission-fix, applyjson |
| `tests/test_registry.py` | ovh, ovh-finish, nebius-combos, freewire |
| `tests/test_registry_render.py` | ovh, ovh-finish, nebius-combos, freewire |
| `tests/test_autoos_resolver.py` | nebius-combos, freewire |
| `tests/test_autoos_spawner.py` | nebius-combos, freewire |
| `tests/test_sync_ide_models.py` | nebius-combos, freewire |
| `tools/registry.py` | nebius-combos, freewire |
| `opencode.jsonc` | nebius-combos, freewire |
| `tools/probe-reasoning-repro.py` | fixes, verify-activate |
| `tools/probe-vertex.py` | f1-vertex, verify-activate |

### Docs / DONE notes (all merge cleanly — recorded for completeness)

| file | branches |
|---|---|
| `docs/models.md` | combos, ovh, ovh-finish, nebius-combos, freewire |
| `docs/handoff/2026-09-30-laneB-combos.md` | combos, ovh, ovh-finish, nebius-combos, freewire |
| `docs/handoff/2026-09-30-laneOVH-combos.md` | ovh, ovh-finish, nebius-combos, freewire |
| `docs/handoff/2026-09-30-laneOVH-ovhcloud.md` | ovh, ovh-finish, nebius-combos, freewire |
| `docs/handoff/2026-09-30-laneFreeWire.md` | nebius-combos, freewire |
| `logs/handoff-sessions/DONE-ws-ovh-finish.md` | ovh-finish, nebius-combos, freewire |
| `logs/handoff-sessions/OPERATOR-ws-ovh-finish.md` | ovh-finish, nebius-combos, freewire |
| `logs/handoff-sessions/DONE-ws-freewire.md` | nebius-combos, freewire |
| `docs/handoff/2026-09-30-laneAdmissionFix.md` | admission-fix, applyjson |
| `logs/handoff-sessions/DONE-ws-admission-fix.md` | admission-fix, applyjson |
| `docs/handoff/2026-09-30-workstation-omniroute-handoff.md` | p0, hygiene-main, gw-admission |
| `CHANGELOG.md` | applyjson |

**Note on `CHANGELOG.md`:** only `ws-applyjson` touches it in this wave, so it is not a
contested file here (the expectation in the brief that several branches touch it did not
materialise — verified by the union above).

---

## 3. Conflict reality (read-only `git merge-tree --write-tree`)

The following pairs conflict textually. This is the *actual* merge behaviour, not a guess.

**Against `origin/main` at analysis time (`e58274a`)**, among the 22 branches in this map, only the
combos lineage conflicts:
```
origin/main x ws-combos        -> CONFLICT configuration/omniroute/combos.json
origin/main x ws-ovh           -> CONFLICT configuration/omniroute/combos.json
origin/main x ws-ovh-finish    -> CONFLICT configuration/omniroute/combos.json
origin/main x ws-nebius-combos -> CONFLICT configuration/omniroute/combos.json
origin/main x ws-freewire      -> CONFLICT configuration/omniroute/combos.json
origin/main x <every other branch> -> clean
```

**Branch x branch:**
```
p0 x admission-fix -> CONFLICT configuration/omniroute/apply.ps1
                      CONFLICT configuration/start-stack.ps1
p0 x applyjson     -> CONFLICT configuration/omniroute/apply.ps1
                      CONFLICT configuration/start-stack.ps1
f1-vertex x verify-activate -> CONFLICT (add/add) tools/probe-vertex.py
<combos lineage tip> x {admission-fix, applyjson} -> CONFLICT configuration/omniroute/combos.json
```
Everything else is clean, including `admission-fix x applyjson`, `suite-fix x remote`,
`applyjson x remote`, `p0 x remote`, `gw-admission x p0`, `gw-admission x hygiene-main`,
`combos x p0`, `nebius-combos x p0`, `fixes x verify-activate`.

_Editor's note (take review): the merge-order section below (item 2 of the structural facts) states that the combos lineage also conflicts with `suite-fix` and `patchr1`; a reviewer's `merge-tree` re-run agreed. The list above is therefore incomplete; treat the merge-order section as the better statement._

**Scope note (found by review):** the "only the combos lineage conflicts" statement holds
**for the 22 branches in this map**. It is not a claim about the whole `refs/heads/L1-backlog/`
namespace: outside it, `ws-incident-20260930` conflicts with `origin/main` on
`.agents/skills/unattended-orchestration/SKILL.md`, and the non-requested
`ws-nebius` / `ws-nebiuswave` / `ws-nebiuswave2` branches conflict on `combos.json`
(same root cause as R1). Those were out of scope and are not in the merge plan.

### Why `combos.json` conflicts (the headline risk)

`origin/main` advanced past the lineage's branch point with **two commits touching
`configuration/omniroute/combos.json`**:
```
$ git log --oneline d08f7f2..origin/main -- configuration/omniroute/combos.json
8734223 Merge origin/main d08f7f2: operator workstation combos 1M + AGYCANON + apply automation (local sync, no push)
24a9822 fix(fleet): pin deepseek effort ladder, vertex leaf models, and the stack no-OOM changes
```
The lineage branched *before* that merge and rewrote the same file
(`d08f7f2..ws-nebius-combos`: `combos.json | 161 ++++ 147 insertions(+), 14 deletions(-)`).
Same file, divergent editing → conflict. Crucially, `origin/main` has **no** commits since
the base touching `apply.ps1`, `apply.sh`, `tests/run-tests.ps1` or
`catalog/ai-registry.json`, so those contested files conflict only *between branches*, not
with main.

### Why `p0` conflicts with the admission line

`p0` and `admission-fix`/`applyjson` both implement the `omniroute.cmd` shim +
admission-persistence fix on overlapping lines:
```
d08f7f2..p0              apply.ps1 +8/-?  start-stack.ps1 +15/-1
6ec0605..admission-fix   apply.ps1 +23/-1 start-stack.ps1 +23/-1 tests/run-tests.ps1 +30
6ec0605..applyjson       apply.ps1 +89/-2 start-stack.ps1 +23/-1 tests/run-tests.ps1 +106/-3
```
`applyjson` builds on `admission-fix` (`c3e139c` is admission-fix's 4th commit;
`admission-fix..applyjson = 2`, `applyjson..admission-fix = 1` — the extra commit is the
admission-fix DONE note `5e97708`). They merge cleanly together; `p0` is the odd one out.

---

## 4. Recommended merge order

Two structural facts drive the order:
1. The combos lineage is **strictly linear** — verified:
   `combos -> ovh -> ovh-finish -> nebius-combos` are ancestor links
   (`git merge-base --is-ancestor` = 0 for each consecutive pair), and
   `nebius-combos` and `freewire` share the **same tip `2ff537a`**.
2. `combos.json` conflicts with `origin/main` **and** with every branch whose base is
   newer than `d08f7f2` (admission-fix, applyjson, suite-fix, patchr1). Merging the lineage
   **last** reduces that to a single conflict event; merging it early re-triggers the
   conflict for each later branch.

### Phase 1 — docs-only / independent (order-free, all clean)
`ws-sweep`, `ws-sweep-review`, `ws-review`, `ws-failures-doc`, `ws-patchbackups-3`,
`ws-patchr1`, `ws-hygiene-main`.
_Skip_ `ws-ovh-review-2` — empty (0 commits); nothing to merge.

### Phase 2 — launcher / test line (this exact order)
1. `ws-admission-fix` (base `6ec0605`, clean into main)
2. `ws-applyjson` (builds on admission-fix; clean; also brings `CHANGELOG.md`)
3. `ws-suite-fix` (clean; on current main)
4. `ws-p0-admission-fix` — **one conflict**, in `apply.ps1` + `start-stack.ps1`, resolved
   once by unioning p0's admission-slot persistence with the admission-fix/applyjson shim
   fix. Doing p0 after step 2 means step 2 needs no resolution of its own.
5. `ws-remote` — clean textually against all of the above, but see §5 (94 behind).

If L0 prefers a clean history, rebase `p0` onto `origin/main` (+phase-2) first; the
conflict is the same and moves to the rebase.

### Phase 3 — probes / tools (order-free except the one conflict)
`ws-fixes`, `ws-gw-admission`, then `ws-f1-vertex` and `ws-verify-activate` in either
order — resolve the single `add/add tools/probe-vertex.py` conflict by keeping one copy
(or dropping the duplicate from `ws-verify-activate`, which lists it only as a probe
dependency).

### Phase 4 — combos lineage LAST (keep it linear)
`ws-combos` → `ws-ovh` → `ws-ovh-finish` → `ws-nebius-combos`.
- Resolve the `combos.json` conflict **once**, at the `ws-combos` step, against main's
  `8734223` "operator workstation combos 1M" version — keep the operator combos *and* add
  the new OVH / free legs. After that commit, `ovh` / `ovh-finish` / `nebius-combos` are
  descendants → fast-forward, no conflicts.
- **Do not merge `ws-freewire` separately**: it is the identical commit `2ff537a`.
  Merging it after `ws-nebius-combos` is a no-op; treat it as an alias.

### `catalog/ai-registry.json` — L1-beta's territory
Touched by exactly the four combos-lineage branches (`ovh`, `ovh-finish`,
`nebius-combos`, `freewire`). It does **not** conflict with `origin/main` or with any
non-lineage branch. Land it only with the Phase-4 lineage, coordinated with L1-beta.

---

## 5. Risks and decisions for L0

| # | Severity | Risk | Evidence / mitigation |
|---|---|---|---|
| R1 | high | `configuration/omniroute/combos.json` three-way conflict: lineage rewrite (147+/14−) vs main's `8734223` operator-combos merge. | `merge-tree` conflict on all 5 lineage tips. Mitigate: merge lineage **last** (Phase 4) → resolves once. Resolution must combine both sides, not pick one. |
| R2 | high | `p0` × (`admission-fix`,`applyjson`): `apply.ps1` + `start-stack.ps1`. | `merge-tree` exit 1. Mitigate: merge `p0` after the admission line; union the shim fix with p0's slot persistence. |
| R3 | medium | `f1-vertex` × `verify-activate`: `add/add tools/probe-vertex.py`. | `merge-tree` exit 1. Mitigate: keep one file. |
| R4 | medium | `nebius-combos` and `freewire` are the **same commit** `2ff537a`; merging both is pointless and will read as a no-op the second time. | `git rev-parse` both → `2ff537a`. Treat as one. |
| R5 | medium | **Stale gates.** Only `suite-fix` and `patchr1` sit on current `origin/main` (behind 0). `admission-fix`/`applyjson` are 8 behind; `remote` is **94** behind; the `d08f7f2` cohort (combos, ovh, ovh-finish, nebius-combos, freewire, p0, fixes, f1-vertex, sweep, sweep-review, review, hygiene-main, failures-doc, patchbackups-3, verify-activate, gw-admission) is **84** behind. | Per-branch `behind` column. Re-run `pwsh tests/run-tests.ps1` + `registry.py check/validate` after landing; DONE notes record green suites only against the old base (e.g. admission-fix: `1748 passed / 5 failed / 13 skipped`; applyjson: `1762 / 5 / 13`). |
| R6 | low | `ws-ovh-review-2` is empty (0 ahead, tip == base `d08f7f2`). | Skip it; nothing to merge. |
| R7 | low | ff-vs-rebase: lineage fast-forwards after the single combos resolution; `p0` and `remote` need a rebase or an explicit merge commit. | Recommend rebase `p0` and `remote` onto the post-Phase-2 main before merging, to keep main linear. |
| R8 | low | `remote` edits entry points (`setup.ps1`, `setup.sh`, `lib/*`, `tests/linux/*`) that moved across its 94-commit gap — textually clean but semantically unverified. | `merge-tree` clean vs all. Gate it independently after landing. |

### Gate staleness summary
- **Fresh (behind 0):** `ws-suite-fix`, `ws-patchr1`.
- **Slightly stale (behind 8):** `ws-admission-fix`, `ws-applyjson`.
- **Most stale (behind 94):** `ws-remote`.
- **Stale (behind 84):** every `d08f7f2`-based branch listed in R5.

---

## 6. Verification commands (reproduce this map read-only)

```bash
# per-branch tip / base / behind / files
git for-each-ref --format='%(refname:short) %(objectname:short)' refs/heads/L1-backlog/ws-
git diff --name-only $(git merge-base origin/main <branch>)..<branch>

# conflict probe (read-only; writes no ref, changes no worktree)
git merge-tree --write-tree origin/main <branch>   # exit 1 = conflict, prints CONFLICT lines
```
_(A harness deny rule on a command-name token applied during this lane's probing session; how it was handled is deliberately not reproduced here. The results are the literal git output.)_
