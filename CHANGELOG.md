# Changelog

All notable changes to AutoOS are recorded here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed - the secret gate reads a padded token; the backup CLI's stamp really is optional (A3 review 5, LOW 1-2, 2026-09-28)

- **`lib/linux/install.sh`** (`file_holds_omnigraph_token`): the gate that decides
  whether a backup goes through `secret_backup.py` (private, born 0600) or
  `cp -p` (the source's own mode) matched `OMNIGRAPH_TOKEN=[^[:space:]]` — a
  non-space *immediately* after the `=` — while every reader of these files
  decides on the *trimmed* value (`tools/omnigraph-mcp-autoos.sh` and the rc line
  `install.sh` writes strip the whitespace around it; `has_token` in
  `omnigraph_env_state` compares the stripped line). So `OMNIGRAPH_TOKEN=   secret`,
  `export OMNIGRAPH_TOKEN= secret` and a tab after `export ` were live credentials
  to the wrapper and invisible to the gate: that file took the `cp -p` branch and
  left a 0644 copy of the bearer token behind the edit that removed the line. The
  gate now uses the readers' rule — non-empty after trimming — so bare `KEY=`, a
  whitespace-only value and `KEY = value` (not an assignment, and the readers skip
  it) still get an ordinary backup that keeps the user's own mode. A quoted value
  counts as a token even when it is `""`: the gate does not strip quotes, and the
  error is only ever in the direction of a more private copy.
- **`lib/linux/secret_backup.py`** (`main`): `argv[2]` was read unguarded, so the
  call the usage line itself documents as optional — `secret_backup.py <path>` —
  died with an `IndexError` traceback and exit 1. `backup_file_before_write` turns
  any non-zero from the helper into "could not back up", so the defect would have
  made the rc-file edit refuse to run at all rather than take the copy. The stamp
  is now read only when it is there, and defaults to the clock exactly as the
  in-process caller does.
- Tests (`tests/linux/38-omnigraph-client.sh`): the gate is asserted *against the
  shipped wrapper's own verdict* per form — one rule, two consumers, so the
  definitions cannot drift again without a test noticing — and the CLI is called
  with the stamp omitted, passed empty (the shell call site's shape), and with too
  many arguments (still the usage error).

### Fixed - token-bearing files: backups and temp files (A3 review 4, S1-S2, 2026-09-28)

- **`lib/linux/secret_backup.py`** (new, `lib/linux/install.sh` uses it from both
  sides): the one implementation of "back up a file that holds a live credential".
  It creates the copy `O_CREAT | O_EXCL` at `0600` - mode and exclusivity in the
  same syscall - copies the bytes in, carries only the source's *times* across
  (`copystat` would copy the mode too and so would widen it), keeps the
  repository's `<path>.autoos-backup-<stamp>[-N]` name shape, and leaves nothing
  behind when the copy fails. The shell reaches it through
  `backup_file_before_write`; `omnigraph_env_state` imports it inside the very
  process that renames the new bytes into place, because the copy of the old token
  has to land before the replace that destroys it, not in a second run that could
  disagree with the first about whether anything changed.
- **`lib/linux/install.sh`** (`backup_file_before_write`,
  `file_holds_omnigraph_token`, `append_line_once`,
  `omnigraph_retire_rc_token_lines`, `replace_or_append_marked_line`): the rc-file
  edits backed the user's dotfile up with `backup_file`, i.e. `cp -p`, which
  creates the destination with the *source's* mode - so a 0644 `.bashrc` got a 0644
  backup holding `OMNIGRAPH_TOKEN=...`, and that copy outlives the line the step
  deletes: the backup becomes the place the bearer token stays readable to every
  local user (and survives the rotation, and the checkout, and the machine). Every
  edit a token-bearing file can reach now takes its copy through
  `backup_file_before_write`, which hands that case to `secret_backup.py` and keeps
  `backup_file` - mode and times included - for every other file, unchanged for all
  its other callers. That is four sites, not the one the finding named: on a first
  run the rc file is opened by `append_line_once` (the current line is missing)
  *before* the retire step ever runs, so fixing only the retire step would have
  left the same leak on the path production takes first.
  This corrects the claim in the entry below that `cp -p` was clean: it is clean as
  to the *window* (coreutils creates the destination with the source's mode, so no
  group-readable instant exists), which is not the same question as whether the
  finished copy may be read - and a 0644 copy of a token is the leak either way.
- **`lib/linux/install.sh`** (`omnigraph_env_state`): the rewrite staged the new
  bytes at the fixed, guessable name `<path>.tmp`, opened `O_CREAT | O_TRUNC`.
  Anyone able to write in the home - another user on a shared or NFS box, a
  component that ran earlier - could plant a symlink there, and the step then
  truncated and overwrote the target they chose with the token's bytes before
  renaming that link onto `~/.autoos-omnigraph.env` itself (the new test reproduces
  exactly that: the env file came out a link and mode 777). Two AutoOS runs in one
  second shared the one name too. The temp is now `tempfile.mkstemp(dir=<dirname>,
  prefix=<basename>.autoos-tmp-)`: unpredictable, exclusive, `0600` from the birth,
  fsynced, renamed, and unlinked if anything in between fails - the same shape
  `lib/linux/serve.py`'s `write_secret` and the Claude Code settings writer already
  use.
- **`tests/linux/38-omnigraph-client.sh`**: four cases - the retire step under
  `umask 022` (a `sitecustomize` probe records the mode each backup is *born* with,
  so a `cp -p` followed by a `chmod 600` cannot pass), a sweep that reads every
  `.autoos-backup-*` the component run left behind and refuses any that holds a
  token line and is group- or world-readable, each of the three rc-writing branches
  (append, purge, replace), and the planted-symlink temp case. The existing
  env-backup case now asserts the exclusive `0600` creation across the writer *and*
  the shared helper, and that the writer imports it rather than re-typing it.
- Not changed here, and the same defect: `setup_opencode_config` stages
  `opencode.json.tmp` at a fixed name (`lib/linux/install.sh`, the writer that
  merges the api-key file) - a separate component, so a separate brief.

### Fixed - a token-bearing backup is created 0600 and never widened (A3 final review S1, 2026-09-28)

- **`lib/linux/install.sh`** (`omnigraph_env_state`): the backup taken before the
  env file is rewritten holds the **previous token**, still a live bearer
  credential until the server expires it, and `shutil.copy2` creates the
  destination with `open(dst, "wb")` — mode `0666 & ~umask`, i.e. **0644 on any
  normal machine** — and only tightens it *after* the bytes landed. On a shared or
  NFS home another local user could read the token inside that window (measured:
  the probe saw the backup born 0644). The writer now creates it with
  `os.open(path, O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` — the restrictive mode
  and the exclusive create in one syscall, so a same-second backup is never
  clobbered — copies the bytes in, and carries the times across itself
  (`os.utime`) rather than with `copystat`, which would have copied the mode too.
  `backup_file` (`cp -p`) was probed the same way and is clean: coreutils creates
  the destination with the source's mode, so no rc-file backup ever opens.
- **`lib/linux/install.sh`** (`replace_or_append_marked_line`): the replace branch
  assigned `rc=0` with no `local`, unlike the purge branch a few lines above it, so
  the step's exit status overwrote the *caller's* `rc` — the variable every step
  here uses to report a failed write.
- **`tests/linux/38-omnigraph-client.sh`**: a `sitecustomize` shadow hooks
  `builtins.open` and `os.open` (the idiom `32-answer-file-templates.sh` already
  uses) and asserts the mode every backup is created with under `umask 022`, that
  the backup holds the pre-edit bytes and the source's times, and — so a future
  rewrite that dodges both hooks cannot pass by accident — that the writer holds no
  `copy2` call and does create with `O_EXCL`. The `rc` leak is asserted
  behaviourally: a caller whose `rc` is a sentinel still holds it after the step.

### Fixed — the rc-line writer is contained and byte-safe; one value rule for all three readers (A3 review 2, 2026-09-28)

- **`lib/linux/install.sh`** (`replace_or_append_marked_line`): both of its
  heredocs read and wrote the rc file as strict UTF-8 *text*, unguarded. On an
  undecodable `.bashrc`/`.zshrc` the python died — aborting the run outright
  where errexit was live (measured: under `set -euo pipefail` the step never
  returned), and where `run_post_install` contained it, printing a traceback into
  the log, **announcing "replaced the … line" for a file it had not touched**,
  and recording nothing. A refused write was the same story. Editing one line of
  a dotfile also re-encoded the whole thing, converting every CRLF neighbour to
  LF. Both edits now go through one helper, `autoos_rc_edit_lines` — the
  byte-safe, contained form the retire step had already learned, and which gave
  up its private copy of that python. A file that cannot be written is a warning
  plus `autoos_record_failure`, the run continues, and a replacement line keeps
  the newline of the line it replaced.
- The same helper's `replace` mode now leaves **one** line. With two stale
  AutoOS lines in one file it wrote two copies of the new one — reachable for
  the first time through the version bump below.
- **The rc tag went `AutoOS:omnigraph-env-v2` → `-v3`**, with the tag held in one
  function (`omnigraph_rc_marker`). A bump is *required* whenever the rc line's
  shape changes: the writer recognises a line by the tag alone, so a changed body
  under an unchanged tag leaves every machine already carrying the old line
  sitting on it, while the gate — which compares the whole line — never reads
  current again. The tag without its `-vN` tail is still the older-line marker,
  so this one bump replaces v1 and v2 alike.
- **One value rule, three readers.** `KEY= value` produced a token with a leading
  space in `tools/omnigraph-mcp-autoos.sh` and a trimmed one in the `.ps1` twin,
  so one env file yielded different tokens per platform; a quoted value that was
  quoted *after* a space was never unquoted at all. The rule is *whitespace round
  a value is not part of it, then one layer of matching quotes goes*, applied
  identically by the shell wrapper, its PowerShell twin (which already had it)
  and the rc line install.sh writes — verified for all three under bash, zsh and
  `pwsh` in one test. Whitespace a quoted value keeps *inside* its quotes
  survives, as it must.

### Fixed — the `omnigraph-client` gate compares the values it would write; the retire step is contained and byte-safe (A3 review S2, 2026-09-28)

- **`lib/linux/install.sh`**: `omnigraph_client_is_current` (the skip gate
  `install_component` asks *before* any postInstall runs) compared "a token is in
  the file" and an unanchored `grep -F` of the URL — so a **rotated token stayed
  stale forever**, and a commented `# OMNIGRAPH_BASE_URL=…` or a
  `…invalid.evil` suffix URL read as current. It now compares the resolved URL
  *and* token, as whole `KEY=value` lines, plus the `environment.d` link, the rc
  line this run would write, and no retired line left; the env-file half is asked
  of `omnigraph_env_state … check`, the writer's own code path in a new mode, so
  the gate and the write cannot disagree, and the values reach python in the
  environment — never printed. A bridge pin with an empty `@version` (`name@`)
  no longer equals an empty installed version, and a **symlink** at the wrapper
  path is refused instead of counting as the copy it promises (a `cp` through
  such a link writes into the tracked checkout).
- **`lib/linux/install.sh`** (`omnigraph_retire_rc_token_lines`): the heredoc ran
  unguarded under the runner's `set -euo pipefail` and in text mode, so an
  undecodable dotfile printed a python traceback into the log, *claimed the line
  was removed* and left it there, and a read-only file aborted the step; the
  whole file was also re-encoded (CRLF neighbours converted to LF). It now edits
  bytes, is contained like every other step (warn +
  `autoos_record_failure omnigraph-client`, the run continues), and leaves every
  other line untouched.
- **`tools/omnigraph-mcp-autoos.sh`**, **`.ps1`**: hand-edited env lines —
  `export KEY=value`, an indent, `"…"`/`'…'` round a value — were kept literally
  or skipped, so the bridge started with no or wrong token; both twins now strip
  those forms (one layer of matching quotes, nothing else), ignore every key but
  the three, are CRLF-safe and still never evaluate a value. The env-file writer
  recognises the same forms, so a stale hand line is normalised rather than left
  to shadow the resolved value.
- **Disproved, not fixed**: `exec "$bridge" "$@"` with no arguments under
  `set -u` on bash 3.2 (stock macOS) — measured on real `bash:3.2.57` (the
  `bash:3.2` image): the wrapper starts the bridge with `argc=0`, token exported,
  exit 0, and `set -u; printf %s "$@"` with no positional parameters exits 0.
  The bash-4.4 nounset entry that is usually cited here covers `${a[@]}` on an
  *empty array* (which does fail on 3.2, verified), not `$@`; the portable
  `${1+"$@"}` form would be noise. A zero-arg case is now tested as a guard.
- **`tests/linux/38-omnigraph-client.sh`**: 11 cases added, each written against
  the bug first — env rotation via `$OMNIGRAPH_TOKEN` and via `api-keys.yml`,
  commented/suffix URL, deleted rc line, reappeared retired line, lost
  environment.d link, CRLF and non-UTF-8 rc files, unwritable rc file through
  `run_post_install` (the production shape), decorated env rows normalised,
  empty-pin refusal, symlinked wrapper refusal, export/quoted/CRLF/injection
  parsing and the zero-arg start.

### Added — `omnigraph-client`: a pinned bridge, the env file, and the wrapper that reads it (A3, 2026-09-27)

- **`catalog/linux.json`**, **`catalog/macos.json`**: new `custom` component
  `omnigraph-client` (profiles `workstation`, `ai-coding`, `light`; **not**
  `server`, which the spec leaves open), `requires: nodejs`, prompt
  `omnigraph_url`, postInstall `install_omnigraph_client`.
- **`lib/linux/install.sh`**: `install_omnigraph_client` resolves the base URL
  from the `omnigraph_url` answer (`omnigraph_url_answer` is now the one strip
  rule, with `omnigraph_base_url` keeping the localhost default for its existing
  callers) and the token from `$OMNIGRAPH_TOKEN` else the git-ignored
  `configuration/api-keys.yml` key `omnigraph_token`, through the one parser
  `tools/keys_file.py`. With either missing it warns with the exact thing to set
  and returns 0 as `skipped: no omnigraph URL` / `skipped: no omnigraph token` —
  not a failure, and nothing written. Then four idempotent steps: the env file
  (via `write_omnigraph_env`, which now takes the token as an optional second
  argument so a resolved secret never has to be exported into setup's shell, and
  publishes `OMNIGRAPH_ENV_STATE`); the pinned bridge
  (`catalog/agent-harness.json`, `mcp_package omnigraph`) installed with
  `npm install -g --prefix ~/.local/share/autoos/omnigraph-mcp` — pre-installed
  because npx start-up measured 6.7–9.3 s median over 16 parallel bridges
  (decision D9), skipped when the prefix's own `package.json` already holds the
  pin and reinstalled when the pin moves; the wrapper copy to
  `~/.local/bin/omnigraph-mcp-autoos` (mode 755, copied not linked, replaced only
  on a content difference *and* only when the file carries AutoOS's marker — the
  user's own file there is left alone with a warning and a recorded refusal, as
  is a file that cannot be backed up); and spec §C's recognised-only removal: an
  rc-file line that both reads from the retired `agent-skills` tree and names
  `OMNIGRAPH_TOKEN` is removed after a backup, anything else in the file stays.
  `custom_is_installed` learns the component, so a second run reports `skipped`
  at the package level too and every step says `unchanged`. A `--dry-run`
  announces each step and ends `dry run: nothing was written` — it never claims
  the machine is already current, because a dry run compares nothing.
- **`tools/omnigraph-mcp-autoos.sh`**, **`.ps1`**: the bridge launcher an MCP
  client calls. It reads `~/.autoos-omnigraph.env` itself — only the three
  `OMNIGRAPH_*` keys, values assigned and never evaluated, a value already in the
  env winning — then `exec`s the pre-installed bridge; no bridge is one stderr
  line naming the component and exit `127`. The token is never printed. Windows
  *wiring* is a later lane; the twin is tracked now so the two cannot drift.
- **`configuration/api-keys.example.yml`**: the commented `omnigraph_token`
  placeholder, with the line saying the token is issued by the graph server.
- **`docs/omnigraph.md`** ("The `omnigraph-client` component", "Token rotation")
  and **`docs/catalog.md`**: the inputs, the output file, the wrapper, the skip
  hint, and the fact that the env file is the token's only home on the machine.
- **`tests/linux/38-omnigraph-client.sh`**: 15 cases with `SYS_HOME` in a temp
  dir and npm stubbed to land the tree `npm install -g --prefix` lands — both
  skip paths (no failure recorded, nothing on disk), the keys-file token path
  with no printed value, a full run's modes and contents, the second run all
  skipped, a moved pin reinstalled, the user's own wrapper kept and AutoOS's
  replaced with a backup, the retired rc line removed and its neighbours kept,
  the wrapper's env precedence and its 127, and a dry run that writes nothing.
### Fixed — flock's post-lockfile shell stop is still a stop behind a leading `--` (hx3 review LOWs, S1, 2026-09-28)

Fast-follow on the hx3 Sonnet FINAL review: one LOW in the policy, one in a test message. Failing tests written first (8 red assertions).

- **`tools/hostexec/policy.py`** (`_walk_wrapper_options`): the post-`--` catch-up loop filled the wrapper's positional slots and then handed the next token to the caller as the head without consulting `spec.post_positional_stops`, so `flock -- /tmp/l -c id` and `flock -- /tmp/l --command id` — nested one wrapper deep, `nice flock -- /tmp/l -c id`, likewise — denied as `path-hijack` on a literal `-c` that no program will ever exec, instead of as the shell form flock really runs there. Measured on util-linux 2.39.3: `flock -- ./l -c '/bin/echo FIVE'` prints `FIVE` via `sh -c`, while `flock -- ./l --comm x` and `flock -- ./l -cX` report `failed to execute` (rc 69) and `flock -- ./l ls` runs `ls`. `--` now ends option parsing without leaving the loop: a `scanning` flag replaces the separate catch-up, so the positional slots still take the following tokens verbatim (the `flock -- -c rm -rf /` reading, where `-c` is a file name, is unchanged) and what lands past them is judged by the one rule that already knows about `post_positional_stops`. The decision stays deny in both spellings — only the reason was wrong, and it is the reason an audit record is made of.
- **`tests/test_check_omnigraph_bridge.py`**: `test_a_bridge_leads_its_own_process_group` asserted `getpgid(pid) == pid` behind the message *"the bridge shares the benchmark's process group"* — that is the safe state, not the failure, so a red run printed a sentence describing the opposite of what it detected. The message now names the failure it means: the bridge sits in somebody else's group, so `close()`'s `killpg` would signal that group, the benchmark's own processes included.
- **`tests/test_hostexec_policy.py`**: the two `--`-before-the-lockfile shell forms joined `_FLOCK_COMMAND_FORMS` (flat and nested under `nice`) and `_NON_PERMUTING_HEADS` (no head, plus the unhonoured `--comm` as the verbatim head), and `NonPermutingGetoptTests.test_flock_shell_stop_survives_a_leading_dashdash` pins the walker's option list, the denial reason and the nested head. `flock -- /tmp/l ls` stays allowed and still yields `["ls"]`.

Measured here: `python3 -m pytest -q tests/test_hostexec_policy.py tests/test_hostexec_runner.py tests/test_hostexec_server.py tests/test_check_omnigraph_bridge.py` 99 passed / 3 skipped / 4402 subtests; `python3 -m pytest -q tests/` 1847 passed / 4 skipped / 4571 subtests.

### Fixed — the leak check stays strict; only another worktree's own branch move is exempt (LEAKFP2, 2026-09-28)

- **`tools/autoos-agent.py`**: 75f2866 required three signals before blaming a commit on the worker — a write visible in this worktree's HEAD reflog, the worker's own identity, and a committer timestamp inside the run window — and then exempted anything that looked like another lane's work (made on a ref created during the run, or contained in a new or sibling-worktree ref). Sonnet's review of that commit demonstrated each as an *evasion of a real leak* against live repositories: a decoy `git branch` laid on the worker's own tip, a backdated `GIT_COMMITTER_DATE`, a `git switch -c` + commit + fast-forward back. The window and both exemptions are gone and the 75f2866~1 detection is back — HEAD first-parent range, the checked-out branch's own reflog for a commit-then-reset, every ref that existed at the snapshot and moved, author OR committer = the worker, plus the new-dirt porcelain leg — and one narrow exemption is kept, the measured cause of false positive B: a ref that is the checked-out branch of ANOTHER worktree of the same repository at *both* the snapshot and the check, and is neither this worktree nor this run's sandbox (`_lane_worktree_moved`). False positive A — the orchestrator fast-forwarding this parent onto another lane while the child runs — is deliberately not exempted in code, because nothing distinguishes it from a worker write; the exit-7 report now says so on its own line (`if you moved this branch yourself during the run (merge/ff), this is expected - do not move a parent while its child runs (skill R-coord-01)`). Consequence, and intended: a pre-run lane commit brought in mid-run and a moved ref checked out in no worktree (another writer's *clone*) report LEAK 7 where 75f2866 stayed silent.
- **`tests/test_autoos_spawner.py`**: **`LeakStrictnessTests`** (new, 8 cases) drives the real `parent_snapshot`/`parent_leak` against real temp repositories across the exemption boundary — a sibling worktree's own branch moving is exempt; a worktree added mid-run is not; a worktree inside the sandbox is not; a commit on a branch created during the run is a leak either way HEAD then goes. `IsolateContainmentTests` gains four fake-worker modes for the evasions (decoy branch, backdated committer date, `switch -c` + ff back, commit on a new branch) and the three flipped expectations above. 13 red before the fix, 417 green after on the file; `python3 -m pytest -q tests/` 1831 passed, 4 skipped.
### Fixed — affected-tests re-reads a file plainly when its scan ends unbalanced (AFFFIX3, 2026-09-28)

Fast-follow on `review-afffix.md` (Sonnet round 3: one CRITICAL, one HIGH; the recorded decision is *stop chasing bash grammar*), failing tests written first.

- **`tools/affected-tests.py`** (`masked_lines`, `regions`): an unfinished scan is not an answer. A stray opener used to park the reader inside a heredoc until the end of the file — `((x<<=1))` opened one named `=1`, because `=` sits in the delimiter charset — and every real case below it went missing with nothing printed. `masked_lines` now returns `(masked, balanced)`; when a file reaches EOF with a heredoc delimiter, a here-string quote, a quote or a substitution still open, `regions()` discards *that file's* masking, matches its headers plain, and writes `affected-tests: <file>: unbalanced scan at EOF, masking disabled for this file` to stderr. Worst case becomes over-inclusion — one phantom run, which is what this tool is allowed to emit — instead of whole-file blindness, and the fallback is per-file, so the balanced files keep hiding their phantoms.
- **`tools/affected-tests.py`** (`_code_view`, `SHELL_OPENER`): the root cause of that report. `<<` is bash's shift operator inside `(( … ))` and `$(( … ))`, and a `<<` immediately followed by `=` is `<<=`, never a heredoc opener. `_code_view` carries an `("arith", paren_depth)` frame (`((`, `$((`, nested grouping parens, including `$(( ))` inside a double-quoted string) and blanks a `<` seen inside it, and `SHELL_OPENER` gained `(?!=)` so the shift-assignment is excluded even where no arithmetic frame was tracked.
- **Known boundary — documented and pinned, not fixed**: a `case` pattern's `)` inside a `$( … )` is at paren depth 1, so it closes the substitution frame early and everything after it is read outside (the HIGH of the same review). Repairing it means real bash grammar tracking, which this round deliberately does not attempt, so the module docstring's new limits list names it — with `<<$var`, whose delimiter is only known at run time, and the deprecated `$[expr]` arithmetic form — and `test_a_case_pattern_inside_a_substitution_closes_the_substitution_early` pins what it costs today (a phantom case, with the outer case's own mention credited to that phantom). A future fix flips the pin on purpose.
- **`tests/test_affected_tests.py`**: eight cases — the three arithmetic shapes, `<<=` at the opener-regex level, a shell file and a here-string file left open at EOF, the pinned boundary, and a tool-level trio asserting the stderr line, that stdout stays nothing but the filter, that the real case below the stray heredoc is selected, and that masking survives in the files that did balance. The real-repo heredoc test now checks every suite file ends balanced, because a file that does not has its masking thrown away.

Measured here: the real suites contain no arithmetic shift and every one of them ends balanced, so masking is unchanged line-for-line (`tests/run-tests.ps1` 188 masked lines), `t1-orchestrator muse-spark opus-4-6` hits the same 127 cases in the same blocks against HEAD's own copy of the tool, and no format emits a warning. `python3 -m pytest -q tests/test_affected_tests.py` 37 → 45 passed; live `systemd-run --user --scope -p MemoryMax=2G bash tests/run-tests.sh --filter "$(… t1-orchestrator --format filter)"` 77 passed / 0 failed.

### Fixed — affected-tests finds a heredoc opened inside a quoted command substitution (AFFFIX2, 2026-09-28)

Fast-follow on `review-afffix.md` (HIGH), failing test written first.

- **`tools/affected-tests.py`** (`_code_view`, `masked_lines`): the quote state reset on every line, so the suites' dominant idiom — `out="$(python3 - 2>&1 <<'PY'` … `PY` … `)"` — never registered as a heredoc: the opening `"` blanked the rest of the line, `<<'PY'` included, and the body was read as code. A header-shaped line in one would then start a case that does not exist and cut the case holding it, hiding its mentions. `_code_view` now takes and returns the open contexts (quoted string, `$( … )`, backticks, with a paren depth so a `( … )` group inside a substitution does not close it) and `masked_lines` carries them across lines the way `masked_lines` already carries a pending heredoc. bash parses a substitution's contents as code even while an outer `"` is open, and it is the *body* that is data, so the state is frozen while a body is being read. Measured here: 64 such openers in `tests/linux/*.sh`, 0 registered before, 64 after, and the same 3,012 cases with the same bodies — every file's context stack ends empty, so the phantom that was latent under AFFFIX is now latent under a shape the suites actually use.
- **`tests/test_affected_tests.py`**: the repro (a `if it "phantom"` line inside a substitution heredoc whose body precedes a real id mention) at fixture, `regions()` and real-repo level, plus the 64-opener count as a measurement test (`>= 60`, so a small edit to the suites does not break it) and a nested-`( … )` guard.

### Fixed — affected-tests reads a heredoc body as data and stops an id at its own edge (AFFFIX, 2026-09-28)

Fast-follow on the AFFTESTS review (`logs/handoff-sessions/20260925/work/L1-routing/review-afftests.md`): findings F1 (HIGH, latent) and F2 (two LOW precision items), each with the failing test written first.

- **`tools/affected-tests.py`** (F1): `regions()` matched `if it "…"` / `Test-Case '…'` and `describe` against raw lines, so a header-shaped line *inside* a heredoc (`cat >"$f" <<'EOS'` … `EOS`, `<<-EOS`, `<<"X"`) or a PowerShell here-string (`@'` … `'@`) ended the real case above it — the id mention after the closing delimiter was credited to a case that does not exist, and the emitted filter could not select it. `masked_lines()` now walks the file the way its parser does (quoted content and trailing `#` comments blanked first, `<<<` is not an opener, `<<-` strips leading tabs from its terminator, a here-string's closing delimiter must sit at column 0) and ignores a header or group heading that lands inside a body; the body's own text still counts toward the case containing it. Measured here: the same 2,918 blocks with the same bodies, so the shape is latent in this repository today — which is why it is a fast-follow and not a hotfix.
- **`tools/affected-tests.py`** (`id_pattern`, F2): `\b` reads `-` as a word break, so querying `t1-orchestrator` also selected blocks naming `t1-orchestrator-clean` — a different route. What follows a mention is now checked against the characters that continue an id (`-`, `_`, letters, digits). Two edges deliberately stay loose, and they are the reason the fix is asymmetric: a `.` is not an id character (`omniroute-t1-orchestrator.json` and a sentence-ending `t1-orchestrator.` name the route), and the *left* neighbour keeps the plain word boundary, because rejecting `-` there too hides 24 blocks of this repository — among them every `tests/linux/34-ai-services.sh` case naming the `omniroute-t1-orchestrator` profile that route generates, and every cap case naming `claude-opus-4-6`, the model it serves — while what the looseness lets back in (`chip-auto`, `AUTOOS_WIPE_TARGET`) only ever runs one extra case. A hidden case is the exact failure this tool exists to prevent: over-inclusion allowed, misses not. Audited over `t1-orchestrator muse-spark auto opus-4-6` (162 affected cases → 115, none added): all 47 blocks the tightened right edge drops are mentions followed by `-`, i.e. a longer id (`muse-spark-1.3-contributor`, `t1-orchestrator-paid`, `claude-opus-4-6-thinking`) or plain English (`auto-added`, `AUTOOS_DRY_RUN`, `automatically`).
- **`tools/affected-tests.py`** (`choose_terms`, F2): a test name with no word character in it (`✓ ✗ ✗`) got no token to build a term from and vanished from the filter in silence; it now falls back to the whole name, and when even that is impossible — a comma in the name, which would split the term in `--filter` — the case is named on stderr as unfilterable and the exit status stays 0.
### Fixed — flock stops option parsing at its lockfile too, except a bare -c/--command (hx3 review, S1-S2 security, 2026-09-28)

- **`tools/hostexec/policy.py`**: the entry below left **flock** as the one launcher modelled with permuting GNU getopt, so the walker kept reading options after its lockfile. util-linux 2.39.3 flock(1) does not permute either — measured on this host with `/bin/echo`: `flock ./l -- /bin/echo A` → `flock: failed to execute --` (rc 69) and `flock ./l -s /bin/echo B` → `failed to execute -s` (rc 69), while `flock -- ./l /bin/echo C` prints `C` and `flock -s ./l /bin/echo F` prints `F`, and `flock -n -w 5 ./l /bin/echo J` prints `J`: options *before* the lockfile keep full getopt parsing (abbreviations, clusters, `--`). Past the lockfile the next token is the exec target verbatim, with one exception the program special-cases itself — a **bare `-c` / `--command`** there runs its argument via `sh -c` (`flock ./l -c 'echo D'` prints `D`, and `flock ./l -c echo D` reports `-c requires exactly one command argument`, rc 64, proving it was read as an option and not as a file name). Nothing else is honoured in that position: `flock ./l --comm x` → `failed to execute --comm` and `flock ./l -cX` → `failed to execute -cX`, so the abbreviation and the attached form *are* the command, and `flock ./l /bin/echo H -c echo I` prints `H -c echo I` — only the token *right after* the lockfile is special. `_WrapperSpec.permute` is gone (no launcher takes a positional and permutes, so its **True** branch was unreachable); `post_positional_stops` replaces it — the literal argv tokens still read in the command region, matched exactly, recorded in the walker's option list under the same `c`/`command` name the option region uses so `_flock_runs_shell` denies either spelling. flock now reaches `_non_permuting_child_heads()` with chrt and taskset, and `_idx_after_flock()`, its only caller, is deleted. Behaviour, old → new: `flock /tmp/l -- ls` and `flock /tmp/l -s ls` claimed the head `["ls"]` and **allowed** calls that exec `--`/`-s`, `flock /tmp/l --comm x` and `flock /tmp/l -cX` were refused as the shell form (`no-inline-shell`) for a program that execs those literal tokens, and `flock /tmp/l -- rm -rf /` denied as `destructive` for an `rm` that never runs; they now yield the verbatim head (`["--","ls"]`, `["-s","ls"]`, `["--comm","x"]`, `["-cX"]`, `["--","rm","-rf","/"]`) and deny as an unresolvable `argv[0]` (`path-hijack`), while `-c`/`--command` right after the lockfile stays a stop with no head and the `--`-*before*-the-lockfile reading (`flock -- -c rm -rf /` locks on the file `-c` and runs `rm`) is unchanged. Not exploitable in the sandbox — no `--`, `-s` or `--comm` resolves on the policy's fixed PATH, and every one of these calls was denied or allowed only under a false head — but an audit that records `ls` for a call that execs `--` is wrong on its face, and it is the reading that decided whether an option-looking token was flock's own. Cross-checked against the binary (15 argv shapes, `/bin/echo` as the command): every call the policy now **allows** execs exactly the head it records (rc 0) — `flock -s ./l`, `flock -n -w 5 ./l`, `flock -- ./l`, `./l /bin/echo H -c echo I` — and each refused dash-token is the exec target (`failed to execute --`/`-s`/`-w`/`-E`/`--comm`/`-cX`, rc 69), which includes `flock /tmp/l -w 5 /bin/echo x` and `flock /tmp/l -E 1 /bin/echo x`: the permuting model swallowed those option values and allowed the call against an `["/bin/echo","x"]` head. Note that 2.39.3's getopt refuses `-c`/`--command` *before* the lockfile too (`flock -c x ./l` → `invalid option -- 'c'`, rc 64), so the option-region `stops` entry stays as the fail-closed deny of a call the program rejects itself — `stops` and `post_positional_stops` coexist rather than replace each other.
- **`tests/test_hostexec_policy.py`**: the flock rows moved onto the non-permuting reading and the false ones went. `_HARMLESS_ALLOWED` dropped `flock /tmp/l -- ls` and gained `flock -s /tmp/l ls` + `flock -n -w 5 /tmp/l ls`; `_THREE_LAUNCHER_HEADS` and `_THREE_LAUNCHER_DENIED_BY_CHILD_RULE` lost their `flock /tmp/l -- …` rows (the `--`-before-the-lockfile row, still true, stays). `_NON_PERMUTING_HEADS` now asserts the execed-verbatim heads (`["--","ls"]`, `["-s","ls"]`, `["--comm","x"]`, `["-cX"]`), `[]` for `-c`/`--command`, and `["ls"]` for the pre-lockfile forms, and `_NON_PERMUTING_DENIED` denies each execed-verbatim form as `path-hijack`. The guard became `test_every_positional_wrapper_stops_at_its_positional`: exactly flock, chrt and taskset take a positional before their command, and exactly flock declares `post_positional_stops == ("-c", "--command")`. 10 assertions were red before the fix (4 head rows, 5 decide() denials, the guard); after — 40 tests / 4394 subtests pass, with `tests/test_hostexec_runner.py` and `tests/test_hostexec_server.py` (77 passed, 3 skipped) and the whole `tests/` suite green.

### Fixed — chrt and taskset stop option parsing at their positional, and the policy models that (hx3, S1-S2 security, 2026-09-28)

- **`tools/hostexec/policy.py`**: the shared launcher walker modelled **flock's** permuting GNU getopt on every wrapper that sets `positionals_before_command`, so after chrt's priority and taskset's mask it kept reading tokens as options. util-linux 2.39.3 calls `getopt(3)` for those two with a `+`-prefixed optstring, whose scan stops at the first non-option — nothing past the positional is an option, and the very next token is what reaches `execvp` verbatim. Measured on this host with `/bin/echo`: `chrt -i 0 -- /bin/echo x` → `chrt: failed to execute --: No such file or directory` (rc 127), likewise `taskset 0x1 -- /bin/echo x`, `taskset 9 -- /bin/echo x` and `taskset 0x1 -c /bin/echo x` (which execs `-c`), and `chrt -i 0 -p 123` → `failed to execute -p` — it never enters pid mode. Only `--` *before* the priority/mask terminates the options: `chrt -i -- 0 /bin/echo x` and `taskset -- 0x1 /bin/echo x` both print `x`, and `chrt -- -i 0 /bin/echo x` reports `invalid priority argument: '-i'`, proving the positional region survives the separator. Behaviour, old → new: `chrt 9 -- ls` claimed the head `["ls"]` and allowed the call as `ls`, `chrt 5 -p 123` was refused as pid mode (`no-inline-shell`) for a command that would have exec'd `-p`, `taskset 0x1 -- ls` claimed `["ls"]`; both now yield the verbatim head (`["--","ls"]`, `["-p","123"]`, `["--","ls"]`) and decide() denies them as an unresolvable `argv[0]` (`path-hijack`), while `chrt -f -- 5 ls` still yields `["ls"]`. Not exploitable — no `--` or `-p` resolves on the policy's fixed PATH, and the stub test proved the token is the exec target rather than a later error: with an executable literally named `--` / `-p` handed to the call in a temp PATH, `chrt -i 0 -p 123` printed `RAN-STUB-p 123` and `taskset 0x1 -- y` printed the `--` stub's output — but an audit that records `ls` for a call that execs `--` is wrong on its face and drops everything past the separator out of the audit. `_WrapperSpec` gains `permute` (default **True**: flock is explicit, watch and the rest unset), chrt and taskset set **False**, and `_non_permuting_child_heads()` is the one place that reports a dash-prefixed head — a permuting launcher's `-` still means "no head", because there getopt really did consume the option. Every flock, watch and permuting case is unchanged.
- **`tests/test_hostexec_policy.py`**: `NonPermutingGetoptTests` — a head table for the execed-verbatim forms (including a `rm -rf /` parked behind the `--`), the decide() verdicts, a pid-mode contrast (`chrt -p 123` denies `no-inline-shell`, `chrt 5 -p 123` denies `path-hijack` and the walker reports no `p` option for it), and a guard that exactly chrt and taskset are non-permuting while flock stays permuting. The two fictitious rows went: `chrt 9 -- ls` / `taskset 9 -- ls` were in `_HARMLESS_ALLOWED` and `_THREE_LAUNCHER_HEADS` asserted `[["ls"]]` for them; the honest `--`-before-the-positional forms stay allowed, and `chrt -f -- 5 ls` was added. All 15 new assertions were red before the fix; 40 tests / 4383 subtests pass after, and `tests/test_hostexec_runner.py` and `tests/test_hostexec_server.py` with them.

### Added — affected-tests.py derives the filter a registry change needs (AFFTESTS, 2026-09-27)

- **`tools/affected-tests.py`** (new), **`tests/test_affected_tests.py`** (new), **`tests/linux/33-documentation.sh`**, **`docs/testing.md`**: a route or provider flip was followed by a hand-picked `--filter` list, the shell and Pester cases naming the changed id never ran, and CI went red twice (lessons PROVPIN, MUSEPIN). The list is derivable, so it is derived now: given ids — on the command line, or read out of `catalog/ai-registry.json`'s routes / providers / models entries changed since a rev with `--from-diff` — the tool scans `tests/linux/*.sh` (`if it "…"` blocks), `tests/run-tests.ps1` (`Test-Case '…'`) and `tests/test_*.py` (functions located with `ast`), word-boundary matches each block's text, and emits `--format filter` (one comma-separated list for both runners, terms always whitespace- and comma-free so `$( )` cannot truncate it), `--format pytest` (node ids) or the default table showing which term selects which case and why. Over-inclusion is intended and misses are not: a term is always a substring of an affected test's own name.
### Fixed — a dry run on a host that has no uv plans the graphify tool instead of failing (A4 CI S1, 2026-09-28)

- **`lib/linux/install.sh`**: `install_graphify_tool` returned 1 when `has_cmd uv` was false, *before* the `AUTOOS_DRY_RUN` branch below it. A plan runs uv's own component earlier in the same pass and installs nothing, so on a machine that does not have uv yet — every CI runner, every fresh host — `install_mcp_graphify` took the refusal as its own and recorded `mcp-graphify` failed: CI 36360904338 turned `a dry run executes no commands at all` and `dry run with herdr-sessions installed still exits 0` red (rc=1) while the run those plans describe would have succeeded, because by the time execution reaches the step uv is on PATH. A dry run now announces `would install the pinned graphify tool once the uv component puts uv on PATH: uv tool install <pin>` and returns 0; the clients go on to hear their own `would run: claude mcp add …` / `would merge 'graphify' into Antigravity` plan as before. A live run with no uv is unchanged — it still refuses, names uv, registers neither client and exits non-zero.
- **`tests/linux/18-mcp-wiring.sh`**: two cases driving `install_mcp_graphify` with uv stubbed out of `has_cmd` (a PATH trim would pass on a runner that happens to ship uv and fail on one that does not): the dry run exits 0, plans the tool, names where uv comes from, plans both clients and writes nothing — no claude call, no user config, no Antigravity config, no `graphify-mcp` at the bin path; the live run on the same stub exits 1, names the missing uv and registers nothing. Both were red before the fix (the dry run read `RC=1` with the refusal in place of a plan). Reproduced and verified without uv: `PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -v -e '\.local/bin' -e cargo | paste -sd:) bash tests/run-tests.sh --filter='a dry run executes no commands,herdr-sessions installed still exits 0'` — 2 failed before, 2 passed after; `--filter='graphify,homelab,mcp,agent-skills,idempotency,end-to-end'` passes 70 with and without uv on PATH.

### Fixed — a wedged bridge takes its process tree with it, and Ctrl-C stops the wave (A5 review, 2026-09-28)

- **`tools/check_omnigraph_bridge.py`**: `McpStdioClient.close()` killed only the direct child it spawned — and that child is `npx`. The `npm`-started `node` of a hung or timed-out bridge survived, reparented to init, one per abandoned bridge per run. Every bridge now starts in a group of its own (`start_new_session=True`, `CREATE_NEW_PROCESS_GROUP` on Windows) so the whole tree can be stopped at once, and close() escalates over that group: SIGTERM, wait, SIGKILL, wait. A group is signalled only when the child *leads* it (`os.getpgid(pid) == pid`) — aimed at any other pgid it would be this process's own group. After a SIGKILL the child is waited for, so it is reaped rather than left a zombie holding its pid (an unreaped one still answers `kill(pid, 0)`). Windows has no group-SIGTERM, so the force stage is `taskkill /T /F /PID <pid>`, each branch guarded by `os.name`.
- **`tools/check_omnigraph_bridge.py`**: the wave ran on `ThreadPoolExecutor.map`, which blocks until every bridge is finished, so Ctrl-C stopped nothing: the run sat out the full `--timeout` for each in-flight bridge and then exited on a traceback as though nothing had happened. It polls the futures in short slices instead, and SIGINT in the main thread sets a shared `threading.Event` that every worker's read loop checks between queue polls — an in-flight bridge is abandoned and closed (which kills its group), a bridge still queued is cancelled before it starts. An interrupted run prints one "interrupted" line to stderr and exits **130** (128+SIGINT) with no report: nothing was scored, and a half wave rendered as a D9 verdict is the worse failure. A run that finishes is unchanged in behaviour and output — a wedged bridge still reads as `no answer before the timeout`.
- **`tools/check_omnigraph_bridge.py`**: a run a shell put in the background — a wrapper, a headless lane — inherits SIGINT as *ignored*, and CPython then never arms its KeyboardInterrupt handler, so Ctrl-C on such a benchmark did nothing at all and the tool ran the whole wave out (the gotcha is in AGENTS.md §6). `arm_interrupt_handler()` re-arms the default handler on entry, and only when the inherited disposition is SIG_IGN — a handler someone installed deliberately is left alone.
- **`tests/fixtures/omnigraph_bridge_stub.py`**: `FAKE_BRIDGE_MODE=hang` reproduces the shape the real bug needs — a descendant *process* (not a thread) that outlives the bridge, a bridge ignoring SIGTERM and never answering, and its pid plus the grandchild's appended as one line to `$FAKE_BRIDGE_PID_FILE`, so a test can watch every bridge a wave started after the tool has returned.
- **`tests/test_check_omnigraph_bridge.py`**: 6 cases, all red before the fix — both bridges' grandchildren are gone once the benchmark returns; close() leaves no zombie; a bridge leads its own process group; SIGINT during the wave, SIGINT during `--warm` priming, and SIGINT on a run that inherited SIGINT ignored each exit 130 in under 20 s (pre-fix all three were still waiting at 30 s) with no traceback and no surviving process. The signal and process-group cases carry an `os.name` guard, so what is covered here is the POSIX path; the Windows `taskkill` branch is untested by this host.

### Added — Omnigraph bridge benchmark with a fake-server mode (SPEC-OMNI A5, 2026-09-27)

- **`tools/check_omnigraph_bridge.py`**: starts N bridges in parallel (default 16, `--parallel`) and speaks MCP JSON-RPC to each over stdio — `initialize` → `notifications/initialized` → `tools/call health` → `tools/call query` with the `whoami` Project read — then reports spawn → `health` latency (min/median/max), `healthy N/M`, `whoami ok N/M`, and every npm lock/cache error seen on stderr (`EEXIST`, `ENOTEMPTY`, `lock`, with the matched lines kept so a false positive is judgeable). `--cold` gives the wave one fresh `npm_config_cache` (the contention case D9 is actually about); `--warm` primes the default cache with one sequential bridge and measures the wave. Exit 0 = every bridge healthy, every slug right and D9 met; 1 = a died bridge, a slow health, a wrong graph, or a lock error; 2 = unusable input. `--json` for the machine-readable report.
- **The paths are recorded from the package source, not guessed** (`npm pack` into a temp dir): `@modernrelay/omnigraph-mcp@0.8.0` `dist/bin.js:8,13,20` requires `OMNIGRAPH_BASE_URL`/`OMNIGRAPH_GRAPH_ID` and takes the token as optional; its dependency `@modernrelay/omnigraph@0.8.0` `dist/index.js:477,485` calls `GET /healthz` and `POST /query`, and `dist/index.js:159,253` prefixes every non-flat path with `/graphs/<urlencoded graphId>` — so `health` is flat and `query` is graph-scoped. `dist/index.js:203` sets `Authorization: Bearer`, and `dist/index.js:373,443` keeps `rows`/`columns` opaque, which is why `p.slug` survives the camelCase mapping. Line numbers are in the module header.
- **D9 measured on this host** (6 cores, Node 24, against the stub so npm resolution is timed with network excluded): cold 4-parallel 9.1–14.2 s, warm 16-parallel 5.8–7.3 s, **0 lock errors, 16/16 healthy, 16/16 correct slug**. Contention is not the problem — per-bridge npm resolution is, at 3–7× the 2 s limit, so this host fails D9 and points at the pre-installed pinned bridge. Recorded in `docs/omnigraph.md`; the live run against `<omnigraph-url>` is still owed before D9 closes.
- **`--fake-server`**: a stdlib stub on 127.0.0.1:<free port> answering exactly those two paths (`health` 200, the query returning a `Project` row whose `p.slug` is the graph id, 404 for anything else), with `--fake-slug` to aim it at the wrong graph. Paired with **`tests/fixtures/omnigraph_bridge_stub.py`** (a fake stdio bridge that hits the same two paths and bends its own contract on `FAKE_BRIDGE_MODE` — the modes are listed in that fixture's own header, their one home) the whole benchmark runs with no npm, no network and no token — so CI can gate the logic.
- **`tools/check-omnigraph-bridge.sh` / `.ps1`**: thin wrappers, every argument forwarded, no logic (BOM + CRLF on the PowerShell side per AGENTS.md §3, and its native call is wrapped in a local `Continue` because 5.1 turns the tool's stderr `ERROR:` lines into a terminating error).
- **Tests**: `tests/test_check_omnigraph_bridge.py` — 15 cases: all healthy exits 0; a bridge that exits early is counted and exits 1; a wrong slug fails while health still passes; lock noise on stderr is counted and fails D9; the token never appears in stdout, stderr or `--json` even when the bridge leaks it on stderr; health slower than `--limit-ms` fails; `--cold` creates its cache dir; five usage shapes exit 2; the fake server's path contract (flat `/healthz`, graph-scoped `/query`, 404 otherwise); and the command/graph-id/base-url defaults come from `.mcp.json`. Wired into `tests/linux/18-mcp-wiring.sh` (unit tests plus a wrapper end-to-end run), which `tests/test_suite_wiring.py` requires.
- One home for the pin: the bridge command, `OMNIGRAPH_GRAPH_ID` and the base-url default are read from `.mcp.json` (`--bridge-cmd` overrides it, and an override given but blank is a usage error rather than a silent fall back to real npx). Nothing is hard-coded twice.
### Fixed — graphify registers only over a tool it installed, and repairs its own stale entry (A4 review S2, 2026-09-27)

- **`lib/linux/install.sh`**: `install_mcp_graphify` ran `register_mcp_server` and `register_antigravity_mcp_server` whatever `install_graphify_tool` answered — its `rc=1` was collected and returned, never acted on. So on a host with no uv, an outage during `uv tool install`, or the user's own file at `~/.local/bin/graphify-mcp`, Claude Code and Antigravity were handed a `graphify-mcp` server that cannot start, and because `register_mcp_server` leaves a name it already sees alone, every later run reported *already registered* over the broken entry instead of repairing it. Registration is now gated: `install_graphify_tool` returning non-zero (including the blocked link, which now reports non-zero instead of a silent pass) or an installed `graphify-mcp` that does not resolve at uv's tool bin dir means neither client config is touched and the step returns 1 — a refusal that names itself, not a promise the client holds at every session start. A dry run cannot install anything, so it announces the same plan it would have. (This supersedes the registration half of the A4/A4b entry below; the install verdicts are unchanged.)
- **`lib/linux/install.sh`**: `graphify_user_entry` + `replace_stale_graphify_mcp_entry` close the other half of the same hole — an entry that predates the pinned tool is never repaired by `register_mcp_server`, so every machine an older AutoOS wired kept `uv --quiet run --with <pkg> python -m graphify.serve …` (resolved at launch, no executable on PATH) or a `graphify-mcp` path from a checkout that has moved. The classifier recognises exactly AutoOS's own previous forms, by shape and by path components rather than by a hardcoded location, and a bare `graphify-mcp` entry additionally by whether the installed tool actually resolves behind it; the re-add is `register_mcp_server`'s own call right after, so the repair is idempotent and one run cannot leave the name free. Anything else under that name — another command, an `env` block of the user's, a config that does not parse — is reported as left alone and never removed, an unreadable verdict fails closed, the config outside `$SYS_HOME` is refused as it is for `homelab`, the file is backed up through `backup_file` before `claude mcp remove` touches it (no copy, no write), the entry's args are never printed because they may hold a token, and `register_mcp_server` itself is not broadened: this recognises graphify's own history and nothing else.
- **`lib/linux/install.sh`**: `graphify_mcp_bin_path` is the one home for the path uv writes its tool executable to; `graphify_mcp_link_prepare` (which clears it) and the registration gate (which trusts it) both ask it, so the two cannot drift apart.
- **`tests/linux/18-mcp-wiring.sh`**: eight cases driving `install_mcp_graphify` itself, not the install helper — failed install registers neither client and returns non-zero; a blocked bin path registers nothing and leaves the user's file untouched; a resolving install registers both; the old `uv run --with` entry is backed up, removed *before* the add, and replaced while `serena` and `firstTimeRun` survive; a dead absolute `graphify-mcp` path is replaced; a custom entry stays byte-identical with no `claude` call, no backup and no token in the output; the second run writes no client config again and keeps the graphify entry *skipped*; the dry run announces without installing or writing. The `claude` fake now owns a real JSON config and refuses an `add` over an existing name — otherwise the repair could pass by re-adding on top of the stale entry. Two registration stubs were corrected to the real `uv` contract (a successful install leaves an executable `graphify-mcp` in its bin dir and its link at `~/.local/bin/graphify-mcp`); they had been answering `tool install` with nothing written, which is the state the old code registered anyway.

### Changed — graphify is the pinned `uv tool`, and recognised agent-skills leftovers go (A4, A4b, 2026-09-27)

- **`lib/linux/install.sh`**: `install_mcp_graphify` installs the catalog pin with `uv tool install` (spec D13) instead of leaving every client launch to resolve `uv run --with` on its own. The package string comes only from `mcp_package graphify` — neither the extras nor the version appear under `lib/`, so `tests/linux/07-mcp-pins.sh` still holds the pin in one place. Verdicts are decided before anything runs and are the same in a dry run: `uv tool list` already names the pinned version → *skipped* and uv is never called; another version → `uv tool install --force` → *updated*; nothing installed → *installed*. Claude Code and Antigravity now register the installed `graphify-mcp` command, measured to be exactly as reachable in a non-interactive `bash -c` as `uv` itself (uv's tool bin dir is `~/.local/bin`, the same directory `uv` resolves from), which is what D9 asks for: a pre-installed bridge started directly, not a resolver.
- **`lib/linux/install.sh`**: `graphify_mcp_link_prepare` replaces `graphify_mcp_symlink`. Repointing the link was the pre-D13 job; the path is now uv's to write, so a link into the retired `*/agent-skills/*` tree or to this checkout's docker wrapper is removed (a symlink holds nothing of the user's, so there is nothing to back up) and reported, uv's own link is kept and lets the update force past a deleted receipt, and anything else at `~/.local/bin/graphify-mcp` is the user's: left exactly as it is and the install is refused on top of it — `--force` never runs over a file that was not ours. A dangling link is judged by the target it names, so the realistic migration case (the clone already deleted) is still recognised.
- **`lib/linux/install.sh`**: `remove_stale_homelab_mcp_entry` (spec §C, D14) drops the user-scope `homelab` MCP entry from the Claude config only when its `env.PYTHONPATH` or its args name the retired clone; the config is read, backed up once, and changed through `claude mcp remove --scope user` rather than hand-edited JSON, because that CLI owns the file's format and the rest of its contents. Any other `homelab` entry is reported as left alone with the command the user can run themselves; a second run finds nothing and reports *skipped*. It refuses outright when the config it resolves to is not in the home this run configures (`$SYS_HOME`): under sudo, or in the test harness with a faked `SYS_HOME`, that file belongs to another session and AutoOS was never pointed at it. Called from `install_agent_skills`, where the graphify link cleanup used to sit.
- **`catalog/{linux,macos,windows}.json`**: `mcp-graphify`'s homepage points at the upstream project (`https://github.com/Graphify-Labs/graphify`, from the pinned release's own PyPI metadata) instead of at this repository.
- **`tests/linux/18-mcp-wiring.sh`**: stateful `uv` and `claude` fakes cover the eight link shapes (free, clone's link, wrapper link, user's file, user's link, uv's link, dangling clone, and dry-run/live verdict parity for all of them) and the five install cases (fresh, pinned → skipped with no uv call, other version → `--force`, double run → one install then skipped, never over the user's file), plus the registration argv, and the homelab cases: recognised PYTHONPATH and args shapes removed with exactly one backup, unrecognised entry byte-identical with no backup and no `claude` call, absent entry skipped, project-scope entry untouched, dry run announcing without writing, a config outside the run's home untouched, and the call-site guard so the cleanup cannot become an orphan function.
### Fixed — a route's declared context is a promise its legs must keep (PROVFIX3, 2026-09-27)

Follow-up findings on the PROV review (`logs/handoff-sessions/20260925/work/L1-routing/review-prov2.out`), re-judged against the MUSEAPI base where `meta_api/muse-spark-1.3-contributor` heads `t1`.

- **`tools/registry.py`** (findings 1, 8): `clamp_route_context()` — a route's declared context may not exceed the smallest advertised window among the legs it actually *serves*, because the resolver can fall through to a leg that small at any time and a bigger request then simply fails. `t1-orchestrator` promised 1M while its gemini fallback takes 131,072, so the combo, the picker entry, the OpenHands profile and `docs/models.md` all advertised a window no leg could serve; all four now render `128k` (the `CONTEXT_LADDER` rung at or below 131,072, so the decimal-spelling and binary-spelling floors agree). Gates (`unavailable_legs`, `client_bound`, provider-off, `policy.leg_rules`) do not lower the promise — only servable legs do — and a leg with no recorded window cannot clamp anything. The same served-head rule now drives `effort_ladder` and `reasoning_effort` in `catalog/ide-models.json`: the ladder came from `legs[0]` even when that leg was gated, so a route gated down to gemini advertised `xhigh` (which `muse-spark` alone carries) and the picker would send an effort the answering leg rejects. Gated-out ladders are dropped entirely (`t2-worker-clean`, `t3-driver-clean`).
- **`configuration/litellm/config.yaml`** (findings 4, 5): `fallbacks:` emptied to `[]`. Every chain ended in a `*-paid` group whose only leg is `deepseek/deepseek-flash`, and `providers.deepseek.available: false` (402, 2026-09-27T16:4xZ) — the escalation path was a guaranteed second failure after the real one, with an honest-looking comment pointing at it. Nothing is invented in its place: the header says the hand chains are inert and that `meta_api` is the paid leg that answers today. Finding 3's other half: `tools/sync-router-tiers.py`'s docstring still listed `t1-orchestrator-paid` among "the true hand groups left byte-for-byte untouched"; it names only `t2-worker-paid`/`t3-driver-paid` now, because MUSEAPI gave the tier a leg and it is managed like the others — which also makes the picker entry the review feared (an `opencode.jsonc` litellm model with no group) legal again.
- **`configuration/openhands/config.toml`** (finding 6): `[llm.t1-orchestrator-clean]` removed. The combo is *omitted* — a route with no servable leg, pruned from the gateway store by `apply` — so the profile pointed at a 404. `tests/linux/17-ai-routing.sh` asserts its absence instead of its presence.
- **`tests/test_registry_render.py`** (finding 7): `test_openhands_drops_a_tier_that_declares_legs_but_serves_none` had been gutted to a `pass` — it no longer proved anything. It now synthesizes the dead route out of the real registry (every `t1-orchestrator` leg `available: false`), asserts `gateway_legs` is empty, both t1 tiers vanish, and exactly two tiers are gone. The context clamp gained `RouteContextCapTests` (synthetic + real-render sweep: no committed combo overshoots a leg it serves) and `IdeContextAndEffortFollowServedLegsTests`; the fallback rule gained `LitellmFallbackServabilityTests` (no chain may end in an all-dead group; the committed file explains the dropped ones).
- **`configuration/omniroute/apply.sh`**, **`apply.ps1`** (L0 2026-09-27T19:07:39Z): order is now register → **refresh the gateway's model catalog** (`omniroute models <provider>`, for exactly the providers this run added) → read `/v1/models` → write combos. Registering a connection does not enumerate it, so a fresh machine's `free-ai/qwen7b` failed the catalog check on the first run and appeared only on the second. Both scripts also carry the same duplicate-`key_name` rule now: `apply.sh` exits 1 where `apply.ps1` threw, and a dry run plans the refresh it would have performed. Stubbed-CLI tests on both sides (`apply: one run registers a provider, refreshes the catalog…`, `apply.ps1: …`, the POSIX stand-in's `models` branch rebuilds the served `/v1/models`; plus a platform-independent text assertion of the step order, because a `.cmd` cannot emulate the rebuild).
- **`tests/test_autoos_resolver.py`**: the measured-overlay variant of the sensitive-implement regression asserted `route is not None` — a claim about the day `measured.json` was probed, not about the rule. It now asserts the rule: no unsafe leg may be picked, and `input_required` with a reason is the *correct* answer when no private-safe route survives the filters. (Verified against a synthetic all-legs-failed overlay: `route=None`, `state=input_required`; the old assertion failed there.)
- **`docs/models.md`** (finding 2): the hand-written sections re-pinned to the clamped, meta_api-headed t1 — the managed table already carried `128k`, the prose around it still said 1M.
- **`tests/`**: the pins that measured the old promise are re-pinned, not relaxed — `combos.json is valid…` (both suites: `128k` for the two t1 tiers, `1M` only where every served leg has it), `zed routing merges one provider…` and the Windows Zed settings case (131,072), `test_the_1m_tier_is_1000000_everywhere` → `test_the_wide_tiers_carry_the_window_their_smallest_servable_leg_takes`, the opencode variants pin (`t1-orchestrator-free-only` carries `low/medium/high`; `t2-worker-clean` lost its deepseek-inherited ladder and with it its `variants`), and `test_non_string_rung…` now matches the error text instead of an incidental route id. Found and fixed on the way: the Linux `opencode repo config pins omniroute…` list was left behind by MUSEAPI (it still lacked `spark-1.3-contributor`/`t1-orchestrator-paid`, so the suite failed at the branch point), and the Windows `openhands template` case still required the pruned `[llm.t1-orchestrator-clean]` section. Four more pins were red at `6ed98c7` for the same reason (MUSEAPI re-serviced t1 and spark and nobody came back for them): `tier profiles come from the spec, installer and tool agree` in both suites (the two spark tiers are in the spec again, so the id pin and the gateway-model regex list them), `opencode tiers declare matching context limits` in both suites (spark is a client model now and t1 is pinned at the clamped 131,072 — with the Windows "absent" check rewritten as `PSObject.Properties.Name -contains`, because strict mode turns `$null -eq $models.<missing>` into an error), and the `apply prune` examples (the orphan on show moved from `t1-orchestrator-free-only` to `deepseek-v4.1-flash`).
- S1 (the `t1-orchestrator-paid` picker leg) is moot after MUSEAPI and was only verified: it renders a managed LiteLLM block and a combo.
- Hand prose outside every managed marker, checked by no render: `docs/models.md` still said the pinned `deepseek-v4.1-flash` combo is omitted "like `t1`/spark" (t1 and spark came back with MUSEAPI — DeepSeek is now the *only* omitted pinned route), called `t1-orchestrator` "spark-only" twice (it is a `meta_api` head with a free gemini fallback and a 128k promise), and its pinned-routes diagram still marked `spark-1.3-contributor` omitted; `docs/openhands-runbook.md` described t1 as "spark-only + xhigh" with no window; `tools/autoos_agent_mcp.py`'s `spawn` description told every caller that `privacy=sensitive + ctx=1m` is refused "unless `allow_training`", which `routing.select_combo` states flatly is inert — the flag only waives the privacy check on an explicit `--model`. All four corrected to what the registry serves today.
- Lesson: a rendered number is a promise made by every leg it can fall through to, so the renderer — not the catalog's prose and not the operator's memory — is the only place the invariant can be enforced; and a test that asserts *a* route exists instead of the rule that keeps a route safe will be "fixed" by whatever route happens to be alive.

### Fixed — a failing postInstall is recorded, never aborts the run (rv5, 2026-09-27)

- **`lib/linux/install.sh`**, **`setup.sh`**: `run_post_install` called the step bare, and setup.sh calls it bare on both the installed and the skipped path under `set -euo pipefail` — so any postInstall returning non-zero killed the run where it stood: no summary, no state file, and every later component silently never installed. One real trigger was `install_ai_stack` (its own checks return 1 when the docker CLI is missing or `docker info` fails right after `add_user_to_docker_group` ran in the same session), then `install_litellm_proxy` and `route_zed_to_proxy`. The containment goes in this one place, not in each step: `run_post_install <fn> [<component id>]` captures the step's exit code, warns with the step name and that code, records the component (or the step name when called with no id) through `autoos_record_failure`, and always returns 0. The existing fold then lists the component once, as *failed (post-install)*, drops it from the installed/skipped buckets, and setup.sh still exits 1. Steps keep their own return codes — nothing about them changed but who contains them.
- **`lib/linux/install.sh`**: the five places that call a catalog postInstall directly, outside `run_post_install`, were the same abort through a different door — `install_agent_skills` calling `install_mcp_graphify`/`install_mcp_serena`/`install_mcp_playwright`/`install_mcp_context7`, and `route_detected_clis_to_gateway` calling `route_claude_to_gateway` (Claude Code's own postInstall) from inside the OmniRoute step. Each is guarded with `|| autoos_record_failure <its own component id>`; `install_mcp_playwright`'s internal record (rv3) is the same id, so the dedup in `autoos_record_failure` keeps one failure line, not two.
- **`setup.sh`**: the installed-path line read "post-install refused to change a file" for every recorded failure. A step that exited non-zero is now recorded too, and it did not refuse anything, so the line says what is always true: "post-install step failed" (the summary line still carries the `(post-install)` marker).
- **`tests/linux/14-state-verify-and-undo.sh`**: end-to-end through the real entry point — a scratch tree (setup.sh and lib linked, the catalog copied and given two test-only `custom` components that install nothing) run under setup.sh with a postInstall that returns 1: the next component still gets its step, the summary names the failed component once with `(post-install)`, the state file puts it in `failed` and the other one in `installed`, and the exit code is 1. Failed before the fix: the run stopped at the step, no state file was written. Two in-process cases cover `run_post_install` itself (the step's code is in the warning, the id is recorded, the fallback records the step name with no id given).

### Fixed — review follow-ups: one result per component, herdr path escaping, refusal residue (rv4, 2026-09-27)

- **`setup.sh`**, **`lib/linux/install.sh`**: a component whose post-install step refused to change a user file was counted twice — once as installed (or skipped) and once as failed, and the state file named it in both buckets, so "3 installed, 1 failed" described four things that happened to three components. The three result buckets are now arrays of component **ids** in `lib/linux/install.sh` (`AUTOOS_RESULT_INSTALLED/SKIPPED/FAILED`), `autoos_fold_extra_failures` moves a recorded id into the failed bucket and out of the success ones, and the printed counts are the bucket lengths, so a number cannot disagree with the list it summarises. The failure report prints one line per component through `autoos_result_label` (catalog name, `… (post-install)` for a refusal, the raw token for an id that is not a component) — the old `for f in $failed_names` split a multi-word display name into several lines.
- **`lib/linux/install.sh`**, **`configuration/herdr-sessions/*`**: `herdr-sessions` now passes the herdr binary to both `herdr-server` templates as a positional argument (`_ @HERDR_BIN@`, exec'd as `"$1"`) instead of splicing it into `ExecStart`/`ExecCondition`, so a path with a space, a `%`, a quote or a backslash is only a *shell* problem in one place; the rv3 `@HERDR_BIN_SH@` token and `shell_quote` are gone (supersedes the rv3 entry below, which quoted the path for the shell with `printf %q` and never escaped it for systemd).
- **`configuration/herdr-sessions/install.sh`**, **`configuration/herdr-sessions/lib/herdr-lib.sh`**: `%h` is honoured as a *leading* specifier only, `systemd_escape_path` quotes every rendered path (a leading `%h` stays outside the quotes so systemd still expands it per user), and `hs_resolve_h_specifier` is the one rule both consumers share — the driver no longer dies on "herdr not found at %h/.local/bin/herdr" for a site that copies the unit default into its profile. The herdr binary is now a precondition for the **user** scope too, not only the system one, and `--unregister` and `--dry-run` stay exempt.
- **`lib/agent_harness.py`**: `_backup_and_write` deletes the backup it already made when the `O_NOFOLLOW` open refuses a symlink that appeared after the pre-check (a refused write leaves nothing behind, as its docstring promised); `cmd_openhands` returns 1 when any file is `left alone (symlink)`, like `cmd_opencode`, instead of reporting success over a config it never touched.
- **`lib/linux/install.sh`**: the OpenCode and OpenHands writers record the component (`autoos_record_failure`) when the agent harness refuses or python3 is missing, instead of only warning — a warning was invisible to the summary. The id comes from `AUTOOS_POST_COMPONENT`, published by `run_post_install`, because `setup_opencode_config` is the postInstall of both `opencode` and `opencode-cli`.
- **`tests/test_catalog_uniqueness.py`**: the duplicate report unpacks the full 5-tuple key (a bare 2-tuple unpack raised `ValueError` on the first real duplicate, so the lint could only ever pass or crash) and `duplicate_report` names `arch`/`cask`/`source` in the message; `ReportSelfTests` covers the report path, which nothing exercised before.
### Fixed — review follow-ups: symlink-safe backups, counted backup failures, honest dry-run, one backup stamp, catalog uniqueness, non-vacuous harness test, real mktemp, herdr unit escaping, suite wiring (rv3, 2026-09-27)

- **`lib/agent_harness.py`**, **`tests/test_agent_harness.py`**: the agent-harness backup helper now refuses to write through a symlink using `os.open(O_NOFOLLOW)` (TOCTOU-safe); a symlink refusal returns non-zero and is recorded in the run summary; dry-run reports "left alone (symlink)" instead of "would update"; `cmd_opencode` and `cmd_openhands` propagate the refusal.
- **`lib/linux/install.sh`**: `register_playwright_lazy_proxy` returns non-zero on registration failure; `install_mcp_playwright` checks the return code explicitly and propagates it (fixes `|| autoos_record_failure` swallowing the failure because `autoos_record_failure` returns 0).
- **`configuration/herdr-sessions/install.sh`**, **`configuration/herdr-sessions/systemd/{user,system}/herdr-server.service`** (superseded by rv4): `render_unit` now systemd-quotes `HERDR_BIN` for `ExecStart` (quotes paths with spaces), shell-quotes it for `sh -c` contexts via new `@HERDR_BIN_SH@` token, and escapes `WORKDIR` for `WorkingDirectory` (spaces as `\x20`); default `%h` specifier preserved.
- **`tests/test_catalog_uniqueness.py`**: duplicate detection key now includes `arch`, `cask`, `source` to avoid flagging legitimate platform/method splits; wired into `tests/linux/02-catalog-schema.sh` (suite wiring guard passes).
- **`tests/linux/01-test-harness.sh`**: filtered-harness self-test asserts at-least-1 passed and 0 failed instead of exactly `passed 1`.
- **`templates/rescue-bootstrap.sh`**: backup stamp uses canonical `%Y%m%d-%H%M%S`.
- **`tests/run-tests.sh`**: test HTTP server reserves its port file with a real `mktemp`, never `mktemp -u`.
- **`configuration/herdr-sessions/install.sh`**, **`configuration/herdr-sessions/systemd/{user,system}/*.service`**, **`lib/linux/install.sh`**, **`tests/linux/{18-mcp-wiring,22-herdr-sessions}.sh`** (rv2, 2026-09-27): every rendered user unit now leads `PATH` with `%h/.local/bin`, so a pane inherits the same tools an interactive shell would; `render_unit` applies hostexec's systemd quoting/escaping (`\`, `"`, `%`) to the substituted paths, so a profile directory containing a space or `%` no longer renders an unloadable unit (`WorkingDirectory` stays bare — systemd does not strip quotes there); the herdr binary in both `herdr-server` templates is rendered from the profile's `HERDR_BIN` via `@HERDR_BIN@`, with the per-scope default unchanged (`%h/.local/bin/herdr` user, `/root/.local/bin/herdr` system); and the WSL CAO FIFO probe passes its path as argv instead of splicing it into Python source, so a home path containing a single quote no longer reads as "no FIFO support" and falsely relocates a working ext4 tree.
### Fixed — atomic ask-file create, unpredictable config-save temp names, 400 on a corrupt config (REVFIX, review routed 15:16Z, 2026-09-27)

- **`tools/autoos-ask.py`**: `question.json` is created with `O_CREAT|O_EXCL` instead of an `exists()` check followed by a tmp+replace write, and every `qa-<n>.json` history slot is reserved the same way before it is written. Two askers in one run dir used to both pass the check: the loser replaced the winner's pending question, so the orchestrator answered the wrong worker, and two archivers that picked the same `n` silently dropped an exchange from the history. Sharing one `question.json.tmp` was worse still — one of the two could exit 5 on a file the other had already renamed away. A loser now takes the same exit 2 ("already pending") that a sequential duplicate already took, and a create that fails mid-write removes its own half-written file rather than leaving the run pending forever.
- **`lib/linux/serve.py`**: `POST /api/config` writes through `tempfile.mkstemp(dir=ROOT, prefix=".autoos.config.")` and renames that fd-written file onto `autoos.config.json`, keeping the previous file's mode (mkstemp's 0600 must not quietly change who can read a config `setup.sh --config` may run as). The fixed `autoos.config.json.tmp` was a target anyone could pre-plant as a symlink: the save wrote the config bytes into their file and the rename then moved the link itself onto the config path, so the config *became* their file. A failed write unlinks its temp.
- **`lib/linux/serve.py`**: a corrupt existing `autoos.config.json` answers `400 {"error": "existing autoos.config.json is corrupt: <parser position>"}` instead of a 500 from the catch-all. It writes nothing, backs nothing up, and echoes none of the file's content to the page.
- **`lib/windows/AutoOS.Serve.psm1`**: `Save-AutoOSWebConfig` got the same shape — a GUID in the temp name (`$Path.<guid>.tmp`), `Move-Item -Force` onto the config, and the temp removed if either step throws. BOM and CRLF preserved.
- **`.gitignore`**: the dead fixed-name entry is replaced by the two unpredictable shapes (`.autoos.config.*.tmp`, `autoos.config.json.*.tmp`), so a save that fails mid-write cannot leave a committable file in a public repository.
- Tests: **`tests/test_autoos_spawner.py::AskRaceTests`** (two concurrent askers yield exactly one question; a widened check-then-create window yields exit 2 with the pending question intact; six racing archives claim six slots), **`tests/linux/14-state-verify-and-undo.sh`** (a symlink planted at the old fixed temp name is neither written through nor replaced, and a corrupt config is a 400 that echoes nothing), **`tests/run-tests.ps1`** (the Windows save leaves no stray temp, keeps the planted link and its target alone, and leaves the config a regular file; the link assertions skip on a host that refuses unprivileged symlinks).
- Open: an existing config that parses but is not an object (a JSON array) still answers 500 on both platforms, and `tools/autoos_agent_mcp.py` still writes `answer.json` through a fixed `<path>.tmp` in the run dir — neither was in the routed batch.
### Added — Meta Model API: Muse Spark 1.3 contributor as the main writer (MUSEAPI, 2026-09-27)

- **`catalog/ai-registry.json`**: `providers.meta_api` — Meta's OpenAI-compatible endpoint `https://api.meta.ai/v1` (Bearer), registered as a custom provider with `litellm_prefix: openai` + its own `api_base`, the `free_ai` shape, so LiteLLM never falls back to `api.openai.com`. `models.muse-spark-1.3-contributor` carries the vendor's published terms: $0.10 in / $0.20 out per 1M, 100 rpm, 3M tpm, 1,048,576 in / 131,072 out, `reasoning_effort minimal|low|medium|high|xhigh` (no `none` — 400; no `max`), and **`trains_on_prompts: true`** (`tool_calls: "unproven"` — the vendor says "tools supported", nobody has measured an agentic run). Contributor = MAIN writer: `meta_api/muse-spark-1.3-contributor` heads `t1-orchestrator`, `t1-orchestrator-paid` (which was legless, and so unservable, until this) and `spark-1.3-contributor`, and joins `t2-worker`/`t3-driver` *after* their free legs. It is in no `*-clean` route, and the spawner refuses a `privacy: sensitive` card on any of these routes (`tools/autoos_routing.py`). Effort aliases `spark-1.3-contributor-{minimal,low,medium,high,xhigh}` render on every surface that carries a ladder (combos cannot — an OmniRoute combo is one entry per leg, no per-effort alias). `providers.meta_api.model_prefix` is `meta-api`, **not** `meta`: `meta` is another provider's registry key, and because `sync-router-tiers.py --combos` resolves a combos leg's prefix through maps keyed by name/`omniroute_id` only, borrowing it rendered the contributor leg with no `api_base` at all.
- **`catalog/ai-registry.schema.json`**: new additive `providers.<id>.key_name` — the `api-keys.yml` entry a provider's key is read from when it is not an entry named after the provider id. `meta_api` shares the operator's existing `meta` key rather than asking for a second vendor key.
- **`configuration/omniroute/apply.sh`**, **`apply.ps1`**, **`tools/mirror-litellm-env.py`**: all three read `key_name`, so one `meta` value feeds the gateway registration (`meta-api`), the LiteLLM `.env` (`META_API_KEY`) and the plan output; a name that two providers claim differently is a hard error in the mirror tool and the PowerShell map, and the `.env` keeps exactly one `META_API_KEY=` line (a second line — even a `REPLACE_WITH_` placeholder — wins over the first).
- **`tools/autoos_usage.py`** (spend guard): `--cost` adds estimated `cost_in`/`cost_out` (USD) per group, priced from the registry's per-token `price_in`/`price_out`, plus `cost.source` and `cost.models_unpriced` so a 0 row reads as *free* or *unknown* — `autoos-agent.py usage --since 24h --by provider --cost`. Off by default: existing `--json` readers get the shape they get today.
- **`docs/api-keys.md`**, **`docs/models.md`**, **`configuration/omniroute/combos.json`**, **`configuration/litellm/config.yaml`**, **`catalog/ide-models.json`**, **`opencode.jsonc`**, **`configuration/openhands/tier-profiles.json`**: the `meta_api` row, the managed blocks re-rendered for the new legs and aliases, and the contributor's training/terms in prose.
- **`tests/`**: `svc: apply registers meta_api from the shared meta key` + `… and stays idempotent` (stubbed CLI, asserts `providers add meta-api --credential-env AUTOOS_KEY_META --yes` and then `= meta-api already registered`), the Windows `apply registers meta_api from the shared meta key and stays idempotent`, `SharedKeyNameTests`/`EnvSharingContractTests`/`ModelPrefixContractTests` in `tests/test_mirror_litellm_env_registry.py`, `CostTests` in `tests/test_autoos_usage.py`, and `tests/helpers/check-provider-registry.py` grew two data rules (`litellm_env_problems()`, `model_prefix_problems()`) in place of a hardcoded `meta`/`meta_api` allowlist. Re-pinned to the post-MUSEAPI state: the combos name lists (14, `t1-orchestrator-paid` now servable with a declared 1M context), `provider data JSON survives both PowerShell generations` (`Map['meta'] == 'meta-api'`, no `meta_api` key), the render suites' legless-route cases (a synthesized legless route keeps the rule that a legless route has no `effort_ladder`), and `docs/models.md`'s models-doc check.
- Lesson: a third spelling of a provider's name (`model_prefix`) is as much a namespace as `omniroute_id` — the collision was invisible to every registry-sourced check because that path never reads the gateway spelling, and only the explicit `--combos` escape hatch did.
### Fixed — hostexec policy/runner/audit hardening (HX, 2026-09-27)

- **`tools/hostexec/policy.py`**, **`tools/hostexec/server.py`**, **`tools/hostexec/runner.py`**, **`tools/hostexec/audit.py`**, **`configuration/hostexec/install.sh`**, **`configuration/hostexec/README.md`**, **`tests/`**: deny `env -S`/`--split-string` (the shebang split-string form re-splits an argument string into argv; new `no-inline-shell` rule). `policy.decide` now refuses a non-str argv element (`argv-caps`) instead of crashing, and `server.host_run` wraps `decide()` so a raise is audited as a `policy-error` denial rather than escaping as an unhandled error. Remote `ssh` executes in the audited cwd via a `cd` prefix (the local ssh process runs in `/`, so a remote-only path no longer breaks local spawn). `audit.hash_argv` hashes the redacted argv, so a secret no longer changes the fingerprint, and non-str audit tokens are rendered via `json.dumps` instead of crashing `audit.write`. Dead `_strip_wrappers`/`_WRAPPERS` deleted; the residual check-then-exec race in `_path_hijack_problem` is documented. `install.sh` stages the unit and client configs through `mktemp` in the destination directory (unpredictable, exclusively created), not a guessable `.<name>.tmp.$$`.
- **`tools/hostexec/policy.py`** (hx2): wrapper option parsing is now prefix- and cluster-aware, so `env` abbreviations/clusters can no longer slip a shell past the deny-list. Short clusters are scanned skipping the wrapper's own value-taking flags, any `S` before a value-taking flag (`env -vS`, `-0vS`, `-iS`) denies `no-inline-shell`, and abbreviated long options (`--s`, `--sp=`, `--split`, `--split-str=`, and value-taking forms like `env --ch /tmp sudo id`) resolve against the real option table: a unique prefix is honoured, while ambiguous or unknown `--X` on any wrapper (`nice`, `timeout`, `stdbuf`, `ionice`, `setsid`, `nohup`, `xargs`, `flock`) denies `no-inline-shell` instead of silently stopping the scan.
- **`tools/hostexec/runner.py`** (hx2): `_pump` now drains the child's pipe to EOF even past `output_cap`, storing only up to the cap and discarding the rest while still counting `out_bytes`. A chatty child no longer blocks forever on a full 64 KiB pipe and get misreported as `timed_out`; `truncated` is set from the total produced.
- **`configuration/hostexec/install.sh`** (hx2): `--unregister` decides unit ownership from the installed unit itself -- its own `Environment=AUTOOS_EXEC_PORT=` line and the unescaped `ExecStart` script path -- instead of re-rendering from the live `AUTOOS_EXEC_PORT`/checkout. A unit installed under another port, or from a checkout that has since moved or been renamed, is still removed; anything else is still left untouched.
- **`tools/hostexec/policy.py`** (hx3): the per-wrapper option scan is now a single `_walk_wrapper_options` that returns the wrapped command's index *and* every option it saw, so the `env -S`/`--split-string` and `flock -c`/`--command` predicates read that list instead of re-walking the options with their own rules -- one option walker per wrapper, so a spelling the walker understands cannot be missed by a second, weaker scan. The walker learns env's bare `-` (means `-i`, not a command) and flock's leading lockfile positional (a wrapper that takes an argument before its command), and sees options the C library's getopt permutes after that positional (`flock /tmp/l -c cmd`). After `--`, flock's next token is always the lockfile, so `flock -- -c rm -rf /` locks on the file `-c` and runs `rm` (previously the head was taken from the lockfile). The bypass matrix is now generated, flat *and* nested one level (2185 cases).
- **`tools/hostexec/policy.py`** (hx4): the same prefix-aware matching hx3 gave the wrappers now covers the command rules, which compared long options with `==`/`startswith` and so let the abbreviation of a forbidden flag execute it -- `rm --recurs /`, `rm --forc /`, `chmod --recurs /`, `tar --checkpoint-ac=exec=id`, `tar --to-com prog`, `tar --use-compress-prog=evil`, `git push --mir origin`, `git rebase --exe evil`, `git --git-di=/tmp status`, `iptables --flu`, `man --page evil`. One matcher, `_long_opt_hits(token, flagged)`, sits behind every deny gate (rm/chmod/chown, git push, git's global and rebase options, tar's exec hooks, man's pager, docker run/exec, parallel's ssh options, iptables), and it fails closed: any prefix of a flagged option counts, even one the real program would reject as ambiguous, because over-deny is safe and under-deny is the bug. Allow gates (`crontab --list`, `git config --get`, a bare `env bash --version`) deliberately stay exact -- over-matching there would over-*allow*. Each gate's flagged options moved into module-level `_FlaggedLongs` tables, and `tests/test_hostexec_policy.py` generates the matrix from them (409 cases: every prefix from 3 characters up, bare and `=value`, in the position each rule reads), so a new flagged option is covered by a table edit, not a new test. `_DOCKER_GLOBAL_VALUE_LONGS` also fixes the subcommand scan, where `docker --log-l info run -v /:/h alpine` read `info` as the subcommand. `parallel -I`/`--replace` now consume their replacement string, so the harmless `parallel -I foo echo foo ::: a` is no longer denied as a path-hijack, and `flock -- -evil ls` (lock file named `-evil`, child `ls`) is pinned as allowed.
- **`tools/hostexec/runner.py`** (hx3): the remote command is prefixed `cd -- <cwd> && ...`. `shlex.quote` leaves a leading `-` unquoted, so a cwd like `-evil` was parsed by `cd` as options and never changed directory; `--` makes the path literal.
- **`tools/hostexec/policy.py`** (hx2 follow-up, S2 security): `chrt`, `taskset` and `watch` were the last launchers still hand-scanned -- each skipped any token starting with `-`, so an unknown or abbreviated long option was never fail-closed and a value-taking short option left its value standing where the command is looked for (`chrt -T 1000 5 cmd`, `watch -q 5 cmd`, `taskset --zz 0x1 cmd`). They now carry `_CHRT_LONGS`/`_TASKSET_LONGS`/`_WATCH_LONGS` (util-linux 2.39.3 `chrt --help` and `taskset --help`, procps-ng 4.0.4 `watch --help`; prefix and ambiguity behaviour measured against those binaries -- `chrt --r` and `watch --e`/`--no` are reported ambiguous there, `taskset --cpu` and `watch --ex` resolve) and parse through `_walk_wrapper_options`, so `_wrapper_option_problem` denies an unparsable option region and the three `_idx_after_*` are one-line wrappers like the rest. The walker's `trailing="file"` special case is gone -- `--` ends option parsing but not the positional region, so a wrapper consumes its own remaining positional(s) after it, which also gives flock back the head it lost: `flock /tmp/l -- rm -rf /` had no head and so was never scanned by the child rules and now denies `destructive`. `chrt`/`taskset` `-p`/`--pid` in any spelling or cluster operate on an existing pid instead of a command, so they deny `no-inline-shell` the way `env -S` and `flock -c` do (this also refuses the read-only `chrt -p 123` / `taskset -p 700` query -- deliberately fail-closed, no command is auditable); and `watch` without `-x`/`--exec` joins its arguments and runs them through `sh -c` (measured: `watch -n 1 zzcmd` reports `sh: 1: zzcmd: not found`, `watch -x -n 1 zzcmd` reports `watch: unable to execute 'zzcmd'`), so it denies the same way and only the `-x` form keeps a transparent head. Tests: the three wrappers joined the generated flat *and* nested bypass matrix, and `ChrtTasksetWatchWalkerTests` pins every head shape, every pid-mode/unknown/ambiguous denial, the `--` cases, and asserts each table against the program's own option list.
### Added — the publication scanner learns four more shapes and gates the docs (SPEC-OMNI A2, 2026-09-27)

The public-scrub gate covered `infra/` and `scripts/` with four shapes, and its
own test file was run by nobody — a rule could stop matching, or a hostname could
land in `docs/`, without any check noticing. Both are fixed here.

- **`scripts/public-scrub/patterns.txt`**: five generic shapes added — `cgnat`
  (RFC 6598's 100.64.0.0/10, where Tailscale and Docker's pooled addresses live),
  `link-local` (RFC 3927's 169.254.0.0/16), `ipv6-ula` (RFC 4193's fd00::/8),
  `private-host` (a single-label name ending in `.lan`, `.local`, `home.arpa` or
  `.internal`) and `email`. Names describe the kind, never the value.
  Documentation and look-alike values are deliberately unmatched so the gate stays
  green on honest prose: the RFC 5737 / RFC 3849 documentation ranges, loopback,
  RFC 2606 reserved example domains, Anthropic's and GitHub's published no-reply
  addresses, Docker's `host.docker.internal` alias, a dotenv `.env.local` filename
  and a `settings.local.json` filename. Known limit: a private host one label
  deeper than the last (`vm01.dc.internal`) is out of reach of a rule that must
  ignore `coding.example.internal` — that is what `patterns.private.txt`
  (gitignored) is for. (`CHANGELOG.md` is not in the gate's scope; it names the
  shapes it describes, like this entry does.)
- **`scripts/public-scrub/scan.py`** now loads **`scan-exclude.txt`** (new) by
  default, so the scrubber's own rules, private literals and planted fixtures are
  never reported as hits — previously only the `--patterns` file was skipped, and
  a developer holding a real `patterns.private.txt` saw it listed as a leak.
  `--exclude-file` still overrides it; one reason per entry in the file.
- **`.github/workflows/ci.yml`** ("Public scrub scan") scans `docs/`, `catalog/`,
  `README.md` and `AGENTS.md` beside `infra/` and `scripts/`, and runs
  `scripts/public-scrub/test_scan.py` directly (no pytest is installed anywhere —
  the file grew a dependency-free `__main__` runner, and pytest still collects
  the same functions). **`tests/linux/36-static-analysis.sh`** runs the same
  command, checks the rules and fixtures, and greps the CI step for each scope
  token so narrowing the gate back fails the suite instead of un-gating the docs.
- Documentation placeholders for hits the widened scope found: `docs/web-services.md`
  named container homes that `configuration/docker/ai-stack/compose.yml` owns
  (now `/home/<service-user>` — one home per fact), `AGENTS.md` the OpenHands
  image's own `HOME`, `catalog/ai-registry.schema.json`'s `$id` (`.internal` is a
  private-host shape; `autoos.example` is reserved documentation space, and no
  code resolves the id), and `infra/mcp-servers/docs/OBSERVABILITY-MCP-SETUP.md`
  used `sentry.local` as its example hostname.

### Changed — run rules folded into the orchestration skill, one home per fact (FOLD2, 2026-09-27)

- **`.agents/skills/unattended-orchestration/SKILL.md`**: 30 rules → 28. New from this run's lessons: `R-coord-07`/`R-coord-08` keep a 10-minute `CronCreate` heartbeat from launch to stop (each beat pushes, rewrites the timestamped status file, reads the inbox, checks children — common.md "Heartbeats never stop", 15:3xZ). Extended: `R-orch-13` (a plan, spec, decision or bigger change gets a pinned cross-family review before it is executed or merged — common.md "Second opinion on everything bigger"), `R-orch-14` (new: a slow free reviewer is queued, never skipped; Haiku stays an extra first pass only; the lane record names writer, reviewer, verdict — HAIKU-EVAL.md, inbox 17:52:55Z), `R-coord-02` (findings are judged by the author's orchestrator, Opus decides critical), `R-coord-03` (writers by complexity come from `route --explain`, not a prose model list), `R-coord-06` (the cap is `autoos-agent.py context` against registry `policy.handoff_caps`), `R-orch-06` (relaunch a child that went quiet >25 min), `R-orch-11` (a retired route is grepped as a DEFAULT in `configuration/`, `lib/`, `start-stack.*` too — inbox 17:09:02Z), `R-orch-01` (agent-to-agent text is terse: one line per fact, evidence by pointer). Five rules restated another rule's fact and were folded instead: `R-orch-03`→`R-router-01`, `R-orch-05`→`R-coord-01`, `R-orch-07`→`R-orch-13`, `R-orch-09`→`R-coord-08`, `R-coord-05`→`R-worker-03`; **`references/rule-map.md`** resolves each retired id.
- **`references/main-orchestrator.md`** gains §1a (the heartbeat, from the first minute) and cites live ids; **`references/{l3-routing,layers,state-file}.md`**, **`unattended-orchestration.md`**, **`docs/agent-protocol.md`**, **`docs/agents/leaf-contract.md`** now point at the rule that owns each fact instead of restating it. The tiers table drops its hand-kept "chain head" column — that is registry data and went stale inside a day.


### Fixed — `failover off` really hands the gateway port back (lstby2)

- **`configuration/docker/ai-stack/ai-stack.sh`**: the standby pid is the
  process listening on the gateway port, found with `ss` exactly like
  `start-litellm.sh` does and accepted only when `/proc/<pid>/cmdline` is
  litellm (`start-litellm.sh` writes no pid file — the old pid-file lookup
  left the standby running with "litellm pid unknown", live 2026-09-27).
  `off` and every `on` rollback stop the standby (TERM, KILL after 10 s),
  then refuse when the port stays busy (names the holder from `ss`, starts
  nothing, rc 1), otherwise recreate the gateway
  (`compose up -d --no-deps omniroute` — a bare `start` of a port-less
  container never republishes the port), wait for `/api/health`, check the
  port is published, and only then remove the state file (on failure: the
  manual fix `ai-stack.sh up omniroute`, rc 1). Tests: the realistic fakes
  (no pid file, fake `ss` listener, real `litellm`-renamed sleep) plus
  busy-port and foreign-listener cases in `tests/linux/34-ai-services.sh`.
  Docs: `docs/web-services.md` "Standby router (LiteLLM)".
  Review round (DeepSeek v4.1-flash): a pid counts as the standby only when it
  is litellm AND holds (or was started with `--port`) the gateway port, so the
  always-on `:4000` proxy is never signalled; the published-port check rejects
  `{}` and `{"20128/tcp":null}`; the gateway comes back through `dc_up`
  (preflight guards); a failed state write leaves no state file; `off` without
  a state file still stops a standby on the port; the Ctrl-C path waits a
  short health window; TERM->KILL is tested.
- Review round (lstby2d): `failover off` accepts a gateway that already serves
  the port and clears the stale state instead of refusing forever; it finds a
  standby holding the port even with no state dir (the dir is a hint, never a
  gate); and a stale state-file pid that is simply gone is reported as
  "already gone", not signalled as if it belonged to a foreign program.
### Fixed — infra/ review round (asm-a2, 2026-09-27)

- **`scripts/public-scrub/`**: `patterns.txt` shipped private usernames, emails, a domain, a LAN IP and personal win/wsl/unix home paths **literally** in a public repo, and the whole `scripts/public-scrub/` dir was self-excluded in `migrate-exclude.txt` so the scanner never inspected its own rules. Split generic shapes (tracked, in `patterns.txt`) from the private literals (gitignored `patterns.private.txt`, loaded by `scan.py::load_all_patterns` when present); `patterns.txt` now holds only `rfc1918`/`win-home`/`unix-home`/`key-shape`. **The literals are gone from the tracked tree; they remain in the import commit `bb322a8`, which is already on `origin/L1-backlog/asm2` — removing them from history is an operator `git filter-repo` + force-push, not a lane action, and is flagged for handoff.**
- **`ci.yml`** + **`tests/linux/36-static-analysis.sh`**: the no-secrets gate was invoked by nothing. Added a `public-scrub` CI job running `scan.py` over the imported `infra/` tree and the scrubber's own `scripts/`, plus a wiring-guard test asserting `ci.yml` calls `scan.py` and that the scan is clean. A whole-tree `--git-tree HEAD` scan is deliberately NOT used: the generic `unix-home`/`rfc1918` shapes legitimately match test fixtures, `example.conf` and autoinstall placeholders elsewhere, so the gate is scoped to the area under review.
- **`.env` templates** (root cause of the false asm-a "templates for all compose files" claim): the root `.gitignore`'s broad `.env.*` (a basename match) silently swallowed `infra/mcp-servers/.env.{shared,server,client}.example`, so none was ever tracked and every onboarding `cp` and the installer hint failed. `infra/.gitignore` now re-includes the three; created them as placeholder-only templates covering every `${VAR}` the compose files reference. Verified with `check-ignore` and `git add`.
- **`infra/local-ai/docker-compose.yml`**: `LITELLM_MASTER_KEY` and `WEBUI_SECRET_KEY` now fail closed with `:?` instead of falling back to a fixed public literal / an empty key (the proxy publishes on all interfaces and fronts a paid API key; `WEBUI_AUTH` defaults true).
- **`infra/.gitignore`**: `cluster/cluster.yaml` and `cluster/seed/` were anchored at `infra/` while the real tree is `infra/mcp-servers/cluster/` — both returned NOT-IGNORED, contradicting `infra/README.md:26`. Repointed to `mcp-servers/cluster/...` with an `!` allow-list for the two tracked seeds; added `site.env` and `users.policy.yaml` (real host/user names).
- **`infra/mcp-servers/cao-setup/`**: `dedup-graph.service` and `omnigraph-sync.service` hardcoded the retired `agent-skills` checkout in `ExecStart` (the timers would run a nonexistent script); templated to `@AUTOOS_ROOT@` like `setup-sync.sh`. Honest version gate in `apply_wsl_bridge_patch.sh` (detect via `importlib.metadata`, confirm before clobbering a whole module, `AUTOOS_YES` for unattended) and fixed a SyntaxError (dropped `)`) the WIP introduced. Replaced invented `cli-agent-orchestrator` URL/version/licence claims in the `antigravity_cli.py` header with pointers to the installed metadata. Fixed the new `cao-setup/README.md` `../../` → `../../../` `.agents` links. Documented `superpowers-mcp` provenance in **`third_party/THIRD_PARTY.md`** (MIT per `package.json`, upstream `LICENSE` absent — not fabricated).
- **`infra/mcp-servers/cluster/seed/agent-skills.jsonl`**: retired the stale `severity:must` rule forbidding the sync/dedup timers — both `dedup-graph.py` and `omnigraph-sync.sh` are multi-graph now (slug kept stable so `merge`-load upserts, not duplicates).
- **docs/other**: `homelab.example.yaml` `allow_run` defaults false (SSH command-exec server, deny list is a UX guardrail); repointed retired-`agent-skills` paths in `herdr` README/`start-session.ps1` and the `setup-agent-memory.{sh,ps1}` comments and hint text (no path into `agent-skills` remains in a runtime file — the surviving mentions are provenance prose and `github.com/...` URLs marked archived, plus the omnigraph graph *id* `agent-skills`, which is data, not a path); `omnigraph-mcp` bridge pinned 0.8.1 to match the server; `.env.example`/`site.env.example` blanks and dead placeholders cleaned; `docker-compose.server.yml` `:?` messages name `.env.server`/`.env.shared`; `seed/README.md` schema link resolves into `.agents/skills/`; `infra/.gitattributes` trimmed to non-duplicate rules; `AGENTS.md` and `README.md` gained an `infra/` row; `cao-setup/README.md` filled (was 0 bytes).
### Changed — the Linux installer no longer depends on the agent-skills clone (asm-b1, 2026-09-27)

- **`lib/linux/install.sh`**: `install_agent_skills` clones nothing any more. Skills come from this checkout's `.agents/skills`, `omnigraph` and `autoos-agent` are pinned through AutoOS's own `.mcp.json` (`enable_project_mcp_server`), and an existing `~/Documents/Code/agent-skills` is left alone with a one-line retirement hint. Detection of the `agent-skills` catalog id (the profiles still name it) now means "this checkout has `.agents/skills` and `.mcp.json`", with the clone dir as the mid-migration fallback; so does every skills link, which `autoos_skills_source` resolves once for Antigravity, Claude Code (global and project), `~/.agents/skills`, `~/.codex/skills` and OpenHands instead of only the last two. `~/.local/bin/graphify-mcp` is (re)pointed at `infra/mcp-servers/bin/graphify-mcp` in this checkout when it is missing or points into an `agent-skills` path — **including one left dangling when the user deleted the clone**, which the first cut of the check mistook for a user-managed link — and a user's own file or symlink is left alone; the dry run decides from the same verdict the real run acts on. API keys resolve through one new helper, `autoos_api_keys_conf`, at both former call sites: `~/.config/autoos/api_keys.conf`, then the repo's git-ignored keys file (`AUTOOS_KEYS_FILE` overrides that entry, as everywhere else), then the legacy `agent-skills/secrets/api_keys.conf`; nothing is created and no path is invented when there is none. Windows (`lib/windows/*.psm1`, whose `agent-skills` entry still clones) is untouched — Windows CI is off.
- **`tools/keys_file.py`** (new): the one reader for a flat keys file, understanding both `name=value` (`api_keys.conf`) and `name: value` (`api-keys.yml`), skipping `REPLACE` placeholders and reading a missing file as no keys. The bug it closes: the helper above hands a `.yml` to readers that had only ever parsed `k=v`, so a configured machine came out keyless and looked exactly like an unconfigured one. `lib/linux/install.sh`'s two inline parsers and the OpenHands OmniRoute gateway lookup are deleted in favour of it, and `route_detected_clis_to_gateway` now sees the same chain (a key in the user conf used to be invisible to it).
- **`lib/agent_harness.py`**: an empty `--skills-source` no longer writes `<skill>/SKILL.md` — a relative path that resolves nowhere — into the user's OpenCode `instructions`; it reports `skills: source missing` instead.
- **`catalog/linux.json`**, **`catalog/macos.json`**: the `agent-skills` entry says it wires the stack from this checkout, and both that entry's and `mcp-graphify`'s `homepage` point at AutoOS instead of the retired repo. `catalog/windows.json` keeps the clone wording (true there) and only the homepage changed.
- **`docs/catalog.md`**, **`docs/omnigraph.md`**, **`docs/openhands.md`**, **`AGENTS.md`**: the key-file order, the skills source, the omnigraph image build path, `trust_worktree.py`'s location and the skill-provenance pointer all name AutoOS paths; `AGENTS.md` no longer cites a `THIRD_PARTY.md` that left with the retired repo. Dated records (`docs/plans/`, `docs/decisions/`, `docs/research/`, `docs/archive/`) and the changelog keep their original wording.
- **`tests/linux/18-mcp-wiring.sh`**, **`tests/linux/33-documentation.sh`**, **`tests/test_keys_file.py`**, **`tests/test_agent_harness.py`**: fail-first tests for all of the above (11 of them red against the pre-change installer), driven through the entry points production takes — `setup_opencode_config` and `install_agent_skills` with the sibling writers stubbed, not a re-derived copy of the lookup order. The keys-order tests assert on the path the helper *prints*; every tier's file exists in each sandbox, so reading one back would only prove the test wrote it. `tests/test_keys_file.py` is run from the Linux suite because `tests/test_suite_wiring.py` refuses a unit-test file no harness executes. Keys tests use dummy values in temp HOMEs only.

### Added — infra/ imported from agent-skills (retired) (asm-a, 2026-09-27)

- **`infra/`**: imported `infra/mcp-servers/`, `infra/local-ai/`, `infra/remote-access/` and `scripts/public-scrub/` from Ch3fUlrich/agent-skills cfb4fc6. Added `.env.*.example` templates for all compose files (**false at import** — the root `.gitignore`'s broad `.env.*` swallowed the `infra/mcp-servers/` ones so none was tracked; corrected in asm-a2), fixed hardcoded API key in `perplexity-pipefunction.py` to read `PERPLEXITY_API_KEY` from environment with no default, fixed 8 broken links in infra docs (marked as archived references to retired agent-skills repo), added `infra/.gitignore` (ignores real env files, cluster.yaml, seeds, runtime data), `infra/.gitattributes` (LF for shell/Python/systemd/Docker, CRLF+BOM for PowerShell), and `infra/README.md` with provenance. Shellcheck warnings fixed in `apply-cluster.sh`, `setup-cao.sh`, `setup-agent-memory.sh`. All tests pass.

### Changed — t1-orchestrator keeps a free gemini leg (T1FREE, 2026-09-27)

- **`catalog/ai-registry.json`** + renders: `gemini/gemini-3.8-flash` appended to `t1-orchestrator` and `t1-orchestrator-free-only`, so the default model of start-stack, the installer and OpenHands stays servable while OpenRouter, DeepSeek and cheapinference credit is out; `t1-orchestrator-clean`, `spark-1.3-contributor`, `deepseek-v4.1-flash` and the cheaperinference pins stay omitted. Open: `t1-orchestrator-paid` (LiteLLM picker) has no leg until MUSEAPI.

### Changed — review fixes + OpenRouter off, `deepseek-v4.1-flash` native with effort ladder (PROVFIX, 2026-09-27)

- **`catalog/ai-registry.json`**: `providers.openrouter.available: false` — the operator's DSMAX decision (2026-09-27T15:05:54Z via L0): a re-probe hit 401 "insufficient credits" even for BYOK, so the provider is off entirely (not just per-leg); this also covers `openrouter/meta/muse-spark-1.3-contributor` (Muse is reachable as the free Zen leg through the opencode client only). `routes.deepseek-v4.1-flash` dropped the OpenRouter leg for native `deepseek/deepseek-flash` → `opencode-zen/deepseek-v4.1-flash`, and `models.deepseek-flash` gained `reasoning: true` plus the native `effort_ladder` `["none","low","high","max"]` (restoring `#low`/`#high`/`#max` while it lasted). Then `providers.deepseek` hit 402 Insufficient Balance (2026-09-27T16:4xZ, `available: false`): the native leg is gated too, so `routes.deepseek-v4.1-flash` has no servable leg left and FAILS CLOSED like `t1`/spark — its combo is omitted and its IDE/opencode/LiteLLM/OpenHands entries are removed until the balance is topped up (the ladder stays in `models.deepseek-flash`, so the aliases re-render unchanged). `t2-worker-clean`/`t3-driver-clean` still serve via `mistral-small`; `t2-worker`/`t3-driver` serve gemini/antigravity/cheap-inference/mistral. `routes.<t1-orchestrator|t1-orchestrator-clean|spark-1.3-contributor>` have no servable gateway leg left (Zen is client-bound, OpenRouter is off), so those three combos are omitted and their client models removed. `routes.deepseek-v4.1-flash` (`openhands_profile`) gets `reasoning: true`.
- **`tools/registry.py`**: `leg_rule_for()` now matches a leg under its canonical `omniroute_id` spelling as well as the raw string, so a providers-key spelling such as `cheapinference/glm-4.5-air` hits `deny-cheaperinference` (finding 3).
- **`tools/sync-router-tiers.py`**: `provider_maps_from_dict()` emits name keys in a first pass and only adds an `omniroute_id` key when absent, matching `resolve_leg`'s name-first precedence (finding 4).
- **`tests/helpers/check-provider-registry.py`**: asserts no provider name equals another provider's `omniroute_id`, and compares the `docs/api-keys.md` provider-id column against each non-null `omniroute_id` (findings 4, 5).
- **`docs/api-keys.md`**, **`configuration/omniroute/combos.json`**, **`catalog/ide-models.json`**, **`opencode.jsonc`**, **`configuration/litellm/config.yaml`**, **`configuration/openhands/tier-profiles.json`**, **`docs/models.md`**, **`.agents/skills/unattended-orchestration/unattended-orchestration.md`**: review prose/comment corrections (findings 1, 2, 6, 7), the `free-ai` provider-id cell, combos/tier/IDE/openhands regenerated for the DSMAX change, and the effort aliases re-rendered per surface.
- **`tests/test_registry_render.py`**, **`tests/test_registry.py`**: `free_ai/` AND `free-ai/` both asserted absent from every `-clean` combo (finding 11); the `$comment` provenance assertion on `routes.t2-worker.unavailable_legs["openrouter/openai/gpt-oss-120b"]` re-added (finding 12).

### Changed — cheaperinference re-enabled (3 legs), cerebras off, built-in `free-ai`, native DeepSeek first (PROV, 2026-09-27)

- **`catalog/ai-registry.json`**: cheaperinference re-enabled with a small balance (the 10:12Z "no top-up" note is withdrawn): only `cheaperinference/kimi-k3`, `cheaperinference/glm-5.2` and `cheaperinference/minimax-m2.7` may serve (`policy.leg_rules` allow entries, then a trailing `deny-cheaperinference`); `cheaperinference/glm-4.5-air` is route-gated in `t2-worker`/`t3-driver` and `cheaperinference/deepseek-v4-flash` keeps its `t2-worker` gate. `cerebras.available: false` (no free tier, no credits; its legs stay in the data, route-gated, so the intent is visible). `free_ai` gains `model_prefix: "free-ai"` and moves its `omniroute_id` to the built-in OmniRoute connection `free-ai`: registry legs stay `free_ai/qwen7b` while gateway combos spell `free-ai/qwen7b` (same shape as `antigravity` → `agy`), and `apply` addresses the existing built-in connection instead of registering a duplicate. `routes.deepseek-v4.1-flash` now leads with the native `deepseek/deepseek-flash` leg before `openrouter/deepseek/deepseek-v4.1-flash`.
- **`configuration/omniroute/combos.json`**: two pinned single-leg combos `cheaperinference/glm-5.2` and `cheaperinference/kimi-k3`; `t2-worker` gains `cheaperinference/kimi-k3`; `t3-driver` gains `cheaperinference/glm-5.2`, `cheaperinference/kimi-k3` and `cheaperinference/minimax-m2.7`; the free-only combos spell `free-ai/qwen7b`.
- **`configuration/litellm/config.yaml`**, **`catalog/ide-models.json`**, **`opencode.jsonc`**, **`docs/models.md`**: regenerated from the registry (`registry.py render …`); the new `cheaperinference/glm-5.2` and `cheaperinference/kimi-k3` managed blocks/IDE models appear, and the native DeepSeek leg is first. The `deepseek-v4.1-flash` lead leg changed from `openrouter/deepseek/deepseek-v4.1-flash` (which supplied the `low/high/max` effort ladder) to `deepseek/deepseek-flash` (empty ladder at the time), so opencode/Zed temporarily dropped `#low/#high/#max` on that id — restored natively in PROVFIX (DSMAX).
- **Reverted**: the OpenRouter BYOK `openrouter/openai/gpt-oss-120b` un-gate (C) was reverted after a re-probe hit 401 (credits exhausted), so `t2-worker` keeps its `available: false` gate; the per-leg allow rule stays in place, and the un-gate's `3/3` measurement note is removed. Pinned assertions re-pinned in **`tests/test_registry_render.py`**, **`tests/test_registry.py`**, **`tests/linux/17-ai-routing.sh`**, **`tests/linux/33-documentation.sh`**, **`tests/run-tests.ps1`**.

### Fixed — `provider_maps_from_dict` keys transport by providers-key or `omniroute_id` (PROV, 2026-09-27)

- **`tools/sync-router-tiers.py`**: each provider's LiteLLM transport is now keyed by both its providers key and its `omniroute_id` (deduplicated), because `resolve_leg()` accepts either as a leg prefix. This lets `free_ai/qwen7b` keep `litellm_prefix: openai` + `api_base: https://api.free.ai/v1` + `FREE_AI_API_KEY` even though the gateway catalog names the model `free-ai/qwen7b`; the config is unchanged and the registry comment now matches it. Mirrored in **`tests/helpers/check-provider-registry.py::expected_by_omni`** and asserted in **`tests/test_sync_router_tiers_registry.py`**.
### Fixed — review batch: dropped track records, per-attempt fallthrough records, shared registry loader (REVFIX, S1-S2, 2026-09-27)

- **`tools/autoos_track.py`**: `FAILURES` now names `containment` and `provider`, so a `record_run()` for a LEAK (rc 7) or a provider stop (rc 8) is no longer rejected by `validate()` and silently dropped — every `failure_class` `tools/autoos-agent.py`'s `track_entry()` emits is accepted.
- **`tools/autoos-agent.py`**: each provider-stopped attempt in the `--isolate` fallthrough loop writes its own `track_entry`/`record_run` (gate fail, failure_class `provider`, its own route), not only the final plan; `tests/test_autoos_spawner.py::ProviderStopFallthroughTests` asserts one track line per attempt.
- **`tools/autoos_measure.py`**: coverage matches on whole tokens, so a test name covering any token of a multi-token stem counts as covered.
- **`tools/registry_loader.py`** (new): the one importlib-by-path loader for `tools/registry.py`; `tools/sync-ide-models.py`, `tools/audit-router.py`, `tools/mirror-litellm-env.py` and `tools/sync-openhands-profiles.py` import it instead of each carrying an identical copy. `tools/sync-router-tiers.py` guards its `sys.path.insert` against duplicates. `tools/probe_common.py:make_post()` gains a `classify_error` hook and `tools/probe-toolcalls.py`'s forked post is deleted.
- **Tests**: `tests/test_registry_loader.py` (new, wired into both suites), the suite-wiring guard `tests/test_suite_wiring.py` (every `tests/test_*.py` must be named by a harness), and the `sk-test-dummy` fixture in `tests/linux/34-ai-services.sh` is renamed `TEST-ONLY-fake-litellm-key` (secret-scanner false positive); its three duplicated loopback HTTP servers become one `_svc_loopback_server` helper.

### Added — route by client shell/write capability, fall through on a provider stop (SPAWNCAP, S2, 2026-09-27)

- **`catalog/ai-registry.json`**, **`catalog/ai-registry.schema.json`**: every client now declares `capabilities` (`shell`, `write`); new `$defs.capabilities` requires both and forbids extras, and `$defs.client` requires it. opencode/claude/codex/gemini/qwen declare `true/true`; agy and qoder declare `false/false` (source: the `HEADLESS_REFUSAL_MARKERS` refusals and qoder's `--permission-mode dont_ask, no shell`, `docs/agent-protocol.md:160`).
- **`tools/autoos-agent.py`**: `run` now refuses *before* anything is planned or started when the task needs `shell`/`write` and the named `--client` lacks one (exit 2, naming the missing capability and the clients that have it); with no `--client` it picks the first capable client (`choose_client`, `opencode` when nothing is required). A run needs shell or write when `--isolate` is set or the card is *explicit* editing (`kind` implement/debug/bulk; v1 `role=implement`); an absent, read-only (`review`/`research`/`plan`) or defaults-only card is never gated, so the existing qoder/privacy tests keep their behaviour.
- **`tools/autoos-agent.py`**: a provider-stopped resolver-routed `--isolate` run now WIP-commits the stopped attempt and re-runs the same task in the **same sandbox** on the next route (at most 2 fallthroughs), instead of stranding the work on a dead route. Each fallthrough is logged exactly as `provider stop on <route>: <marker> -> falling through to <next>`; the gateway's `ALL_TARGETS_SKIPPED` ("all targets were skipped by pre-dispatch filters") and `credits exhausted` join the provider-stop markers. A `--tier`/v1-card/`--joinable` run keeps today's immediate exit 8.
- Tests: `tests/test_autoos_spawner.py::ClientCapabilityTests` and `::ProviderStopFallthroughTests`.

### Changed — `tests/run-tests.sh` refuses an unfiltered local run (FULLGUARD, 2026-09-27)

- **`tests/run-tests.sh`**, **`.github/workflows/ci.yml`**, **`tests/linux/01-test-harness.sh`**, **`AGENTS.md`**, **`docs/`**: an unfiltered local run now exits 2 and names `--filter` / `AUTOOS_FULL_SUITE=1`; CI sets the opt-in so its one full run is unchanged (the full suite's shellcheck once OOM-killed a 16 GB host, R-host-08).
### Changed — orchestration skill cut to 30 level rules (S1, 2026-09-27)

- **`.agents/skills/unattended-orchestration/SKILL.md`**: ~130 topic rules become 30 rules (R-orch-13 different-family review before every ready, operator 2026-09-27T14:3xZ) grouped by level (`router` L0, `coord` L1, `orch` L2, `worker` L3); mechanical rules point to `autoos-agent.py heartbeat`, the resolver and `registry.py` instead of restating them. New rules from 2026-09-27 lessons: sudo/root changes always get the Sonnet final, test fakes follow the real tool's contract, data lanes grep all of `tests/` for changed ids. **`references/rule-map.md`** maps every old id; **`tests/test_skill_rules.py`** fails when a cited `R-` id resolves nowhere.

### Added — Free.ai free provider restores `t3-driver-free-only` (FREEAI, 2026-09-27)

- **`catalog/ai-registry.json`**, **`configuration/omniroute/combos.json`**, **`configuration/litellm/config.yaml`**, **`catalog/ide-models.json`**, **`configuration/openhands/tier-profiles.json`**, **`opencode.jsonc`**, **`docs/models.md`**, **`configuration/omniroute/apply.sh`**, **`configuration/api-keys.example.yml`**, **`docs/api-keys.md`**: Free.ai (`free_ai`, model `qwen7b`, OpenAI-compatible at `https://api.free.ai/v1`; 30k tokens/day, 10 rpm, public/may train) joins as the last leg of `t2-worker-free-only` and the only leg of `t3-driver-free-only`, restoring the latter as a servable combo; never private-safe and never in a `*-clean` route. `apply.sh`'s existing-id regex widens to `[a-z0-9_-]+` so the underscored `omniroute_id` stays idempotent.
### Changed — Antigravity Hub sets up chrome-sandbox itself via sudo (agysb)

- **`lib/linux/install.sh`**: `antigravity_sandbox_note` is now
  `antigravity_sandbox_setup` (operator decision 2026-09-27: this one step may
  run sudo). When the kernel needs the SUID helper it re-checks
  `chrome-sandbox` (regular file, no symlink, 1 hard link), skips an already
  root-owned 4755 helper, prints the would-run line under `--dry-run`, and
  otherwise runs `sudo -n chown root:root` + `sudo -n chmod 4755` with
  `setup.sh --yes` (plain `sudo`, so it may prompt, on an interactive TTY
  without `--yes`); a missing/failed sudo prints the two commands for the
  operator and never fails the install. NEVER `--no-sandbox`.
- **TOCTOU fix**: each privileged step re-checks inside the one root process
  (`find -P` on a still-regular single-link file, chmod only on a file root
  owns), so a symlink swapped in after the check can no longer escalate;
  anything else falls back to the printed commands.

### Added — Free.ai free provider restores `t3-driver-free-only` (FREEAI, 2026-09-27)

- **`catalog/ai-registry.json`**, **`configuration/omniroute/combos.json`**, **`configuration/litellm/config.yaml`**, **`catalog/ide-models.json`**, **`configuration/openhands/tier-profiles.json`**, **`opencode.jsonc`**, **`docs/models.md`**, **`configuration/omniroute/apply.sh`**, **`configuration/api-keys.example.yml`**, **`docs/api-keys.md`**: Free.ai (`free_ai`, model `qwen7b`, OpenAI-compatible at `https://api.free.ai/v1`; 30k tokens/day, 10 rpm, public/may train) joins as the last leg of `t2-worker-free-only` and the only leg of `t3-driver-free-only`, restoring the latter as a servable combo; never private-safe and never in a `*-clean` route. `apply.sh`'s existing-id regex widens to `[a-z0-9_-]+` so the underscored `omniroute_id` stays idempotent.

### Changed — standby router renders every servable tier; starter host/key-file/state-dir (LSTBY)
### Added — one-command standby router: `ai-stack.sh failover` (lstby)

- **`configuration/docker/ai-stack/ai-stack.sh`**: new `failover on|off|status`
  (in `--help` and the unknown-command list). `on` refuses (rc 2) when already
  on, else stops the omniroute container and starts LiteLLM through
  `configuration/litellm/start-litellm.sh` with `AUTOOS_LITELLM_HOST` (the
  gateway's bind), `AUTOOS_LITELLM_PORT` (the gateway's port),
  `AUTOOS_LITELLM_MASTER_KEY_FILE` (`client.key`, 0600 — the value is
  redirected, never argv/printed) and `AUTOOS_LITELLM_STATE_DIR` (its own dir,
  so the always-on `:4000` unit is untouched); waits for `/health/liveliness`
  (60 s), and on failure stops the standby, restarts the gateway (rc 1 — the
  port is never left empty). `off` stops only the standby pid, restarts the
  gateway, waits for `/api/health`, removes the state file (already off is a
  no-op, rc 0). `verify` prints `failover ON: LiteLLM serves the gateway port`
  while the standby holds the port (informational, never FAIL). Clients keep
  base URL, key and combo ids. Tests: nine `aistack: failover …` cases plus a
  `start-litellm` stand-in in `tests/helpers/aistack_fake.sh` (env names, never
  values; liveliness ok/fail via marker). Docs: `docs/web-services.md`
  "Standby router (LiteLLM)".

### Fixed — probe-toolcalls uses the shared leg selection (ptc)

- **`tools/probe-toolcalls.py`**: deleted its local `_skip_reason`/`legs_to_probe` copies and imports both from **`tools/probe_common.py`**, so a `policy.leg_rules`-denied leg is skipped with `policy: denied by <id>` and only free legs are probed (D18); its own `make_post`/400 body classification is unchanged. Tests: new `DenyRuleTests` in `tests/test_probe_toolcalls.py`; existing leg fixtures now declare `tier: free` (no skip-reason assertion needed rewording — the shared wording matches).

### Added — Free.ai in the key guide

- **`configuration/litellm/config.yaml`**, **`tools/sync-router-tiers.py`**, **`tools/registry.py`**: every registry route `gateway_legs` can serve through LiteLLM is now an `AUTOOS-MANAGED` block (12 groups) regenerated from the registry, replacing the hardcoded `SYNCED_TIERS` (`t2-worker`, `t3-driver`) pair. A route whose final servable set is empty gets no group (instead of the render raising); the legless `*-paid` chains stay hand-curated. New `managed_tiers` / `litellm_servable_refs`; `registry_refs`/`combos_refs` default to every servable tier. Tests: `tests/test_registry_render.py`, `tests/test_sync_router_tiers_registry.py`, `tests/linux/17-ai-routing.sh`.
- **`configuration/litellm/start-litellm.sh`**, **`configuration/litellm/start-litellm.ps1`**: `AUTOOS_LITELLM_HOST` (bind address), `AUTOOS_LITELLM_STATE_DIR` (where `litellm.log` lives) and `AUTOOS_LITELLM_MASTER_KEY_FILE` (a private file whose single line overrides `.env`; unreadable or empty is a hard error). Values are never printed. Tests: `tests/linux/34-ai-services.sh`.
### Fixed — gateway renders use the catalog's agy/* ids for antigravity legs (AGYID, 2026-09-27)

- **`tools/registry.py`**: new `gateway_ref(leg, registry)` is the single translation point between a registry leg and the id the live OmniRoute catalog serves: a provider declaring `model_prefix` has each leg rewritten to `<model_prefix>/<model>`, every other leg (or an unresolvable/non-string one) is returned unchanged. `render_omniroute` maps its legs through it, so an antigravity leg renders as `agy/...`; the registry keeps its own `antigravity/...` spelling and `omniroute_id` stays `antigravity`, so `resolve_leg` and `apply.sh`/`apply.ps1` are untouched.
- **`catalog/ai-registry.json`**, **`catalog/ai-registry.schema.json`**: `providers.antigravity.model_prefix: "agy"` (sourced: the live catalog's `/v1/models` names antigravity models `agy/*`, never `antigravity/*`; L0 live apply 2026-09-27T11:44:31Z skipped the `antigravity/*` legs for exactly this) and the optional `model_prefix` field documented under `$defs.provider`.
- **`configuration/omniroute/combos.json`**: the four antigravity refs (t2-worker, t2-worker-free-only, t2-orchestrator, opus-4-6) now read `agy/...`, so the gateway stops skipping them.
- **`tools/sync-router-tiers.py`**: `GATEWAY_ONLY` also carries `agy` (the gateway spelling of antigravity): `combos_refs()` reads an already-rendered combos.json, so the LiteLLM mirror must drop `agy/*` exactly as `registry_refs()` already drops `antigravity/*`. Tests: `tests/test_registry.py::GatewayRefTests`, `tests/test_registry_render.py::GatewayRefTests`, `tests/test_sync_router_tiers_registry.py::CliDefaultsToRegistryTests::test_explicit_combos_override_still_works`, `tests/linux/17-ai-routing.sh` (free-only `known_drops`).

### Changed — Claude Code (cc) legs unavailable (operator 2026-09-27: never connected to OmniRoute)

- **`catalog/ai-registry.json`** `providers.cc.available: false` (sourced); `opus-4-6` and `t2-orchestrator` keep their antigravity opus-4-6-thinking leg, the cc leg leaves combos.json.

- **`configuration/docker/ai-stack/compose.yml`**: the omniroute service caps the
  per-call log artifacts that fed page cache (`CHAT_LOG_MAX_BODY_KB=64`,
  `CHAT_LOG_TEXT_LIMIT=16384`, `CALL_LOG_RETENTION_DAYS=3`; image defaults
  1024/65536/7). Measured 2026-09-27: 831 MB in 3905 files under
  `/app/data/call_logs` in one day left `memory.current` at 2.62G of 2.68G max
  with only 0.83G `anon` (1.65G reclaimable `file`), and the gateway's
  pressure guard (503 at >= 92%, not configurable in 3.8.50) tripped 48x/h.
  The caps reach the gateway only when its container is recreated with the new
  env (`ai-stack.sh up omniroute` recreates it because the compose config
  changed). Documented as commented defaults in **`stack.env.example`**.
- **`configuration/docker/ai-stack/ai-stack.sh`**: new `restart <service>`
  subcommand (one compose restart through the same wrapper as the other
  subcommands, no backup; unknown name is a usage error, rc 2), and `verify`
  prints the real cgroup memory split (`omniroute memory: current … of …
  (…%), anon …, reclaimable cache …` from the `file` line of `memory.stat`,
  read with cat+sed only; "no limit" with no ratio when `memory.max` is `max`),
  informational like the admission gate, and warns with the root-free relief
  (`ai-stack.sh restart omniroute`; measured 11:04Z, `memory.current`
  2215M -> 786M) when page cache - not anon - holds the guard at 503.
  Docs: `docs/web-services.md` "Page-cache pressure guard".

### Changed — cheaperinference disabled (operator 2026-09-27T10:12Z: no top-up, no free tier) (OR1h)

- **`catalog/ai-registry.json`** `providers.cheapinference.available: false` (sourced); every gateway declaration drops its legs (t2-worker, t3-driver) and the two single-leg combos (`cheaperinference/kimi-k3`, `cheaperinference/glm-5.2`) move to combos.json `omitted`, so `apply.sh` prunes them live.

### Changed — Opus/Fable orchestrator hand-off cap 600k (operator 2026-09-27)

- **`catalog/ai-registry.json`** `policy.handoff_caps.claude-opus-1m`: 0.6 × 1M = 600000 (was 400000), sourced; `tools/autoos_context.py` DEFAULT_CAPS fallback follows so a registry-less run agrees.

### Fixed — "omitted" holds only orphaned routes; Windows apply prunes it too (OR1g)

- **`tools/registry.py`**, **`configuration/omniroute/combos.json`**, **`configuration/omniroute/apply.ps1`**: `render_omniroute` names a route in `"omitted"` only when it declared legs but `gateway_legs` serves none (orphaned) — a deliberately legless route (`legs: []`: `auto`, `auto/cheap`, `auto/smart`, `t1-orchestrator-paid`, `t2-worker-paid`, `t3-driver-paid`) is no longer listed, so apply can never delete a live combo with one of those ids. `apply.ps1` now prunes `retired` + `omitted` like `apply.sh` (`<id>: omitted, would delete`/`deleted`), removing the platform divergence. Tests: `tests/test_registry_render.py::OmittedRoutesListTests`, `tests/linux/34-ai-services.sh`, Pester `apply prune…` / `combos.json is valid…`.

### Fixed — apply prunes managed combos the registry omitted (OR1e)

- **`configuration/omniroute/apply.sh`**, **`tools/registry.py`**, **`configuration/omniroute/combos.json`**: `render_omniroute` now emits an `"omitted"` list — every route it renders no combo for (no `gateway_legs`) — and `apply.sh` prunes `retired` + `omitted` from the live store, never a user-made combo and never a current one. `--drift` labels an omitted live combo `extra <id> (omitted: no servable leg)`, and a bare-JSON `combo list` answer is an unreadable store (exit 3, one reason line) instead of an `AttributeError` crash. The LiteLLM `t2-worker-free-only` group already mirrors only the servable leg. Tests: `tests/linux/34-ai-services.sh`, `tests/test_registry_render.py::OmittedRoutesListTests`, Pester `combos.json is valid…` / `provider data JSON…`.

### Fixed — OR1f resolver honours policy.leg_rules (2026-09-27)

- **`tools/autoos_resolver.py`**: `usable_legs` now skips every leg `policy.leg_rules` denies, with a reason naming the rule, so the resolver plans only the legs `registry.gateway_legs` serves (a gateway combo never carries a denied leg).
### Fixed — a route with no servable leg is offered by no declaration (OR1d)

- **`tools/registry.py`**: new `servable_route_ids(registry)` names every route with at least one `gateway_legs`; `render_ide` and `render_openhands` now drop any route that *declares legs* but has none servable, matching the leg filter `render_litellm_blocks` already applied. The four all-legs-dead routes (`t1-orchestrator-free-only`, `t3-driver-free-only`, `samba/gpt-oss-120b`, `samba/MiniMax-M3`) disappear from `catalog/ide-models.json`, `configuration/openhands/tier-profiles.json`, the litellm gateway blocks and `opencode.jsonc`; deliberately legless routes (`*--paid`, `auto*`) stay. Tests: `tests/test_registry_render.py::NoServableLegOffersNoDeclarationTests`.

### Fixed — R4 review fixes (R4FIX, 2026-09-27)

- **limits/resolver/context**: `_check_provider_limits` now rejects an unknown key in a `providers.<id>.limits.<model>` entry (naming provider, model and key; the allowed set mirrors the schema's `provider_limits`); the tpm filter's keep-on-equal boundary and input-only estimate are pinned/documented; `load_caps` treats a `handoff_caps` row missing `cap_fraction` as unusable (`source: default`); the live groq limits test asserts shape only, its exact console numbers moved to an inline-registry test.

### Changed — orchestration skill: 2026-09-27 lessons folded as rules (routing v2 D10/D20)

- **`.agents/skills/unattended-orchestration/SKILL.md`**: 14 new rules (R-spawn-23/24, R-review-09/10, R-tests-23, R-gateway-15/16/17, R-brief-06/07/08, R-level-02, R-handoff-10/11), each with its measured source; R-spawn-18, R-review-05/08, R-tests-03 and R-handoff-04 sharpened (a review pins its model because `--card role=review` routes to t3-driver; gates need `set -o pipefail` + the passed count; the handoff cap comes from `policy.handoff_caps`).
- **`references/state-file.md`** (new): the status/state file template and the handoff procedure (C4 skill-edit request).
### Fixed — AGYFIX: agy command form, its default model, its quota exit; --free now Zen Muse Spark

- **`tools/autoos_clients.py`**: agy's `build_command` emits `--model <m>` **before** `-p <task>`
  (`agy --model <m> -p <task>`), the form measured working in the 2026-09-27 K3 CLI audit; the old
  `-p --model <m> <task>` made agy 1.2.12 read `--model` as the print prompt and ignore the task.
- **`tools/autoos_clients.py`**: new `AGY_DEFAULT_MODEL = "claude-opus-4-6-thinking"`, supplied when the
  caller passes no model: agy's own default Gemini quota is out until ~2026-10-01 and exits 3 after
  ~157 s, while `claude-opus-4-6-thinking` measured PONG in 8 s.
- **`tools/autoos-agent.py`**: a provider-stop tail now upgrades to exit 8 from rc 3 as well as 0 and 6,
  so agy's quota exit (`AGY_ERROR: ... RESOURCE_EXHAUSTED (code 429) ... quota reached`, rc 3) is a
  retryable provider stop for unattended recovery, not the client's own code. `provider_stop()` already
  matched the line; only the rc gate and the exit-code doc changed.
- **`tools/autoos-agent.py`**: `DEFAULT_FREE_MODEL` is now `opencode/muse-spark-1.3-contributor-free`
  (Zen Muse Spark 1.3 through the opencode client, measured 200 on 2026-09-26) instead of
  `opencode/big-pickle`; `--free --clean` stays refused.
- Tests: `tests/test_autoos_spawner.py` — `test_agy_puts_an_explicit_model_before_the_print_prompt`,
  `test_agy_default_model_is_the_measured_working_one`,
  `test_an_agy_quota_stop_that_exits_3_is_still_a_provider_stop`,
  `test_free_default_is_the_operators_muse_spark_leg`; `test_agy_uses_its_own_login` and
  `test_run_goes_ahead_when_agy_is_signed_in` updated for the new agy form and
  `test_sensitive_card_with_free_is_refused` for the new promo model.
- **`configuration/omniroute/apply.sh --drift`** (OR1b): compares the live combos against `combos.json` (name + ordered legs, `retired` ids ignored) without writing; exit 0 in sync, 1 on any `drift`/`missing`/`extra` line, 3 when the store cannot be read.
### Fixed — gateway renders serve only usable legs (OR1a)

- **`tools/registry.py`**: both gateway renders now share a new `gateway_legs(route, registry)` that drops every leg the registry marks unavailable, `policy.leg_rules` denies, or that resolves to a `client_bound` model; `configuration/omniroute/combos.json` and `configuration/litellm/config.yaml` drop those legs too (tests: `tests/test_registry_render.py::GatewayLegsFilterTests`).

### Added — provider rate limits as registry data + resolver request-size filter (R4)

- **`catalog/ai-registry.schema.json`**: new `providers.<id>.limits` field (object keyed by the provider's own model spelling) and a `provider_limits` def (`rpm`/`rpd`/`tpm`/`tpd` non-negative ints, each optional; `source` required). The renders never read it; resolver-only.
- **`catalog/ai-registry.json`**: `providers.groq.limits` carries the three free-tier models measured from the Groq console (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`): 30 rpm, 1000 rpd, 8000 tpm, 200000 tpd, source `operator Groq console screenshot 2026-09-27`. A `models."openai/gpt-oss-20b"` entry was added (mirrors the 120b sibling; non-limit fields unsourced defaults) so the limits key resolves via `resolve_leg`. `version` bumped to `2026-09-27`.
- **`tools/registry.py`**: `check_registry` rule 10 (`_check_provider_limits`) validates every limits key resolves via `resolve_leg("<provider>/<key>")` (unknown key = error naming it) and every value is a non-negative int.
- **`tools/autoos_resolver.py`**: `usable_legs` skips a leg whose provider limits for that model carry `tpm` when `need_tokens * 1.3 > tpm` (reason `limit: <provider>/<model> tpm <tpm> < need <n>`), counted as a skipped leg like the context filter. New `provider_tpm()` helper. No clock, no counters: `rpm`/`rpd`/`tpd` are data only for now. This is the data+filter half that lets groq come back safely once the separate `deny-groq` 400 bug is measured fixed (that rule stays).
- Tests: `tests/test_registry.py::ProviderLimitsTests`, `tests/test_autoos_resolver.py::ProviderLimitsFilterTests`.

### Added — gateway attribution (OR3)

- Spawned opencode runs on the omniroute provider send `x-omniroute-session-id: <lane>/<title>` (env `AUTOOS_SESSION_TAG` overrides) via the `OPENCODE_CONFIG_CONTENT` overlay's provider `headers`, so OmniRoute `call_logs.session_tag` attributes every spawned call.

### Fixed — TOOLFIX: four small rule→code fixes (probes, audit-router, spawner, measure)

- **`tools/probe_common.py`**: `_skip_reason` gained a policy-deny rule (checked first): a leg that
  `registry.leg_denied` denies is skipped with `policy: denied by <rule id>`, and the provider check
  uses `registry.unavailable_now` (UNTILfix self-heal). With the free-tier rule (spec D18) paid and
  policy-denied legs are never probed. `tools/probe-toolcalls.py` keeps its own copy until it moves
  onto probe_common (L1-backlog). Test: `tests/test_probe_recall.py` (`DenyRuleTests`).
- **`tools/audit-router.py`**: `_chat_once` no longer forwards provider error bodies or exception text
  into its result. The `HTTPError` branch returns `"HTTP %d"` and the transport branch returns
  `"transport error: %s" % type(exc).__name__`; the provider's JSON `detail`/`error` body and the raw
  exception message never reach the audit verdict. Tests: `tests/test_audit_router_probe_retry.py` —
  `test_always_503` and `test_non_http_exception` now assert the neutral detail strings (the old
  `assertIn` on the provider's error text is the one named assertion change), and a new test asserts an
  org secret planted in a provider 503 body never appears in the result.
- **`tools/autoos-agent.py`**: `PROVIDER_STOP_MARKERS` now includes `"no active credentials for
  provider"`, so a run that dies with `Error: No active credentials for provider: <name>.` is classified
  as a provider stop (retryable, not a containment failure) instead of leaking through as a crash.
  Only the marker list is touched. Test: `tests/test_autoos_spawner.py`
  (`test_provider_stop_matches_no_active_credentials`).
- **`tools/autoos_measure.py`**: `tracked_files` dedups `git ls-files -z` output order-preserving. During
  an unresolved merge `git ls-files` lists a conflicted path three times (stages 1/2/3), which inflated
  the measured file count and double-counted diffs. Tests: `tests/test_autoos_measure.py`
  (`ConflictDedupTests`) build a real conflicted-merge repo and assert the path is counted once.

### Added — usage report (OR4)

- `autoos-agent.py usage --since <ISO-8601 UTC | 30m | 6h | 2d> [--by provider,combo,lane,model] [--json]`: pages the OmniRoute gateway's `/api/usage/call-logs` with the manage-scoped key and prints calls, ok/errors and tokens per group.
### Added — Free.ai in the key guide

- **`docs/api-keys.md`**: where to get a Free.ai key (`free_ai`), its free terms (30k tokens/day on self-hosted models, 10 requests/min) and that only public work goes there.

### Fixed — OmniRoute gateway stops refusing every chat call on page-cache pressure

- **`configuration/docker/ai-stack/compose.yml`**: the omniroute service caps the
  per-call log artifacts that fed page cache (`CHAT_LOG_MAX_BODY_KB=64`,
  `CHAT_LOG_TEXT_LIMIT=16384`, `CALL_LOG_RETENTION_DAYS=3`; image defaults
  1024/65536/7). Measured 2026-09-27: 831 MB in 3905 files under
  `/app/data/call_logs` in one day left `memory.current` at 2.62G of 2.68G max
  with only 0.83G `anon` (1.65G reclaimable `file`), and the gateway's
  pressure guard (503 at >= 92%, not configurable in 3.8.50) tripped 48x/h.
  The caps reach the gateway only when its container is recreated with the new
  env (`ai-stack.sh up omniroute` recreates it because the compose config
  changed). Documented as commented defaults in **`stack.env.example`**.
- **`configuration/docker/ai-stack/ai-stack.sh`**: new `restart <service>`
  subcommand (one compose restart through the same wrapper as the other
  subcommands, no backup; unknown name is a usage error, rc 2), and `verify`
  prints the real cgroup memory split (`omniroute memory: current … of …
  (…%), anon …, reclaimable cache …` from the `file` line of `memory.stat`,
  read with cat+sed only; "no limit" with no ratio when `memory.max` is `max`),
  informational like the admission gate, and warns with the root-free relief
  (`ai-stack.sh restart omniroute`; measured 11:04Z, `memory.current`
  2215M -> 786M) when page cache - not anon - holds the guard at 503.
  Docs: `docs/web-services.md` "Page-cache pressure guard".

### Added — tool-calling probe feeds the resolver; spawner proposes a re-probe (d86711d)

- **New `tools/probe-toolcalls.py`**: probes each leg's tool-calling support and writes the verdicts into the `logs/routing/measured.json` overlay, which **`tools/autoos_resolver.py`** now reads; **`tools/autoos-agent.py`** proposes a re-probe when a run contradicts the record (an own-account client run is not counted as a gateway-route observation).
- **Resolver review fixes** in `tools/autoos_resolver.py`: UTC provider windows, the exact next cheap start, and closer kept as its own key.

### Added — resolver v2 hard filters and expected-cost scoring (0ff2879)

- **`tools/autoos_resolver.py`**: B3b hard filters with `no_route` and override applied after filtering, plus B3c-1 expected-cost scoring and theta pick.
- **`tools/registry.py`** review fixes: route ids, unavailable legs, unknown training, hosts, dated keys; **`tools/autoos-agent.py`** spawner fixes: unique sandbox names and exit 6 on a headless tool refusal.

### Added — resolver v2 time tie-break and `plan()` (12c4624)

- **`tools/autoos_resolver.py`**: B3c-2a time tie-break that defers by provider windows, and B3c-2b `plan()` composing filters, bucket, score, pick and time into one `route_plan`; **`tools/autoos-agent.py`** spawner review fixes.

### Fixed — a 401/403 probe answer is no verdict, not unproven (2bc52ee)

- **`tools/probe-toolcalls.py`**: a 401/403 (no gateway credentials) keeps the previous value instead of scoring the leg unproven.
- **`catalog/ai-registry.json`**: the `opencode-zen`/`deepseek-v4.1-flash` leg is marked unavailable (402 in the tool-calling probe).

### Changed — a privacy=sensitive task can no longer reach a leg that trains on prompts (c55f16c)

- A `privacy=sensitive` task can no longer reach a leg that trains on prompts via `--model` or `--free`: an explicit `--model` on a sensitive run must land on private-safe legs only, and `--free` is refused for sensitive work (the promo model may train on prompts). Fail closed throughout; `--allow-training` keeps its documented, logged escape.
- **`tools/autoos-agent.py`** routes `--card` v2 runs through resolver v2 (RUNV2) and records each run's route registry class in the track record; **`tools/registry.py`** renders `configuration/omniroute/combos.json` (A4a) and the `AUTOOS-MANAGED` blocks of `configuration/litellm/config.yaml` (A4b).

### Added — the registry renders the IDE model lists; the resolver skips client-bound legs (8611bbd)

- **`tools/registry.py render ide`** writes `catalog/ide-models.json` (the opencode/Zed model lists); **`tools/sync-ide-models.py`** is retargeted onto it.
- **`tools/autoos_resolver.py`**: a client-bound leg never serves a gateway route.

### Added — the registry renders OpenHands profiles and the models doc; heartbeat rules are code (a759555)

- **`tools/registry.py render openhands`** writes `configuration/openhands/tier-profiles.json` (via `tools/sync-openhands-profiles.py`) and **`render models-doc`** writes the `AUTOOS-MANAGED` table in `docs/models.md`.
- **New `tools/autoos_heartbeat.py`**: the heartbeat/pause rules previously carried as skill prose now run as code (a relaunch is the resume; a `PAUSE` older than the session is history).

### Changed — the omniroute apply scripts and router tools read the registry (3a3157f)

- **`configuration/omniroute/apply.sh` / `apply.ps1`** read providers from `catalog/ai-registry.json` instead of carrying their own map.
- **`tools/audit-router.py`**, **`tools/mirror-litellm-env.py`** and **`tools/sync-router-tiers.py`** read the registry (A5c); `catalog/providers.json` ordering is no longer a contract.

### Changed — installers read model lists from the registry; operator model policy is registry data (95ba955)

- **`lib/linux/install.sh`** and **`lib/windows/AutoOS.Install.psm1`** read model lists from `catalog/ai-registry.json` through one `legacy_models()` helper in `tools/registry.py` (A5d).
- **`catalog/ai-registry.json`** carries the Q1 operator model policy as data: Claude-budget credit legs by bucket, with `opencode.jsonc`, `configuration/omniroute/combos.json` and `configuration/litellm/config.yaml` updated to match.

### Removed — one-shot catalog files deleted; `catalog/ai-registry.json` is the source (1a146c4)

- Deleted, replaced by `catalog/ai-registry.json` (read via `tools/registry.py`): `catalog/providers.json`, `catalog/llm-models.schema.json`, `tools/registry-convert.py`, `tests/test_registry_convert.py`. `catalog/llm-models.json` is gone from the catalog: it survives only as the test fixture `tests/fixtures/legacy-models.golden.json`.
- Also in this merge: registry `unavailable_until` honoured by every consumer, an unpinned OpenRouter BYOK `gpt-oss-120b` leg on `t2-worker` gated until operator BYOK setup, a spawner WIP-commit at exit with `PROVIDER-STOP` exit 8, and the leak check no longer flags a sibling lane's own worktree branch move.

### Changed — test suite polish (589f116, 7b6f7ea, d73fda5, 58506cb)

- `tests/run-tests.ps1` judges settings backups byte-exact and pins the skill junction, BOM-free rewrite and no-op second run; `tests/run-tests.sh` bounds `shellcheck` memory (an out-of-memory run skips loudly) behind one `SHELLCHECK_FILES` list checked against CI, compares untouched files byte-exact, and covers autostart, apt keys, dangling symlinks and undo; skill-link repair repoints only dangling `.agents/skills/<name>`-shaped links and a failing settings writer is reported, not called written.

### Added — Linux catches Windows-only test failures first (K1)

- **`tests/test_windows_portability.py`** (in `tests/linux/36-static-analysis.sh`): an AST lint fails when a Python test writes a `#!/bin/sh` stub or uses `os.chmod`/`os.killpg`/`os.setsid`/`signal.SIGKILL`/`fcntl`/`pwd`/`grp` without an `os.name`/`sys.platform` skip guard, and every tracked `.ps1`/`.psm1` must start with a UTF-8 BOM. Both classes had failed the Windows CI job repeatedly (measured over 300 runs) while Linux stayed green; the 26 unguarded functions it found now carry the guard (a skip counts only in the branch taken on Windows).

### Fixed — a worker without a shell no longer loses its question (K2)

- **`tools/autoos_agent_mcp.py`**: a worker that cannot run `tools/autoos-ask.py` (measured: qoder in `dont_ask`) prints `QUESTION <id>: <text>` and/or a REPORT with status `input_required`; the run used to end as `completed`. `_stdout_channel()` now reads the exited run's output tail once, for both `_state()` (`input_required / ended` with the question, `report` attached, REPORT `failed` -> `failed / reported-failed`) and the runner, which writes `<id>.question.md` into `AUTOOS_FALLBACK_DIR` (else the run dir) only in that case and never overwrites it. `respond()` refuses an ended run and says to spawn a follow-up. Measured channel table in `docs/agent-protocol.md` "Channels".

### Added — handoff caps single source: registry policy.handoff_caps (spec 8.3)

- **`catalog/ai-registry.json`** policy.handoff_caps rows now carry `match` (list of lowercased model-id substrings) and `window` (context window in tokens) as required fields; schema updated (additionalProperties stays false). **`tools/autoos_context.py`**: `caps_from_registry(registry_dict)` converts the registry rows to the `(substring, window, cap)` tuple format; `load_caps(path=...)` reads the registry or falls back to `DEFAULT_CAPS` with source `"default"` when the file is missing/unreadable/malformed; `cap_for(model, caps=None)` loads from the registry by default (source `"policy"`), keeps its signature and `[1m]` rule. Drift test pins policy caps equal to `DEFAULT_CAPS`; edited-registry test proves `cap_for` follows the registry; missing/malformed tests prove the fallback. Blocker: `tools/autoos-agent.py:544-600 context_state` hardcodes `source="default"` and must be updated to report `"policy"` when caps come from the registry (diff in REPORT).

### Added — BRIEF/REPORT protocol parser (routing v2 spec §5.7, §8.2)

- **`tools/autoos_report.py`** (Python stdlib only): `format_brief(dict)->str` and `format_report(dict)->str` produce the terse fixed-field protocol; `parse_report(text)->dict` and `parse_brief(text)->dict` find the LAST block in free text (workers print prose around it), accepting both the "·"-joined single-line form and the multi-line "field: value" form (case-insensitive field names). Status must be one of `completed|failed|input_required` else `missing` includes "status". Tests entries split on `->` or `→`. `check_report(report, changed_files)->list` implements the §5.7 gate: files claimed but not in the diff, diff files not claimed, status completed with no tests. CLI: `parse <file|->` prints JSON; `check <report-file> --changed <file...>` prints problems, exit 1 if any. Tests: `tests/test_autoos_report.py` (offline — no network, no subprocess to live tools). Docs: `docs/agent-protocol.md` with templates and examples.

### Added — installers link agent skills into ~/.agents/skills and ~/.codex/skills

- **`lib/linux/install.sh`**: `install_agent_skills` now calls `link_skill_dirs` for
  `~/.agents/skills` (unconditional — gemini, qoder, qwen) and guarded against codex presence
  for `~/.codex/skills` (codex). Each skill is one symlink; a user's own files are left
  untouched with a warning; a second run is a no-op; `--dry-run` prints "would link".
- **`lib/windows/AutoOS.Install.psm1`**: new `Sync-AutoOSAgentSkillTargets` function reads
  `$HOME/.agents/skills` (unconditional) and `$HOME/.codex/skills` (guarded by directory
  existence or `Get-Command codex`). Called from `Install-AutoOSAgentSkills`. Exported for
  tests. Uses the same one-link-per-skill, never-overwrite, second-run-no-op pattern as the
  existing OpenHands skill writer.
- **Tests** (`tests/run-tests.ps1`, `tests/linux/18-mcp-wiring.sh`): fresh install, codex linked
  when present and `~/.codex` never created when absent, second run skipped, a user's own skill
  untouched, dry run creates nothing. **`AGENTS.md`** §8: client -> skills dir -> linked table.

### Fixed — `ai-stack.sh verify` combo probes survive gateway warm-up and reasoning legs

- The keyed combo probe sends `max_tokens` 256 (at 16 a reasoning leg spent its budget thinking and the gateway's
  quality check answered 502) and retries a 502/503 twice, 10 s then 20 s (`AUTOOS_VERIFY_RETRY_SLEEP`), because a
  freshly recreated gateway answered 503 for its free-only combos and then 200 on re-probe (L0, live 2026-09-27).
  Any other status is still a FAIL on the first answer.

### Fixed — `probe-toolcalls` no longer prints or stores a provider error body

- **`tools/probe-toolcalls.py`**: its `make_post` returned `exc.read(500)` for an HTTP error and `str(exc)` for a transport failure, and that text is what the probe prints and writes into the `detail`/`trials` of `logs/routing/measured.json` — so a gateway error's org/project id or internal host landed in a public repository's log tree (AGENTS.md rule 1). The body is now read only for an HTTP 400, and only to choose between two fixed tokens: `HTTP 400 (mentions tool/function support)` and `HTTP <code>`; a transport failure is `transport error: <ExceptionType>`. `classify()` matches the token, never a body, so the 400-about-tools verdict stays `broken` — the plural counts now too ("tools are not supported", which the old whole-word `tool|function` missed), and every other verdict (proven/broken/unproven/no-verdict, and `400` not being a no-verdict status) is unchanged. Its 429/503 retry, overlay read-modify-write, gateway probe, key-reading module load and timestamp now come from **`tools/probe_common.py`** instead of a second copy; its leg list and no-verdict statuses stay its own, because `probe_common`'s free-legs-only rule and "any non-200 measured nothing" would both change what this probe reports. Tests: `tests/test_probe_toolcalls.py` (offline — `urlopen` faked, no live probe).

### Added — RTK A/B probe: the measurement behind decision D19

- **`tools/probe-rtk.py`** (Python stdlib only): collects real tool output locally (`git log --stat`, `git diff`, a `grep` over `tools/`, this repo's own probe test runs) plus synthetic failing outputs buried in 300 lines of passing noise, sends each sample through the host CLI's management-scoped RTK endpoint (`omniroute --output json -q ctx rtk test --file …`), and checks that every failure line of the original survives verbatim in the compressed text. TSV per sample plus a summary on stdout; `--report PATH` adds a markdown table and the verdict line — `D19: enable RTK on tool output` only when the corpus saves >= 10% **and** no sample lost a line or failed to measure, else `D19: keep RTK off (<reason>)`. The manage key is read from `~/.config/autoos/ai-stack/manage.key` (or `$AUTOOS_AI_STACK_CONFIG`) and passed only in the child's environment — never argv, never printed; without it the probe exits 3 and says it is an operator step. Exit 0 on a completed run whatever the verdict, 2 usage, 3 CLI/auth unavailable. `--dry-run` lists the corpus and calls nothing. Tests: `tests/test_probe_rtk.py` (offline, `subprocess.run` monkeypatched).

### Added — ask-back: a blocked worker asks its orchestrator (spec routing-v2 §9)

- **`tools/autoos-ask.py`** (Python stdlib only): the worker-side helper — writes `question.json` into the run dir
  (the spawner now exports it to the child as `AUTOOS_TASK_DIR`; the CLI forwards its environment to the client),
  polls for the orchestrator's `answer.json`, prints the answer and archives the exchange as `qa-<n>.json`
  (history kept). Exit 0 answered, 3 timeout (question withdrawn, run back to `working`), 2 misuse
  (no `AUTOOS_TASK_DIR`, a question already pending, an empty question).
- `tools/autoos_agent_mcp.py`: a live run with a pending `question.json` and no `answer.json` reports
  `state: "input_required"` (plus the question text, `detail` still `running`); the new `respond(run_id, text)`
  MCP tool answers it atomically and the run returns to `working`; `cancel` now also stops a run parked in
  `input_required`. Ask-back files are documented in the module docstring.

### Fixed — ask-back: stale answer.json after timeout no longer blocks the next ask

- **`tools/autoos-ask.py`**: "pending" now means `question.json` exists (only). An `answer.json`
  with no `question.json` is stale (left by a timeout the previous run hit, or an answer landing
  at/after the deadline); it is archived as `qa-<n>.json` with `"stale": true` and a null question,
  never silently deleted. On timeout the helper also archives a late `answer.json` that appeared
  between the last poll and the deadline. A second ask after a timeout now writes its question and
  works instead of refusing "already pending" (exit 2) forever.
- **`tools/autoos-ask.py`**: every file operation into `AUTOOS_TASK_DIR` is wrapped; an `OSError`
  (a read-only mount, a full disk, an `--isolate` outside-path fence) exits 5 with a message that
  names the reason and suggests `--isolate`, not a raw traceback. Exit code 5 is documented in the
  module docstring with the other exit codes.

### Fixed — the --isolate fence denied the ask-back run dir

- **`tools/autoos-agent.py`**: `outside_fence` gains the spawner's `AUTOOS_TASK_DIR` (passed in by `build_plan`) and re-allows `<realpath>/*` only when the realpath exists and stays under `<root>/logs/agents/` (the MCP `run_job` layout); any other value (outside, a `..`/symlink escape, relative, nonexistent) adds no rule and prints one stderr warning naming the variable — a blocked `--isolate` worker's `autoos-ask.py` no longer exits 5 writing `question.json`.

### Changed — the autoos-agent MCP server reports A2A task states (spec routing-v2 §9)

- `tools/autoos_agent_mcp.py`: `status`/`result` states are now `submitted`/`working`/`completed`/`failed`/`canceled` (A2A spelling, one `l`) with the old value kept in a new `detail` field (`lost` stays visible as `failed` + `detail: "lost"`), a refused `spawn` returns `state: "rejected"`, and the full set is the module constant `TASK_STATES`.

### Fixed — OmniRoute chat admission gate no longer kills parallel agent workers

- **`configuration/docker/ai-stack/compose.yml`**: `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` (default 6) and
  `OMNIROUTE_CHAT_ADMISSION_QUEUE_MS` (default 60000) on the omniroute service. The image defaults (1 heavy
  request in flight, 2000 ms queue) rejected parallel agent sub-requests with retryable 503, killing worker
  runs (4 of 5; measured 2026-09-27). `NODE_OPTIONS` is not set in compose: `OMNIROUTE_MEMORY_MB` is the
  heap knob (the entrypoint appends it; last flag wins).
- **`configuration/docker/ai-stack/ai-stack.sh`**: `init` writes both keys to `stack.env` (only when missing;
  an operator's own value survives). `verify` prints the effective values the running gateway process sees
  (read from `/proc/1/environ`): `max_heavy`, `queue_ms`, and `heap MB` (the last `--max-old-space-size` in
  `NODE_OPTIONS`); "unset (image default 1 / 2000)" when absent. Informational, not a pass/fail check.
- **`configuration/docker/ai-stack/stack.env.example`**, **`docs/web-services.md`**: documented.

### Added — effort probe: reasoning tokens and pass rate per effort rung on free legs

- **`tools/probe-effort.py` + `tests/test_probe_effort.py`** (spec §5.5/§10; wired into `tests/linux/33-documentation.sh`):
  n >= 5 trials of a fixed five-puzzle task set per `effort_ladder` rung (rung `none` omits `reasoning_effort`;
  `max_tokens` 48k, 64k at high and above, capped by `output_max`) -> `overlay.models.<id>.effort`. Shared plumbing
  moved to `tools/probe_common.py`. Both probes: only a 200 is a measurement - any other status (a live groq 413 was
  scored recall 0.0) keeps the previous value, and provider error bodies (org ids) are never printed or stored.

### Added — recall probe: usable context measured on free legs only

- **`tools/probe-recall.py` + `tests/test_probe_recall.py`** (spec §3.1/§10, D18; wired into `tests/linux/33-documentation.sh`): builds a deterministic ~N-token
  haystack (seeded filler, 5 access-code needles at spread depths) per size (default 32000/128000/256000/500000, never above `context_advertised`), asks the model for
  the codes back as JSON, and writes `context_usable` — the largest size where every trial recalled ≥ 0.9 — to the git-ignored overlay `logs/routing/measured.json`
  keyed by model (the exact shape `usable_context()` already reads; smallest size failing writes only a detail, 401/402/403/429/timeout keep the old value). Paid or
  subscription legs are never probed — naming one with `--leg` refuses with exit 4 and makes no request — and every request logs its token use to stdout.

### Changed — the Linux test suite is split into one file per describe block

- `tests/run-tests.sh` keeps the harness and summary and sources `tests/linux/NN-<describe>.sh` in order; test names
  and `--filter` are unchanged (all 719 names in the same order). CI and the suite shellcheck each part in its own
  process: one shellcheck over the 14k-line file needed more than 2.5 GB and OOM-killed a 16 GB host twice; a part
  peaks near 1.2 GB. `docs/testing.md` says how to lint and where a new block goes.

### Added — hostexec: one logged, policy-checked path for agents to run host commands (nothing enabled)

- **`tools/hostexec.py` + `tools/hostexec/`**: an MCP server over HTTP (`host_run`, `host_policy`, `host_log_tail`) that runs argv
  (never a shell string) on this host or, through a policy host alias, over ssh on another VM. Every call is checked against a
  deny-list policy (`configuration/hostexec/policy.example.toml`: no sudo in any position, no inline shells or interpreters, destructive
  commands, docker host-root, git option/config injection, env injection, PATH hijack, ssh outside the host table, forbidden hosts)
  and written to a JSONL audit log (90-day retention, secrets redacted, fail-closed before the run) plus journald.
  **It is a guard and an audit trail, not containment**: a deny-list is never complete (README). Bearer token per actor.
- **`configuration/hostexec/install.sh`**: installs the systemd USER unit (never enables or starts it) and writes the `hostexec`
  entry for OpenHands, Claude Code, codex and opencode (own key only, backup first, second run "already current"; the token is
  never on argv or in output). qoder wiring is manual for now.
- **Operator step (not run):** create the policy and 0600 token files, then `configuration/hostexec/install.sh --unit --clients ...`
  and the printed `systemctl --user enable --now autoos-hostexec.service`. OpenHands needs `AUTOOS_EXEC_BIND` set to the docker
  bridge address.

### Fixed — the web UI config save no longer churns backups

- `POST /api/config` (`lib/linux/serve.py`, `lib/windows/AutoOS.Serve.psm1`) backed up and rewrote
  `autoos.config.json` on every save, even when the merged result equalled the file, and the Linux
  `<time_ns>` / Windows `-fffffff` backup names were not rankable by the undo listing. "Equal" is
  now a data compare, key order aside (`Add-Member -Force` reorders keys on a no-op merge) and type
  strict on Linux (`1` is not `true`), via the shared `ConvertTo-AutoOSCanonicalJson` /
  `Save-AutoOSWebConfig` helpers. An unchanged save — identical or reorder-only — returns
  `unchanged` with no backup and no write; a changed save backs up under the standard
  `<file>.autoos-backup-YYYYmmdd-HHMMSS` name (`-1`, `-2`, ... on a clash, never overwrite).
- `Set-AutoOSManagedFile` now uses `Copy-AutoOSBackup`, making shell-file backups rankable and preserving each same-second version with `-1`, `-2`, ... suffixes.

### Fixed — backups outside the installer never overwrite a same-second backup

- `configuration/autostart/register-autostart.sh`, `configuration/docker/ai-stack/ai-stack.sh` (config backups and the
  migrate `aside` directory) and `templates/rescue-bootstrap.sh` named backups `<file>.autoos-backup-<second>` and
  overwrote an earlier backup taken in the same second. They now pick a name that did not exist yet (`-1`, `-2`, ...,
  the rule of `lib/linux/install.sh` `backup_path`), and a failed copy leaves the file untouched.
- `ai-stack.sh` now gives migration and rollback archives an unused `-N.tar.gz` name, while WSL CAO relocation uses a free `-N` directory and leaves native state untouched if it cannot be backed up.
- `ai-stack.sh init` (also run by `up` and `migrate`) now stops when a config backup fails instead of continuing with
  a stack.env it could not update. `configuration/start-stack.sh` moves an unparseable OpenHands `settings.json` aside
  under a unique name and deletes it only after the copy succeeded.
- `configuration/herdr-sessions`: detection also sees a system-scope install; `--unregister` stops the timer
  (`disable --now`) and backs up under a unique name; profile paths with `&` and values with inner spaces render
  correctly.

### Added — herdr-sessions: Claude Code panes come back after a reboot (opt-in, Linux)

- **`configuration/herdr-sessions/`**, imported from the Server repo's `Applications/herdr-sessions` at 12f0ff7 and
  scrubbed for a public repo (only `profiles/example.conf`, `/home/youruser` placeholders): systemd units snapshot
  the live Claude Code sessions every 5 minutes and restore them into Herdr panes at boot (full resume).
  `install.sh --profile` takes a profile name or an absolute path to a site profile kept outside AutoOS,
  `--unregister` removes exactly the installed units (backup first; a second run says "nothing to remove"), and
  a re-run with unchanged units prints `herdr-sessions: all units already current`.
- **Fixes in the engine:** background (`claude --bg`) sessions are no longer picked as a pane's session (T1-T4),
  and the pid registry wins over the newest transcript (T5-T7); `tests/test_herdr_sessions.py`.
- **`herdr-server.service` sets `OOMPolicy=continue`** (user and system templates): one process killed by the
  kernel OOM killer no longer stops the whole unit and every pane with it (happened twice on 2026-09-26).
- **Catalog component `herdr-sessions`** (opt-in, in no profile): prompt `herdr_sessions_profile` = absolute
  path of a site profile; empty => `skipped: no profile`. Selecting both in one plan is refused (error); when the other is already installed the installer skips with rc 0 instead of failing, so a re-run reports `skipped`. Detected
  by `~/.config/systemd/user/herdr-sessions-restore.service`. The component reports `skipped` only when every
  unit was already current, and a drifted unit's backup never overwrites a same-second earlier one.
- **Operator step (not run):** on the herdr host, `./setup.sh --only herdr-sessions` with the site profile path;
  it replaces the units installed from the Server repo copy (backed up first).

### Added — the OmniRoute gateway can log in to Qoder, and knows its public origin

- **The gateway image carries `qodercli`**: `configuration/docker/ai-stack/omniroute.Dockerfile` builds
  `autoos/omniroute:3.8.50-autoos1` from the digest compose pinned, plus `@qoder-ai/qodercli@1.1.63`
  (`CLI_QODER_BIN=/usr/local/bin/qodercli`). The Qoder PAT login in the dashboard failed with
  `spawn qodercli ENOENT`. `qodercli` needs a writable HOME even for `--version`, so the read-only container gets
  `HOME=/home/qoder` on a persistent bind mount `${AUTOOS_STACK_DATA}/qoder-home` (a tmpfs would give a new
  machine id per restart); the login itself stays in the gateway data dir that backup and migrate cover.
- **`ai-stack.sh up` builds every service with a `build:` stanza** and rebuilds it when the Dockerfile changed
  (a source-hash label), and **creates missing data directories itself**; before, `up` on an initialised host
  left a new bind-mount source for dockerd to create as root.
- **`AUTOOS_OMNIROUTE_PUBLIC_URL`** (in `~/.config/autoos/ai-stack/stack.env`, placeholder in `stack.env.example`,
  no hostname in git) sets `NEXT_PUBLIC_BASE_URL` and `OMNIROUTE_PUBLIC_BASE_URL`, pinning `BASE_URL` to loopback
  while it is set. OmniRoute exits at startup on an invalid value (a crash loop under `restart: unless-stopped`),
  so `up` and `migrate` refuse one first. Google logins (Antigravity) keep the loopback callback by design:
  `docs/web-services.md` explains the host browser, `ssh -L 20128:127.0.0.1:20128 <host>`, or pasting the
  failed callback URL.
- **`ai-stack.sh verify`** checks `omniroute has qodercli` and prints the public URL as the gateway normalizes it
  (skip when unset). It is no longer strictly read-only: `qodercli --version` writes its log files under the
  qoder-home mount.
- **Operator step:** `ai-stack.sh up omniroute` (try `--dry-run` first) recreates the gateway: a short gap on
  :20128. Then `verify`, then retry the Qoder PAT login.
- **Unverified:** the live PAT login, anything with the public URL set beyond the env plumbing (dashboard origin,
  links, a remote Google login), and `up` against the live gateway. Measured: a `--no-cache` build, `qodercli
  --version` = 1.1.63 as 1000:1000 on a read-only rootfs, and the whole gateway booting healthy in a scratch run.

### Added — Playwright MCP starts its container only when a session browses, and stops it when idle

- **`tools/playwright_mcp_lazy.py`**, a per-session stdio proxy: it answers the session-start handshake and
  `tools/list` from a cache, starts the Playwright container on the first real call, stops it after 15 minutes
  idle (`AUTOOS_PLAYWRIGHT_IDLE_SECONDS`) and starts it again on demand; the proxy itself stays up, because Claude
  Code does not reconnect a stdio server that exits. The containers left running were held by live but idle
  sessions that had started them eagerly; an owner's exit already stopped its container.
- **Measured live** (Claude Code 2.1.283, the real image, 2026-09-26): a warm cache and no browsing start no
  container; a cold start takes ~1.5 s; the idle stop came 20 s after the last call at an idle of 20 s; the proxy
  uses ~13 MB RSS.
- **Hardened after a cross-family review:** the backend starts on its own thread; backend-initiated request ids
  carry a generation so a stopped backend's late answer never reaches the next one; a duplicate in-flight id is
  refused (`-32600`); `NaN`/`Infinity` are parse errors. The cache is believed only as a regular file (no
  symlink, no FIFO hang) of at most 4 MiB, owned by the user and not writable by group or others, in a directory
  that is too; it lives in `~/.cache/autoos/playwright-mcp/` (0700), apart from the shared `~/.cache/autoos`,
  which the image cache creates group-writable under umask 002.
- **Installer switch:** `./setup.sh --only mcp-playwright` replaces only the two known Playwright entry forms
  and a proxy entry from another checkout path, after backing up the Claude config; a failed backup stops the
  write. Sessions started before keep their old containers until they end.
- **Residuals:** JSON-RPC batches are rejected (MCP 2025-06 and later has none).

### Changed — Linux installs the Antigravity Hub 2.x in user space and can update it

- **`./setup.sh --only antigravity` installs the Antigravity Hub** (2.17.0 today) into `~/.local/opt/antigravity`
  with a command link `~/.local/bin/antigravity` and a desktop entry. No sudo, no apt, nothing root-owned. The
  vendor publishes no Linux feed, so the newest version is read from the `microsoft/winget-pkgs` manifest: highest
  numeric version, download URL built only from a constant host plus the version id (nothing taken from the manifest
  text), a rate-limited GitHub API (403/429) reported with its reset time, a `GITHUB_TOKEN`/`GH_TOKEN` passed on stdin
  only.
- **Verified before anything is replaced:** size against `Content-Length` and a floor, gzip, tar members (no `..`,
  absolute path, link, device or setuid entry), an ELF main binary, `chrome-sandbox`, and the `app.asar` version equal
  to the manifest's. A failed check leaves the current install byte-identical; install and update are staged and
  swapped; a directory or link AutoOS did not create (stamp-based ownership) is never touched.
- **`./setup.sh --update`** (or `--only antigravity --update`) replaces the install only when the discovered version is
  newer; `--dry-run` names the lookup and the target directory. The old apt package `antigravity` (IDE 1.23.2) is not
  removed: a warning names the removal commands and the PATH order.
- **Electron sandbox:** when the kernel restricts unprivileged user namespaces, the installer prints the two sudo
  commands for `chrome-sandbox`; it never runs sudo.
- **Unverified, on purpose stated:** the Linux download has no published sha256 (the checks above, TLS and winget-pkgs
  are the only provenance); the sandbox step on this kernel and the Hub's MCP-config location were not exercised
  (the MCP writers are unchanged).

### Added — the Windows OpenCode writer knows the V2 CLI

- **`Set-AutoOSOpenCodeConfig` writes the V2 `providers` block** when `opencode --version` reports 2.x (the same
  test as Linux): `providers.omniroute` and `providers.litellm` are copied verbatim from the repo `opencode.jsonc`,
  a user's other providers stay, and `model` switches to the repo default only while it is unset or still the
  AutoOS Ollama default. A V1 CLI gets no `providers` key. `ConvertFrom-AutoOSJsonc` reads the JSONC source on
  Windows PowerShell 5.1 (comments and trailing commas outside strings; linear time, fails loudly).
- **The `%APPDATA%\opencode\config.json` copy is written only on change** and backed up once; it used to be
  rewritten on every run with no backup. `Get-AutoOSBackups` ranks the newest backup by stamp, then by the
  numeric suffix (`-10` no longer loses to `-2`), and `configuration/start-stack.ps1` never overwrites a
  same-second OpenHands settings backup.

### Added — OpenHands gets the repo skills

- **The installer mirrors `.agents/skills` into `~/.openhands/skills`, one link per skill** (`link_skill_dirs` on
  Linux, `Sync-AutoOSSkillDirs` with a junction per skill on Windows). A user's own skill, a foreign link and a
  whole-directory link from an older install are left alone, a dangling link into the repo is repaired, a second
  run reports `skipped`. Measured: `load_user_skills()` reads `~/.openhands/skills` in the process that runs the
  agent, so the mirror serves native OpenHands and Windows; in the Docker stack the agent runs in a sandbox
  container whose home is not the host's, and only `{workspace}/.agents/skills` reaches it. AGENTS.md section 8
  states this (it wrongly said the profiles set `load_skills_from_dir`).
- **The OpenHands settings writers are idempotent.** Linux backed `settings.json` up on every run and always
  said "written"; both writers now back up and write only on a change and read a BOM'd file as UTF-8 (a BOM
  used to parse as invalid and drop the user's keys from the merge).

### Fixed — Linux installer: a failed key download, a same-second backup

- **vscode, chrome and gh no longer trust an empty apt key.** A failed fetch used to leave a 0-byte key that the
  `-f` guard accepted forever; one checked helper (`apt_repo_key_install`) now installs a key only when it is
  non-empty, warns and installs nothing otherwise, and heals a leftover empty key on the next run.
- **One backup helper (`backup_file`) never overwrites an earlier backup**: a second changing write in the same
  second used to replace the backup of the user's original. Ten sites use it; `undo` picks the newest backup by
  mtime. `lib/agent_harness.py` (also called by the Windows installer) follows the same rule. Sites that still
  use a one-second name: three embedded-Python writers in `install.sh` and the scripts under `configuration/`.
- **`install_claude_autostart` backs a changed unit up before replacing it** (it used `mv -f` with no backup),
  and the Zed omnigraph entry honours the `omnigraph_url` answer instead of a hard-coded `localhost:8080`.

### Added — `ai-stack.sh verify`: the post-migrate checklist as one read-only command

- **`configuration/docker/ai-stack/ai-stack.sh verify`** replaces the by-hand checks run
  after `migrate --yes`: every compose service running (and healthy), the gateway and
  opencode refusing a keyless request with 401, the three router combos answering 200 with
  the gateway key, the code directory visible in the opencode container and in the OpenHands
  sandbox volumes, and the public URLs redirecting (302). One `ok` / `FAIL - reason` /
  `skip - why` line per check, then `verify: N ok, M failed, K skipped`; exit 0 only when
  nothing failed.
- **Read-only, and the key never leaves stdin.** Docker is only asked `inspect` and
  `exec ... test -d`; the key (`AUTOOS_OMNIROUTE_KEY`, never read from a file) reaches curl
  as `-H @-`, not on argv. `AUTOOS_VERIFY_COMBOS` overrides the combo list and
  `AUTOOS_VERIFY_PUBLIC_URLS` supplies the public URLs (none are kept in the repo).
  `configuration/healthcheck.sh` is reported as `skip`: it writes a log file on every run and
  always exits 0. `docs/web-services.md` describes the checks.
- Ten `aistack: verify ...` tests run it against the docker stub and a curl stand-in
  (`AUTOOS_CURL`) that logs the argv it received.

### Fixed — the router audit no longer reads a load-shed 503 as a dead leg

- **`tools/audit-router.py` reported OmniRoute's HTTP 503 "resource pressure" (host short of
  memory) as a probe result on the first answer.** A live probe now retries a 503 with a
  backoff of 5 s, 15 s and 45 s before reporting it; 400, 404 and transport errors are
  drift and are never retried. `docs/routing.md` says so. A leg that still answers 503
  after the last retry is reported as state, as before, and does not fail the audit.

### Fixed — Windows config writers back up only when the content changes

- **Eight Windows writers took a backup on every run, so a second run was not `skipped`**
  and the backup folder grew by one file per run (AGENTS.md §4, the twice-safe rule).
  `Enable-AutoOSProjectMcpServer`, `Set-AutoOSAntigravityMcp`,
  `Register-AutoOSAntigravityMcpServer`, `Set-AutoOSOpenCodeConfig`,
  `Set-AutoOSOpenHandsConfig`, `Install-AutoOSOmniRouteRouting` (the Qwen settings),
  `Set-AutoOSZedProxy` and `Enable-AutoOSSidekickExtra` now compare first and back up once,
  before the first change only, as the Linux writers do.
  `Set-AutoOSSerenaExclusions` and `Set-AutoOSClaudeGateway` were measured and already
  complied. One `backup-once:` test per writer runs it twice and asserts that the surviving
  backup is the user's original, not just that there is one (the backup name has one-second
  resolution, so a bare count cannot tell a skipped second run from an overwritten backup).
- The OpenHands settings script now writes `settings.json` only on a real difference.
- **A backup no longer overwrites an earlier one.** The backup name has one-second resolution, and one
  setup pass changes `mcp_config.json` five times (`Set-AutoOSAntigravityMcp`, then serena, graphify,
  playwright and context7): the later `Copy-Item -Force` replaced the earlier copy, so the user's original
  was lost and only an intermediate file survived. Every Windows writer now copies through
  `Copy-AutoOSBackup`, which appends `-1`, `-2`, ... on a name clash. The two Antigravity writers also
  compare an entry case-sensitively (`"NPX"` is not `npx`; PowerShell's `-eq` ignores case).

### Fixed — OpenHands reaches omnigraph from inside its container

- **The OpenHands profile pointed its omnigraph bridge at `http://localhost:8080`**, which
  inside the app container (and its sandbox containers) is the container itself: the
  connection was refused. `setup_openhands_config` (and its Windows twin
  `Set-AutoOSOpenHandsConfig`) now writes `http://host.docker.internal:8080`, the
  container-side form the same file already uses for the gateway. Measured on the live stack: both containers resolve
  `host.docker.internal` (compose `extra_hosts: host-gateway`) and get 200 from
  `/healthz`; omnigraph-server is already published on the host's `0.0.0.0:8080`, so no
  binding or firewall change was needed. A profile written earlier keeps the old URL
  until the installer's OpenHands step runs again.

### Fixed — the omnigraph token reaches services started by systemd

- **`autoos-opencode` and `autoos-stack` never saw `OMNIGRAPH_TOKEN`**: a systemd user
  unit does not inherit what `~/.zshrc` exports, so opencode's omnigraph bridge showed
  "connected" and then failed every read with "missing bearer token". Both templates now
  carry `EnvironmentFile=-%h/.autoos-omnigraph.env` (the per-user file the installer
  keeps; the dash makes it optional). `autoos-omniroute` and `autoos-litellm` do not get
  it. `register-autostart.sh` backs an installed unit up before it gains the line and
  skips it on the next run.

### Changed — Antigravity installs from Google's apt repo, no pasted URL

- **The Antigravity app no longer asks for a `.deb` link.** The installer adds Google's
  signed apt repository (`us-central1-apt.pkg.dev`, the one antigravity.google/download/linux
  prescribes) and installs `antigravity` from it. The `antigravity_url` question, the
  catalog entry's `prompt` and the web form's field are gone; an old `antigravity_url`
  answer in a saved config is ignored.
- **The repo is frozen at 1.23.2** (Release dated 2026-04-16; the current 2.x apps are
  tarball-only), and the installer says so with a warning. The signing key is fetched
  over https and dearmored but not fingerprint-pinned: Google publishes no fingerprint.
- A second run finds the key and the source line and writes nothing; a failed key fetch
  leaves no key and no source list behind (`failed`, never `installed`).

### Changed — one source for the gateway model list (`catalog/ide-models.json`)

- **`catalog/ai-registry.json` is now the source and `catalog/ide-models.json` is rendered from it** (`python3 tools/registry.py render ide`): the tier/model list was hand-kept in about eight places and had drifted. the 1M
  tier was `1000000` in `opencode.jsonc`, `1048576` in the Zed writers, the OpenHands
  tier profiles and the installers, `128000` in `configuration/openhands/config.toml`,
  with three different output budgets. `catalog/ide-models.json` now owns ids, display
  names, windows and per-surface membership (opencode, Zed, OpenHands); leg order stays
  in `combos.json`. The 1M tier is `1000000` everywhere.
- **Both Zed writers and both OpenCode user-config writers read the catalog at run time**
  instead of carrying literals. Zed's litellm list gains `t4-rag`, the V1 OpenCode config
  gains the four combos it had missed, and every surface shows the same names (the old
  "no training" label on `t1-orchestrator-clean` was wrong: its only leg trains).
- **`tools/sync-ide-models.py`** regenerates the static copies — the `opencode.jsonc`
  model blocks (between `// AUTOOS-MANAGED` markers) and the token windows in the
  OpenHands tier spec and `config.toml` — and checks OpenHands membership both ways.
  `--check` exits 1 with a diff; both suites run it. A `config.toml` table naming a
  model the catalog does not know is warned about and left alone.
- **A missing or malformed catalog stops each installer writer with one line naming the
  file** — no traceback, no backup, nothing written (the OpenHands default only loses
  its token windows).
- **The OpenHands default LLM uses its own model's windows** (t1 from the catalog, or the
  keyless fallback's from `llm-models.json`) instead of one `1048576` for all three —
  the local 32k Ollama model was told it had a 1M window.

### Fixed — the OpenHands app gets the ten most useful tier profiles

- **The app keeps at most 10 profiles and the push kept whatever came first**: the spec
  spent a slot on `t1-orchestrator-free-only` (red by design outside OpenCode) while
  `t3-driver` and `t4-rag` never fit, and a live app filled under an older order kept
  that order. `tier-profiles.json` is reordered (t1 → t2 → t3, t2-orchestrator, the
  `-clean` worker and driver, t4-rag, opus-4-6, gemini-3.8-flash, t2-worker-free-only
  first; the free-only t1 profiles last), and the push now deletes the AutoOS
  profiles the spec no longer lists and makes room for a higher-ranked tier by
  removing the lowest-ranked AutoOS one. AutoOS owns only what it recorded pushing
  plus the spec's `retired_ids` - never a name just because it starts with
  `omniroute-`. The active profile is re-read before every delete and never
  deleted; an app that does not name its active profile gets no deletes at all.

### Fixed — the OpenCode user config converges in one run

- **`setup_opencode_config` needed two runs**: the providers merge wrote `—` escapes
  where the agent harness wrote UTF-8, so the second run always "merged" again and took
  a fresh backup. The merge now writes only when the document changes, in UTF-8.

### Fixed — agy and the Antigravity app no longer read as available when they are not

- **A signed-out `agy` passed `list` as installed** and a headless run then waited 60 s on
  an OAuth prompt before failing. The spawner probes `agy models` (under a second either
  way): `list` shows `signed-out` with the reason, `run --client agy` refuses with exit 3,
  and the MCP `list_clients` carries `usable` + `reason`.
- **The Antigravity app on Linux counted as installed when it was skipped**: a blank
  `.deb` URL returned 0. (The URL is gone altogether now: see the apt-repo entry above.)
- **agy's vendor installer edits shell profiles** (`agy install` appends a PATH line to
  `~/.zshrc`, `~/.zprofile`, `~/.profile`); `install_agy` backs them up first.
- **`--client gemini` exited 55 in any folder gemini had not been told to trust.** The
  spawner passes `--skip-trust` (this session only), so the approval mode is kept too.

### Fixed — tier orchestration on opencode v2 (measured live 2026-09-24)

- **Nested tiers could not run.** `opencode.jsonc` set a top-level `subagent_depth`,
  which opencode 2.0.16 drops as an unsupported legacy setting; the depth stayed 1 and
  t2-worker answered "Subagent depth limit reached (1)". It is `experimental.subagent_depth`
  now, and both suites gate it.
- **The read-only reviewer could write and commit.** Its `bash` rule matched nothing (v2
  calls it `shell`) and serena's write tools bypassed the `edit` deny. t3-reviewer now
  denies `serena_*` except a read-only list, the omnigraph write tools and browser code,
  and carries every `agent-harness.json` shell fence (no commit, push, checkout, reset).
  Both suites assert the fence shape and that no `bash` rule survives.
- **LiteLLM did not install on Ubuntu 24.04.** `pip install --user` fails under PEP 668
  and the step still reported success. The installer uses `uv tool install` (then pipx),
  returns failure when both fail, the catalog entry requires `uv`, and a present
  `litellm` is detected so a second run skips.
- **`apply.*` registered `REPLACE_WITH_*` placeholders as provider keys**, which then
  shadowed the real key as "already registered". Placeholders now count as no key.
- **`start-stack.sh openhands` never ran the tier-profile sync**: the repo root resolved
  one directory too high.

### Added — one spawner for every agent client

- `tools/autoos-agent.py --client opencode|claude|qwen|gemini|codex|agy|qoder`: one
  command for every agent CLI. qwen, gemini and codex go through `omniroute run`. claude,
  agy and qoder run on their own login, and `list` prints the matrix.
- **Task cards** (ADR 0006): without `--tier`, `--card role=…,privacy=…` resolves to a
  combo through one tested `select_combo`. `privacy=sensitive` with `ctx=1m` is refused
  unless `--allow-training`, and the qoder promo takes public work only.
- **Depth budget** for every client (`AUTOOS_AGENT_DEPTH`/`MAX_DEPTH`, exit 4 past it).
- **`--lean`**: no serena, playwright or context7 (1406 → 678 MB peak per opencode run).
  The overlay needs opencode 2.x's `disabled: true`; `enabled: false` is dropped.
- **`tools/autoos_agent_mcp.py`**: the spawner as an MCP server (spawn, status, result,
  cancel), registered for Claude Code, opencode, OpenHands and Zed.
- Catalog: `qwen-code`, `gemini-cli`, `codex` (npm). The `qoder` client uses the
  existing script-provider `qodercli` entry and its `Set-AutoOSQoderMcp` / `setup_qoder_mcp`.

### Added — one-command tier agents

- `tools/autoos-agent.py` spawns one tier agent with its own model (a bare
  `opencode run --agent` uses the default model), standalone (so the current key is
  used), with stdin closed (a piped stdin hangs `opencode run`). `--isolate` runs it in a
  private clone with writes outside denied; `--free` runs every tier on opencode's free
  model with no key; `--dry-run` prints the plan.

### Added — router: t2-orchestrator combo, opus curation, free-only mirrors

- **New `t2-orchestrator` combo** (200k, small-scope orchestration):
  `antigravity/claude-opus-4-6-thinking` (OAuth free, ack-probed 3.4s) →
  `cc/claude-opus-4-6` (subscription overflow) →
  `openrouter/deepseek/deepseek-v4.1-flash` (cheap smart tail). Wired through
  all four client surfaces (opencode, OpenHands profiles, Zed writers).
- **New pinned `opus-4-6` combo** (agy thinking → cc opus). `t1-orchestrator`
  stays spark-only by design; `t2-worker` gained the ack-probed
  `antigravity/gemini-3.7-flash-medium` leg beside the gemini head.
- **Free-only LiteLLM groups** (`t1/t2/t3-*-free-only`): hand-curated mirrors
  minus gateway-only legs, no fallbacks entries (zero spend fails loudly).
  A new suite test pins mirror-equality and the declared-drop set.
- **CLI routing automation**: `Install-AutoOSOmniRouteRouting` /
  `route_detected_clis_to_gateway` (omniroute postInstall) routes
  pre-installed Claude Code + Qwen Code at the gateway; `Set-AutoOSApiKeyEnv`
  exports `OMNIROUTE_API_KEY` / `QODER_PERSONAL_ACCESS_TOKEN` absent-only;
  `Install-AutoOSQoderCli` fixes the "qodercli not recognized" dashboard
  error (PATH append + PAT); `devin-cli` catalog entries (winget +
  vendor script). Environment writes broadcast `WM_SETTINGCHANGE` so new
  terminals see them without sign-out.

### Removed — router: credit combos, retired ids, Z.AI

- **`tier2-credit` / `tier3-credit` combos deleted** (breaker fast-skip +
  cooldowns demote exhausted balances automatically).
- **Retired `tier1/2/3`, `rag`, `*-paid`, `*-credit` ids deleted from the
  live gateway store**; both suites assert the exact combo list plus an
  explicit retired-ids regression test, and `apply.*` only ever creates.
- **Z.AI dropped**: key unfunded (429-insufficient-balance), connection
  removed, registry/example/docs rows reverted.

### Added — Qoder desktop, and Qoder's MCP servers

- **`qoder-desktop` joins the catalog** on all three platforms: `winget
  Alibaba.Qoder` on Windows and `manual` on Linux/macOS, where the vendor ships
  only `.deb`/`.rpm`/`.dmg` and no Homebrew cask exists (checked against the
  live cask API, not assumed). The entry carries no `verify`: the app puts no
  confirmed CLI on PATH, and a verify that fails after a successful install is
  worse than none.
- **Qoder's MCP servers are wired** by the `Set-AutoOSQoderMcp` /
  `setup_qoder_mcp` postInstall, attached to the existing `qodercli` component
  (whose installer is the `Install-AutoOSQoderCli` entry above). Registration
  goes through `qodercli mcp add-json` at user scope — never a hand-edit of
  `~/.qoder/settings.json`, for the same reason Claude Code's registration goes
  through `claude mcp add`. Pins resolve from `catalog/agent-harness.json` at
  runtime. `omnigraph` is deliberately absent: its graph is per-repository, so
  a machine-global entry would pin the wrong graph for every other repo.

### Fixed — Qoder detection, Qoder on Windows arm64, and the worktree memory pin

- **`detect.sh` had no `qodercli` case.** A `script`-provider component with no
  detection heuristic is never recognised as present, so an already-installed
  Qoder was reinstalled on every run instead of reported `skipped`
  (AGENTS.md §4). It now tests `has_bin qodercli`.
- **`catalog/windows.json` offered the Qoder CLI on arm64.** The vendor's own
  installation docs state Windows arm64 is not currently supported, so `arch`
  is `["x64"]` and the component is hidden there rather than shown and failing
  (AGENTS.md §3). The vendor manifest does ship linux/darwin arm64 builds, so
  those two catalogs are untouched.
- **Qoder is the one client AutoOS wires for tools but not for model routing**,
  and that is a vendor constraint, not an omission. Its Custom Models accept a
  curated provider list only (Alibaba Cloud Model Studio, DeepSeek, Z.ai, Kimi,
  MiniMax, Xiaomi MIMO) with no arbitrary OpenAI-compatible base URL, and
  `~/.qoder/.models/<uid>/customs` is encrypted — so `:20128` is not addressable
  and nothing in `lib/` could write a provider even if it were. Recorded in
  `docs/models.md` and `docs/catalog.md` so the next reader does not re-derive
  it, and so nobody names a function `route_qoder_to_gateway`.
- **`trust_worktree.py` pinned the wrong omnigraph graph.** It derived
  `OMNIGRAPH_GRAPH_ID` from the checkout folder's case (`AutoOS`), but the cluster
  graph is `autoos` — so every unattended lane wrote its memory to a graph that
  does not exist, silently, which is the wrong-graph failure `CLAUDE.md` opens
  with. It now reads the pin the repo already ships in `.mcp.json` and falls back
  to the folder name only when there is none. A worktree provisioned before this
  fix keeps its bad pin until the `.env` line is removed: the helper writes the key
  only when absent, it does not correct one.

### Changed — documentation restructure

- **README.md is a front page again** (391 → 130 lines): the setup commands,
  usage, ONE screenshot and a Documentation table. The manual moved into `docs/`:
  [architecture](docs/architecture.md) (why it works this way, the layout table),
  [getting started](docs/getting-started.md) (the terminal view),
  [the catalog](docs/catalog.md) (software on offer),
  [model routing](docs/models.md) (fresh OS → working agent stack) and a new
  [OpenHands Agent Canvas](docs/openhands.md) page. `docs/README.md` and the
  README table list every page; `tests/check-links.py` and the docs-index test
  keep them honest.

### Changed — routing: free-first with a fast-skip

- **The zen free contributor promo is the first leg again** in `tier1` /
  `spark-1.3-contributor`, backed by `providerBreaker.apikey.failureThreshold`
  12 → 2 (`apply.*` sets it on both platforms). A 403 is a permanent-class
  error, so at 12 the dead promo was retried on every request; at 2 the
  connection is skipped for `resetTimeoutMs` (30 s) after two failures.
  Measured: 5 spark requests → only 3 zen attempts, the skipped ones faster,
  all served by the paid contributor leg. The same threshold makes every
  failing free leg hop fast instead of being retried ~12 times.

### Fixed — clients silently falling back to defaults

- **`opencode.jsonc` was invalid JSON**: a hand-added OpenRouter provider block
  was missing its closing brace, so every client loaded defaults while the
  suites' text-based assertions stayed green. Repaired, and both suites now
  assert the file parses. The added provider is kept — it is the direct
  effort-ladder surface (`openrouter/muse-spark-1.3-contributor`).
- **A direct-provider tier takes its own key.** The OpenHands profile
  `openrouter-muse-spark-1.3-contributor` declares `"gateway": "openrouter"`;
  both installers and `tools/sync-openhands-profiles.py` resolve a key per
  gateway, so it receives the OpenRouter key and its own base URL, never the
  gateway client key. Both suites assert it.

### Added — OpenHands Agent Canvas

- **Vendored OpenHands profiles** in `openhands/`: 21 LLM profiles projected from
  `catalog/llm-models.json`, and 11 agent profiles forming a three-level hierarchy
  (orchestrator → sub-orchestrator → worker, each with a free OpenRouter variant, plus
  Claude Code and Gemini CLI over ACP). Both installers copy them into `~/.openhands`.
  `tests/check-vendored.py` and two suite cases fail on any drift from the catalog.
- **The Ollama address is chosen per host.** `OLLAMA_BASE_URL` wins when set. Otherwise
  `host.docker.internal` is used when Ollama answers there. Otherwise the catalog's
  `127.0.0.1` is kept. A containerised OpenHands and a host-run `agent-canvas` on native
  Linux both reach Ollama now.
- [docs/openhands.md](docs/openhands.md) on installing, configuring and starting `agent-canvas`.
- [ADR 0005](docs/decisions/0005-openai-agents-api-not-the-backbone.md) (accepted) and
  [the survey](docs/research/2026-09-18-openai-agents-api.md) behind it: the OpenAI Agents
  API is not the orchestration backbone, and OpenAI joins as one reviewer pool.

### Fixed — OpenHands setup

- **OpenHands settings 500 after agent-canvas 1.20 writes schema 6.** `agent-canvas`
  writes `agent_settings.schema_version: 6` plus an `enabled` key on every MCP server,
  while the `docker.openhands.dev/openhands/openhands:latest` image supports version 4
  and rejects `enabled` with `extra_forbidden` — every `/api` settings route 500s with
  `AgentSettings schema_version 6 is newer than supported version 4`. The old
  `start-stack` guard checked top-level `schema_version` (3 on broken files too) and
  never fired. `start-stack.ps1`/`.sh` now repair in place with a timestamped backup:
  clamp `agent_settings.schema_version` down to 4 (older payloads keep theirs, so the
  image's own migrations still run) and strip the `enabled` keys; only unparseable files
  move aside. Both installers also write schema 4 instead of 5 and strip inherited
  `enabled` keys. Suite pins the repair shape in both harnesses.

- **Windows OpenHands setup failed under `Set-StrictMode`.** The embedded Python was an
  expanding here-string, so every `$` in it was evaluated by PowerShell. It is now a literal
  here-string that reads the catalog from disk. One suite case parses it; another runs it
  against a temp home.
- Non-thinking models (Ollama, `deepseek-chat`) get explicit thinking opt-outs. They used to
  inherit `reasoning_effort: high`, which Ollama rejects.

### Added — installer / rescue USB

- **Your own image:** `--image custom-local --image-path <file>` or
  `--image custom-url --image-url <url> --image-sha256 <hex>`, with a required
  `--write-mode hybrid|raw`. See [usb-creator.md](docs/usb-creator.md#your-own-image).
- **The Windows read-back reads the stick itself**, not the file cache
  (`FILE_FLAG_NO_BUFFERING`), matching Linux's `dd iflag=direct`.
- **Verified mirrors** for Debian netinst and Fedora Workstation.
- **CI runs both suites with no route to the internet**, so a test can no longer
  install or download anything for real.

### Fixed — installer / rescue USB

- **Fedora Workstation never resolved**: its filename pattern, checksum filename,
  directory depth and bare-integer version folders no longer matched upstream, and its
  index went through a geo-redirector that could land on a broken mirror. It now resolves
  from `dl.fedoraproject.org` (new optional catalog field `leaf` for the deep ISO folder).
- **`--config` replays failed on Git Bash for Windows** with "Unknown profile" — the
  config and state loaders kept a trailing carriage return from native python3.

- **`--create-usb` / `-CreateUsb` builds a bootable stick end to end**: resolve the image from
  its catalog entry (never a pinned version), fetch the vendor's GPG-signed checksum manifest,
  fetch the image — from a catalog mirror when one is listed — and verify it, re-check the
  target device immediately before the first destructive step, write with the chosen engine,
  flush, then **read every file back and compare it with the image** before saying
  `Ready to boot`. A real write needs `--wipe-target-disk` / `-WipeTargetDisk`; `--dry-run`
  shows the plan and touches nothing. See [usb-creator.md](docs/usb-creator.md) and
  [ADR 0004](docs/decisions/0004-ventoy-and-wsl-not-rufus.md).
- **A terminal chooser** (image → kind → engine → device) when `--create-usb` is run
  interactively without every flag, and a *Create installer USB* entry in the profile menu.
  The confirmation names the device, its model, its size and that all data on it will be
  destroyed; a non-interactive run never consents on the user's behalf.
- **`rescue` and `local-ai` profiles**, Claude Code and `agy` as independent entries, an `ai`
  dispatcher with a local backend, offline `.deb` cache and pip wheelhouse for the stick.
- Catalog: `images.json` (version-free image entries with checksum/signature/key and an
  optional `mirrors` list), `engines.json` (five write engines).

### Fixed — `claude-autostart`

- **It recorded no sessions on Windows.** Discovery parsed process command lines,
  but `Win32_Process` exposes no working directory, so every session was dropped
  — measured 0 of 6 live sessions on a developer machine. Discovery now reads
  `~/.claude/projects/*/*.jsonl`, whose records carry `cwd` and `sessionId`
  directly. See [ADR 0001](docs/decisions/0001-discover-claude-sessions-from-transcripts.md).
- **`claude-sessions.ps1 hook-start` was dead code.** It built a session list in a
  local variable and then called a function that ignored the argument. The hook
  layer is removed entirely — `SessionEnd` deleted the very record restore
  depends on. See [ADR 0003](docs/decisions/0003-no-claude-code-lifecycle-hooks.md).
  AutoOS no longer writes to `~/.claude/settings.json` at all.
- **Restore produced processes no human could reach.** A hidden Scheduled Task
  spawning `claude.cmd`, and a bare `nohup claude &` on Linux, both start an
  interactive TUI with nowhere to draw. Restore now targets a real terminal host
  (herdr → tmux on Linux, Windows Terminal on Windows) and reports `skipped` with
  a reason when none is available. See
  [ADR 0002](docs/decisions/0002-restored-sessions-need-a-visible-terminal.md).
- **The snapshot timer stopped scheduling after the first missed window.**
  `OnBootSec=` + `OnUnitActiveSec=` is monotonic-only; it is now
  `OnCalendar=*:0/<interval>` with `Persistent=true`, the trap the upstream unit
  file documents. The Windows task's repetition was grafted onto another
  trigger's `Repetition` object and effectively ran daily; it is a one-shot
  trigger with a real repetition interval now.
- **The restore unit could not be diagnosed and was killed half-done.** Added
  `StandardOutput=journal`, `StandardError=journal` and `TimeoutStartSec=900`, and
  removed the `After=default.target` / `WantedBy=default.target` ordering cycle.
- **One session could be restored twice.** A session that left transcripts under
  two project slugs (a scratchpad directory beside the repo does this) produced
  two records with the same id, and two terminals fighting over one conversation.
- **The installer was not idempotent.** It re-registered the Scheduled Task and
  rewrote `settings.json` on every run, leaving a timestamped backup behind each
  time, and always reported success. A second run now reports `skipped`.
- **It claimed to work on macOS.** It was listed in `catalog/macos.json`, and
  `setup.sh` routes macOS through `lib/linux/install.sh` — which writes *systemd*
  units to a machine that has no systemd, after which detection reports it
  installed. Removed from the macOS catalog until a launchd implementation exists.
- `loginctl enable-linger` is announced before it runs, and when it cannot be
  done the exact command is printed rather than the failure being swallowed.
- `claude_autostart.enabled` is a real pause switch (keeps the supervisor and the
  snapshots, restores nothing). It had been in the shipped schema and the web card
  from the start with no code reading it.

### Fixed — web UI

- **Settings never reached the code that reads them.** The card wrote
  `resume_prompt_mode` / `interval_minutes` while the scripts read `resume_mode` /
  `snapshot_interval_mins`, and saving replaced the config object wholesale,
  discarding `fallback`, `fallback_cwd` and `fallback_name`. One schema now
  (`autoos.config.example.json` is its single home, read by both the PowerShell
  and the Python side), the save merges, and a test compares the keys.
- **The configuration form offered settings the platform never asks.** It
  hardcoded six fields, but `git_user_name`, `git_user_email`, `ollama_models` and
  `antigravity_url` are Linux-only prompts — on Windows those boxes wrote answers
  no installer reads. The form is rendered from the catalog's own `prompts` now.
- **The form was seeded from the example file.** With no `autoos.config.json`,
  `/api/config` returned `autoos.config.example.json`, putting `Your Name` and
  `you@example.com` in the identity fields where they looked answered and were
  saved as though they were real. The seed comes from the machine
  (`git config --global`); catalog defaults render as placeholders rather than
  values; and an empty field is saved as *unanswered* rather than as `""`, which
  would shadow the default (`herdr_source: ""` reaches the installer as a real
  answer and fails its source check).
- **Accessibility regression.** The card-chooser stylesheet replaced
  `@media(prefers-reduced-motion:reduce){.bar.indeterminate>div{animation:none}}`;
  restored, and the new transitions and `scrollIntoView` now honour it too.
- `GET /api/claude/sessions` ran a snapshot, mutating state on every page load.
  It is read-only; `POST /api/claude/snapshot` is the only writer.
- Every inline `onclick` is gone. They forced a value to be HTML-escaped into a
  JavaScript string context, which is the wrong escaper; the page uses one
  delegated listener instead.

### Fixed — elsewhere

- **The status screen showed a date two months in the future.** A bare
  `[datetime]::TryParse` reads an ISO-8601 round-trip string under the current
  culture, so `2026-09-11T12:04` came back as `2026-11-09` — next to session rows
  dated correctly.
- The post-run report told the user to "run claude" to use a background service,
  then, after the misleading `verify` was removed, that it had "no launcher found
  yet". A component can declare `"launcher": "none"` now, and the report says it
  runs in the background instead of hunting for an executable.

### Added

- `docs/decisions/` — architecture decision records, starting with the three
  above — and this changelog.
- **Configure and System sections in the browser UI.** Overview had grown to six
  cards covering three unrelated jobs: what this machine is, what to install, and
  how the installed things are configured. Overview is now only *choose what to
  install*; see the layout section below for where the rest went.
- `setup.ps1 -ClaudeSessions <action>` and `./setup.sh --claude-sessions <action>`
  — status, snapshot, restore or configure, from the entry point people already
  use, rendered through the shared UI layer so `--no-color`, non-TTY output and
  the run log keep working. The status screen leads with the three facts that
  decide whether sessions come back: the supervisor, the terminal host, and how
  long ago the snapshot was taken.
- `configure` presents each setting as a radio menu rather than a free-text
  prompt, since every one of them is a closed set and a typo used to be accepted
  silently and then ignored by the code that read it.
- Behavioural tests, in both suites, driven from a fixture transcript tree built
  at test time: discovery, the liveness window, the `max_sessions` cap,
  deduplication, anti-clobber, the restore plan, the `--rc` override, terminal-host
  refusal, the disabled switch, config merge-not-replace, and the schema
  agreements between the web UI, the catalogs and the shipped example.

### Changed

- **Dry run is off by default.** A run that installs nothing, from a button that
  says *Install selected*, is a surprise in the wrong direction; the confirmation
  already lists every package before anything happens.
- The Claude autostart card leads with state — service, last snapshot age, tracked
  count — rather than with settings selects, and opens only when the component is
  installed.
- Choosing a card in the chooser expands it: a preset that reveals a folded card
  has not really revealed it, since the controls it was chosen for stay hidden.
  The view presets carry a pressed state, so the active view is visible.

### Changed — browser UI layout

- **Navigation is a dropdown in the header**, not a strip that grew a tab each
  time the page gained a job. It is a real menu: arrow keys walk it, Escape
  closes it, a click elsewhere closes it, and the current section is checked.
  Panels are `role="region"` now — with no tablist left, `role="tabpanel"` was
  describing something that no longer existed.
- **The header carries the machine and nothing else about it.** The environment
  pill, the installed pill and the detected-system facts all described the
  machine; they are on the System tab, which is what that tab is for.
- **System and Installed are one section**, last in the menu: "what is this
  machine" and "what is already on it" are the same question asked twice.
- **The theme is one button** showing the theme it would switch to, instead of a
  three-way Auto/Light/Dark group.
- **The page has its own favicon**, inlined as a data URI. The local server has
  no asset route, so every page load was logging a 403 for `/favicon.ico`.
- **Profiles are a row of square chips plus one full-width detail.** Growing the
  selected card inside the same flex row capped how wide it could get and left
  the others stretched to its height. Each profile has an icon and its component
  count on the chip.
- **The component list is compact by default** — icon, name, one line — with
  **Details** adding the provider, package, platform and dependency chips.
  Already-installed components sort last within their category, and the grid
  fits more per row (290px → 215px minimum).
- **Components carry their real icon**, the application's own favicon fetched by
  the homepage domain the catalog already holds. Drawn over a monogram, so an
  offline or blocked request degrades to a letter rather than a broken image.
  Note the tradeoff: the icon service learns which domains are in the catalog.
- **⚡ Install on any component** installs just that one without touching the
  selection. Presses during a run are queued rather than racing it (the server
  rejects a second concurrent run), and progress shows in the header so it is
  visible from whichever section it was started from.
- The install order lists only what will actually be installed; already-present
  components made the plan look longer than the work.

### Added — elsewhere

- `.claude/skills/autoos-install/` — a skill that teaches an agent to drive this
  repository: the pipeline, the real flags on both entry points, how to answer
  prompts non-interactively, and how to add a component to the catalog.
- `setup.ps1 -Installed`, to match `setup.sh --installed`.
  `docs/getting-started.md` had documented the Windows flag for some time; it
  did not exist.
