---
name: homelab-access
description: How to reach homelab hosts safely — pick the right account for the task, decode the errors that mean something other than they say, and know what is deliberately unreachable. Site facts come from a config file, never from this page. Load before any command that touches a VM, the firewall, or storage.
---

# Homelab Access

**This page contains no hostnames, users, addresses or products.** Every site fact
lives in a config file so the skill is portable and the repository is publishable.
Ask the `homelab` MCP server, which reads that config:

| Question | Tool |
|---|---|
| What hosts exist? | `homelab_hosts` |
| Which account should I use for this? | `homelab_plan` |
| What does this error actually mean? | `homelab_diagnose` |
| Run it with the right account | `homelab_run` |
| What is deliberately off-limits? | `homelab_boundaries` |
| Does access still work? | `homelab_verify` |

Config: `HOMELAB_CONFIG`, else `~/.config/homelab/homelab.yaml`. Schema and setup:
[`infra/mcp-servers/servers/homelab-mcp/`](../../infra/mcp-servers/servers/homelab-mcp/README.md).
**Never hard-code a host or user in a script or a doc — add it to the config.**

## The one idea worth internalising

**Accounts are a capability matrix, not a hierarchy.** A host may offer several
accounts, and each can do things the others cannot. "More privileged" is not
"better": the right account is the *least* capable one that can do the job.

This matters because **picking wrong produces a misleading error, not a clear
one** — which is why guessing costs so much time here. `homelab_plan` makes the
choice from the config's capabilities, so you do not have to remember it:

```
homelab_plan(host="<from homelab_hosts>", intent="container_logs")
→ role, alias, why, and warnings (host status, fleet exclusions, quirks,
  whether the role is recorded)
```

If an intent needs a capability the host does not offer, the error says which
capabilities it *does* have — rather than failing at the shell later.

## Errors that mean something else

Do not debug these from first principles — `homelab_diagnose(text=<stderr>)`
decodes them from the config. The classic shapes:

| What you see | What it usually is |
|---|---|
| `Permission denied (publickey)` | the **default** key was offered — a raw address was used instead of the alias |
| `permission denied … docker.sock` | the account is **not in the docker group** — wrong account, not a broken daemon |
| `sudo: a terminal is required` | the command is **outside** that account's scoped sudo. **Not** a TTY problem — no `-t`/`-S` helps |
| `Illegal variable name.` / `Ambiguous output redirect` | a **successful login** whose shell cannot parse bash — not an auth failure |
| `ping` unreachable on a host that is up | ICMP filtered — probe with `ssh <alias> true` |
| `/proc/<pid>/…: Permission denied` even as uid 0 | you are inside a **container** lacking `CAP_SYS_PTRACE` — read `/proc` from the **host** |

That last one generalises: **to diagnose a hung process in a container, go in
through the host, not `docker exec`.** The most diagnostic fields — kernel wait
channel, per-task state, fd offsets — are exactly the ones the container cannot
show you, and they are what separates "blocked on storage I/O" from "deadlocked
in userspace". Use the intent that grants host `/proc` access.

## Before you touch anything wide

`homelab_boundaries` returns two things the config declares:

- **Unreachable** — deliberately no agent credentials. These are *decisions*, not
  faults; do not debug them, and do not file them as bugs.
- **Fleet exclusions** — hosts that must never be included in a fleet-wide
  operation, with the reason. A host that serves storage other hosts mount is the
  usual case: a fleet reboot or prune takes its dependents down with it.

`homelab_plan` surfaces an exclusion as a warning before you act on that host.

## Root is not an agent capability

If a task needs root, the answer is not "find the account that has it". A key that can be
stolen from the agent host must authenticate exactly one account, and that account must have
no path to root — otherwise every "scoped" account is decorative, because whoever holds the
key simply picks the privileged one. Root-needing work belongs to **automation with its own
identity** (a key that never touches the agent host) or to a **human**. Declare it under
`unreachable` in the config so `homelab_boundaries` says so, and so nobody wires it back in.

A host can also **forbid** specific intents and command patterns outright (`hosts[].forbid`),
enforced by `homelab_plan`/`homelab_run` rather than merely warned about — the shape for "this
host is never updated or restarted by an agent, because everything else depends on it".

## Running things

Prefer `homelab_run` over hand-written `ssh`: it selects the account, wraps the
command for non-POSIX login shells, refuses configured destructive patterns
before anything is sent, requires an explicit reason for recorded roles, and
attaches a diagnosis when the command fails.

Reach for raw `ssh` only for something the config does not model — and then add
it to the config so the next agent does not have to.

**Recorded access:** a config may mark a role as recorded (session I/O logs,
audit keys). `homelab_plan` warns when it selects one. Use it only for what the
lesser roles cannot do, and say why in the commit or report.

## Verifying

`homelab_verify` probes every configured host and role over SSH (read-only
`id -un`) and reports which authenticate. Run it when access "suddenly breaks" —
it distinguishes a real outage from a stale alias or the wrong key in one shot.

## Keeping the config honest

The config is the only place site knowledge lives, so it is the only place worth
correcting. When you learn something the hard way — a new quirk, a host that must
never be in fleet operations, an error whose meaning is not obvious — add it to
`quirks`, `exclusions` or `diagnostics`. The next agent gets it decoded
automatically instead of rediscovering it.
