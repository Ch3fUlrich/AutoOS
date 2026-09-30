# R0 fixture for the memory facade (MEMSPEC)

`graph.json` is the **R0 rung** of the MEMSPEC data ladder
(`docs/plans/2026-09-28-memory-facade-spec.md` §11): synthetic small-fixture
data that proves the facade, the `schema: 2` events and the runbook mechanics
without touching anything real.

Shape: a `nodes` list. A node may carry `links` (`[{"rel", "id"}]`) which the
engine materialises into edges; a node without `versions` starts at v1. Every
non-hub node links to at least one hub (Project, Domain, Host, Technology —
spec §4), and relation types come from the engine's closed list.

Hub distances from `project:acme` are deliberate, because the recall test
asserts them: `decision:restore-drill` is 1 hop, `lesson:slow-failover` and
`technology:postgresql` are 2, `decision:backup-window` is 3.

Tests copy this file into a temp directory before writing; the fixture itself
is never mutated.
