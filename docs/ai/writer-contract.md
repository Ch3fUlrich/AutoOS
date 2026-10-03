# writer contract — the rule every writer task carries

## Purpose

`WRITER_RULE` in `tools/diff_guard.py` is the rule that forbids gaming a
test, held as ONE reviewed text so task authors paste the same wording into
every brief and no human ever retypes it. It is data, not prose to rewrite.

## The rule

```text
Never change production behaviour to satisfy a test and never edit tests outside the write scope. If a test fails, report it. Do not add equality or hash overrides to subclasses of builtin types, do not detect that a test is running (no checks for the test runner, its environment variables or loaded modules), do not special-case test inputs or expected values, and do not weaken, skip or delete a test or a guard. Report the failure instead.
```

The canonical text is whatever `python3 tools/diff-guard.py --writer-rule`
prints (exit 0, the rule plus one newline, nothing on stderr) — copy it from
there, never from memory. This file's copy is asserted byte-for-byte against
`WRITER_RULE` by `tests/test_diff_guard.py`, so the doc cannot drift.

## Where it must appear

- **Every writer task text**: append the rule verbatim as the **last
  paragraph** of the brief.
- **Seat prompts** carry the standing question of `tools/review-call.py`
  instead — do not duplicate this rule there.

## Post-run step

```sh
python3 tools/diff-guard.py --base <start> --tip <end> --scope <the write-scope globs>
```

Paste its `R1`-`R5` lines into every seat prompt. A finding is a candidate
to review, never an automatic verdict.

## Not yet in the spawner's built-in brief

The spawner's built-in brief does **not** carry the rule yet: a follow-up
lane has to add it there, in `tools/autoos-agent.py`, function
`isolate_task_prefix`. This task does NOT change it.
