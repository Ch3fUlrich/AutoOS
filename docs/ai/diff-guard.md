# diff-guard — report-only post-run diff guard

## Purpose

Compare two git revisions after a run and report suspicious drift. The guard is
**report-only**: it reads the two revisions with `git diff` and `git show`,
prints findings, and never edits anything. It exists so a reviewer can see,
in one pass, whether a run changed tests (or risky source) somewhere it should
not have. It cannot decide — it only finds candidates for review.

## Usage

```sh
python3 tools/diff-guard.py --base <rev> --tip <rev> --scope <glob> \
    [--scope <glob> ...] [--repo <dir>] [--json]
```

- `--scope` (repeatable, required): path glob the change is allowed in.
  `**` matches any depth, `*`/`?` stay within one path segment; a pattern
  without a `/` matches the file's basename. Without a match, rule R1 fires.
- `--repo` defaults to the current directory.
- Output: one `RULE path:line text` line per finding (text clipped to 160
  chars, always one line), or a JSON list of `{rule, path, line, text}`.
- Exit codes: `0` clean, `1` findings, `2` usage error or git failure
  (a git failure always prints a `diff-guard:` message — never a silent pass).
- Python 3.9+, standard library only; the tool can be run from any cwd.

## Writer rule

`WRITER_RULE` (in `tools/diff_guard.py`) is the one reviewed rule every
writer task brief must carry verbatim; `python3 tools/diff-guard.py
--writer-rule` prints it and exits 0 without needing `--base/--tip/--scope`,
so nobody retypes it. Placement, the post-run step and the follow-up lane:
`docs/ai/writer-contract.md`.

## Rules

- **R0** `path:1 unparsable` — a tip Python source failed to `ast.parse`;
  R2 is skipped for it, the run does not crash.
- **R1** `path:1 test file changed outside scope (X)` — a test file
  (`tests/**`, `test_*.py`, `*_test.py`, `*.Tests.ps1`, `tests/linux/*.sh`)
  was added/modified/deleted/renamed (code `X`) outside every `--scope` glob.
- **R2** `path:N class C(bases) defines m1, m2` — a non-test Python file
  gained a class on a builtin basis (`tuple`, `list`, `dict`, `str`, `int`,
  `float`, `set`, `frozenset`, `bytes`) that defines `__eq__`/`__ne__`/
  `__hash__`; custom equality there silently drops the implicit `__hash__`
  (the tuple-subclass inventory-page incident).
- **R3** `path:N <line>` — a non-test Python file gained a line referencing
  `pytest`/`unittest`/`PYTEST_CURRENT_TEST`/`importorskip`, `sys.modules`
  plus a test-ish name, or an `os.environ` quoted key containing `TEST`.
- **R4** in a test file: `path:N <line>` for an added skip/xfail marker;
  `assert lines removed N > added M` when removals outnumber additions;
  `assert numeric literal grew: 3 -> 5` when an added assert's number grew
  against the removed twin in the same hunk with the rest of the line
  identical.
- **R5** `path:N <def line>` — a test definition (`def test_...`,
  indented `def test_...`, `it "..."`, `Test-Case '...'`) was removed and no
  identical line was re-added anywhere in the same file.

## Traps

- The rules are **heuristics and produce false positives** — a comment that
  mentions `pytest` in non-test code, a `skip` substring in an ordinary line,
  a deliberately grown literal. The reviewer judges each finding; none of
  them blocks anything by itself (exit 1 is "here is the report", not "fail").
- R2/R3/R0 read the file as of `--tip` and only when the entry added lines;
  R4/R5 only look at test-file endpoints (rename check feeds both endpoints,
  change detection keying prefers the new path).
- Every finding's text is clipped to 160 characters on a single line.

## Known limits

- R2 resolves no aliases: `import builtins`/`builtins.tuple` or a
  module-level `Base = tuple` alias of a builtin base is not seen.
- Every rule looks at added lines only; a pre-existing issue is never flagged.
- A `--scope` glob with no `/` matches the file's basename in any directory.
