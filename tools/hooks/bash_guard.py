#!/usr/bin/env python3
"""Claude Code PreToolUse guard for the Bash tool (FLEET-HOOKS, P0).

Incident this closes: a seat prompt was built with an UNQUOTED heredoc
(``<<EOF``) whose prose body happened to contain backticked shell commands.
On a host running sessions in bypass mode (no classifier in front of Bash),
the shell executed that prose as commands the moment the heredoc was read -
including a `netplan apply` nobody asked for. An unquoted heredoc expands
backticks and ``$(...)`` in its body exactly like a double-quoted string
does; a quoted delimiter (``<<'EOF'``, ``<<"EOF"``, ``<<\\EOF``) suppresses
that expansion entirely, which is the fix this guard enforces.

Reads one PreToolUse hook JSON object from stdin (``{"tool_name": "Bash",
"tool_input": {"command": "..."}, ...}``, the shape Claude Code sends) and
denies (stderr message, exit 2) exactly two shapes:

1. An unquoted heredoc whose body contains a literal backtick or ``$(`` -
   the incident shape above.
2. A `claude` invocation carrying `--bg` (background) or `-p`/`--print`
   (non-interactive) whose double-quoted argument contains a backtick or
   ``$(`` - the same hazard one layer up: a backgrounded or one-shot `claude`
   call whose prompt string is built with an unquoted-feeling double-quoted
   argument lets the CALLING shell, not claude, expand the substitution
   before claude ever sees the text.

Everything else allows silently (exit 0, no output) - a guard that prints on
every ordinary command is noise nobody will read before disabling it. A
malformed or unexpected hook payload fails OPEN (exit 0) with one stderr
note: this is a shell gate, not a sandbox, and a bug here must never be the
thing that bricks an interactive session.

This module intentionally re-implements two small, independent scanners
rather than reach for a real shell grammar: one for heredoc operators (see
``_heredoc_operators_in_line``), one for splitting a command into top-level
simple commands to find `claude` invocations (see
``_split_top_level_commands``). Both track quote state and `(`/`$(`/`$((`
nesting with the same small stack idea, kept separate because they answer
different questions and a shared "generic parser" would hide exactly the
one-character details (`<<<` vs `<<`, `((` vs `$((`) that make the
difference between a real hazard and `echo "a << b"`.

Run directly for a smoke check:

    echo '{"tool_name":"Bash","tool_input":{"command":"cat <<EOF\n`id`\nEOF"}}' \
        | python3 tools/hooks/bash_guard.py; echo "exit=$?"
"""
import json
import re
import sys

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_CLAUDE_BG_OR_PRINT_FLAGS = ("--bg", "-p", "--print")
_SEPARATORS = ";&|\n"


# ─── heredoc scanner ────────────────────────────────────────────────────

def _parse_heredoc_word(line, j):
    """Parse the delimiter word for a `<<`/`<<-` operator starting at `j`.

    Returns (delimiter_text, quoted, new_index). `quoted` is True the moment
    ANY part of the word was single-quoted, double-quoted or backslash-
    escaped - bash's own rule for "this heredoc gets no expansion", matching
    <<'EOF'/<<"EOF"/<<\\EOF alike and leaving bare <<EOF as unquoted.
    """
    n = len(line)
    out = []
    quoted = False
    while j < n:
        c = line[j]
        if c == "'":
            quoted = True
            j += 1
            while j < n and line[j] != "'":
                out.append(line[j])
                j += 1
            if j < n:
                j += 1
            continue
        if c == '"':
            quoted = True
            j += 1
            while j < n and line[j] != '"':
                if line[j] == "\\" and j + 1 < n:
                    out.append(line[j + 1])
                    j += 2
                else:
                    out.append(line[j])
                    j += 1
            if j < n:
                j += 1
            continue
        if c == "\\":
            quoted = True
            if j + 1 < n:
                out.append(line[j + 1])
                j += 2
            else:
                j += 1
            continue
        if c in " \t;&|<>\n":
            break
        out.append(c)
        j += 1
    return "".join(out), quoted, j


def _heredoc_operators_in_line(line):
    """Every real `<<`/`<<-` heredoc operator in one physical line, in
    order, as (delimiter, quoted, dash) - skipping occurrences that are not
    heredoc operators at all:

    - inside single or double quotes (`echo "a << b"`)
    - `<<<` here-strings (a different operator, no body to scan)
    - the `<<` left-shift operator inside `$((...))`/`((...))` arithmetic
    - after a `#` comment that starts a shell word

    A heredoc operator INSIDE an unquoted `$(...)` command substitution is
    still a real heredoc (bash reads it there) and is still reported - only
    arithmetic context turns `<<` into something else.
    """
    ops = []
    i, n = 0, len(line)
    in_single = False
    in_double = False
    stack = []  # top-of-stack 2 = needs "))" to close (arith), 1 = needs ")"
    while i < n:
        c = line[i]
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_double = False
                i += 1
                continue
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "#" and (i == 0 or line[i - 1] in " \t;|&(\n"):
            break
        if line[i:i + 3] == "$((":
            stack.append(2)
            i += 3
            continue
        if line[i:i + 2] == "((":
            stack.append(2)
            i += 2
            continue
        if line[i:i + 2] == "$(":
            stack.append(1)
            i += 2
            continue
        if c == "(":
            stack.append(1)
            i += 1
            continue
        if c == ")":
            if stack:
                needs = stack[-1]
                if needs == 2 and line[i:i + 2] == "))":
                    stack.pop()
                    i += 2
                    continue
                stack.pop()
            i += 1
            continue
        if c == "<":
            if line[i:i + 3] == "<<<":
                i += 3
                continue
            if line[i:i + 2] == "<<":
                if stack and stack[-1] == 2:
                    i += 2  # the shift operator inside arithmetic
                    continue
                j = i + 2
                dash = False
                if j < n and line[j] == "-":
                    dash = True
                    j += 1
                while j < n and line[j] in " \t":
                    j += 1
                delim, quoted, j = _parse_heredoc_word(line, j)
                if delim:
                    ops.append((delim, quoted, dash))
                i = j
                continue
            i += 1
            continue
        i += 1
    return ops


def _find_unquoted_heredoc_with_injection(command):
    """The delimiter of the first UNQUOTED heredoc whose body contains a
    backtick or `$(`, or None. Heredoc bodies are consumed line by line
    starting right after the line that carries the operator, in the order
    the operators appear, and end at the first line matching the delimiter
    (leading tabs stripped first for `<<-`) - text after that line is never
    body, even if the same delimiter text reappears later.
    """
    lines = command.split("\n")
    i, n = 0, len(lines)
    while i < n:
        ops = _heredoc_operators_in_line(lines[i])
        i += 1
        for delim, quoted, dash in ops:
            body = []
            while i < n:
                candidate = lines[i]
                i += 1
                check_line = candidate.lstrip("\t") if dash else candidate
                if check_line == delim:
                    break
                body.append(candidate)
            if quoted:
                continue
            text = "\n".join(body)
            if "`" in text or "$(" in text:
                return delim
    return None


# ─── claude --bg / -p / --print scanner ────────────────────────────────

def _split_top_level_commands(command):
    """Split on `;`, `&`, `|` and newline OUTSIDE quotes and OUTSIDE any
    unquoted `(`/`$(`/`$((` nesting, so a separator inside an unquoted
    command substitution (`$(a; b)`) does not split the command that
    contains it. Quoted separators never split at all - the quote branches
    below never consult `stack`."""
    parts = []
    buf = []
    i, n = 0, len(command)
    in_single = False
    in_double = False
    stack = []
    while i < n:
        c = command[i]
        if in_single:
            buf.append(c)
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\" and i + 1 < n:
                buf.append(c)
                buf.append(command[i + 1])
                i += 2
                continue
            buf.append(c)
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(command[i + 1])
            i += 2
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_double = True
            buf.append(c)
            i += 1
            continue
        if not stack and c in _SEPARATORS:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        if command[i:i + 3] == "$((":
            stack.append(2)
            buf.append(command[i:i + 3])
            i += 3
            continue
        if command[i:i + 2] == "((":
            stack.append(2)
            buf.append(command[i:i + 2])
            i += 2
            continue
        if command[i:i + 2] == "$(":
            stack.append(1)
            buf.append(command[i:i + 2])
            i += 2
            continue
        if c == "(":
            stack.append(1)
            buf.append(c)
            i += 1
            continue
        if c == ")":
            if stack:
                needs = stack[-1]
                if needs == 2 and command[i:i + 2] == "))":
                    stack.pop()
                    buf.append(command[i:i + 2])
                    i += 2
                    continue
                stack.pop()
            buf.append(c)
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _tokenize_args(segment):
    r"""Word-split one simple command, quote-aware. Returns a list of
    (word_text, had_unescaped_backtick_or_cmdsub_in_double_quotes) pairs -
    the second element is True only when an UNESCAPED backtick or `$(`
    occurred literally inside a double-quoted part of that word; an escaped
    one (`\\`` / `\$(`) is deliberately excluded, and a single-quoted or
    bare `$VAR` carries no trigger at all."""
    words = []
    i, n = 0, len(segment)
    cur = []
    cur_trigger = False
    started = False
    while i < n:
        c = segment[i]
        if c in " \t\n":
            if started:
                words.append(("".join(cur), cur_trigger))
                cur = []
                cur_trigger = False
                started = False
            i += 1
            continue
        started = True
        if c == "'":
            j = segment.find("'", i + 1)
            if j == -1:
                cur.append(segment[i + 1:])
                i = n
            else:
                cur.append(segment[i + 1:j])
                i = j + 1
            continue
        if c == '"':
            j = i + 1
            trig = False
            while j < n and segment[j] != '"':
                if segment[j] == "\\" and j + 1 < n:
                    cur.append(segment[j + 1])
                    j += 2
                    continue
                if segment[j] == "`":
                    trig = True
                elif segment[j] == "$" and j + 1 < n and segment[j + 1] == "(":
                    trig = True
                cur.append(segment[j])
                j += 1
            if trig:
                cur_trigger = True
            i = j + 1 if j < n else n
            continue
        if c == "\\" and i + 1 < n:
            cur.append(segment[i + 1])
            i += 2
            continue
        cur.append(c)
        i += 1
    if started:
        words.append(("".join(cur), cur_trigger))
    return words


def _basename(token):
    return token.replace("\\", "/").rsplit("/", 1)[-1]


def _find_claude_bg_or_print_with_quoted_injection(command):
    """The matched flag (`--bg`, `-p` or `--print`) of the first `claude`
    invocation that carries both that flag AND a double-quoted argument
    with an unescaped backtick or `$(` in it, else None. Flag and the
    injected argument may appear in either order."""
    for segment in _split_top_level_commands(command):
        words = _tokenize_args(segment)
        idx = 0
        while idx < len(words) and _ENV_ASSIGN_RE.match(words[idx][0]):
            idx += 1
        if idx >= len(words):
            continue
        if _basename(words[idx][0]) != "claude":
            continue
        rest = words[idx + 1:]
        flag_hit = next((w for w, _trig in rest if w in _CLAUDE_BG_OR_PRINT_FLAGS), None)
        if not flag_hit:
            continue
        if any(trig for _w, trig in rest):
            return flag_hit
    return None


# ─── hook entry point ───────────────────────────────────────────────────

def check_command(command):
    """The deny message for `command`, or None to allow."""
    delim = _find_unquoted_heredoc_with_injection(command)
    if delim is not None:
        return (
            "bash-guard: DENIED - unquoted heredoc <<%s whose body contains a "
            "backtick or $(...) command substitution; the shell expands that "
            "before the command even runs (the netplan-apply incident shape). "
            "Quote the delimiter if this is prose, not a command: <<'%s'."
            % (delim, delim)
        )
    flag = _find_claude_bg_or_print_with_quoted_injection(command)
    if flag is not None:
        return (
            "bash-guard: DENIED - claude %s carries a double-quoted argument "
            "containing a backtick or $(...) command substitution; the CALLING "
            "shell expands that before claude ever sees the prompt. Use single "
            "quotes, or pass the prompt from a file, instead." % flag
        )
    return None


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception as exc:
        sys.stderr.write(
            "bash-guard: could not parse hook input as JSON, allowing (%s)\n" % exc)
        return 0
    if not isinstance(payload, dict):
        sys.stderr.write("bash-guard: hook input is not a JSON object, allowing\n")
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.stderr.write("bash-guard: Bash tool_input missing or malformed, allowing\n")
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0
    try:
        reason = check_command(command)
    except Exception as exc:
        sys.stderr.write("bash-guard: internal parse error, allowing (%s)\n" % exc)
        return 0
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
