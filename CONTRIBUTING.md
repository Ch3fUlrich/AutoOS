# Contributing

This repository stores reusable AI-agent skills, per-repo starter adapters, and a
self-hosted MCP runtime. Contributions that add or improve skills are welcome.

## The rule behind most of the others

**Declared ≠ live. Verify against the running system, never against a config file or a
script's own report.** Nearly every serious defect here has been drift or false success —
a cluster config that was never applied (so five edge types silently did not exist), a
sync that logged "pulled central main -> local main" while writing nothing, a seed loader
that swallowed every failure, a `verify` that called an empty export "clean". If you add a
check, make it fail loudly; if you add a claim to a doc, make sure something executes it.

## Layout

```
skills/     # reusable skills — each SKILL.md is the single source of truth
starters/   # thin per-repo adapters that POINT at a skill, never copy it
infra/      # self-hosted runtime (MCP stack, sync tooling, optional local-ai)
prompts/    # copy-paste prompts for handing a scoped job to another agent
docs/       # architecture, agent-compatibility, ADRs (docs/decisions/)
```

## Adding a New Skill

Each skill lives under `skills/<skill-name>/` and follows this convention:

```
skills/<skill-name>/
├── SKILL.md              # Required — the full skill instructions
├── agents/               # Recommended — agent-specific configs
│   └── openai.yaml       # Codex agent definition
└── references/           # Optional — supporting docs, pattern catalogs
    └── ...
```

**`SKILL.md`** should be self-contained enough that an agent reading only that file can execute the skill. Put reusable detail in `references/` and keep the main file focused on when to use the skill and how to execute the workflow.

## Adding a Starter Pack

Some skills benefit from a starter pack — a short README that points users to
the full skill and explains the convention. These live under
`starters/<skill-name>/`:

```
starters/<skill-name>/
└── README.md             # What the skill does and how to reference it
```

The README should be short — a few sentences directing the user to the full
skill. Reference the skill by path, not by tool-specific commands.

If additional agent-specific instruction files are genuinely needed (different
content per agent, not just different filenames), add them alongside the README.
See `docs/agent-compatibility.md` for the native format each agent expects.

## When a Starter Pack Is Needed

Create a starter pack when:

- The skill requires per-project context or convention explanation.
- Users need a quick reference for how to wire the skill into their project.

If the skill is self-contained and the agent will load it from a skill library,
a starter pack is not needed.

## Compatibility Goal

Prefer broad, plug-and-play compatibility over a single-vendor setup. If a workflow should work in multiple agents, provide the native instruction file each agent expects and keep each adapter short.

## Register a new skill in the router

`skills/repository-index/SKILL.md` is the routing map an agent reads first. A skill nobody
routes to is a skill nobody loads — add a row with an **observable trigger** (e.g. "diff
touches > 3 files", "the answer would exceed ~100 lines of markdown"), not "any complex
task". `skills/SYNC.md` is the vendoring ledger for borrowed skills (upstream, licence,
vendored date) — update it when you vendor or re-sync one; it is not a router.

## Recording decisions

- **ADRs** live in `docs/decisions/NNNN-kebab-title.md`. Write one for any decision a
  reasonable engineer might later question — a dependency choice, a trade-off, a rejected
  alternative.
- **Amend, don't rewrite.** An ADR records what was decided *then*. When reality moves on,
  leave the text and add a dated note pointing at the ADR that changed it (see 0001 → 0003).
- **`CHANGELOG.md`** gets an entry in the same change that makes the change — describe the
  effect, not the diff, and reference the ADR. It is history: add to it, don't rewrite it.
- The same applies to memory: supersede an accepted `Decision` with a new node plus a
  `Supersedes` edge rather than overwriting its rationale.

## Line endings (this will bite you)

`.gitattributes` pins `*.sh`, `*.py`, systemd units, and configs to `eol=lf`; `*.ps1` stays
`crlf`. Do not override it. A CRLF shebang becomes `#!/usr/bin/env bash\r`, the kernel
looks for an interpreter literally named `bash\r`, and the script cannot run — while `bash
script.sh` still works on Windows, so it looks fine locally and breaks everywhere else.
Check with `file <script>` (expect no `CRLF`).

## Touching the memory stack

`infra/mcp-servers/omnigraph-setup/` holds the local↔central sync. Before changing it,
read `omnigraph-setup/SYNC-MANUAL.md` ("How it works" explains *why* each choice exists —
each was bought with an incident) and
`skills/structured-memory/references/operations.md`. Two invariants:

- **Nodes are `@key(slug)` and upsert; edges have no key and DUPLICATE.** Never push a
  whole export at a graph that already has the data.
- **Never `load --mode overwrite` a populated graph** — it trips a Lance bug on v0.8.1 and
  can land while exiting 1, so even its failure is untrustworthy.

Both sync scripts (`omnigraph-sync.sh`, `sync-windows.ps1`) drive the same two Python
helpers, so the logic lives once — change the helper, not one platform. `omnigraph_jsonl.py`
has unit tests (`test_omnigraph_jsonl.py`); run them.
