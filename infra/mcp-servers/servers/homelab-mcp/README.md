# homelab-mcp

An MCP server for reaching homelab hosts. **Fitted to a homelab entirely by a
config file** — no hostname, username, product or path appears in the code.

## Why it exists

Homelab access fails in ways that mean something *other* than they say: a
successful login that could not parse the command; a scoped-sudo refusal that
looks like a TTY problem; the default SSH key offered because a raw address was
used instead of an alias. Documentation describing that is only as good as the
agent's willingness to read it. This server makes the same reasoning executable:

- **`homelab_plan`** answers *"which account should I use for this?"* and picks
  the **least-capable role** that satisfies the intent.
- **`homelab_diagnose`** decodes an error into what it actually means.
- **`homelab_run`** applies guardrails, wraps for non-POSIX login shells, and
  attaches a diagnosis when a command fails.

## Roles are capabilities, not a hierarchy

The central idea. Three accounts on the same host can each do things the others
cannot — being "more privileged" does not make one the right choice. The config
declares capabilities; intents declare what they need; the server matches them
and escalates only when required, warning when the chosen role is recorded.

## Tools

| Tool | Purpose |
|---|---|
| `homelab_hosts` | Inventory: address, roles, shell, status, tags, quirks |
| `homelab_roles` | The capability matrix and the configured intents |
| `homelab_plan` | Which role/alias to use for a host+intent, and why |
| `homelab_run` | Run a command via the right role, with guardrails |
| `homelab_diagnose` | Decode an error message |
| `homelab_verify` | Probe every host+role over SSH (read-only `id -un`) |
| `homelab_boundaries` | What is deliberately unreachable; fleet exclusions |

## Setup

```bash
cp homelab.example.yaml ~/.config/homelab/homelab.yaml   # keep it OUT of git
chmod 600 ~/.config/homelab/homelab.yaml
$EDITOR ~/.config/homelab/homelab.yaml
```

Register it (user scope — it serves every repo):

```bash
claude mcp add -s user homelab -- \
  python3 -m homelab_mcp.server          # with HOMELAB_CONFIG exported
```

Config discovery: `HOMELAB_CONFIG`, then `~/.config/homelab/homelab.yaml`, then
`~/homelab.yaml`. A missing config produces an actionable error, not a stack trace.

## Safety

`safety.deny_patterns` refuses destructive commands before anything is sent.
`safety.require_reason_for` forces an explicit reason for privileged/recorded
roles. `safety.allow_run: false` disables execution entirely, leaving the server
read-only (plan/diagnose/verify still work).

## Tests

```bash
python -m venv .venv && .venv/bin/pip install pytest pyyaml "mcp<2"
.venv/bin/python -m pytest tests/ -q
```

`test_no_site_facts_in_code` is the guarantee that matters: it fails if a
hostname, username, product or private IP ever appears in the source.
