# Decisions log fixture — fleet rule D-825 GO-ref tests

This is NOT the routing decisions log. It is a fake artefact tree the suite reads
through `AUTOOS_ROUTING_DIR` / `AUTOOS_DECISIONS_LOG` / `AUTOOS_WORKERS_DIR` (see
`tests/fixtures/go-gate/README.md`) so the gate's ref-existence check is tested
against known content and never against a host's own `~/code/routing`.

The ONLY decision id that appears below is D-825 — a fixture that named the ids it is
meant to keep absent would prove nothing about the exact-id match.

| id | project | decision | by |
|---|---|---|---|
| D-825 | all L1s, prox | Rule adopted: a live or infra step never proceeds on silence. The silence default is HOLD; only an explicit GO from the judge naming the sha and the scope counts, and the tools that apply take a `--go` reference and refuse without it. | router |
