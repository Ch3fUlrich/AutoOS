# Fleet hooks: the Bash PreToolUse guard

**What it does:** before Claude Code runs a `Bash` tool call, `tools/hooks/bash_guard.py`
reads the call's command text and refuses exactly two shapes that let the
*calling* shell execute text nobody meant to run as a command:

1. An **unquoted heredoc** (`<<EOF`, `<<-EOF`) whose body contains a literal
   backtick or `$(...)`. An unquoted heredoc expands those exactly like a
   double-quoted string does — a quoted delimiter (`<<'EOF'`, `<<"EOF"`,
   `<<\EOF`) does not, and is always allowed.
2. A `claude --bg` / `claude -p` / `claude --print` invocation whose
   double-quoted argument contains a backtick or `$(...)`. The calling shell
   expands that before `claude` ever sees the prompt text — same hazard,
   one call frame up.

Everything else is allowed **silently** (exit 0, no output). A malformed or
unexpected hook payload fails **open** (exit 0, one stderr note) — this is a
shell gate, not a sandbox, and a bug in the guard must never be the thing
that blocks an otherwise-fine session.

---

## Why

A seat prompt was built with an unquoted heredoc whose prose body happened
to contain backticked shell commands. On a host running sessions in bypass
mode (no classifier in front of `Bash`), the shell executed that prose the
moment the heredoc was read — including a `netplan apply` nobody asked for
(it only failed for lack of root). The fix is the same one bash already
offers: quote the heredoc delimiter when the body is prose, not a command.
This guard makes that fix mandatory instead of advisory, and extends the
same check to a one-shot/background `claude` launch built the same way.

## Install

Claude Code reads hooks from `~/.claude/settings.json`. A `PreToolUse` entry
for the `Bash` matcher runs the guard before every Bash call:

```jsonc
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/tools/hooks/bash_guard.py"
          }
        ]
      }
    ]
  }
}
```

That file is user-owned config, not something this repository writes for
you — read the existing file, merge the `PreToolUse` entry into it (hard
rule 2, `AGENTS.md`), and point `command` at this checkout's
`tools/hooks/bash_guard.py` with an absolute path (the hook itself has no
shell-specific syntax and runs the same way under POSIX `python3` on the
workstation and on a Windows sandbox's `python`).

## Behaviour

| Input to the hook | Result |
|---|---|
| `tool_name` is not `"Bash"` | allowed, silently |
| stdin is not valid JSON | allowed, one stderr note |
| `tool_input` is missing or not an object | allowed, one stderr note |
| `tool_input.command` is missing/empty | allowed, silently |
| an unquoted heredoc's body has a backtick or `$(` | **denied**, exit 2 |
| a `claude --bg`/`-p`/`--print` call's double-quoted argument has one | **denied**, exit 2 |
| anything else | allowed, silently |

The heredoc check tracks quote state and `(`/`$(`/`$((` nesting itself (no
shell is invoked to parse the command), so it does not fire on a heredoc
whose delimiter is quoted, a literal `<<` inside a double-quoted string
(`echo "a << b"`), the `$((...))` arithmetic shift operator, or text that
merely follows a heredoc's terminator line. The `claude` check only flags a
**double-quoted** argument — a single-quoted or escaped backtick, and a
bare `$VAR`, are never expanded by the shell and are always allowed.

Round-2 coverage extends checks through leading command wrappers (`sudo`, `env`, `command`, `nohup`, `time`, `exec`, `nice`, `setsid`, `timeout`, `builtin`), flag assignments (`--print="..."`), clustered short flags, backslash-escaped triggers in unquoted heredoc bodies (`\` and `\$(`), resilient 1 MiB fail-open stdin decoding with robust UTF-8 replacement, and recursive scanning of `bash`, `sh`, `zsh` and `dash -c` strings up to depth 3. Any single-dash letters-only word containing p is treated as -p, so clusters with unknown letters and words like -prefix, -print, -pretty, -help are over-denied on a dirty prompt: harmless, listed under known false positives.

Nobody should rely on the hook beyond its reach: the hook only looks at the FIRST word of a command (after the wrapper words it knows and inside `bash|sh|zsh|dash -c` strings), so these are NOT covered:
- subshells and groups `( ... )`, `{ ...; }`
- compound statements (`if/for/while ... do ... done`, `! cmd`)
- command substitutions around claude (`x=$(claude ...)`, `echo "$(claude ...)"`)
- `eval`
- wrapper OPTION forms it does not know (`sudo --user root`, `env -C/--chdir/--unset/-S`, `exec -a name`, `stdbuf`, `ionice`, `time -f`)
- other spellings of the program (`claude.exe`, `claude.cmd`, `npx claude`)
- `su -c` and `script -qc`
- `bash -O opt -c` / `bash +o opt -c` (options with an argument before -c)
- `bash -c $'...'`
- a line continuation without indent before the flag
- an outer double-quoted `bash -c "claude -p '\`id\`'"` whose backtick the OUTER shell expands
- claude started through xargs/ssh/other unknown wrappers
- a prompt passed through a variable
- here-strings <<<
- process substitution <( )
- author-written substitutions outside a heredoc or claude prompt
- -c nesting deeper than 3
- a raw NUL byte inside the JSON command string makes the payload invalid JSON and the guard fails open (Claude Code escapes control characters, so this is theoretical)

Known false positives (over-deny is the safe direction):
- text in a comment (`# ... $(x)`)
- redirect targets (`> "$(date).out"`)
- `$$(id)` and arithmetic `$((1+2))` inside an unquoted heredoc body
- clusters with unknown letters and words like `-prefix`, `-print`, `-pretty`, `-help` on a dirty prompt

## Tests

[`tests/test_bash_guard.py`](../../tests/test_bash_guard.py) runs the hook
as a real subprocess with its own PreToolUse JSON on stdin — the same
transport Claude Code uses — and checks the exit code and stderr text for a
table of deny and allow cases, including every false-positive guard named
above and the exact incident shape (an unquoted heredoc with a backticked
`netplan apply` in its prose body).

```bash
python3 tests/test_bash_guard.py
```
