# Agent protocol — BRIEF and REPORT messages

Routing v2 spec §8.2: terse, fixed-field messages between orchestrator and worker. Bulky output (logs, diffs, test output) goes to files; messages carry paths.

## Templates

### BRIEF (orchestrator → worker)

Single-line form (fields separated by `·`):

```
BRIEF <id> · <goal> · <paths> · <spec> · <done-when> · skills: <names> · <effort> · <budget>
```

Multi-line form:

```
BRIEF <id>
goal: <text>
paths: <path1>; <path2>
spec: exact|partial|vague
done-when: <text>
skills: <skill1>; <skill2>
effort: trivial|medium|hard
budget: <number>
```

### REPORT (worker → orchestrator)

Single-line form:

```
REPORT <id> · <status> · <files> · <tests> · <blockers> · <lessons>
```

Multi-line form:

```
REPORT <id>
status: completed|failed|input_required
files: <file1>; <file2>
tests: <cmd> -> <result>; <cmd> -> <result>
blockers: <text>
lessons: <text>
```

## Examples

### BRIEF example

```
BRIEF fix-auth-bug · fix the login token refresh · src/auth/ · exact · tests pass · skills: coding-principles · medium · 10
```

Or multi-line:

```
BRIEF fix-auth-bug
goal: fix the login token refresh
paths: src/auth/
spec: exact
done-when: tests pass
skills: coding-principles
effort: medium
budget: 10
```

### REPORT example

```
REPORT fix-auth-bug · completed · src/auth/token.py; tests/test_token.py · pytest -> pass; mypy -> clean · · refresh logic now handles expired tokens
```

Or multi-line:

```
REPORT fix-auth-bug
status: completed
files: src/auth/token.py; tests/test_token.py
tests: pytest -> pass; mypy -> clean
blockers:
lessons: refresh logic now handles expired tokens
```

## Parser

`tools/autoos_report.py` parses both forms from free text (workers print prose around the block). It finds the **last** `BRIEF` or `REPORT` block in the input.

CLI:

```bash
# Parse a report file or stdin
python3 tools/autoos_report.py parse report.txt
python3 tools/autoos_report.py parse -

# Check a report against the actual diff (§5.7 gate)
python3 tools/autoos_report.py check report.txt --changed $(git diff --name-only base..HEAD)
```

The arguments after `--changed` are the changed file names themselves, not a file that lists
them; `--changed` with nothing after it means nothing changed.

The `check` subcommand verifies:
- Files claimed in the report match the diff (no extra, no missing)
- Status `completed` includes at least one test
- Exit 0 if ok, 1 if problems found

## Field semantics

### BRIEF fields

- **id**: stable task identifier (kebab-case)
- **goal**: one-line description
- **paths**: files/dirs the task may touch (semicolon-separated)
- **spec**: `exact` | `partial` | `vague` (feeds complexity bucket §5.2)
- **done-when**: acceptance criteria
- **skills**: skill names the worker should load (semicolon-separated)
- **effort**: `trivial` | `medium` | `hard`
- **budget**: token or USD budget (number)

### REPORT fields

- **id**: matches the BRIEF id
- **status**: `completed` | `failed` | `input_required`
- **files**: files changed (semicolon-separated)
- **tests**: test commands and results (`cmd -> result`, semicolon-separated; split on `->` or `→`)
- **blockers**: what stopped progress (semicolon-separated, empty if none)
- **lessons**: what was learned (semicolon-separated)

## Gate (§5.7)

"The report's claims match the diff" — `check_report(report, changed_files)` compares the report's `files` list against the actual diff and flags:
- Files claimed but not in the diff
- Files in the diff but not claimed
- Status `completed` with no tests

The spawner calls this before accepting a report.
