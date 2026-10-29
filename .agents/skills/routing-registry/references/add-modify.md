# ADD / MODIFY — one path, in this order

`registry edit -> render --check -> combo-contract -> apply --dry-run -> GO
(operator/router) -> apply`

Every step is a separate command with its own exit code. Do not reorder: the
gate exists to fail before anything touches the live gateway, and the GO exists
to prove a human (operator or router judge) approved the exact code being
applied.

## 1. Edit the registry

Edit `catalog/ai-registry.json` under the [tier-order rule](tier-order.md).
Conventions:

- `id` is a contract — stable, kebab-case, never reused (a retired id goes to
  `OMNIROUTE_RETIRED_IDS`, never back into `routes`).
- Every leg decision is reasoned and **dated** in the route/model `$comment`
  (the decision id, e.g. `ORQWEN-DEAD`, `SCWREMOVAL`) and mirrored in the
  availability ledger. No silent churn.
- Verify a leg against the live catalog before adding it; if that is impossible
  without inventing a measurement, keep what exists and report it (D-TORDER-2
  pattern).

A **new route** also needs:

- `IDE_MODEL_ORDER` in `tools/registry.py` (which surfaces offer it, in order),
- the litellm managed block in `configuration/litellm/config.yaml`,
- an `OMNIROUTE_RETIRED_IDS` check — the id must not already be retired.

## 2. Render and check

```sh
python tools/registry.py render omniroute --check     # exit 0
```

The render owns the file's structure; `--out` is scratch-only — the target's
own `--help` says "never the real `combos.json`", and aiming `--out` at the
committed file drops the hand `$comment`s and the dated TORDER entry.
Regenerate `combos`/`omitted`/`retired` from the render into the committed
file by hand, keeping the hand `$comment`s and the dated TORDER entry, then
re-check — [surfaces](#surfaces) names each surface's updater.

## 3. Contract gate

```sh
python tools/combo-contract.py                        # exit 0, per-combo PASS
```

Fail-closed everywhere it matters (see [tier-order](tier-order.md#gate-fail-closed)).
A `PASS,e-exempted` line is a documented single-provider exemption, not a pass
you can widen.

## 4. Every consumer surface

```sh
python tools/registry.py render litellm --check
python tools/registry.py render ide --check
python tools/registry.py render openhands --check
python tools/registry.py render models-doc --check
python tools/sync-ide-models.py --check               # opencode.jsonc + openhands
```

All exit 0.

## Bringing a committed surface back in line {#surfaces}

Drift is fixed through the surface's sanctioned updater — never through a
render's `--out` onto a file its own `--help` calls "never the real ..." (each
tool's `--help` is the source here). Only `render ide` may write a committed
file:

| Committed file | Sanctioned updater |
|---|---|
| `configuration/omniroute/combos.json` | no writer owns it — render to stdout or a scratch path and copy the structure into the committed file by hand (keep the hand `$comment`s and the dated TORDER entry), then `render omniroute --check` proves it (equality ignores `$comment` and key order) |
| `configuration/litellm/config.yaml` | `python tools/sync-router-tiers.py` — writes the AUTOOS-MANAGED tier blocks in place (`--check` only diffs) |
| `catalog/ide-models.json` | `python tools/registry.py render ide --out catalog/ide-models.json` — the one sanctioned render write (its `--help` says so) |
| `configuration/openhands/tier-profiles.json`, `opencode.jsonc`, `configuration/openhands/config.toml` | `python tools/sync-ide-models.py` — writes every client surface (`--check` only diffs) |
| `docs/models.md` | hand splice only — there is no `--write`: run `python tools/registry.py render models-doc` and replace the text between the `AUTOOS-MANAGED-START/END models-doc` markers with its output |

Then re-run every `--check` above until green. Prove the client limits moved
too (e.g. opencode `limit.context=1048576, output=65536` for the 1M gemini
tier).

## 5. Offline checks

`python tools/registry.py check` + `validate`, `audit-router.py --offline`,
`pytest tests/test_registry.py`, `apply.ps1 -DryRun` exit 0,
shellcheck/PSScriptAnalyzer clean.

That is the folded-in predecessor procedure's step 6, restored word for word.
When this skill replaced the predecessor, the step's `pytest` and
shellcheck/PSScriptAnalyzer legs were dropped; they are part of this gate, not
optional extras. Spelled out:

```sh
python tools/registry.py check
python tools/registry.py validate
python tools/audit-router.py --offline
pytest tests/test_registry.py                 # targeted: a registry change
bash configuration/omniroute/apply.sh --dry-run       # Windows twin:
# configuration/omniroute/apply.ps1 -DryRun
```

All exit 0, and the linters are clean: shellcheck over every touched `.sh`,
PSScriptAnalyzer over every touched `.ps1`/`.psm1` (a skip is not a pass). The
pytest leg is the targeted `tests/test_registry.py` (a skill edit adds
`tests/test_skill_routing_registry.py`); the bare full suite is long and
carries unrelated collection errors, so CI runs it and the targeted run is the
local gate. A
dry run plans without executing; read the plan output and confirm it says what
you expect. Under the repo's test harnesses the gateway URL is aimed at a
closed port (`AUTOOS_OMNIROUTE_URL`) so a dry run never reads the live gateway.

## GO (operator/router), then apply {#go}

A run that **mutates** the live gateway — anything that is neither `--dry-run`
nor `--drift`, `--probe` included — refuses with exit 2 unless it carries an
explicit judge GO (fleet rule D-825):

```sh
bash configuration/omniroute/apply.sh --go <ref> --go-sha <sha>
```

- `--go <ref>` names the approving artefact: a judge run id
  (`YYYYMMDD-HHMMSS-…`), a decision id (`D-<n>`), or an OS-`<n>` item.
- `--go-sha <sha>` is this checkout's `git rev-parse HEAD`, so the GO covers
  the exact code being applied.
- The gate also proves the ref **exists** (decisions log, QUESTIONS/ANSWERS for
  OS items, a worker record for a run id) via
  `infra/mcp-servers/scripts/_go_gate.py` — one implementation every gate on
  this rule calls. `--go-offline` is the operator's declared exception
  (`GO-OFFLINE: <ref> unverified`; the sha is still verified).
- `--dry-run` and `--drift` need no GO; a `--go` there is only echoed. The
  first line a gated run prints is `GO: <ref> sha=<sha>`.
- Windows twin: `configuration/omniroute/apply.ps1 -Go <ref> -GoSha <sha>`.

Optional: `--probe` sends one tiny request per combo and reports what answered
(spends a few hundred tokens; skipped under `--dry-run`).

## 6. Definition of done

- `--dry-run` plan read and understood before the GO.
- Gate green (`combo-contract.py`, `registry.py check`, `validate`,
  `audit-router.py --offline`, `pytest`) and shellcheck/PSScriptAnalyzer clean.
- Every `--check` green on all five surfaces + `sync-ide-models.py --check`.
- The change is reasoned and dated (`$comment` + ledger record where a leg
  moved).
- Ran twice: the second run reports `skipped`, not `installed` — apply is
  idempotent; a re-run that re-mutates is a defect.
