"""hostexec: a host-enforced deny-list + audit boundary for agent shell access.

NOT containment (spec status/L1-backlog.spec-host-shell.md F1, operator
decision Q0): a host CLI that already holds docker/sudo group membership can
always bypass this broker by running a shell directly. hostexec's job is a
sanctioned path with a real, fail-closed audit trail, and denying the worst
known-dangerous argv patterns it can see. See configuration/hostexec/README.md.

Modules: policy (decide allow/deny), audit (JSONL log), runner (subprocess
execution), server (MCP over streamable HTTP, imported lazily).
"""
