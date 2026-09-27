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

## Channels

Two channels for worker → orchestrator communication.

### Ask-back helper (`autoos-ask.py`)

The primary channel: a worker runs `python3 tools/autoos-ask.py "question?"` and blocks
for an answer via `respond()`. Files involved:

```
question.json → answer.json → qa-<n>.json  (archived exchange)
```

Needs a shell tool and `AUTOOS_TASK_DIR` (the MCP spawn sets it). Measured 2026-09-27 through
MCP spawn:

| worker | ask-back | evidence |
|---|---|---|
| opencode `big-pickle` (spawner `--free` default) | works | asked, `respond()` answered, REPORT completed |
| opencode `omniroute/deepseek-v4.1-flash` | works | same |
| opencode `omniroute/spark-1.3-contributor` (Muse Spark 1.3) | works | same |
| qoder (`--permission-mode dont_ask`, no shell) | no | printed the stdout QUESTION + REPORT `input_required` |
| agy (default model) | not measured | Gemini quota 429 before the task ran |

### Stdout channel

Workers that cannot use the ask-back helper print tagged messages to stdout:

```
QUESTION <worker-name>: <text>
REPORT <id> · <status> · <files> · <tests> · <blockers> · <lessons>
```

The MCP server reads the tail of `output.log` (last 64 KiB) with `_stdout_channel()` once the run
has exited. An rc-0 run that printed a `QUESTION` line among its last 12 non-blank lines (an
earlier QUESTION-shaped line in tool output does not count), or a REPORT with status `input_required`
(its blockers become the question), and never used the ask-back helper (no `qa-*.json`) is
`state: input_required, detail: ended`. A REPORT with status `failed` turns an rc-0 run into
`failed / reported-failed`. Any parsed REPORT is attached to the state as `report`.

The worker has already exited, so `respond()` refuses such a run: spawn a follow-up task that
includes the answer.

### Fallback file

Only when the primary channel failed (the `input_required / ended` case above), the runner
writes `<run id>.question.md` - the question, then `report: <raw REPORT line>` - into
`$AUTOOS_FALLBACK_DIR` (point it at `RUN/work/<lane>/`), else into the run dir. It never
overwrites an existing file, and a failed write never fails the run.
