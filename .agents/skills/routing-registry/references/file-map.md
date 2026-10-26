# File map — the registry and everything derived from it

`catalog/ai-registry.json` is the **one model truth** (D-2038). Everything else
on this page is either generated from it, a live-gateway override applied on
top of it, or runtime state that records what the live gateway actually did.
A fact lives in exactly one place: the registry carries declarations, the
renders carry none of their own, and the ledger carries measurements.

## The registry — `catalog/ai-registry.json`

Sections: `providers`, `models`, `clients`, `routes`.

- `routes.<id>` holds the ordered `legs` (provider/model pairs), the
  `unavailable_legs` gate (declared legs that must not serve today), and a
  `$comment` carrying the dated reasoning for every leg decision.
- A route id is a contract: renaming one breaks saved selections, `--only`
  flags and every rendered surface at once.
- Two constants live beside it in `tools/registry.py`, deliberately as code:
  `IDE_MODEL_ORDER` (which routes the IDE surfaces offer, in order) and
  `OMNIROUTE_RETIRED_IDS` (ids apply must prune from the live gateway).
- `tools/combo-contract.py` holds the gate constants (`T1_MIN_WINDOW`,
  `T2T3_ROUTES`, `SINGLE_PROVIDER_EXEMPTIONS`) — see
  [tier-order](tier-order.md#gate-fail-closed).

## Generated surfaces (never hand-structured)

Each is rendered by `python tools/registry.py render <surface>` and verified by
`render <surface> --check` (exit 1 on semantic drift):

| Surface | Render |
|---|---|
| `configuration/omniroute/combos.json` | `render omniroute` — keys `$comment`, `retired`, `omitted`, `d657_combos`, `combos`; hand `$comment`s are kept, structure is generated |
| `configuration/litellm/config.yaml` (AUTOOS-MANAGED tier blocks) | `render litellm` (or `python tools/sync-router-tiers.py`) |
| `catalog/ide-models.json` | `render ide` |
| `configuration/openhands/tier-profiles.json` | `render openhands` |
| `docs/models.md` (AUTOOS-MANAGED table) | `render models-doc` |

`combos.json` `retired` mirrors `OMNIROUTE_RETIRED_IDS`; `omitted` is for
declared routes that deliberately render no combo (kept declared, not deleted).
The difference matters: a deleted route belongs to no set; an omitted one keeps
its declaration.

## Live-gateway override inputs (appliers, not renders)

| Input | Applied by |
|---|---|
| `configuration/omniroute/capability-overrides.json` | `configuration/omniroute/apply-capability-overrides.ps1` |
| `configuration/omniroute/context-overrides.json` | `apply.sh` / `apply.ps1` — the known-wrong context resolutions, patched through the gateway's management route |
| `configuration/omniroute/custom-models.json` | `configuration/omniroute/apply-custom-models.sh` (called by `apply.sh` before the catalog sync; idempotent) |

These describe the live gateway's own state, so they are applied, not rendered.
Editing one is still a registry-adjacent change: re-run the dry run and the
gate after touching them.

## Drift snapshot and probes

- `configuration/omniroute/live/server-combos.json` — the drift snapshot
  `apply.sh --drift` compares the live gateway against (override:
  `AUTOOS_DRIFT_SNAPSHOT`). `--drift-against registry` compares against
  `combos.json` instead, including `retired`/`omitted`.
- `configuration/omniroute/probes/` — recorded probe output (the `probe-d657-*`
  TSVs and leg files): what was measured, when, and what answered.
- **Availability ledger**: `python tools/probe-ledger.py` (`record`, `check`,
  `credit`, `dump`). State lives in `logs/routing/availability.json` —
  git-ignored runtime state, never committed; recording a leg is a live-host
  step.

## Population scripts (client model lists)

- `tools/sync-ide-models.py` — syncs every client's gateway model list from the
  registry render: the `opencode.jsonc` managed blocks (repo root),
  `configuration/openhands/config.toml`, and the tier-profile surfaces.
  `--check` prints a diff and changes nothing.
- `tools/sync-openhands-profiles.py` — regenerates OpenHands gateway tier
  profiles from the spec (`--openhands-dir <dir>`; also `--consumer`).
- `tools/render-opencode-container-config.py` — derives the container's opencode
  config from the host's own; the container copy is never hand-kept.

Rule of thumb: if you are about to edit a generated file to make something
true, edit the registry and re-render instead. If a generated file and the
registry disagree, the registry wins and the render is the fix.
