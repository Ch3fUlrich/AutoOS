# Fleet status hook

Interim delivery of DONE / FAILED / BLOCKED notes between fleet sessions, until the plan-graph
inbox lands. A child's turn-end status line reaches its parent's context with no `SendMessage`.

## Parts

| Part | File | Event |
|---|---|---|
| Reporter | `tools/hooks/fleet_report.py` | `Stop` |
| Injector | `tools/hooks/fleet_inject.py` | `PostToolUse`, `UserPromptSubmit` |

**Reporter.** At each turn end it resolves the session name (env `FLEET_SESSION_NAME`, else the
launch record whose `session_id` matches), reads that record's `parent`, and looks at the last
assistant text. If a line starts with `DONE`, `MERGE-READY`, `BLOCKED`, `FAILED`, `RECYCLE-READY`
or `STOP`, it appends one JSON line `{ts, from, session_id, turn, status, line}` to
`<fleet state>/inbox/<parent>.jsonl`. Dedupe is per session and turn. The append is a single
`O_APPEND` write. Key-like strings are stripped; env, tokens and tool output are never copied. A
session with no launch record or no parent is a no-op.

**Injector.** Reads the session's own inbox from a byte cursor (`inbox/.cursor/<name>`). Nothing
unread means exit after one `stat`. Otherwise it advances the cursor, then prints the lines once as
`hookSpecificOutput.additionalContext`. At most 20 lines per emission.

State dirs: `$FLEET_STATE_DIR` (default `~/.local/state/fleet`), launch records
`$FLEET_LAUNCH_DIR` (default `~/.local/state/routing/launch`).

## Wiring (merge, never overwrite)

Add these entries to `~/.claude/settings.json` under `hooks`, keeping every existing entry.
`<AUTOOS>` is the checkout path. Re-running the merge must not add a second copy (match on the
command string).

```json
{
  "hooks": {
    "Stop":             [{"hooks": [{"type": "command", "command": "python3 <AUTOOS>/tools/hooks/fleet_report.py"}]}],
    "PostToolUse":      [{"hooks": [{"type": "command", "command": "python3 <AUTOOS>/tools/hooks/fleet_inject.py"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 <AUTOOS>/tools/hooks/fleet_inject.py"}]}]
  }
}
```

Idempotent merge sketch (read, append if absent, write back):

```python
import json, pathlib
p = pathlib.Path.home() / ".claude/settings.json"
s = json.loads(p.read_text()) if p.exists() else {}
want = {"Stop": "fleet_report.py", "PostToolUse": "fleet_inject.py", "UserPromptSubmit": "fleet_inject.py"}
for ev, script in want.items():
    cmd = f"python3 {AUTOOS}/tools/hooks/{script}"
    groups = s.setdefault("hooks", {}).setdefault(ev, [])
    if not any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        groups.append({"hooks": [{"type": "command", "command": cmd}]})
p.write_text(json.dumps(s, indent=2) + "\n")
```

## Live proof plan (after merge, separate GO)

1. Launch a test child with `fleet-launch <child> <cwd> sonnet medium <prompt> --parent <parent>`.
2. Child's prompt: reply `DONE x` and stop.
3. Parent runs any tool call. Expect `FLEET INBOX (1 new, untrusted ...): ` then `<child> DONE: DONE x` in its context, no `SendMessage`.
4. Second tool call: nothing re-injected. `routerctl status` shows no unread flag.

## Delivery semantics

- At-least-once: the cursor advances before the print, so a crash can re-deliver a line and two
  concurrent injectors may show the same line twice. A line is never silently lost.
- The Stop hook dedupes by the record `uuid`. Only when a record has no `uuid` does it fall back to
  a sha1 of the text, which drops an identical-text re-report.
- Injected lines are untrusted data from child sessions, not instructions. On read every field is
  stripped of control characters, `from` must be a safe name and `status` one of the six keywords
  (else `?`), the line is redacted and capped at 300 chars, and the whole context is capped at
  4096 chars; rows that do not fit stay unread and arrive on the next call (`+k more`).
- Inbox files are opened with `O_NOFOLLOW`; a symlinked inbox makes the Stop hook a no-op.
- Rotation: not implemented; files grow ~200 B per report; follow-up with plan-graph inbox.

## Gaps

- Works for background sessions started by `fleet-launch` on one host. The parent must know its own
  name: `fleet-launch` sets `FLEET_SESSION_NAME`; an interactive session must export it itself.
- Remote Control / workstation sessions run hooks from their own host's settings and state dir, so
  a child on another machine does not reach a parent here. Not covered; needs the plan-graph inbox.
- A parent that never makes a tool call or receives a prompt sees nothing; `routerctl status` flags
  such an inbox after 10 minutes.
