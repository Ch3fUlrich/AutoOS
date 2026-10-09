# `go-gate` — fake artefact tree for the fleet rule D-825 GO gate

`_go_gate.py` (and the tools that call it) proves that a `--go <ref>` names an
artefact that EXISTS, by reading the routing decisions log, the routing
QUESTIONS/ANSWERS files, and this checkout's worker records. Those are a host's own
files, so the suite must never read them: this tree stands in for all three and the
harness points the gate at it through the gate's own env knobs.

| env | this tree |
|---|---|
| `AUTOOS_ROUTING_DIR` | `routing/` |
| `AUTOOS_DECISIONS_LOG` | unset — the gate then reads `routing/docs/decisions-log.md` AND `routing/DECISIONS.md` |
| `AUTOOS_WORKERS_DIR` | `worker-tree/workers/` (its sibling `worker-tree/agents/` is the agent-dir shape) |

`worker-tree/`, not `logs/`: the repository's `.gitignore` excludes every `logs/`
directory so a real worker log can never be committed, and this tree has to be
committed. The gate does not care what the parent is called — it takes the agent dir
as the sibling of whatever `AUTOOS_WORKERS_DIR` names.

Ids deliberately present: `D-825`, `OS-0`, `OS-1`, `OS-2`,
`20261009-064139-goref-fix` (worker json), `20261009-070000-goref-agent` (agent dir).
Ids deliberately absent: `D-82` (only ever inside `D-825`, so the exact-id match is
what refuses it), `D-9999`, `OS-9`, `OS-10`, `20261009-000000-nosuchrun`.

Individual cases that need other content build their own temp tree instead of
editing this one. No host paths, no real ids, nothing here is a routing artefact.
