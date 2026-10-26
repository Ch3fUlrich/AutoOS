# Live state — read it, never write around it

The live gateway (OmniRoute, its `~/.omniroute` store and its API) is a
production surface with its own state. The repo's files describe what *should*
be there; the live gateway answers what *is* there. Reading the second is fine
and often necessary. Writing it outside `apply.sh` / `apply.ps1` is forbidden.

## The rules

1. **Read live state only through the read-only MCP (RT-P5-OR-MCP) or
   `apply.sh --drift`.** No ad-hoc HTTP spelunking to answer "what combos exist
   today" — those two read paths are the contract.
2. **NEVER edit `~/.omniroute`.** That store is the gateway's own; a hand edit
   bypasses every gate and every render, and the next apply reconciles it away
   or worse, adopts it.
3. **NEVER write through MCP/API.** No MCP mutation, no `curl`/`Invoke-RestMethod`
   POST/PATCH/DELETE to the gateway's management routes to make a change stick.
   The override inputs in the [file map](file-map.md) are applied by their
   appliers, and combo changes by `apply.sh` / `apply.ps1` — that is the whole
   write surface.

## Drift: `apply.sh --drift`

```sh
bash configuration/omniroute/apply.sh --drift                    # live vs snapshot
bash configuration/omniroute/apply.sh --drift --drift-against registry
```

- Read-only: it mutates nothing and needs no GO.
- Compares the live combos with the server snapshot
  (`configuration/omniroute/live/server-combos.json`, or
  `AUTOOS_DRIFT_SNAPSHOT`); `--drift-against registry` compares with
  `combos.json`, including `retired`/`omitted`.
- Exit codes: **0** in sync, **1** drift (differences printed), **3** snapshot
  unreadable. A typo'd flag exits 2 — a bad argument never falls through to a
  live run.
- `--allow-drift <name>` permits later mutating/pruning of a drifted combo by
  name; it is the exception you must name explicitly, never a default.

Drift is information: `1` means somebody (or some lane) changed the live
gateway outside the registry pipeline. Reconcile through the registry and a
gated apply — do not "fix" the drift by editing either side by hand.

## Probes and the availability ledger

- `configuration/omniroute/probes/` holds recorded probe output (the
  `probe-d657-*` TSVs, leg files): measurements, kept as data.
- The ledger is `python tools/probe-ledger.py`:

  ```sh
  python tools/probe-ledger.py record --leg <provider/model> \
      --status <served|failed|limited> --reason <text> [--latency-ms <n>]
  python tools/probe-ledger.py check --leg <provider/model> [--max-age-hours <n>]
  python tools/probe-ledger.py dump
  python tools/probe-ledger.py credit ...
  ```

  State lives in `logs/routing/availability.json` — **git-ignored runtime
  state**. Recording is a live-host step; the repo carries the probe TSVs and
  the dated `$comment`s, not the ledger file.
- `apply.sh --probe` sends one tiny request per combo and reports what
  answered (spends a few hundred tokens; skipped under `--dry-run`). The
  mutating-run GO gate covers `--probe` too.

## What live reads are for

- Before adding a leg: confirm it exists and serves (the tier-order rule
  requires live verification before curation).
- After an apply: confirm what actually served (the TORDER log pattern: the
  combo served by its free head, `Combo context limit: … (source=target)`).
- When a route misbehaves: `--drift` first, then the ledger
  (`probe-ledger.py check --leg …`), then the registry. Suspect the live state
  last, hand-edit it never.
