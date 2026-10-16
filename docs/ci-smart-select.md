# Smart CI: only what the diff needs, the full suite once before merge

A small change should not pay for the whole suite. `smart-ci.yml` is one shared,
reusable workflow that reads the PR diff and a per-repo **map** and answers: which
jobs and which tests does this change need? The caller's own jobs are gated on that
answer. Nothing here runs a test; it only decides.

| Event | What runs |
|---|---|
| PR push | the jobs and tests the diff maps to, plus the map's `always_jobs` (lint) |
| PR with the `merge-ready` label, or a dispatch with `full=true` | the **full** suite |
| push to the default branch, any non-PR event | the **full** suite |
| workflow, dependency or CI file changed | the **full** suite |
| a path no map rule names (unknown) | the **full** suite |
| map missing or broken, diff unreadable, selector failed | the **full** suite |
| docs-only (all paths in `ignore`) | nothing |

There is no nightly run. Two checks are required for merge: the `CI merge gate` job
(red only when a job failed or was cancelled) and the commit status
`Full suite (merge-ready)`. A selected-only PR run leaves that status **pending**
(yellow, not red) and blocks merge; the full run (label or dispatch) sets it
success, a failing job sets it failure.

## Adopting it in a caller repo

1. Add `.github/smart-ci.json` (format below).
2. Add the selector call and gate your jobs. `<owner>/AutoOS` is the repository
   that holds this workflow and `tools/smart-ci/selector.py`; pin `@<ref>` to a
   release ref or SHA you trust.

```yaml
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, labeled]
  workflow_dispatch:
    inputs:
      full: {type: boolean, default: true}

concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  smart:
    uses: <owner>/AutoOS/.github/workflows/smart-ci.yml@<ref>
    with:
      selector_repo: <owner>/AutoOS
      selector_ref: <ref>
      runs_on: ${{ toJSON(<your existing runs-on expression, fork guard included>) }}
      full: ${{ github.event_name == 'workflow_dispatch' && inputs.full }}

  tests:
    needs: smart
    # `full == 'true'` first: if the selector could not even run, jobs is {} and
    # that term keeps the fallback on the safe side.
    if: ${{ needs.smart.outputs.full == 'true' || fromJSON(needs.smart.outputs.jobs).tests }}
    runs-on: <as before>
    env:
      SELECTED: ${{ needs.smart.outputs.tests }}   # space separated; empty = everything
      FULL: ${{ needs.smart.outputs.full }}
    steps:
      - uses: actions/checkout@v4
      - run: |
          # $SELECTED is split on spaces on purpose: map paths must not contain spaces
          if [ "$FULL" = true ] || [ -z "$SELECTED" ]; then pytest; else pytest $SELECTED; fi

  heavy:                       # a heavy job: runs only when its own paths changed
    needs: smart
    if: ${{ needs.smart.outputs.full == 'true' || fromJSON(needs.smart.outputs.jobs).heavy }}
    ...

  merge-gate:                  # required check, together with the status it sets
    name: CI merge gate
    if: always()
    needs: [smart, tests, heavy]
    runs-on: <as before>
    permissions: {contents: read, statuses: write}
    steps:
      - env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          SHA: ${{ github.event.pull_request.head.sha || github.sha }}
          EVENT: ${{ github.event_name }}
          FULL: ${{ needs.smart.outputs.full }}
          RESULTS: smart=${{ needs.smart.result }} tests=${{ needs.tests.result }} heavy=${{ needs.heavy.result }}
        run: |
          ctx="Full suite (merge-ready)"   # curl, not gh: runner images may lack gh
          post() {
            printf 'Authorization: Bearer %s\n' "$GH_TOKEN" | curl -fsS -X POST -H @- -H "Accept: application/vnd.github+json" \
              "${GITHUB_API_URL:-https://api.github.com}/repos/$REPO/statuses/$SHA" \
              -d "{\"state\":\"$1\",\"context\":\"$ctx\",\"description\":\"$2\"}" >/dev/null || true
          }
          for pair in $RESULTS; do
            name="${pair%%=*}"; r="${pair#*=}"
            case "$r" in
              success) ;;
              skipped) if [ "$FULL" = true ]; then post failure "full run but $name was skipped"; exit 1; fi ;;
              *) post failure "$name ended $r"; exit 1 ;;
            esac
          done
          if [ "$EVENT" = pull_request ] && [ "$FULL" != true ]; then post pending "selected-only: add the merge-ready label"
          elif [ "$FULL" = true ]; then post success "full suite passed"
          else post failure "not a full run"; exit 1; fi
```

AutoOS's own `.github/workflows/ci.yml` is the worked example (it also trims the
shard matrix with `tests/ci-shards.py --only`). The `labeled` trigger re-runs CI
for any label; only `merge-ready` (input `merge_label`) makes the run full, and
`concurrency` cancels the superseded run.

Outputs of `smart`: `mode` (`full`/`selected`/`none`), `full`, `reason`, `jobs`
(JSON object, job name to bool), `tests` (space separated), `tests_json`.

For python, pytest-testmon is an alternative to the path map: it needs a cache that
survives between runs, which self-hosted slots do not guarantee, so the committed
map is the default. A repo may put `--testmon` inside its own `tests` job and keep
the map for job selection.

Caveats. The `Full suite (merge-ready)` status is a process guard, not a security
boundary: anyone whose token can write commit statuses can set it. A fork PR gets a read-only token, so it can never write the
`Full suite (merge-ready)` status (blocked, not unsafe: it stays unset). The status
is posted to the PR head SHA while the run tested the merge ref, so a push to the
base branch after the run does not invalidate it. In the gate, a skipped job in a
full run is red, so a job that is deliberately off must be named there (AutoOS
allows Windows to be skipped until the repository variable `CI_WINDOWS_ENABLED` is
`true`). Single-job repos gain little on source changes (the install step dominates);
the saving is docs-only and test-only changes, and the skipped heavy jobs.
Pass `results` as `name=result` pairs so the gate can tell which job it was.

## The map: `.github/smart-ci.json`

```json
{
  "version": 1,
  "jobs": ["lint", "pytest", "pg", "rust"],
  "always_jobs": ["lint"],
  "ignore": ["docs/**", "**/*.md"],
  "full_if": ["src/core/**"],
  "rules": [
    {"paths": ["src/api/**"], "tests": ["tests/test_api.py"], "jobs": ["pytest"]},
    {"paths": ["src/db/**"],  "tests": ["tests/test_db.py"],  "jobs": ["pytest", "pg"]},
    {"paths": ["tests/test_*.py"], "tests": ["{path}"], "jobs": ["pytest"]},
    {"paths": ["crates/**"], "jobs": ["rust"]}
  ]
}
```

| Key | Meaning |
|---|---|
| `jobs` | every job name the caller gates on |
| `always_jobs` | jobs that run whenever anything non-ignorable changed (lint, secret scan) |
| `ignore` | paths that need nothing (docs); all-ignored diff gives mode `none` |
| `full_if` | extra patterns that force a full run, on top of the built-in ones |
| `rules[].paths` | globs a changed file must match |
| `rules[].tests` | tests to run for it; `{path}` = the changed file itself; a caller-defined token such as `*` is passed through untouched |
| `rules[].jobs` | jobs to turn on (must be in `jobs`) |

Globs are anchored at the repo root: `**` crosses directories, `*` and `?` do not
(no `[...]` classes). Every changed file must be ignored or match a rule: one
unplaced file makes the run full, so a new directory is slow until it is mapped,
never unchecked. Keep the map conservative: when unsure, leave the path out.

Built-in full triggers (always on): `.github/workflows/**`, `.github/actions/**`,
the map, `tools/smart-ci/**`, requirements / constraints / pyproject / setup /
uv.lock / poetry.lock / Pipfile, package.json and lockfiles, Cargo.toml and
Cargo.lock, rust-toolchain, go.mod / go.sum, Gemfile(.lock), `Dockerfile*`,
`*.csproj`, `Directory.Packages.props`.

## The selector

```bash
python3 tools/smart-ci/selector.py --map .github/smart-ci.json --base <sha> --head <sha>
python3 tools/smart-ci/selector.py --map .github/smart-ci.json --files a.py b/c.rs
python3 tools/smart-ci/selector.py --map .github/smart-ci.json --full --full-reason "label merge-ready"
```

It is stdlib-only, prints the JSON answer, appends outputs to `$GITHUB_OUTPUT`
with `--github-output`, and always exits 0: a failure to select becomes a full run.
Its tests are `tests/test_smart_ci.py` (run by `tests/run-tests.sh` and
`tests/run-tests.ps1`).
