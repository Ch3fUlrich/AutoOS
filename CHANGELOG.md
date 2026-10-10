# Changelog

All notable changes to AutoOS are recorded here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
- D-1038 A3 (2026-10-10, lane `og013-p2c2-split`, headless, failing tests first): **`split-project-graph.py` now sends the Omnigraph 0.13 contract header.** Measured: 0.13.0 answers 400 `{"code":"api_contract_mismatch"}` to every request (except `/healthz`) that lacks `omnigraph-http-api: 0.13`, so both `export_graph` and `mutate` failed. One module constant `CONTRACT_HEADERS = {"omnigraph-http-api": "0.13"}` merged into the headers dict of both urllib Requests (Authorization and content-type kept); nothing else changed. Tests: `test_export_graph_sends_contract_header` / `test_mutate_sends_contract_header` in `infra/mcp-servers/scripts/test_split_project_graph.py` (fake `urlopen` records the Request; assert the header, `Bearer tok`, `application/json`, and the `/export` resp. `/mutate` URL) — both red before the fix. `python3 -m pytest -q infra/mcp-servers/scripts/test_split_project_graph.py -o addopts="" -p no:randomly`: 5 passed.
- D-1038 A3 PULL-GRAPH (2026-10-10, lane `og013-p2c1-pull-b-4236ca`, headless, failing tests first): **`pull_graph.py` now sends the Omnigraph 0.13 HTTP contract header.** A 0.13.0 server answers 400 `{"code":"api_contract_mismatch"}` to every request that omits `omnigraph-http-api: 0.13` (everything but `/healthz`), so both urllib Requests — `export()` and `mutate()` — merge one module constant `CONTRACT_HEADERS` into their headers next to Authorization and content-type; URLs, payloads and the docker `omnigraph` load path (the 0.13 CLI sends the header itself) are untouched and the `IMAGE` pin stays a later phase. Tests: `test_export_sends_contract_header` / `test_mutate_sends_contract_header` both failed first with `KeyError: 'omnigraph-http-api'`; the three pre-existing assertions pinned the *whole* headers dict, so the new key necessarily moved them — they gained `"omnigraph-http-api": "0.13"` and nothing else changed.
- D-1038 A3 (2026-10-10, lane `og013-p2b-check-a91333`, headless, failing tests first): **the live doctor `tools/check-omnigraph.py` failed against Omnigraph 0.13.0 for two reasons — it sent no HTTP-contract header, so every request but `/healthz` came back 400 `api_contract_mismatch`, and 0.13 omits a NULL column from a query row, so `str(r.get("p.slug"))` turned the missing key into the slug `"None"` and reported it as a Project.** `_request` now sends `omnigraph-http-api: CONTRACT_VERSION` ("0.13") on every call; a 400 carrying `code: api_contract_mismatch` — on the schema read or the Project read — yields ONE problem naming the contract difference (expects 0.13) instead of the generic "schema read: 400 …" text; slugs are taken only from rows that are dicts with a non-empty string `p.slug`, a row without it is ignored, and an empty result still reports "has no Project hub node". New offline unit tests `tests/test_check_omnigraph.py` (fake `ThreadingHTTPServer` on 127.0.0.1:0 that records request headers: happy path, header-on-every-gated-request, contract-mismatch single problem, `[{}]` row never read as None, `[{"p.slug":"a"},{}]` → project a only), wired into `tests/linux/18-mcp-wiring.sh` directly after the bridge-benchmark block. `python3 -m pytest -q tests/test_check_omnigraph.py`: 6 passed; `python3 tests/test_suite_wiring.py`: OK.
- P2a SYNC-MEMORY / D-1038 A3 (2026-10-10, lane `og013-p2a-sync`, headless, failing tests first): **D-1038 A3 sync_memory_graph contract header + 0.13 load response.** Measured on a server-L1 rehearsal against Omnigraph 0.13.0: every request now needs `omnigraph-http-api: 0.13` or the server answers 400 `api_contract_mismatch` (`CONTRACT_HEADER` / `CONTRACT_VERSION`, one definition, sent by `post_load`), and a load no longer replies `{"tables":[{table_key, rows_loaded, error}]}` but with a flat `total_entities` plus `nodes`/`edges` per-type counts (errors are flat `{"error","code"}` bodies — the whole load fails, no per-table list). `load_confirmed` keeps the 0.8.1 tables behaviour and gains: non-empty `error` → False, int `total_entities` → confirmed only when it equals what was sent (fewer is partial, more is not this batch), and a dict carrying neither key → False, fail closed — the permissive node-only path is exactly what would mark a failed edge batch loaded, and edges have no @key, so those records would be lost rather than re-sent. New `load_summary` feeds the `loaded:` log line from either shape and never raises KeyError. Tests: `LoadConfirm013Tests` (real rehearsal samples as fixtures, plus the `--load` integration paths that mark and that refuse to mark) and the contract header asserted on the fake HTTP server; the pre-existing `{"tables": …}` tests are untouched, and five mutations of the new code (permissive unknown shape, `total_entities >= sent`, wrong header version, dropped error check, un-tolerant log summary) each fail one of them. `python3 -m pytest -q tests/test_sync_memory_graph.py -o addopts="" -p no:randomly`: 55 passed, 25 subtests passed.
- D-1038 A4 (2026-10-10, lane `og013-p3a-seeds-b088d9`, headless, failing tests first): **seeds drop `data.id`** — Omnigraph 0.13 refuses it on load (`unknown input field 'id': move data.id to the top-level 'id' field`) and an id-less node takes its id from the `@key(slug)` (rehearsal round-tripped ids == slugs), so the key is gone from all 47 node records in `infra/mcp-servers/cluster/seed/agent-skills.jsonl` (every one carried `data.id == data.slug`), removed by script (`json.loads` → `data.pop('id')` → `json.dumps`), never hand-edited: the 79 edge lines are byte-identical, each node line differs ONLY by the dropped `"id": "<slug>", ` pair, and the file still round-trips byte-exact. Edges keep `from`/`to` slugs — 0.13 still accepts them. The seed README's Format paragraph now states the rule. Tests: `tests/test_agent_skills_seed.py` (5) — record counts, no `id` under `data` or at top level, non-empty slugs unique per type, edges exactly `{edge,from,to}` with both endpoints resolving to a seed slug, byte-exact line round trip; every assertion mutation-checked (drop a line, duplicate a slug, re-add an id, corrupt an edge, reformat a line). `python3 -m pytest -q tests/test_agent_skills_seed.py -o addopts="" -p no:randomly`: 5 passed.
- D-1038 P1c-WIRING (2026-10-10, lane `og013-p1c-wiring`, headless): **`tests/test_omnigraph_deny_audit.py` was not wired into any harness, so it never ran in CI — a unit test no harness runs is a silent no-op.** `tests/linux/33-documentation.sh` gained one `if it …; then out="$(python3 …)" && pass || fail …; fi` block, directly after the `omnigraph gateway policy` block and shaped exactly like it, running the deny-audit test. Nothing else in the file changed. `python3 tests/test_suite_wiring.py`: OK; `bash -n tests/linux/33-documentation.sh`: clean.
- D-1038 A8 (2026-10-10, lane `og013-p1b-fixup`, headless, failing tests first): omnigraph bridge deny-list audit test (`tests/test_omnigraph_deny_audit.py`), D-1038 A8.
- OMNIGRAPH-POLICY-SYNC (2026-10-10, lane `cluster-policy-sync-writ`, headless): **the repo's cluster policy bundles had drifted behind the live cluster, so a re-apply from the repo would have revoked grants that are running now.** `infra/mcp-servers/cluster/memory.policy.yaml` and `infra/mcp-servers/cluster/project-graphs.policy.yaml` gained the two actors the live server already carries: the `plangraph-kg-export` group with the `plangraph-kg-export-read-only` rule (`read, export, invoke_query` only — it lives in the SAME bundle as the operator rule because cluster-mode serving refuses to boot when two bundles bind one graph), and, on the project graphs, the `workstation` group with the `workstation-project-read-write` rule (`read, export, invoke_query, change, branch_create, branch_merge` — no admin, no `schema_apply`, no `branch_delete`, no graph create/delete). Nothing else in either file changed. Tests: `GatewayPolicyTests.test_cluster_policy_grants_export_and_workstation_groups_exactly` in `tests/test_gateway_policy.py` asserts both new groups exist, each actor's granted set equals exactly its action set over the known + unlisted vocabulary (default-deny included), and that both readers — PyYAML and the stdlib fallback the cross-bundle write checks depend on — parse the bundles identically; mutation-checked, granting `branch_delete` to the workstation rule fails it. `python3 tests/test_gateway_policy.py`: 18 tests, OK.
- AO-GUARD-LEGACY-BRIEF / FILES-cap (2026-10-10, lane `p10-files-cap-changelog`, headless, failing tests first): **the brief's `<= 3 files` cap now counts only the files a lane may EDIT, so a lane that records its change still gets 3 code files.** `brief_files` capped `len(out) > 3` with `CHANGELOG.md` inside the count, which left every lane carrying a changelog line able to touch 2 code files. The cap now skips the ONE entry spelled exactly `CHANGELOG.md` — repo-root, case-exact, after the module's own `_files_item`/`exact_path` normalisation, so `./CHANGELOG.md` is exempt while `changelog.md`, `docs/CHANGELOG.md` and `CHANGELOG.md.bak` are ordinary files — and the unparsable/duplicate checks plus `scope_fence` membership are untouched, so a brief still spells at most 4 entries (a duplicate changelog dies on the duplicate check, never double-counts) and a 4th counted path refuses with the cap and the exemption named. The canonical `_FILES_RE` wording stays as writer-ops.md renders it. Lanes briefed after 2026-10-10 12:00Z must satisfy the guard; the one-time exception for F1a `eac8dda6` is logged in ready-inbox and is NOT encoded here (no allowlist entry, no sha special-case). Tests: 6 new cases in `tests/test_autoos_ready_guards.py` (36 total).
- OMNIGRAPH-POLICY-SYNC (2026-10-10, lane `cluster-policy-sync-writ`, headless): **the repo's cluster policy bundles had drifted behind the live cluster, so a re-apply from the repo would have revoked grants that are running now.** `infra/mcp-servers/cluster/memory.policy.yaml` and `infra/mcp-servers/cluster/project-graphs.policy.yaml` gained the two actors the live server already carries: the `plangraph-kg-export` group with the `plangraph-kg-export-read-only` rule (`read, export, invoke_query` only — it lives in the SAME bundle as the operator rule because cluster-mode serving refuses to boot when two bundles bind one graph), and, on the project graphs, the `workstation` group with the `workstation-project-read-write` rule (`read, export, invoke_query, change, branch_create, branch_merge` — no admin, no `schema_apply`, no `branch_delete`, no graph create/delete). Nothing else in either file changed. Tests: `GatewayPolicyTests.test_cluster_policy_grants_export_and_workstation_groups_exactly` in `tests/test_gateway_policy.py` asserts both new groups exist, each actor's granted set equals exactly its action set over the known + unlisted vocabulary (default-deny included), and that both readers — PyYAML and the stdlib fallback the cross-bundle write checks depend on — parse the bundles identically; mutation-checked, granting `branch_delete` to the workstation rule fails it. `python3 tests/test_gateway_policy.py`: 18 tests, OK. **Follow-up (POLICY-SYNC-PIN, judge):** the asymmetry that sync introduced is now pinned in the repo — `GatewayPolicyTests.test_memory_policy_declares_no_workstation_and_export_actor_holds_no_write_action` asserts `memory.policy.yaml` declares no `workstation` group and carries no rule whose `actors` reference the `workstation` group or actor, and that `plangraph-kg-export`'s granted set contains no write action (`change`, `branch_create`, `branch_merge`, `branch_delete`, `schema_apply`, `admin`, `load`) in EITHER bundle, evaluated on both readers so a grant in a shape one cannot parse still trips it; mutation-checked by adding a `workstation` group, then a `workstation`-actor rule, then `change` to the export rule — each fails it, and all three were restored. `python3 tests/test_gateway_policy.py`: 19 tests, OK.
- AO-ISOLATE-IGNORED-TRACKED (2026-10-10, lane `s9-isolate-ignored-track`, headless, failing tests first): **the `--isolate` sandbox dropped files that are TRACKED in the source but match its own `.gitignore`.** `_isolate_build` materialised the allowed HEAD entries and then made its one commit with `git add -A`, which walks the worktree and honours the `.gitignore` the materialiser had just written — so a force-added path was written to the seat and never staged, and the seat's single commit lost a HEAD file. Measured: `infra/mcp-servers/cluster/seed/README.md` is in `git ls-files` yet missing from sandbox `20261010-060406-secret-argv-writer-1944be`, the writer then reported it "git-ignored, cannot commit", and a scan test passed in the seat but failed in the lane. The fix stages explicitly: `_isolate_materialise` returns the paths it actually wrote (a refused entry never reaches the disk, so the entry list is not the same set) and `_isolate_stage` runs `git add -f` over that list as `:(literal)` pathspecs on stdin — nothing untracked, nothing the exclusion filter dropped, still no ARG_MAX cliff and a tracked name of `*` still cannot glob the tree (I1/I2). `REVIEW-DIFF.patch` was already added with `-f --`, so a `.gitignore` for `*.patch` never dropped it, and the patch builder reads `git diff`/`ls-tree`, which are tree-to-tree and ignore-insensitive, so `lane_diff_paths`, the MODEFLIP read and the take-it base share no blind spot. Tests: `T2IsolateIgnoredTrackedTests`.
- AO-LEDGER-WRITE P6 (2026-10-10, lane `lane-seat-integrity`, headless, failing tests first): **the verdict ledger is now written in production, not only read.** Measured: P3 shipped `tools/autoos_writer_ledger.py` and nothing but `autoos_writer_rule.py` ever read it — `logs/writer-ledger.jsonl` did not exist, demotion never fired, and the 1-week measurement clock had no first row to start from. `review_status` (tools/autoos-agent.py) appends one row per countable, evidence-backed seat through the ledger's OWN API: `parse_verdict` ACCEPT -> `accepted`, REJECT -> `rejected` with `failure_class` from a new optional `failure=` entry field (default `other`), HOLD / invalid / blind -> no row. The writer is never invented: a new optional `run=<writer-run-id>` field resolves `(writer_client, writer_model_served)` with `writer_for_run`, and when the field is absent or the runner-private record cannot name the writer, no row lands and the gate prints one line (`ledger: no writer record for run X, not recorded`) — the seat counts exactly as before. Rows carry a new optional validated ledger key `ref` (the sha256 of the evidence text, `[0-9a-f]{7,64}`), so re-running the gate over the same answer appends nothing while a changed answer is a new row; that check-then-append is serialized by an advisory `fcntl.flock` on a sibling `.lock` file (POSIX, a no-op branch on Windows) — two threads racing one record still write one row. A failed write (`LedgerError` / `OSError` / `ValueError`) reports the seat NOT READY as `ledger write failed: <class>`, so `ready` exits non-zero: a verdict that cannot be recorded does not clear the gate, and it says so instead of raising a traceback. `review-status --no-ledger` reads without writing; `ready` always writes; `AUTOOS_LEDGER_PATH` overrides the ledger's home so tests never touch the real file. Tests: `LedgerWriteTests` (13) in `tests/test_autoos_spawner.py`, `RefKeyTests` (3) in `tests/test_autoos_writer_ledger.py`.
- AO-L2-SEAT-INTEGRITY P4-FIX2 (2026-10-10, lane `lane-seat-integrity`, headless, failing tests first): **a seat now reads back the user site of the interpreter it actually runs, not the spawner's.** Measured: P4 asked the *spawner's* python (`site.getusersitepackages()`), but the version is part of the path — this host spawns on 3.13 while a seat's `python3` is 3.12 and its pytest lives in `~/.local/lib/python3.12/site-packages`, so the seat still printed `No module named pytest`. `seat_python_user_site` (tools/autoos-agent.py) resolves `python3` on the PATH of the environment the seat is built from (`shutil.which`) and asks that interpreter once — `<py> -I -c site.getusersitepackages()`, stdin DEVNULL, captured, `SEAT_PY_USER_SITE_TIMEOUT` 5 s, env scrubbed of `PYTHON*` (load-bearing: `-I` does not ignore `PYTHONUSERBASE`, which site.py reads itself, so the toolhome redirect would have been reported back as the real site). Its answer goes through the unchanged `qualified_user_site` checks (real dir, no symlink up to HOME, strict descendant of HOME, not group/world-writable); any error, timeout, non-absolute or multi-line answer, or no `python3` on that PATH falls back to the spawner's site and then to no `PYTHONPATH` — never a raise. Cached per (interpreter, HOME) so plan building asks once. Tests: `SeatPythonProbeTests` (4) in `tests/test_autoos_spawner.py` — a fake `python3` (POSIX, chmod +x) naming a 3.12 site while the spawner pins a 3.13 one, and the seat env carries the 3.12 site; the fallback table (garbage, relative, multi-line, 0777, outside HOME, empty, non-zero exit, no python3); one probe per interpreter plus a hung python3 costing only its (patched, 1 s) timeout; and a real end-to-end on this host — `/usr/bin/python3 -c "import pytest"` fails with `PYTHONUSERBASE` redirected and no `PYTHONPATH`, succeeds on the built seat env (skipTest where a host proves neither). Gates: `pytest -q tests/test_autoos_spawner.py -k "UserSite or Isolate or passlist or SubprocessEnvAudit"` **161 passed**, `tests/test_windows_portability.py -k posix_guards` **1 passed**, `tests/test_agent_mcp_tool_profile.py tests/test_suite_wiring.py` **66 passed**, the whole `tests/test_autoos_spawner.py` **1462 passed, 6 skipped**; `SeatPythonProbeTests` red before the fix (the 3.13-vs-3.12 assertion). ~154 changed lines incl. tests — over the ~80 asked: the four test groups the task enumerates do not compress below that without dropping one. Not pushed.
- AO-L2-SEAT-INTEGRITY P4 (2026-10-10, lane `lane-seat-integrity`, headless, failing tests first): **an L2 launched in a scratch/foreign repo no longer dies on a skill it cannot load.** Measured: the fixed footer's `Load the \`unattended-orchestration\` skill first.` (`ROLE_LINES[0]`, tools/oc_l2.py) hard-required a skill that only exists in this repo's `.agents/skills` (or the user's skills dir), so the lane errored `Unable to load skill unattended-orchestration` and never worked. The load is best-effort now — "if it is available (try the Skill tool once); if it is not present in this repo, do NOT stop or error - follow the inline rule pointer instead:" — and the rules an L2 acts on travel with the footer as ONE constant `INLINE_RULES_LINE` (444 chars, no private content): R-coord-14 (orchestrate, never fix or research, spawn L3 sized to the complexity), R-coord-09 (an L3's spawn/routing/status through the `autoos-agent` MCP only), R-orch-01/R-orch-22 (terse, one line per fact, evidence by pointer, end every wave with a visible `GOAL / DONE / NEXT / BLOCKED` block), R-coord-08 (keep the heartbeat, read the L1 inbox before every launch). `ROLE_LINES[1]`/`[2]`, `CHILD_WAIT_LINE` and the launcher's `HINT_LINE` are unchanged and still in order; who may spawn what is unchanged. Tests: `ForeignRepoFooterTests` (4) in `tests/test_oc_l2.py` — the conditional wording and the unconditional phrase gone, the inline line exactly once / one line / <= 600 chars, the rule table, the rest of the footer in order — all four red before the fix. Gates: `pytest -q tests/test_oc_l2.py` **107 passed**, `tests/test_agent_mcp_tool_profile.py tests/test_suite_wiring.py` **66 passed**, `tests/test_autoos_spawner.py -k "oc_l2 or L2 or footer or lane"` **58 passed**. ~65 lines changed incl. tests. Not pushed.
- AO-RECOVER-BLANK-CHARS (2026-10-10, lane `lane-spawner-followups`, S4, headless, failing tests first): **a blank-LOOKING character now hides a continuation footer the way a zero-width space does.** The invisibility rule was `ch.isspace() or unicodedata.category(ch) in _INVISIBLE_CATS` (Zs/Zl/Zp/Cc/Cf), so a character that draws nothing but belongs to Lo/So/Mn — U+3164 HANGUL FILLER, U+115F/U+1160/U+FFA0, U+2800 BRAILLE PATTERN BLANK, U+17B4/U+17B5, U+034F, U+180B-U+180D/U+180F, U+FE00-U+FE0F, U+E0100-U+E01EF, U+1D159 — slipped past it, and a record that IS only the footer read as a brief: it bought a continuation leg and stacked a second footer on the one it already was (D-914's loop). ONE frozenset `_BLANK_LOOKING` — a list of code points, deliberately NOT a category, because U+2801 draws dots and U+AC00 draws a syllable, and both stay content; a variation selector (U+FE0F and the other VS ranges) is in the list, so it counts as invisible and a variation selector inside CHECK evidence is REFUSED, fail-safe — behind ONE predicate `_is_invisible_char(ch)`, and every invisibility test in `tools/autoos_recovery.py` calls it (`_has_visible_text`, `_heading_form`'s footer cut, `_footer_prefix_invisible`), so no site drifts behind the others. `_suspect_line`'s `_SUSPECT_CATS` rule is untouched: it judges whether a report can be re-split, not whether a character is blank. Tests: `InvisiblePrefixFooter.test_a_blank_looking_character_hides_the_footer_like_a_zwsp_does` (parametrised per code point, asserted through `plan`) and `test_ordinary_letters_and_marks_are_never_invisible`. **Follow-up (same lane, S4 delta-fix round): the predicate is ONE object for BOTH ready readers.** `tools/autoos_ready_guards.py` had kept its own category-only `_invisible_char`, so the review found `report_checks("CHECK 1: PASS ㅤ")` answering `ok` TRUE — a blank as the whole evidence of a required CHECK — while recovery refused the same character; the frozenset and the predicate moved to `tools/autoos_blank.py` (`BLANK_LOOKING`, `INVISIBLE_CATS`, `is_invisible_char`), which `autoos_recovery.py` and `autoos_ready_guards.py` each import (as `_is_invisible_char`, the one addition to recovery's hermetic import allow-list), so every body check — a CHECK's evidence tail and each FILES item alike — is judged by the identical function, and `tools/autoos_ready_guards.py` keeps only the four characters a real body may carry (`_ALLOWED_CHARS`). Tests: `test_a_blank_looking_evidence_or_files_item_is_never_ok`, `test_a_blank_that_draws_something_is_still_content`, `test_both_ready_readers_share_one_invisibility_predicate` (identity, not two copies). Same round: the `ready`-caller lint now also matches a call SPLIT by a shell continuation (`tools/autoos-agent.py \` / `ready …`), and `tests/test_autoos_spawner.py::test_the_ops_refusal_names_the_exact_command_to_rerun` pins the refusal's `Run:` line to the full corrected call.
- AO-READY-CALLERS (2026-10-10, lane `lane-spawner-followups`, S4, headless, failing tests first): **every instruction that shows the `ready` gate being run names both writer-guards files.** `cmd_ready` requires `--brief` and `--report` only when the DIFF reaches R2, so a caller that copied a command line out of a doc omitted them silently and was refused at a gate the lane had already cleared. Updated sites: the `tools/autoos-agent.py` usage block (`ready <RECORD> --branch --sha --inbox --brief --report`), `_parser_ready`'s help, `cmd_ready`'s own docstring, `.agents/skills/unattended-orchestration/SKILL.md`'s interface list, and `cmd_ready`'s refusal for an ops lane without the guards, which now prints the exact corrected call with this lane's record, branch, sha and inbox filled in. `tests/test_ready_callers.py` keeps it true: a static lint over `git ls-files` that fails any line spelling the invocation unless the same line — or its continuation within 3 lines — carries `--brief` and `--report`, exempting prose that only mentions the command with the marker `<!-- ready-no-guards -->` (applied at `docs/plans/2026-09-28-restart-spec.md`, `docs/tasks.md`, `tools/prepush.py`) and CHANGELOG.md wholesale, since history records what a lane ran and rewriting it is not a fix. Wired into both in-house harnesses (`tests/linux/33-documentation.sh`, `tests/run-tests.ps1`).
- AO-L2-SEAT-INTEGRITY P3b (2026-10-10, lane `lane-seat-integrity`, headless, failing tests first): **a review SEAT can write its findings, and says so when it cannot test.** Measured (run `20261009-165450-mem-tools-sonnet-68764c`): an isolated claude/sonnet review ran `--permission-mode plan` and could not write its own `REVIEW-FINDINGS.txt`; `clients.seat_level` now maps a read-level run that is `--isolate` and not `--read-only` onto claude's declared `"seat"` mode (`acceptEdits`, spawn deny unchanged — writes stay confined to the private clone by the sandbox plus the leak check), and only a client row that declares a seat gains one, so qoder/qwen/gemini/codex argv is byte-for-byte unchanged. Second: a seat sandbox lacked `mcp`, so its project tests never started and the seat reported an untested verdict — `seat_deps_note` (tools/autoos-agent.py, `importlib.util.find_spec`, never an install) appends `project tests unrunnable: missing python modules: …` to the `--review-base` seat brief once when something is missing, and nothing when it is not; an optional per-sandbox `uv venv` from the lockfile stays documented future work.
- AO-SEAT-VERDICT-GRAMMAR P2-FIX (2026-10-10, lane `lane-seat-integrity`, headless, failing tests first): **the verdict grammar is wired into production, and its two semantics bugs closed.** G1a: a lane record line may now carry `evidence=<path>` (`REVIEW_ENTRY_FIELDS` + the hint), and `review_status`/`ready`/`review-status` read that file (resolved against the repo root; a `..` component or a realpath leaving the repo or `logs/briefs/evidence` is refused) through `parse_verdict` — the seat is not counted, with the reason printed in the existing report line, unless the file is a valid verdict AND says the word the entry claims (`evidence verdict REJECT != entry PASS`, `evidence invalid: …`, `evidence file missing`, `evidence path outside the repo`); no `evidence=` is byte-for-byte today's behaviour. G1b: `run --card role=review --review-base` passes the task through `seat_prompt` ONCE, so the seat answers on the standard grammar, and `sandbox base:` is quoted exactly once in the prompt (`isolate_task_prefix(..., stamp_base=False)` when the template carries it) — `base_line` is now a required non-empty argument (`ValueError`; an empty one printed a template that proved nothing). G2: the blindness check reads first-person statements about the seat's own view (`(I|we|this seat|my seat) … (could not|cannot|was unable to|did not|unable to) … (see|read|view|access|find) the change|diff|patch`, `REVIEW-DIFF.patch is|was empty|missing|not found|not present|unreadable`, `no access to the diff`; zero-width/NBSP folded first) instead of a substring of the whole text, and it may only VOID an ACCEPT — a REJECT from a seat that says it was blind stays valid and countable (fail-closed: a blocking verdict must not vanish), a HOLD stays valid and uncountable, and "the fixture cannot see the row" is ordinary prose again. G3: `verdict_word` and `parse_verdict` share ONE `_normalise` that also strips trailing punctuation, list markers (`1.` / `1)`) and stacked decoration (`> # VERDICT: ACCEPT`) in a loop, so `verdict=ACCEPT.` and `VERDICT: ACCEPT.` cannot disagree. Tests: `tests/test_autoos_verdict.py` (blind table — void ACCEPT, keep REJECT, HOLD, and the four non-triggering sentences; the shared-normaliser agreement table; `seat_prompt` refusals) and `tests/test_autoos_spawner.py` (`EvidenceGateTests`: agree / mismatch (cross-family and final) / invalid / missing / three escape spellings / absent field / hint / decorated-entry agreement, plus the real-CLI `ready` fail-closed case and the one-`sandbox base:` dry-run plan case). Gates: `pytest -q tests/test_autoos_verdict.py tests/test_suite_wiring.py` **23 passed**, `-k "review_status or ReviewStatus or ready or Seat or Evidence or evidence"` over `tests/test_autoos_spawner.py` **131 passed**, full `tests/test_autoos_spawner.py` + verdict + wiring + ready_guards **1435 passed, 6 skipped**, `bash tests/run-tests.sh --filter documentation` green. Deviation: ~370 lines changed incl. tests, over the lane's 200 — the judge's test list (12 evidence/grammar/plan cases) is most of it. Not pushed.
- AO-L2-SEAT-INTEGRITY P2 (2026-10-09, lane `lane/seat-integrity`, headless, failing tests first): **one grammar for a review verdict.** Measured: seats wrote `**VERDICT:** ACCEPT` and the judge's parse found 0 refs (GO-REF HOLD); seats that said they could not see the change still got a verdict; the seat text and the record's `verdict=` field each kept their own idea of a yes. `tools/autoos_verdict.py` is that one owner: `parse_verdict(text)` demands `VERDICT: ACCEPT|REJECT|HOLD` on the FIRST non-empty line (case-insensitive, exactly one colon, decoration — bold, backticks, heading/quote/bullet — normalised first), and calls anything else invalid with a specific reason: verdict further down, two verdicts, an unknown word (`SHIP`/`LGTM`), or the reviewer talking after the token. A seat whose text carries a declared-blindness phrase (`could not see`, `cannot see`, `unable to see`, `no access to the diff`, `REVIEW-DIFF.patch is empty|missing|not found`) is INVALID even when its line parses — except a HOLD, which is the correct answer for a blind seat, so valid but `countable: False`. `verdict_word` is the same normalisation for one field, so `_review_entry_verdict` reads `verdict=**ACCEPT**` and `verdict=ACCEPT` alike and `ACCEPT` joins `READY_VERDICTS` (every legacy word — ready/pass/passed/approve/approved/lgtm/ship — still works, REJECT/HOLD/`fix-first` still refuse, reasons unchanged). `seat_prompt(angle, criteria, base_line="")` returns the L2 seat template: the angle, the criteria verbatim, the first-line grammar, the REJECT-severity rule (secret or outside-sandbox leak, stamp mismatch, change not visible, crash instead of a clean refusal; everything else minor + ACCEPT), a repro per REJECT, and the `sandbox base:` line to quote back. Tests: `tests/test_autoos_verdict.py` (5 cases over ~30 assertions: decoration normalisation and countable/HOLD, later-line/unknown-word/extra-words/two-verdict/second-colon/empty, blind ACCEPT invalid and blind HOLD valid-not-countable, seat prompt, entry gate incl. back-compat), wired into the existing `autoos_ready_guards` case of `tests/linux/33-documentation.sh` and `tests/run-tests.ps1` — `tests/test_suite_wiring.py` fails a test file no harness runs. Gates: `pytest -q tests/test_autoos_verdict.py` **5 passed**; `-k "review_status or ReviewStatus"` over `tests/test_autoos_spawner.py` **34 passed** (**39** with the new file); full `tests/test_autoos_spawner.py` + `tests/test_suite_wiring.py` **1392 passed, 6 skipped**; the entry-gate cases were red before the wiring (`verdict ACCEPT` refused) and the parse tests red before the module existed. `seat_prompt` ships as the template its caller pastes — no call site changed in this commit. Not pushed.
- AO-MCP-REVIEW-BASE (2026-10-10, lane `lane-spawner-followups`, S5, headless, failing tests first): **MCP `spawn` can now hand a review seat its base commit.** `run --review-base SHA` (8488a32f) rides `git diff SHA..HEAD` into an isolated sandbox as `REVIEW-DIFF.patch`, stamps `sandbox base: <sha> HEAD <sha>`, and exits 2 on an unknown SHA or a missing `--isolate` — but the MCP tool never carried the field, so an MCP-spawned review seat could not use it. `tools/autoos_agent_mcp.py`: `spawn` takes `review_base` beside `not_family`/`review_of`/`no_fallthrough`, `build_argv` validates it with the CLI's own arg-half rule (`resolve_review_base` answers None for a blank value and for a leading `-`, which would reach the argv as a flag) through the ONE new `review_base_refusal`, then forwards `--review-base <rev>`; anything that NAMES a commit stays the CLI's resolution, so an unknown rev and a clone-less run still surface as the CLI's rc 2 text in a normal error dict, before a run dir exists and never as a traceback. The value rides job.json's `request` like its FAMILYFENCE siblings (the runner-private kill record is not the only copy); `dry_run` is unchanged. Consumers of the spawn request shape updated: the tool docstring/inputSchema and the three pinned CLI arg fixtures (`tests/test_run_budget_spawner.py`, `tests/test_run_budget_spawner_v3.py`, `tests/test_cost_gate_default_path.py`) now list `review_base`. Tests: `McpReviewBasePlumbingTests` (argv carries it when set and omits it when unset; blank / `-x` / option-shaped / over-long refused with no run dir created; job.json records it; the CLI's two rc-2 messages reach the caller; the advertised tool schema names it).
- AO-MCP-REVIEW-BASE (2026-10-10, lane `lane-spawner-followups`, S5, headless, failing tests first): **MCP `spawn` can now hand a review seat its base commit.** `run --review-base SHA` (8488a32f) rides `git diff SHA..HEAD` into an isolated sandbox as `REVIEW-DIFF.patch`, stamps `sandbox base: <sha> HEAD <sha>`, and exits 2 on an unknown SHA or a missing `--isolate` — but the MCP tool never carried the field, so an MCP-spawned review seat could not use it. `tools/autoos_agent_mcp.py`: `spawn` takes `review_base` beside `not_family`/`review_of`/`no_fallthrough`, `build_argv` validates it with the CLI's own arg-half rule (`resolve_review_base` answers None for a blank value and for a leading `-`, which would reach the argv as a flag) through the ONE new `review_base_refusal`, then forwards `--review-base <rev>`; anything that NAMES a commit stays the CLI's resolution, so an unknown rev and a clone-less run still surface as the CLI's rc 2 text in a normal error dict, before a run dir exists and never as a traceback. The value rides job.json's `request` like its FAMILYFENCE siblings (the runner-private kill record is not the only copy); `dry_run` is unchanged. Consumers of the spawn request shape updated: the tool docstring/inputSchema and the three pinned CLI arg fixtures (`tests/test_run_budget_spawner.py`, `tests/test_run_budget_spawner_v3.py`, `tests/test_cost_gate_default_path.py`) now list `review_base`. Tests: `McpReviewBasePlumbingTests` (argv carries it when set and omits it when unset; blank / `-x` / option-shaped / over-long refused with no run dir created; job.json records it; the CLI's two rc-2 messages reach the caller; the advertised tool schema names it). **S5-FIX1**: a revision RANGE (`HEAD~1..HEAD`, `HEAD...HEAD`, `a..b`) passed the MCP rev regex (it allowed `.`), reached the CLI, and `git rev-parse` answered it with 3-4 lines, so the two-slot stamp `"sandbox base: %s HEAD %s" % pair` raised TypeError -> traceback (rc 1) on both the CLI flag and the MCP path; `resolve_review_base` now returns None unless rev-parse yields EXACTLY two full 40-hex tokens (a `_FULL_SHA_RE` per token), routing a range onto the existing clean rc 2 'not a reachable commit' refusal, and `review_base_refusal` additionally refuses any value containing `..` so a range is turned away early and in text. Tests: `P1ReviewBaseSeatTests.test_a_revision_range_base_refuses_cleanly_at_the_cli` (hermetic tmp repo, both range forms exit 2, no Traceback), `test_resolve_review_base_refuses_a_multiline_range_output` (3- and 4-token stub -> None), `McpReviewBasePlumbingTests.test_a_revision_range_is_refused_early_as_text_not_a_traceback`.
- AO-RECOVER-BLANK-CHARS (2026-10-10, lane `lane-spawner-followups`, S4, headless, failing tests first): **a blank-LOOKING character now hides a continuation footer the way a zero-width space does.** The invisibility rule was `ch.isspace() or unicodedata.category(ch) in _INVISIBLE_CATS` (Zs/Zl/Zp/Cc/Cf), so a character that draws nothing but belongs to Lo/So/Mn — U+3164 HANGUL FILLER, U+115F/U+1160/U+FFA0, U+2800 BRAILLE PATTERN BLANK, U+17B4/U+17B5, U+034F, U+180B-U+180D/U+180F, U+FE00-U+FE0F, U+E0100-U+E01EF, U+1D159 — slipped past it, and a record that IS only the footer read as a brief: it bought a continuation leg and stacked a second footer on the one it already was (D-914's loop). ONE frozenset `_BLANK_LOOKING` — a list of code points, deliberately NOT a category, because U+2801 draws dots, U+AC00 draws a syllable and a letter wearing U+FE0F draws a heart, and all of them stay content — behind ONE predicate `_is_invisible_char(ch)`, and every invisibility test in `tools/autoos_recovery.py` calls it (`_has_visible_text`, `_heading_form`'s footer cut, `_footer_prefix_invisible`), so no site drifts behind the others. `_suspect_line`'s `_SUSPECT_CATS` rule is untouched: it judges whether a report can be re-split, not whether a character is blank. Tests: `InvisiblePrefixFooter.test_a_blank_looking_character_hides_the_footer_like_a_zwsp_does` (parametrised per code point, asserted through `plan`) and `test_ordinary_letters_and_marks_are_never_invisible`.
- AO-READY-CALLERS (2026-10-10, lane `lane-spawner-followups`, S4, headless, failing tests first): **every instruction that shows the `ready` gate being run names both writer-guards files.** `cmd_ready` requires `--brief` and `--report` only when the DIFF reaches R2, so a caller that copied a command line out of a doc omitted them silently and was refused at a gate the lane had already cleared. Updated sites: the `tools/autoos-agent.py` usage block (`ready <RECORD> --branch --sha --inbox --brief --report`), `_parser_ready`'s help, `cmd_ready`'s own docstring, `.agents/skills/unattended-orchestration/SKILL.md`'s interface list, and `cmd_ready`'s refusal for an ops lane without the guards, which now prints the exact corrected call with this lane's record, branch, sha and inbox filled in. `tests/test_ready_callers.py` keeps it true: a static lint over `git ls-files` that fails any line spelling the invocation unless the same line — or its continuation within 3 lines — carries `--brief` and `--report`, exempting prose that only mentions the command with the marker `<!-- ready-no-guards -->` (applied at `docs/plans/2026-09-28-restart-spec.md`, `docs/tasks.md`, `tools/prepush.py`) and CHANGELOG.md wholesale, since history records what a lane ran and rewriting it is not a fix. Wired into both in-house harnesses (`tests/linux/33-documentation.sh`, `tests/run-tests.ps1`).
- AO-ROUTER-SCRUB-CI F1a (2026-10-10, lane `lane/router-fold`, headless, failing tests first): **the public scrub gate now reads `.agents/` and `deploy/`.** AutoOS is a public repository and both trees publish router skills and host-unit / hook templates — content about a real machine — while CI's `Public scrub scan` scanned only `infra/ scripts/ docs/ catalog/ tools/ README.md AGENTS.md`. `.github/workflows/ci.yml` adds the two trees and states the reason. `scripts/public-scrub/test_scan.py` guards the scope three ways: it parses the scan command out of ci.yml (a comment naming a tree is not scope), runs the real patterns over the real `.agents/ deploy/` from the repository root, and asserts that neither path is exempted by `scan-exclude.txt` while a planted `.agents/skills/x/SKILL.md` is still reported — a directory-wide exclude there would have kept the job green over an un-gated tree. The first real run reported 5 hits, all throwaway `git config` identities in three `unattended-orchestration` test fixtures, now excluded by exact file with a reason each; no fixture was changed and nothing renamed. `deploy/README.md` is new: template-only, placeholders, no site content.
- AO-JOB-LANE-ID (2026-10-10, lane `lane-spawner-followups`, S3, headless, failing tests first): **the spawner records the L2 lane id in the runner-private kill record as well as job.json.** The only lane a run carried was F1's `parent_lane` in job.json — the worker's own file, so a lane read from it alone is spoofable (R-orch-17) and `plan` could only run for a lane named by hand (D-914). `tools/autoos_agent_mcp.py` validates `AUTOOS_L2_LANE` with recovery's own key rule and writes it to both stores (absent/invalid writes neither and never refuses a spawn); `autoos_recovery.py plan RUN_ID` therefore takes `--lane` optionally, reading the record via `record_lane`, which reuses the launcher's one reader through a lazy import and answers 'no lane' on any fault. No record lane is the same usage exit 2, naming both ways; a `--lane` differing from the record is refused as well, naming both — a caller may not spend another lane's attempts. `record` still requires `--lane`. S3 review fix round: `record_lane(run_id, state)` reads the kill store of the state root `plan(state=X)` was given, so one plan never consults two trees; the private-record half of the spawner's lane test is skipped on Windows where that store is POSIX-only (the job.json half still runs there); and `docs/ai/opencode-fleet.md` quotes the wake's `recovery: <action> (attempts N)` clause and says `plan --lane` is optional once the spawner recorded the lane.
- AO-RECOVER-L2-LOOP (2026-10-10, lane `lane-spawner-followups`, S3, headless, failing tests first): **the L2 stall loop says what recovery would do, not only what died.** `tools/oc_l2.py` `_recovery()` calls `autoos_recovery.plan(run_id)` in-process for a `child-exited` stall whose kill record names THIS lane and whose rc != 0, and `_next_action`, `_wake_text` and `cmd_resume`'s answer carry the action and the attempt count (`recovery: rerun (attempts 0)`, `"recovery": {action, attempts, state}`), all through `_safe_text`. Plans only — no `record`, no `spawn` in the loop. A clean exit, another lane's record, no record, or a plan that raises leaves the existing text byte-for-byte unchanged and the wake still goes out.
- AO-VERTEX-LIVE (2026-10-09, lane `lane-vertex-live`, D-908, headless, failing tests first): **the trailing-turn guard was blind to a mixed user turn, so a signed tool call still 400'd on a body that ends on `user`.** Live capture D-908 (evidence pointer `logs/briefs/evidence/vertex-capture-d908.md`) shows Vertex answering `Requests ending with a model turn are not supported` for upstream contents `user[text]`, `model[thoughtSignature+functionCall]`, `user[functionResponse, text(notice)]` — the body ends on `user`, so the v2 guard had nothing to pop, and the real trigger is a `user` turn that MIXES a `functionResponse` part with a text part on the signed path. Live probes (all 200 unless noted): inserting a synthetic assistant text turn between the last tool result and the user text -> 200 for single **and** parallel (two `functionResponse`) calls; folding the text into the last tool result -> 200 single but **400 parallel** — so the fix is a **split**, not a fold. **v3** rewrites, at the same 12 compiled-chunk call sites plus the `.ts` site, every `user` turn whose parts hold >=1 `functionResponse` part and >=1 part without one into three turns: `{role:"user",parts:[the functionResponse parts, original order]}`, `{role:"model",parts:[{text:"Noted."}]}`, `{role:"user",parts:[the other parts, original order]}`; text-only and functionResponse-only turns are untouched. It runs after `mergeConsecutiveSameRoleContents` and **before** the v2 pop/refill (kept unchanged), so the split's final turn is `user`, the body ends on `user`, and the synthetic model turn sits mid-history where the pop can never reach it. The patcher's state machine is now v3-present -> SKIP; v2, v1 or pristine -> PATCH to v3 (the v2 anchor is frozen literal in the tests like v1, so the upgrade is proved against shipped autoos4 bytes); anything else, or a doubled anchor, -> ERROR. `tools/vertex-patch-gate.py` gained a `split` marker (the synthetic `Noted.` model turn) and now requires `patched(v3) == refill == split == call sites`, so a tree left on the v2 guard fails as `split=0` instead of passing on matching numbers; the report line is now `VERTEX-GATE: total=12 patched=12 refill=12 split=12 v1=0 files=6 minimum=12 verdict=PASS` (was `total=12 patched=12 refill=12 v1=0 …`). `tools/vertex-trailing-turn-reapply.ps1` carries the v3 replacements and the v2/v1/pristine upgrade anchors (chunks and the `.ts` block); `configuration/omniroute/vertex-trailing-turn-README.md` documents the D-908 finding, the split, and why v1 and v2 were each incomplete. The gateway image tag moves `autoos/omniroute:3.8.50-autoos4` -> `3.8.50-autoos5` (`compose.yml`, `docs/web-services.md`) because autoos4 already names the v2 build; recreate + live smoke wait for a written GO naming the autoos5 digest (D-825). Tests: `tests/test_vertex_trailing_turn_patch.py` and `tests/test_vertex_patch_gate.py` extended, not rewritten (69 passed); the D-908 single/parallel shapes, fr-only and text-only untouched, a mixed mid-history turn, a mixed final turn with a trailing model still ending on user, split idempotence, `systemInstruction`, `node --check`, both site-shape families, and the v2->v3 patcher/`.ps1` upgrades are all covered; the failing-first run was 8 failed + 1 collection error before the v3 code landed. Verify `python3 -m pytest -q tests/test_vertex_trailing_turn_patch.py tests/test_vertex_patch_gate.py`.
- AO-SPAWN-COOLDOWN-RETRY (2026-10-10, lane `lane-spawner-followups`, S2, headless, failing tests first): **a combo leg that counts itself down in seconds now gets waited out and retried on the same leg.** Two `--isolate` review runs (`logs/agents/20261009-164000-gw-strict-hex-seat1b-a859a4`, `logs/agents/20261009-164036-mem-tools-seat3-a51764`) ended rc 1 in under 90 s on `Error: All credentials for model deepseek/deepseek-v4-flash-0731free:free are cooling down (reset after 3s)`, with no retry and no fallthrough. Cause, verified against the runs' own bytes: the stop WAS classified — that output.log carries the `provider bazaarlink unavailable until` line only the classified path prints, and `_ANSI_RE` already strips the `\x1b[91m\x1b[1mError: \x1b[0m` prefix the ANSI candidate blamed — so markers, `parse_reset` and `PROVIDER_STOP_WINDOW` were never the bug. Two rules downstream were: a cooldown states no rate-limit wording and no bare `429`, so it is not `rate_limit_stop`, and the rc-1 upgrade covers only rc 0/3/6 — the client's 1 stood, which is what made a refused leg read as a failed task; and `cmd_run`'s only recovery is a RE-PLAN onto a different combo (`elif plan["route"].get("resolver") and plan["sandbox"]`), which this host never reaches because `autoos_routing` ROUTING_VERSION 1 builds its route dict without the `resolver` key, so nothing fell through at all. `tools/autoos-agent.py` adds `cooldown_stop`/`cooldown_wait`/`cooldown_sleep` beside `parse_reset` and, in `cmd_run`, a stop whose cooldown window is <= `COOLDOWN_SAME_LEG_MAX_SECONDS` (60) now sleeps that window +1 and re-launches the SAME plan once — same combo, same sandbox, no re-plan, no in-process bench, no fallthrough spent, no track record against a leg that served 3 s later; the retry is its own numbered attempt (`attempt=fallthroughs + cooldown_retries + 1`) so the kill store and scope stay aimed at what is running. A second cooldown, a window over 60 s, or an unstated one falls through to the next leg exactly as a rate-limit stop does (MAX_FALLTHROUGH and `--no-fallthrough` both respected, and a cooled run ends on the stop's rc 8). Unchanged: every non-cooldown stop, `--joinable` runs (never stop-checked), `record_reset_stop` for a stated window, the ERRCHANNEL rule. Tests: `CooldownRetryTests` (7) in `tests/test_autoos_spawner.py` — the real ANSI-coloured tail fixture classified as a provider stop, retry-once-succeeds (sleep 4, rc 0), second cooldown falls to the next leg, 120 s waits for nothing, unstated falls, `--no-fallthrough` after the retry ends rc 8; hermetic through `_fallthrough_run`'s fake client (D-852) with `cooldown_sleep` patched, so nothing sleeps. 1574 passed / 6 skipped in `tests/test_autoos_spawner.py` with the six `tests/test_run_budget*.py` suites (292 s), 131 on `-k "Cooldown or allthrough or et_reset or ROVIDER"`. Seat-note follow-up (same lane, confirmed by L1): the retry re-runs the loop top, where the `--free` slot gate keyed on `fallthroughs` alone — which a retry never spends — so a `--free` retry started unclaimed while its own worker record was closed during the nap and ran as cap+1 over `policy.free_concurrency`; the gate now reads `fallthroughs or cooldown_retries` and refuses with the same rc 9, covered by `FreeCooldownReclaimTests` (3) plus the `0s` / no-unit / huge-window cases in `CooldownRetryTests` (9).
- AO-FAMILYFENCE-QWEN (2026-10-09, lane `lane-spawner-followups`, S1, headless, failing tests first): **the family fence now excludes only the families it names.** `run --tier=3 --not-family qwen --model omniroute/deepseek-direct-flash` exited 12 saying "no model outside family qwen left", and so did every other pin — the fence asked the model's family of `reviewer_family`, the registry ROW alone, and a gateway pin the client config declares but the registry lists no row for answered None; the review role's `strict` reads None as "cannot prove independence" and blocked it, so under a review every unrowed pin sat inside every fence whatever its id named. The family is now read from the layers that can name one (`fence_family_of` = `run_model_family`'s first three: the registry row, the vendor named inside the model id, the one family a route's legs all declare), so a deepseek/nvidia/qwen pin is judged on the family its own id names. Unchanged: a pin INSIDE the fence is still refused (exit 12), an id no layer can place is still unsafe for a review (D-115's invented reviewer), an unpinned resolver plan still drops every route with a fenced leg, an unpinned `--tier`/v1-card plan still refuses when the one combo it maps to carries a fenced leg and names the way out (`--free`/`--model`), and the `--review-of` writer family still binds. Checked, not assumed: with no pin it is FALSE that every combo carries a qwen leg — 7 of the 45 registry routes do (`l3-driver`, `l3-implementer`, `l3-review-diff`, the three qwen direct routes and `l3-driver-free-only`), the v2 review route plans onto `l1-orchestrator-free-only`. Tests: `FamilyFencePinnedModelTests` (12, per family qwen/deepseek/nvidia across `--tier=3`, `kind=review` v2 and `role=review` v1, plus the unpinned-plan and `--review-of` halves) in `tests/test_autoos_spawner.py`; 1458 passed / 14 skipped there with the budget and cost-gate suites, 518 / 13 in the remaining `cmd_run` and budget suites, 113 on the `-k "Fence or fence"` filter. S1 review fix round: `fence_family_of` now reads a multi-vendor unrowed id (`deepseek-r1-distill-qwen-32b`, `qwen3-nemotron`) as unplaceable instead of the longer token, and the fence is a declared-family check on the pin and its legs, not proof of what the gateway served — the post-run serving-family backstop still applies (`test_the_fence_refuses_a_pin_whose_id_names_two_vendors`, `test_an_unplaceable_pin_is_refused_at_tier3_and_for_a_review_card`). S1 review fix round 2: `vendor_family_name_unique` lets the longer token win when the shorter match's span lies INSIDE it (`openai_oss` and `omniroute/openai-oss-120b` read as `openai-oss`, not unplaceable; only DISJOINT spans are ambiguous), a route the registry CARRIES is judged on its legs alone — a multi-family route is never read by its own name (`deepseek-mixed` with a qwen leg under a qwen fence is refused) — and the fence's per-leg route read uses the unique rule while the record's `route_family` default is unchanged (`test_a_nested_vendor_token_still_names_the_longer_family`, `test_a_registry_route_is_never_read_by_its_own_name`, `test_a_multi_vendor_leg_is_not_read_by_the_longer_token`).
- AO-L2-SEAT-INTEGRITY P1 (2026-10-09, lane `lane/seat-integrity`, D-887, headless, failing tests first): **an `--isolate` review seat can now prove what it is reviewing.** The seat sandbox is a one-commit HEAD, so `git diff A B` / `git show sha` saw nothing and verdicts were reached blind. `tools/autoos-agent.py run --review-base <sha>` (with `--isolate`) writes `base..head` into the sandbox as `REVIEW-DIFF.patch` (committed inside the one commit), stamps the exact line `sandbox base: <sha> HEAD <sha>` into the seat prompt and the run record (`sandbox_base`), and refuses with rc 2 on an unknown/unreachable base, a missing `--isolate`, a moved HEAD, or a pruned object (never a traceback). The patch carries only what the materialiser itself would accept: excluded and sparse-hidden paths, base-side plaintext secrets (refused, paths only), escaping symlinks and unsafe names never ride; pathspecs are literal; a fail-closed backstop refuses the run if the diff would name any path outside the kept set (closes the whole pathspec-expansion class, e.g. dir-to-file); sparse sources carry deletions only when git reports cone mode, else drop them (documented omission, an unreproducible matcher must not decide what leaks). A pinned model now records its family (`source=pinned`, never counted as proven, cross-family gates stay closed). Review: 8 isolated deepseek-direct-flash seats (7 REJECT, each a NEW class, all fixed; final `20261009-230246-p1-seat-ds8-06f711` ACCEPT) + Sonnet final `20261009-230930-p1-seat-sonnet2-e32d50` ACCEPT on 51de290e; writers qoder Qwen3.8-Flash; CI 38003787788. Open items: `--not-family qwen` refuses every model (FAMILYFENCE), MCP `spawn` lacks `--review-base`, a tracked non-UTF-8 name makes `--isolate` raise, base-side secret check is name-based, P2 (verdict grammar), P3 (sandbox hygiene), P4 (L2 footer in foreign repos) not started. Evidence: `logs/briefs/evidence/20261009-230246-p1-seat-ds8-06f711.out`.
- AO-WRITER-GUARDS P4c (2026-10-09, lane lane-writer-guards / wg-p4c-recovery, headless): writer-ops.md §5 now has a home — `tools/autoos_recovery.py` plans the recovery of a dead or stalled writer from the fleet run record alone (`<state>/agents/<run_id>/{job.json,exit.json,output.log}`): `classify_run` answers one of running/completed/died/stalled/unknown (a live pid quiet past `stall_secs` (default 900) is stalled, a dead one died, and **rc 0 AND a REPORT** — an exit 0 that said nothing is the exit-without-REPORT case — is the only `completed`; an unreadable record, an unusable pid or a corrupt budget is `unknown`, never `completed`), `next_action` maps attempts+state to none/wait/rerun/escalate at `MAX_ATTEMPTS=2`, `continuation_task` rebuilds the brief with the `CONTINUE FROM CURRENT DIFF (recovery attempt N/2)` footer (task capped at 200 KiB so the footer always arrives), and attempts are counted **per lane key** (`<state>/recovery/<sha256(key)>.json`, mode 0600, atomic temp+`os.replace`, idempotent per run id, and the lane key is ALWAYS caller-supplied — D-914 removed the derived default key (sha256 of the task head): key drift across continuation legs, footer-only tasks collapsing onto one sha256-of-'' budget, and Cf/Cc-prefixed footers were all rooted in hashing free task text) so two dead writers on one lane spend one budget. `plan(run_id, lane_key)` returns the verdict plus `spawn_hint.cwd` — the kept worktree, parsed from the `sandbox:` / `review:` / `take it:` tail lines through the same path validation (`..`, relative, control chars, `~` refused; only the last 256 KiB of `output.log` is read) — and **never spawns**: the L2/L1 that asks calls `spawn` with it. CLI `plan RUN_ID --lane KEY [--stall-secs N]` (`--lane` required — without it, like a bad run id or key, exit 2) exits 0 none/wait, 3 rerun, 4 escalate; `record RUN_ID --lane KEY` spends one leg. Stated residuals: pid reuse cannot be detected from the record, a deliberately cancelled run reads as `died`, and a `sandbox:` line whose path contains a space is only recovered from the shlex-quoted `review:` pair. HERMETIC (D-852): pure file reads/writes in a caller-named state dir (`AUTOOS_STATE_DIR` moves the tree), time through the module's own `_now` hook, liveness through an injectable `pid_probe` — no git, no subprocess, no network; the 104 tests (`tests/test_autoos_recovery.py`, wired into `tests/linux/33-documentation.sh` and `tests/run-tests.ps1`) assert the states, the stalled/running boundary, garbage and non-dict records, the tail limit, the refusals, the 3rd-death escalation, and an AST check that the module imports nothing it could shell out with. `tools/autoos-agent.py` untouched.
- AO-SERENA-NOMEM-DOC (2026-10-09, lane `lane/serena-doc`, D-895): `infra/mcp-servers/README.md` says to check which compose project owns `serena-mcp` before recreating it — on a host where another project owns it, `docker-compose.client.yml up -d serena` clashes on the container name, swaps the `serena_data` volume and drops a pwsh-carrying image; the no-memory `--context` goes into the owning compose instead. Found while preparing the D-894 live recreate, which was withdrawn for exactly this.
- AO-WRITER-GUARDS P4b-fixes (2026-10-09, lane lane-writer-guards / wg-p4b-fixes, headless, round 1): the deepseek attacker seat's REJECT of P4b named three real defects in `ready`'s writer-guards, all of them **fail-open doors in the reader**, and each now has a regression test that is red on the pre-fix module. **D1 (HIGH) — the ops detection was defeated by the reader's own convenience.** `lane_diff_paths` stripped the leading `+` off the diff body and handed the ALREADY-stripped text to `requires_ops_guards` → `autoos_writer_rule.required_r_level` → `_added`, which is written for RAW `git diff` text and strips it *again*: on stripped text one added line whose source starts with `--- ` / `@@` / `diff ` (a markdown horizontal rule, a pasted patch, a diff-stat table) makes `_added` conclude the body IS a diff and return only its `+`-subset — empty, because the real `+` was already eaten — so a lane that added `token: …` read **R1**, `ops_required` came back False and `ready` exited 0 with no brief, no report, no scope fence. Measured pre-fix: `_added` `[]`, level `R1`; post-fix `R3`. FIX: the reader returns the RAW `git diff -U0 --no-color <base>...<sha>` body verbatim (the `--no-color` keeps escape sequences out of the text the risk patterns read) and `cmd_ready` passes it on unchanged. **D2 (MED-HIGH) — `--base` equal to `--sha` cleared the gate.** `git diff abc…abc` is empty → `ops_required` False → nothing to fence; any base that is not an ancestor did the same. FIX: the reader now verifies the range before it reads it — `git rev-parse --verify <base>^{commit}` and `<sha>^{commit}` must both name a commit, the two oids must **differ** (a commit is its own ancestor, so `--is-ancestor` alone would not catch it) and `git merge-base --is-ancestor <base-oid> <sha-oid>` must say yes; then an empty path list is refused too, because a lane that changed nothing is not a lane that is ready. Those refusals are a new, third shape: `LaneDiffRefusal`, raised by the reader and caught by `cmd_ready` as **exit 1** naming `writer-guards: --base must be a strict ancestor of --sha` / `writer-guards: empty lane diff` — distinct from the returned `error`, which stays **exit 2** (`cannot read diff`, and over the 2 MiB cap now reads `diff too large to judge`) because "the lane failed the gate" and "the gate never ran" are different next actions. A base that does not resolve (an unfetched `origin/main`) is the exit-2 shape, never an allow. **D3 (LOW) — a non-UTF-8 diff crashed the gate.** `subprocess.run(text=True)` raises `UnicodeDecodeError`, a `ValueError` the reader's `except (OSError, SubprocessError)` does not name, so `ready` died on a traceback. FIX: git runs in bytes mode and decodes leniently — `surrogateescape` for the path list (an invalid byte is a NAME; the scope fence has to compare the same bytes back, and `--name-only` never quotes such a path away), `replace` for the body, which is only pattern-matched. Tests (`tests/test_autoos_spawner.py`): `LaneDiffPathsTests` rewritten around the five-git-call sequence with a `seq()` helper that hands bytes like real git does — argv-by-argv shape incl. no `text=`/`encoding=`, raw body unstripped, the composition test (`this reader's output` → `requires_ops_guards` → ops True), base==sha (refuses after 2 calls, before `merge-base`), non-ancestor (after 3, before the diffs), empty diff, unresolvable base / non-answering `merge-base` / failing `diff` as errors, the 2 MiB cap on both reads, `OSError`/`TimeoutExpired` at each stage, a `docs/\xffunfriendly.md` path round-tripped byte-faithfully and an invalid byte in the body replaced; `ReadyWriterGuardsTests` gained the end-to-end D1 pair (`ops lane without --brief`, then the cleared lane's ` guards=ok ops=1` line) and the two exit-1 mappings for the refusals; the driver's `diff_added` parameter is now `diff_raw` plus `diff_refusal`. `cmd_ready`'s docstring, the `--base` help and this entry record the changed semantics; `tools/autoos_ready_guards.py` needed nothing (it only forwards `diff_text`). HERMETIC (D-852) unchanged: `stub_lane_diff()` signatures in `ReadyCommandTests` / `test_ready_main_freeze.py` are untouched and no ready test runs git. Gates: `pytest -q tests/test_autoos_spawner.py -k "ready or Ready or LaneDiff"` **74 passed**; whole file **1363 passed / 6 skipped**; `tests/test_ready_main_freeze.py tests/test_autoos_ready_guards.py tests/test_windows_portability.py` **153 passed**. Red-then-green proved by swapping `git show HEAD:tools/autoos-agent.py` under the new tests: **15 of them fail pre-fix**, and the two D1 composition cases fail with `AssertionError: False is not true` / the stripped body. Not pushed.
- AO-WRITER-GUARDS P4b (2026-10-09, lane lane-writer-guards / wg-p4b-ready-wiring, headless): `tools/autoos-agent.py ready` now **runs the P4a guards** instead of leaving them a library nobody calls. A new `writer-guards` gate sits between the pushed-sha check and the CI check and reads the lane's own diff through one injectable reader, `lane_diff_paths(repo, base, sha)` (`git diff --name-only --no-renames <base>...<sha>` plus `git diff -U0` added lines, 2 MiB cap, fail closed to exit 2 `cannot read diff`), so **ops work is decided from what the diff touched, not from the card's label** — a `--task-type docs` lane that edited `playbooks/x.yml` is an ops lane. New options `--brief PATH` / `--report PATH` / `--base REF` (default `origin/main`) / `--task-type {ops,code,docs,infra}` (default `code`): when the diff reaches R2 the brief is required and its canonical `FILES` line becomes the allow-list (`scope_fence` refuses naming every violating path — `scope-fence`), and the REPORT must carry CHECK 1-6 with a PASS verdict and evidence (`report-checks`, naming what is missing / failed / input_required); a `GuardError` from any helper is exit 1, an unreadable `--brief`/`--report` exit 2. A lane that clears the guard appends ` guards=ok` (plus ` ops=1` for an ops lane) and a lane that triggers nothing appends no new field, so existing inbox lines still parse. New verb `preflight` prints `tool_preflight`/`preflight_report` as JSON — exit 0 all present, exit 3 `input_required` with the documented install step, and nothing installed. HERMETIC (D-852): the new gate's tests stub `lane_diff_paths`, `remote_branch_tip`, `prepush_mod.local_green` and `main_ci_status` and start **zero** processes (verified with a `subprocess.run` landmine over the whole class), and the existing `ReadyCommandTests` / `MainCiFreezeTests` fixtures keep their shape through ONE shared `stub_lane_diff()` in each class's `setUp` — with a `ready()`-level assertion plus `test_a_ready_test_that_forgets_the_diff_stub_cannot_run_real_git` so a future ready test that forgot the patch fails loudly rather than shelling out to git.
- AO-VERTEX-GUARD-LIVE (2026-10-09, lane `lane/vertex-live`, D-877 steps 1-3): the gateway image tag moves `autoos/omniroute:3.8.50-autoos3` -> `3.8.50-autoos4` so the build that carries the VERTEX-GUARD v2 guard (826a25b5) and the counting gate (`tools/vertex-patch-gate.py`) cannot be confused with the v1 image still running. autoos3 (`7ac22153d432`) stays on the host as the rollback tag; recreate + live smoke wait for a written GO naming the autoos4 digest (D-825).
- AO-SERENA-NOMEM (2026-10-09, lane `lane-serena-nomem`, D-869, headless): **the shared Serena SSE server on `127.0.0.1:9121` no longer exposes memory tools — `docker-compose.client.yml` now starts it with `--context /serena-config/serena-context-no-memory.yml`, a read-only bind mount of the new `infra/mcp-servers/config/serena-context-no-memory.yml` (a fork of the image's own `claude-code` context — what `--context ide-assistant` resolved to — plus `onboarding` and the six `write_memory`/`read_memory`/`list_memories`/`edit_memory`/`rename_memory`/`delete_memory`).** Every repo-side disable up to now (`~/.serena/serena_config.yml` via `ensure_serena_exclusions`/`Set-AutoOSSerenaExclusions`, opencode.jsonc, `catalog/agent-harness.json`, `config/mcp*.json`) reached only *clients* and only the host config file, which the container never reads: it carries its own `/root/.serena` volume, so Claude Code sessions saw the seven tools. Chose a context file over the two alternatives — `--mode no-memories` sets a *dynamic* mode, and a dynamic mode's exclusions deliberately never reach the *exposed* tool set of a multi-project server (`serena/agent.py:_create_base_toolset`), so `tools/list` keeps advertising them; binding `serena_config.yml` into the container would break project registration, because Serena re-saves that file (`serena/config/serena_config.py:_save`). Verified against a throwaway `serena-agent==1.7.0` SSE server on loopback ports 9137/9138 (never `:9121`, never the running container): with the vendor context `tools/list` returns 22 tools including all seven, with the new context 16, `find_symbol` intact. `tools/check-serena-tools.py` gained `--tools-file` (JSON capture: name list, tool objects, `tools/list` result, or a full JSON-RPC response) and `--url` (the MCP SSE two-legged handshake: GET the stream for the `endpoint` event, POST JSON-RPC, read answers off the stream), sharing one `judge()`; the live probe against `:9121` is an operator/L1 run **after** the GO'd recreate, never a test. Docs now send project notes to Omnigraph (`INSTALL-GUIDE.md` step 3, `README.md` data flow + starters list, `mcp-servers-setup` SKILL.md — its "one home for the setting" claim is now two, host config + container context). Tests: 7 in `tests/linux/06-serena-tool-exclusions-serena.sh` (serena-filtered, hermetic) + 16 in `tests/test_serena_shared_server_no_memory.py`; 14 of the 16 fail on the pre-change tree (`git archive HEAD`), and the two that pass assert the invariants that had to survive.

- AO-REDACT-SPAN (P4, 2026-10-09, lane lane-l2-fixes / redact-fix, headless, failing tests first): `tools/autoos_redact.py` closed the two **single-line** shapes that still printed key material in the clear, in the report stream and in the L1's own answer. **The real leak (:370):** the END line under an open BEGIN was masked as a *span*, so the text in front of the marker survived — and the last body line of a wrapped key, or of a one-line log record, is **glued** to that marker with no whitespace at all (`QUJD…WVo=-----END PRIVATE KEY-----`), which printed that base64 verbatim. There is no safe way to tell that run from prose, so the line that closes an open BEGIN is now masked **IN FULL** (fail closed): `return TEXT_MASK` after `_exit_pem()`. The cost is pinned where it lands — `test_a_line_that_closes_an_open_begin_is_masked_in_full` (which replaces the old `test_an_end_marker_inside_prose_is_still_masked`) records that prose on that one line is lost with the key, because a lost report line is recoverable and a printed key is not. The same glue on the **BEGIN** side is closed by `_glue_start`, which walks back over the PEM alphabet (`[A-Za-z0-9+/=]`, `-`/`_` deliberately excluded so limit (A)'s base64url and a hyphenated word in front of a quoted marker stay prose) and moves the mask start there: `foo-----BEGIN RSA PRIVATE KEY-----MIIB…` is masked from `foo`, and a floor at the previous span end keeps the walks of one line non-overlapping, so a line still costs one linear pass. A whitespace-separated prefix is untouched — `test_prose_in_front_of_a_begin_marker_still_keeps_its_prefix` keeps the fixture-list report that the original AO-REDACT-SPAN failure was about. **Only the first pair (:393-396):** one `search` for the BEGIN and one for its END meant `a <RSA block> b <EC block> c` masked the RSA block and printed the EC one whole; `_mask_markers` replaces the pair special-case with a left-to-right walk over `_PEM_MARKER_RE` (derived from the BEGIN/END grammars, so a label widened later widens the walk at once), masking **every** complete pair on the line, each counting one key, and an unclosed trailing BEGIN still opening the body-only swallow. Fail closed extends to the stray case: an END nobody is waiting for masks the run glued to it, and a line that closes one key **and opens the next** masks the whole line but restarts the swallow, so the second key's body cannot ride underneath it. `redact_argv` is untouched (hostexec's pinned text). **oc_l2 (:999):** the probe-unknown `detail` carried an unfilled `%s`, so the caller of `resume` read the literal `(%s)` instead of the reason the lane was left running — filled with `% reason`, and `_assert_probe_unknown` now asserts the reason is named and that no `%s` survives into a detail string. Tests: `tests/test_autoos_redact_span.py` gained `PemGluedMarkerTests` (7: glued END for RSA/EC/PKCS8/PGP-BLOCK, a glued END with key text after the marker, a glued prefix before an open BEGIN and before a complete pair, a stray glued END outside PEM mode, the prose prefix that must survive) and `PemMultiPairTests` (4: two blocks, three blocks, a pair plus an open BEGIN, whole-stream and line-by-line agreement); `tests/test_oc_l2.py`'s helper pins the detail. VERIFY: `python3 -m pytest -q tests/ -k redact` → 90 passed; `python3 -m unittest tests.test_oc_l2` → 99 passed; the surviving VERDICT and cap tests, `tests/test_hostexec_log.py`, `test_autoos_risk.py`, `test_memory_events.py` and the spawner's redaction/cost cases all pass, and a 200 KB line still costs ~5 ms.
- AO-REDACT-SPAN (P3, 2026-10-09, lane lane-l2-fixes / tiny-fixes-p1-p3, headless): `tools/autoos_redact.py` covers the **PGP armour** shape the PEM matcher missed, and the two limits the span machine accepts are now written down where they bite and pinned, instead of being met as surprises. **The missed shape:** `_PEM_BEGIN_RE`/`_PEM_END_RE` ended their label at `KEY`, so an RFC 4880 armored secret keyring — whose label is `PRIVATE KEY` plus ` BLOCK` — matched nothing at all and printed **whole**, base64 and END marker included (the entry below only ever masked what its patterns name). The label takes the optional ` BLOCK` suffix now, and `_PEM_HEADER_RE` treats the armour headers (`Version:`, `Comment:`, `MessageID:`, `SessionKey:`, `Label:`, `Arc:`) as header lines, because an armored body sits underneath them behind a blank line — the blank and the closing `=XXXX` CRC24 line were already body-shaped, so neither needed a rule of its own. A **PUBLIC** label still does not match: only a PRIVATE label is secret, and `test_a_pgp_public_key_label_is_not_taken_for_a_private_one` keeps the widening from turning back into over-eating. **The cap:** `PEM_BODY_CAP` 200 → **256** — an armored keyring export is a primary key plus its subkeys, each with its own signatures and CRC line, so the 6-30 lines of one RSA-4096 are the floor of what a real block costs, not the ceiling; `test_a_block_that_fills_the_body_cap_is_masked_whole` reads the bound off the constant and asserts the constant, so lowering it again below an export that fits fails loudly. **The two documented limits** (code comment + test each, no silent acceptance): **(A)** `_PEM_BODY_RE`'s alphabet is STANDARD base64, all RFC 7468 allows in a PEM body, so a line in the **base64url** alphabet (`-`/`_` where `+`/`/` belong) is not body and **ends PEM mode** — deliberate, because a body that strays from the PEM alphabet is not a PEM block and swallowing on anything-and-everything is the over-eating this shape replaced; the cost is that a base64url-encoded key handed over under an open marker is masked only by the per-line value patterns, never by the span. **(B)** a `VERDICT:` line is never swallowed, so key text a worker **appends to its own verdict line** after an open BEGIN stays in the clear — the trade is one-sided on purpose: a report that lost its verdict to a swallow is useless to the caller that reads it (exactly the failure the entry above records), while a worker that puts key material on its verdict line has already broken the reporting rule it was given; a value a per-line pattern does name — bearer, `token=` carriers, vendor prefixes, the injected literals — still masks there. Tests: `tests/test_autoos_redact_span.py` gained `PgpArmorTests` (4: full block with headers/CRC, a subkey-bearing export, an unterminated PGP mention keeps its prose and its verdict, PUBLIC not mistaken for PRIVATE), `PemDocumentedLimitsTests` (2, one per limit) and the cap case — 30 total, markers split in the source so no key shape sits in the tracked file as one string. Gates: `pytest -q tests/test_autoos_redact_span.py` **30 passed** and `python3 tests/test_autoos_redact_span.py` OK (the harness shape); against the pre-fix module **4 of the new cases are red** (both armored-block masks, the unmasked PGP marker, and `200 not greater than or equal to 256`) and green after, while the two limit cases and the PUBLIC-label guard pass on both sides — which is the point: they pin accepted behaviour, not a bug. `pytest -q tests/test_oc_l2.py tests/test_autoos_redact_span.py tests/test_windows_portability.py tests/test_agent_mcp_tool_profile.py` = **201 passed**; `pytest -q tests/test_autoos_spawner.py -k "Redact or edact or Pem or PEM"` = **32 passed / 1272 deselected**; `pytest -q tests/test_autoos_risk.py` = **108 passed**. No other Redactor behaviour, mask text, carrier pattern or caller changed. Not pushed.
- AO-REDACT-SPAN (2026-10-09, lane lane-l2-fixes / p3-writer, headless): `Redactor._one_line` masks **the span it matched**, never the whole line and never the rest of the stream. `tools/autoos_redact.py`: a line with a `-----BEGIN … PRIVATE KEY-----` marker and no END set `_in_pem` and swallowed *every* following line until an END that may never arrive — so a worker report that merely MENTIONED the marker (a detector's regex string, a python list of fixture strings, prose) lost its tail **including its `VERDICT:` line**, and the reviewer looked like it never answered (observed runs 20261009-054241 / 055402 / 060115, `logs/agents/<run>/output.log`: `VERDICT` present 0 times, 25/22 secrets "redacted" that were never secrets). The BEGIN line also returned the marker for the WHOLE line, erasing the prose in front of it. Now: BEGIN..END on one line masks only that span (both sides survive); an open BEGIN masks from the marker to end of its line — the rest of that line may already be base64 body — and PEM mode continues only over lines that look like key body (`[A-Za-z0-9+/=]{16,}` fullmatch, or a `Proc-Type:`/`DEK-Info:` header), stopping at the END line (span-masked, so one key still counts once), at the first non-body line (processed normally, not swallowed), after `PEM_BODY_CAP` = 120 body lines, or at any `VERDICT:` line, which is never swallowed and ends the block. The survivors go through the ordinary injected-literal + value-shape pass, so masking only got narrower where it was over-eating, never weaker. `tests/test_autoos_redact_span.py` (new, 9 cases: span, prefix-kept, body-until-END, prose-stops-swallow, 25-line real block counts 1 and the report around it survives, cap, VERDICT line, short-line-not-body, and the observed python-list shape with the marker mention and `VERDICT: ACCEPT`) is wired into `tests/linux/33-documentation.sh` and `tests/run-tests.ps1` — `tests/test_suite_wiring.py` fails a `tests/test_*.py` no harness runs. No existing Redactor case changed: `REDACT_PEM_LINES` (BEGIN, 2 base64 lines, END) still masks all three and keeps the surrounding output. Gates: `pytest -q tests/test_autoos_redact_span.py` 9 green (and `python3 tests/test_autoos_redact_span.py` OK, the harness shape); `pytest -q tests/test_autoos_spawner.py` **1299 passed / 5 skipped / 0 failed** (baseline at the branch point: green); the redact classes alone `-k "Redact or Pem"` 32 green; `pytest -q tests/test_windows_portability.py tests/test_hostexec_log.py tests/test_memory_events.py tests/test_autoos_risk.py tests/test_suite_wiring.py tests/test_affected_tests.py` 231 green; `shellcheck -S warning tests/linux/33-documentation.sh` clean; `run-tests.ps1` parses clean with 0 `Invoke-ScriptAnalyzer` errors. The redaction was proved red against the pre-fix module before it was fixed: the old code drops `]`, `return len(cases)` and `VERDICT: ACCEPT` from that same stream, the new one keeps all three. Not pushed.
- AO-VERTEX-GUARD-GATE (2026-10-09, lane VERTEX-GUARD gate `06c19a`, headless, failing tests first): **the Vertex build gate counted the chunks it was told about, so a 13th call site shipped unpatched and green.** `configuration/docker/ai-stack/omniroute.Dockerfile` asserted `Done: 12 patched, 0 skipped, 0 errors` and counted `text:"Continue."` over a hard-coded list of the 6 chunk names from `tools/apply-vertex-patch.py`'s table — every one of those numbers is a property of the *table*, not of the image: a gateway bump that adds a 13th `mergeConsecutiveSameRoleContents` call site in a new chunk leaves that site unpatched, the patcher never sees it, still reports 12/0/0, the refill count stays 12, and the build passes; a bump that renames a chunk failed only because a named file was missing, never because a site went uncovered. The counting moved into `tools/vertex-patch-gate.py <chunks-dir> [--min-sites 12] [--run1 F] [--run2 F]`, which reads every `*.js` chunk on disk (patch backups and `node_modules` skipped) and derives `total` = call sites (the merge function dereferenced and applied; a `function …(` declaration and a `name:()=>…` re-export are not calls), `patched` = v2 guards, `refill` = `Continue.` pushes — all three patterns built from the patcher's own bytes, so the guard is described once. The build stops unless patched == total AND refill == total AND total >= 12 AND no v1 guard text survives AND both patcher runs account for exactly `total` sites (a second run that patches anything fails: idempotence asserted, not assumed). It prints `VERTEX-GATE: total=… patched=… refill=… v1=… files=… minimum=… verdict=…` plus the offending file name, so a red build names the chunk a human then adds to the table; no chunk name and no fixed site count are left in the recipe. Corrected in `configuration/omniroute/vertex-trailing-turn-README.md` too: the stated reason for the v1-first anchor order — "the pristine anchor is a substring of the v1 text" — is false for the compiled chunks and true only for the `.ts` site, measured over the patcher's table (0 of 12 chunk anchors contain the other form, because the guard sits between head and tail; the `.ts` pristine line is the first line of the block v1 wrote, so matching it first would splice v2 above the `if (result.contents.length > 1)` and leave both guards running, the broken one still popping); the chunk table keeps the same order because the two tables are asserted byte-identical, so one rule covers both tools. Tests are hermetic (D-852, `tests/test_vertex_patch_gate.py`, 23 cases: a fully patched 12-site tree passes; a 13th unpatched site in a chunk no table names fails with total=13/patched=12 and names the file; the 13th site left on the v1 guard fails; a renamed chunk is still counted (total=12, patched=10) while a renamed *patched* chunk passes; definitions and re-exports are not counted and a direct `fn(args)` call is; 2-of-2 patched fails the 12 floor and passes at `--min-sites 2`; a run summary that misses a site, reports errors, or patches on the second run each fails the gate), and `tests/linux/34-ai-services.sh` now asserts the recipe delegates to the gate, names no chunk, hard-codes no count and runs the gate between run 2 and the backup cleanup. Verify `python3 -m pytest -q tests/test_vertex_trailing_turn_patch.py tests/test_vertex_patch_gate.py` and `bash tests/run-tests.sh --filter vertex` (4 passed).
- AO-VERTEX-GUARD (2026-10-09, lane VERTEX-GUARD, routing D-859, headless): the U2 Vertex trailing-turn guard tested `contents.length>1`, so a translated request whose contents is ONE lone model turn — the shape that actually reproduces Vertex's "Requests ending with a model turn are not supported" 400 (hermetic replay steps 5/9/10) — was never stripped, while `>=1` pops it and sends `contents: []`, which Vertex rejects too; the guard is now v2, popping every trailing model turn and refilling an emptied contents with one synthetic `{role:"user",parts:[{text:"Continue."}]}` turn instead of refusing the call (a refused call is the failure being removed — it kills the unattended lane), the model text it pops being dropped rather than re-appended. Same 12 anchors, so `tools/apply-vertex-patch.py` and `tools/vertex-trailing-turn-reapply.ps1` gained an upgrade-in-place path (SKIP when v2 is present, PATCH from pristine or v1, ERROR on anything else — the v1 form is matched before the pristine one — order that matters only for the `.ts` site, whose pristine line the v1 block begins with, since the 12 chunk anchors do not contain each other), and because a v1 patcher would also report `12 patched` the omniroute.Dockerfile build stage now asserts the guard's own bytes over the 6 named chunks (12 refill sites, no v1 text) — measured: the v2 patcher passes that check and the old v1 patcher from `git show HEAD` fails it with rc=1. That count over 6 named chunks was itself a fail-open and was replaced the same day by `tools/vertex-patch-gate.py`, see AO-VERTEX-GUARD-GATE below. Tests are hermetic (D-852, `tests/test_vertex_trailing_turn_patch.py`: fixture chunk text, `node --check` plus a per-shape harness over the patched bodies, `pwsh` over a fake package tree where installed — 30 cases, no container runtime, no network); the `.ps1` keeps the repo's BOM'd-UTF-8 pure-ASCII contract (`.gitattributes`, Windows PowerShell 5.1 code-page decoding); root cause, the trade-off and the replay's two dead ends (no post-translation compact-and-retry path exists in 3.8.50; the combo-declared context never gates a vertex leg) are in `configuration/omniroute/vertex-trailing-turn-README.md`; verify `python3 -m pytest -q tests/test_vertex_trailing_turn_patch.py` and `bash tests/run-tests.sh --filter vertex`.
- AO-GATE-HERMETIC (2026-10-09, lane `apply-go-ref`, D-852 round, headless): **every D-825 test is hermetic now — one of them had restarted the live omnigraph-server.** The incident: `compact-graphs.ps1`'s valid `-Go -GoSha` case *cleared* the gate, and a cleared gate is where the danger starts — the case went on to exec the host's real `docker` and the container came up at `2026-10-09T08:46:48Z`. So the suite, not the tool, carries the guard. `tests/run-tests.sh` owns the stand-ins (`gate_stubs_make` / `gate_stub_run` / `gate_stub_saw`): a recording PATH directory for `docker curl systemctl ssh omniroute npx node` that logs the call, prints nothing and exits 0, with every gateway/graph URL var (`AUTOOS_OMNIROUTE_URL`, `OMNIROUTE_BASE_URL`, `AUTOOS_OMNIGRAPH_URL`, `OMNIGRAPH_URL`, `OMNI_S3`) pinned to the discard port `http://127.0.0.1:9` unless the case names its own loopback stand-in. Wired through `tests/linux/40-omnigraph-images.sh`, the `tests/linux/34-ai-services.sh` D-825 battery (host-only stand-ins where the case depends on `curl` FAILING — a full shadow would false-green a down-gateway case), `tests/run-tests.ps1` (`New-GateStubDir` / `Get-GateStubEnv` with `.cmd` twins, `Assert-GateUntouched` on all 9 refusal cases, real loopback stand-in gateways for the apply and compact valid-GO cases so the recorded call is provable instead of a 120 s stall), and `tests/test_go_gate_ref.py` (`Standins`, 28 tests green). The shadow itself is a tested property — four bash, three pwsh and four pytest guards fail if `command -v docker` resolves outside the stand-in dir, if a stub falls through to the host binary, or if the URL sink is not a closed port; each is named `D-825: guard (D-852) …` so `--filter D-825` selects them. Wiring also exposed a stale pin: two `apply prune` pwsh cases hard-coded an orphan combo that is not in `combos.json`'s `omitted` list, so the expectation could never be met — now read from the file (`Get-AutoOSOmittedCombo`), mirroring bash's `_omitted_combo`. Tests and helpers only; no product code changed.
- AO-APPLY-GO-REF-FIX3 (2026-10-09, lane `apply-go-ref` fix round, headless): **a GO reference now has to name an artefact that EXISTS — the judge's HOLD was that the gate checked only its SHAPE, so `--go D-1` sailed through.** `_go_gate.py` is the one implementation of both bars (`verify_ref()` + `enforce(go_offline=…)`, and a `verify-ref` subcommand CLI): `D-<n>` must appear as an exact id in the routing decisions log (`$AUTOOS_DECISIONS_LOG`, else `$AUTOOS_ROUTING_DIR/docs/decisions-log.md` and `$AUTOOS_ROUTING_DIR/DECISIONS.md`, default routing dir `~/code/routing`), `OS-<n>` in that dir's `QUESTIONS.md` or `ANSWERS.md`, and a judge run id `YYYYMMDD-HHMMSS-…` as a worker record (`$AUTOOS_WORKERS_DIR/<id>.json`, else `logs/workers/<id>.json` / `logs/agents/<id>/` under the checkout). The match is the whole id, so `D-82` never satisfies a `D-825` line, and an unreadable or missing source refuses like an absent one; `--go-offline` / `-GoOffline` is the operator's declared exception — the run proceeds and logs `GO-OFFLINE: <ref> unverified` as its first line, while `-GoSha`/`--go-sha` against `git rev-parse HEAD` is still verified. Check order is shape, then sha, then the artefact, so a malformed ref or a GO naming the wrong code never reaches a lookup. `apply.sh` and `apply-cluster.sh` exec the CLI; the PowerShell gates (`apply.ps1`, `apply-capability-overrides.ps1`, `compact-graphs.ps1`) call it through the new `infra/mcp-servers/scripts/invoke-go-gate.ps1` front, which resolves the interpreter, returns `{Code, Out, Err}` and never prints or exits, so each gate keeps its own voice and no ref grammar lives there; the Python live-store tools keep importing the module. The inline `[[ =~ ]]`/`-notmatch` copies of the grammar are gone — the only duplication left is each caller's degraded-path line for a host with neither python nor the gate script, which refuses unless `--go-offline` was said. **`apply.sh` now refuses an unknown flag with exit 2** (`--bogus` used to be silently ignored, the way `apply-cluster.sh` already refused it). Tests: the checked-in artefact tree `tests/fixtures/go-gate/` (routing log + DECISIONS + QUESTIONS/ANSWERS, a workers tree with one `*.json` record and one agent dir — `logs/` is git-ignored, hence the separate dir name), `tests/test_go_gate_ref.py` (24 cases: presence per ref kind, both directions of the exact-id boundary, `$HOME` defaults, the whole-source rule for an explicit `AUTOOS_DECISIONS_LOG`, verdicts and the `verify-ref` CLI in both flag styles), the bash shards `tests/linux/34-ai-services.sh` and `40-omnigraph-images.sh` (refuse-on-absent, D-82 vs D-825, OS present/absent, run id present/absent, unreadable source, `--go-offline` proceeds + logs first and still refuses a bad sha, `apply.sh --bogus` → rc 2, dedup-graph/apply-cluster wiring), and `tests/run-tests.ps1` (the same bars through `apply.ps1`, `apply-capability-overrides.ps1`, `compact-graphs.ps1` and the front directly). The Windows shard is now hermetic — the child runs get the fixture routing dir and workers tree instead of the host's own `~/code/routing`, so a CI box with no artefact tree decides exactly like the machine the tests were written on — and `compact-graphs.ps1: a valid -Go + -GoSha clears the gate` runs against a stubbed `docker` on `PATH`, because clearing that gate on a host with the stack up was stopping and restarting the live `omnigraph-server` from a test suite. The stand-in gateway cases (`apply prune: …`, `apply registers meta_api …`) invoke `apply.ps1` for real and were left without a `-Go` when this gate first landed, so `Invoke-AutoOSPruneApply` now carries `-Go D-825 -GoSha <HEAD>` plus the fixture tree on every non-`-DryRun` call, the way the bash twins in `tests/linux/34-ai-services.sh` already did — two of them run green again and the rest fail on their own stand-in issues, not on the gate. Documented in `configuration/README.md` (the ref→source table), `docs/models.md`, the omnigraph runbook, each script's own header/usage.
- AO-APPLY-GO-REF-CONT (2026-10-09, lane `apply-go-ref` continuation, headless): the D-825 survey is finished and every tool the running store actually depends on is now behind the same GO gate. **Four live-omnigraph-store mutators that sit next to the already-gated `apply-cluster.sh` and were overlooked the first pass are gated now**: `infra/mcp-servers/scripts/compact-graphs.ps1` (stops and restarts the running `omnigraph-server` and rewrites every graph manifest), `infra/mcp-servers/scripts/dedup-graph.py` (a live run resets the store and overwrite-loads), `infra/mcp-servers/scripts/populate-embeddings.py` (a live run overwrite-loads the graph via docker), and `infra/mcp-servers/scripts/split-project-graph.py --apply` / `--prune-source` (merge-loads the live target graph, deletes nodes from the live source graph). Each refuses with exit 2 unless `--go <ref>` names a judge run id (`YYYYMMDD-HHMMSS-…`), a decision id (`D-<n>`), or an `OS-<n>` item **and** `--go-sha <sha>` equals `git rev-parse HEAD` of this checkout; each prints `GO: <ref> sha=<sha>` as its first line before any docker or token call, so a refused run touches nothing; each read-only mode (`-DryRun` / `--dry-run` / `--no-load` / no `--apply`) is unchanged and a `--go` there is echoed only. The Python tools share one implementation in `infra/mcp-servers/scripts/_go_gate.py` (`add_go_args` + `enforce`) so the rule cannot drift between them, exactly the same shape `apply-cluster.sh` uses inline; `compact-graphs.ps1` inlines the same rule as its PowerShell siblings `apply.ps1` and `apply-capability-overrides.ps1` already do. The local-checkout compiled-chunk patchers and the build-time `apply-vertex-patch.py` Docker stage stay ungated — the previous entry's reasoning holds: they edit a named local file tree, not the gateway over the network, already carry `-DryRun`/`--check` plus timestamped backups, and gating `apply-vertex-patch.py` would break the tested `vertex-patch` Docker build-stage contract (`tests/linux/34-ai-services.sh:4590`). Tests: 10 new bash cases (`D-825: dedup-graph …`, `populate-embeddings …`, `split-project-graph …`, plus a pwsh smoke case for the compact-graphs gate) in `tests/linux/40-omnigraph-images.sh` and 6 new pwsh cases (`compact-graphs D-825 GO gate`) in `tests/run-tests.ps1`; each case drives the real script against no stack at all, so a refusal never reaches docker and the whole battery is CI-safe. No push (commit in clone).
- AO-APPLY-GO-REF (2026-10-09, lane `apply-go-ref`, headless): **a live gateway change now runs only on an explicit judge GO (fleet rule D-825)** — 2026-10-09 06:27Z an `apply.sh` real run began before any GO existed and stopped only because `api-keys.yml` was missing. Any mutating `configuration/omniroute/apply.sh` run (not `--dry-run`, not `--drift`; `--probe` included) now exits 2 unless `--go <ref>` (a judge run id `YYYYMMDD-HHMMSS-…`, a decision id `D-<n>`, or an `OS-<n>` item) and `--go-sha <sha>` equal to `git rev-parse HEAD` of the checkout are both given, so the GO names the exact code applied; the gate prints `GO: <ref> sha=<sha>` as its first line and `--dry-run`/`--drift` are unchanged (a `--go` on a read-only run is echoed only). The same gate is on the Windows twin `configuration/omniroute/apply.ps1` (`-Go`/`-GoSha`), the standalone live-PATCH twin `configuration/omniroute/apply-capability-overrides.ps1`, and `infra/mcp-servers/scripts/apply-cluster.sh` (a live omnigraph converge with no read-only mode, which now refuses any bare invocation). The build-time source-patch/reapply tools (`patch-gateway-clamp.ps1`, `qwen-clamp-reapply.ps1`, `reason-fix-reapply.ps1`, `tools/apply-gateway-quota-patch.py`, `tools/apply-vertex-patch.py`, `tools/vertex-trailing-turn-reapply.ps1`) are left ungated — they edit a named local checkout's compiled chunks, not the gateway over the network, and already carry `-DryRun`/`--check` plus timestamped backups. Tests: 7 new bash cases (`D-825: …`) in `tests/linux/34-ai-services.sh` plus a `_go_flags`/`_is_readonly_run` helper that adds a valid GO to every mutating run the existing `_prune_apply`/`_node_apply` stand-ins issue (all 56 apply cases still green); 8 new pwsh cases (`omniroute apply D-825 GO gate`) in `tests/run-tests.ps1`; 4 new bash cases (`omnigraph cluster apply gate`) in `tests/linux/40-omnigraph-images.sh`. Usage docs updated (`configuration/README.md`, `docs/models.md`, `docs/verification.md`, the apply.sh/apply.ps1 headers, `add-project-graph.sh`). No push (commit in clone).
- AO-OGPIN-FIX (2026-10-09, lane lane-omnigraph-pin, headless): `infra/mcp-servers/servers/omnigraph-mcp/Dockerfile` pinned the bridge at `@modernrelay/omnigraph-mcp@0.8.1`, a version npm never published (only 0.8.0, 0.9.0, 0.10.0, 0.11.0, 0.13.0 exist), so `docker build` died with `ETARGET` before the image existed; the pin is `0.8.0` — the release server-L1 verified against `omnigraph-server` v0.8.1 with a healthy `/healthz` — matching `catalog/agent-harness.json`, `.mcp.json` and the `tests/linux/38-omnigraph-client.sh` stub, and the run-example comment's network moved `mcp-server_mcp-net` → `mcp-servers_default`, the network the deployed `omnigraph-server` actually sits on. No other bridge `0.8.1` pin remains anywhere (the remaining `0.8.1` hits are the server image, which is real); verify `npm view @modernrelay/omnigraph-mcp@0.8.0 version` → `0.8.0`.
- AO-RUN-FAMILY-RECORD-FIX2 (2026-10-09, lane `run-family`, headless adversarial-seat REJECT fixes): **three of D-807's claims were stronger than the evidence behind them, and each is now bounded by what a witness actually saw.** **(1) HIGH — the client default answers only for the model that IS the default.** `run_model_family()`'s fifth layer fired for any unqualified id on an own-account client, so `--model widget-9` on qoder recorded `qwen` — the family of the account's *default* model, read onto a model nothing ever served. It answers only where the id names that default: no model named at all, the default's own name, or one of the account's internal keys for it, now declared as `clients.CLIENT_DEFAULT_KEYS` (`qfmodel`, `efficient` — the spellings a qoder transcript and qodercli's own fallback line use for `Qwen3.8-Flash`, which no registry row carries). Anything else keeps the `null` + `family_reason` a provider-qualified id already got, and the cross-family check stays closed on it. **(2) HIGH — `vendor_family_name()` matches whole names, not pieces of them.** The table ran `if token in key`, which read `notsonnet` as Anthropic's and `mimosa-v1` as Xiaomi's, and read the registry's own `ollama-qwen2.5-coder` as `meta` because `llama` sits inside `ollama`. A spelling is now normalised (case, then every separator, then the `omniroute/`·`opencode/`·`agy-` prefixes and the `-free`/`:free` promo tail) and split at each letter↔digit boundary, and a token answers only where its own names stand together in that list, longest token first: `gpt-oss-120b` is openai-oss, `Qwen3.8-Flash` is qwen, `muse-spark-1.3-contributor-free` is meta, `claude-sonnet-5-5` is anthropic — and an id the table cannot place is None, which is what the docstring had always claimed (`nothing is lifted out of an arbitrary string`). Walking every registry model id, provider id, client name, route id and leg: one spelling changes family, that `ollama-*` one, and it changes to the vendor its own id names. **(3) MED — `planned-model` satisfies no gate.** A run that died before answering took its family from the launch line and recorded it under a *layer* name (`model-vendor-name`, `client-vendor-name`), which reads as a witnessed claim it never was. `FAMILY_SOURCE_PLANNED`, `witnessed_family()` and `family_is_planned()` are the one rule: `resolved_writer()` labels a family `planned-model` whenever no witness named the model, and the three records written from a plan — `_worker_record_start()`'s, job.json's `request`, exit.json's fallback — say so by construction. `writer_family_of_run()` returns None on it, so `--review-of` a run that never answered is refused on FAMILYFENCE-4 instead of fenced on a plan, and `cross_family_line()` prints no `yes` from it; the collision direction is unchanged — `fence_collision()` and the exit-12 backstop still read an unattested family that landed inside the fence, so the sentence and the exit code still agree (FAMILYFENCE-3 N5). A record that carried no `family_source` at all is not disqualified: silence predates the field, and the rule refuses only a claim the record itself calls the plan's. **This supersedes two D-807 assertions**: on a plan-derived record `family_source` is now `planned-model` where it named the layer (the `family` value and the footer's `writer: … (family)` are unchanged; `run_model_family()` still returns the layer to a caller that has a witnessed model). Tests: 8 new in `RunFamilyRecordTests` — `widget-9`/`NoSuchModel99`/`mimosa-v1` on qoder and `widget-9` on agy answer null while the default, `qfmodel`, `Efficient` and an unnamed model still answer their own layer; the substring refusals plus the whole-name negatives; an unwitnessed model's family is labelled `planned-model` while a reported one keeps `model-vendor-name`; the gate half, on a kill record (`writer_family_of_run` None, `family_fence` refusal, footer `unknown`) and on the records a keeper reads (`_worker_record_start`, `planned_family_fields`, `recorded_family_fields` re-labelling a layer it fell back to). Re-pinned 4 existing assertions that had pinned the plan's layer name (`ResolvedWriterTests`, `WriterProvenanceTests`, 2 in `RunFamilyRecordTests`). `python3 -m pytest -q tests/test_autoos_spawner.py -k "family or writer or review"` = **242 passed / 86 subtests**; `tests/test_seat_model_evidence.py tests/test_review_call.py` = **101 passed / 19 subtests**; `tests/test_autoos_spawner.py` whole file = **1327 passed / 6 skipped / 258 subtests**; `tests/test_autoos_resolver.py tests/test_t2_record_pin.py tests/test_agent_mcp_tool_profile.py` = **560 passed / 20 skipped**. Files: `tools/autoos-agent.py`, `tools/autoos_agent_mcp.py`, `tools/autoos_clients.py` (the alias table is the client's own account fact, so it lives beside `QODER_DEFAULT_MODEL`), `tests/test_autoos_spawner.py`. No push (commit in clone).
- AO-RUN-FAMILY-RECORD (2026-10-09, lane AO-RUN-FAMILY-RECORD, headless): **the run record carries a RESOLVED family, not the word `unresolved`** — every run footer printed `family: writer=unresolved reviewer=unresolved CROSS-FAMILY: unknown` beside a `writer:` line that named the served model (`qoder/Qwen3.8-Flash (qwen)`, `claude/claude-sonnet-5-5`, `qoder/qfmodel`), because the only lookup read registry model rows and a client's own id is not one, so the plangraph keeper that reads a reviewer family out of the record had nothing to read. `run_model_family()` is the one resolution both entry points share, ordered by how directly each names what ANSWERED: a registry row, the vendor the model id itself names (`FAMILY_VENDORS`, one normalisation table that keeps `gpt-oss` in the openai-oss family and never reads it as openai), a route whose legs all declare one family, the provider half, an own-account client's own default model, that client's name — and a provider-qualified id (`jetski/jetski-9`) never inherits the account's default, since the prefix says the model is not the account's business. Nothing naming a model answers `null`; a family is `family_source`-labelled and an unknowable one travels as `family: null` plus `family_reason`, so a cross-family check stays fail-closed instead of reading `unresolved` as a family. `resolved_writer()` records it (and says which half of the gateway log was unreadable), `_worker_record_start()` writes it into the `ps`/`status` record, `autoos_agent_mcp.spawn()` writes the spawn-time answer to job.json's `request` and `run_job()` to exit.json — the kill record's witness first, else the plan marked `family_source: planned-model`, because "what I meant to run" and "what answered" are claims of different strength — and an L2 lane's child resolves through the same path, so a nested sandbox's record is readable by the keeper too. `author_family`/`family_for_model` (the lane-record review seats) stay registry-only and closed: this resolved the run's model, it did not widen what counts as evidence of independence. Tests: `RunFamilyRecordTests` (per client — qoder default, qoder pin outside the account, opencode `--free`, opencode gateway combo, claude — the L2 child, unknown → null with a reason, the footer text, job.json/exit.json, and the vendor table). `python3 -m pytest -q tests/test_autoos_spawner.py -k "family or writer or review"` = 233 passed / 53 subtests; `tests/test_seat_model_evidence.py tests/test_review_call.py` = 101 passed / 19 subtests; `tests/test_t2_record_pin.py tests/test_agent_mcp_tool_profile.py` = 104 passed / 20 skipped. No push (commit in clone).
- AO-ADMISSION-2 (2026-10-09, lane AO-ADMISSION-2, headless): **unit tests no longer read live host state** — `tests/_host_state.py` is the one place that names the spawner's three host measurements (the run budget gate's spend report, the live worker count, `MemAvailable`) as throwaway fixtures through the knobs `tools/autoos-agent.py` already reads (`AUTOOS_DAILY_GATE_FILE`, `AUTOOS_WORKERS_DIR`, `AUTOOS_MEMINFO_PATH`, `AUTOOS_ADMISSION_OFF`), applied in `tests/test_autoos_spawner.py` (module pins + `pinned_env()` for the five `mock.patch.dict(..., clear=True)` dicts that were dropping them), `tests/test_t2_record_pin.py` (module pin) and `tests/test_run_budget_spawner.py` (`McpStartGateTests`: an empty state home and an empty records dir for the CLI child preflight execs), while `tests/test_seat_model_evidence.py`'s two real-sandbox cases write their evidence to a per-test temp dir and supply their own run record instead of reading the live `logs/workers`, whose 2026-10-01 record rotates out. **Tests only: no production gate, threshold or refusal changed.** This closed the 4 `prepush` overrides of 2026-10-08/09 — 15 host-dependent failures (6 in `GeminiSideModelPinTests`/`FreeConcurrencyCapTests`, 2 in `McpStartGateTests` — the unset-var case read today's real ledger and the fresh-block case answered with a memory refusal —, 6 across `D284GuardTests`/`D284NormalisedSpellingTests`/`D284PostPlanTests`, 1 `test_real_nemotron_db_three_way_match`) became 0 under a simulated busy host (`XDG_STATE_HOME` holding a blocking `daily-gate.json`, `host_admission.mem_available_floor_mb` raised above the host's free memory — reverted after the run), and `python3 -m pytest -q tests/test_autoos_spawner.py tests/test_run_budget_spawner.py tests/test_t2_record_pin.py tests/test_seat_model_evidence.py` = **1428 passed / 6 skipped / 0 failed** on this host as it stands (baseline before the fix: 1 failed). `HostAdmissionTests` is untouched — it is the admission rule itself, names both sources per case, and pops the escape.
- AO-DEADROWS (2026-10-08, lane AO-DEADROWS-writer) — three dead rows the operator's SCWREMOVAL and the Q7/D-654 reads left behind, each closed where it lived. **(1)** `configuration/omniroute/capability-overrides.json`: the last `overrides` row `scaleway/qwen3-235b-a22b-instruct-2507` (`max_output_tokens` 16384) is gone and the file is `[]` — SCWREMOVAL 2026-10-06 had already gated the provider and pulled its legs from every band, so the cap guarded a leg no combo can reach. The header's TARGET-SPELLING example moved to a live alias pair (`ovh` → `ovhcloud`) and the entry says what an operator must still do: delete the row on the gateway, because `apply-capability-overrides.ps1` only ensures rows and never removes one. No `scw/*` leg is left in `combos.json` or any route (checked by walking both files); `providers.scaleway` and its two model rows stay registered for pricing history, so `gateway_ref()`/`registry_ref()`'s `model_prefix` tests keep their worked example. **(2)** `catalog/ai-registry.json`: `navyai`, `bluesminds` and `arcee` already read `available:false` and list no leg in any combo or rendered route (verified by walking `combos.json` and every route's `legs`/`unavailable_legs`), and their `$comment` now carries the reason verbatim — **no free models (Q7, D-654)**. **(3) `spend=credit` is refused at card parse.** The resolver has no credit tier (`tools/autoos-agent.py` `TIERS` is 1/2/3 → `l1-orchestrator`/`l2-worker`/`t3-reviewer`) and no credit combo exists in `ALL_COMBOS`, so `select_combo` accepted the value only to return the same route it returns for `free-ok` — a card that asked for paid work and a plan line (`reason=public-credit`) that claimed something untrue. `CARD_VALUES["spend"]` is `("free-ok",)` now, `normalize()` raises `CardError("card spend='credit': expected one of free-ok")`, and the unreachable `spend == "credit"` branch in `select_combo` went with it. Contract text follows: the module docstring, the MCP `spawn` docstring, and the `unattended-orchestration` skill's card table and resolution table (the skill owns that table — it had documented the inert value). Tests: `tests/test_autoos_spawner.py` — the two `*_credit_*` rows of the resolution table are dropped (they pinned the same combo the `free-ok` rows above already pin) and every other `spend=credit` card became `free-ok`, including `test_mixing_v1_and_v2_fields_is_an_error`; `test_spend_values` re-pinned to `("free-ok",)`; new `RoutingBoundaryTests::test_spend_credit_is_refused_naming_its_values` asserts the refusal on bare/role/privacy cards, that the message names the offending value and the allowed one, and the same on the v1 path of `normalize_v2` through `parse_card`. Verify: `python3 tools/registry.py validate` ok (36 routes, 92 models, 55 providers); all five `render … --check` byte-identical (no provider `$comment` reaches a render); `pytest -q tests/test_registry.py tests/test_registry_render.py tests/test_autoos_resolver.py tests/test_sync_router_tiers_registry.py tests/test_agent_mcp_tool_profile.py` = 952 passed / 21 skipped; `pytest -q tests/test_autoos_spawner.py` = 1293 passed / 6 skipped / 217 subtests; `bash tests/run-tests.sh --filter catalog` = 71 green. Note: this lane's sandbox exports `AUTOOS_AGENT_LAYER=L3`, which fails ~230 spawn cases with the leaf-fence refusal before the diff is reached — every count above is with that name unset, where the suites are green.
- OVH-REPROBE-CITE (2026-10-09, lane lane-denylegs, headless): the OVH re-probe's own artifact is now committed at `configuration/omniroute/probes/probe-d657-ovh-20261009.tsv`, so the prose that said *"probe artifact not committed in this lane"* is no longer true and is replaced by a citation of that file — in `providers.ovhcloud.$comment` (registry source) and in the OVH-REPROBE entry below. **Comments and CHANGELOG only: no chain, leg, gate or model data changed.** What the TSV actually records, stated as it reads: `ovh/Qwen3.8-27B`, `ovh/Qwen3.5-397B-A17B` and `ovh/gpt-oss-120b` on gateway `central`, `tool` ok, `served` = the requested id, no error (ack_ms 578 / 649 / 800) — and the `ack` column carrying `empty`, which is not the `ok`/`fail` vocabulary the 10-04 TSVs use, so what the file records is a tool call and a served model rather than a non-empty plain-completion ack, and the citation says so. The file is **central-only** — the workstation gateway is unreachable from this host, so it has no workstation rows and no workstation verdict is claimed. The note that the committed 10-04 `probe-d657-{central,workstation}.tsv` rows (`ovh/Qwen3.8-27B` HTTP 404, no `Qwen3.5-397B` row) predate the re-probe and are stale is kept. **Left as a judgement for the next lane, not silently adopted:** the new TSV's `tool: ok` on `ovh/Qwen3.5-397B-A17B` is evidence against that model's tracked `tool_calls: "unproven"` row and the OVH-REPROBE-CAVEAT passages built on it — flipping a model fact is a routing change, out of this lane's scope, and one tool call is not the 3/3 round trip the other proven rows record. Renders re-checked rather than rewritten: a fresh `render --out` is **structurally identical** to the committed `combos.json`, `ide-models.json` and `tier-profiles.json` (a `$comment` only, and `docs/models.md`'s generated section), so nothing was overwritten — the re-render caution in the entry below (a generator `--out` replaces those files' 154- and 70-line `$comment` **arrays** with one string, and `--check` ignores `$comment`) is exactly why. Gates: `tools/combo-contract.py --strict-d657` rc 0 (PASS, judged 27 of 27), `tools/sync-ide-models.py --check` rc 0, all five `registry.py render --check` ok, `registry.py validate`/`check` ok (45 routes / 94 models / 55 providers), `pytest tests/test_registry.py tests/test_registry_render.py tests/test_combo_contract.py` **588 green**. No full suite run, per brief. Not pushed.
- FIX-CI-ROUND2 (2026-10-08, lane lane-denylegs, this checkout — headless): the three checks `lane/denylegs` shipped red and `main` passes are closed. Each was a half-applied change, not a flaky test, and nothing was weakened to hide one. **(1) `the router declarations do not drift from each other`** (`tests/linux/33-documentation.sh`, `tools/audit-router.py --offline` rc 1 with four DRIFT lines naming `l1-orchestrator-paid` and `l3-review-transcript`): the audit's rule is "anything catalog/ide-models.json offers through omniroute must be a combo", and both routes are `legs: []` now — §8 emptied the first, §3's II ≥ 44 bar left the second born empty — while `render_ide()` still handed both to opencode and Zed. Fixed in the renderer rather than per id: **NOLEGS-OFFER** withdraws a legless route's *gateway* membership (the registry keeps its `surfaces.omniroute` block as the route's own record of window and display name) and gives no row at all to a route no gateway offers — the treatment OR1d already gives a legged route that serves none. `auto/*` keep their membership: they resolve inside OmniRoute with no combo and are the audit's own exemption. `l1-orchestrator-paid` is now offered through LiteLLM only, the shape `l2-worker-paid`/`l3-driver-paid` already have, and `l3-review-transcript` leaves the picker, where it was never routable — the lens is carried by `policy.reviewers`' qoder row. Pinned by `ALeglessRouteIsOfferedThroughNoComboTests` (4 cases, including the control that a leg earns the membership back; verified red by disabling the rule and re-running) so the next legless route cannot re-open it. Re-rendered `catalog/ide-models.json` and `opencode.jsonc` (`sync-ide-models.py`); `combos.json`, `tier-profiles.json` and `docs/models.md` are unchanged by the rule and every `render --check` stayed green. **(2) `litellm fallback config is internally consistent`** (`tests/linux/17-ai-routing.sh`, `expected [] but got [l1-orchestrator-paid]`): the test's own rule — a legless `*-paid` route is a hand-curated LiteLLM escalation chain and must carry a group — is the intent D657-CHAIN recorded ("it is LiteLLM-only again") and left unwritten, while `config.yaml`'s header still prescribed the chain ("meta_api leads l1-orchestrator-paid as the pattern"). Wrote it as a hand group with no `AUTOOS-MANAGED` markers (`sync-router-tiers.py` refuses to invent a legless block): `openai/muse-spark-1.3-contributor` at `api_base https://api.meta.ai/v1`, then `deepseek/deepseek-flash` — the two direct legs its managed block rendered before §8, the same shape as both siblings, `router_settings.fallbacks` still empty so nothing escalates silently. Corrected the two header passages that still called it a managed group with real legs, and the one that called `spark-1.3-contributor` one (it declares legs and serves none, so it has no group). **(3) the t1-credit R5 / C4 / T2-C4 precondition** (`tests/test_t1_credit_fix7.py`, 3 failures): each needed an implement card to *skip* a vertex leg for `tool_calls`, which the shipped registry used to supply for free; D-657 probed `vertex/gemini-3.8-flash` with a real tool call and recorded it `proven`, so the **fact** moved and the behaviour under test (a skipped leg must never print a kept-claiming line) did not. The skip is driven through `tools/probe-toolcalls.py`'s own overlay shape (`_vertex_tool_calls_unproven`, which `_tool_calls_value` reads ahead of the registry verdict) and every assertion, the `"tool_calls"` reason text included, is unchanged. Gates (the three checks and their neighbours, no full suite this round): `bash tests/run-tests.sh --filter "litellm,router,IDE model,combo,sync-"` **69 passed / 0 failed**; `tools/combo-contract.py --strict-d657` rc 0 (27 combos, judged 27); `tools/sync-ide-models.py --check`, `tools/audit-router.py --offline` ("no drift") and `tools/sync-router-tiers.py --check` (the hand block survives the sync) all rc 0; `registry.py validate`/`check` ok (45 routes / 94 models / 55 providers) and all five `render --check` ok; `pytest tests/test_registry.py tests/test_registry_render.py tests/test_combo_contract.py tests/test_t1_credit_fix7.py` **644 green**, the six other `test_t1_credit_fix*` files 176 green, `test_autoos_resolver.py`/`test_render_opencode_config.py`/`test_fleet_sandbox_agents.py` 380 green, `test_sync_ide_models.py`/`test_registry_loader.py`/`test_audit_router_registry.py` green. Not pushed.

- OVH-REPROBE + DENOISE (2026-10-08, lane AO-DENYLEGS D2 open items 1–4 — headless, self-judged: a pinned cross-family review is still owed, R-orch-13): merged `origin/main` (one conflict, `tests/test_registry.py`, resolved by keeping both sides — the PRICE-GAP provider-spelling normaliser and main's OVHCODER-DROP retire test). **1. The OVH legs answer 200 again** — `gpt-oss-120b`, `Qwen3.8-27B`, `Qwen3.5-397B-A17B` re-probed on 2026-10-08 (provenance: the re-probe was announced as HTTP 200 on 2026-10-08 by judge brief D-716 (autoos-L1-main) and its own artifact is now committed at `configuration/omniroute/probes/probe-d657-ovh-20261009.tsv` — central gateway only, the workstation gateway being unreachable from the host that ran it, so that file carries no workstation rows; the older committed `configuration/omniroute/probes/probe-d657-*.tsv` (10-04) — `ovh/Qwen3.8-27B` HTTP 404, no `Qwen3.5-397B` row — predate the re-probe and are stale, so no 200 or tool-call verdict is read into them) — so `providers.ovhcloud.available` is back to `true`, and exactly the two ids that are priced ride **L3 only**: `ovhcloud/Qwen3.8-27B` in `l3-implementer` (its `tool_calls` are proven) and `ovhcloud/Qwen3.5-397B-A17B` in `l3-driver`, each at `legs[-2]` — head of the credit band, immediately before `vertex/gemini-3.8-flash`. `ovhcloud/Qwen3.5-397B-A17B`'s row is `tool_calls: unproven` in the tracked data — the re-probe confirmed an ack and a price, not a tool call — so an agentic card falls through that leg exactly as FREEKEYS-1b describes for the unproven gpt-oss row, and the caveat is written into `routes.l3-driver.$comment` instead of being papered over by a probe this lane cannot run. "Trial position" is written as that seat, not as a tier: the schema has no `trial` provider/model tier and rule (b) forbids a free leg behind a credit one. It buys the two constraints §8 asked for by construction — one OVH leg per chain, so never consecutive with another (rule (g)), and nothing OVH in any `l0-`/`l1-`/`l2-` chain or any `-clean` route (OVH does not cache: CACHEORCH). `ovh-gpt-oss-120b` re-probed 200 too but is **unpriced** — `ovhcloud/gpt-oss-120b` resolves to the shared `models.gpt-oss-120b` row whose `price_in`/`price_out` are 0.0 with no `price_source`, and that row is also `tool_calls: unproven` in the tracked data (FREEKEYS-1b) — so rule (h) fails it closed and it sits in its declared seat behind an `unavailable_legs` gate whose comment says *unpriced*, not *dead*; `Qwen3-Coder-30B` stays retired (gate reason: withdrawn upstream, "does not exist"). Renders re-cut and re-checked: `combos.json` 26 → **27 combos, 12 omitted** (`ovh-qwen3.8-27b` joins, the two gated seats go to `omitted`), a hand-inserted `AUTOOS-MANAGED` block for `ovh-qwen3.8-27b` in `configuration/litellm/config.yaml` (the runner refuses to invent a block) then `sync-router-tiers.py` green, `catalog/ide-models.json` + `configuration/openhands/tier-profiles.json` + `config.toml` show **131072 → 128000** for `l3-driver`/`l3-implementer` — `clamp_route_context` obeying the new OVH leg's advertised window, while the display label stays "128k" so rule (d) is untouched. **2. The transcript lens** (`l3-review-transcript`, §3's IV ≥ 44): it stays `legs: []` and out of `policy.d657_combos` because it renders no combo and `tests/test_combo_contract.py:275` asserts the judged set equals the rendered set — so the lens is carried as data instead: `policy.reviewers` (10 → 11 rows) gained a `{"client": "qoder", "model": "Qwen3.8-Max", "family": "qwen", "paid": true}` row with **no `leg`**, the shape the schema allows for a spawner-only client, reached on `reviewer_for`'s last-resort pass. New `ReviewLensRouteTests` pins all four §3 lenses as declared cheap routes, the three servable ones leg-checked, and the transcript lens legless + gate-free + named by that carrier row; the credit-gate test was rewritten to read every gated leg's stated reason (`price` / `does not exist` / `withdraw` / `retire`) instead of hardcoding which OVH id is dead, with a mutation control that blanks the comments and expects the check to go red. **3. The `task_type=ops` writer rule is refused, not implemented** — ADR 0007 (`docs/decisions/0007-…-needs-a-task-type-and-a-capability-scale.md`) records the six facts that make it unexpressible today (`_CARD_FIELDS = ("spec", "kind")`, S0–S4 *size* buckets, `risk` = diff-derived `normal|high`, no capability figure on `models.<id>`, no `policy.writers`, and no `leg` allowed on a spawner-client row) and the sized follow-up. **4. The L2 seat takes a ≥ 600k route** (the gap the entry below filed): `opencode.jsonc`'s `l2-worker` agent — what `TIERS[2]` names — now runs `omniroute/l2-orchestrator` (clamp 1048576) instead of `omniroute/l2-worker` (262144, and its "128k" label is pinned by rule (d), so the combo could not be raised); `--clean` follows to `l2-orchestrator-clean`, both already declared, no permission changed. Pinned by `TierSeatContextTests` (red first at 262144 < 600000), which reads the window off the registry's own `route_context_cap` rather than combos.json's label, checks the `-clean` twin, pins that the seat names no route the contract clamps, and keeps a control asserting `l2-worker` still falls short. **Left open, in the config comment too:** a card-driven run's model comes from the resolver (`resolve_model(…, override or "omniroute/" + combo)`), so an implement card still lands on the 128k `l2-worker` — moving the routing table's L2 band needs an `l2-orchestrator-free-only` route that does not exist, which §8 assigns to the AO-LENS-REVIEW wiring; and the seat loses its third leg (openrouter nemotron `:free`), so it fails over to vertex credits one step sooner. `unattended-orchestration`'s tier table and depth note now name `omniroute/l2-orchestrator` for the `l2-worker` seat, so the skill stops describing a config it no longer matches. Gates: `registry.py validate`/`check` ok (46 routes / 94 models / 55 providers), all five `render --check` + `sync-router-tiers.py --check` + `sync-ide-models.py --check` green, `python3 tools/combo-contract.py --strict-d657` **PASS, judged 27 of 27** (was 26), `test_registry.py` 388 / `test_registry_render.py` 170 / `test_combo_contract.py` 25 / `test_autoos_resolver.py` 312 / `test_review_call.py` 56 / `test_sync_ide_models.py` 36 / `test_fleet_sandbox_agents.py` 19 / `test_render_opencode_config.py` 6 all OK, `test_autoos_spawner.py` 1286 with 3 failures and 2 skips — all of them host admission under this sandbox's own memory pressure (`MemAvailable 4709–4717 MB against the registry's 6144 MB floor`) and the `systemd-run --user` end-to-end spawn, the same three this file already records at the branch point, none of them reading a route. Re-render caution for the next lane: `render --out` replaces the historical `$comment` **arrays** in `combos.json` (148 lines) and `tier-profiles.json` (70) with the generator's one-line string and `--check` ignores `$comment`, so the prose is lost silently — both were restored by hand here.
- D657-CHAINS (2026-10-08, lane AO-DENYLEGS D2 — headless, self-judged: a pinned cross-family review is still owed on the gate-adjacent claims below, R-orch-13): every judged chain was re-cut from `configuration/omniroute/probes/probe-d657-central.tsv` + `-workstation.tsv`, where a leg serves only if it answered **ack 200 and one tool call** on at least one gateway. The L1/L2 orchestrator chains are `bzl/deepseek/deepseek-v4-flash-0731free:free` → `vertex/gemini-3.8-flash`: the cached ∩ ack pool for L0–L2 is exactly those two free legs plus the vertex credit leg, and contract rule (b) (free before credit) moves vertex off the head spec combo-v2 §7b put there — reported as a delta, not silently reordered. The bans that decided the rest: `providers.ovhcloud.available: false` (six `ovh`/`ovhcloud` spellings × both gateways answered 404 "no Route matched", and OVH served 1,253 calls OK the week before, so this is a recent break, not a stale gate) — which empties the three `ovh-*` singles into `omitted` and takes every ovh leg out of every render; §8's paid `deepseek/deepseek-flash` and paid meta-api `muse` tails; §7b's `qoder_ai` legs (spawner client only). `l1-orchestrator-paid` (legs `[]`, like the other `*-paid` routes), `opus-5-5`, `spark-1.3-contributor` and `deepseek-v4.1-flash` now declare legs with nothing servable behind them. New routes: the §2 layer twins `l3-implementer`/`l3-implementer-clean` and `l3-researcher`/`l3-researcher-clean`, and the four §3 lenses `l3-review-diff` (Qwen → DeepSeek → Poolside), `-tests`, `-codebase` (NVIDIA → Google, inkling 403s so nemotron-ultra heads it), `-transcript`. **Rule (b-or) × spec 3.1 rule 3 = the clean band is one leg**: `private_safe()` admits only a non-training `paid|subscription|credit` leg while (b-or) forces every servable openrouter leg to end `:free`, so after the ack gate exactly one gateway leg qualifies — `vertex/gemini-3.8-flash` (credit, `trains_on_prompts: false`, cache `documented`) — and every `*-clean` combo is that single leg with **no fallback at all**; the free non-training-looking legs (bazaarlink, groq, the four openrouter `:free`, cohere) are all refused by the PRIV/PRIV2 free-pool rule. `l3-review-transcript` is `legs: []` on purpose rather than invented: §3 sets II ≥ 44 and all four of its candidates left (both qoder legs per §7b, the muse contributor is a non-`:free` openrouter leg whose free oc/opencode-zen spellings 403, vertex claude 501s). Leg facts added: `models.'nvidia/nemotron-3-ultra-550b-a55b:free'` and `models.'command-a-plus-05-2026'` rows with `prompt_cache` from the TSV, `tool_calls: "proven"` on `command-a-03-2025`/`command-r-plus-08-2024`/`command-a-plus-05-2026`, and contexts taken from the registry's own sibling rows — **no gateway key is present in the sandbox, so the two new context numbers are sourced defaults, not measurements**, and §2's "436k" AA claim for command-a-plus was deliberately not adopted. `policy.leg_rules` gains `allow-bazaarlink-ds-v4-flash-0731-free`, a per-leg allow placed above the blanket `deny-deepseek`: D-657 supersedes FREEKEYS-2's "routes nowhere" verdict for the **free** spelling only, `deny-deepseek-pro` stays first so V4 Pro remains unreachable. **D657-EFFORT (the head swap's real cost):** moving vertex off the head takes `l1-orchestrator`'s `low`/`medium`/`high` effort variants with it — `render_ide()` reads the ladder off the **served head leg**, and `deepseek/deepseek-v4-flash-0731free:free` documents `effort_ladder: []`. The rung is dropped rather than invented (models.md's own rule; DSAMEND measured DeepSeek's accepted set against `api.deepseek.com`, not against this mirror), and `surfaces.{litellm,omniroute}.effort_default: "xhigh"` is removed from the route with it: with an empty head ladder the renderer forwards `effort_default` unconditionally, which would have put an unproven `reasoning_effort` on every L1 request. The hand pin in `opencode.jsonc` therefore goes from `omniroute/l1-orchestrator#high` to the bare combo — opencode answers `Variant unavailable` for a rung no variant declares, the failure it recorded for `#xhigh` before PROVFIX — and `docs/models.md`'s per-route note is corrected the same way (`docs/handoff.md:9` still names `#high` but is a dated 2026-09-24 snapshot, left as the record it is). The clean band keeps the ladder: `l1-orchestrator-clean` and `l2-orchestrator-clean` head `vertex/gemini-3.8-flash`. Restoring the rung needs a probe of effort levels on this host, then `models.'deepseek/deepseek-v4-flash-0731free:free'.effort_ladder`. Pipeline: `render_omniroute()` now projects `policy.d657_combos` (21 ids) into combos.json's `d657_combos`, so the (f)–(i) opt-in is registry data instead of a hand edit to a generated file, and `IDE_MODEL_ORDER` names the nine new routes. Rendered and re-checked green: `configuration/omniroute/combos.json` (26 combos, 13 omitted), `catalog/ide-models.json` (OR1d drops the six dead ids), `configuration/openhands/tier-profiles.json` (18 tiers; `omniroute-l1-orchestrator-clean` joins, four tiers whose routes serve nothing leave), `configuration/litellm/config.yaml` (five marker pairs inserted by hand because `sync-router-tiers.py` refuses to invent a block, then synced; eight blocks pruned — the three `ovh-*`, `l2-worker-clean`/`l3-driver-clean` whose only leg carries declared auth, `spark-1.3-contributor`, `deepseek-v4.1-flash`, `gemini-3.8-flash`), `opencode.jsonc` + `configuration/openhands/config.toml` via `sync-ide-models.py`, and `docs/models.md`. Gates: `python3 tools/combo-contract.py` rc 0 (judged 21 of 26) and `python3 tools/combo-contract.py --strict-d657` rc 0 (judged 26 of 26). **Gap, not fixed:** spawner `TIERS[2]` stays `l2-worker`, which rule (d) pins to the 128k label, so the tier-2 seat's ctx is still <600k — the contract-passing 1M L2 chain is `l2-orchestrator` and is reachable today only as an explicit `run --tier 2 --model omniroute/l2-orchestrator`; making it the default needs a new `l2-orchestrator` agent in `opencode.jsonc` and the `l1-orchestrator` subagent permission that names `l2-worker`, i.e. the spawner wiring the §8 AO-LENS-REVIEW lane owns. Also stale and left for the operator to judge: `tools/autoos_routing.py:select_combo()` still refuses `privacy=sensitive` + `ctx=1m` with the 2026-09-27 reason ("the only 1M leg was muse-spark-1.3-contributor"), while `l1-orchestrator-clean`/`l2-orchestrator-clean` now serve 1M on the vertex credit leg — a privacy-band change, so not this lane's call.
- L2SPAWN-TIER (2026-10-08, lane L2GATES-tier, found by AO-L2-PRODTEST's live run; failing tests first). `tools/autoos_agent_mcp.py`: an L2 could never start a writer. **(1)** `L2_SPAWN_TIER` 3 → 2 — criterion 3's rationale ("below tier 3 is a session that holds its own editor and a checkout of the main tree") is false of an isolated spawn, and tier 3 is the review-only seat (T2-RECORD-PIN), which refuses an `implement` card: a tier-3-only lane had no write path at all. `l2_spawn_refusal` now answers None for tier 2 and 3 and still refuses tier 1 (the L1's own combo/seat), any `role: orchestrate` card whatever tier it routes to, the `claude` client and every Claude model pin; the refusal text and the helper docstring say what the fence actually is. **(2)** profile `l2` FORCES `--isolate` on every spawn it lets through, whatever the caller passed (`isolate=False` is overridden, not honoured and not refused; `route.l2_forced_isolate` names the leg, `route.forced_isolate` stays the shared marker), which is what makes an L2's tier-2 worker safe and does not depend on `ISOLATE_TIERS` or `NO_ISOLATE_CLIENTS` continuing to say so. **(3)** the L2's own contract text follows the gate: the `spawn` tool docstring, the module header, `tools/oc_l2.py` (module docstring and the `ROLE_LINES` footer — it told the lane to spawn tier-3 only, so the bug survived in the prompt as well as the code) and `docs/ai/opencode-fleet.md` (the layer-fence bullet and the first-prompt bullet). Tests: `tests/test_agent_mcp_tool_profile.py` +3 (46→49) — the `L2_SPAWN_TIER == 2` pin, a `role=implement,complexity=standard` card (resolves tier 2) allowed AND carrying `--isolate`, explicit tier 2 allowed, tier 1 (int and text) refused, orchestrate refused, `isolate=False` forced at every allowed shape with no `l2_forced_isolate` marker outside profile l2, the claude client / pinned Claude model refusals unchanged; `test_the_helper_reads_the_profile_and_the_resolved_tier` and `test_an_explicit_tier_below_three_is_refused` / `test_a_card_below_three_is_refused_by_its_resolved_tier` were the pins that encoded the bug and are rewritten. Verify set — the brief's two suites plus every lane/guard suite that re-runs with them, in one `python3 -m unittest` run: `tests.test_oc_l1_serve`(45) `tests.test_oc_l1_status`(11) `tests.test_agent_mcp_tool_profile`(49) `tests.test_skill_rules`(26) `tests.test_oc_l1`(18) `tests.test_oc_l1_render`(19) `tests.test_oc_l2`(47) `tests.test_opencode_bash_guard`(82) `tests.test_oc_l1_canary`(42) = 339 green (19 skips, all in the profile suite's subprocess/spawn cases); `AUTOOS_ADMISSION_OFF=1 pytest tests/test_autoos_spawner.py -k "l2 or L2 or isolate"` = 135 + 44 subtests green; `node --check configuration/opencode/plugins/bash-guard/index.mjs` clean. The whole `tests/test_autoos_spawner.py` was NOT run to completion (the run was stopped once the `-k` set passed, so the host is not held by two lanes at once) — the diff reaches only the `AUTOOS_AGENT_LAYER=L2` legs, which that file never sets. **(4) SEAT REJECT of (1)–(3) — L2SPAWN-TIER-LEAF (2026-10-08, same lane, adversarial REJECT; failing tests first).** **(4.1, HIGH)** The tier-2 door (1) opened had no wall behind it: `WORKER_ENV_AUTOOS` never carried `AUTOOS_AGENT_LAYER`, so a writer an L2 spawned came up on the FULL MCP profile — the profile selection keys on the child's own env, and that env said nothing — and with `DEFAULT_MAX_DEPTH = 2` (`tools/autoos_clients.py`) a grandchild that resolves tier 1 fitted inside the budget, which is exactly the L1's seat criterion 3 was written to protect. The layer a child runs at is now the **spawner's** decision, not the caller's: `tools/autoos-agent.py` gained `agent_layer` / `child_agent_layer` / `leaf_spawn_refusal` and `worker_env` stamps `AUTOOS_AGENT_LAYER=L3` whenever the spawner's own env reads `L2` **or** `L3` (a leaf's child is a leaf too) and POPS the name when the spawner is unmarked, applied after the plan merge exactly like the `SHELL` pin — so neither an inherited value nor a `plan["env"]` entry nor an `AUTOOS_AGENT_LAYER=L1`/empty the caller passes can relabel a child; the name was added to `WORKER_ENV_AUTOOS` so the stamp reaches the child at all. `spawner_child_env` copies the lane's own mark verbatim and LAST, after `extra`, because there the child is the CLI process that will do the stamping and re-read these gates — handing it the leaf mark would refuse the very spawn the profile had just allowed. **(4.2)** a leaf never spawns at all (skill rule R-worker-06): `cmd_run`'s FIRST statement returns `refuse(leaf, EXIT_LAYER_FENCE)` — new **rc 14**, documented in the module header, deliberately not rc 2 — before any spend, admission, preflight or preview work, and `build_argv` raises the same text at its head so no `spawn` call under L3 can reach a subprocess at any tier, any card, or even a dry run; `LEAF_PROFILE = "l3"` registers the spawner tools **minus `spawn`** and `mcp_tool_profile` selects it from `agent.agent_layer()`, so a leaf is not even offered the tool, and `lane_control_fence` refuses the lane-control tools at L3 as well as L2. The **depth budget was already enforced** (`child_depth` / `DepthError`, rc 4 at both the spawn and inside `build_argv`) — pinned by a test, not changed. **(4.3, MED/LOW)** in the same `l2_spawn_refusal`: `int(run_tier) >= L2_SPAWN_TIER` read "at least 2" and let **tier 4 and tier 99** through as workers — now `tier in L2_SPAWN_TIERS == (2, 3)`, and the refusal prints the request as it arrived (`" 2"` is normalised, `"banana"` is not a tier). And `role` was compared exactly, so `Orchestrate`, `ORCHESTRATE` and `" orchestrate"` walked past the L1's-own-seat guard into an allow — now `.strip().casefold()` before the compare. The gate lives ONCE: `l2_spawn_refusal` / `leaf_spawn_refusal` / `L2_SPAWN_TIERS` are defined in `tools/autoos-agent.py` and aliased by `tools/autoos_agent_mcp.py`, so a lane that reaches `autoos-agent.py run` through its read-only shell gets the same answer as its MCP, and the CLI's route tier is the same `_tier_for_route(combo)` the MCP resolves (pinned by a card dry-run). Contract text follows the code: the `spawn` docstring (new LEAF paragraph), the MCP module header, `tools/oc_l2.py` (module docstring leg (c) plus the `ROLE_LINES` footer — it told the lane what its children may not do) and `docs/ai/opencode-fleet.md`'s layer-fence bullet. Tests: `tests/test_agent_mcp_tool_profile.py` +12 (49→61) — the `L2_SPAWN_TIERS == (2, 3)` pin, tier 4 / 99 / `" 2"` refused, `Orchestrate` / `ORCHESTRATE` / `" orchestrate"` refused, the depth-budget refusal, `l3` = the `l2` menu minus `spawn`, `test_the_l3_marker_selects_the_leaf_profile`, `test_an_l3_environment_serves_the_leaf_profile` and `LeafSpawnGateTest` (5, no `mcp` needed so no skip guard — `build_argv`/`spawn` refuse at every shape and name the rule, an unmarked/`L1`/`L2` spawn is not refused as a leaf, and no spelling of the marker — `l3`, `" L3 "`, `"\tl3"` — escapes the profile); `tests/test_autoos_spawner.py` +15 (`AgentLayerFenceTests`) — the child env carries `AUTOOS_AGENT_LAYER=L3`, an unmarked spawner pops the name, a caller-supplied value cannot downgrade the child, `spawner_child_env` copies the lane's mark and ignores `extra`, the CLI refuses under L3 with **rc 14** and the rule on stderr, the L2 CLI refuses a resolved tier 1 exactly as its MCP does; `tests/test_oc_l2.py` +1 (47→48) — lane control refused from inside a leaf too, with a spy that raises so the launcher subprocess is provably never reached. Verify: `python3 -m unittest tests.test_agent_mcp_tool_profile tests.test_oc_l2` = 109 green (20 skips, all in the two pre-existing `mcp`-importing classes) and the same profile suite in a `uv` venv carrying `mcp<2` = 61 green **0 skips** — a skip is not a pass, so registration was proved where the package exists. `tests.test_oc_l1_serve`(45) `test_oc_l1_status`(11) `test_agent_mcp_tool_profile`(61) `test_skill_rules`(26) `test_oc_l1`(18) `test_oc_l1_render`(19) `test_oc_l2`(48) `test_opencode_bash_guard`(82) `test_oc_l1_canary`(42) `test_agent_harness`(72) = 424 green. `AUTOOS_ADMISSION_OFF=1 pytest -q tests/test_autoos_spawner.py -k "l2 or L2 or isolate or layer or depth or leaf or env"` = 221 passed + 44 subtests, 1 failed: `GeminiSideModelPinTests::test_an_explicit_GEMINI_MODEL_in_the_spawner_env_does_not_reach_the_child`, "daily budget blocked: day total $20.89 exceeds budget $14.00" — host spend, pre-existing: the identical `-k` subset on a clone of the base commit `be87a3b` gives 1 failed / 206 passed. `python3 -m py_compile` clean on the three touched modules.
- L2SECRETS (2026-10-08, lane AO-L2-LAUNCH, judge follow-ups to L2LAUNCH-live-fix10 / L2GATES-r4, failing tests first). **(1)** `tools/oc_l1_serve.py` applied the gateway base-URL default with `dict.setdefault`, which answers only a **missing** name — a launcher that exported `AUTOOS_OMNIROUTE_URL=` (a shell that sourced a template, a CI job that declares every var it mentions) handed the lane the same empty base URL as one that never exported it, and opencode died again in `LLM.compile` with `TypeError: Invalid URL`; the rule is now BLANK → default: an empty and a whitespace-only value both fall back to `oc_l1.DEFAULT_BASE_URL`, an explicit value still wins, the key still gets nothing invented for it. **(2)** criterion e proved rather than asserted: the L2 lane's environment must CARRY `AUTOOS_OMNIROUTE_KEY` (opencode expands the rendered config's `{env:…}` inside the child) while the lane's own shell reads nothing that holds a credential — ten probes under `AUTOOS_GUARD_ROLE=l2` (`printenv`, `printenv AUTOOS_OMNIROUTE_KEY`, `env`, `cat /proc/self/environ`, `cat /proc/$PPID/environ`, `cat /proc/1234/environ`, `rg KEY /proc/self/environ`, `echo $AUTOOS_OMNIROUTE_KEY`, `ls ${AUTOOS_OMNIROUTE_KEY}`, `git log --format=$AUTOOS_OMNIROUTE_KEY`) are all DENIED, each by the rule that owns its spelling (the env-dump heads, the live-process-entry rule, the path allow rule, the closed head list). **SEAT REJECT of that clause — L2SECRETS fix 10 (2026-10-08): those ten probes were the wrong ten.** `l2OptionOperandReason` (index 1261) path-checked a glued short-option value (`-XVALUE`) only for rg's file-opening flags, so every other glued value went unexamined and six spellings were ALLOWED: `rg -r$AUTOOS_OMNIROUTE_KEY foo README.md`, `rg -r${OPENCODE_SERVER_PASSWORD} foo README.md`, `head -c$AUTOOS_OMNIROUTE_KEY README.md`, `tail -n$AUTOOS_OMNIROUTE_KEY README.md`, `rg -m$AUTOOS_OMNIROUTE_KEY foo README.md`, `git log -G$AUTOOS_OMNIROUTE_KEY` — the key printed through a read-only head, because the operand rules read a token as a PATH and an option's value is not one. The `l2` gate is lexical before it is per-word: `l2ExpansionDenialReason` refuses any command whose text carries a `$`, under any head and in any position, and names the offending word (`$KEY`, `${KEY}`, `$((1))`, `$(cmd)`), because the child's own environment holds the gateway key and the server password and the shell expands it before the head ever runs; no single-quoted exemption — `tokenizeSegment` flattens the quotes away, so nothing downstream can be proved on a quote context that no longer exists (and the operand rules already denied `'$HOME'`), and `%` needs no rule because the shell does not expand it. Glued values are now path-checked for every head, which is what denies `head -n~/lane.key` and `rg -m~/x`. `orchestrator` and the unroled path are untouched: both checks live in the `l2` gate only, and the reads the lane still needs are pinned beside them (`head -n5`, `tail -c20`, `rg -m10 foo`, `rg -e foo`, `head -c-1`, `git log -Gfoo`). The child env is audited by NAME on a real spawned L2 lane: exactly two credential-shaped names travel — `AUTOOS_OMNIROUTE_KEY` and `OPENCODE_SERVER_PASSWORD` — with `GH_TOKEN`, `AWS_SECRET_ACCESS_KEY`, `LANE_DB_PASSWORD`, `OMNIGRAPH_API_KEY` and `OPENAI_APIKEY` held back, which is why `oc_l1.env_is_credential_name` runs before the `OMNIGRAPH_`/`AUTOOS_` prefix allowance. The canary probe set stays one probe (D-665: a denial proves the guard runs; enumerating reads is the test suite's job). Tests: `tests/test_oc_l1_serve.py` +3 (42→45), `tests/test_oc_l2.py` +1 (46→47), `tests/test_opencode_bash_guard.py` +1 method / 10 commands (80→81), fix 10 +1 method / 22 commands (81→82) — the six ALLOWED spellings above plus `-r"$X"`, `--replace=$X`, `-e$X`, `-A$((1))` and the two `~` glued values, with the six surviving reads asserted in the same method; three probes that used to be refused by the path rule (`cat /proc/$PPID/environ`, `ls ${AUTOOS_OMNIROUTE_KEY}`, `git log --format=$AUTOOS_OMNIROUTE_KEY`, and `echo $AUTOOS_OMNIROUTE_KEY` that used to fall to the head list) now name the expansion rule, which is the rule that owns them. Verify set `tests.test_oc_l2`(47) `tests.test_oc_l1_canary`(42) `tests.test_oc_l1`(18) `tests.test_oc_l1_render`(19) `tests.test_agent_mcp_tool_profile`(46) `tests.test_oc_l1_serve`(45) `tests.test_opencode_bash_guard`(82) `tests.test_bash_guard`(162) = 461 green (19 skips, unchanged), `node --check` clean.
- ADMISSION-FLOOR (2026-10-08, lane; routing D-733): lowered `coding.vm` launch floor — `catalog/ai-registry.json` `host_admission.mem_available_floor_mb` 6144 → 4608 (4.5 GiB), `max_live_workers` unchanged at 6; `tools/autoos-agent.py` `ADMISSION_MEM_FLOOR_MB_DEFAULT` stays 6144 (the absent-section code default). Pin updated: `tests/test_autoos_spawner.py::test_host_admission_limits_come_from_the_shipped_registry` asserts the shipped 4608 (the `host_admission_config(None)` leg, which loads the shipped registry); `test_cmd_run_refuses_for_admission_with_its_own_exit_code` asserts the shipped-floor message "floor 4608 MB". The `{}`/synthetic-floor pins and `ADMISSION_MEM_FLOOR_MB_DEFAULT` stay 6144. Gates: `registry.py validate` ok, `pytest -k dmission` green.
- L2LAUNCH-live-fix10 (2026-10-08, lane AO-L2-LAUNCH, live check of `tools/oc_l2.py start` on a scratch repo at main 483d6438): three start-time defects, each with its failing test first. **(D1)** `tools/oc_l1_canary.py` read only a *string* tool error, but the current opencode records it as an object — `state.error = {"type":"unknown","message":"bash-guard: DENIED - l2 read-only: a here-document …"}` on an item keyed `"name"` — so a real denial from a working guard came back "shell call inconclusive" and `start` refused its own healthy lane (rc 5); `_denial_text` now takes the object's `message`, the marker still anchors at its start, and the state detail carries the plugin's throw instead of a dict repr. **(D2)** `tools/oc_l1.py` documented the gateway base URL default `http://127.0.0.1:20128` as applied at start time while `oc_l1_serve._child_env` was a pure filter over the launcher's environment: with `AUTOOS_OMNIROUTE_URL` unset the served lane resolved `{env:AUTOOS_OMNIROUTE_URL}/v1` to an empty base and opencode died in `LLM.compile` with `TypeError: Invalid URL`, the session producing nothing (serve log run 19711e43); the default is now applied into the serve child env, an explicit parent value still wins, and the key gets no invented default. **(D3)** the lane's MCP never connected — `mcp connect failed server=autoos-agent status.error="Connection closed"` — because the repo config runs `python tools/autoos_agent_mcp.py`, a path relative to the *lane's* directory (`directory=/…/scratch`), which is outside AutoOS for a scratch lane repo: `oc_l1_render` pins that script to the rendering checkout, absolutely (both command shapes), and the suite spawns the rendered command with the rendered env from a foreign cwd and reads the `initialize` answer. Tests: `tests/test_oc_l1_canary.py` +7 (35→42), `tests/test_oc_l1_serve.py` +4 (38→42), `tests/test_agent_mcp_tool_profile.py` +5 (41→46). Verify set `tests.test_oc_l2`(46) `tests.test_oc_l1_canary`(42) `tests.test_oc_l1`(18) `tests.test_oc_l1_render`(19) `tests.test_agent_mcp_tool_profile`(46) = 171 green (19 skips, unchanged), plus `tests.test_oc_l1_serve`(42) `tests.test_oc_l1_status`(11).
- L2GATES-r4 (2026-10-08, lane AO-L2-LAUNCH, Sonnet round-4 REJECT of the `l2` read-only shell): seven findings, each with its failing test first, all in `configuration/opencode/plugins/bash-guard/index.mjs`. **(F1, BLOCKER)** a line continuation was a second tokenizer — `collectSegments` flattens `\<newline>` to `\ ` and `tokenizeSegment` reads that as an escaped space glued to the next word, so `cat \<NL>/etc/passwd` and `cat \<NL>.env` reached the leading-`/` test as `" /etc/passwd"` and passed; the `l2` role now refuses any command containing a backslash and checks every path word stripped as well as written. **(F2, HIGH)** `head` was `baseName(words[idx])`, which proved only that the last word looked like `cat` — `/tmp/evil/cat x` and `./cat x` were allowed; a head must now be a bare word with no separator in it. **(F3, MED)** the operand loop skipped every token starting with `-`, so the path rode in as an option's value (`rg --file=/etc/passwd foo`, `rg --ignore-file=/etc/shadow x`); a `=` value is now path-checked whatever option it hangs off, and a value-taking option whose job is to open a file (`rg --file`/`--ignore-file`/`-f`/`--pre*`/`--path-separator`) is denied outright — its value is checked first so the reason names the file, not the flag. **(F4, MED)** the secret rule matched the whole token, so git's rev:path spelling passed (`git show HEAD:.env`, `git show :.env`); an operand is now checked whole, trimmed, and per `:` and `/` segment. **(F5, MED)** the secret list was five case-sensitive patterns; it is now case-insensitive and carries `.netrc`, `.ssh/` and `.aws/` as a path segment, `id_rsa*`/`id_ed25519*`/`id_ecdsa*`, `credentials`, `secrets.y*ml`, `*.key`/`*.pem`/`*.p12`/`*.pfx`, `api-keys.yml`, `*credentials*.json`, `auth.json`, `.git-credentials`, `.npmrc`, `.pypirc`. **(F6, LOW)** a repo-relative NAME is not a repo-relative FILE: `rg -L`/`--follow` is denied and a surviving operand of `cat`/`rg`/`ls`/`git` is resolved with `fs.realpathSync` and refused when it lands outside the checkout, or when the resolver answers anything but ENOENT/ENOTDIR (a symlink loop fails closed). **(F7, LOW)** a wrapper was skipped, not obeyed — `sudo ls`, `timeout 5 ls`, `nice ls` all passed because the skip that the orchestrator role needs (`sudo rm /` must read as `rm`) left an allow-listed head behind; `sudo`/`doas`/`su`/`env`/`exec`/`command`/`nice`/`timeout`/`time`/`stdbuf`/`nohup` are refused as `l2` heads, which costs the lane the wrapper and nothing else. The other roles are untouched: F1–F7 live in the `l2` gate only, and a shared tokenizer keeps `orchestrator` byte-identical in behaviour. Tests: `tests/test_opencode_bash_guard.py` +12 methods (63 → 75) — the three allowances that encoded the holes (`timeout 5 ls`, `nice ls`, `rg --ignore-file .gitignore pattern .`) now assert the denial, and the reads that must survive are pinned beside them (`git show HEAD:README.md`, `cat .npmrc.example`, an in-checkout symlink). **Sonnet round-5 REJECT (2026-10-08) closed two more in the same gate:** **(1, MED-HIGH)** rg discovers its own inputs, so the operand rules never saw the files a recursive search read and the ignore rules were the only thing keeping it off this checkout's gitignored secrets — `--hidden`/`-.`, the `-u` cluster (`-u`/`-uu`/`-uuu` and any bundle carrying `u`), `--unrestricted`, `--no-ignore*` and `-g`/`--glob`/`--iglob`, each in its `=`-value and bundle spellings, are denied for `l2`, which is what let `rg --hidden KEY`, `rg -uu KEY`, `rg -. KEY` and `rg --no-ignore --hidden KEY` print `./.env`; **(2, LOW)** git resolves an unambiguous abbreviation of a long option, so an exact-match deny table was a hole — every denied git long (`--output`, `--ext-diff`, `--textconv`, `--no-index`, `--exec-path`, `--git-dir`, `--work-tree`, …) is now refused from three characters of its name upward, so `git log --outp=f`, `git diff --ext-d` and `git show --textc` are read as what they are; `orchestrator` and the unroled path still byte-identical, the abbreviation rule and the rg list live in the `l2` gate only, tests +5 methods (75 → 80). Verify set `tests.test_oc_l2`(46) `tests.test_oc_l1_canary`(35) `tests.test_opencode_bash_guard`(80) `tests.test_bash_guard`(162) `tests.test_agent_mcp_tool_profile`(41) = 364 green (19 skips, unchanged), `node --check` clean.
- L2GATES-r3 (2026-10-08, lane AO-L2-LAUNCH, Sonnet round-3 REJECT of the `l2` read-only shell): three holes closed, failing-first
  tests first. **(1) the wrapper was the read** — `skipLeading` walks past `env` by design, so a bare `env` had no head to audit and
  dumped the lane's own environment (server password, gateway keys) past the gate; `env`, `printenv`, `set`, `export`, `declare` are
  now denied wherever the skip left them, prefix word or head word. **(2) paths became an allow rule, not a spelling list** — naming
  `/proc/self/environ` left `/proc/./self/environ`, `//proc//self//environ`, `/proc/$$/environ`, a globbed `/proc`, `~/.claude.json`
  and `~/.config/opencode/auth.json` all readable, so a path operand of any allowed head (`cat`/`head`/`tail`/`wc`/`ls`/`rg` and a
  `git` pathspec) is allowed only when it is relative and the shell cannot move it: no leading `/`, no leading `~`, no `..` segment,
  no `//`, no `$`, no backtick, no glob character, denied as `bash-guard: DENIED - l2 read-only: path outside repo`. The cost of the
  rule is stated where it bites: an absolute path is refused whatever it points at (`/proc/cpuinfo` included, a test that had been
  passing now asserts the denial), and a glob character is refused even inside a search pattern. The secret files that live *inside*
  the repo stay denied on their own rule: `.env*`, `*.key`, `*.pem`, `api-keys.yml`, `*credentials*.json`. **(3) a short-flag bundle
  hid the denied flag** — `l2OptionDenial` matched whole tokens, so `rg -uz` passed while `rg -z` did not; the deny sets are now split
  by spelling and a bundle is tested character by character (`git`'s `-x`/`-a` get the same parsing).
  Tests: `tests/test_opencode_bash_guard.py` (+5 methods / 43 commands, the `l2` role's env heads, escaping paths, in-repo secrets,
  bundles, and the repo-relative reads that must survive); verify set `tests.test_oc_l2`(46) `tests.test_oc_l1_canary`(35)
  `tests.test_opencode_bash_guard`(63) `tests.test_bash_guard`(162) `tests.test_agent_mcp_tool_profile`(41) = 347 green.
  Docs: `docs/ai/opencode-fleet.md`, `configuration/opencode/plugins/bash-guard/index.mjs`.
- L2GATES (2026-10-08, lane AO-L2-LAUNCH, the three merge criteria left open by L2LAUNCH-REJECT), one commit and failing-first tests
  each. **(1) the L2 shell is read-only, not scoped-write**: the bash-guard gained its own role, `AUTOOS_GUARD_ROLE=l2` (set by
  `tools/oc_l2.py`), whose allow list is `git status|log|diff|show`, `ls`, `cat`, `rg`, `head`, `tail`, `wc`, `pwd` and nothing else —
  any output redirect, any stdin redirect or here-document, any backtick/`$(…)`/`>(…)` substitution, and every mutating `git` verb
  are denied with `bash-guard: DENIED - l2 read-only: …`. Both roles share one tokenizer, wrapper-skipping and git-verb parser
  (`gitSubcommand`), so they cannot drift, and the denial still carries the marker verbatim: the canary is denied by the `l2` role too,
  so a lane that starts is a lane whose guard works. Consequence, stated in the L2's own footer: a read-only shell cannot append its
  `REPORT`/`DONE` line, so the report is a tier-3 spawn whose whole task is that line. **(2) the L2's MCP lists the spawner, not the
  lane menu**: which tools `autoos_agent_mcp` registers is now a profile picked from its own environment (`AUTOOS_AGENT_LAYER=L2`,
  which `tools/oc_l1_render.py` renders into the MCP server's env from the lane's `agent_layer`). Profile `l2` registers `spawn`,
  `status`, `result`, `ps`, `list_clients`, `route`, `context`, `heartbeat` — no lane tool, no `cancel` of a foreign run, no `respond`
  to a question this lane did not ask — and an unknown profile falls back to that narrowest list, never to the full one. The
  function-level fence stays: those five lane tools still answer `refused: true` on `L2`, because the same code is reachable from the
  CLI and a hidden tool is not a permitted call. `serve()` became `build_server(profile=None)` + run so the pin drives real
  registration (`build_server(...).list_tools()`). **(3) an L2 spawns tier-3 workers only**: `l2_spawn_refusal` is checked in
  `build_argv`, where the tier is known AFTER routing, so `--tier 2`, a card that routes to tier 2, and a `role: orchestrate` card are
  all refused before a run dir exists — below tier 3 is a session that holds its own editor, which is the thing the lane exists to
  prevent (the full ROLE-GATE is a later lane). Tests: `tests/test_opencode_bash_guard.py` (+16, the `l2` role, the canary under it,
  no leakage into `orchestrator`), `tests/test_oc_l2.py` (the rendered role, the dispatch site in the plugin, the marker in the MCP
  env), new `tests/test_agent_mcp_tool_profile.py` (21: profile selection, the registered tool set per profile, the renderer marking
  the server it starts, the spawn tier gate); the brief's verify set is 387 green (291 for the four named suites, plus the profile,
  lane, render, serve and canary suites) and the wider lane set 1791 with one failure this diff does not reach:
  `test_a_real_spawn_runs_in_its_scope` skips when its own class runs alone (`scope_supported()` is false — no user manager on this
  host), and it only trips in full-module order, where an earlier SCOPECLI test calls `scope_supported(force=True)` under a synthetic
  bus environment and the module-wide cache then claims a scope the fresh child process cannot create.
  Docs: `docs/ai/opencode-fleet.md` (the layer fence, the rendered L2 lane), `tools/oc_l2.py`, `tools/autoos_agent_mcp.py`.
- L2LAUNCH-REJECT (2026-10-08, lane AO-L2-LAUNCH, Sonnet final REJECT of the D-665 launch work): seven findings, one commit and one
  failing-first test each. **(F1, HIGH)** `permission.task: deny` alone left the renderer's `edit: allow` standing, so an L2 — whose
  whole job is to coordinate — could write files with its own editor tools; `tools/oc_l2.py` now denies every file-mutating and
  spawn key the opencode schema declares, in both spellings (`edit`, `write`, `patch`, `apply_patch`, `task`, `subagent`, from
  `lib/agent_harness.py`'s rename map), keeping `read` and the guarded `bash`. REPORT: the same hole exists for **L1** lanes —
  `PERMISSIONS` in `tools/oc_l1_render.py` still renders `edit: allow`, deliberately unchanged, because an L1 lane does write work;
  closing it is a decision, not a fix. **(F2, HIGH)** one `autoos-agent` MCP server answers both levels, so an L2 could call
  `l2_start`/`l2_stop`/`l2_inbox`/`oc_start`/`oc_restart` and relaunch its own supervisor or switch off a neighbour's phase; the L2
  lane now marks its level (`agent_layer: "L2"` → `AUTOOS_AGENT_LAYER` in the child env, a lane property and never an inherited one)
  and those five tools answer `refused: true` when they read `L2` back, without reaching the launcher at all — the two status tools
  stay open. And `_spawn` no longer hands the lane `dict(os.environ)`, which put every credential the launcher's shell exported
  (GitHub token, provider key, database password) in `/proc/<pid>/environ` where the lane, its MCP server and every worker it spawns
  could read it: `oc_l1.env_is_allowed` is one rule — the names and families a session needs, minus every credential-shaped name in
  them, the two gateway names exempt because the rendered config references them as `{env:...}`, the server password reaching the
  child under `OPENCODE_SERVER_PASSWORD` only — with a lane-level `child_env` list of extra NAMES (a value or a credential name in a
  lane config is refused by validation) and a names-only report of what stayed behind. Found while testing it: `_OC_L1_ENV_KEYS`
  forwarded `AUTOOS_OMNIROUTE_URL` but not `AUTOOS_OMNIROUTE_KEY`, so a lane started through the MCP had no key for its model.
  **(F3, MED)** `inbox` nudged any session whose state file answered 200, including an rc-5 lane whose canary never denied: the line
  is still appended, but the wake is refused (`refused: true`, exit 2) unless the stored canary denied AND the pilot was prompted.
  **(F4, MED)** `stop` signalled the recorded PID's *process group* unconditionally — when that PID was not the lane's own child
  leader it SIGTERM'd the caller's whole group, i.e. the suite or the L1 — and matched the lane by command *stem*; now the group is
  signalled only when `getpgid(pid) == pid`, and identity is the full recorded `argv` tail plus the `/proc` start time the launcher
  records at spawn, failing closed on a state file that carries neither. **(F5, MED)** the canary counted a denial on any guarded
  tool call whose error text started with the marker, so a lane that denied some *other* command passed; a denial now has to be of
  the canary's own command. **(F6, MED)** `derive_port` is a hash guess onto 47200-47299 and `start` spawned onto it blind: a
  collision cost a health-poll timeout, a killed child and read as a broken lane. The port is bound-tested before the spawn, the
  walk continues forward inside the range (refused when the range is full), and the port the server actually took is what the state
  file records — status, stop, canary and inbox all talk to the server that answers. **(F7, LOW)** a lane named
  `l2-<repo-basename>-<phase>` collided between two checkouts of the same basename (two sandboxes of one project = one lane), and a
  second `start` called an unverified rc-5 stub "already running"; the name now carries a six-hex tag of the repo's absolute path
  (one project spelled two ways stays one lane) and the two refusal messages differ: a live guarded lane points at `l2_inbox`, a
  stub says it never cleared its canary and must be stopped first.
  Tests: the pinned suite (`test_oc_l1`, `test_oc_l1_canary`, `test_oc_l1_serve`, `test_oc_l1_render`, `test_oc_l1_status`,
  `test_oc_l2`, `test_opencode_bash_guard`, `test_bash_guard`) 326 → 360, all green; each fix was seen red first, the whole set
  re-run green after the last one. Docs: `docs/ai/opencode-fleet.md`, `configuration/oc-l1.example.json`, and the module docstrings
  of `tools/oc_l1.py`, `tools/oc_l1_serve.py`, `tools/oc_l2.py`, `tools/autoos_agent_mcp.py`.
- CANARYFIX2 (2026-10-08, lane AO-L2-LAUNCH step 2, security seat REJECT of CANARYFIX): five ways the L1 canary could report
  `denied=yes` without the bash-guard ever denying anything, each closed with a test that failed first.
  **(F1)** `tools/oc_l1_canary.py` accepted the tool name `execute`, which the guard's `execute.before` hook never inspects
  (`index.mjs` guards `shell` and `bash` only) — a model echoing the marker through `execute` false-passed. The accepted set is
  now one list, `GUARDED_TOOL_NAMES = (shell, bash)`, and `tests/test_oc_l1_canary.py` pins it against the names parsed out of
  the plugin source, in both directions. **(F2)** a transcript item with no tool name but a `state.status` fell through the
  name check and read as a shell call, so a nameless `error` item carrying the marker passed (rc 0) and a nameless `completed`
  item claimed "tool completed without denial"; a guarded call must now NAME a guarded tool. **(F3)** `DENIED_MARKER in err`
  matched the marker anywhere in the text, so any model-authored string that quoted it passed — a denial is now
  `_is_denial()`: the error string must start with `bash-guard: DENIED` after stripping whitespace, on a guarded tool call in
  `error` state. **(F4)** `_child_stderr_log` in `tools/oc_l1_serve.py` opened `<scratch>/opencode.log` with the process umask
  and chmod 0600 afterwards, leaving a window in which the child's log (next to an environment carrying the server password)
  was 0644/0666 and the scratch tree kept the umask mode; the log is now created by `os.open(O_WRONLY|O_CREAT|O_APPEND, 0o600)`
  and the tree by `_private_dir()` at 0700 under a `0o077` umask — a path an earlier, looser launcher left is tightened, never
  widened. **(F5)** `_scrub` in `tools/oc_l1_http.py` replaced only the bare password, while urllib echoes the request's
  `Authorization` header into the error text the launcher prints and logs: it now also removes the exact `Basic <b64(user:pw)>`
  header, the base64 form on its own, and any `Authorization:` value (an unquoted value takes the rest of its line — fail
  closed), including on a path with no password in scope. Prose that says "Basic auth" is left alone.
  Tests: `tests/test_oc_l1_canary.py` 17 → 32, `tests/test_oc_l1_serve.py` 11 → 26; the pinned suite (`test_oc_l1`,
  `test_oc_l1_canary`, `test_oc_l1_serve`, `test_oc_l1_render`, `test_oc_l1_status`, `test_opencode_bash_guard`,
  `test_bash_guard`) 257 → 287, all green. Docs: `docs/ai/opencode-fleet.md`,
  `.agents/skills/unattended-orchestration/references/opencode-fleet-lessons.md`.
- CANARYFIX (2026-10-08, lane AO-L2-LAUNCH step 1, D-665 P0): the L1 canary could not report a denial at all, so every unattended lane sat in `rc=5` (`UNATTENDED-REFUSED`) forever — 461 starts on the host. Five causes, five fixes, each with a test that failed first: **(1)** `tools/oc_l1_canary.py` matched only the tool name `shell`, while opencode's own rename map makes `bash` an alias of `shell` (KEYDENY3b) and code mode exposes the executor as `execute`; it now accepts `{shell, bash, execute}` and, when it finds no shell call, writes the tool names it saw plus the first 200 chars of assistant text into `canary.detail` — the next failure says WHY. **(2)** the c0 orchestrator-role throw in `configuration/opencode/plugins/bash-guard/index.mjs` runs BEFORE `tools/hooks/bash_guard.py`, and it emitted `bash-guard: orchestrator role: …` without the `bash-guard: DENIED` marker the canary greps for: a real role denial read as "shell call inconclusive". The throw now carries the marker verbatim, and `tests/test_opencode_bash_guard.py` imports `DENIED_MARKER` from the canary instead of restating it, so the two cannot drift again. **(3)** `plugins` was an optional lane key and `render` omitted it when empty, so an unguarded lane paid for a model call that can never be denied; `start` now refuses `plugins: []` with **rc 2** (config defect, not a canary result) before rendering, spawning or requesting anything. **(4)** the child's stderr was `DEVNULL`, which swallowed every fail-open note the plugin writes ("guard script missing", "timeout, allowing") — the reason a missing guard was indistinguishable from a permissive one; it is now appended to `<scratch_dir>/opencode.log` (mode 0600, best-effort: no log never costs the lane, and a relaunch appends rather than destroying the earlier evidence). **(5)** the canary prompt now demands `EXACTLY ONE tool call` and names the shell tool, and a prose-only answer is reported as `inconclusive: text-only answer` instead of the guard allowing the command — same exit 5, different `detail`. Not in this change: the amplifier, `docs/ai/oc-runtime-sources/watch2.py.txt` deletes the state file on any non-zero rc and has no consecutive-failure ceiling, so it re-pays three full canaries every ~10 min forever (D-665 asks for keep-state-on-rc5 + park after 3 cycles). Tests: `tests/test_oc_l1_canary.py` 8 → 15, `tests/test_oc_l1_serve.py` 8 → 11, `tests/test_opencode_bash_guard.py` 31 → 33; the shared fake lane (`tests/_oc_l1_fakes.py`) now carries a guard plugin directory and writes a stderr note, because a fake must reproduce the real contract. Docs: `docs/ai/opencode-fleet.md`, `configuration/oc-l1.example.json`.
- OVHCODER-DROP (2026-10-08, hotfix, lane lane-ovh-coder-drop): OVH withdrew `Qwen3-Coder-30B-A3B-Instruct` upstream — measured 17:34Z central: HTTP 404 "The model `Qwen3-Coder-30B-A3B-Instruct` does not exist"; probe D-657 2026-10-08 had already 404'd both the `ovh/` and `ovhcloud/` spellings on central and workstation while `gpt-oss-120b`, `Qwen3.8-27B` and `Qwen3.5-397B-A17B` answered 200. Retired the same way ORQWEN404 retired `or-qwen3.8-27b-free`: the leg left `l2-worker`/`l3-driver` (legs **and** their `unavailable_legs` gates — L1-CLEAN's "remove when OVH re-lists it or after a re-probe" condition is now met), route `ovh-qwen3-coder-30b` is deleted and its id moved to `OMNIROUTE_RETIRED_IDS` so `apply` prunes the live combo, and it left `IDE_MODEL_ORDER`. The model row is kept for pricing history and carries the dated `OVHCODER-DROP` unavailable note. Consumers: the `ovh-direct-qwen3-coder-30b` hand entry is removed from `opencode.jsonc`, `combos.json`/`config.yaml`/`ide-models.json`/`docs/models.md` re-synced (`render --check` green on all five surfaces), `run-tests.ps1` context map, `test_autoos_spawner.py` hand-entry loops, `combo-create` skill, `models-proposed.md` retired list. The two old pins of the dead state became pins of the retirement: `test_stale_ovh_coder_legs_are_marked_unavailable` → `test_ovh_coder_leg_is_retired_not_merged`, and the models-doc strike-through moved to the gated `opencode_gateway/glm-5.3-flash` head leg. `logs/routing/availability.json` is git-ignored runtime state, so the ledger record is a live-host step, not a commit here.
- OVHCODER-DROP-RESTORE (2026-10-08, lane lane-denylegs, this checkout): the removal above did not survive the lane's merge of main — `routes.ovh-qwen3-coder-30b` came back in `catalog/ai-registry.json` while `tools/registry.py` kept the retirement, so `routes` and `IDE_MODEL_ORDER` disagreed and `sync-ide-models.py --check`, `registry.py render omniroute --check` and the router-drift/litellm-mirror CI lines all failed. Deleted the route again, kept `models.Qwen3-Coder-30B-A3B-Instruct` for pricing history with the dated OVHCODER-DROP note added to its `$comment`, removed the `ovh-direct-qwen3-coder-30b` hand entry from `opencode.jsonc`, and re-synced the five render surfaces (`combos.json`: id out of `omitted`, into `retired`, plus its own dated header line; `config.yaml`, `catalog/ide-models.json`, `configuration/openhands/tier-profiles.json` and `docs/models.md`'s managed table were already or are now what the registry renders — `render <surface> --check` green on all five, `registry.py check` green at 45 routes). Tests: `test_ovh_legs_ride_l3_chains_only_after_the_reprobe` no longer pins the dead seat route (it asserts the leg is absent from `routes`), and the retirement itself is pinned by `test_ovh_coder_leg_is_retired_not_merged` — route gone from `routes`, `combos.json`, every render surface and `opencode.jsonc`, id in `OMNIROUTE_RETIRED_IDS` and in `combos.json.retired`, model row kept with its price. `test_registry_render.py`'s two `renders-nothing` sets dropped the id: a deleted route is not a declaration that renders nothing, so it belongs to no set (that is the difference between `omitted` and `retired`). `tests/test_autoos_spawner.py`, `tests/run-tests.ps1`, the `combo-create` skill and `docs/models-proposed.md`'s retired list already carried main's text; only `docs/models-proposed.md`'s keep-table, leg table and LiteLLM group list still named it and were corrected. Lane intent untouched: OVH `gpt-oss-120b`/`Qwen3.8-27B`/`Qwen3.5-397B` still ride L3 chains only, never consecutive, `l3-review-transcript`, ADR 0007, the L2 >=600k band, the D-657 chains and `--strict-d657` are unchanged. Three linux checks failed on this branch's base commit exactly as they do after this restore, so they were never this removal's symptoms. `free-only litellm groups mirror combos` is fixed in the same push: its hardcoded `namespace` map needed the FREEKEYS-2e translation for `bzl` -> `bazaarlink` (providers.bazaarlink.model_prefix), which D657-CHAIN owed the bazaarlink head leg it put on every tier. Two remain open and each needs a decision, not a re-render: `litellm fallback config is internally consistent` wants an `l1-orchestrator-paid` hand group that D657-CHAIN left unwritten when it emptied that route's legs (writing it means choosing a paid chain), and `the router declarations do not drift` names `l1-orchestrator-paid` and `l3-review-transcript` because a deliberately legless route keeps its `ide-models` declaration while `audit-router.py` demands a combo for anything offered through `omniroute`.
- PRICE-GAP (2026-10-08, lane; L1-measured on the live gateway): `usage --cost` read provider `vertex` — 232 calls, 13.8M tokens_in — at **$0.0000**, and `credit_guards.vertex_ai.models_unpriced` said 2, while `models.gemini-3.8-flash.provider_prices.vertex_ai` carried a price the whole time. One spelling gap, in two consumers: a call-log row names its provider by the gateway connection id (`vertex`) or its model namespace (`ovh`), the registry files everything by provider id (`vertex_ai`, `ovhcloud`). `autoos_usage`'s report aggregation (`_add_row`/`aggregate`/`totals`/`build_report`) called `price_for(model, prices)` with neither the row's provider nor the registry — `paid_spend` (the guard) passes both, so the ledger printed $0 across a grant the guard was pricing at the same moment — and `run_budget.get_price` keyed `provider_prices` by the row's raw spelling. Fix: `tools/registry.py` gains the ONE registry-derived normaliser — `provider_spellings_for` (one provider's row: its id plus every `omniroute_id` / `model_prefix` / declared `aliases` spelling, read from the data so a renamed connection needs no code edit), `provider_alias_map` (the same per provider id in the registry) and `provider_id_from_spelling` (a provider id is never shadowed by another provider's gateway id; an ambiguous spelling resolves to nothing rather than borrowing a stranger's price). `leg_price`, `prices_from_registry` (the table now carries every namespace a provider declares, never the bare model id), `autoos_usage._provider_spellings` (previously a hand-read of two fields, and now a per-row lookup instead of a whole-map rebuild — it runs per call-log row per credit provider) and the new `run_budget.registry_price_row` all go through it. The gate's fallback policy is untouched: a registry row that cannot state a complete price still loses to `configuration/google-prices.json`, same order, same rates. Data: `provider_prices.vertex_ai` filed the 2027-01-01 **list** rate ($1.50 in / $7.50 out per 1M) as current while the rate in force through 2026-12-31 is the intro $0.75 / $3.75 (50% credits back) — no dated-price mechanism exists (`provider_prices` admits no extra key), so the row now holds the in-force rate and the list price plus its open date ride in `price_source`, and the repo's two price sources stop disagreeing by 2x on every Vertex row. `resolve_leg` deliberately still refuses the `ovh` / `agy` / `bzl` / `oc` namespaces — routing spelling is not pricing spelling (the D657-FINAL skipped-legs note). Tests: +13 `test_registry.py` (`ProviderSpellingTests`), +11 `test_autoos_usage.py` (`ProviderSpellingCostTests`, including the measured 13.8M-token row priced through the real registry, a guard/report equality pin, and a pin that the guard's row filter and the price table read the same per-provider row), +6 `test_run_budget_prices.py` (`TestRegistryScopedProviderSpelling`, one of which pins the unchanged fallback); red before the fix in all three files. Re-measured on the shipped registry: 13.8M Vertex input tokens = $10.35, guard and report agree to the cent, `models_unpriced` 0. ovhcloud/morph rows stay unpriced because no price is on file for their models — not invented here. Gates: `registry.py validate` ok (37 routes / 92 models / 55 providers), `registry.py check` ok, all five `render --check` byte-identical (no re-render needed), `test_registry.py` 372, `test_autoos_usage.py` 121, `test_run_budget_prices.py` 16, `test_run_budget.py`/`_more`/`_v3`/`_v3b`/`_spawner_v3` OK, `test_t1_credit_fix2/6/7/10/14/16/17` OK (`fix6`'s two shipped-registry pins now read the rate off the row instead of restating it, the figure itself pinned once in `test_autoos_usage`). Full `tests/test_*.py` sweep: 105 files, no new failure; the sweep's reds are all host-admission under the sweep's own load and none of them reads a price — `test_run_budget_spawner.py::McpStartGateTests` (6 live workers / cap 6, fails at the branch point too), `test_autoos_spawner.py::FreeConcurrencyCapTests` (MemAvailable 5926 MB against a 6144 MB floor — green when re-run alone) and `test_autoos_spawner.py::McpToolTests.test_a_real_spawn_runs_in_its_scope` (self-skips without `systemd-run --user`).
- D657-FINAL (2026-10-08, lane AO-PROBE-D657, server-L1-routing judged the schema): `models.<id>.prompt_cache` is now one of the **strings** `"true" | "documented" | "false" | "unknown"` instead of `boolean|null` — a boolean could not hold a vendor's documented claim next to a measured hit, and `tools/probe-free.py` already spells the fact as a string in its TSV cell. New optional `prompt_cache_source` carries the dated attribution (rides the `price_source` rule-5 exemption); `_check_prompt_cache` owns both, because a gate reading `is True` and one reading truthiness disagree about the string `"false"`. Data: every leg of `configuration/omniroute/probes/probe-d657-central.tsv` + `-workstation.tsv` was mapped to its registry row through `resolve_leg` and given the verdict its two gateway `cache` cells add up to — 21 rows: true=2, false=5, unknown=13, and `gemini-3.8-flash` = documented (Google implicit caching covers its vertex legs). 58 TSV rows (29 distinct legs) were skipped, not invented: `resolve_leg` names no provider for the `agy`, `bzl`, `oc`, `ollama-cloud` and `ovh` prefixes — neither a provider id nor any `omniroute_id` carries those spellings (18 legs) — and names no model row for `cohere/command-a-plus-05-2026`, the three `gemini/deep-research-*`, `nscale`'s three org-keyed ids (`Qwen/Qwen3-235B-A22B-Instruct-2507`, `Qwen/Qwen3-32B`, `mistralai/Devstral-Small-2505`), `opencode-zen/muse-spark-1.2-contributor-free`, `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`, `openrouter/thinkingmachines/inkling-small:free` and `vertex/claude-opus-5` (11 legs). 23 distinct legs resolved onto the 21 rows. `tools/probe-free.py` gains `--no-retry` (one call per request, never `RETRY_DELAYS_S`) and does not retry a 429 whose own body states a reset beyond `RESET_GIVE_UP_S` (300 s) — `reset_window_s` sums compound windows (`110h47m`) and stops at a trailing timestamp; `tsv_cell` strips `\r` with `\t`/`\n`. Gates: `registry.py validate` + `check` ok, `test_registry.py` 359 (was 356), `test_registry_render.py` 169, `test_probe_free.py` 30 (was 19), all five renders and `sync-ide-models.py --check` byte-identical (no re-render needed).
- PUBLIC-IDS-SCRUB (2026-10-08, lane, AGENTS Hard Rule 1): the operator's real GCP project id and Vertex service-account key id are gone from the tracked tree — 5 occurrences across `catalog/ai-registry.json` (`providers.vertex_ai.$comment`), the `configuration/omniroute/apply.sh` + `apply.ps1` headers and `docs/handoff/2026-10-01-laneTierOrder-batch2.md` now read `configuration/vertex-credentials-<gcp-project>-<keyid>.json`, with the reason written beside the placeholder. A project id is not a password but it is a lookup key: it names the account, appears in IAM bindings and `*@<project>.iam.gserviceaccount.com` addresses, and the credential's filename is built from it. **History rewrite is NOT in this change** — operator decides. Proving test: `tests/test_public_ids.py` (red on the 5 hits first, green after; line-scoped GCP project-id shape near project/GCP/vertex/service-account vocabulary, plus any real `@<project>.iam.gserviceaccount.com`, so the shape-sharing `*.autoos-backup-<date>-<time>` names this repo writes stay clean), wired into `tests/linux/33-documentation.sh` and `tests/run-tests.ps1`. A `$comment` is not rendered anywhere, so the derived files were untouched: `registry.py validate` ok (37 routes / 92 models / 55 providers), all five `registry.py render <target> --check` ok, `sync-ide-models.py --check` ok.
- SMALL-BATCHES (2026-10-08, operator: "only test on smaller batches, investigate differences and continue until the development is finished"): `coding-principles` gains **Principle 10, develop on small batches; run the full data once (rigid)**. It covers the data ladder (synthetic data with a known output → a small, varied real batch → the full data once, at the end), minute-scale iterations with cached intermediates, reading stored figures instead of recomputing them at scale, labelling every number with its sample, and capped, below-normal-priority jobs on a shared machine. Trigger: three investigators in basic-analysis each started a corpus-wide pass, which held a 32-core host at 99 % CPU. `unattended-orchestration/references/main-orchestrator.md` §4b restated the ladder; it now points at Principle 10 (single owner) and keeps only the orchestration-specific rule that briefs name the small batch and require L1 approval for a full run. The description and checklist gain the matching trigger and line.
- SCWREMOVAL (2026-10-06, operator: scaleway not used anymore): legs removed from every band (t2-worker, t2-worker-free-only, t3-driver, t3-driver-free-only, t4-researcher); provider gated `available: false` (rows kept for pricing history); reviewer leg moved to `groq/qwen/qwen3.8-27b` (same qwen family). Honest clamp rises to 131072 on the free-only twins + researcher (`sync-ide-models.py` regenerated). All renders re-synced; `docs/models.md` managed table re-rendered and hand prose + mermaid redrawn to current legs. Live probes `T3-OK` (omniroute) + `LITELLM-OK` (litellm). Gates: registry ok, all renders ok, contract PASS 24, `test_registry.py` + `test_registry_render.py` OK, combos filter green.
- ORQWEN-DEAD (2026-10-06): OpenRouter withdrew the `qwen/qwen3.8-27b:free` slug (live 404, gateway pre-dispatch skip); leg removed from 4 bands, `or-qwen3.8-27b-free` single gated to `omitted`, live stale combo pruned; nemotron/laguna/north-mini re-probed serving. Renders re-synced; `t2-orchestrator`/`-clean` pins corrected to 128k, `opus-5-5` pin added, ovhcloud credit-tier qwen exemption in the `-clean` free-leg ban.
- PUSHGATE (2026-10-05/06, operator): L0 merges the take loop and pushes `origin/main` — `opencode.jsonc` allows plain `git push origin main`, `git merge --no-ff L1-backlog/*` + `origin/main`, `--ff-only origin/main`, `merge --continue/--abort`, `fetch`; branch `-D` and worktree-remove allows are exact-name/path only. Force-push/delete/reset/rebase stay denied.
- OMNI-AUTOOS3 (2026-10-06, routing D-626): central gateway image `autoos/omniroute:3.8.50-autoos3` applies the reviewed U2 Vertex trailing-model-turn patch (`tools/apply-vertex-patch.py`, sha256 c6544e91…) at BUILD time: a python stage patches a copy of `/app/.build/next/server/chunks` (the runtime rootfs is read-only and has no python) and the result is copied back; the build fails unless run 1 reports `12 patched, 0 skipped, 0 errors` and run 2 `0 patched, 12 skipped` (chunk names are build-specific, so a FROM bump that renames them breaks the build instead of shipping unpatched). compose adds the `tools` additional build context. U13 (quota lockout) is NOT included: 7 of its 12 chunk names differ in this image.
- spawner: read-only git subprocesses on the MCP tool paths no longer inherit stdin (MCP-STDIN)
- opencode: oc_l1.py launcher (render|start|status) with the bash-guard CANARY for unattended OpenCode L1 sessions (O1-LITE)
- COST-GATE-CULTURE-FIX (2026-10-04): the Windows rows export (`lib/windows/cost-gate-export.ps1`) no longer stops paging after the first page on non-US cultures (PowerShell turned the call-log timestamps into DateTime objects and re-parsed them with the current culture: de-DE read 4 October as 10 April, so the daily gate counted only ~500 rows and under-reported spend); dates are now used without a culture round trip, the cutoff is the start of the current UTC day minus 1 h (page cap 60 x 500), and an export that ended for any other reason (page error, unreadable timestamp, page cap, a cutoff the raw ISO timestamps do not confirm) writes `UNAVAILABLE: rows export incomplete: <reason>` and keeps the old gate file. Proving tests: `tests/test_cost_gate_culture.py`.
- opencode: bash-guard plugin (shell tool) sharing the FLEET-HOOKS core (O4)
- OVH-PRICE-ROWS (2026-10-04): cited OVH price row for Qwen3.5-397B-A17B ($0.708/M in, $4.248/M out, converted at 1.18 USD/EUR from 0.6 EUR/M in and 3.6 EUR/M out on OVH catalogue, USD table cross-check pending).
- OVH-TEXT: the three `ovh-direct-*` hand entries in `opencode.jsonc` no longer say OVH is public work only; T1-CLEAN (D-502) cited the no-training terms, so the entries now read private-safe, trial credit, cap per D-567 (name strings only, no behaviour change).
- COST-GATE-ACTIVATE (2026-10-03): activation of the COST-GUARD spawner gate — `tools/cost-gate-refresh.py` (subprocess of `run_budget.py day`, atomic `daily-gate.json` write, one-line `daily-gate.status`), `tools/cost-gate-status.py` (status line + `STALE`/`PRICE-GAP` alarm, exit 0 always, 30-minute threshold), the default gate path (`AUTOOS_DAILY_GATE_FILE` unset falls back to `<state dir>/daily-gate.json`, else fail open), per-host `daily-gate.conf` budgets (workstation warn 12 / block 15, central warn 8 / block 10, built-in defaults 20/25) and the installers (Linux user timer `cost-gate.timer` every 10 min, Windows scheduled task `AutoOS cost gate` every 10 min + export wrapper; files only, enable is opt-in). Docs: `docs/ai/cost-guard.md` (Activation section). Proving tests: `tests/test_run_budget_prices.py`, `tests/test_cost_gate_refresh.py, tests/test_cost_gate_refresh_more.py`, `tests/test_cost_gate_default_path.py`, `tests/test_cost_gate_units.py`, `tests/test_cost_gate_units_more.py`, `tests/test_cost_gate_windows.py`.
- COST-GUARD (2026-10-03, operator D-543): mechanical cost discipline for Google-paid models (vertex and gemini/AI Studio) via stdlib `tools/run_budget.py` and spawner integration (`tools/autoos-agent.py`); `run_budget.py run` evaluates per-run uncached token volume and estimated USD (stop at >1M uncached or >$2.00, rotate at last call >=150k input tokens, flag on trailing 3 calls >60k tokens), `run_budget.py day` aggregates Google-paid spend across row files (ok / warn >=$20 / block >=$25, agy and OVH excluded), and `autoos-agent.py` refuses Google-paid starts when `AUTOOS_DAILY_GATE_FILE` reports block - checked before the plan, on the plan the run launches, and again in the fallthrough re-run, and on the MCP spawn path through the child-environment passlist (fail-open printing `daily gate unavailable: <reason>` to stderr when unset, unreadable - including a non-numeric `usd`/`budget` - older than 2 hours, dated in the future, or not written for today's UTC day; a UTF-8 BOM or a UTF-16 gate file is read, not skipped; spawner has no override flag - operator writes a new gate file with `run_budget.py day --budget N` to raise it). Exclusions are per model string: `agy`/`antigravity`/`ovh`/`ovhcloud` models are never refused and an `ovh/` leg does not stand the check down for a Google-paid plan model; `--free` no longer exempts a pinned Google-paid model. Pricing loaded from registry with fallback to `configuration/google-prices.json`. Intended behaviours counting or refusing beyond the basic spec: provider `unknown` / `(unknown)` is treated like an empty provider; the bare word `google` counts as Google-paid; `main(argv, fetch)` has an injected fetch for tests. Docs: `docs/ai/cost-guard.md`. Proving tests: `tests/test_run_budget.py`, `tests/test_run_budget_more.py`, `tests/test_run_budget_v3.py`, `tests/test_run_budget_v3b.py`, `tests/test_run_budget_spawner.py`, `tests/test_run_budget_spawner_v3.py`.
- OMNIGRAPH-POLICY (2026-10-03, lane GM-PREP-POLICY): read-only gateway actor policy (`infra/mcp-servers/cluster/gateway.policy.yaml`) and static refusal test suite (`tests/test_gateway_policy.py`).
- GATEWAY-QUOTA-PATCH (2026-10-03): loud-fail substitution patcher (`tools/apply-gateway-quota-patch.py`) resolving spurious 5-minute quota lockouts every 70 minutes on Vertex AI and other providers reporting unbounded quota windows without a total (such as vertex spend self-tracked via `getConnectionSpendUsdSinceAdded`). In compiled Next.js chunks (`dist/.build/next/server/chunks/`), `normalizeQuotas` and `setQuotaCache` defaulted missing/zero totals to `remainingPercentage: 0`, which `isExhausted` evaluated as true, locking the connection with `503 ALL_TARGETS_SKIPPED` for `EXHAUSTED_TTL_MS = 5 min` after each refresh. The patch maps missing/zero totals to `null` (unknown) and applies Case H exhaustion semantics where windows without a valid total are ignored and a connection is exhausted iff at least one window has a valid total and every window with a valid total is exhausted (`t.every((e,_,a)=>a.some(w=>null!=w.remainingPercentage)&&(null==e.remainingPercentage||(!1!==e.fractionReported&&e.remainingPercentage<=0)))`); closes persistence gap (total-less window saved as `is_exhausted: 0` with `null` remaining), reload gap (`hydrateQuotaCacheFromSnapshots` preserves `null` remaining instead of coercing via `?? 0`), and window status gap (`getQuotaWindowStatus` returns `remainingPercentage: null` and `reachedThreshold: false`). Tool features strict argparse (`--root`, `--check`/`--dry-run`, `--backup`/`--no-backup`, `--file`), signature-based chunk discovery (`__omnirouteQuotaCacheState`), two-phase write with all-or-nothing validation, and upgrade support. Tests: `tests/test_gateway_quota_patch.py` pins the decision rule on the real chunk fixture `tests/fixtures/quota_cache_chunk_fixture.js` (the node tests run the patch script's output and read every decision from the patched code: single-window, total-less, mixed, multi-window cases, the snapshot reload, byte preservation) and `tests/test_gateway_quota_patch_cli.py` pins command-line behaviour (apply/skip/check/dry-run, backups, strict flags, all-or-nothing validation, half-patched refusal, signature discovery, upgrade from the v1-patched package); a package patched by the v1 script is upgraded, a package patched by the v2 script is NOT upgraded (the script aborts and writes nothing; v2 was never applied live). Docs: `docs/ai/gateway-quota-patch.md`.
- WIN-PROMPT (2026-10-03): on native Windows (`os.name == 'nt'`) `resolve_client_executable` never passes a multi-line or special-character prompt through a `.cmd`/`.bat`/`.ps1` shim (which invokes `cmd.exe` and truncates arguments at the first newline, dropping worker task briefs). The spawner resolves the real entry (first checking sibling `node_modules/@opencode/cli/bin/opencode.exe` for opencode, else parsing the shim for a direct executable or node/python script target across interpreter variants and preserving value-taking and standalone interpreter flags, quoted paths with spaces, and `py` launcher selectors) so `CreateProcess` launches the binary directly without `cmd.exe`. Shims without a resolvable target (including `-e`/`--eval`/`-p`/`-c`/`-m` shims with no script file target) fail closed and refuse spawns loudly (exit code 3, `ClientMissing`) when given multi-line prompts, hazard characters, or when the full command line exceeds ~7000 characters, while safe single-line arguments and POSIX platforms remain byte-identical. Docs: `docs/routing.md`. Proving tests: `tests/test_win_prompt_spawn.py`.
- REVIEWCALL-SEAT / TESTGAMING (2026-10-03): `tools/review-call.py` now appends the verbatim module constant `STANDING_QUESTION` (a blank line, then its own paragraph) to **every** prompt it sends — no CLI flag and no environment variable disables it; the only seam is `compose_prompt(prompt, standing=...)`'s module-level argument — and REQUIRES the answer: `review_test_gaming()` parses one line at line start with the verdict parse's own indent/fence/ANSI helpers (`_indent_columns`, `_FENCE_OPEN_RE`/`_FENCE_CLOSE_RE`, `_ANSI_RE`), first valid line wins, into evidence.json's `test_gaming` key (`no` / `yes` / `unsure` / `missing` / `yes_unverified_quote`, plus `test_gaming_quote` for the `yes` outcomes); a `yes` quote must be a strict verbatim substring of the CALLER's prompt text only (never the appended `STANDING_QUESTION`, so a model cannot satisfy `yes` by quoting the question back at itself), a `no` must carry `searched` and `0 hits` and its scope text may never contain a double quote anywhere (`no - searched [^"]+: 0 hits`, not just reject a wholly-quoted `no`), and any missing/malformed line is exit **5** (stderr names the missing line, evidence.json still written; truncation keeps exit 4, VERDICT parsing is untouched), `TEST-GAMING: <outcome>` printing beside the `VERDICT:` line with `unsure`/`yes_unverified_quote` printed but not counted; `tools/diff-guard.py`'s `R1`-`R5` lines are pasted into the seat prompt as the objective cross-check (guard `diff_guard.py` untouched). Docs: `docs/api-keys.md`. Proving tests: `tests/test_review_call.py` (fake gateway, no network).
- DIFFGUARD (2026-10-03, base `6f873bf`): report-only post-run diff guard (`tools/diff_guard.py` library + `tools/diff-guard.py` CLI) — `python3 tools/diff-guard.py --base <rev> --tip <rev> --scope <glob> [--scope <glob> ...] [--repo <dir>] [--json]` compares two git revisions (`--name-status` + `-U0` with rename detection) and prints one `RULE path:line text` finding per line (text clipped to 160) or a JSON list (`{"rule","path","line","text"}`): R1 a test file (`tests/**`, `test_*.py`, `*_test.py`, `*.Tests.ps1`) changed outside every `--scope` glob, R2 a non-test Python file gained a builtin-basis class defining `__eq__`/`__ne__`/`__hash__`, R3 test-framework references (`pytest`/`unittest`/`PYTEST_CURRENT_TEST`/`importorskip`, `sys.modules`+test, `os.environ` key containing `TEST`) appear in non-test code, R4 a test file gained a skip/xfail marker or removed more assert lines than it added or grew an assert's numeric literal, R5 a test definition was deleted with no identical line re-added, R0 an unparsable tip source is reported as `unparsable` without crashing; exit codes 0 clean / 1 findings / 2 usage-or-git failure (git errors print `diff-guard: ...`, never a silent pass) and the tool never edits anything — heuristics can false-positive, the reviewer judges (known limits: no alias resolution for R2, added-lines-only, basename scope globs). Docs: `docs/ai/diff-guard.md`. Writer rule (TESTGAMING part c): the module constant `WRITER_RULE` in `tools/diff_guard.py` holds the rule as data (never change production behaviour / never edit tests outside the write scope / no builtin-basis equality or hash overrides / no test-runner detection / no special-cased inputs — report the failure instead), `python3 tools/diff-guard.py --writer-rule` prints it verbatim plus one newline and exits 0 **before any other argument is required** (`--base/--tip/--scope` not needed; every other invocation, including the exit-2 usage error, is unchanged), and `docs/ai/writer-contract.md` records the placement (last paragraph of every writer task text; seat prompts carry the standing question of `tools/review-call.py` instead), the post-run step (`--base <start> --tip <end> --scope <write-scope globs>`, paste its `R1`-`R5` lines into every seat prompt, a finding is a candidate to review and never an automatic verdict) plus the plain statement that the spawner's built-in brief does not carry it yet — the follow-up lane adds it in `tools/autoos-agent.py`, `isolate_task_prefix`, untouched here. Proving test: `tests/test_diff_guard.py` (existing cases unedited and green).
- D284-LIFT (2026-10-03, routing D-507, on operator D-505): lift the D-284 spawn-pin hold for exactly omniroute/gemini-3.8-flash (paid tier, $200 hard stop / $180 warn, private-safe per cited paid terms; training-leg classification of this pool is F0's business); all openrouter/google/* pins and other Gemini spellings remain refused.
- FLEET-HOOKS (2026-10-03, P0, base `93ec5450`): `tools/hooks/bash_guard.py` is a Claude Code `PreToolUse` guard for the `Bash` tool - stdlib `python3` only, reads the hook's own JSON on stdin and denies (exit 2, one stderr line) a command that carries either (1) an UNQUOTED heredoc (`<<EOF`/`<<-EOF`; `<<'EOF'`/`<<"EOF"`/`<<\EOF` are quoted and exempt) whose body contains a literal backtick or `$(...)`, or (2) a `claude --bg`/`-p`/`--print` call whose double-quoted argument contains one - the exact shape of the incident this closes: a seat prompt's unquoted heredoc had a backticked `netplan apply` in its prose, and a host running sessions in bypass mode (no classifier) executed it. Everything else allows silently; a malformed hook payload fails OPEN (exit 0, one stderr note) rather than ever blocking a session. The heredoc scanner and the `claude`-call scanner each track quote/`(`/`$(`/`$((` state themselves (no shell is invoked) so neither fires on `echo "a << b"`, `$((1<<3))`, a quoted heredoc delimiter with backticks in its body, a single-quoted or escaped backtick, a bare `$VAR`, or text after a heredoc's terminator line. Proving test: `tests/test_bash_guard.py` (pipe-style: feeds JSON on stdin through a real subprocess, asserts exit code and stderr text over a table of deny/allow cases). Install snippet and behaviour table: `docs/ai/fleet-hooks.md` (settings.json is user config, never written by this repo).
- EVTAG D-426 (2026-10-02, evidence gate, base `9ae2ebc1`): a foreign-repo keyed opencode run reached the gateway with `providers.omniroute.headers` still stamped from the PRE-S4 tag - OR3 stamped them before the sandbox retitle switched the session tag from the AutoOS lane to `<repo>/<title>`, so the call log said `<AutoOS lane>/<title>/<run-id>` while the run record stored `<repo>/<title>` and `tools/seat-model-evidence.py --gateway` (exact match, D-266 H1) refused every row with "no status-200 row carries this seat's session tag"; `build_plan` now re-stamps the same provider-level pair with the record's tag after the retitle, leaving an AutoOS-source overlay byte-identical (`tests/test_fleet_sandbox_agents.py`).
- REVIEWCALL (2026-10-02, operator D-439/D-440/D-441): `tools/review-call.py` is the sanctioned single gateway review call — `python3 tools/autoos_gateway_key.py exec -- python3 tools/review-call.py --model <omniroute model id> --prompt-file <path> --out-dir <dir> [--title <slug>] [--max-tokens N] [--gateway-url URL]` takes the key ONLY from `AUTOOS_OMNIROUTE_KEY` in the env `exec` sets (a missing key is exit 2, `run me via autoos_gateway_key.py exec -- ...`), POSTs one tool-less `chat/completions` (`stream: false`, no `tools`, no temperature by default (`--temperature X` sends one; the central gateway cuts temperature-0 calls at 64 completion tokens), `max_tokens` default 16000, one user message) stamped with the spawner's own `x-omniroute-session-id` (`review/<title or prompt-sha12>/<run-id>`) and `X-AutoOS-Run-Id` header pair so the gateway call log attributes the call, and writes `review.txt` plus `evidence.json` (requested/served model, status, correlation id, response header NAMES only, session tag, run id, prompt sha256, token usage, UTC start/finish) — or `error.json` and exit 3 on a non-200, exit 4 when finish_reason is `length` or the answer is empty — printing only the two paths, the served model and the answer's last `VERDICT:` line (or `VERDICT: missing`); never the key, the request headers or the prompt. Proving test: `tests/test_review_call.py` (fake `http.server` gateway on 127.0.0.1:0, no network). RC-2: tolerant VERDICT parse (D-337 rules; normalised in stdout and evidence.json) and default --max-tokens 16000 (reasoning models used 6.2k on an 11k prompt).
- REVIEWCALL (2026-10-02, operator D-439/D-440/D-441): `tools/review-call.py` is the sanctioned single gateway review call — `python3 tools/autoos_gateway_key.py exec -- python3 tools/review-call.py --model <omniroute model id> --prompt-file <path> --out-dir <dir> [--title <slug>] [--max-tokens N] [--gateway-url URL]` takes the key ONLY from `AUTOOS_OMNIROUTE_KEY` in the env `exec` sets (a missing key is exit 2, `run me via autoos_gateway_key.py exec -- ...`), POSTs one tool-less `chat/completions` (`stream: false`, no `tools`, no temperature by default (`--temperature X` sends one; the central gateway cuts temperature-0 calls at 64 completion tokens), `max_tokens` default 4096, one user message) stamped with the spawner's own `x-omniroute-session-id` (`review/<title or prompt-sha12>/<run-id>`) and `X-AutoOS-Run-Id` header pair so the gateway call log attributes the call, and writes `review.txt` plus `evidence.json` (requested/served model, status, correlation id, response header NAMES only, session tag, run id, prompt sha256, token usage, UTC start/finish) — or `error.json` and exit 3 on a non-200, exit 4 when finish_reason is `length` or the answer is empty — printing only the two paths, the served model and the answer's last `VERDICT:` line (or `VERDICT: missing`); never the key, the request headers or the prompt. Proving test: `tests/test_review_call.py` (fake `http.server` gateway on 127.0.0.1:0, no network).
- `lib/linux/memguard.sh`: a 10 s user timer stops the **newest** *running* agent-run scope at all (never systemd's ambiguous `*` glob match; `list-units --state=active`, so an inactive/dead scope can no longer win "newest" while the real hog runs) only when MemAvailable is below 1 GiB on **two consecutive samples** ≤ 30 s apart (a first low sample only records the verdict, a sample at/above the floor or stale/garbled state breaks the pair; the pair is consumed before the stop is issued and stops only when the consumed pair was durably recorded — fails closed so unwritable state stops nothing and cannot repeat stops across subsequent ticks), any `systemctl` failure or ambiguity is a log line and nothing else (an unknown timestamp for any listed unit or a tie for newest timestamp stops nothing and logs a rate-limited line, failed `stop` logs one rate-limited line, failed `list-units` logs one rate-limited line and stops nothing — never a non-zero exit; `state_write` returns 0 on success and 1 on failure without aborting on unwritable directories), log write failures are swallowed without leaking redirection errors to stderr while stdout/journal output remains (guarded `mkdir -p` and grouped redirection), the stop log is bounded at 256 KiB (soft cap, rotated to one `.1` generation) and written to both the file and stdout for the journal, and the unit's `ExecStart` runs the installed copy `~/.local/share/autoos/memguard.sh` (mode 0755, cmp-checked by `install_memguard`; detection now requires the timer **and** the copy).
- CI-GREEN-HERMETIC (2026-10-02, main run 37000953057, base `3309852`): `tests/test_t2_record_pin.py` and `tests/test_prepush.py` now pass on a runner that has no `opencode` on PATH, no pytest and no git identity — `D284PostPlanTests.setUp` patches `shutil.which` for the client binary only (every other lookup delegates to the real one) so the client pre-check's rc 3 can no longer beat D-284's rc 2, and a plan with a non-empty `pytest` list runs the gate in-process with `prepush.python_executable` pointed at a fixture-side fake interpreter (`PY_FAKE`) that answers `-m pytest -q <files>` the way pytest does (exit 0 + `<n> passed` green, red exit otherwise, 4 for a file that is not there) and `execv`s the real interpreter for every other command; `tools/prepush.py`/`tools/autoos-agent.py` and the gate's own behaviour are untouched.
- KEYEXEC (2026-10-02, operator D-370): `tools/autoos_gateway_key.py` gained an `exec` subcommand — `autoos_gateway_key.py exec [--optional] [--no-notice] [KEYS_FILE] -- <cmd> [args...]` resolves the OmniRoute client key by the one `resolve` precedence (same keys-file default, same notices on stderr, same `--no-notice`) and then runs `<cmd>` with `AUTOOS_OMNIROUTE_KEY` in the CHILD's environment ONLY — never printed, never in argv, never written to any file; POSIX replaces this process with `os.execvpe` (so the exit code is the child's), Windows uses `subprocess.run` and exits with the child's return code; a missing key exits 1 with the existing message unless `--optional` (the child then runs WITHOUT the variable), and a missing `--` or an empty command is a usage error on stderr, exit 2. Proving test: `tests/test_gateway_key_exec.py`.
- AI-STACK-ZCODE / task D2 (2026-10-02, base `2de54502`): the OmniRoute gateway container (`omniroute` / `autoos-omniroute`, host uid:gid, `HOME=/home/qoder`, read-only root fs) can now run the ZCode remote-server bundle the ZCode desktop app installs on the host in `$HOME/.zcode/server` — a new optional override `configuration/docker/ai-stack/compose.zcode.yml` adds exactly one volume (`${AUTOOS_ZCODE_DIR}:/home/qoder/.zcode:${AUTOOS_ZCODE_MOUNT_MODE:-ro}`) to `services.omniroute` and touches nothing else, so `opencode`/`openhands` never mount a directory that also holds the ZCode login. `ai-stack.sh` (`zcode_dir`/`zcode_decide`, called by `dc()`) passes the override as a second `-f` and exports the validated values for that compose call **only** while `<dir>/server/zcode-server.cjs` is a regular file: a host without ZCode keeps the single `-f` call and `up` behaves exactly as before; an `AUTOOS_ZCODE_DIR` that is relative, colon- or newline-bearing skips the override with one warning line, a `AUTOOS_ZCODE_MOUNT_MODE` that is not `ro`/`rw` mounts read-only with one warning line. Proving tests: cases (a)–(f) in `tests/linux/34-ai-services.sh` (two `-f` flags, single `-f` without the bundle, bad mode, bad dir, static `compose.zcode.yml` shape plus the `.zcode` grep across `configuration/docker/ai-stack`, `check_compose.py` on `compose.yml` unchanged); docs in `docs/web-services.md` ("OmniRoute with ZCode (GLM)").
- TAILDROP-SORT D-410 (2026-10-02, base `b361888`): `catalog/linux.json` gains the `taildrop-sort` entry (network category, provider `script`, requires `tailscale`, profiles workstation/ai-coding/server), the shape `claude-autostart` already uses, so the one-per-host fetcher already wired at `install.sh` dispatch `taildrop-sort)` and `detect.sh` `script_is_installed` is selectable from the menu; `tests/linux/43-taildrop-sort.sh` drives `lib/linux/taildrop-sort.sh` with a fake `tailscale` on PATH plus `AUTOOS_TAILDROP_ROOT` — owner prefix filed (`autoos__x.txt` → `autoos/x.txt`), unprefixed → `unsorted/` with exactly one UTC-stamp `sort.log` line, a second delivery becomes `name.1`, `..__x`/`a__b/c` cannot leave the root, no `tailscale` → exit 0, and a comment-stripped static gate on `cat|head|tail|sha256sum`, then `install_taildrop_sort` twice against a scratch home (second run `TDS_RESULT skipped 0`, four `is already current` lines, no backup, mode 0755, both units); part `43` joins shard `d` in `tests/ci-shards.txt`, docs section next to the Claude autostart one in `docs/web-ui.md`.
- DEEPINFRA-WIRE-MIN (2026-10-02, operator D-366/D-381, base `3d3633d9`): DeepInfra's `XiaomiMiMo/MiMo-V2.6-Flash` is usable as an opencode writer pin `omniroute/deepinfra-mimo-v2.6-flash` (hand entry in `opencode.jsonc`; modelID `deepinfra/XiaomiMiMo/MiMo-V2.6-Flash`) with a registry model row (family xiaomi, `tool_calls` unproven, price unsourced at 0) and `providers.deepinfra.monthly_cap_source` recording the $5 prepaid / auto top-up removed hard stop; on no route's legs, never `-clean` (trains_on_prompts unverified), never Claude (covered by `deny-claude-paid-api`); guarded by `tests/test_deepinfra_wire_min.py`.
- FLEET-AGENTS-2 (2026-10-02, lane `fleet-agents-2`, base `3d3633d9`): a foreign-repo sandbox's overlay now also carries ROOT's `providers` block (env var names only, merged per provider with the per-run headers and stamped `settings.baseURL` winning, one level deep) and ROOT's top-level `permissions` fence placed first, so the spawn-gate rules stay last — fixes `Model unavailable: omniroute/t2-worker` after FLEET-AGENTS; an AutoOS-source overlay is unchanged (`root_overlay_blocks`, `merge_root_providers`, `tests/test_fleet_sandbox_agents.py`).
- D-287 (2026-10-02, lane DEBTCAP-T2 NEBIUS-OUT, base `4e8b9eca`): `providers.nebius` set `available: false` with the exact reason `D-287 operator: debt-capable billing (incurred debt), excluded from free/trial tiers` recorded in the provider's `$comment` (the schema's `$defs.provider` is `additionalProperties: false`, so no separate `unavailable_reason` key is allowed) — nebius is debt-capable and may serve no free/trial combo or route; the provider row and its three `zai-org/GLM-5.1/5.2/5.3-Flash` model rows stay registered for pricing history, combos already carry no `nebius/*` leg (NEBREMOVAL), and all five renderer `--check` gates pass unchanged. Proving test: `tests/test_debtcap_nebius.py`.
- T2-RECORD-PIN (2026-10-02, lane `t2-record-pin`, base `4e8b9eca`): an explicit model pin is honoured or refused, never swapped — `--free --model X` launches X (disagreeing pins refuse with `model_mismatch requested=… launched=…`), the plan and the worker record carry `requested_model`/`launched_model`/`family`, a bare `or-*` pin is qualified with the single opencode.jsonc provider that declares it, review-only tier 3 refuses an implement task (`review-only tier 3 cannot run an implement task`, rc 2 / MCP state `rejected`; preview only announces it), and D-284's `gemini-3.8-flash` (any prefix/variant) plus every `openrouter/google/` pin are refused until stage 2. Proving tests: `tests/test_t2_record_pin.py`.
- FLEET-AGENTS (2026-10-02, lane `fleet-agents`, base `d79438b2`): foreign-repo sandboxes now always get the AutoOS agent definitions via the config overlay, with any per-run per-agent keys in the overlay layered on top (per-agent dict merge: definition first, overlay keys win). An AutoOS-source run continues to behave exactly as before (no injected block). Proving tests: `tests/test_fleet_sandbox_agents.py`.
- T1-CLEAN-4 (2026-10-01, lane, base `9e5ea48c`; operator D-189 / DECISIONS 4.3): privacy + gateway-tier findings from the final cross-family seat.
  - **K2 `credit` admission is now enforced, not just documented** (`tools/registry.py`): the schema and `private_safe()`'s docstring both said a `credit` leg is admitted *because* its provider cites a `privacy` block, while the only code check was `trains_on_prompts is False` — a credit row that merely asserted the flag read as clean. `private_safe()` now gates tier `credit` on the same `_privacy_block_problems()` predicate `check_registry()` reports with (one home, so the gate and the report cannot disagree), and a `credit` provider with `trains_on_prompts: false` and no/invalid block is a validate failure. Proving tests: `CreditPrivacyEvidenceGateTests`.
  - **K1** `CreditSpendGuardTests.test_a_credit_tier_is_not_the_free_tier` re-stated for the new contract: `morph`/`deepinfra`/`together_ai` stay unsafe with reason `trains on prompts` (their `trains_on_prompts` is null — unknown, not a tier defect), `vertex_ai`/`ovhcloud` are safe through their cited blocks.
  - **K3** `ResolveLegFoldTests` pins `resolve_leg()`'s case-fold fallback (exact key wins, an ambiguous fold raises, one fold match resolves); the mutant `len(folded) >= 1` is now killed instead of silently picking.
  - **Rework 1** (cross-family seat FAIL, 3 MAJOR + 3 MINOR): K2's "single predicate" was false across the tier-override axis — `_cited_privacy_block_required()` now decides it for both `private_safe()` and `_privacy_block_problems()`, fail-closed on either the leg's effective tier or the provider's tier being `credit`, and `_check_privacy_evidence()` re-reads every route leg with its effective tier so a model-level override cannot be clean at the gate and invisible to the report; `SKIPPED_BY_TIER` is cleared and owned by each entry point (`combos_refs`, `registry_refs`, `render_litellm_blocks`) instead of accumulating across calls in one process; the `--combos` declared-auth drop is tested and its mutant killed, and a combo emptied by that drop is named by `unmanaged_skip_notices()` rather than rendering nowhere; the ovhcloud `$comment` states the shipped rule (a provider that declares `litellm_auth`), not the rejected one. Proving tests: `CreditPrivacyEvidenceGateTests`, `CombosDeclaredAuthSkipTests`.
  - **K4** `providers.vertex_ai.privacy`: the `ai.google.dev/gemini-api/terms` row quoted the **AI Studio unpaid-tier** training clause under a `trains_on_prompts: false` block — true of that surface, not of Vertex — so it is a labelled caveat now (`AI Studio unpaid tier contrast (not this provider)`, quote verbatim) and the two Vertex rows that would not re-fetch say `quote not re-verified (page rendered as a nav index)`. Nothing was re-worded or invented; rule 5 keeps dates out of values, so the day lives in each row's own `accessed`.
  - **K6** LiteLLM never renders a key a provider does not have (`tools/sync-router-tiers.py`, `configuration/litellm/config.yaml`, schema): `providers.vertex_ai` authenticates from a GCP service-account JSON, yet every block carrying a vertex leg rendered `api_key: os.environ/VERTEX_API_KEY` — a guessed name for a variable that does not exist (the unset-key hazard `GATEWAY_ONLY` exists to avoid, the META_API_KEY lesson). The new optional `providers.<id>.litellm_auth` declares it; the leg is dropped and **named** by a rendered `# litellm-skip:` line produced by `render_block()` itself, so the writer and `render litellm --check` cannot drift. The gate is deliberately NOT "`litellm_env` is null": measured, `deepinfra`/`samba`/`scaleway`/`morph`/`ovhcloud` all leave `litellm_env` null and rely on the conventional `<PROVIDER>_API_KEY` fallback, so that rule would have silently gutted legs LiteLLM does address. `vertex-gemini-3.8-flash` lost its only leg and its managed block is pruned, which `litellm_diff()`'s stale-group rule requires. Test: `VertexNoKeyLitellmSkipTests` (plus a guard that `ovhcloud/gpt-oss-120b` still renders).
  - **K7 `ovh/Qwen3-Coder-30B-A3B-Instruct` out of `t2-worker`/`t3-driver` was deliberate** (`35148c5c`: "stale … marked unavailable_legs"; the registry row reads *not in the OVH AI Endpoints catalog 2026-10-01 (measured)*), so nothing is restored — an unavailable leg is dropped by `gateway_legs()` and the rendered surfaces follow. Flagged, not fixed: `models.Qwen3-Coder-30B-A3B-Instruct` still carries OVH `price_in 7e-08`/`price_out 2.6e-07`, which contradicts the "no price on file" wording of its own gate.
  - **K8** the T1-CLEAN-3 case-merge dropped values are recorded in the merged rows' `$comment` (no provider-scoped price/context field exists yet): `mistral-small-3.2-24b-instruct-2506` lost OVH's `1e-07`/`3.1e-07` and its `price_source`, `qwen/qwen3.8-27b` kept groq's `131072`/`16384` over huggingface's `128000`/`32768` — both re-verified against `9e5ea48c^`.
  - **Out of scope, pinned**: `CleanCreditHeadPricedTests` records that the clean routes' credit heads (`ovhcloud/gpt-oss-120b`, `vertex/gemini-3.8-flash`) are `credit_leg_priced == False` today — priced by T1-CREDIT `provider_prices` at train integration.
  - **Investigated (no fix)**: a plan line `credit exhausted ovhcloud $0.00/$200.00` is not measured spend. `tools/autoos-agent.py:plan_credit_guards()` wraps the gateway usage read in `except Exception` and fails closed via `_credit_guards_unreadable()`, which returns `state: "refuse"` with `spend_usd: 0.0` and the registry cap; `tools/autoos_resolver.py:1065` prints only state/spend/cap, so "unreadable, no spend data" renders as "exhausted at zero". The guard's own `note` field carries the truth and the resolver never prints it.
- T2-ISOLATE-SECRETS-4 (2026-10-01, lane `t2-isolate-secrets-4`, cross-family FAIL round on the `-3` tip):
  **R6** `_isolate_materialise` refuses a *final* component that is already a symlink or any
  non-regular file before opening it, and lets the first entry win a path two entries claim — the
  old code vetted ancestors only and leaned on `O_NOFOLLOW`, which is `0` where the attribute does
  not exist (Windows), so `[(120000,"evil"),(100644,"evil")]` or a planted `evil -> outside` wrote
  straight through the link. **R7** `os.makedirs`/`os.unlink` are guarded: a tracked name that
  collides with what an earlier entry created (`a/x` then a symlink `a` → `IsADirectoryError`, `p`
  then `p/q` → `FileExistsError`) is one skipped entry with one stderr line, not an aborted clone.
  **R8** the refusal reasons are constants (`could not create the link` / `could not open for
  writing`) instead of `"link: %s" % exc`, which echoed an OSError's text — potentially the refused
  bytes — to the terminal and the log; `_isolate_link_inside` now explicitly refuses an empty,
  backslash-only or NUL-containing target (a NUL reached `os.symlink` and raised `ValueError`, no
  OSError, killing the whole run). **R9** the canary scan gained `_escaping_links`: `_bytes_hits`
  walks without `followlinks`, so an escaping *directory* symlink inside a sandbox passed assertion
  (1) while leaving the host readable through it; the scan now fails on any symlink, file or
  directory, whose realpath leaves the sandbox, with a hand-built negative control. **R10** the
  remaining `git clone --local` wording in `lib/agent_harness.py` and the unattended-orchestration
  skill now says what the sandbox is: a fresh repo holding one base commit of the allowed committed
  files. 10 new tests in `T2IsolateSecretsS5PathSafetyTests` / `S5CanaryTests` (49 across the lane,
  from 39).
- T2-ISOLATE-SECRETS-3 (2026-10-01, lane `t2-isolate-secrets-3`, cross-family FAIL round):
  **R1** `_isolate_materialise` no longer trusts a tracked *name* — a new
  `_isolate_safe_path` refuses `..`, empty / `.` components, a leading `/`, a NUL,
  a drive- or UNC-shaped name and any component spelling `.git`;
  `_isolate_link_inside` refuses a tracked symlink whose target is absolute or
  climbs out of the tree; `_isolate_clear_below` refuses a write whose ancestor
  below the sandbox is a symlink or whose realpath parent is outside the sandbox.
  An unsafe entry is skipped with one stderr line naming the path only, never its
  content. **R2** the isolation claim is now stated and canary-tested: an
  untracked, git-ignored fake key file in a checkout and in a `worktree` of it is
  unreachable from either sandbox (`ISOLATION STATEMENT:` in `isolate_clone` names
  what *is* isolated — the sandbox tree and its own `.git` for every --isolate
  worker and reviewer — and what is *not*: an absolute path read outside the
  sandbox, in-session Claude Code subagents, and any run without --isolate).
  **R3** the dry-run plan says *one-commit materialisation of allowed HEAD files*
  where it still said `git clone --local` (tests, docs/handoff.md included).
  **R4/R5** the `.agentignore` negation drop and the cone-reconstruction fallback
  of the sparse filter are covered by tests.
- T2-ISOLATE-SECRETS (2026-10-01, lane `t2-isolate-secrets-2`, review round 2): `--isolate` no longer builds its sandbox with `git archive HEAD -- <paths>`. HEAD is now **materialised in Python** — `ls-tree -r -z --full-tree` names the entries, the allow filter is applied to names already in hand, and ONE `git cat-file --batch` streams their bytes into a fresh `git init` with a single `sandbox base (source <sha>)` commit (100755 keeps its exec bit, 120000 becomes a symlink that is never followed, 160000 gitlinks are skipped). That removes the whole class of failure the pathspec list carried: a tracked file named `*` or `secrets-*` is no longer re-read as a glob that pulls `secrets-generated/` back in (I1), 30k tracked files no longer overflow ARG_MAX (I2), and `.gitattributes` `export-ignore`/`export-subst` no longer silently rewrites or drops files (I3). Around it: `.agentignore` matches with real gitignore semantics instead of basename equality (I4), blobs are read as bytes so a binary secret cannot leak its own value through a `UnicodeDecodeError` traceback (I5), cone **and** non-cone sparse sources are honoured by asking git's own index (skip-worktree) rather than guessing from patterns (I6/I14), `*.example`-shaped exemption and secret-name matching are no longer fooled by `api.example.com.key`, `ID_RSA`, `.ENV` or a `.pub` (I7/I8), a SOPS blob must actually be SOPS — column-0 `sops:`, a top-level JSON `"sops"` key, or every value an `ENC[AES256_GCM,data:` (I9), `secrets-generated` is excluded as a path component at any depth and any case (I10), a build that fails half way leaves no sandbox directory behind (I11), the plaintext-secret refusal runs before `~/fleet/sandboxes/<repo>/` is created (I12), and the take-it hint names the cherry-pick range of the sandbox base, because the base shares no history with the source (I13). 28 tests in `tests/test_autoos_spawner.py` (`T2IsolateSecrets*`).
- T0-PAID-5 (2026-10-01): paid reviewer/leg only when no free/trial/credit one is usable — a ghost `policy.reviewers` leg reads as unknown (`leg <id> unresolvable`) instead of crashing the plan, unreadable tiers fail closed to paid/last-resort on both the reviewer walk and the route holdback, fenced legs read as skipped (never a last-resort cause), and the reviewer flag-vs-tier validation is two-directional.
- T0-FREEZE-4 (2026-10-01): `ready` gains a sixth gate, `main-ci-red` — the latest completed push run of the CI workflow on `main` must conclude `success` (exit 1 while red, exit 2 when `gh` is unreadable, so every non-waived lane now needs an authenticated `gh`). The lane that fixes main itself waives the gate with `--fixes-main` plus `AUTOOS_FIXES_MAIN=<lane>@<sha>`; a bare or mismatching waiver is refused as `waiver-not-declared` (exit 1), and a waiver lane carrying a quote, backslash, whitespace or control character is refused before it reaches the `fixes_main="<lane>@<sha>"` ready-line field.
- CLAUDEBUDGET-OFF (2026-10-01, operator via routing-00): `catalog/ai-registry.json` `policy.claude_budget` is `mode: normal`, `weekly_share_left: 0.9` — the weekly Claude limit reset and the operator turned budget gating **OFF** (D-102; source carries the date). With the gate off, the two doors it held are open again: `--client claude` / the MCP `spawn` `client: claude`, any `--model` that resolves to a Claude leg (e.g. `sonnet`, `agy`'s default `claude-opus-*`), and the cross-family reviewer selection may pick a Claude leg for any card — a non-final implement/review/research card is no longer held for finals. **Nothing re-arms the gate automatically**: `weekly_share_left` is the operator's own estimate, there is no Claude usage endpoint here, so if the 0.9 share turns out wrong the fix is an operator edit of `mode` back to `budget` (or a share below `budget_below`), not a timer. Tests that proved the ON behaviour now supply their own budget-ON registry copy; `test_the_shipped_value_turns_budget_mode_off` pins the shipped OFF value.
- T1-CREDIT-FIX-16 (2026-10-02, D-274): never govern from old data - FRESH only when stamp >= previous latest and in-month (equal fresh; old/past-month appended never latest never clearing, that provider STALE old-reading); future stales THAT provider only; bad-since fails closed unknown; per-mark keeps global since, reason names oldest; helper-fallback crash fail-closed via refuse_paid_on_overlay_error TYPE-only, credit via guard-error; R1 rework: present-but-old with no series stales (D-274). Gates: test_t1_credit_fix16.py 17 OK.
- T1-CREDIT-FIX-17 (2026-10-02): money-guard follow-ups + train wiring. (1) plan_credit_guards: helper-branch re-assert and unforeseen-bug path calls to refuse_paid_on_overlay_error now wrapped fail-closed (TYPE-only, paid refuses, no crash). (2) overlay_balance_guards: passes unfiltered `readings` (not `usable`) to _mark_missing_provider_reads so a paid entry with garbage/missing fetchedAt is present-but-unusable and stales per-provider (MAJOR-1). (3) _mark_missing_provider_reads: paid provider present-but-unusable (garbage/missing stamp, no series) now stales per-provider; credit unchanged. (4) Month-start boundary test: stamp exactly at UTC month start is fresh, one second before is past-month. (5) Removed unused _future_stale variable. (6) Fixed stale comment in test_t1_credit_fix10.py. (7) Added test_t1_credit_fix17.py with end-to-end tests through overlay_balance_guards (present-but-unusable) and MINOR-3 test for overlay-except wrap block (monkeypatches refuse_paid_on_overlay_error to raise, asserts paid refused + no crash). (8) Wired test_t1_credit_fix16.py, test_t1_credit_fix17.py and test_debtcap_nebius.py into linux/33-documentation.sh and run-tests.ps1. Gates: test_t1_credit_fix16.py 22 OK, test_t1_credit_fix17.py 4 OK, test_debtcap_nebius.py 10 OK, test_suite_wiring.py passes.
- T1-CREDIT-FIX-14 (2026-10-02, D-274): **stale ledger fails closed** - any failed balance read (helper error, non-200, unparseable/non-dict body, empty readings) marks the git-ignored balance ledger STALE (first failure's `since` kept; `load_balance_readings`/`balance_month_spend` never see the marker) and every `tier == paid` guard refuses with `balance stale since <ts> (D-274)` (STALE beats the D-240 local estimate; ledger write OSError refuses too) until the next successful read clears it; a `fetched_at` past now + `BALANCE_CLOCK_SKEW_S` (300 s) is future-dated stale, never fresh/latest; the resolver surfaces the stale reason verbatim in the route explain line. Rework 1: **M1** ledger readers use `errors='replace'` (non-UTF-8 bytes skipped like bad lines, never raise) and ANY overlay exception refuses paid with `balance overlay failed (<Type>) (D-274)` (type only); **M2** the ledger is consulted outside the rows gate (helper down or a persisted marker refuses paid with the stable `since`, STALE beats D-240; credit fail-open untouched); **M3** STALE is per provider (a parse missing a series provider stales only it until that provider reads; old global markers still stale everyone); clear-marker OSError stays refuse; `fetched_at` == now + skew is fresh, +1 s stale; far-future stamps keep refusing until they age into the past. Rework 2: **MAJOR-1** only a STAMPED reading counts as read (missing/garbage `fetchedAt` never shields its provider; an all-unstamped payload is an empty payload -- STALE, never clearing); **MINOR-2** a raising `_mark_balance_stale`/refuse helper refuses paid with the TYPE named, never crashes the plan; **MINOR-3** the unforeseen-bug guard-error path refuses paid too (D-274; credit fail-open untouched); **MINOR-4** the since-kept and per-provider marker tests run the retry an hour later and seed-then-clear. Rework 3: **MAJOR** a persisted per-provider marker is honoured on the overlay SUCCESS path even after that provider's in-month series ages out (month rollover -- only that provider's own usable reading clears it, ORIGINAL since kept); **MINOR-2** the all-unstamped retry runs an hour later so a clear-then-re-mark mutant moves the stamp; **MINOR-3** one credit-stays-fail-open assertion per fail-closed path (overlay error, no-balance-read, overlay stale). Gates: `tests/test_t1_credit_fix14.py` 41 OK, `tests/test_t1_credit_fix10.py` 54 OK.
- T1-CREDIT-FIX-13 (2026-10-02, D-269): **helper path whitelist build** - `helper_fetch` accepts ONLY the two fixed paths the code builds (`/api/usage/call-logs` with exactly `limit=<int>&offset=<int>&excludeTests=1`, `/api/usage/provider-limits` with no query) and BUILDs the forwarded path+query from whitelist constants plus strictly validated ints (`^[0-9]{1,9}$`, `excludeTests` exactly `1`), never from the caller string; any other path, any `%`, control/DEL/non-ASCII, backslash, `#`, `;`, bare `?`, or extra/duplicate/bad query key is refused with a type-name-only `UsageError` before any subprocess runs (three existing call-logs URLs gained the `&excludeTests=1` they were missing). Gates: `tests/test_t1_credit_fix10.py` 54 OK.
- T1-CREDIT-FIX-12 (2026-10-01, D-240/D-253): **N1 money** - a D-240 local-estimate guard now carries `local_estimate: True` (`apply_paid_local_cap`) and ranks as UNKNOWN in the `overlay_balance_guards` merge, so a MEASURED balance guard wins alone (its spend and note govern); a REAL measured ledger refuse/warn still beats a flat balance ok (R2 stands), and with no balance series the D-240 refuse is unchanged. **N2** - `helper_fetch` also rejects `\`, `?` and `#` in the decoded path (still only `/api/usage/...`). **N3** - `load_balance_readings` docstring no longer claims a malformed-line count (it returns the surviving list; callers assert on that), and the overlay-survives-bad-lines test pins the exact guard state. Gates: `tests/test_t1_credit_fix10.py` 44 OK, `tests/test_t1_credit_fix2.py` 40 OK, `tests/test_t1_credit_fix6.py` 15 OK, `tests/test_t1_credit_fix7.py` 56 OK, `tests/test_autoos_usage.py` 110 OK, `tests/test_autoos_resolver.py` 335 with only the 9 pre-existing failures, `tests/test_registry.py` 315 OK, `registry.py validate` ok.
- T1-CREDIT-FIX-11 (2026-10-01, D-255/D-256/D-253): **R1** `gemini-3.1-pro-preview` stays as a DENIED registry row (`policy.leg_rules deny-gemini-3.1-pro-preview`, D-255; `provider_prices.vertex_ai` kept for history D-256, unselectable in every route/leg/spawn path). **R2** `overlay_balance_guards` takes the worse of ledger and balance (refuse>warn>ok>unknown), spend is the max, note names both sources. **R3** `record_balance_readings` dedupes within the same batch. **R4** `helper_fetch` rejects `..` and any non-`/api/usage/` path after normalisation with type-name-only errors. **R5** 10% cache price on `deepseek/deepseek-v4.1-flash`, `deepseek-v4-flash`, `meta/muse-spark-1.3-contributor`, `muse-spark` (golden updated). **R6** `load_balance_readings` skips non-numeric/non-finite `remaining`, never crashing the overlay.
- T1-CREDIT-FIX-7 (2026-10-01, D-220): closes the final Sonnet review of `12d19789` (CREDIT-FIX-6); the two card-backed trial grants (vertex_ai $250, ovhcloud $200) stay **fail-open** while spend is unmeasurable. **R1 money** - `is_spend_row` matches the registry id, `omniroute_id` and `model_prefix` plus `<namespace>/`-prefixed models (all read from the registry, never hardcoded), so gateway spellings count: `vertex`/`vertex/...` rows bill vertex_ai and a 100M-in/30M-out `vertex/gemini-3.8-flash` row now refuses at $375 instead of reading ok/$0; `ovh/...` rows bill ovhcloud the same way; bare AI-Studio rows stay $0 and lookalike namespaces (`vertexish`) match nothing. **R2 window** - new optional `providers.<id>.credit_started` (YYYY-MM-DD, real calendar date, not in the future; `catalog/ai-registry.schema.json` + `registry.py check` enforce it): spend is measured from the grant start when present, else month-to-date with a loud `(window: month-to-date only; set credit_started)` on every guard note and loud line; vertex_ai/ovhcloud carry no date yet (operator input needed). **R3 limit, documented not built** - the hard stop filters resolver plans ONLY; `registry.gateway_legs` and the rendered gateway combos do no credit gating, so a refused grant stays a gateway fall-through leg at run time (D-220 follow-up: gateway-side credit gate T1-COMBO3; `tests/test_t1_credit_fix7.py` pins the current ungated behaviour). **R4** - nan/inf spend reads `unknown` (nan used to read `ok`), `_as_int` is total (non-finite/huge -> 0, never raises), unreadable rows are skipped and counted (`rows_unreadable`) instead of aborting the map, and an unforeseen per-grant bug degrades to single-grant `unknown` instead of map-wide `guard error` (a grant that cannot state its cap still raises: CLI exit 3, plan `unknown`). **R5** - the `SPEND UNKNOWN: <provider> credit leg kept` loud line is emitted only for legs that survive every other filter (it used to claim `kept` for legs then skipped `tool_calls: unproven`). **R6** - the `credit exhausted <provider> $spent/$threshold` reason names the EFFECTIVE threshold (grant minus margin: vertex_ai $230, ovhcloud $180 at the default $20 `credit_hard_stop_margin_usd`), not the cap. Vertex pricing state: `models.gemini-3.8-flash.provider_prices.vertex_ai` at list $1.50/$7.50 per 1M tokens (official pricing page https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing, accessed 2026-10-01; intro $0.75/$3.75 through 2026-12-31), free legs stay price 0. FIX-5 M1 (dated manual figure expires after 7 days, reads `manual` never `ok`), M2 (loud line), M3 (margin default $20), M5 (refuse-before-unknown, unrecognised states named) stand. Gates: `tests/test_t1_credit_fix2.py` 40 OK, `tests/test_t1_credit_fix6.py` 15 OK, `tests/test_autoos_usage.py` 110 OK, `tests/test_t1_credit_fix7.py` 16 OK, `tests/test_autoos_resolver.py` 335 with only the 9 pre-existing failures, `tests/test_registry.py` 311 OK, `registry.py validate` ok. T1-CREDIT-FIX-8 (2026-10-01, D-220): **C1 window fetched** - new single-home `autoos_usage.fetch_cutoff` (min of month-start and the earliest usable `credit_started` over `credit` grants), read by both fetch callers (`plan_credit_guards`, usage CLI); a 2026-09-01 grant's September rows now count on every page size, while each grant is still filtered by its own window (paid stays month-to-date). **C2 money** - the model-namespace branch of `is_spend_row` applies only when the row names no provider of its own; an `openrouter`/`opencode-zen` row for `deepseek/...` no longer bills DeepSeek. **C3 unknown, not silent** - negative token counts are unreadable (never subtract); any `rows_unreadable` makes that grant's guard `unknown` naming the count (fail open for credit, kept for paid), while measured spend at/over the hard stop still refuses. **C4** - the quiet `credit spend unknown ... leg kept` line is stashed like the loud one and emitted only for surviving legs. T1-CREDIT-FIX-9 (2026-10-01, D-220/D-212): **T1 truncated** - `fetch_window`'s `truncated` is threaded into `credit_guards`/`_paid_guards_measured` (both callers); an incomplete fetch reads `unknown` naming `fetch truncated at page cap` (fail open for credit, kept for paid), measured spend over the stop still refuses. **T2** - window-limited ok/warn survivors emit `<provider> credit spend measured month-to-date only (set credit_started)` in the route explain and the report. **T3** - unparseable string token values, list fields and non-dict `tokens` are unreadable (numeric strings stay readable). **T4** - the paid block prints `N unreadable row(s) skipped`. T1-CREDIT-FIX-10 M1 (2026-10-01, D-250): **real meter without the manage key** - new second fetch transport `autoos_usage.helper_fetch` (read-only `GET /api/usage/call-logs` through the gateway's CLI helper inside the container, machine-derived loopback token, GET only, never reads/prints/passes any key); `plan_credit_guards` tries the manage-key HTTP fetch first (key non-empty + 200, note `measured via manage key`), then the helper when docker and the container exist (note `measured via gateway helper`), else the unknown/D-240 fallback (`local estimate (D-240)`). T1-CREDIT-FIX-10 M2 (2026-10-01, D-240): **paid deepseek stays last resort under a local cap while unmeasured** - new registry `policy.paid_local_cap_usd` (default 20, schema + `registry.py check`); an `unknown` paid guard is refused with `paid spend unmeasured - local estimate $X >= $20 cap (D-240)` when the local estimate over fetched rows reaches the cap, else kept with `paid spend unmeasured - leg kept, local estimate $X of $20 (D-240)` (tokenless rows add $0, counted as `unpriced runs: N`); measured spend still governs. Gates: `tests/test_t1_credit_fix10.py` 30 OK.
- TRIALDIRECT-D (2026-10-01, lane trial-direct, gateway client config): three `ovh-direct-*` **hand entries** added to `opencode.jsonc` outside every `AUTOOS-MANAGED` region — `ovh/gpt-oss-120b`, `ovh/Qwen3-Coder-30B-A3B-Instruct`, `ovh/Qwen3.8-27B` (the gateway's own `ovh/*` spelling; `model_prefix` makes them `ovhcloud/*` registry legs), limits from the registry rows (131072/32768, 128000/32768, 128000/32768), named for trial credit and public work only. **The spawn gate now prices a hand entry** (D5): `spawn_spends_claude` used to refuse every one of them with `cannot be priced -- the model ... is no route the registry carries`, because a passthrough is no combo; `hand_entry_leg()` reads the entry's `modelID` back through `registry.registry_ref()` and prices the run off that leg's model row — non-Claude when the family is not anthropic, **Claude when it is**, and still refused when the id resolves to nothing (`omniroute/no-such-hand-entry`, and `vertex-3.6/3.7-flash` until their registry rows land — reported as a registry input). **Operator D-255 is enforced in code**: `gemini_model_allowed()` allows only Gemini 3.6 / 3.7 / 3.8 Flash (variants included) and refuses every Pro model and every other version, checked at the spawn door over the model name, the hand entry's `modelID`, the leg it resolves to, and a combo's legs under the all-off-list rule below; `vertex-pro`, `vertex-flash`, `vertex-flash-lite` and `gemini-2.5-flash` are gone from the hand entries and `vertex-3.6-flash` / `vertex-3.7-flash` replace them alongside `vertex-3.8-flash`. A router-picked combo is refused only when *no* leg is on the list, so the two registry routes that still carry one off-list fall-through leg (`t2-worker`, `gemini-3.8-flash` → `deepinfra/google/gemini-3.1-flash-lite` / `-3.5-flash`) do not bench every tier-2 spawn — that data is reported for the orchestrator, not edited here. `privacy=sensitive` stays refused on `ovh-direct-*` (PRIV3: nothing proves an unrouted id private-safe; `providers.ovhcloud.trains_on_prompts` is null). **D8**: `GEMINI_MODEL` is pinned in the child env of every `gemini` spawn (added to the plan passlist), because gemini-cli re-resolves its model inside the process and its own bundled docs say the fallback default is `auto` → `gemini-3-pro-preview` / `gemini-3-flash-preview` — measured as an unrouted `openrouter/google/gemini-3-flash-preview` side call answering HTTP 402 during a seat. Gates: `sync-ide-models.py --check` 0; `tests/test_audit_router_registry.py` 0; `test_gateway_selection.py` 0; `test_autoos_spawner.py` new `HandEntryPricingTests` 8 / `GeminiAllowListTests` 11 / `SensitiveHandEntryPrivacyTests` 2 / `GeminiSideModelPinTests` 4 all OK, and the regression set (`ModelOverridePrivacyTests` `RunCardV2PrivacyTests` `ClientCommandTests` `RoutingTableTests` `ClaudeBudget*`) 113 run, 1 error pre-existing at the branch point (`opus-4-6` is no declared model in `opencode.jsonc`; reproduced on a clean `HEAD~1` export). `test_sync_ide_models.py` unchanged at its 2 pre-existing `variants`-block failures. See `docs/models.md` "Hand entries".
  - RWP2 S1 (2026-10-01, rework 2): a combo named with `--model` is treated exactly like a router-picked one — refused only when EVERY leg is off-list — because `t2-worker`'s one dead-end fall-through leg made the door refuse `--client qwen --model omniroute/t2-worker` outright, so `ReviewFindingTests.test_gateway_client_model_override_wins_over_the_card` printed nothing (new red on the lane, green on base 75af3236; the RWP claim that the failing set equalled the baseline was wrong). D-255 is thus enforced at the spawn door for what a run is *aimed at* (model name, hand-entry `modelID`, resolved leg, all-off-list combo), while an off-list fall-through leg inside a combo is registry data the operator must remove — reported by the refusal text, not edited here.
  - RWP1 (2026-10-01, rework 1): the registry half the hand entries were waiting on — `models.gemini-3.6-flash` and `models.gemini-3.7-flash` added (80 → **82 models**), shaped like `models.gemini-3.8-flash` (family google, `effort_ladder` low/medium/high, `reasoning` true, `tool_calls` **unproven** — no probe from this lane, `context_advertised` 1048576 / `output_max` 65536 copied, never enlarged). **Price NOT sourced:** the Vertex AI pricing page was unreachable here (no network), so no accessed date is recorded and no figure is invented — `price_in`/`price_out` stay `0.0`, the same unpriced form `gemini-3.8-flash` and its `google/gemini-3.8-flash` twin use, and each `$comment` says the Vertex cost is paid by `providers.vertex_ai` **trial credit** and is priced by **T1-CREDIT `provider_prices`** at train integration. Nothing else in the registry moved: the off-list route legs (`t2-worker` / `gemini-3.8-flash` → `deepinfra/google/gemini-3.1-flash-lite` / `-3.5-flash`) stay for the operator. Gates, red-first: `HandEntryPricingTests` prices `omniroute/vertex-3.6-flash` and `omniroute/vertex-3.7-flash` (leg `vertex/gemini-3.<6,7>-flash`, non-Claude, tier **credit**) and the unrowed-refusal guard now runs on a synthesised id; new `VertexFlashRegistryRowsTests` pins exactly the three Flash vertex ids in the registry, passing `gemini_model_allowed`, unsourced-priced and shaped like the 3.8 head, and asserts `check_registry` returns no problems. `registry.py validate` 0 (82 models); `test_registry.py` 307 OK; all five `render --check` (omniroute / litellm / ide / openhands / models-doc) 0 — an unreferenced model row derives no render, nothing regenerated; `sync-ide-models.py --check` 0; `sync-router-tiers.py --check` 0; `affected-tests.py` 16/16 pytest node ids pass; `test_autoos_spawner.py` 1187 run (9 new) with the same 5 failures + 2 errors as the branch-point baseline; `test_registry_render.py` 15 failures identical to HEAD on this tree, `test_sync_ide_models.py` unchanged at its 2 `variants`-block failures.
  - RWP3 (2026-10-01, rework 3): the cross-family seat's BLOCKER was real — `GEMINI_ALLOWED_RE`'s variant tail `(?:-[a-z0-9]+)*` read a `-pro` as just another suffix, so `gemini-3.6-flash-pro` and `gemini-3.8-flash-pro-preview` full-matched the allow-list while D-255 bans every Pro model. `gemini_model_allowed()` now also refuses any id with a '-'-delimited segment starting with 'pro' (`GEMINI_PRO_SEGMENT_RE`, glued spellings like `-promax` included — the conservative reading), checked on the model part after the last '/' and case-insensitively, so `vertex/gemini-3.6-flash-pro`, `models/gemini-3.8-flash-pro` and `GEMINI-3.6-FLASH-PRO` are refused and the allowed set is unchanged (`gemini-3.8-flash`, `3.7-flash-high`, `3.7-flash-medium`, `3.6-flash`, `3.8-flash-preview`). Red first: `GeminiAllowListTests.test_a_pro_token_hidden_in_a_variant_suffix_is_refused` (8 ids, fails on base) + `test_the_allowed_versions_and_variants_are_unchanged`. **G2 rejected** (combo rule): operator-owned registry data — the one off-list fall-through leg in `t2-worker` / `gemini-3.8-flash` is reported for removal, and tightening "refuse only when EVERY leg is off-list" re-breaks `ReviewFindingTests.test_gateway_client_model_override_wins_over_the_card`; no combo's decision moved here, since no registry or combo leg spells `flash-pro`. **G3 judged correct as is**: `env.update(clients.gemini_side_model_env(...))` overwriting a caller-set `GEMINI_MODEL` is the point — gemini-cli's internal side calls must name the leg this lane routed and D-255 cleared, never whatever the caller's shell held, and `worker_env` assigns plan entries after the allowlist copy so the plan wins; documented at the call and pinned by `GeminiSideModelPinTests.test_an_explicit_GEMINI_MODEL_in_the_spawner_env_does_not_reach_the_child`. Test hardening: G4 `HandEntryPricingTests.test_run_dry_named_hand_entry_is_not_benched_as_unpriceable` now asserts `returncode == 0` and `spends(...) is False`, not just the missing phrase; G5 `test_a_gemini_spawn_hands_the_child_the_pinned_model` (plus new `test_the_pinned_value_is_the_model_the_argv_carries`) reads the pinned **value** off the real call site through `plan_flag_and_pin()`, in-process, instead of building the child from a hand-made pin. Gates: `GeminiAllowListTests` + `GeminiSideModelPinTests` + `HandEntryPricingTests` 33 OK (38 with `ReviewFindingTests`); `test_autoos_spawner.py` 1193 run, failures=4 + errors=2, every name inside the branch-point baseline set (no new name); `test_registry.py` 307 OK; `registry.py validate` 0 (82 models); `affected-tests.py` node ids 6 passed under pytest.
  - RWP4 (2026-10-01, rework 4, D-255 leftover on the container fleet config): the renderer still pinned `leaf-implementer` to `omniroute/vertex-gemini-3.1-pro-preview` — `FLEET_VERTEX_MODELS` loses the off-list `vertex-gemini-3.1-pro-preview` and `vertex-gemini-2.5-flash` rows (nothing referenced them; `tools/probe-vertex.py` uses gateway ids, not the aliases) and gains `vertex-gemini-3.8-flash -> vertex/gemini-3.8-flash, 1048576` (registry `models.gemini-3.8-flash` context_advertised), leaf-implementer re-pointed at it; `context-overrides.json` drops four off-list Gemini rows whose `vertex-*` hand entries no longer exist (3-flash-preview, 3.1-flash-lite, 3.1-pro-preview, 2.5-flash) — the surviving `vertex/gemini-3.8-flash` row already covers the new fleet alias; `tests/test_render_opencode_config.py` gains a red-first D-255 render scan (every Gemini string in the render must pass `gemini_model_allowed()`, leaf-implementer resolves to `vertex/gemini-3.8-flash`, fleet tables carry no 'pro' Gemini id).
  - FINAL (2026-10-02, docs/data close-out): `docs/models.md` hand-entry prose now matches the shipped door — the stale "naming a combo with an off-list leg (`omniroute/t2-worker`, whose data still carries `deepinfra/google/gemini-3.1-flash-lite`) is refused" is replaced by the rule `gemini_spawn_refusal` implements and `GeminiAllowListTests.test_an_explicit_combo_with_some_off_list_legs_is_allowed` pins: a **combo** is refused only when EVERY leg is off-list, an off-list leg alongside allowed legs is D-256 registry data stage 2 removes, not a spawn refusal. The same paragraph named only `vertex-3.8-flash`; it now lists all three hand entries (`vertex-3.7-flash` -> `vertex/gemini-3.7-flash`, `vertex-3.6-flash` -> `vertex/gemini-3.6-flash`). **The two rows are priced** — `models.gemini-3.6-flash` / `models.gemini-3.7-flash` carry `price_in 1.5e-06`, `price_out 7.5e-06`, `price_cache_read 1.5e-07` and a dated `price_source` (https://docs.cloud.google.com/vertex-ai/generative-ai/pricing accessed 2026-10-02; Gemini 3 > Standard global list $1.50/$7.50 per 1M from 2027-01-01, introductory $0.75/$3.75 per 1M through 2026-12-31, no >200K surcharge, non-global +10 %); the list price is stored because it is conservative for the credit guard, and is the same figure as 3.8 — their `$comment` PRICE-NOT-SOURCED block is replaced by that citation plus `tool_calls stay unproven`. `models.gemini-3.8-flash` left out of scope, still 0.0. The pass changed one behaviour: `VertexFlashRegistryRowsTests` had pinned exactly the unpriced form (`test_the_two_new_rows_carry_no_invented_price_and_say_so`, asserting `(0.0, 0.0)` and "price not sourced"); it is now `test_the_two_new_rows_carry_the_cited_vertex_list_price` and pins the three values plus the dated `price_source` — a stronger pin on a sourced number, not a weakened one. `configuration/omniroute/context-overrides.json` gains `vertex/gemini-3.7-flash` and `vertex/gemini-3.6-flash` at `context 1048576`, copied shape from the 3.8 row and each `$comment` naming the hand entry it serves; nothing in `tools/` or `tests/` reads that file (only `configuration/omniroute/apply.sh` / `apply*.ps1` patch the gateway with it). Gates: `registry.py validate` 0 (82 models); `test_registry.py` 307 OK; `render omniroute|litellm|ide|openhands|models-doc --check` all 0 — no generated table drifted; `test_render_opencode_config.py` 6 OK; `combo-contract.py` PASS 24 combos; `sync-ide-models.py --check` 0; `GeminiAllowListTests` 15 / `HandEntryPricingTests` 12 / `VertexFlashRegistryRowsTests` 5 OK; full `test_autoos_spawner.py` 1193 run, failures=4 errors=2, none of them reading these prices (undeclared `opus-4-6`, missing `policy.latency_seed.credit`, t3-driver effort rung, nvidia FAMILYFENCE, two `GatewayCooldownStopTests` provider-list names) — pre-existing on this machine, like `test_registry_render.py` and `test_sync_ide_models.py`.
- SEATEVIDENCE-5 (2026-10-01, lane seat-evidence-5): gateway mode never lets an unparseable timestamp pass silently — a `--since`/`--until` or run-record bound that is present but unparseable **refuses** (exit 3, naming the bound), and an attributed row (key **and** tag match) whose timestamp is unparseable is a **gap**: new output field `n_unparseable_ts`, listed in `warnings`, forces `all_match` false and exit 2, while rows failing the key/tag test stay ignored as before (J1). `--gateway --run-id` combined with `--since/--until` is a **usage error** — the two window sources are exclusive, and with `--since/--until` the tag must come from `--session-tag` (J2); an empty `--key` is refused in argparse and the key filter is `key is not None`-based (J3). Tests 37→45 (bound refusals, gap exit 2 with `n_unparseable_ts=1`, foreign-tag unparseable row still exit 0, both usage errors, default key unchanged; the `<tag>/<run-id>` suffix test moved onto the run-record-window path J2 forces). The opt-in live gateway smoke (J4) was **deleted rather than rewritten**: re-running the tool on the finished WS-OMNI spark seat (`20261001-214452-wsomni-4h-review-spark-d4ec82`) now logs all 40 status-200 rows in that window as served through the deliberately-unmapped connection-id spelling `openai-compatible-chat-c125c82d-…`/`muse-spark-1.3-contributor` → honest exit 2 under the exact-string G5 rule, so the required exact `returncode == 0` assertion cannot be met against the live gateway today — a test that cannot fail is not acceptable. Request-side behaviour untouched.
- SEATEVIDENCE-4 (2026-10-01, D-266, lane seat-evidence-4): the `--gateway` mode is now attributed by **sessionTag**, not by a window. The run record's `session_tag` (`workers/<id>.json`, else `job.json`, else the spawner's `session-tag:` line in `output.log`) is REQUIRED and only call-log rows whose `sessionTag` equals it — bare tag, or the D-063 `<tag>/<run-id>` header form the gateway logs verbatim (measured live: rows carry no `sessionId` field at all) — are kept; no tag in the record, or no tag field on any row, exits 3 `cannot attribute` (a window alone never counts, so two concurrent seats in one window no longer blend). Non-200 rows (504 stall / error / no response) are dropped **iff** at least one 200 row remains **and** every 200 row served `--expect` exactly through the measured alias map; a 200 row on another model still exits 2 (and then nothing is dropped), zero 200 rows exits 3. Output adds `session_tag`/`n_rows_total`/`n_200`/`n_dropped`/`dropped_reasons` (`<status> <class>` counts from a fixed class vocabulary — never an error body) and the summary line prints `n_200=<n> n_dropped=<n>`. Tests 30→37 (`tests/test_seat_model_evidence.py`): concurrent-seat tag exclusion, missing tag → 3, untagged rows → 3, 504 dropped with all-200-match → 0, 200-on-other-model → 2, only-504 → 3; request-side mode untouched. Live regeneration on the WS-OMNI 4h spark seat (`20261001-214452-wsomni-4h-review-spark-d4ec82`): 10323 rows fetched, 62 in window, 48 tagged, `n_200=40 n_dropped=8` (`504 server-error`), exit 0 → `.sessions/l1-main/L2-WSOMNI/spark.evidence.tool.json`.
- SEATEVIDENCE-3 (2026-10-01, D-261, lane seat-evidence-3): the `--gateway` response-side mode fixed G1-G5 — the container snippet now `await r.json()` on the Response-like `apiFetch` result and takes rows from the array or `logs`/`data`/`rows` (non-200 → exit 3 naming the status only); window bounds parsed as aware datetimes (Z / offsets / ms, never string compare); `--run-id` window = run-record start→end padded +-5 s and a record without an end **refuses** (exit 3, never "until now"); rows attributed by the measured spawner key `autoos-local` (+ session id when both sides know it), with `window_rows` and a `window shared with other traffic` warning → exit 2 when other models' rows remain; provider normalisation extended only with the measured gateway alias `spark-1.3-contributor` (uuid connection ids never fuzzy-matched), dead code after `die()` removed, `--run-id` required in argparse for the keyless mode. Tests 17→30 (every G1-G5 behaviour + all old F-tests) plus 1 opt-in live gateway smoke behind `AUTOOS_LIVE_GATEWAY_TESTS=1`, `tests/test_seat_model_evidence.py`.
- SEATEVIDENCE-2 (2026-10-01, D-261, lane seat-evidence-2): a gate review of the D-260 tool proved **opencode stores no provider-response model** — its per-turn `data.model` is the model the CLI was *started* with, so the keyless check is **request-side**, not provider-response. `tools/seat-model-evidence.py` re-cut to honest semantics: output `kind: "request-side"`; the keyless check is now a **three-way** match (D-261) — `--run-id <id>` supplies the autoos-agent run record's requested free model (job.json argv `--free-model`/`request.model`, else the workers record's `model`; only argv/request.model/started/ended read), and the tool also reads the CLI `--model` from the sandbox's `opencode.log` (cli-starting line only) and the per-turn db model on **every** assistant row; all three must equal `--expect`. F3: an assistant row missing `model`/`providerID`/`id` is a **gap** → exit 2, never skipped; the db is opened read-only **with the WAL included** (db + `-wal` copied to a temp dir and the copy opened — `immutable=1` dropped, it hides the WAL); multi-session dbs evaluate the seat's session (newest, or `--session <id>`) and report the others. F4: `--gateway` (`--run-id` or `--since/--until`, optional `--key`) reads OmniRoute call logs **read-only** through the container helper and requires every in-window row's served `model`+`provider` == `--expect`; `kind: "response-side"` lists correlationIds — no key/token ever handled or printed (the helper authenticates inside the container). Wording fix: the mimo-pinned seat's nemotron is **autoos-agent's own cross-family fence** re-launching opencode with `--model nemotron`, not a provider substitution — corrected in `main-orchestrator.md` §4 and the tests. Tests `tests/test_seat_model_evidence.py` rewritten: three-way pass, and one mutant per surviving gap — turn missing model → 2, `id` None → 2, model only-in-WAL → 2, CLI≠db → 2, run-record≠CLI → 2, gateway row other-served-model → 2 — plus the two real dbs located by absolute `/home/s/code/AutoOS/logs/sandboxes` path (skip only if absent): nemotron seat three-way match exit 0 (74 turns), mimo-pin fence divergence exit 2. 17 tests, 0 skips on this host.
- SEATEVIDENCE (2026-10-01, D-260, lane seat-evidence): new `tools/seat-model-evidence.py` — deterministic, no-LLM extraction of the **served** model id for every assistant turn of a keyless opencode review seat, read from the seat's `<sandbox>.opencode-data/opencode/opencode.db` (`session_message` type='assistant', `data.model.providerID`/`.id` — the provider-response record, not the requested model and not model-authored text). Writes a sha256-stamped `<sandbox>.seat-evidence.json` and prints `evidence <path> sha256 <hex> all_match <true|false> turns <n>`; exit 0 all-match / 2 any mismatch / 3 no turn data. Provider-prefix normalisation is an explicit map only — model names are compared byte-exact. `credential`/`account`/`control_account`/`kv` are never SELECTed, no prompt content or tokens printed, the db opens `mode=ro`. Tests `tests/test_seat_model_evidence.py` (tmp SQLite fixtures reproducing the schema + the two real example dbs when present: nemotron seat 74 turns all_match; the mimo-pinned seat whose fence served nemotron exits 2), wired into `tests/linux/33-documentation.sh`. Documented in `unattended-orchestration/references/main-orchestrator.md` §4 as the D-260 seat-evidence gate.
- RENDERFIX (2026-10-01): the **15 stale `tests/test_registry_render.py` expectations** the TORDER wave left red — the render-expectation file was never run by that wave, so this survived its own carrier tip and every take after it. All 15 were stale expectations of the pre-wave registry (gemini head restored by GEMRESTORE; antigravity + huggingface unavailable with their legs removed; `openai/gpt-oss-120b`/paid-openrouter legs gone from `t2-orchestrator`; `free-ai/qwen7b` now mid-band instead of last; t1 the honest **1M** band under D-TORDER-1(b); t2/t3 the 128k clamp), with **zero real defects** — including the `xhigh` case, where the restored gemini head's `low/medium/high` ladder correctly drops a default it cannot carry. Also pinned the researcher lane's new **`t4-researcher` `128k`** context in the combos gate. Gates: `pytest` render trio **15 failed → 472 passed / 0 failed**, `render omniroute --check` exit 0, `combo-contract.py` **24 PASS**, `registry.py check` ok (37 routes / 80 models / 53 providers), `run-tests.ps1 -Filter combos.json` **242/0**, all four render targets `--check` exit 0.
- opencode-direct fallback ladder (2026-10-01, L0/operator): documented the no-gateway model ladder for lanes when the omniroute combos fail — openrouter `:free` only (NO credit; every paid openrouter leg is denied) → opencode Zen free → meta direct → litellm → ollama. In-opencode usage is not gateway proxying (no combos/admission/backoff). Live probes: `opencode/longcat-2.5-preview-free`, `opencode/space-bunny-free`, `opencode/mimo-v2.6-flash-free`, `openrouter/nvidia/nemotron-3-super-120b-a12b:free`, `openrouter/qwen/qwen3.8-27b:free` OK; `meta/muse-spark-1.3` DOWN (`META_API_KEY` unset); `litellm/t2-worker` rejected — the server serves `tier2` (use `litellm/tier2`); `ollama/qwen2.5-coder:7b` answers direct on `127.0.0.1:11434` (54 s cold) but the opencode ref refuses (per-user `baseURL: host.docker.internal`). Also records the 7 paid openrouter route legs TORDER-OR drops. See `docs/handoff/2026-10-01-opencode-direct-fallback-ladder.md`.
- REVIEWGATE-2FAM (2026-09-30, operator): `review-status`/`ready` now count SEATS — a ready record needs at least two `kind=cross-family` entries, each READY, from distinct registry families, none the author's; the detail names missing/duplicated seats, an optional `family=` token is cross-checked against the registry, every counted seat prints as `seat N: <model> (<family>) verdict=<v>`, and more than 3 seats is a non-blocking note (no cap). One seat + the Sonnet final no longer reads as reviewed.
- PROVIDERCOV (2026-09-30, ws-omniroute provider-coverage lane): 20 operator-listed providers added to `catalog/ai-registry.json` as `available:false` with measured `$comment` reasons (none can serve chat completions through the gateway); to avoid colliding with the `clients` section, rows renamed `opencode` → `opencode_gateway` and `qoder` → `qoder_ai`; `meta`/`meta_api` base URL `https://api.meta.ai/v1` re-checked and kept. The D1 reconcile drops this branch's `providers.ovhcloud` credit-tier stub (ws-ovh `f6f5e69` wins at merge), tests 20→19 missing. Gates: `python tests/test_registry.py` 305 OK; `python tools/registry.py check` ok 25 routes/71 models/53 providers (52 after D1).
- TORDER batch 2 (2026-10-01, operator): every **`huggingface/*`** (10 legs) and **`antigravity/*`** (4 legs) leg is gone from the bands — unusable (401 / no credentials) and rate-limited-for-days respectively — with the providers marked unavailable-with-reason plus a revisit note and their declarations kept gated (the openrouter pattern). `hf-glm-5.2`, `hf-qwen3.8-27b` and `opus-4-6` now render `omitted`, and the live apply **pruned all three stale combos** (they had survived the previous apply). `models.Qwen3.8-27B` `context_advertised` 262000 → **128000** (the gateway's own `combo-min`, measured). Vertex's credential requirement is documented: the provider authenticates from the **GCP service-account JSON** `configuration/vertex-credentials-*.json` (now git-ignored), not a plain API key. **Latency:** there is no per-leg timeout knob in the gateway — `requestQueue.maxWaitMs` 180000 is the Spark queue wait, not a hop budget, and `providerCooldown` is disabled; the crawl is fixed by shortening the chains, and the 30-min park then skips a parked leg. Measured after the change: `t2-worker` served by free `groq/qwen/qwen3.8-27b` in **629 ms** (0 fallbacks), `t1-orchestrator` by credit `vertex/gemini-3.8-flash` in **6706 ms / 1 fallback** (was 22556 ms / 3 fallbacks to paid deepseek), `ovh-qwen3.8-27b` by `ovh/Qwen3.8-27B` (0 fallbacks; 23455 ms of *upstream* latency, not chain crawl). Render/contract/DryRun all exit 0 (23 combos, 8 omitted, contract 23 PASS); 2 free-family reviews APPROVE. Remaining: `free-ai/google/gemini-3.8-flash` 429s `premium_requires_purchase` so it is not a free 1M leg, leaving `t1-orchestrator-free-only` effectively single-provider; one `vertex`/`meta-api` connection row still 401s on an undecryptable stored key (credential-store repair, L0); `meta-api`'s 401 is not a missing key (it reads `api-keys.yml` key `meta`).
- TORDER (2026-10-01, operator): the routing chain is re-ordered **trial → free → credits → paid with `deepseek` LAST**, and the tier windows are now contractual. `t1-orchestrator`/`t1-orchestrator-free-only` keep only legs with a measured window **≥ 600000** (`gemini/gemini-3.8-flash`, `free-ai/google/gemini-3.8-flash`, `vertex/gemini-3.8-flash`, `meta-api/muse-spark-1.3-contributor`, `deepseek/deepseek-flash`) so t1 renders **1M**; every sub-1M leg moved to the t2/t3 bands, which stay **128k** (gated by their lowest implementer). `vertex/gemini-3.8-flash` was added to the t2-worker/t3-driver **credits** band and `scaleway/*`/`antigravity/*` kept but no longer heads. Four credit single-combos added: `ovh-qwen3.8-27b`, `ovh-gpt-oss-120b`, `ovh-qwen3-coder-30b`, `vertex-gemini-3.8-flash`. Render-source fix for the 128k-compaction bug: `routes.gemini-3.8-flash` context 131072 → **1048576** with the `gemini/gemini-3.8-flash` free head restored, `models.gemini-3.8-flash` `output_max` 32768 → **65536** and `context_usable.tokens` → **1048576**, `providers.google_ai_studio` re-opened under the repeated-429 backoff policy; all five surfaces re-rendered from the registry, so `render omniroute --check` exits 0 and `opencode.jsonc` carries `limit { context: 1048576, output: 65536 }`. No paid openrouter leg is routable any more (all 8 declared paid legs removed or left gated; `providers.openrouter` stays available for the 22 `:free` legs). New **`tools/combo-contract.py`** gate asserts, per combo, limit == registry context == combos.json context, the free→credits→paid order with paid last, leg resolvability against the live catalog, the t1 ≥600000 / t2-t3 128k windows, and openrouter `:free`-only — wired fail-closed into `apply.ps1`/`apply.sh` and CI, and documented as the `combo-create` skill rule. Verified live after apply: `t2-worker` served by the **free** `groq/qwen/qwen3.8-27b` leg (gemini 503 → skipped by the model lockout instead of hammered), `ovh-qwen3.8-27b` by `ovh/Qwen3.8-27B` (767 ms, 0 fallbacks), `vertex-gemini-3.8-flash` by `vertex/gemini-3.8-flash` (2171 ms, 0 fallbacks), each logging `Combo context limit: 1048576 (source=target)`. Open gaps: `free-ai/google/gemini-3.8-flash` actually 429s `premium_requires_purchase` (not free), the `vertex`/`meta-api` connections 401 after an Encryption "auth tag" failure, `hf-glm-5.2`/`hf-qwen3.8-27b`/`opus-4-6` stay in `combos.json` but are uncreatable live and are not pruned, and `ovh-qwen3.8-27b` resolves 128000 (`combo-min`) against its declared 256k.
- T1SECOND (2026-10-01): `t1-orchestrator` stays at **two** distinct usable providers when the nebius removal lands — the free `groq/qwen/qwen3.8-27b` leg (FREEKEYS-1 `tool_calls: proven`, provider-tier free) is added after the scaleway band and before the paid `meta-api`/`deepseek` legs. Registry `routes.t1-orchestrator.legs` + a dated `T1SECOND` `$comment`, re-rendered `combos.json`, `configuration/litellm/config.yaml` and `docs/models.md`. Gates: `ComboCrossProviderTests.test_every_agentic_route_has_two_distinct_usable_providers` + `..._three_usable_legs` pass (counts 2/4/5/4/5/4); `registry.py check`/`validate` `ok`; `render litellm/ide/openhands/models-doc --check` ok; `audit-router.py --offline` no drift; live `t1-orchestrator` probe 200 with a `get_weather` tool call; failure sets identical at base.
- Resilience defaults (2026-10-01, commits `51fd01d2`/`3b0135e3`): the repeated-429 rotation policy is now applied **respect-set** (an operator value wins) and exactly once in every tracked gateway-spawn site — `OMNIROUTE_ROTATION_ENABLED=true`, `OMNIROUTE_ROTATE_ON_429=true`, `OMNIROUTE_ROTATE_429_THRESHOLD=3`, `OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120`, `OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300`, `OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000` — i.e. **rotate after 3×429 in 120 s, 300 s cooldown per leg, 30 min park** — in `configuration/start-stack.ps1`/`.sh`, `configuration/omniroute/apply.ps1`/`.sh` and the logon helper `configuration/autostart/Start-AutoOSStack.ps1`; previously a free-probe *proposal* with no tracked surface. Gates: failing-first `-Filter 'repeated-429'` 0 passed/1 failed → post 49/0; Linux `--filter '429 rotation policy'` 1/0; a child-process env dump proves respect-set + exactly-once; `shellcheck` clean; no gateway restart.
- Patch artifacts (2026-10-01): certified-pristine revert paths and idempotent reapply for the clamp, deepseek and vertex gateway patches — 15 new `*.autoos-backup-pristine-3.8.50-*` backups streamed from the published `omniroute@3.8.50` tarball (15/15 re-hashed equal; `_14jycqh._.js` byte-identical so it correctly got none), the clamp reapply artifact `configuration/omniroute/qwen-clamp-reapply.ps1` + README (ports the untracked `patch_dist.py`, fixes its dropped `/` in `VERIFY_NEW`, idempotent, backs up each file), and `_18ct13i._.js` added to `reason-fix-reapply.ps1` (which also gained the UTF-8 BOM so it parses under Windows PowerShell 5.1). Gates: deepseek run 1 `12 patched`/run 2 `0 patched, 12 skipped`; clamp run 1 `6 patched`/run 2 `0 patched, 6 skipped`; `-DryRun` writes nothing; `patch-verify` gives pre-restart **GO** — 19/19 patched files carry a certified revert path, `_18ct13i` reapply `0 patched, 12 skipped`, and the reasoning + vertex probes pass against a throwaway gateway on `:20146`; no live package file modified, no gateway restart.
- Probe scripts (2026-09-30/10-01): the gateway probe set is now tracked — `tools/probe-free.py` (the 43-leg free-tier probe plus the `analyse-gemini-log.py`/`analyse-gemini-codes.py` health/429 readers), `tools/probe-clamp.py` (`5b9b7dd7`; asserts an over-cap `max_tokens` is clamped, not rejected; PASS/SKIP exit 0, FAIL exits 1; logs a 429/`chat_admission_busy` with a 60–120 s backoff), `tools/probe-vertex.py`/`probe-vertex-isolated.py` (trailing model turn), and `tools/probe-reasoning*.py` (`probe-reasoning-repro.py` overwrite-vs-preserve before/after). Keys come from `AUTOOS_OMNIROUTE_KEY` or `api-keys.yml` in memory and are never printed. Gates: clamp live `VERDICT: PASS - ... request answered 200; no max_completion_tokens cap 400`, exit-code proof against a fake gateway (fail=1/skip=0/pass=0), key-leak check False, two runs identical; reasoning repro 4/4 isolated cases PASS.
- Admission fixes (2026-09-30): `chat_admission_busy` sheds under ≥3 concurrent heavy streams were the single-slot structural gate — `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` is now respect-set (only when unset, once) in `configuration/start-stack.ps1`/`.sh` and `configuration/omniroute/apply.ps1`/`.sh` to **4** (4+4=8 effective; measured before 3-wide → 2×200+1×503, after 4-wide → 4 admitted, 0 sheds); the launcher shim class is pinned on `apply.ps1`, `start-stack.ps1` **and** the logon helper `configuration/autostart/Start-AutoOSStack.ps1` — each resolves `omniroute.cmd` via PATH with an `%APPDATA%\npm` fallback and fails loudly (`exit 1`) instead of starting the bare name. Commits `880ec58`/`1179e3f` (AdmissionFix) + `d88ed3b` (P0). Gates: new `tests/run-tests.ps1:9779` launcher admission/shim `Test-Case` (failing-first `failed 1` → post `29 passed`); parse errors 0; `apply.ps1 -DryRun` exits 0; full suite 5 failures pre-existing on base.
- Nebius removal (2026-10-01): every `nebius/*` leg is gone — the combos half removed the six legs from `configuration/omniroute/combos.json` (all six agentic combos) with a dated `NEBREMOVAL` `$comment`, and the registry half (L1-beta) dropped the `nebius` provider + legs from `catalog/ai-registry.json` and re-synced `litellm`/`docs/models.md`/consumers. Gates: `apply.ps1 -DryRun` + live apply (22/22 combos, resilience already current); live store `TOTAL combos: 22 / Nebius legs in live store: 0`; six touched combos 6/6 HTTP 200; `registry.py check`/`validate` ok; `render omniroute --check` drifts on exactly the six edited combos until the registry half lands (expected, not weakened).
- FREEWIRE + gemini retention (2026-09-30, commits `7eff6020`/`4cb49b43`/`2ff537a4`): wired the **10 probe-proven free legs** (huggingface `zai-org/GLM-5.2` + `Qwen/Qwen3.8-27B`; groq `qwen/qwen3.8-27b` + `openai/gpt-oss-120b`/`gpt-oss-20b`; openrouter `:free` ×4; `vertex/gemini-3.8-flash`) into the registry and every render, with 5 new model rows and 3 rows promoted `tool_calls: proven`, 7 new single-provider combos, free bands interleaved before the credits band in the five agentic routes (**never** `*-clean`), and **3 policy allow-rules** above `deny-groq`/`deny-openrouter`; the OpenRouter guard moved (`providers.openrouter.available` false→true, the two paid DSMAX legs re-gated at the route level). **Operator correction: the gemini exclusion was reversed — gemini retained; usage governed by the repeated-429 backoff policy (3×429/120 s → 300 s cooldown per leg; 30 min park).** Gates: `registry.py check`/`validate` `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers`; 5 renders + `sync-ide-models.py --check` ok; `audit-router.py --offline` no drift; 10/10 newly wired legs ack HTTP 200 (22:45–22:46Z, no 429/503/504, no `chat_admission_busy`); ≥2-distinct-usable-provider invariant green on all six agentic routes; a full `tests/test_*.py` sweep vs the `a975d48` baseline shows no new failures and 7 fixed.
- OVH legs + registry (2026-09-30, commits `f6f5e695`/`7e329c7e`/`158818ea`): `providers.ovhcloud` credit-tier entry ($200 cap, `model_prefix: "ovh"`) with 3 model rows (Meta-Llama-3.3-70B, Mistral-Small-3.2-24B, Qwen3-Coder-30B — each probed 3/3 ack+tool+round-trip, `tool_calls: proven`) and the `ovhcloud/*` legs in `t2-worker`/`t3-driver`; then stale `context_advertised` synced to live measurements (`gemini-3.8-flash` 131072→1048576, `claude-opus-4-6-thinking` 200000→1048576) with `vertex/gemini-3.8-flash` and `deepseek/deepseek-flash` added to routes and the `context_declared` labels + renders updated. Gates: `registry.py check`/`validate` ok; `test_registry.py` 300 OK and `test_registry_render.py` 160 OK; all 5 renders `--check` ok; `audit-router.py --offline` no drift; live apply idempotent with a 200 ack per touched combo.
- Vertex leg + 1M contexts (a5bcb69, 2026-09-30): `configuration/omniroute/combos.json` raised five combos to the measured 1M window — `gemini-3.8-flash` 128k→1M with `vertex/gemini-3.8-flash` as second leg, `opus-4-6`/`t2-orchestrator` 200k→1M from the antigravity leg, `t2-worker-clean`/`t3-driver-clean` 128k→1M from `deepseek/deepseek-flash` — while the genuine 128k clamps (`t1-orchestrator`, `t2-worker`, `t3-driver`, `t4-rag` and their free-only twins) stay untouched; `apply.ps1`/`apply.sh` gained only the §3 resilience design comment. Gates: `test_registry_render.py` 155 pass / 5 expected cross-lane failures (stale registry contexts are L1-beta's half); `apply.ps1 -DryRun` + live idempotent apply (`vertex/gemini-3.8-flash already 1048576`); `audit-router.py --offline` no drift; per-touched-combo ack probes.
- REVIEWGATE-2FAM (2026-09-30, operator): `review-status`/`ready` now count **seats** — a ready record needs at least two `kind=cross-family` entries, each READY, from distinct registry families, none the author's; an optional `family=` token is cross-checked against the registry; the detail names the missing/duplicated seat, every counted seat prints as `seat N: <model> (<family>) verdict=<v>`, and more than 3 seats is a non-blocking note (no cap). One seat plus the Sonnet final no longer reads as reviewed. Landed on `L1-backlog/reviewgate-2fam` (`d0f70f15`); `tests/test_autoos_spawner.py` red-first (33 tests, 4F+11E before) → `ReviewStatusTests`+`ReadyCommandTests` 63 OK; full-file failure set identical at base `e58274a`.
- Redactions (2026-09-30, public repo): the real username/user-home path is scrubbed from `docs/handoff/2026-09-30-workstation-omniroute-handoff.md` on `L1-backlog/ws-hygiene-main-20260930` (`4ba83ed0` — that branch is not yet an ancestor of this one), and the P0 admission lane redacted token-like session ids and the inherited user-home path → `<session-id>` / `C:/Users/<user>` (findings, verdicts and the evidence list unchanged).
- ApplyJson (2026-10-01): `configuration/omniroute/apply.ps1` now parses `catalog/ai-registry.json` case-sensitively and duplicate-tolerantly, so a registry carrying two keys that differ only by case (vendor model spellings, e.g. `Qwen/Qwen3.8-27B` beside `qwen/qwen3.8-27b`) no longer throws `ConvertFrom-Json`'s `DuplicateKeysInJsonString` (5.1) / "keys with different casing" (pwsh) and silently skips provider registration. New `ConvertFrom-AutoOSRegistryJson`/`ConvertTo-AutoOSRegistryObject` (`-AsHashtable` on pwsh 7; `JavaScriptSerializer` on Windows PowerShell 5.1, which has no `-AsHashtable`) project back onto the same PSObject shape, first-wins on a case-only pair (confined to `models`, which the launcher never reads). Guard test: `Get-AutoOSProviderMap tolerates registry keys differing only by case`, red pre-fix on both shells.
- DS1M follow-up (2026-09-30): `openhands/profiles/deepseek-flash.json` re-pinned to the catalog numbers — `max_input_tokens` 1048576, `max_output_tokens` 393216 (was the stale 131072/32768). The vendored-template gate was red on it: `python tests/check-vendored.py` and `tests/linux/04-registry-models.sh` ("the vendored openhands profiles match the catalog snapshot").
- AGYCANON (2026-09-30, operator): antigravity renders canonically again — the live `/v1/models` was re-measured and lists 19 canonical `antigravity/*` rows and zero `agy/*` (both spellings route; canonical wins), so `providers.antigravity.model_prefix` is retired `"agy"` → `null` and `render_omniroute()` emits the registry spelling unchanged. The mechanism stays live for providers that genuinely diverge (`scaleway` → `scw`), and `GATEWAY_ONLY` keeps dropping both spellings for combos rendered before this change. Re-rendered: `configuration/omniroute/combos.json` (4 combos: `opus-4-6`, `t2-orchestrator`, `t2-worker`, `t2-worker-free-only`); schema description + `gateway_ref()` docstring updated. Gates, red-first: `GatewayRefTests` in `tests/test_registry.py` + `tests/test_registry_render.py` (retired-prefix + still-applied-prefix cases), `tests/linux/17-ai-routing.sh` `known_drops` respelled. Verified: `test_registry.py` 300 OK, `test_registry_render.py` 160 OK, `check-vendored.py` OK, all 5 `render --check` OK, `sync-router-tiers --check` OK, `registry.py check`/`validate` OK, `audit-router.py --offline` no drift.
- DS1M (2026-09-30, operator): `deepseek-v4.1-flash` is a 1M-context route again — the vendor Models & Pricing page (https://api-docs.deepseek.com/quick_start/pricing) states MODEL `deepseek-flash` = VERSION DeepSeek-V4.1-Flash with CONTEXT LENGTH 1M and MAX OUTPUT 384K, and OpenRouter's live catalog agrees (`deepseek/deepseek-v4.1-flash` context_length 1048576). `models.deepseek-flash` + both v4.1 spellings corrected 131072/65536/32768 → 1048576/524288/393216 (usable stays at the 50% default until probe-recall measures it); `routes.deepseek-v4.1-flash` declares `1M` so `clamp_route_context` keeps the promise. Re-rendered: `combos.json`, `ide-models.json`, `tier-profiles.json`, `docs/models.md` table, `opencode.jsonc` (via sync-ide-models). Gate: `DeepSeekFlashContext1MTests` (red-first, 3 tests) + re-pinned `legacy-models.golden.json` and the ps1 combos-context pin. Live gateway applied: 15 combos, native `deepseek/deepseek-flash` leg serving. Known gap exposed by the apply: `meta-api` is not a gateway provider type (`providers add meta-api` → Invalid provider), so the spark legs have no live connection — see the handoff.
- CAO removal (2026-09-29, operator): CAO is deprecated — the batch runner is the only lane. Deleted the `cao/` package (18 modules), `tests/cao/` (21 files), `infra/mcp-servers/cao-setup/` (12 files) and `references/cao-runbook.md`; cut the `## CAO quickstart` section from the skill; dropped the `cao` block from `handoff.config.example.json`; de-CAOed the skill description, Files table, R-coord-09, `l3-routing.md`, `main-orchestrator.md`, `rule-map.md`, `trust_worktree.py`, `pytest.ini`, `repository-index/SKILL.md` and `AGENTS.md`. Gate: `tests/test_no_cao.py` (red-first, 4 passed).

### Changed — the review gate counts two cross-family seats (REVIEWGATE-2FAM, 2026-09-30)

One `AutoOS-Review: kind=cross-family` entry was enough for `review_status()` — and therefore for
`review-status` and `ready` — so one reviewer plus the Sonnet final read as "reviewed", and two
spellings of one family would have read as two seats once the floor rose. The gate now counts
SEATS: an entry counts only when it names a registry-known reviewer and author, the two families
differ, any `family=` the entry declares matches the registry (a mismatch names both sides and
refuses the seat), and the verdict is READY. **Two counted seats from distinct families** are
required, with no cap: past three seats the operator's 2-3 guidance prints as a note, never a
refusal. The detail names exactly what is missing or duplicated ("2 cross-family seats required;
have 1", "reviewers X and Y are the same family (qwen)"), and the report prints every counted seat
as `seat N: <model> (<family>) verdict=<v>`. `REVIEW_ENTRY_FIELDS` gains the optional `family=`
token; the hint and `run`'s paste-ready `record-line:` example show it. The Sonnet final check and
the exit codes are unchanged.

- **Tests**: `tests/test_autoos_spawner.py` `ReviewStatusTests` (red first: 33 tests, 4F+11E before
  the implementation) and `ReviewStatusTests`+`ReadyCommandTests` (63 OK after); full file 1153
  tests, 64F+2E — the identical failure set at base `e58274a` (1148 tests, 64F+2E); lane-only
  failures none.

### Fixed — the spawned worker resolves the gateway too, not the clone's loopback (GWLOOPBACK-2, 2026-09-30)

GWLOOP cured the spawner's own `gateway_up()` pre-check, but the spawned worker was still born
broken in-container: its opencode reads the *sandbox clone's* `opencode.jsonc`, whose omniroute
provider pins `http://127.0.0.1:20128/v1` — refused inside a container, where the gateway is the
`omniroute` sibling — so every in-container gateway-path worker died on its first model call (0 of
58 records; the memspec P1 review seat 1R died `ConnectionRefused`).

- **The child's `OPENCODE_CONFIG_CONTENT` overlay now carries
  `providers.omniroute.settings.baseURL`** = the address this process resolved to (`GATEWAY`,
  rebound by the pre-check), spelled with the repo's API root (`/v1`; an override that carries its
  own path keeps it) by the new `gateway_base_url()`, stamped into the child env by
  `stamp_worker_gateway()` inside `worker_env()` — the one place both spawn sites build the child
  env, after the plan merge and the key. opencode merges the content source last (v2.0.19
  `Config.load` appends it after the discovered files; measured 2026-09-30: the same clone, same
  env — `opencode run --standalone` fails ConnectionRefused without the stamp and reaches the
  gateway with it), so the clone's file cannot pin a worker to an address its container refuses.
  No file on disk is rewritten; on a host the stamp spells exactly what the file already says; a
  `--free` overlay (no provider block) and a document without the provider are left alone.
- **Tests**: `tests/test_gateway_selection.py` `GatewayBaseUrlTests` (5 cases, red first: the
  container/host shapes, the call-time default, trailing slash, an override's own path) and
  `tests/test_autoos_spawner.py` `WorkerGatewayOverlayTests` (9 cases, red first: the stamp, the
  merge into a provider block, the host shape, `--free`/non-omniroute/malformed documents
  untouched, the plan's copy kept, the checkout file byte-identical, and the whole
  `build_plan` → `worker_env` emit path). Full spawner suite: 1140 tests, 64F+2E — the identical
  failure set at base `2df2d5c` (1131 tests, 64F+2E), lane-only failures none.
### Fixed — `autoos-agent.py` resolves its gateway instead of assuming loopback (GWLOOP, 2026-09-30)

`tools/autoos-agent.py` hard-coded `GATEWAY = "http://127.0.0.1:20128"`. That address is right
on a host and refused inside the stack's containers, where the gateway is a sibling container
the compose network reaches as `omniroute` (`configuration/docker/ai-stack/compose.yml` spells
the in-network address `http://omniroute:20128`): from a container every gateway-backed run
refused in the `gateway_up()` pre-check with "start it" advice for an address that could never
answer there.

- **`GATEWAY` is resolved, not hard-coded**: `gateway_candidates()` orders an explicit
  `AUTOOS_OMNIROUTE_URL` (the override `tools/autoos_usage.py` already reads) first, then the
  docker DNS name `http://omniroute:20128`, then `http://127.0.0.1:20128` as the fallback;
  `resolve_gateway()` takes the first. Selection stays string-only, so importing the tool still
  contacts nothing (the new test asserts `urlopen` is never called during import).
- **An override is the only candidate**: a deliberately dead `AUTOOS_OMNIROUTE_URL` (the shell
  suite sets one on purpose) fails closed exactly as before, instead of being rescued by a live
  gateway behind another name.
- **`gateway_up()` is the pre-check**: it walks those candidates in order and rebinds `GATEWAY`
  to the first that answers, so the import-time guess becomes the address the rest of the
  process talks to - the host case too, where `omniroute` does not resolve outside the compose
  network and loopback remains the gateway.
- **Tests**: `tests/test_gateway_selection.py` (16 cases: candidates, the const, an I/O-free
  import, a fresh interpreter with and without the override, the health GET, and every pre-check
  path, all probes injected), wired into `tests/linux/33-documentation.sh`;
  `docs/web-services.md` now states the resolution order in the publish-address section.

- WINFAIL2 (2026-09-29): Windows test fixes — CRLF card fixture in `test_autoos_card.py` (`newline=""` so Windows text mode does not double `\r\n` to 44 lines) and omitted `reasoning_effort` in `run-tests.ps1` (key is dropped, not null — `PSObject.Properties` check replaces the null compare under StrictMode).

### Fixed — the round-1 unclosed-fence rescan forged verdicts; VERDICTFENCE-R2 replaces it (2026-09-29)

The cross-family review (Muse) of the round-1 rescan measured three forgeries on `6e182a3`, all
live: (B1) a reviewer that pasted a file and crashed, `'```\nchecking worker output\n```\nVERDICT:
READY\n```\n'`, graded READY — the paste's verdict, never the reviewer's; (B2) `'VERDICT:
fix-first\nsome notes\n```\nVERDICT: ready\n'` graded `ready` — the rescan overwrote the real
earlier verdict; (B3) `'```\ncode\n~~~\nVERDICT: ready\n'` graded `ready` — `~~~` does not close a
``` fence (and a ` ``` ` cannot close ` ```` `). A rescan that re-reads the tail "unfenced" is
unsafe by construction: a transcript cut mid-block makes the fence pairing ambiguous, so nothing
after the first marker can be trusted to sit outside a paste.

- **`tools/autoos_agent_mcp.py` `review_verdict`**: the rescan is removed — a fence still open at
  the end of the text fails CLOSED, hiding everything from the first fence marker on (verdicts
  stated before any fence, like B2's `fix-first`, survive). The scan is diff-hunk-aware: after an
  `@@ -a[,b] +c[,d] @@` header, the hunk body (counted from b/d; `\ No newline` counts toward
  neither) toggles no fence and matches no verdict, and `diff --git`/`index`/`---`/`+++` header
  lines are skipped — this is what saves the round-1 measured git-diff case deterministically,
  without re-reading anything unfenced. Fences follow CommonMark: the closer must be the opener's
  own character, at least as long, whitespace-only, at ≤3 spaces indent. Quoted/template rejects
  and last-verdict-wins are kept. A blank line while the hunk counts remain is a context line that
  lost its single leading space to a terminal's trailing-whitespace strip and consumes one from
  both counts (VERDICTFENCE-3, measured: `'@@ -1,3 +1,3 @@\n a\n\n VERDICT: READY\n'` had graded
  `READY`).
- **One round-1 test inverted by rule (a)**: `test_an_unclosed_fence_does_not_hide_what_follows_it`
  asserted the rescan behavior itself (`"```text\nVERDICT: fix-first\n"` → `fix-first`); it is now
  `test_an_unclosed_fence_hides_what_follows_it` → `None`, matching pre-round-1 fail-closed.
- New tests (`VerdictLineTests`), all red on `6e182a3` first: B1/B2/B3 exact texts, the
  long-opener/short-closer, and a hunk whose body verdicts are never counted while a verdict
  stated after the hunk still is. Follow-up named in code: verdicts should come from
  reviewer-owned model turns of a structured transcript (R-orch-16), not raw-stdout scraping.

### Fixed — `review_verdict` let one stray fence swallow a reviewer's verdict (VERDICTFENCE, 2026-09-29)

Measured on run `20260929-061040-review-familyfence-a8a66-22ed12`: the reviewer ran `git diff` and
the transcript carried its output. One context line of that hunk was ' ```' — a markdown fence
*sitting in the diffed file*, prefixed by the diff's own leading space. The scan tested the
**stripped** line's first characters, took it for a fence opener, never found a closer, and so
skipped every later line including the reviewer's own `VERDICT: READY` 450 lines down
(transcript line 1436 vs 1894). The run graded `failed/no-verdict` — the deliverable existed and
was read as absent.

- **`tools/autoos_agent_mcp.py`**: `_FENCE_RE` is now markdown's own rule — up to 3 spaces of
  indent, then three or more ` or ~ — matched against the raw line after the ANSI strip. The
  per-line verdict test moved out to `_verdict_value()` so the two scans share one definition of a
  verdict instead of restating it.
- **An unclosed fence no longer hides what follows it**: the scan remembers the line of the last
  opener that was never closed and re-reads everything after it unfenced. The toggling is strictly
  alternating, so that opener is the last fence marker in the text and the tail below it is
  unfenced as far as this scan can tell.
- **Kept**: a `VERDICT` line inside a real, **closed** fenced block is still the contract pasted
  back at us and stays ignored, as do `>`-quoted lines and template lines carrying `<` or `|`; the
  last valid verdict still wins.

Tests first in `tests/test_autoos_spawner.py` (`VerdictLineTests`): the measured git-diff shape,
a closed block staying ignored, an unclosed opener not hiding `fix-first`, and a fence deeper than
markdown's indentation reading as content (that one inverted the parity of a *real* block, which is
how a closed block used to leak its verdict).

- FAMILYFENCE-5 (2026-09-29, cross-family review Muse — these gate D-115, so they are fixed before the final): (1) `reviewer_run_override` replaced the run's combo with the `policy.reviewers` pick AFTER the fence had answered, so an authored review card whose card `author` differs from `--review-of`'s writer planned and announced a reviewer inside the fenced family and only the post-run backstop noticed (rc 12 after the review had run) — `_fence_check_reviewer` now judges the swapped model spelling (its `policy.reviewers` family) and the swapped combo's legs, and `cmd_run`'s fence is passed into both the v1 and the v2 route walk, so the refusal is a PLAN-time `FamilyFenceRefused` / rc 12 (`FamilyFenceReviewerOverrideTests`); (2) the FAMILYFENCE-4 refusal's second "way out" told the caller to add `--not-family`, which does not clear that refusal (the writer stays unnamed and rc 2 re-fires) — it now reads "spawn the review through the same autoos-agent MCP/checkout that spawned the writer, or drop `--review-of` and name the writer's family with `--not-family`", in the CLI text and in the MCP `spawn` tool's own description; (3) `--not-family ""` silently no-oped (`fence_family_names` drops a blank before the FAMILYFENCE-3 B2 name check can call it unknown) while MCP refused the same value — a blank is a broken argument on the CLI too and is refused with rc 2 before any name beside it is judged (`fence_blank_refusal`, `not_family_values`, `FamilyFenceUnknownNameTests`); (4) the white-box N1 test that asserted a `fence=` keyword on a `build_plan` spy (mutation proves nothing else observes that argument on the free path) is replaced by a behavioural one: a fenced model sitting next in the ordered free chain after a rate-limit stop is walked past and the run serves the next un-fenced spelling (`FamilyFenceFreeChainTests.test_a_fenced_model_next_in_line_is_walked_past_on_the_real_re_plan`, kills the test when the chain-walk fence filter goes).
- FAMILYFENCE-4 (2026-09-29): `run --review-of <id>` and `spawn(review_of=...)` whose writer family this checkout's runner-private record store cannot name (no record, no writer, or an unresolved one) now REFUSE with rc 2 — the message names the store searched and the two ways out (spawn through the MCP/checkout that spawned the writer, or `--not-family`) — instead of warning and planning onto a combo carrying that family; `family_fence` gained the `refusal` field, and the `cross-family not enforced` warning stays only for a review naming neither (`FamilyFenceUnreadableWriterTests`).
- WINFAIL2 (2026-09-29): Windows test fixes — CRLF card fixture in `test_autoos_card.py` (`newline=""` so Windows text mode does not double `\r\n` to 44 lines) and omitted `reasoning_effort` in `run-tests.ps1` (key is dropped, not null — `PSObject.Properties` check replaces the null compare under StrictMode).

### Fixed — the CROSS-FAMILY verdict answers the question the exit code asked (FAMILYFENCE-3 N5, 2026-09-29)

A qoder review fenced off its own assumed family printed

    writer: qoder/Qwen3.8-Flash (qwen) source=assumed-default
    family: writer=unresolved reviewer=unresolved CROSS-FAMILY: unknown
    autoos-agent: no model outside family qwen left - refusing (FAMILYFENCE)   [exit 12]

The sentence said *cannot tell*; the exit code said *this one collides*. Both come from
the post-run backstop, but they read different questions: `cross_family_line` compared
the reviewer family with the AUTHOR's family only, and printed `unknown` for an
unproven reviewer, while `cmd_run` flipped rc to 12 on the serving family being in
`fence["families"]` whether or not any witness attested to it. FAMILYFENCE-b's
requirement 3 was about not CLAIMING independence from an assumption — a weaker reason
to print `yes` — and it was read as a reason to print `unknown` about a collision the
run was being refused for.

- **`fence_collision(family, fence)`** (`tools/autoos-agent.py`) is now the one home for
  "is the family that served a family this fence rules out?" — the author's own family
  or any `--not-family` name, compared in `resolver.family_key` form. The verdict line
  and the backstop read it, so they cannot drift apart again; the refusal text is
  unchanged.
- **The verdict set is `yes | NO | NO (assumed) | unknown`.** A collision prints `NO`,
  marked `(assumed)` when nothing witnessed the model that hit it — the exit code acts
  on an assumption, so the line says so and says how strongly. `yes` stays
  witnessed-only: an unattested reviewer that collides with nothing still prints
  `unknown`, never a claim of independence.
- **Docs** — the module docstring, the MCP `spawn` tool description and
  `.agents/skills/unattended-orchestration` all listed `yes|NO|unknown` and said `NO`
  needed a witness; both now describe the four-value set.
- **Tests** (`tests/test_autoos_spawner.py`, `CrossFamilyProvenanceTests`):
  `test_a_proven_review_on_a_fenced_family_costs_the_run` (renamed from
  `test_a_proven_same_family_review_costs_the_run`, which never tested a *same-family*
  comparison — the author was `unresolved` in it, so what it actually proved is that the
  verdict line stayed `unknown` while the exit was 12: the defect, asserted as
  behaviour), `test_an_unattested_review_on_a_fenced_family_says_no_assumed`, and
  `test_an_assumed_review_outside_the_fence_still_claims_nothing` for the `yes` half.

### Added — a real launch carries the session id and the pin, proven unmocked (FAMILYFENCE-3 N3, 2026-09-29)

Skill R-orch-19: "a mocked-Popen suite can be green while a live spawn drops a flag."
`NativeSessionIdTests` asserts on the dry-run's *printed* argv, so a launch change (the
scope wrapper, the env rebuild, the executable resolution) can break the real
`Popen` and leave the printed plan unchanged. This adds a POSIX stub for `qodercli`
and `claude` that writes its OWN received argv to a file and exits 0; the CLI runs
itself through `run_agent` (no mock).

- **Tests** (`tests/test_autoos_spawner.py`, `RealLaunchArgvTests`): the recorded
  qoder and claude launch must both match `--session-id <uuid>` and the caller's
  `--model`. The qoder stub writes one file into its own clone — the INCOMPLETE
  verdict that penalises an isolated worker that changed nothing would otherwise
  refuse the launch, masking the flag check.

### Fixed — a `not_family` handed over as one bare string fences one family, not its letters (FAMILYFENCE-3 N2, 2026-09-29)

`fence_family_names` iterated its argument directly, which is right for the CLI's
`append` list and wrong for every other caller: `not_family="mimo"` — the shape a
programmatic caller of `family_fence` (a hand-built args namespace, an importable API
call) hands over — fenced the families `"m"`, `"i"` and `"o"`, which the registry
carries none of, and the real family stayed unfenced while the run read as guarded.
The MCP spawn path builds a repeated `--not-family` flag and so was already a list; the
defect was one call below, at the fence's single home. A bare string is now one name
there.

- **Test** (`tests/test_autoos_spawner.py`, `FamilyFenceStringNameTests`): a string
  yields one `family_key`, and `family_fence` built from one string excludes exactly
  that family.
- **Follow-up (same day):** 7508d2a landed that test and this entry but *not* the guard —
  the red/green check reverted `tools/autoos-agent.py` to `HEAD` with `git restore` and the
  fix was never re-applied before the commit, so the lane shipped a failing test. The full
  suite caught it two items later; the guard is the same edit re-applied. Lesson recorded
  where it belongs: `git restore --source=HEAD -- <file>` is a *destructive* way to prove a
  test is red. Prove red on a copy of the tree, or re-read the diff into the commit message
  before committing.

### Fixed — the free fallthrough re-plan carries the fence into the leg choice (FAMILYFENCE-3 N1, 2026-09-29)

`_free_fallthrough_plan` filtered the fenced families out of the chain it walks, then
rebuilt the next attempt with `build_plan(args, cfg, sandbox=...)` — without the
`fence=` argument the gateway fallthrough's re-plan already passes. `build_plan` answers
the fence for whatever leg IT picks, so the re-plan was one leg away from serving the
family the chain walk had just refused. The re-build now carries the same fence object.

- **Test** (`tests/test_autoos_spawner.py`,
  `FamilyFenceFreeChainTests.test_the_free_fallthrough_re_plan_carries_the_fence_into_build_plan`):
  the re-plan's `build_plan` call must receive the fence, not a rebuilt or empty one.

### Fixed — a `--not-family` name the registry does not carry is refused, not a silent no-op (FAMILYFENCE-3 B2, 2026-09-29)

`run --client opencode --card role=review,complexity=trivial --free --isolate --lean
--not-family mimo --dry-run` exited **0** and planned the run: `mimo` names no family
the registry declares — `opencode/mimo-v2.6-flash-free` is family `xiaomi` — so the
fence excluded nothing while the run read, to its caller and to any later judge, as a
guarded review. A typo in a safety flag must not downgrade the flag to a comment.

- **`registry_family_names`** (`tools/autoos-agent.py`): the known families are the
  `models` rows' and `policy.reviewers` rows' `family` fields in `resolver.family_key`
  form — the same two sources `reviewer_family` compares against, so the name check and
  the fence cannot drift. `cmd_run` checks the names *this caller typed* (a writer
  family read from a kill record is already the registry's own answer) right after the
  fence is built and before any leg is chosen; an unknown name exits 2 naming it and
  the known families (`fence_name_refusal`). An unreadable registry, or one that
  declares no family at all, is not evidence a name is wrong — the check stands down.
- **Tests** (`tests/test_autoos_spawner.py`, `FamilyFenceUnknownNameTests`): the live
  `--not-family mimo` case (rc 2, names `mimo`, lists `xiaomi`, plans nothing), a stub
  refusal that launches nothing, and the regression that a known name — any spelling of
  it — keeps the fence's own exit 12.

### Fixed — an own-account run records the route it actually ran (FAMILYFENCE-3 N4, 2026-09-29)

`run --client qoder --tier 1 --model Efficient --dry-run` printed
`route: t1-orchestrator reason=explicit-tier routing=1`, and the same lie reached the
worker record and the run log: `t1-orchestrator` is an OmniRoute combo, and a native
client never resolves one. `build_command` gives qoder/claude/agy the model pin (or the
registry's `default_model`) verbatim, the `MODEL_INPUT` row for those clients carries no
`("route",)` kind at all, and `own_account_track_entry` returns `None` for a non-gateway
client precisely because "an own-account client never ran the gateway route it names". The
route line was the one place still naming a route the run could not have run — so `ps`, the
record and the log reported a gateway combo for a run that answered on the client's own model.

- **`build_plan`** (`tools/autoos-agent.py`): when the client is not a gateway client, the
  recorded combo is `native:<client>` (e.g. `native:qoder`). The rewrite happens *after*
  `resolve_route`, on purpose: the FAMILYFENCE leg check and the PRIV3 sensitive-combo check
  both read the card's real gateway combo, and standing them down alongside the label would
  have opened a hole rather than closed a mislabel. `combo_legs("native:qoder", registry)`
  answers no legs, which is the honest input to provider-benching, and the fallthrough
  exclusion set now carries the same label the plan recorded.
- **Tests** (`tests/test_autoos_spawner.py`, `NativeComboTests`): the live dry-run line for
  qoder, claude and agy names `native:<client>` and not a `t…` combo, the in-process
  `build_plan` route dict carries the native combo (with its tier and its `--model` pin
  intact in the argv), and a gateway `--client opencode --card role=implement` keeps its real
  resolver combo — the rewrite is for own-account clients only.

### Fixed — the fence judges the model that serves, not a combo that never does (FAMILYFENCE-3 B1, 2026-09-29)

A live smoke from the FAMILYFENCE-b tip refused a run it must not:
`run --client opencode --card role=review,complexity=trivial --free --isolate --lean
--not-family qwen --dry-run` exited 12 — `no model outside family qwen left` — although
the `--free` head (`opencode/muse-spark-1.3-contributor-free`, family meta) sits outside
that fence. `--not-family meta` announced the walk onto nemotron and *then* refused the
same way; only `--not-family nvidia` ran. The cause was the combo-leg check
(`fence_blocks_route`: a route is fenced when ANY leg is, because OmniRoute can fall
through to it) applied to runs no combo ever serves: every t1/t2/t3 combo carries one
qwen leg and one meta leg, and a `--free` run resolves no combo at all — the free chain
head is the serving model, and it was already fenced by `fence_free_head` before the
plan. The check ran anyway, and ran even when `--free` meant no combo served the run.

- **`model_decided`** (`tools/autoos-agent.py`): when `--free` or an own-account
  `--model` pin has already picked the serving model, the fence is judged on that model
  (`fence_free_head` / `fence_blocks_model`) and `resolve_route_unchecked` /
  `_resolve_route_v2` skip the combo-leg refusal *and* the fenced-route exclusion —
  the exclusion would have left the resolver an empty route table, trading the wrong
  rc 12 for a wrong rc 2. A gateway `--model` pin is still a combo run (OmniRoute
  resolves it to legs), so the combo check keeps its teeth there.
- **A combo refusal now names the way out**: the route-based refusal prints
  `... - use --free or pin --model outside family <fams>` (`route_fence_refusal`);
  model-based refusals (free chain spent, pin inside the fence) keep the plain line,
  because for them there is no combo to route around.
- **Tests** (`tests/test_autoos_spawner.py`, `FamilyFenceServingModelTests`): the
  three live dry-run cases above (qwen and meta allowed on `--free` with the right
  head, a fence covering the whole free chain still `EXIT_NO_OTHER_FAMILY`), the
  own-account-pin live case, and the combo-refusal way-out line.

### Added — every client records the model that really answered; qoder can be pinned (FAMILYFENCE-b / 2026-09-29)

FAMILYFENCE fenced by family, and 0dc1691 prints a `CROSS-FAMILY` verdict — but for
an own-account client the verdict rested on an *assumption*. Measured 2026-09-29: for
`client=qoder` the runner-private record's `model` was the plan's `QODER_DEFAULT_MODEL`
(`Qwen3.8-Flash`), `exit.json` held only a rc, `ps`/`status`/`result` showed no model,
and `spawn` had no way to name one. qodercli 1.1.63 makes the assumption a lie: an
unknown `--model` is **silently substituted** — it prints `falling back to default
model "efficient"`, answers with the account's promo model (`qfmodel` / display name
`Efficient`), and **exits 0**. A qoder review's family could be neither proven nor
fenced, yet the verdict still read `yes`.

- **Provenance is now in the writer record** (`tools/autoos-agent.py`): the resolved
  writer gained a `source` field — `gateway-log` (the OmniRoute call log),
  `client-reported` (the client's own transcript), `pinned` (an explicit `--model`),
  `assumed-default` (only the plan guessed). `WRITER_PROVEN_SOURCES` is the first two:
  an *asked-for* model is never evidence of what answered. `writer_is_proven()` gates
  the post-run verdict, so an unproven reviewer prints
  `family: writer=.. reviewer=unresolved CROSS-FAMILY: unknown` — requirement 3's
  `assumed-default → unknown`, never `yes`. The `source=` string rides on the `writer:`
  line so a human reading one run sees why it believes what it believes.
- **qoder/claude report their own model** (`tools/autoos_clients.py`): a new
  `MODEL_REPORT` table names where each client's truth lives. qoder's is
  `~/.qoder/projects/*/<session>.jsonl` (an assistant `message.model` and a
  `runtime-config` line) joined to `~/.qoder/logs/runs/*/manifest.json` (argv → the
  session id) and `qodercli.log` (`model_config={"key":..,"display_name":..}`) — the
  account stores an encrypted catalog, so the key→display translation is read from the
  run log, not `~/.qoder/.models`. `reported_model()` returns `None` when it cannot
  tell, and `None` is the honest answer that keeps `CROSS-FAMILY: unknown`. claude's
  transcript already carries a full model id. The join is exact because both clients
  accept a caller-supplied `--session-id`: `build_plan` mints one per attempt
  (qodercli refuses a duplicate id) and `build_command` puts it on the argv.
- **A pin reaches the record and the fence**: `resolved_writer`'s native branch prefers
  `client-reported` over the `pinned`/`assumed-default` plan model, and
  `resolve_route_unchecked` now fences `--model` for an **own-account** client too —
  qoder never appears in a route, so a fence that only reads routes was blind to the
  whole choice. `--model` (CLI) / `model` (MCP `spawn`) already reached argv; the
  pinned family now feeds `family_fence` like a gateway leg's, and a name the registry
  cannot place stays unsafe for a review role (0dc1691's rule, unchanged) while a write
  run keeps it.
- **Surfaced, never from job.json** (R-orch-17): the kill record is the only source.
  `ps` gained `MODEL`/`FAMILY` columns that prefer the proven writer's model and print
  `?` for an unproven one, plus `model_source`/`writer`/`model_proven` on every row;
  `status`/`result` already return the record's `writer`, so `source` flows through the
  MCP and CLI for every client. `worker_writer(run_id)` reads the record and nothing else.
- **Tests** (`tests/test_autoos_spawner.py`): `QoderSessionEvidenceTests` (a synthetic
  transcript home proves the join is machine-independent — a real `~/.qoder` session id
  answering would be the AGENTS.md-forbidden "passes only on the box it was written
  on"), `WriterProvenanceTests`, `CrossFamilyProvenanceTests` (assumed-default → unknown,
  never yes; a proven same-family reviewer still costs rc 12), `NativeSessionIdTests`,
  `QoderFenceTests`, `PsWriterRowTests` (a record written for another run never answers
  this one — and the other row keeps *its* writer, so the test cannot pass by both
  being `None`).
- **Deviations worth naming**: no `--list-models` pre-flight refusal — that list is
  account/network-bound and the brief asked the post-run verdict be honest, which the
  provenance gate is; qoder stays on text output because `review_verdict()` anchors
  `^\s*VERDICT:`; the *author's* family still fences even when its own source is
  unproven (the conservative direction — refuse a possibly-cross review, never claim a
  cross one).

### Added — a review never silently runs on the writer's model family (FAMILYFENCE / 2026-09-29)

D-115 says a review is independent because it comes from another model family. The
spawner did not enforce that about its *own* choices: measured 2026-09-29, an
ORCH-A1 writer resolved to NVIDIA `nemotron-3-ultra`, both requested cross-family
reviewers (`mimo`, `muse`) hit rate limits, and SB-B's fallthrough walked the
ordered free chain — which lists nemotron too, with no family exclusion anywhere —
onto the writer's own model. The run exited 0 and printed the review. Nothing said
the word "same family"; the label "cross-family" was simply inherited from the
request.

- **The fence is one object and one predicate** (`tools/autoos-agent.py`):
  `family_fence()` settles who may not serve the run *before* any leg is picked, and
  `fence_blocks_model()` answers it at every choice the spawner makes — the `--free`
  chain head, an explicit `--model`, the v1 `select_combo` route, `--tier`, the v2
  resolver's route set and every fallthrough candidate. Families are compared only in
  `resolver.family_key` form and read only from the registry (`reviewer_family`), never
  guessed from a model name, so the rule cannot be restated differently per site.
  `--not-family <fam>` (repeatable) removes those families from the WHOLE plan.
- **A review fences its writer by default**: `--review-of <run-id>` takes that run's
  WRITER family out of the plan without being asked, reading it from the run's
  **runner-private kill record** — never from its `job.json`, which lives in the
  directory the worker owns and would let a writer choose who reviews it
  (skill R-orch-17). A review with neither flag still runs, and says so on stderr:
  `review without a known writer family - cross-family not enforced`. Its silence
  is what got measured.
- **Refuse rather than lie** (new `EXIT_NO_OTHER_FAMILY = 12`, documented beside
  `EXIT_READ_ONLY_WRITE`, in the module docstring's exit-code table, and in
  `autoos_track.py`'s failure-class comment): when every remaining model or route is
  inside the fence the run exits 12 with
  `no model outside family <fam> left - refusing (FAMILYFENCE)` and launches nothing.
  `FamilyFenceRefused` is deliberately **not** a `ValueError`, because every
  fallthrough path already catches `ValueError` as "no leg answered → exit 8", and a
  fence relabelled as an ordinary provider exhaustion is the same lie in a different
  code. An exhausted chain of un-fenced models still exits 8. The track record classifies
  rc 12 as `refusal` like rc 6 — the fence refused, so the route never got the chance to
  be unreliable (`NON_QUALITY_FAILURES` ignores it in `p_success`).
- **`--no-fallthrough`** pins the run to the model it was planned on: a stop there is
  the run's answer, with its own rc and no re-plan onto whatever survived. An
  orchestrator that asked for a verdict from one named model would otherwise get one
  from whichever model answered, and the report says nothing about which.
- **The claim is checked after the run too**: a review prints
  `family: writer=<fam> reviewer=<fam> CROSS-FAMILY: yes|NO|unknown` beside its
  `writer:` line, judged on the *resolved* writer (the gateway call log, not the plan) —
  OmniRoute can fall through to a leg inside a combo the spawner never saw. A resolved
  same-family answer exits 12 instead of 0. A model the registry cannot place is not
  safe for a review (unknown is never evidence of independence); a write-role run keeps it.
- **Callers reach it** (`tools/autoos_agent_mcp.py`): `spawn` takes `not_family`
  (a list of names, validated — a dict would reach the CLI as one flag per key),
  `review_of` and `no_fallthrough`. `FamilyFenceMcpPlumbingTests` includes an
  unmocked real spawn (R-orch-19) asserting `--not-family nvidia` is in the argv the
  runner actually started the CLI with.
- **Tests** (`tests/test_autoos_spawner.py`): `FamilyFenceFreeChainTests` (the
  measured chain: mimo + muse rate-limited, writer family nvidia → `free_models`
  stops at the two honest reviewers, nemotron never launched, rc 12; the forged
  `job.json` that loses to the kill record; the unknown-family skip; exit-8
  exhaustion preserved), `FamilyFenceRouteTests`, `NoFallthroughTests`,
  `CrossFamilyReportTests`, `FamilyFenceRecordTests`, `FamilyFenceMcpPlumbingTests`.
- **Deviation worth naming**: there is no `verify` card role in either card dialect
  (v1 `role` is orchestrate|implement|review, v2 `kind` adds plan/research/bulk/
  debug/final), so the role default fires for `review` — v1 or v2 spelling — and for
  `--tier 3`, which is the reviewer agent.
### Fixed — the green record is the runner's, not the worker's (PREPUSH-3, 2026-09-29)

PREPUSH-2 made the record's *shape* unreadable to a forgery; the forge was still
trivial because the file it had to match sat in `<git-dir>/autoos-prepush.log`,
inside the checkout the worker writes in. The certificate now lives in a
runner-private store, `<state-dir>/prepush/<sha>.json` — the sibling of the
spawner's kill record, same `clients.state_dir()` base, same 0700 dir / 0600
file, the path imported from `autoos_clients` rather than restated, so the two
stores move together. One JSON record per commit (the newest wins, so a green
after an override still readies a sha and an override after green unready it),
written atomically by temp + rename. It binds what a green run actually implies:
the commit sha, that commit's *tree* hash, the sorted manifest of commands the
gate ran, each command's exit result and its parsed `N passed` count, and the
UTC stamp. `--check-ready <sha>` recomputes the sha's tree from the repo and
accepts only a record whose sha AND tree match what git says now, whose stamp
parses, whose manifest and results are both non-empty, and whose every result is
ok: the sha check is what refuses a lane that amended after a green run (the
record is filed under the commit it ran on), and the tree check is what refuses a
record that names a commit it never actually verified. The old log file stays as a
human-readable annotation and readiness never reads it. Finally, a gate run that
carries `AUTOOS_AGENT_RUN_ID` — the mark the spawner puts into every worker — runs
the checks, prints its verdict, and writes no green record: a worker cannot
certify its own push, only the orchestrator that merges the lane can. `ready`
needed no change; it asks `local_green()`, which is where the store is read. The
reader is guarded the same way as the writer, because the default state root is
`<checkout>/logs`: a runner that was never given `AUTOOS_STATE_DIR` keeps its
records under the very tree it certifies, so under the worker's mark a store that
resolves inside that checkout is refused outright instead of believed — otherwise
a hand-written JSON of exactly the gate's shape would be a self-issued certificate
(what the spawner's env does to `AUTOOS_STATE_DIR` is not a boundary a gate may
depend on). Measured on this lane's own sandbox: `--check-ready HEAD` answers with
that refusal, and the refusal names its remedy.
Tests: `LogRecordTests.test_the_record_binds_the_tree_the_commit_carries_and_the_sorted_manifest`,
`test_the_record_stores_each_commands_result_and_parsed_counts`,
`test_the_record_is_written_privately_and_atomically`,
`test_the_store_lives_outside_the_checkout_the_worker_writes_in`;
`CheckReadyTests.test_a_forged_line_in_the_worktree_log_certifies_nothing`,
`test_a_record_that_binds_another_tree_is_not_green`,
`test_a_record_with_a_red_result_is_not_green`,
`test_a_green_record_does_not_carry_an_amended_commit`,
`test_the_record_the_gate_writes_is_the_record_the_gate_reads` (round trip
through the real gate); `WorkerRecordTests.test_a_worker_run_reports_green_and_records_nothing`,
`test_a_worker_run_still_refuses_a_red_tree`, `test_the_same_gate_outside_a_worker_run_records_green`,
`test_a_record_kept_in_the_workers_own_checkout_certifies_nothing`;
and on the spawner side `ReadyCommandTests.test_a_forged_worktree_log_line_does_not_carry_ready`.

### Fixed — the PREPUSH gate reads its own record shape and nothing else (PREPUSH-2, 2026-09-29)

`local_green()` and `--check-ready` called *any* second field that was not `OVERRIDE` a
green record, so one line appended to the log certified a push that never happened:
`echo "$SHA anything-at-all" >> <git-dir>/autoos-prepush.log` and the sha was ready
(measured on the lane's own HEAD, ee92774). The log lives in the git dir, where any
process that can write the checkout can append to it, and `ready` is the last gate before
main moves — a substring test was never enough. `parse_record()` is now the file's only
reader: it accepts `green_line`'s `<sha> <utc> green: <commands>` and the override's
`<sha> OVERRIDE <reason>`, requires the full 40-hex sha and the UTC stamp the gate stamps
itself, and ignores every other line. `green_records()` is the one predicate both
`local_green()` and `--check-ready` ask, so the two cannot drift apart.
Tests: `CheckReadyTests.test_a_forged_free_text_line_is_not_green`,
`test_only_the_two_shapes_the_gate_writes_count_as_records`,
`test_a_malformed_line_is_ignored_and_hides_no_real_record`, and a round trip pinning the
writer to the reader.

- **The root cause of both CI 36529545083 reds is the mapping, not the two tests.** A lane
  that adds `tests/test_x.py` was sent only `tests/test_x.py`: `SuiteWiringTests` (every
  `tests/test_*.py` must be *run* by a harness) and `RepoLintTests` (every POSIX-only
  pattern in one must carry a Windows guard) are tree-wide *scans*, so they name no file
  and no case body mentions them — the lane ran the file it had written and CI went red in
  the two lints that read it. `tools/affected-tests.py` now keeps `REPO_META_SCANS`, a
  `(lint, scanned-paths)` table: a change to `tests/test_*.py`, a suite harness
  (`tests/linux/*.sh`, `tests/run-tests.ps1`), a workflow, or any `.ps1`/`.psm1` selects
  the lint that scans it. A new tree-wide lint over the suites must be added there the way
  a new harness must be added to `test_suite_wiring.py`'s `WIRING` — an unstated scan is an
  unselected one. Test:
  `MappingTests.test_a_test_file_change_pulls_the_repo_wide_lints_that_scan_the_suite`,
  with `test_an_ordinary_change_selects_no_repo_wide_lint` holding the other side.
- **CI1 (shard b): `tests/test_prepush.py` is wired into a harness**, with the same
  `python3 tests/<name>.py` run shape as its neighbour `test_affected_tests.py`, in
  `tests/linux/33-documentation.sh`. `tests/test_suite_wiring.py` refuses a unit-test file
  no harness runs, and 60 gate cases had been sitting unexecuted since the file landed.
- **CI2 (shard f): its bash fixtures carry a Windows guard.** Seven `#!/usr/bin/env bash`
  and `#!/bin/sh` stubs — the `run-tests.sh` fakes and the foreign-hook fixtures — had none,
  which is what `RepoLintTests.test_posix_guards_are_clean` is there to catch. The guard sits
  on `RepoFixture.suite_is_green`/`suite_is_red` and `RunListTests.capture_suite`, plus the
  four cases that write a shebang themselves, so every test that builds a suite stub skips
  on Windows by itself instead of 30 cases each repeating the reason.

### Fixed — the gate's installer fails closed and keeps its own promises (PREPUSH-2, 2026-09-29)

Four defects at the edges of the gate, each with a test that names it:

- **A hook whose gate is missing refused the push (NB2).** The shim
  `trust_worktree.py` installs printed `no $root/tools/prepush.py -- nothing was
  checked` and exited **0**, so a lane that lost the gate — rebased onto a base
  without it, checked out an older branch — pushed exactly the untested sha D-154
  exists to stop, while the installer's own message said the gate was installed. A
  check that could not run is not a check that passed. It now exits 1, points at the
  `AUTOOS_PREPUSH_OVERRIDE` that leaves a record `--check-ready` refuses, and does
  **not** exec the chained hook: the chain is what runs *after* the gate passes, so
  handing over would let an operator's `exit 0` push the branch anyway. Tests:
  `HookInstallTests.test_a_gate_that_is_not_there_refuses_the_push`,
  `.test_a_gate_that_is_not_there_does_not_hand_over_to_the_operators_hook` — both a
  real `git push` against a bare origin and its ref list, which is also how the first
  cut's unterminated quote was found.
- **`2` means the gate could not run (NB3).** The docstring promised it and every
  path returned 1: no checkout, no HEAD commit, no `tools/affected-tests.py`, a
  mapper that died or printed no JSON. 1 is a verdict the lane can act on; the
  absence of a verdict is not, and a caller that waits on "not ready yet" waits
  forever on a checkout that cannot answer — `tools/autoos-agent.py`'s `ready`
  already splits its own git failures that way. The locating helpers raise
  `GateCouldNotRun`, `main` maps it, and the refusals read as the `REFUSED` constant.
  Test: `CouldNotRunTests` (six paths, plus
  `.test_a_red_check_is_still_a_refusal` holding the other side).
- **The install dir is where git says it is (NB1).** `hooks_dir` asks
  `git rev-parse --git-path hooks`, which is the only answer that tracks
  `core.hooksPath`; a path built from `.git/hooks` installs a gate no push reads on a
  host that set it. That is how `dbba511` already wrote it, so the tests pass at this
  HEAD: they are the regression pin, and were checked by hardcoding `.git/hooks` and
  watching both go red. Test:
  `HookInstallTests.test_core_hooksPath_moves_where_the_gate_installs_and_git_still_runs_it`,
  `.test_a_relative_core_hooksPath_is_resolved_against_the_checkout`.
- **One home for the gate's git helper (NB6).** `_git` existed twice, in
  `tools/prepush.py` and in the skill's `trust_worktree.py`, and the only caller is
  `hooks_dir` — so the copy that can drift is precisely the one that decides where
  the gate lands. The skill now imports the gate's helper, lazily and *after* the
  `tools/prepush.py` existence check, because a repository without the gate must hear
  "nothing installed" rather than an ImportError. Test:
  `HookInstallTests.test_the_git_helper_has_one_home_and_lives_in_the_gate`.

### Added — a pre-push gate, so a lane cannot be ready at a sha it never tested (PREPUSH, D-154, 2026-09-29)

Three lanes were green at home and red in CI, in three different ways: **SCOPECLI** (CI
36517453134) cited rule R-orch-17 that existed only on a newer main — it never merged main and
never ran `tests/test_skill_rules.py`; **SBA** (CI 36493098467) had a fixture `git commit` die
with rc 128 on the runner, because it passed at home only thanks to the dev host's global
`user.name`/`user.email`; **FREEKEYS2** (CI 36506339556 shard e) shipped 17 red render tests
because the worker ran the pytest files it *guessed* were relevant, not the ones its own changed
files imply. Each is a missing run, not a missing fix — so the gate computes the run list from the
diff and refuses the push.

- **`tools/prepush.py`** (new; one home for the logic, the hook is a three-line shim onto it): the
  fetched `origin/main` must be an ancestor of HEAD (R-coord-01), with no network; CI's own plan
  check runs first (`tests/test_ci_shards.py`, `tests/ci-shards.py`); the rest of the run list
  comes from the changed files; the suite runs in **CI's git env**
  (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, inherited `GIT_AUTHOR_*`/`GIT_COMMITTER_*`
  dropped) under `/usr/bin/python3`, so a test that leans on a host identity or a venv shim fails
  here instead of in CI. Green appends `<sha> <utc> green: <commands>` to
  `<git-dir>/autoos-prepush.log` (per worktree, never tracked); red exits 1 and prints the failing
  command. A bash run that examined *nothing* is red too: `run-tests.sh` exits 0 on a filter that
  matched no case, printing `passed 0 failed 0 skipped 0`, so the gate reads the tally as well as
  the status — R-worker-05's "'no tests ran' exited 0 and was pushed" in this tool's own shape.
  `AUTOOS_PREPUSH_OVERRIDE="<reason>"` steps over it loudly, for orchestrators, and is
  logged as **never green**.
- **`tools/affected-tests.py`** gained a file-based question: `--changed-files-from REV` (with
  `--format plan` for the gate). The mapping rules are the three reds — skills, `CHANGELOG.md` and
  `docs/**` always pull `tests/test_skill_rules.py`; registry/route/combo files pull the render
  and sync tests *plus* the bash `render`/`apply` filters; `tools/X.py` pulls the tests that name
  it; a changed `tests/linux/NN-*.sh` pulls that part. Two gaps found while running it against
  this very change: the id corpus attributes a mention to one *case* (so a test file that builds
  the tool's path in a module-level constant named it in every case and in none), and it reads
  only `tests/` (so a skill's own tests under `.agents/skills/<name>/tests/` were invisible).
  Every changed `.py` is now answered a second way, by the pytest files whose text names it, and a
  changed test file is run whichever directory holds it. A bash part is never run unfiltered
  (R-host-08).
- **`autoos-agent.py ready`** now reads that log (D-110): reviews and a pushed sha prove the lane
  was looked at and shipped, only the record proves it was run — which is what catches a lane that
  pushed with `git push --no-verify`, a path no hook can reach. An orchestrator that means to waive
  names a reason with `--allow-unverified "<reason>"`, and the reason rides on the line.
- **`trust_worktree.py`** installs the hook into `git rev-parse --git-path hooks` as part of
  approving a fresh worktree, and **chains** a pre-push hook it did not write
  (`pre-push.autoos-chained`) rather than replacing it. It is idempotent (a second run reports
  `skipped`) and reachable alone via `--hook-only`, because rule D-111 keeps this path away from
  `~/.claude.json` — the hook step never reads or writes that file.
- **Skill**: `R-coord-12` and the code-enforced list in `unattended-orchestration/SKILL.md` state
  the rule; the gate's own docstring stays the source of truth for its order.
- **Tests** (`tests/test_prepush.py`, 50; 8 more in `ReadyCommandTests`): each refusal shape, the
  CI-like git env proven behaviourally (a temp global config that the gate forces to `/dev/null`,
  so the fixture commit fails 128 — and a repo carrying its own identity still passes, so isolation
  is not a wall), the green and override records, `--check-ready`'s exact-sha match, the hook
  install's idempotence and chaining with `HOME` pointed at a temp dir (asserting `~/.claude.json`
  is never created), and the file→tests mapping against the real tree for all three measured reds.

### Removed — the push and dispatch grants, and the fencing that only shaped them (ORCH-A1 phase 1 round 13, routing-00 D-159)

A pre-granted lane push was a capability the profiles could not scope per session, and the
12 rounds of denies that followed it fenced only command *text* — every one of them shaped the
grant rather than guarding anything the classifier could not decide. Round 13 deletes the grant
and its shaping fence set, and keeps the one deny that protects a shared asset.

- **Gone** (`tools/launch_profiles.py`): `COORDINATOR_ALLOW`, `L2_PUSH_ALLOW`,
  `L2_WORKFLOW_ALLOW` and everything that existed to narrow them — `L2_PUSH_DENY`,
  `_l1_push_deny`, `_l1_refspec_deny`, `_l1_workflow_deny`, `L1_PUSH_DENY`,
  `L1_WORKFLOW_DENY`, `L1_REFSPEC_DENY`, the repeated-`--ref`/`-r`/`-R`/`--repo`/empty-`--ref`
  dispatch denies, the round 6–8 character classes (tab/CR/LF, quotes, `$`, backtick,
  backslash, `{`, `&`, `>`, `<`, `;`, `#`), the no-ref / `:` / `+:` / `@` denies, the bare
  `git push` deny, the `git -C`/`--git-dir=`/`-c` wrapper denies as such, the dead
  refspec-destination classes, and the now-unused `LANE_PREFIXES` (LOW-7). Only
  `l1-routing`'s `apply.sh` run grant survives; every other profile's `allow` list is empty.
  Those commands are **unlisted** now, so they reach the classifier — which, unlike a deny,
  can explain itself to the session that hit it.
- **Kept:** push-to-`main` in every spelling git accepts. The fence anchor became
  `*git*push` — a glob between the tokens, not the contiguous literal `git push` — so dropping
  the three wrapper blanket denies did not open a main hole: `git push origin main` and
  `git -C x push origin main` are denied by the same entry, while `git -C x push origin L1-x`
  is not. `main` is fenced as a whole ref name (`main`, `heads/main`, `refs/heads/main`, proven
  equivalent with real git) in every argv position (after a space, a colon or a `+`), plus the
  push-wide flags that reach `main` without naming it (`--force`/`-f`, `--all`, `--mirror`,
  `--tags`/`--follow-tags`, `refs/tags/`, `tag `) and `:HEAD`, which creates a stray branch
  instead of moving `main` but must not be free. The dispatch fence keeps only the `--ref`
  main spellings. `docker push registry/app:main` stays outside the fence — an image tag is not
  a branch. Secret fences, the `~/.claude.json` fence, the always-deny set and the leaf fences
  are untouched.
- **`tests/fixtures/push-corpus.json` + `tests/test_push_corpus.py`** (new): every push
  spelling the 12 earlier rounds ever fenced, 213 rows, each one
  `{cmd, expect: deny|allow-lane|prompt, why, round}`. `deny` rows are the fences' witnesses,
  `prompt` rows name what round 13 stopped fencing and who owns it now. `RealGitPremiseTests`
  moved here from `tests/test_launch_profiles.py` and the push tables left with it (2836 → 631
  lines). Both wired into `tests/linux/33-documentation.sh`.
- **A fence is a decision on a real command:** every rendered push/dispatch deny rule must match
  a corpus `deny` row (`FenceWitnessTests`), which is what surfaced three rules nothing could
  reach — a refspec carries exactly one colon, so `*:main:*` was dead. `A1-D5`'s contradictory
  `allow` target for those fences is now the corpus witness too, instead of a second
  hand-kept table.
- **Residuals, recorded not fenced** (spec §3.3, owner **HOOKS H2**): MED-3 (git config,
  aliases, functions — invisible to text), MED-4 (glob refspecs), MED-5 (a ref name rewritten or
  substituted before push). The handoff is written on both sides — the H2 row of
  `docs/plans/2026-09-28-agent-hooks-spec.md` §6 now carries the same three ids and the corpus
  rows that pin them, because an owner that was never told is a handoff that did not happen.
  Until H2's real-argv guard lands, the guard on those is the
  classifier plus the kept main fence plus server-side protection — and `main` currently has
  neither protection nor rulesets (measured), which stays an open operator action.
- Spec `docs/plans/2026-09-28-orch-a1-role-launch-profiles-spec.md` §3.1–§3.3, A1-D4, A1-D5,
  Q4/Q6/Q9 and all six `configuration/launch-profiles/*.settings.example.json` (via
  `render --out`, never by hand) updated in the same change.

### Added — role launch-profile templates rendered from the harness fences, with scope and contradiction tests (ORCH-A1 phase 1)

*Rounds 1–12. The push and dispatch grants described below, and the denies that shaped them,
were removed by round 13 (the entry above); the render tool, the harness-fence source and the
secret/main fences are still what ships.*

- **`configuration/launch-profiles/<role>.settings.example.json`** (new, six
  roles): per-role pre-reviewed grant bundles (spec
  `docs/plans/2026-09-28-orch-a1-role-launch-profiles-spec.md` §2–§3).
  The L1 roles (`l1-coordinator`, `l1-routing`) pre-grant lane-branch push
  and `gh workflow run`; `l1-routing` alone adds the gateway `apply.sh` run
  grant; `l2-orchestrator` (round 3, D-138, tightened round 4) pre-grants
  only push to `L2-*` lane branches and `gh workflow run --ref L2-*`,
  under the same always-deny fences (deny wins). The L2 grant is per
  role, not per session: any `l2-orchestrator` session may push/dispatch
  on any `L2-*` lane branch (per-session scoping is not expressible in a
  per-role profile). Round 4 renders L2-only deny fences into that one
  profile so the `L2-*` allow glob cannot span a refspec colon, a `refs/`
  path or a delete flag — only `git push [-u] origin L2-<name>`
  (same-name push, destination = source) is allowed there, and branch
  deletion is an explicit deny; round 5 constrains the grant by shape,
  not tokens (enumerating tokens missed `L2-x L1-foo` and `L2-x --prune`):
  anything after the branch token denies (a second argument of any kind)
  and any option before the ref denies but `-u` (long options via one
  shape, short force/delete flags enumerated — a lone `-*` shape would
  shadow the `-u` allow since deny beats allow), so exactly one `L2-*`
  ref, same-name, no options except `-u` is allowed there. The L1 grants
  are unchanged.
  `l0-router` and the leaves carry no pre-grant.
  Every profile denies push-to-`main` (fence set, not one string: ref
  spellings plus `--all`/`--mirror`/`HEAD`/bare, compound and prefixed
  spellings; round 13 widened the anchor to `*git*push` so a `git -C` /
  `--git-dir` / `-c` wrapper is fenced by the same entry as the plain
  form, and dropped the wrapper blanket denies), secret access and
  `~/.claude.json` writes. Rules live under `permissions` (the only shape
  the CLI reads); fail-closed for non-interactive sessions comes from the
  launch flags, not from this file. Runtime `<role>.settings.json` files
  are git-ignored.
- **Text fencing is blind, so the profiles are not the last line** (rewritten by round 13 to match spec §3.3): a command-text fence cannot see git config, an alias or which branch is checked out, so a bare `git push` on `main` carries no `main` token to match — round 13 leaves that command *unlisted*, i.e. to the classifier, and the guard that reads argv is HOOKS H2's `git-push-to-main` PreToolUse hook, not a wider glob here. Server-side protection is the one check that depends on neither, and `main` has none (measured: unprotected, rulesets empty); enabling it is a deferred operator action, not this lane's.
- **`tools/launch_profiles.py`** (new): the one home of the deny render —
  `read_deny_all` plus the secret/credential entries of `bash_deny_all`
  plus the profile-specific always-deny entries. `render --check` fails
  naming each drifted template, in the `tools/registry.py` style. No
  `--dangerously-skip-permissions` anywhere; consumption (`--settings`
  wiring), fleet cutover and REVIVE stay later lanes.
- **`tests/test_launch_profiles.py`** (new, stdlib unittest): branch-scope
  and secret-scope tables over the rendered matchers (documented matcher
  model: `Bash(cmd:*)` prefix / exact / `*` glob with `&&`/`||`/`;`/`|`
  splitting and `Read/Edit` path-glob forms, deny wins) and the A1-D5 contradiction test (a contradictory `allow` still
  decides `deny`; precedence re-checked at CLI 2.1.283/2.1.267 —
  `claude --help` prints no precedence rule). Wired into the Linux suite
  (`tests/linux/33-documentation.sh` "launch profiles: ...").
### Fixed — a run now records and announces which scope path it took (SCOPECLI-b, 2026-09-29)

L1-main's evidence from the SCOPECLI mechanism: on WSL a `run --isolate` sat in `0::/init.scope`,
no `autoos-worker-*` unit existed, and **nothing said so** — not `ps`, not the run log. A reader
could not tell a host that has no scope mechanism from one whose scope failed to start, and the two
need different cancellers: a scope is a cgroup and reaches a `setsid()` child, the fallback is a
process-group kill that cannot.

- **`tools/autoos-agent.py`**: `scope_decision()` frames one dict —
  `{"path": "scoped"|"inherited"|"unscoped", "unit": <unit or null>, "reason": <why>}` — and it is
  the only place the three paths are weighed. `run_client` launches from it (the two gates moved
  there verbatim), `_worker_record_start()` writes it into the live worker registry record so
  `ps`/`ps --json` say it *during* the run, `_worker_record_end()` carries it through the rewrite,
  and `cmd_run` prints `scope:` beside the `writer:` line. `inherited` names the outer unit read
  out of `/proc/self/cgroup` — what a canceller actually stops — not the id `run` was handed, which
  a fallthrough re-run re-mints. Nothing is written into `job.json`: the worker shares its uid with
  that file, so this stays in the runner-private records (R-orch-17).
- **The fallback is loud (POSIX)**: `SCOPE_WARNING` puts one line on the spawner's stderr —
  `autoos-agent: WARNING client runs UNSCOPED (<reason>): cancel falls back to the
  process-group kill`. Windows is not shouted at; it has no scope to miss.
- **Reasons, not silence**: `_probe_scope()` answers with the reason it hit (`""` = supported) —
  `windows`, `no systemd-run`, `no systemctl`, `no user manager / XDG_RUNTIME_DIR`, `user manager
  unreachable` — and `scope_unsupported_reason()` caches that one string while `scope_supported()`
  became its bool projection. The gate and the record can no longer disagree, because there is only
  one probe behind both.
- **WSL / `0::/init.scope` (c)**: `worker_scope_unit_from_cgroup()` is the single reader of a
  cgroup line for both `in_worker_scope()` and the inherited unit name, and it matches a *path
  segment* shaped `autoos-worker-*….scope`. `0::/init.scope` is the session's own cgroup and
  yields nothing, so a run there with a reachable user manager still gets a scope; the probe never
  consulted the cgroup at all, so it rejects nothing either.
- **Tests** (`tests/test_autoos_spawner.py`, new `ScopeRecordTests` + `CliScopeLaunchTests`): each
  path value with its reason, each probe reason against a faked PATH/env/launch, the exact warning
  line, Windows quiet, the registry record carrying `scope` at start and at end and `list_workers`
  row-ing it (a pre-SCOPECLI-b record lists with `scope: null` rather than a guess), a full
  `cmd_run` asserting `scope:` beside `writer:`, and the cgroup table with the synthetic
  `0::/init.scope` line. `test_a_host_with_no_user_manager_is_unchanged` asserted the fallback was
  *silent* (`err == ""`) — that silence was the defect, so it became
  `test_a_host_with_no_user_manager_launches_the_same_argv_and_says_so` and now asserts the warning
  while keeping the unchanged argv/env assertions.

### Fixed — a `run` started from a shell launches its client in a scope too (SCOPECLI, 2026-09-29)

SB-A2 item A gave a worker a cgroup so a cancel could follow a `setsid()` child, but only
on the MCP path: `run_job` wraps the whole `autoos-agent.py run …` in
`systemd-run --user --scope`. A `run` started straight from a shell reached `run_client`
unscoped, so its client led nothing but a new session — no cgroup at all, and
`systemctl --user stop` had no unit to stop, leaving only the group kill that a
`setsid()` grandchild escapes.

- **`tools/autoos-agent.py`**: `run_client`'s POSIX branch wraps the launch in
  `worker_scope_launch()` when **both** gates hold: `scope_supported()` says a user
  manager is reachable, and `in_worker_scope()` says no worker scope is already around
  this process. `in_worker_scope()` reads the unit out of `/proc/self/cgroup` through
  `self_cgroup()`, one small read so a test can hand it a synthetic cgroup. The second
  gate is load-bearing: `systemd-run --scope` does not nest — the new scope lands as a
  sibling under `app.slice` (measured on this host) — so an MCP-spawned run that scoped
  its own client would move it out of the outer cgroup, and the MCP `cancel`, which stops
  the outer scope by name, would stop reaching it. Windows and a host without a user
  manager run exactly the code they ran before.
- **Unit name**: `cli_scope_unit()` → `autoos-worker-cli-<run id>-a<attempt>.scope`, or
  `…-cli-pid<pid>-a<attempt>…` where the run has no id. `cli-` keeps it out of the runner's
  own `autoos-worker-<run id>.scope` name space, and the attempt number matters because the
  fallthrough loop re-starts the client: a reused unit name is a `systemd-run` failure, not
  a no-op. One line on the spawner's own stderr names the unit and the command that stops it,
  since a shell user is the only canceller such a run has.
- **Unchanged by construction** (measured, not assumed): `--scope` keeps the child's pid,
  its process group and both pipes, so `start_new_session`, the group reap, the capture
  pump and `record_attempt_group()` see the shape they saw unscoped, and the client's exit
  code propagates through the wrapper (7 in, 7 out).
- **Tests** (`tests/test_autoos_spawner.py`, `CliScopeLaunchTests`): the scoped argv
  (`systemd-run --user --scope --unit autoos-worker-cli-<id>-a2 --collect`), the inner
  `env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS` with the bus address given to
  systemd-run only (SCOPEBUS), no nesting when a worker scope already holds us, unchanged
  argv/env with no user manager, `in_worker_scope()` against cgroup v1 and v2 lines, a
  delegated sub-cgroup and near-miss names, and an end-to-end case that mocks no launcher:
  a real client reads its OWN `/proc/self/cgroup` and names the unit (it skips where the
  host has no reachable user manager; it passes against the real one here).
- **Residual, stated**: where the probe says yes and the launch still fails (a user
  manager that dies mid-run), the run exits with `systemd-run`'s code and the client never
  starts — there is no silent fall back to an unscoped launch. The alternative is worse: a
  run cannot tell `systemd-run`'s rc 1 from the client's own, so any fallback would be
  guesswork, and the thing it guessed past is exactly the uncancellable run this closes. The
  failure is loud: the stderr line names the unit the run tried to use. One cached probe
  (`python -c pass` in a scope) runs per process, as it already does on the MCP path.
- **Coupled test fixed in the same act** (`WinshimClientResolutionTests`):
  `test_on_posix_a_resolvable_client_still_runs` asserted `argv[0] == <shim>`, which is only
  true on a host that cannot scope. It now asserts the resolved file is in the argv and the
  bare name is not — the WINSHIM fact — so it holds in both launch shapes.
### Fixed — the REST adoption c7ca607 added is hardened (APPLYADOPT-2, 2026-09-29)

Eight non-blocking findings from review of c7ca607, all in `configuration/omniroute/apply.sh`.
Each was a sentence the run could print that was not true, or a credential in the wrong place:

- **"(no previous version)" could be a guess.** The retry path printed it whenever
  `LIVE_COMBO_HELD` did not name the combo — including a run whose live combo list was never
  read (`! live combo list unreadable - replacing every combo`), where the delete had just
  removed a tier nobody had looked at. It now says
  `! <name> creation failed - previous version unknown (live list unreadable)` in that case, and
  keeps "(no previous version)" only when the store *was* readable and did not hold the name.
- **A restore could put back a shorter tier.** `live_combo_norm` skipped a step whose shape it
  did not understand and printed the refs it did read, so the restore wrote a combo with the
  unknown leg silently missing and announced "previous version restored". The normaliser now
  prints a fourth column — the number of steps it could not read — and the retry path refuses to
  restore a combo whose count is non-zero: `! <name> creation failed - previous version LOST,
  restore refused: … recreate <name> from your own record …`. `--drift` tolerates the extra
  column (it compares legs, and a short leg list is already drift).
- **An inactive connection counted as registered.** `isActive: false` is the dashboard's
  disabled toggle: the row exists and routes nothing, so the old code promised the operator a
  working provider and never added the one connection that would work. A disabled connection is
  now *not* registered — and apply adds no second one either, because that is not the fix; it
  prints `! <id> has an inactive connection - enable it in the dashboard` and moves on. Same rule
  on the node-bound path (`register_provider` re-reads the binding after the node check).
- **Another node's connection could be adopted by name.** `provider_connection_exists` matched
  `provider == <node-id>` **or** `name == <provider-id>`, and the name is only ever the registry
  id apply itself passes — so a connection left behind on a *different* provider node (a renamed
  or re-created node) satisfied the check, and the provider read as registered with its key bound
  to an endpoint the registry does not name. The name now decides only for a connection carrying
  this node's id or the registry id as its provider; the node id alone is sufficient.
- **A truncated page was read as the whole list.** `{"connections":[…],"total":N}` with `N`
  beyond the rows served is a slice, and deciding "not registered" from a slice adds a duplicate
  connection. `omni_connections` refuses it (exit 2 from the parser), the Providers step falls
  back to the CLI's list, and the one line it prints names the actual condition —
  `! the gateway's connection list is partial - deciding from the CLI's local store instead` —
  instead of the generic "unreadable".
- **The key-carrying body outlived the parse.** `GET /api/providers` returns every stored
  `apiKey`; `REST_BODY` was left set after the rows were read, so anything later in the run that
  prints a captured body could find that one. Cleared the moment the parse has consumed it.
- **`register_provider` leaked a global.** `node_exists` was assigned bare inside the dry-run
  branch. Declared in the function's `local` block with the rest of its state.
- **The omniroute client key was in argv.** The catalog read was
  `curl -sf -H "Authorization: Bearer <client key>" $GATEWAY/v1/models` — readable by every user
  on the machine out of `ps` for the lifetime of the call, the exact thing `omni_rest` exists to
  avoid. The read now goes through `omni_rest`, which gained a bearer argument (the manage key
  stays the default) and writes the token into its 0600 `curl --config` file that the interrupt
  trap removes on every path. No second copy of that code.

`omni_rest`'s doc comment records the bash trap the fallback line had to route around: an
apostrophe inside a `${VAR:-default}` expansion *within a double-quoted string* makes bash fail
to parse the whole script (`unexpected EOF while looking for matching '`), so the default is
assigned in a variable first.

Tests: eight new cases in `tests/linux/34-ai-services.sh`, each with one new switch on the
stand-in side. `$d/require_bearer` makes the fake curl answer `/v1/models` only from a bearer it
reads out of apply's own config file (recording what it found in `bearer.log`), so a key sent as
`-H` argv fails the case twice over; `$d/partial_total` reports a `total` beyond the rows served;
`isActive: false` rows and a connection bound to a second node need no switch beyond
`connections.json`/`nodes.json`. The two restore cases drive `$d/live.fail` (store unreadable,
combo present in `live.json`) and `_drift_json … broken` (a `{"kind":"combo"}` step in the same
tier `swap` reverses) — the broken-leg case is the one that matters: without the guard the third
create *succeeds* against the stand-in, because the legs a partial restore drops are not the ones
listed in `fail_create`. Red before: all eight. Green after: `--filter apply` 70 / 0,
`--filter 'node,prune,drift,combo'` 48 / 0, shellcheck clean on `apply.sh`, pytest 176 passed.
No run touched a live gateway.

**Out of scope:** `configuration/omniroute/apply.ps1:418` still prints the "untouched" line and
still decides from the local CLI list (recorded under APPLYADOPT above); the Windows twin takes
these eight in its own lane.

### Fixed — apply adopts the live gateway's connections, and a failed combo re-create restores (APPLYADOPT, 2026-09-29)

Two false statements in `configuration/omniroute/apply.sh`, both measured on the dockerised
gateway on 2026-09-29:

- **It lied about what was registered.** The Providers step decided "already registered" from
  `omniroute providers list`, which reads the CLI's own `~/.omniroute` store — on the container
  host that store is *not* the gateway: the CLI listed 14 connections while
  `GET /api/providers?limit=5000` answered 32 (most rows named `main`). A real run therefore
  re-added scaleway, nebius, free-ai, bazaarlink, navy, arcee-ai, bluesminds, agentrouter,
  novita and together — a duplicate connection each, on someone's real machine (AGENTS.md hard
  rule 3) — while `--dry-run` printed "would create its provider node" for meta-api even though
  node `meta-api` and its connection (whose `provider` is the node's `openai-compatible-chat-<uuid>`
  id, not the registry id) already existed. New `rest_provider_ids` reads the gateway's
  connection list once per run (only the `provider` field; the body carries every stored
  `apiKey` and is never printed), and `provider_already_registered` answers from it whenever a
  manage key made the read succeed — a node provider by its node *plus* the connection bound to
  it, through the existing `provider_node_id` / `provider_connection_exists`, so there is one
  rule and not two. `EXISTING_FROM_REST` is a read-succeeded flag, not "the list was non-empty":
  a gateway with no connections is an answer. The CLI list stays the fallback for a run that
  cannot ask (no manage key — the local gateway, where the CLI store *is* the gateway's — or a
  refused read), and a run that had a key and was refused says which source decided, without
  echoing the gateway's body. `--dry-run` now prints the idempotence line it owed the operator:
  `= <id> already registered`, and for a node that exists but holds no key, "would add the key
  to its existing provider node" instead of promising to create it.
- **It claimed a deleted combo was untouched.** The combo retry path (`create` → on failure
  `delete` → `create`) printed "previous version, if any, is untouched" after it had already
  deleted. `LIVE_COMBO_REFS` / `LIVE_COMBO_STRATEGY` are read for exactly this reason and are
  now remembered *before* the delete: if the re-create fails too, the tier is re-created from
  them and the run says `! <name> creation failed - previous version restored`; if even that
  fails it names the command the operator has to run (`... previous version LOST, restore
  failed: omniroute combo create <name> --strategy <s> --models <refs>`), and when the store
  never held the name it says `(no previous version)`. Counted as failed either way — the tally
  no longer hides a hole in a live tier.
- **`tests/linux/34-ai-services.sh`**: seven new cases on the existing stand-ins, each with one
  new switch on the fake side. `$d/local_stale` in `_node_sandbox` reproduces the live
  divergence (the fake CLI's `providers list` answers nothing while the fake REST surface still
  serves `connections.json`): a gateway-held connection reports `already registered` with no
  `providers add` in `calls.log` and no `POST /api/provider-nodes`, a node provider's existing
  node + connection the same, `--dry-run` included, and the REST branch is proven by the `GET`
  in `curl.log` rather than inferred. `$d/no_rest` refuses the connection read (503 on a health
  that still answers) and asserts the CLI fallback, and a keyless run makes no management call
  at all. `$d/fail_create` in `_prune_sandbox` fails `combo create` for the legs listed in it,
  which is the only way to reach the retry path's *second* failure: restore-then-`restored`
  line, restore-then-`LOST` line with the store empty and three create calls, and
  `(no previous version)` for a name the store never held, with no invented restore. The
  `untouched` wording is asserted absent in all three. Red before: `--filter adopt` 1 / 3
  failed, `--filter 'apply combos: a create'` 0 / 2 failed. Green after: `--filter apply` 62 /
  0, `--filter 'node,prune,drift,combo'` 44 / 0, shellcheck clean on `apply.sh`, pytest 176
  passed (registry render + sync-router-tiers).
- **Out of scope:** `configuration/omniroute/apply.ps1:418` still prints the `untouched` line
  and still decides from the local CLI list. The Windows entry point has no container-host
  divergence to adopt, but the restore wording is the same defect and its pwsh twin should
  follow in its own lane.

### Fixed — the gateway namespace leaked into the LiteLLM mirror (FREEKEYS-2e, 2026-09-29)

CI 36506339556's bash suite went red on three cases the lane's filtered pytest runs never
touched. One root shape behind all of them: FREEKEYS-1 registered scaleway with
`model_prefix: scw`, so `combos.json` — rendered through `gateway_ref()` — spells those legs
`scw/*` while `routes.<id>.legs` and `config.yaml` spell them `scaleway/*`.

- **Code** (`tools/registry.py`, `tools/sync-router-tiers.py`): `sync-router-tiers.py --combos`
  is the documented escape hatch onto the rendered file's own leg order, and it read those
  `scw/*` refs as if they were provider ids — resolving no transport and no env key, so the
  mirror came out `model: scw/... , api_key: os.environ/SCW_API_KEY`: a LiteLLM provider that
  does not exist and an env var nothing sets. New `registry_ref()`, the inverse of
  `gateway_ref()`, rewrites a declared `model_prefix` back onto its provider id (only when
  exactly one provider declares it and it is not itself a provider id; an unknown or ambiguous
  namespace is returned unchanged rather than invented). `combos_refs()` runs every ref
  through it. `test_explicit_combos_override_still_works` was the failing test; a focused
  `RegistryRefTests` and a `combos_refs` case pin the rewrite.
- **Test pinned the old spelling** (`tests/linux/17-ai-routing.sh`, "free-only litellm groups
  mirror combos minus gateway-only legs"): its hardcoded LiteLLM string builder knew no
  gateway namespace, so every `scw/*` leg read as drift against the correct `scaleway/*`
  mirror. The mapping is pinned in the test as an independent second opinion — deliberately
  not read from the registry, same rule as `known_drops`.
- **Test pinned the old legs** (`tests/linux/34-ai-services.sh` + its `run-tests.ps1` twin,
  "apply: one run registers a provider…"): it asserted the first run writes
  `--models free-ai/qwen7b` — t3-driver-free-only's single leg before FREEKEYS-2 put the
  scaleway and nebius free grants ahead of the stopgap. The apply order itself (register →
  refresh catalog → write combos) was and is correct in one pass. The check is now the whole
  ordered model list read from `combos.json`, so it fails if ANY leg goes missing, and keeps a
  separate assertion that the freshly registered provider's leg is in the first write.

No check was weakened: the mirror test gained its namespace translation, and the apply test
went from one hardcoded ref to the file's full ordered list plus the new-leg assertion.

### Added — combos ordered free → credit → paid, spend guard on every plan (FREEKEYS-2 / 2b, 2026-09-28)

- **Band order** (`catalog/ai-registry.json`): every agentic tier route now lists
  its legs by what they cost — the probe-passed free grants first, the `credit`
  grants last and gated, the paid legs between the free band and the credit tail.
  A combo that leads with a paid leg spends operator money before it spends a
  grant, and an OmniRoute `priority` fall-through on a 429 lands on the *next
  provider*, so the leg order is the redundancy. The reset-aware cooldown handling
  the route `$comment`s cite (§18) is untouched.
- **DeepSeek stays V4.1 only** (the L1-routing DECISION on FREEKEYS-2's open
  question): the free bazaarlink `deepseek-v4-flash-0731` grant is V4 weights, so
  it enters no combo and the per-leg allow drafted for it is withdrawn — the
  blanket `deny-deepseek` governs. Its measured model/provider rows stay, and the
  rows' `$comment` says they route nowhere until the operator lifts the rule.
  `DeepseekV41OnlyDecisionTests` pins all of that.
- **Unpriced credit legs are documented fallbacks only**: `morph` ($10) and
  `deepinfra` ($5) grants carry no per-token price, so the resolver refuses them
  fail-closed and each combo marks them `available: false` with the gate that
  lifts it (a real price on the model row). An unpriced grant renders as a free
  leg and would drain with nothing in the ledger to show it.
- **Spend guard wired in** (`tools/autoos-agent.py`, `tools/autoos_agent_mcp.py`):
  `plan_credit_guards()` reads this month's spend per `credit` provider through
  `autoos_usage.credit_guards` — the same reader the `usage` report prints — and
  passes it to `autoos_resolver.plan` from `run`, `route` and the MCP `route`
  tool (MCP `spawn` inherits it: it builds a `run` argv and preflights it). One
  gateway read per process; a read that fails refuses every credit leg rather
  than assuming $0 spent, and the note carries only the exception *type*, never
  its message (no host or path can reach the plan text).
- **New tests**: `ComboCrossProviderTests` (≥2 distinct usable providers and ≥3
  usable legs per agentic route; credit legs last and gated; no new free leg in a
  `-clean` route) and `ComboFallthroughTests` (a fake gateway `priority` walk: a
  429 on the head lands on another provider, two provider failures still leave a
  third, and the fall-through lands on a leg an agentic card can use).
- **Red → green**: seven tests the reorder broke were resolved by decision, not
  by deletion — the T2FREE stopgap is now asserted over the *live* legs (a gated
  leg renders nothing), MUSEAPI's "meta heads t1-orchestrator" became "the paid
  leg follows the free band" with the two paid routes still pinned to it, the two
  reviewer grants moved inside the free band so `policy.reviewers` keeps Haiku
  last, and the Gemini-cooldown tests now cool the two new free providers too,
  because their premise is "every leg of the route is cooling".
### Added — `tools/claude-cli-lag.py`: the lag check that replaces the pin (CLIPIN / D-137, 2026-09-29)

The operator superseded the Claude Code version-pin idea: every host runs the
latest published release and the autoupdater stays on. What replaces a pin is
something that *tells you* a host is behind, without ever touching it.

- **`tools/claude-cli-lag.py`** (stdlib, Linux and Windows/WSL, read-only):
  prints, per host, `claude --version`, the newest published release from the
  npm registry (cached an hour in the git-ignored `logs/`; an unreachable
  registry is `unknown`, never an error), the lag verdict, and the
  autoupdater state from `DISABLE_AUTOUPDATER` in the environment or in the
  `env` block of `~/.claude/settings.json` (`%USERPROFILE%\.claude\` on
  Windows). Exit 0 up to date / ahead / unknown, exit 1 only on a confirmed lag;
  a lagging host is flagged `lags - restart picks it up`, because that is when
  the auto-update actually lands.
- **A recommendation line, not a gate**, when the installed version differs
  from the last one recorded: re-run the cheap spec behaviour checks
  (ORCH-A1 §3.3 deny-over-allow, the HOOKS guard contracts) — both read
  Claude Code's own permission precedence and hook payload shape, which a
  release can change underneath a lane. The first run has nothing to compare
  against, and an `unknown` version never overwrites the last known one.
- **`docs/api-keys.md`**: the policy paragraph next to the Claude Code
  gateway settings it reads. Nothing in the repo pins a Claude Code version and
  nothing sets `DISABLE_AUTOUPDATER` (verified across catalog, lib, templates
  and infra) — `claude-code` installs `@anthropic-ai/claude-code` unpinned.
- **`tests/test_claude_cli_lag.py`**: fake version output and fake registry
  JSON (lags / up-to-date / ahead / unknown), the cache TTL, offline fallbacks,
  a corrupt cache and settings file, autoupdater detection from env and from a
  generated `settings.json`, the Windows `%USERPROFILE%` path, the
  version-change recommendation, the exit codes, and the state file's location
  under the git-ignored `logs/`. Wired into `tests/linux/33-documentation.sh`
  and `tests/run-tests.ps1`.

### Fixed — `cancel` takes its kill target from the run id, not from the worker (SB-A3, 2026-09-28)

SB-A2 took the group out of `pgrp.json` and wrote it into `job.json` — but
`job.json` lives in the run dir, which `run_job` exports to the worker as
`AUTOOS_TASK_DIR`, and the worker is the same uid as the file. `cancel` then read
`job["scope"]` and `job["group"]` verbatim, so the process being cancelled could
still aim the killer at any `--user` unit, at another run's scope, or at any
process group.

- **`tools/autoos-agent.py`**: `worker_scope_unit(run_id)` derives the unit name
  `autoos-worker-<run id>.scope` and raises for anything that is not a run id
  (`is_killable_run_id` / `KILL_RUN_ID_RE`, 64-char cap); `stop_scope()`
  re-validates the name it is handed (`scope_unit_is_worker_scope`: prefix,
  `.scope` suffix, unit-safe charset, length) and refuses — with no `systemctl`
  call — anything else, so the guard lives in the killer, not only at the call
  site.
- **`tools/autoos_agent_mcp.py`**: `cancel` never reads a scope from a file. It
  derives it from the run id it was given and refuses a `job.json` that claims a
  different run. The fallback group record moved out of the task dir into a
  runner-private store, `$(state dir)/kill/<run id>.json` (dir 0700, file 0600,
  atomic write, `kill_store_dir`/`write_kill_record`); `spawn` and `run_job`
  write it, `cancel` reads only it, and `job.json` carries no `group` any more —
  its `scope` field stays as information only.
- **Residual, stated**: a same-uid worker that goes looking can still find the
  kill store through `AUTOOS_STATE_DIR`; a file cannot be hidden from the uid that
  owns it. The scope path — derived, no file involved — is the primary and is what
  a host with a user manager uses.
- **Tests** (`tests/test_autoos_spawner.py`, `CancelDerivesKillTargetTests`): a
  rewritten `job.json` scope (`foo.service`, another run's scope) does not change
  what `cancel` stops; `stop_scope` refuses every non-worker-scope name; a
  malformed run id is refused at both levels; the group kill reads only the
  private record (an edited `job.json` group kills nothing); the store is not
  under `AUTOOS_TASK_DIR`, is not named in the worker env, and is 0700/0600.
### Fixed — Claude-family spelling escape, wrapper-only peer origin, orchestrator-only shared checkout, deepseek-flash priced (SB-C2, 2026-09-28)

Four items from the SB-C2 brief, all red-then-green against HEAD:

- **deny-claude-paid-api spelling escape (HIGH)** (`catalog/ai-registry.json`):
  the rule matched only `*/claude-*`, so a third-party leg named by its family
  word alone — `openrouter/anthropic/opus-5`, `bluesminds/sonnet-5`,
  `free-ai/haiku-4-5`, `zen/fable-5` — escaped it and was usable the moment the
  budget gate said yes. Added `deny-claude-family-{opus,sonnet,haiku,fable}`
  (`*/*<word>*`, the CLAUDE_MODEL_MARKERS the gate itself reads) and
  `deny-anthropic-paid` (`anthropic/*`; `anthropic` is not one of the two seat
  rows, `cc/*` and `antigravity/*`, whose allows precede every family deny).
  Placed after the seats, before every provider wildcard allow, so first-match-
  wins still allows a seat leg and a family deny can never be outranked. A
  registry sweep confirmed no *non-Claude* model/leg name carries opus/sonnet/
  haiku/fable, so no carve-out was needed (pinned by
  `test_no_registered_non_claude_name_carries_a_family_word`).
- **classify_origin wrapper-only `from=` (MED)** (`tools/skill-rules.py`): R-worker-11
  read `from=` anywhere in the text, so a compaction request could be dressed as
  a peer by quoting the marker in its own body. Only a `from=` attribute on a
  *leading* `<cross-session-message …>` wrapper tag counts now; quoted text, code
  spans and body `from=` are ignored. Documented residual (stated in the rule
  docstring): a real peer message that arrives without the wrapper reads as
  harness-compaction — the safe miss, since complying is the correct action for a
  self-managed context anyway. The fixture carries all three SB-C2 cases.
- **SPAWNISO override is orchestrator-only (MED)** (`tools/autoos_agent_mcp.py`):
  `allow_shared_checkout` was SB-C's writer escape hatch, but a leaf cannot spawn
  at all (KEYDENY3), so it made the shared-worktree hole explicit. The MCP spawn
  now refuses `allow_shared_checkout=True` unless the card's role is `orchestrate`,
  and records an accepted override as `shared_checkout_override` on the run's route.
- **deepseek-flash priced and resolvable (REGISTRY GAP, L1-main)**: the D-102 gate
  refused `deepseek-flash` by bare name as unpriceable, though it is the allowed
  paid bulk leg under DSGUARD's $25 cap. Added `price_source` to
  `models.deepseek-flash` citing the numbers it already carries — `price_in 3e-07 /
  price_out 1.2e-06 / price_cache_read 6e-09`, reused from `models.deepseek-v4-flash`
  (the retired llm-models.json sibling) and the exact prices `autoos_usage`/
  `deepseek_call.check_cap` bill — no invented number. The gate's unknown-combo
  branch now reads a priced registry model row (bare name and the `deepseek-v4-flash`
  alias, with `omniroute/…#effort` normalised) instead of refusing; an anthropic-
  family row still reads as Claude, and a string with no row at all still fails
  closed. `tools/registry.py` exempted `price_source` from rule 5 so its dated
  attribution passes the check.

### Fixed — spawn isolation, compaction rule, and the zen Claude leg hole (SB-C, 2026-09-28)

Three items from the SB-C brief:

- **SPAWNISO** (`tools/autoos_agent_mcp.py`): KEYDENY3 forced tiers 2-3 and every
  leaf role into an isolated clone, but a tier-1 *write* role still ran in the
  caller's checkout — and `role=implement, complexity=hard` routes UP to tier 1
  (`routing.select_combo`'s public-strong bucket). A write-role card now defaults
  to `isolate`, and one that explicitly asks for `isolate=False` while `cwd` is the
  caller's own worktree is refused unless the call names `allow_shared_checkout=True`.
  The MCP tool's `isolate` default became `None` (absent = the default applies) so a
  caller that never mentions it is not read as asking to run in place. Read-only
  roles (review, orchestrate) still run in place.
- **COMPACTRULE** (D-146): `.agents/skills/unattended-orchestration/SKILL.md` gains
  `R-worker-11` — a "summarise, no tools" ask carrying no cross-session `from=` is
  the agent's own harness compacting it, so it should comply or hand off before the
  cap; only a `from=` line can be a peer. `classify_origin()` in
  `tools/skill-rules.py` decides it, pinned by `tests/fixtures/skill-rules-compaction.jsonl`
  (both verdicts must appear in the fixture).
- **ZENCLAUDE** (`catalog/ai-registry.json`, found by FREEKEYS-1): `leg_rules`
  matched `allow-opencode-zen-client-bound` (`opencode-zen/*`) before
  `deny-claude-paid-api` (`*/claude-*`), so a Claude leg spelled under zen was never
  denied by the leg rules. Measured live, not latent: with `AUTOOS_CLAUDE_FINAL`
  declared (the gate the leg rules are supposed to sit under), the resolver returned
  `opencode-zen/claude-sonnet-5` as a usable leg; without the declaration the Claude
  budget held it, so only that first half was open. `deny-claude-paid-api` now
  precedes every provider wildcard allow, with the two free Claude seats
  (`cc/*`, `antigravity/*`) moved up with it so they keep matching first. Sweeping
  every leg the registry names: no committed verdict changed
  (`tests/test_autoos_resolver.py ZenClaudeLegRulesTests`).

### Changed — `agent-skills` is a tombstone on Windows too; its installer no longer clones (SPEC-OMNI A7, D-066, 2026-09-28)

A7 retired `agent-skills` on Linux and macOS and left Windows live — its entry
cloned a second repository and its installer did the MCP wiring. This lane
finishes the retirement on Windows. The native Windows twins
(`agent-skill-links`, `omnigraph-client`) are deliberately *not* added (D-066:
the Windows path is frozen to fixes), so the four `mcp-*` components are the
only successors the catalog names there. What `Install-AutoOSAgentSkills`
additionally did on Windows (the project-scope `omnigraph`/`autoos-agent` pins,
the Antigravity MCP merge, the user-scope skill links) now has no catalog
caller: it is reachable only from tests, and its Windows successor is the parked
w1 lane. Known, accepted under D-066. Leftovers deliberately kept (a frozen
path gets fixes only): the unreachable `agent-skills` probe in
`AutoOS.Detect.psm1` and the function itself as a test fixture.

- **`catalog/windows.json`**: `agent-skills` is `"tombstone": true` with the
  note *its work moved to the mcp-\* components* and `replaced_by` naming the
  four ids that took it (`mcp-graphify`, `mcp-serena`, `mcp-playwright`,
  `mcp-context7` — the only successors this platform ships; naming
  `agent-skill-links`/`omnigraph-client` would promise work Windows does not
  have). `postInstall`, `prompt`, `requires` and `profiles` go with the live
  row.
- **`lib/windows/AutoOS.Install.psm1`**: `Install-AutoOSAgentSkills` no longer
  clones `Documents\Code\agent-skills` (nor pulls it): the servers it wires are
  declared by this checkout's own `.mcp.json`, so the project-scope pins now
  read `$script:RepoRoot`. `Write-AutoOSOmnigraphReadiness`'s parameter is
  renamed `-AgentSkillsDir` → `-RepoRoot` to match. The MCP wiring, the
  `omnigraph`/`autoos-agent` project pins, the Antigravity config merge and the
  repo/user skill links are unchanged, as is the retargeting of links into the
  retired clone (a machine that ran the old installer keeps its checkout — this
  never deletes it). The function is now reachable only from tests: no catalog
  entry carries it as `postInstall`.
- **Tests**: `tests/linux/18-mcp-wiring.sh`'s tombstone case becomes
  per-platform (Windows expects the four-successor set) and validates
  `catalog/windows.json`; a new case proves the Windows installer names no clone
  URL, runs no `git clone`/`pull` and writes nothing into the retired path.
  `tests/run-tests.ps1` gains the matching pwsh cases (`agent-skills is never
  cloned`, asserted on the installer body and the whole module; and the Windows
  catalog entry carries the flag, the note and exactly the four successors with
  no installer hooks).
- **`docs/catalog.md`**: the `replaced_by` example now shows both shapes and says
  why Windows names four.
- **Open, recorded rather than hidden**: with the row retired, the
  `omnigraph_url` prompt in `catalog/windows.json` is owned by no component, so
  a Windows run never asks it and `Set-AutoOSAntigravityMcp` falls back to
  `localhost:8080`. Fixing that belongs to the parked w1 lane (which adds the
  Windows `omnigraph-client` twin), not to this frozen-path lane.

Verified: `bash tests/run-tests.sh --filter catalog` 69 passed / 0 failed;
`AUTOOS_TEST_PARTS=18 bash tests/run-tests.sh --filter agent-skills` 21 passed /
0 failed, and the schema case validates `catalog/windows.json`. pwsh 7.6 on this
Linux host ran too: `-Filter agent-skills` 23 passed / 2 failed and
`-Filter catalog,tombstone` 319 passed / 2 failed — all four failures reproduce
at the branch point, so none is new (two backup-record cases, the Linux-only
`powershell`-on-PATH `--CheckCatalog` case, and an unrelated usb case). The real
gate is still one `powershell -File tests\run-tests.ps1` on a Windows
workstation, where the Windows PowerShell 5.1 code paths these cases cover
actually run.

### Fixed — every spawned tier is isolated, in the CLI and through MCP (KEYDENY3g, 2026-09-28)

Policy decision (L1-routing): a worker spawned at tier 2 or 3 runs in an isolated
clone; only tier 1 (`role=orchestrate`, the operator's own session) may run in
place. KEYDENY3b refused tier 3 and deliberately left tier 2 in the caller's
checkout, recording the hole it left — a t2 running in place has the same
pattern-fence gap for itself, and the native `t3-reviewer` it launches inherits
that cwd and carries none of the fences. The directory, not the pattern, is what
closes it.

- **`tools/autoos-agent.py`**: `LEAF_TIERS = (3,)` is now `ISOLATE_TIERS = (2, 3)`,
  and `leaf_isolation_refusal(tier, isolate, client, leaf=)` keys on the role's
  `leaf` flag **or** the isolated tier, never on the tier number alone — a
  `role=review` card is a leaf at any tier. The flag is read from
  `catalog/agent-harness.json` (`harness_role_is_leaf` / `role_for_run`), so there
  is one home for it. An in-place spawned tier exits 2 with the reason and the fix
  named; a `--dry-run` only announces it. The qoder-writes force (the plan sets
  `--isolate` itself) is what keeps that run legal, and the verdict is computed
  after `build_plan` for exactly that reason.
- **`tools/autoos_agent_mcp.py`**: `build_argv` calls the same shared helper — no
  second rule table to drift — and **forces** `isolate` for a spawned tier rather
  than refusing, because its caller is a headless agent that cannot retype a flag
  and the alternative is a job that reports itself started and then exits 2. The
  force is reported as `route.forced_isolate` in the spawn answer.
- **`tools/autoos-agent.py` (item 7)**: an `--isolate` clone was forked from
  `ROOT` — the checkout the *script* lives in — so an MCP isolated spawn cloned
  the MCP server's own repo at its HEAD and every sandbox started on the wrong
  branch while the worker ran elsewhere. New `isolate_source(cwd)` forks from the
  caller's `git rev-parse --show-toplevel` (falling back to `ROOT` when the cwd is
  not a repository), the containment prompt names that source, and the `--dry-run`
  clone line prints it.
- **`tools/autoos_clients.py`**: no client may leave a leaf able to spawn
  silently. `LEAF_SPAWN_DENY` renders each CLI's own deny for a leaf run —
  `claude`/`qoder` `--disallowed-tools`, `qwen` `--exclude-tools` (flag names
  verified against each `--help` on this host; the values are tool-name patterns,
  so every known spelling goes in and an unknown one is inert rather than an
  error). `gemini`, `codex` and `agy` carry no gate and their rows already say
  `subagents: False`; opencode's gate stays the config overlay. `opencode.jsonc`'s
  agent blocks are unchanged.
- **Tests**: `LeafIsolationMandatoryTests` re-pinned for tiers 2 and 3 plus the
  leaf-flag keying; `McpIsolateForceTests`, `IsolateSourceTests` (a two-head repo:
  a worktree on branch X yields a sandbox whose HEAD is X's, never the server's)
  and `ClientSpawnGateTests` are new; `FixtureSpawnGateObjectTests` pins the
  `{task, subagent, bash}` permission object against the checked-in fixture, and
  `IgnoredKeyFilesTests` proves with git's own answers that
  `configuration/api-keys.yml` and `.env*` are ignored and none of them tracked.
  Tests of unrelated exit codes call `allow_in_place`, which neutralises only the
  isolation gate; the two agy signin probes moved to `--tier 1` for the same
  reason.
- **Skill/docs**: `.agents/skills/unattended-orchestration` now shows `--isolate`
  on every spawned-tier example and states the rule; so does `autoos-agent.py`'s
  own usage block; `lib/agent_harness.py`'s fence comment records the hole as
  closed.
- **Open, recorded rather than hidden**: opencode 2.0.16's canonical action names
  are `shell` / `subagent` / `patch` (its rename map is
  `{bash: "shell", task: "subagent", apply_patch: "patch"}`) and `Config.Info`
  declares `permissions` (ordered rules), not the `permission` map the harness
  writes — the map is the legacy shape the normaliser still accepts, and the
  spawner's overlay carries the ordered-rule form. A tier-1 run in place still has
  the pattern hole for itself; that is the operator's own session, in a lane the
  operator is watching.

### Fixed — the stop class is PAUSE plus the imperative STOP/HALT/ABORT; HOLD and FREEZE stay capacity notes (RESTART R2a10, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (this repo's own R2a9 open #1, S1, safety): R2a9 widened
  the pause class to stop *vocabulary* with PAUSE's wide rules, and the live corpus paid at
  once — `from L1-main: MEM HOLD LIFTED (MemAvailable 7.0G) … max 4 local units/workers`, a
  released memory-capacity note, read as a hard stop: heartbeat exited 3 and `run`/`spawn`
  refused on three live inboxes. A false stop that halts the fleet is not acceptable, so the
  class is now `PAUSE_ORDER_WORDS` = PAUSE, STOP, HALT, ABORT (HOLD and FREEZE out) and
  `_gives_stop` splits it by **shape**, symmetric with the strict release: PAUSE keeps the
  R2a4–R2a9 wide read unchanged, the other three stop only when the bare uppercase word is
  the first word of an unmarked payload — `→ done: STOP all lanes obeyed`,
  `fleet note: runs were stopped at 14:00`, `no STOP needed` mid-note and
  ``operator: `STOP` `` are mentions. The record-wide negation veto and the bare-leading
  `RESUME` release are untouched. Re-scanned over the ten real inboxes: **0** records are a
  bare imperative stop, and `pause_state` is back to the R2a8 baseline exactly — 3 active
  (`L1-backlog.md`, `L1-main.md`, `L1-routing.md`) with the same winning records, the
  `MEM HOLD LIFTED` note inactive everywhere.

### Fixed — the veto is the record, the release is a bare leading RESUME, and STOP/HOLD/HALT/ABORT stop the run (RESTART R2a9, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (this repo's own R2a8, S1, safety, FIX-FIRST): R2a8
  replaced one heuristic with another. It split the record into sentences and let both the
  negation veto and the release ride that split — and a splitter reads an abbreviation
  (`e.g.`) and a decimal (`3.5`) as a sentence break, which is a way for a negation to
  escape the veto, and for a *mention* to earn a head. The decision is to stop
  sentence-splitting and make both sides strict by construction. **(1) The veto is the
  record.** `_sentence_span` is gone; `_record_is_negated` reads the whole **payload**
  (everything after the speaker prefix and, in an acknowledgement, after the marker —
  `_ack_head`/`_payload_start` stay the one home of that split), so a `NEGATION_WORDS` word
  anywhere in it closes nothing: `→ done: PAUSE lifted e.g. not confirmed by ops`,
  `→ done: no merges today. PAUSE lifted` and
  `→ main: no agreement was reached; PAUSE acknowledged` all report a stop still in force.
  No word joined `NEGATION_WORDS`; the round's point is the structure again.
  **(2) The release is a bare leading RESUME.** `_resumes`' shape (a) no longer accepts the
  first word of *any* sentence — the payload must open **directly** with the uppercase word,
  with no quote, backtick or parenthesis in front of it (`operator: "RESUME all lanes"`,
  ``operator: `RESUME` ``), no `?` anywhere in the payload (`operator: RESUME?`,
  `RESUME tomorrow?`), no negation, and no *undoing* close within the window; shape (b)
  stays the acknowledgement that says nothing but `RESUME acknowledged`/`acked` plus
  punctuation or a time. So `note the fix landed. RESUME every lane`,
  `operator: work done. RESUME all lanes` and `noting e.g. RESUME is due` order nothing,
  while `operator: RESUME all lanes` and `→ done: RESUME acknowledged` still lift.
  **(3) STOP, HOLD, HALT and ABORT are PAUSE-class.** `PAUSE_ORDER_WORDS` is the filter
  `pause_state` reads (through `_gives_order(text, _PAUSE_ORDER_RE)` — the same exemption,
  one narrower list), closing R2a8's recorded probe: `operator: STOP all lanes` reported
  `active: False` while `_gives_order` called it an order. `FREEZE` stays out of the class
  and `RESUME` never joins it. Both sides read `_ack_head`, `_record_is_negated` and
  `_order_word_is_closed`; no second copy of the rule.
- **Measured over the real corpus** (`logs/handoff-sessions/{20260924,20260925}/inbox`,
  read-only, HEAD's classifier and the working copy in one pass over 10 files / **2238
  records** — the corpus is live and grew during the run — 927 acknowledgements, 187
  quoting an order word, 8 quoting `PAUSE`, 0 quoting
  an uppercase `RESUME`): **0** records change `_resumes` (the release half moves nobody —
  the corpus has never written an imperative `RESUME`), **1** changes `_gives_order`
  (`→ done: combined FLEETSPEC review came back NOT READY …`, whose close R2a8's sentence
  cut had exempted), and **30** change class as *stop* records under (3) — 25 unmarked
  fleet notes and 5 acknowledgements. `pause_state` goes from **3 active to 4**:
  `L2-general.md` flips inactive → active and `L1-backlog.md`/`L1-routing.md` change their
  winning record, in all three cases to the same line —
  `from L1-main: MEM HOLD LIFTED (MemAvailable 7.0G). The normal freeze rule applies
  again: max 4 local units/workers fleet-wide …`. It wins because an **unmarked** record
  never gets its closing word read at all (R2a4's wide reading, unchanged here), so a
  `MEM HOLD`/`CHEAP-WORKER HOLD`/`ON HOLD` capacity note now reads as a hard stop that its
  own `LIFTED` cannot end. That is the spurious direction the asymmetry allows — one wasted
  heartbeat and a `RESUME`, never a lost order — but it is a cost measured on live inboxes,
  so it is recorded as an open item rather than glossed.
- **Docs:** RESTART spec §0 holds the rule (the payload-wide veto, the bare-head release,
  `PAUSE_ORDER_WORDS`, the measured cost); `docs/routing.md` cites §0 and
  `_gives_order`/`_resumes`/`_ack_head`/`_payload_start`/`_record_is_negated` instead of
  restating it.

### Fixed — the release is strict by construction and the negation veto spans the sentence (RESTART R2a8, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Muse review of R2a7, S1, safety, FIX-FIRST): R2a7
  widened the negation veto to the whole *window* and gated the RESUME half like a PAUSE —
  both were still wide enough to lose a stop. **(1) The veto outran its window.**
  `→ done: PAUSE lifted but it was never really confirmed by ops` closed the stop because
  the negation sat five words out, past `_CLOSING_WINDOW`. The unit is now the **sentence**:
  `_sentence_span` splits on `.` `;` `!` `?` and newline, and `_order_word_is_negated` reads
  it whole, so a sentence that keeps talking after its closing word cannot close the order
  (`PAUSE lifted and the record is unconfirmed by ops`,
  `PAUSE released, and ops never signed that off`, `PAUSE ended but that was not the
  operator's call` all stay in force). The same rule is what makes the veto *narrower* where
  a negation belongs to a different claim: `→ done: no merges today. PAUSE lifted` and
  `→ main: nothing was agreed; PAUSE acknowledged` close — the negation never reaches in
  across a sentence boundary. No word joined `NEGATION_WORDS`; the round's point is that the
  structure, not the vocabulary, carries this.
  **(2) A mention of RESUME lifted the stop.** R2a7's gate vetoed a *negated* and an
  *undone* RESUME, so `→ done: we should RESUME tomorrow`, `→ done: considering RESUME
  options`, `→ done: RESUME pending` and `→ done: discussed RESUME` each un-stopped a run
  nobody released. `_resumes` is now strict by construction and counts exactly two shapes:
  **(a)** a record with **no** acknowledgement marker, where `RESUME` is the first word of
  its payload or of its sentence — the imperative the operator writes (`operator: RESUME all
  lanes`, `from L0 (operator) RESUME now`, `work done. RESUME every lane`) — unnegated in
  that sentence and un-undone within the window; **(b)** an acknowledgement whose sentence
  says nothing but the landing — `RESUME` plus a `RELEASE_ACK_WORDS` word (`acknowledged`,
  `acked`, the release half of `REPORTING_CLOSING_WORDS`, named once and asserted as a
  subset), optionally followed by punctuation or a time (`→ done: RESUME acknowledged at
  12:00`), and by no prose (`→ done: RESUME acknowledged but ops still holding` lifts
  nothing). `lesson:` lifts nothing, as before. Both shapes read the shared `_ack_head`
  (the marker *and* where the payload starts), `_sentence_span`, `_order_word_is_negated`
  and `_order_word_is_closed` — the rule has one home, no second copy.
- **Recorded, not redesigned (the brief's follow-up probe):** `→ main: PAUSE lifted. STOP
  all lanes` — `_gives_order` reports the record as an active order (the closed PAUSE half
  does not exempt the open STOP half), and `pause_state` therefore calls the inbox active
  *through* the PAUSE word in it. `pause_state`'s filter is `_PAUSE_RE` alone, so a bare
  `operator: STOP all lanes` — or `HOLD every merge` — reports `active: False` while
  `_gives_order` says it is an order. Both are pinned by tests as the exact behaviour.
- **Measured over the real corpus** (`logs/handoff-sessions/{20260924,20260925}/inbox`,
  read-only, HEAD's classifier and the working copy in one pass over 10 files / **2220
  records**, 921 acknowledgements, 186 quoting an order word, 8 quoting `PAUSE`, **0 quoting
  `RESUME`**): **0** records change `_gives_order`, **0** change `_resumes`, and
  `pause_state` is identical on all 10 files (**3 active** both ways). The corpus is again
  no evidence for either fix — it has never written a RESUME line and its PAUSE lines are
  short, so the release half and the far-flung negation are covered only by the unit tests.
- **Docs:** RESTART spec §0 holds the rule (sentence-span veto, the two release shapes, the
  derived `RELEASE_ACK_WORDS` subset); `docs/routing.md` and the docstrings cite §0 and
  `_gives_order`/`_resumes`/`_ack_head`/`_sentence_span` instead of restating it.

### Fixed — a RESUME is gated the way a PAUSE is, and a negation anywhere in the closing window keeps the order open (RESTART R2a7, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Sonnet review of R2a6, S1, safety): two HIGHs,
  both in the direction that loses a stop. **(1) The ungated RESUME.** `pause_state`
  gated its PAUSE half on the R2a5/R2a6 ack rules but took its RESUME half on a bare
  `_RESUME_RE.search`, so
  `→ done: applied the fix already; RESUME was never issued, still holding` written after
  `→ done: PAUSE all lanes until further notice` reported `active: False` — a release
  nobody gave. A RESUME now counts only where it is itself an order, through the same
  `_ack_marker` / `_order_word_is_negated` / `_order_word_is_closed` helpers the PAUSE
  half uses (`_resumes`), never a second copy of the rule. Two readings of that window
  differ, each toward holding the stop: a negation vetoes a *release* in every record
  shape (`operator: no RESUME given yet` clears nothing), where for a stop word it vetoes
  only the close (`PAUSE NOW, no launches` stays a hard stop); and `CLOSING_WORDS` is
  partitioned, derived rather than hand-copied, into `REPORTING_CLOSING_WORDS`
  (`acknowledged`, `acked`, `cleared`, `resolved`), which report a release landing so
  `→ done: RESUME acknowledged` still clears, and `UNDOING_CLOSING_WORDS` (the rest),
  which undo one so `→ done: RESUME cancelled` holds. A `lesson:` record releases nothing.
  **(2) The close that outran its own negation.** `_order_word_is_closed` returned at the
  first closing word, so a negation standing *after* it closed the order anyway:
  `PAUSE lifted but not confirmed`, `PAUSE lifted, not really` and
  `PAUSE cleared, unconfirmed by ops` all reported a stop that ended. The veto now reads
  the whole `_CLOSING_WINDOW` on either side of the order word before any close is
  accepted, and `un-` is a prefix negation on any word of the window — R2a6's
  `_NEGATED_CLOSING_RE` (which only caught `un-` on a closing word itself) is now
  `_NEGATION_PREFIX_RE`. So `→ done: PAUSE lifted, not because the operator forgot`,
  which R2a6 documented as closed, is deliberately an order still in force: an `un-` or
  `no`-shaped word that is only vocabulary can veto a close and hold a lane one heartbeat
  longer, and a spurious order remains the accepted cost while a lost one is not.
- **Measured over the real corpus** (`logs/handoff-sessions/{20260924,20260925}/inbox`,
  read-only, HEAD's classifier and the working copy in one pass over 10 files / **2036
  records**, 864 of them acknowledgements, 161 quoting an order word, 7 carrying `PAUSE`,
  **0 carrying `RESUME`**): pause-order records **7 before and 7 after**, `pause_state`
  identical on all 10 files (**3 active** both ways), **0 records reclassified by the
  RESUME gate**. Exactly one record changes `_gives_order` verdict at all —
  `→ done: freeze cleared (2/4 units, 6.73GB)` — because `units` wears the `un-` prefix;
  it names no `PAUSE`, so no inbox flips. The corpus is again no evidence for either fix:
  it never writes a RESUME line, so the release half is covered only by the unit tests.
- **Docs:** RESTART spec §0 is the one home for the rule (window, veto, stop/release
  partition); `docs/routing.md` and the docstrings point at it and at
  `_gives_order`/`_resumes` instead of restating it, and §0's "whichever of the two comes
  first decides" sentence is replaced by the whole-window rule it describes.

### Fixed — no generic closing words and no negated close, so an acknowledgement stops swallowing live orders (RESTART R2a6, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Muse review of R2a5, S1, safety): R2a5's
  exemption was still wide enough to lose an order two ways. **(1) Generic words.**
  `over`, `done` and `noted` sat in `CLOSING_WORDS` and they are ordinary vocabulary
  *inside* an order sentence, so `→ done: noted. PAUSE over the weekend`,
  `→ done: PAUSE done by 18:00` and `→ main: PAUSE noted for all lanes` each read as
  the report of a stop that never ended. The list now holds only words that state one
  thing — that the order is over (`lifted`, `ended`, `cancelled`, `canceled`, `removed`,
  `released`, `acknowledged`, `acked`, `cleared`, `resolved`). **(2) Negation.** A
  closing word with a negation in front of it says the opposite:
  `→ done: PAUSE was not lifted`, `→ done: PAUSE isn't cleared` and
  `→ main: PAUSE never released` report a stop still holding, yet all three were
  exempted. The new §0 list `NEGATION_WORDS` (`not`, `cannot`, `n't`, `never`, `no`,
  `without`)
  vetoes a close when one of its words stands between the order word and the closing
  word, `n't` matching at the end of the word it hangs on (so `won't` is caught too),
  and `_NEGATED_CLOSING_RE` vetoes the prefix shape (`PAUSE unlifted`). `cannot` is on
  the list because the shape was found while spot-checking the veto —
  `→ done: PAUSE cannot be lifted` negates the close and spelled it as one word, so the
  five words the brief named would have left that order lost.
  `_order_word_is_closed` returns on the first word that decides either way, so a real
  close ahead of a later negation still closes (`PAUSE lifted, not because …`).
  Asymmetry unchanged (R2a4/R2a5): a veto past a genuine close costs a **spurious**
  order — one wasted heartbeat and a RESUME — a lost one lets workers run against a
  stop the operator gave.
- **Measured over the real corpus** (`logs/handoff-sessions/20260925/inbox`, read-only,
  both classifiers in memory — the HEAD copy and the working copy — in one pass over
  7 files / **1987 records**, 846 of them acknowledgements and 52 of those quoting an
  order word): pause-order records **7 before and 7 after**, **0 PAUSE-bearing records
  changed classification**, and `pause_state` identical on all 7 files (the three
  active files' winning texts exactly 80 chars, truncated as specified). The corpus is
  again silent on the defect — exactly one record flips verdict at all, a `→ done:` ack
  whose *HOLD* was closed by `noted` alone, and it is not a PAUSE line, so it moves no
  lane. The words the corpus really closes with are `lifted` (2), `cleared` (1) and
  `noted` (1); two acks carry a negation inside the 3-word window and both were already
  orders. Fixtures carry what the corpus has not (AGENTS.md §5).
- **Docs** (R-orch-11, wording moves with the rule): spec §0 states the narrowed
  `CLOSING_WORDS`, names `NEGATION_WORDS` as §0's fourth one-list rule and the veto in
  one sentence; `docs/routing.md` cites the same two lists; the `CLOSING_WORDS`,
  `_order_word_is_closed`, `_gives_order` and `pause_state` docstrings say what the
  filter now does.
- **Tests** (red before the code: 4 failed, 87 passed in `tests/test_autoos_heartbeat.py`
  against `git show HEAD:` of the module — the four generic-word and negation shapes,
  and the list-membership guard, each failing on the reproduced defect):
  `→ done: noted. PAUSE over the weekend` / `PAUSE done by 18:00` /
  `PAUSE noted for all lanes` / `→ ack: PAUSE over the weekend, → main held` → order;
  `PAUSE was not lifted` / `isn't cleared` / `never released` / `unlifted` /
  `won't be removed until I say so` / `cannot be lifted` → order; the three genuine
  closes the brief names (`PAUSE lifted`, `PAUSE acknowledged`, `STOP cancelled`) plus
  `PAUSE ended` → report;
  the list guards (no generic word in `CLOSING_WORDS`, `_NEGATION_RE` built from
  `NEGATION_WORDS`, `note`/`notebook`/`amount`/`nope`/`none`/`nevertheless` not read as
  negations). Two
  pre-existing fixtures that had relied on the struck words (`→ ack: PAUSE noted`,
  `→ ack: PAUSE over`) now write a kept closing word, so each still tests the shape it
  names. `tests/test_autoos_heartbeat.py` 86 → 91; the brief's subset
  (`card` + `inbox` + `heartbeat` + `suite_wiring`) is 215 passed.

### Fixed — an acknowledgement exempts only the order word it closes, so an order after an ack is still an order (RESTART R2a5, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Sonnet review of R2a4, HIGH, safety):
  `pause_state` gated the PAUSE scan on `not _acknowledgement(text)` for the **whole
  record**, so one acknowledgement at the head swallowed every order word that came
  after it in the same line —
  `2026-09-28T10:00:00Z → done: applied R2a4 fix. PAUSE all lanes until further notice`
  reported `active: False`, a hard stop that held nothing — and `parse_inbox_line`'s
  docstring claimed the reply was still scanned for PAUSE when it was not. An
  acknowledgement now absorbs an order word **only where a closing word follows it
  within 3 words**: the new `CLOSING_WORDS` list (`lifted`, `ended`, `over`,
  `cancelled`, `canceled`, `removed`, `released`, `acknowledged`, `acked`, `noted`,
  `done`, `cleared`, `resolved`) sits beside `ACK_MARKERS` and `ORDER_WORDS` as §0's
  third one-list rule, matched case-insensitively and as a whole word after trimming
  the punctuation a writer sticks beside it (`PAUSE, lifted`). So `→ done: PAUSE
  lifted` and `→ main: PAUSE acknowledged` stay reports, while `→ main: merged. STOP
  all lanes` and `→ done: 12:00 noted; PAUSE all merges now` are fresh orders; the
  gate is `_gives_order`, which keeps the R2a4 reading of an unmarked record (any
  `ORDER_WORDS` word is an order). `lesson:` is the one marker that exempts a whole
  record — a lesson reports on the code and never addresses the run, a rule that
  predates this lane and is now pinned by a test. Same asymmetry as R2a4: an order
  word whose closing word sits past the 3-word window is a **spurious** order (one
  wasted heartbeat, then a RESUME), never a lost one.
- **Measured over the real corpus** (`logs/handoff-sessions/20260925/inbox`, read-only,
  both classifiers in memory — the HEAD copy and the working copy — in one pass over
  7 files / **1954 records**, 832 of them acknowledgements): pause-order records
  **4 before and 4 after**, **0 records changed classification**, and `pause_state`
  identical on all 7 files — the three active files' winning texts exactly 80 chars,
  truncated as specified. The corpus had no ack record that both quoted a PAUSE and
  left an order word unclosed, which is why every earlier lane shipped green over it
  (AGENTS.md §5: the fixtures carry the case the corpus has not).
- **Docs** (R-orch-11, wording moves with the rule): spec §0 states the
  `CLOSING_WORDS` list and the 3-word window in one sentence; `docs/routing.md` cites
  the same window instead of the whole-record exemption; the `ACK_MARKERS`,
  `parse_inbox_line` and `pause_state` docstrings say what the filter now does.
- **Tests** (red before the code: 7 failed, 54 passed in `PauseStateTests` against
  `git show HEAD:` of the module — the reproduced defect failing on the assertion,
  `False is not true : {'active': False, …}`): the Sonnet line → order, the four
  closed shapes → report, `→ done: 12:00 noted; PAUSE all merges now` → order,
  `lesson: PAUSE handling was wrong` → report, `→ main: merged. STOP all lanes` →
  order, the window itself (`PAUSE was lifted by the operator` closed, `PAUSE is still
  holding every lane, and was not lifted` an order), and the §0 one-home guard that
  `_CLOSING_WORD_RE` is built from `CLOSING_WORDS` and that every
  `NEVER_ORDER_MARKERS` entry is a real marker. Six pre-existing marker fixtures that
  had relied on the whole-record exemption (`→ done: PAUSE handled`,
  `→ operator: PAUSE needs your call`, …) now write a closed order word, so each still
  tests the marker it names.
  `tests/test_autoos_heartbeat.py` 79 → 86; the brief's subset
  (`card` + `inbox` + `heartbeat` + `suite_wiring`) is 210 passed.

### Fixed — a speaker prefix never names an order word, so a PAUSE clause is never the speaker (RESTART R2a4, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Muse review of R2a3, HIGH, safety): the
  speaker shape `(?:WORD\s+){0,2}WORD(\(<note\))?\s*:` absorbs *any* short clause that
  ends in a colon, and an order that opens a record usually opens with its order
  word — so `PAUSE all lanes: → main is held`, `PAUSE lanes: → main …` and
  `PAUSE: → main …` stripped `PAUSE …` as the *speaker*, found `→ main` at the head of
  what was left, and reported `active: False`: the stop that was really given held
  nothing. A prefix may now never name one of the new `ORDER_WORDS` (`PAUSE`,
  `RESUME`, `STOP`, `HOLD`, `FREEZE`, `HALT`, `ABORT` — case-insensitive, whole word,
  read over the whole match including its `(<note>)`), and a rejected prefix strips
  nothing (`_speaker_prefix` returns the split index or None, `_acknowledgement` asks
  it instead of matching the regex directly). Two tightenings close the same door from
  the other side: a speaker word must look like a name (letters, digits and `-`, `_`,
  `.`, at least one letter — a bare count like `4 lanes:` is prose, and a token with
  any other punctuation no longer poses as a name), and the whole prefix is bounded at
  `_SPEAKER_PREFIX_MAX = 40` characters, past which a clause before a colon is a
  sentence. The wide, case-insensitive list is the deliberate asymmetry: over-ruling a
  prefix costs a spurious order — `hold on: → main merged` is classified as one, and
  `UN-HOLD:` blocks its own prefix — which only holds a lane until a RESUME, one
  wasted heartbeat; under-ruling one lets workers run against the operator's stop.
- **Measured over the real corpus again** (`logs/handoff-sessions/20260925/inbox`,
  read-only, both classifiers in memory from `git show HEAD:` so nothing was copied or
  written; one pass over 7 files / **1913 records**, 816 acks before and after, 12 of
  them resting on a speaker prefix over 3 shapes: `from L1-backlog:` ×10,
  `from L1-routing:` ×1, `from L1-backlog (relaunch #3)` ×1 — the inboxes are live and
  grew from 1896 records at the first census to 1913 at this pass, so the pair of
  classifiers is always read in the same pass):
  **0 records changed classification, 4 orders before and 4 after, 3 marker-headed
  PAUSE mentions before and after, and `pause_state` identical on all 7 files** — each
  active file's winning text exactly 80 chars, i.e. truncated as specified. The risk
  class is counted rather than assumed: **5** records open with a colon-terminated
  clause that names an order word (`PAUSE (operator, via L0 router): the host reboots
  soon …`, `from L1-main HOLD Q-001 (L0 routing-00): …`, `from L0 (operator) A8
  UN-HOLD: …`) and **0** of them have a marker after that colon — so today's writers
  never produced the losing shape and every one of these was already an order, which is
  why R2a3 shipped green while the bug sat in it (AGENTS.md §5: the fixtures carry the
  case the corpus has not).
- **Docs** (R-orch-11, wording moves with the rule): spec §0 states the
  `ORDER_WORDS` list next to `ACK_MARKERS`, the name-shaped speaker word and the
  40-character bound; `docs/routing.md` cites the same three constraints instead of the
  old character-exclusion clause alone.
- **Tests** (red before the code: 8 failed, 195 passed):
  `tests/test_autoos_heartbeat.py` 70 → 79 — the three `PAUSE …: → main …` lines →
  order (the reproduced defect, failing on the assertion, not on a missing symbol),
  one case per `ORDER_WORDS` word in three prefix shapes and the same words
  lower-cased, an order word inside the `(<note>)` rejecting the prefix, the brief's
  two named acks (`L1-main: → done: PAUSE lifted`,
  `operator on duty: → done 12:00 PAUSE lifted`) still acks, `hold on:` documented as
  the accepted spurious order, the name shape (`L1-routing.coordinator_x:` ack against
  `4 lanes:`, `2026:`, `state=held:`, `L1/routing:`, `[operator]:` as orders), the
  bound (a 55-char clause rejected, the corpus's 30-char prefix kept), and the §0
  one-home guard that `_ORDER_WORD_RE` is built from `ORDER_WORDS` and covers PAUSE and
  RESUME as whole words. Green: **203 passed** over `test_autoos_card.py
  test_autoos_inbox.py test_autoos_heartbeat.py test_suite_wiring.py` (194 at the
  branch point), 746 passed + 124 subtests over `test_autoos_report.py
  test_autoos_spawner.py test_agent_harness.py test_autoos_track.py` (unchanged).

### Fixed — an ack marker needs a boundary, a speaker may be 3 words, the body head is normalised (RESTART R2a3, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Muse review of R2a2, 1 MEDIUM + 3 LOWs,
  safety): the head-anchored marker still matched as a bare *prefix*, so
  `→ mainline PAUSE all lanes`, `→ maintenance: PAUSE` (both start with `→ main`)
  and `→ operators` / `→ doneX` were swallowed as acknowledgements of a stop that
  was never taken. `_MARKER_AT_HEAD_RE` now requires a boundary — `:`, whitespace
  or the end of the text (`(?=[:\s]|$)`) — while `→ main: merged` and
  `→ done 12:00 …` stay acks. A speaker prefix of more than one word
  (`operator on duty: → done: PAUSE lifted`, `from L1-main relay (x): → done: …`)
  was not stripped, so a PAUSE quoted inside an ack read as a fresh order and
  paused a running lane: `_SPEAKER_PREFIX_RE` takes up to 3 words for the colon-
  required `<name>:` shape and any words for `from <name>` **only** when its own
  `(<note>)` or `:` delimits it, still at most one prefix. A speaker word excludes
  `:`, `→` and parentheses, so a prefix can never eat the marker after it and a
  bare first word without a colon is never a speaker. `_acknowledgement` strips a
  BOM, spaces, tabs and CR remnants at the body head (a `  → done: PAUSE lifted`
  or a BOM-headed line read as an order), `parse_inbox_line` strips them at the
  line head so a BOM does not silently drop the record — an *order* lost is as
  unsafe as an ack missed — and a line with no timestamp is no record at all
  (§0), so it is never scanned.
- **Measured over the real corpus again** (`logs/handoff-sessions/20260925/inbox`,
  read-only, 1878 records, 7 of them naming PAUSE): **4 orders before and 4 after,
  3 marker-headed PAUSE mentions before and after, 0 records parsed or classified
  differently**, and `pause_state` agrees on every one of the 7 files. The census
  says why: 799 records are acknowledgements under the new rules, but **0** open
  with a marker-as-prefix (the `→ mainline` class), **0** carry a BOM/space/CR at
  the head and **0** files use CRLF — all three defects were latent, reachable only
  from writers the corpus has not produced yet, which is exactly the case a fixture
  suite has to cover (AGENTS.md §5: a fix with no test that failed before it
  proves nothing).
- **Docs** (R-orch-11, wording moves with the rule): spec §0 now states the
  prefix grammar exactly as implemented — boundary rule, colon optional only for
  the `from` form, up to 3 words for `<name>:`, the speaker-word character class,
  the head normalisation and the no-timestamp rule; `docs/routing.md` cites the
  same shapes instead of the old three-item list.
- **Tests** (red before the code: 5 failed): `tests/test_autoos_heartbeat.py`
  60 → 70 — boundary pairs per marker (`→ mainline` order vs `→ main: merged`
  ack, `→ operators` vs `→ operator:`, `→ doneX` vs `→ done 12:00`), both
  two-word speaker shapes as acks and the same prefixes carrying a bare PAUSE as
  orders, `from L0 (operator): → done 12:00 PAUSE lifted` ack vs
  `from L0 (operator): PAUSE NOW` order, a bounded `from` prefix that keeps a
  mid-sentence marker an order, a bare word without a colon never a speaker, the
  head normalisation (space/BOM/CR/CRLF file/BOM'd order) and the no-timestamp
  record. Green: 194 passed over `test_autoos_card.py test_autoos_inbox.py
  test_autoos_heartbeat.py test_suite_wiring.py` (184 at the branch point), 746
  passed over `test_autoos_report.py test_autoos_spawner.py
  test_agent_harness.py test_autoos_track.py`.

### Fixed — a marker counts only at the head of a record, so a PAUSE naming one still stops the run (RESTART R2a2, 2026-09-28)

- **`tools/autoos_heartbeat.py`** (the Sonnet review of R2a, MEDIUM, safety): the
  pause filter matched the §0 markers *anywhere* in the line, so
  `2026-09-28T10:00:00Z operator: PAUSE all lanes; nothing merges → main until I
  say so` — a real hard stop — read as `active: False`. A marker now counts only
  at the head of the record body: the text after the leading ISO timestamp and,
  at most, after one speaker prefix. `_NOT_AN_ORDER_RE` is gone; `_acknowledgement(body)`
  combines `_MARKER_AT_HEAD_RE` and `_SPEAKER_PREFIX_RE`, both built from
  `ACK_MARKERS` (a guard test pins them to that one list, §0). The prefix shapes
  are read off the real inboxes (`logs/handoff-sessions/20260925/inbox`,
  read-only: `→ done:` 579 times, `from <name>` 254 with no colon against 79
  with, `from L0 (operator) PAUSE NOW` at L1-routing.md:126), so the `from` form
  takes its colon optionally and a bare `<name>` needs it — otherwise an ordinary
  first word reads as a speaker.
- **Measured over that real corpus** (1796 timestamped records, 805 opening with
  a marker, 7 naming PAUSE): the scan classified **4 orders before and 4 after,
  no line changed** — today's inboxes contain no order that names a marker, which
  is why R2a's marker tests passed while the bug shipped. Requiring the colon on
  the `from` form gives the same 4, so the optionality costs nothing today and
  covers the 254 no-colon lines later. The fixtures are hand-written in those
  shapes; no inbox was copied into the repository (AGENTS.md §1).
- **`tools/autoos_card.py`** (the Muse review of R2a, LOW): the open-question
  rule was `thread_id.startswith("Q")` — it flagged a `QUOTE-2` lane and missed a
  lowercase `q-008`. The shape is `^[Qq][-:]?\d` (`_Q_ID_RE`), so `Q-008`, `q-008`,
  `Q008` and `Q:008` need an `asked <time>` field and `QUOTE-2`/`Query-1`/`Q&A`
  do not. Wording moved with the rule (R-orch-11): `card check`'s CLI help,
  `docs/routing.md`, spec §3.
- **`docs/plans/2026-09-28-restart-spec.md`** (two LOWs): §1 now states the
  counting rule the checker implements — a section's cap counts content lines
  (headings and blank lines are not content) while the 40-line total counts every
  line, and the total binds; §0 names `ACK_MARKERS` as the one list instead of the
  removed `_NOT_AN_ORDER_RE` and states the head-anchored rule.
- **Tests** (red before the code: 6 failed — 3 heartbeat, 3 card):
  `tests/test_autoos_heartbeat.py` 50 → 60 (the reproduced line → active True, the
  `→ main:` / `from L1-main: → done:` / `lesson:` heads → not orders, one case per
  marker at the head and mid-sentence, one per speaker-prefix shape);
  `tests/test_autoos_card.py` 64 → 67. Green: 184 passed over
  `test_autoos_card.py test_autoos_inbox.py test_autoos_heartbeat.py
  test_suite_wiring.py` (171 at the branch point).

### Added — `card check`: the state-card checker, and the §0 marker list completed (RESTART R2a, 2026-09-28)

- **`tools/autoos_card.py`** (new, stdlib, read-only): the §1 shape of
  `<RUN>/status/<name>.card.md` as one table — `SECTIONS` (goal 3, state 6,
  next 3, threads 12, traps 8, operator 4), `MAX_TOTAL_LINES = 40`,
  `MAX_LINE_CHARS = 200`. `check_card(text)` returns `Problem(line, text)` for
  every violation and `check_file(path)` reads it; nothing is written, so the
  check is safe to run twice (AGENTS.md §4). `last-event` parses through
  `autoos_inbox.parse_position` — §0 keeps one position parser and this module
  deliberately does not own a second one.
- **Two numbers §1 left open, now settled** (a successor has to write to them,
  so they are in the module docstring, not implied): a section's cap counts its
  **content lines** — a heading line and blank lines are not content — while
  every line in the file counts toward the 40. The caps therefore overflow the
  total on purpose (36 content + 6 headings + the header = 43): the total is
  what binds a real card. And an old `references/state-file.md` heading
  (`## Decisions + why`) is a *problem*, not a synonym — §1 says a successor
  writes a fresh card rather than converting the status file. `## Goal (…)` and
  `## traps: what bit us` do resolve to their sections, so a note on a heading
  is free.
- **§3's one card rule**: a `threads` line whose id starts with `Q` must carry
  an `asked <time>` field, because the pack's `open questions` section prints
  those lines and nothing else knows an id was asked about.
- **`autoos-agent.py card check <file>`** (parser + dispatch tables, same pair
  `inbox` uses): problems on stdout, one per line, each with its line number;
  the reason a file cannot be read on stderr. Exit 0 valid, 1 invalid, 2
  unreadable. **No MCP twin**: `inbox`, `ready` and `review-status` have none
  either — only the spawning/routing surface is mirrored in
  `tools/autoos_agent_mcp.py`, so this stays a CLI verb (open: below).
- **`tools/autoos_heartbeat.py`**: the §0 acknowledgement-marker list was
  `lesson:|→ done` only, so an acknowledgement that quotes the word PAUSE —
  `→ main: merged before the PAUSE landed` — reads as a fresh order to stop. The
  markers in use are now one named list, `ACK_MARKERS` = `lesson:`, `→ done`,
  `→ ack`, `→ relaunched`, `→ operator`, `→ main`, and `_NOT_AN_ORDER_RE` is
  built from it (a guard test pins the regex to the list, so §1 and §3 can cite
  it without restating it).
- **`tests/test_autoos_card.py`** (new, 64 tests) and marker tests in
  `tests/test_autoos_heartbeat.py` (45 → 50). Red before the code: 5 marker
  tests failed (`→ ack`, `→ relaunched`, `→ operator`, `→ main`; `→ done`
  already passed) and the card suite could not import. Green: 166 passed over
  `test_autoos_card.py test_autoos_inbox.py test_autoos_heartbeat.py`, and
  `test_autoos_spawner.py test_suite_wiring.py test_agent_harness.py
  test_skill_rules.py` 696 passed / 0 failed. Wired into both harnesses
  (`tests/linux/33-documentation.sh`, `tests/run-tests.ps1`).
- **Not here**: heartbeat's `card: stale` (lane R2b, needs the `card` parameter
  in `heartbeat_state`, `cmd_heartbeat --json` and the MCP twin) and `pack` /
  `relaunch-line` (R3+).
### Fixed — a load cannot emit an unvalidated edge, and D-88 is D-088 (memlink round 2, D-140, 2026-09-28)

A cross-family review of `2c27a24` returned NOT READY on the D-140
Task→Decision edges; this closes all six items test-first in
`tests/test_sync_memory_graph.py` (28 → 47 tests).

- **`--load` without `--known-slugs` emitted unvalidated edges** (HIGH):
  `main` now refuses (clear stderr, rc 1, nothing marked) when the emitted
  batch carries an edge-only Implements/Supersedes record and no known-slug set
  was passed — an unresolved citation would create a dangling edge. Plain dump
  mode still defaults to no filtering. The module Usage now shows how to build
  the slug list (an omnigraph query of Decision slugs written to a file). An
  `HTTPError`/`URLError` from the load endpoint becomes a named message and a
  non-zero exit, never a traceback, and the ledger stays untouched.
- **A bare `D-NNN` was not zero-padded** (HIGH): `D-88` and `routing-d-88` both
  normalise to `routing-d-088`. A test documents the design fact behind the
  review's "two ledgers" worry: this repo's D-NNN ids ARE the router's routing
  decision numbers — one number space — so the two spellings name one decision.
- **An edge batch could be marked on an empty-tables response** (MED):
  `load_confirmed` returns False when the response has no per-table detail and
  the batch carried an edge-only record (no `@key` — a retry would duplicate);
  a node-only batch keeps the prior permissive behavior.
- **`Supersedes` read across a sentence break** (MED): the citation must sit in
  the same clause as the supersede/replace verb (the window is cut at `.`, `;`
  or a newline), so a later sentence's citation is not read as a replacement.
  A data-driven test uses fixture text, not the module's DECISIONS constants.
- **Duplicate ledger keys from colliding task titles** (LOW): `records()` now
  emits each ledger key once. `--known-slugs` rejects an empty value, a missing
  value, and a value starting with `--` (so `--known-slugs --load` cannot
  swallow `--load`); `-`, a file and `=VALUE` are covered, and a test pins node
  lines before edge lines in the `--load` body.

Known limitation (not fixed): a Task's board row is its slug source, so editing
a row's wording mints a new slug and leaves the old slug's Implements edges
behind (stale-edge churn); the module docstring records it.

### Fixed — the runtime-dir fence stopped at the leaf, and the fallthrough re-run provisioned nothing (FF1 Sonnet LOWs, D-106)

Sonnet's final pass over `6bdeca5..f6d2885` closed READY with two LOWs, both the
same shape as FF1c item 3 one step away from where that fix looked:

- **The parent was walked through** (LOW): `provision_runtime_dir` judged the
  leaf (symlink / not-a-dir / another uid) but only asked `os.path.isdir` about
  its parent — which follows a symlink. A pre-existing
  `…/state/runtimes -> somewhere else`, or one owned by another account on a
  shared host, was created *and* chmod'd from underneath, exactly the leak the
  leaf check exists to stop. The parent is now `lstat`'ed under the same three
  rules, through one helper (`_provision_path_usable`) so leaf and parent cannot
  drift apart. Tests: `ChildRuntimeDirTests.test_provisioning_refuses_a_parent_*`
  (both red before the change).
- **A `--free` fallthrough re-run lost its own directories** (LOW): the re-plan
  mints a fresh run id and the private `XDG_RUNTIME_DIR`/`XDG_CONFIG_HOME` are
  named after it, but only the *first* launch site provisioned them. The
  survivor of a provider-stopped attempt therefore ran with an XDG dir nobody
  created — which the client makes itself, outside the 0700 rule. A non-isolate
  run has no clone to touch the disk, so nothing else masked it. The re-run
  site provisions both. Test:
  `ChildRuntimeDirTests.test_a_fallthrough_rerun_provisions_its_own_dirs`.

### Fixed — git still read the operator's global config, and `extra` skipped the scrub (FF1c, D-106)

Muse#high over 6bdeca5..ce65d22 confirmed all six FF1b fixes and opened four more.
Tests first in `tests/test_autoos_spawner.py` (`GitGlobalConfigFenceTests`,
`ChildRuntimeDirTests`, `SpawnerChildEnvTests`, `SubprocessEnvAuditTests`,
`WorkerEnvAllowlistTests`) and `tests/linux/33-documentation.sh` — 17 red before
the change, plus one more the first fix opened (the identity bullet below), 815
passing in the three spawner/harness/suite-wiring files after.

- **`XDG_CONFIG_HOME` and `HOME` reintroduced git's global config** (MED): the
  two-entry guard *cancels* `credential.helper` and `core.askPass`; it does not
  stop git **reading** `$HOME/.gitconfig` or `$XDG_CONFIG_HOME/git/config`, both
  of which were inherited. A worker could therefore run under the operator's
  `url.insteadOf` (a remote repointed at the parent), `core.sshCommand` and
  `core.hooksPath` (a program of the operator's), none of them reachable by the
  guard. `worker_env` now forces `GIT_CONFIG_GLOBAL=<os.devnull>` and
  `GIT_CONFIG_NOSYSTEM=1` in the git guards — the only variables that hide the
  file itself and `/etc/gitconfig`, which this repo's own installers write — and
  drops an inherited `XDG_CONFIG_HOME` the same way FF1b dropped
  `XDG_RUNTIME_DIR`; the plan points it at a private, per-run directory in the
  git-ignored state tree. `GitGlobalConfigFenceTests` writes a fake evil
  `~/.gitconfig` *and* a fake `$XDG_CONFIG_HOME/git/config`, proves a plain git
  sees them, and asserts `git config --get core.sshCommand` under the worker env
  does not.
- **`spawner_child_env(extra)` merged after the scrub** (LOW-MED): the CLI-child
  env filtered `base` and then did a bare `env.update(extra)`, so the policy for
  the one call site that passes an entry was caller discipline alone. `extra`
  now clears the same deny check and a passlist of its own
  (`_child_env_passed`: the plan passlist plus the spawner's `AUTOOS_*` state
  names — `PATH` is inheritable but not settable by a caller), and a refusal is
  printed to stderr.
- **`provision_runtime_dir()` walked through a symlink** (LOW):
  `makedirs(exist_ok=True)` accepted a leaf somebody else had pre-created as a
  symlink and `chmod 0700` was applied *through* it — on a shared host that is a
  write into, and a hole punched in, a directory this run does not own; parents
  were left 0755. It now `lstat`s first and refuses a symlink, a non-directory
  and a directory of another uid, saying so on stderr, creates the parent 0700
  and the leaf with `os.mkdir` (atomic; a racer is caught and re-checked under
  the same rules, once, rather than merged into).
- **The child-env audit missed three shapes** (LOW): the walker read five
  `subprocess.*` names and only the first element of a *literal* argv, so
  `os.system` / `os.popen` / `os.exec*` / `os.spawn*` (which take no `env` at
  all), `shell=True`, and a variable argv walked straight past it. The walker
  now classifies each site (`os_call`, `shell`, `dynamic`) and the plumbing
  exemption applies to literal argv only; a site that genuinely must run outside
  the audit marks itself with a `# subprocess-audit:` comment. Both file checks
  pass today — the exemption is exercised, not assumed:
  `test_the_walker_sees_every_shape_it_claims_to` feeds the walker the shapes it
  claims to catch and fails if it stops seeing them.
- **A sandbox had no git identity of its own** (MED, opened by the first fix
  above): hiding the global config also hides the operator's `user.name`, and
  every brief in this lane ends with the *worker* running `git commit` — it died
  on "Author identity unknown" and left the run's work uncommitted, a worse
  failure than the leak that was closed. The clone now sets `user.name` and
  `user.email` `--local` where it stands up, to the same author the spawner's
  own end-of-run commit signs with, and `SandboxGitIdentityTests` commits inside
  a real sandbox under the real worker env with a fake operator identity in
  `HOME` — so a worker commit that quietly inherited an operator again fails the
  suite.


### Fixed — the FF1 env scrub had a second door, and its push fence was an accident guard (FF1b, D-106)

Muse#high over 362b8af..6bdeca5 read FF1's own diff and found four things it
did not close. Tests first, in `tests/test_autoos_spawner.py`
(`PlanEnvPasslistTests`, `ChildRuntimeDirTests`, `PushFenceHonestyTests`,
`SubprocessEnvAuditTests`) and `tests/test_user_config_fence.py`
(`LanePathIdentityTests`) — 24, red before the change.

- **`plan["env"]` bypassed the allowlist** (HIGH): the scrub only filtered what
  the *caller* exported, then copied the plan's entries in behind it. A builder
  that set `PATH`, `LD_PRELOAD`, `PYTHONPATH` or `NODE_OPTIONS` owned the child
  and nothing said so. Plan entries now need a name on
  `WORKER_PLAN_ENV_PASSLIST` (the names the plan builders actually set:
  `OPENCODE_CONFIG_CONTENT`, `XDG_DATA_HOME`, `XDG_RUNTIME_DIR`, the gemini
  header constant, `AUTOOS_AGENT_*`) **and** must clear the deny check; anything
  else is refused and printed to stderr. The deny set gained the loader and
  interpreter injection names, `GIT_*`, `SSH_*`, `*_ASKPASS`,
  `GIT_PROXY_COMMAND`, `KUBECONFIG`, `DOCKER_CONFIG`, `AUTOOS_KEYS_FILE` and the
  `*_CONFIG_FILE` / `*_CREDENTIALS` shapes. A test derives the passlist from
  `build_plan`'s own `env[...] =` assignments, so a new plan name that is not
  reviewed fails rather than leaking.
- **git's config channels were not all forced** (HIGH): the worker's git reads
  `GIT_CONFIG_PARAMETERS` (what a parent's `git -c key=value` exports) and
  `GIT_CONFIG_KEY_n/VALUE_n` by *index*. `worker_env` now forces
  `GIT_CONFIG_PARAMETERS=""`, sets `GIT_CONFIG_COUNT` from the guard list itself
  (two entries, cancelling `credential.helper` and `core.askPass`), deletes any
  `GIT_CONFIG_KEY_n/VALUE_n` past that count, and names `GIT_SSH`,
  `GIT_SSH_COMMAND` and `GIT_PROXY_COMMAND` off rather than trusting a pattern.
- **The push fence was called a fence** (HIGH): the `pre-push` hook and the
  disabled push URLs are an **accident guard** — `git push --no-verify`,
  `core.hooksPath` and `git remote set-url` walk past both. They stay (they stop
  the worker that types `git push` at a path its own brief named), the code and
  `docs/handoff.md` now say what they are, and
  `test_a_no_verify_push_is_NOT_blocked_by_the_hook` asserts the bypass *works*
  so no future reader treats the hook as containment. Real containment is the
  disposable clone plus an environment with no credential in it. Also:
  `fence_sandbox_push()` was defined and unit-tested but **never called** — a
  real `--isolate` sandbox only had `origin`'s push URL disabled. It is wired
  into the clone site now, which is what installs the hook.
- **`XDG_RUNTIME_DIR` was inherited** (HIGH/MED): the operator's session
  directory (message bus, sockets, sometimes the agent's own) came through the
  `XDG_` allow prefix. It is now dropped from the inheritance and the plan points
  it at a private, empty directory under the state tree keyed by run id, created
  mode 0700 at the launch site (`provision_runtime_dir`) — a dry run still writes
  nothing. A test asserts no value in the child env names an ssh-agent socket or
  the operator's runtime dir.
- **`user_config_fence()` compared paths as strings** (MED): `os.path.abspath`
  folds `..` lexically and never follows a symlink, so a lane reached through a
  link read as a real checkout and the render wrote into the home directory. Both
  halves now go through `os.path.realpath`, and the comparison folds case
  (`normcase` + `casefold`) where the filesystem does — Windows and macOS open one
  directory under `AutoOS-lanes` and `autoos-lanes`; a case-sensitive host keeps
  them apart. `is_lane_checkout(..., fold=True)` lets the Linux suite prove the
  case-insensitive branch.
- **Every child site is audited** (MED): `tools/autoos_agent_mcp.py` handed its
  detached runner, its preflight and `run_job` the caller's whole environment
  (`env=dict(os.environ, AUTOOS_TASK_DIR=path)`). The three now pass
  `agent.spawner_child_env()` — the worker scrub with the one credential the CLI
  genuinely reads (`AUTOOS_OMNIROUTE_KEY`) added back. An ast-based test walks
  every `subprocess.run/Popen/call/check_output/check_call` in both files and
  fails on any site without `env=`, allowing only the ones whose argv head is the
  literal `git`/`taskkill` (the spawner's own plumbing, running as the operator on
  purpose).
- Out of scope, recorded under `open:`: per-worker `HOME` isolation.


### Fixed — a spawned worker's environment is chosen, not inherited (FF1, D-106)

- **`tools/autoos-agent.py` `worker_env()`** (new, used by both spawn sites —
  the first launch and the provider-stop fallthrough re-run): the child's
  environment is an **allowlist** of the caller's (`PATH`, `HOME`, `USER`,
  `LOGNAME`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `SHELL`, `XDG_*`, `NVM_DIR` and
  an explicitly named set of `AUTOOS_*`), plus the plan's own entries. A
  denylist only covers the names somebody remembered, and the operator's shell
  on this host holds GitHub, provider, cloud and ssh-agent credentials — all of
  which a worker could read with `env`. `AUTOOS_OMNIROUTE_KEY` is never
  inherited; the run's own minted key is added only when it uses the gateway,
  and `register_secret_env` still sees the final dict.
- **git in a worker can neither prompt nor fetch a stored credential**:
  `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS` to a binary that always fails, and
  `GIT_CONFIG_COUNT/KEY_n/VALUE_n` cancelling `credential.helper` and
  `core.askPass`.
- **`fence_sandbox_push()`**: an `--isolate` clone had its `origin` push URL
  disabled; every remote is now disabled and the clone gets a `pre-push` hook
  that exits 1, because `pushurl` does not fence `git push </absolute/parent>`
  — a path the containment brief itself names to the worker.
- **`lib/agent_harness.py` `user_config_fence()`**: rendering a user-level
  config (under `$HOME`/`$XDG_*_HOME`) from a lane sandbox checkout is refused
  (exit 1). Measured on this branch: a lane render baked the sandbox's own path
  into `.config/opencode/opencode.json` twice, so the user's next session read
  instructions and fences pointing at a directory deleted with the sandbox. A
  staged target outside any home is unaffected.
- Tests: `tests/test_autoos_spawner.py` (`WorkerEnvAllowlistTests`,
  `SpawnerChildEnvTests`, `SandboxPushFenceTests` — 15, red before the change)
  and the new `tests/test_user_config_fence.py` (13, wired into
  `tests/linux/07-mcp-pins.sh`).
### Fixed — the budget gate judges the model the runner launches (CLAUDEBUDGET-f, D-102, 2026-09-28)

- Follow-up on CLAUDEBUDGET-d, from `rev-claudebudget3.out` (FIX-FIRST). One design rule for all
  of it: **the gate must read the same value the argv carries**, computed once in the runner's own
  precedence order — explicit `--model` > the tier agent's model (for a client that goes through
  the gateway) > the client's own default > the card's combo.
  - **An unknown combo was priced as free.** `spawn_spends_claude` returned `False` whenever the
    caller named the string itself (`--model`, a registry `clients` row), so an attacker naming an
    all-Claude combo the registry does not carry was told the run costs nothing. A combo the
    registry cannot price is now "cannot tell" — refused under the budget, unchanged with the
    budget off, with the value named in the message. The name still decides for the two values that
    are not route ids at all: a client default compiled into the adapter
    (`clients.QODER_DEFAULT_MODEL`) and a caller's `--model` on an own-account client, which goes
    to that CLI verbatim. A typo in a registry `clients` row is registry data and refuses (item 5).
  - **The client default shadowed the tier.** `effective_spawn_model` consulted the registry row /
    `AGY` / `QODER` default *before* the tier, so a tier agent whose `opencode.jsonc` model IS
    Claude was priced as a free client default. The tier branch now calls the launcher's own
    `resolve_model`, so a `--clean` tier and a declared variant come back spelled exactly as the
    run receives them.
  - **`model or free_model` short-circuited the resolution.** Both spawn paths pre-OR'd the promo
    model into `model`, so `--free`/`--tier` never reached the tier branch. The flags are passed as
    flags now (`free`, `free_model`, `clean`), and the tests assert gate input == launch model
    across 12 client/tier/card/free/`--model` combinations.
  - **`AUTOOS_CLAUDE_FINAL` leaked to the closer.** `_select_reviewers` gated the `risk=high`
    closer on the env declaration alone, so any high-risk non-final card with the variable in the
    profile got client `claude` / model `sonnet`. The closer now needs the card to be a final
    (`kind`/`role`) *and* the declaration; a non-budget run keeps the behaviour it always had.
  - Two tests asserted the wrong door (item 6): `agy_default_model_is_a_claude_spend` fell back to
    a unit call because its `role=implement` card hit the capability check first — it is a CLI run
    with a read-only card now; `qoder` non-Claude never-gated asserts the run is *allowed*
    (`would run:`), not only that the budget said nothing.

### Fixed — a Claude final is declared, never claimed; the spawn gate reads the model (CLAUDEBUDGET-d, D-102, 2026-09-28)

- Follow-up on CLAUDEBUDGET-b, from `rev-claudebudget2.out` (FIX-FIRST). Three holes, all of them
  "the gate trusted the wrong side of the table":
  - **`kind=final` was self-grantable, exactly like `critical` was.** `claude_allowed("final")`
    returned True with no env, so any worker that wrote `kind=final` (or `role=final`) into its own
    card got a Claude leg, client `claude`, a Claude reviewer *and* the `risk=high` closer
    (`_CLOSER`, client claude / model sonnet), which HEAD emitted with no gate at all. A final is
    now what the orchestrator declares: `AUTOOS_CLAUDE_FINAL="<lane>@<sha>"` in the caller's env, or
    `AUTOOS_CLAUDE_CRITICAL` (which declares the bigger thing and so opens the final too). A blank
    declaration is not one. `strip_claude_env()` already removed the whole `AUTOOS_CLAUDE*`
    namespace from every child, so a worker still cannot hold or pass down a declaration — and the
    plan line now cites `final declared (<lane>@<sha>)`, not only a critical-path override.
  - **The spawn gate read the client *name*.** `qoder`/`agy`/`opencode`/`codex`/`qwen`/`gemini` with
    `--model claude-*|opus|sonnet|haiku|fable`, an `openrouter/anthropic/*` leg, or a client whose
    own default model IS Claude (`clients.AGY_DEFAULT_MODEL` = `claude-opus-4-6-thinking`) walked
    straight past `claude_spawn_refusal` and `client_held`. Both spawn paths — `autoos-agent.py run`
    and the MCP `spawn` tool — now gate the **effective model**: `--model` / `--free-model`, a
    registry `clients.<id>.default_model` row, the adapter's own default, the `--tier` agent's model
    in `opencode.jsonc`, then the card's combo route (all-Claude legs = a spend, one non-Claude leg
    falls through to it). When none of those can say what will answer, budget mode **refuses**
    instead of assuming free, and names `--model=<provider/leg>` as the way to ask.
  - **The MCP `spawn` tool takes `claude_reason`** — a per-spawn declaration threaded to
    `claude_allowed(kind="spawn")`, so an orchestrator does not have to export
    `AUTOOS_CLAUDE_CRITICAL` server-wide where every later caller inherits it. It reaches the CLI
    preflight and the runner as that one spawn's env, and the spawner strips it before the worker's
    own env. `filter_routes`' client hold asks `kind="spawn"` now instead of its default `"leg"`, so
    the plan and the spawn path are one decision.
  - Tests: `tests/test_autoos_resolver.py` `ClaudeBudgetGateTests` (a forged final gets no leg, no
    client, no reviewer, no closer; `AUTOOS_CLAUDE_FINAL` opens each; a final declaration does not
    open the ordinary kinds) and `tests/test_autoos_spawner.py` `ClaudeBudgetSpawnTests` /
    `ClaudeBudgetMcpSpawnTests` (`--model sonnet`, `openrouter/anthropic/claude-*`, agy's default,
    the unknown-model refusal, `claude_reason` unlocking exactly one spawn, budget-off unchanged).
    Non-Claude-shape tests that spawn `agy` declare the run the way `claude_env()` already did for
    `--client claude`, because agy *is* a Claude spend now.

### Fixed — the Claude budget has one gate, and only the orchestrator holds the key (CLAUDEBUDGET-b, D-102, 2026-09-28)

- Follow-up on CLAUDEBUDGET (`claude_budget`, same day). Four holes in a policy that was written
  once per caller instead of once:
  - **`critical=true` was self-grantable.** A card is written by the worker that wants the model, so
    any worker could unlock Claude. The override now needs `AUTOOS_CLAUDE_CRITICAL="<why>"` in the
    **spawning process's** env; `tools/autoos-agent.py` `strip_claude_env()` removes every
    `AUTOOS_CLAUDE*` key from each child env, so a declaration cannot travel down the tree. A held
    card that claimed `critical` is refused with `claude_budget: critical needs the orchestrator's
    AUTOOS_CLAUDE_CRITICAL`, and an allowed plan cites the declared *reason* so the DONE line says
    why Claude was spent.
  - **`wait_until` could name a Claude window** — `cc`'s 21:00 hour, i.e. "wait until Claude gets
    cheap", the opposite of holding it. `budget_wait_until` now skips every Claude provider
    (`is_claude_provider`: a provider whose every leg is Claude); with no non-Claude window on file
    the answer is `free capacity`. `tests/test_autoos_resolver.py` `test_a_claude_offpeak_window_is_never_a_wait`
    replaces the test that pinned the old behaviour.
  - **One gate: `claude_allowed(kind, env, registry, now)`**, called by the leg filter, the
    client-bound hold, **cross-family reviewer selection** (a Claude reviewer only for the final —
    this caller had no budget check at all at HEAD), **the escalation ladders** (same), `route_plan_for`
    (which now passes `client`/`env`, and `route` grew a `--client` so a plan can be asked for the
    client that would run), `autoos-agent.py run --client claude` (refused at rc 2, the existing
    "card or route refused" code, before any sandbox is cloned), and `tools/autoos_agent_mcp.py`
    `spawn`.
  - **The Claude predicate is the model's family and name, not the provider id**: family `anthropic`
    or a model id containing claude/opus/sonnet/haiku/fable, case-insensitive, under *any* provider
    (a proxy, `openrouter/anthropic/*`), plus client `claude` always. An unknown provider now fails
    toward "Claude" — guessing the other way is the side that spends the allowance.
- Deferred plans carry the same key set a ready plan does (`leg`/`p`/`theta`/`expected_cost` null,
  `reviewers`/`escalation` empty): a caller that read `plan["reviewers"]` on every plan raised only
  on a deferred one, which is the state that arrives most when the fleet is busy.

### Changed — memoised identical dry runs and split part 13 across two shards (WS-PART13)

- **`tests/linux/13-end-to-end-dry-run-only.sh`**: identical `setup.sh` runs are
  served from a memo (`memo_dry_run`, keyed on argv + every `AUTOOS_*` variable
  + `PATH` + the e2e/real home mode), so the repeated `--profile ai-coding` and
  `--check-catalog` runs happen once. Filesystem-asserting tests and the
  determinism pair keep real runs; a guard test pins the keying.
- **`tests/linux/41-end-to-end-retirement.sh`** (new): the four retirement tests
  moved out of part 13 onto their own shard (`g 41` in `tests/ci-shards.txt`),
  so the two halves run in parallel.

### Changed — DeepSeek cross-family reviews go over HTTP, with the registry's model policy (WS-DSCALL)

- **`.agents/skills/unattended-orchestration/deepseek_call.py`** (new): one paid
  completion through the OmniRoute gateway, asked for the allowed registry leg
  with `max_tokens` 4096; never local Ollama. Which served models count is
  `catalog/ai-registry.json`'s call: exactly a leg of route `deepseek-v4.1-flash`
  that `policy.leg_rules` allows, checked as served (via
  `tools/registry.leg_denied`), so V4 Pro, a dated snapshot or another provider
  never passes. Keys come from `configuration/api-keys.yml` in-process, never
  argv, scrubbed from errors.
- **Monthly DeepSeek cap, fail-closed:** `providers.deepseek.monthly_cap_usd: 25`
  (with `monthly_cap_source`; schema entry; `tools/registry.py` requires a positive
  number and a source) is read through the one reader
  `autoos_usage.monthly_cap_usd`. `deepseek_call.py` is orchestrator-only: before
  each call it reads this month's spend from the gateway's call logs and refuses
  (exit 3) at or above the cap or when the spend cannot be read.
  `SPEND_WARN_USD` (20) stays the warning line.
- **OpenRouter dropped** from the helper: `providers.openrouter` has no credit
  (BYOK answered 401), so there is nothing to fall back to.
- **`deepseek_review.sh`, `deepseek_chunked_review.sh`** call it instead of
  opencode in WSL, whose `--file` silently reviewed only the first ~1,000 lines
  and whose key file (`~/.config/autoos/api_keys.conf`) no longer existed.
  Re-created from the operator's local work (2026-09-25) and reworked for the
  DSBACK policy (2026-09-28).
- **WS-DSCALL-LOW:** `main()` refuses `--max-tokens` below the 4096 floor (exit 2, before any key or network access); a truncated call-log walk is tested to refuse the cap.

### Fixed — Windows links Claude Code and Antigravity skills from `.agents/skills` (WS-SKILLWIN)

- **`lib/windows/AutoOS.Install.psm1`**: `Install-AutoOSAgentSkills` linked
  `~/.claude/skills` and `~/.gemini/config/skills` from the retired agent-skills
  clone, so edits to `.agents/skills` never reached either client on Windows
  (Linux had moved to `link_skill_dirs`). Both directories are now destinations of
  `Sync-AutoOSAgentSkillTargets`, the same list as `agent_skill_link_dests` on
  Linux, with the same rules: a user's own entry is never touched.
- **Setup retargets links into the retired clone, on both platforms** (operator
  Q-018, 2026-09-28): a machine set up before 2026-09-25 still has links, live or
  dangling, into the retired clone, which the link rule treats as the user's.
  Setup now moves a link whose target is exactly the clone's copy of that skill —
  `Documents/{Code,code}/agent-skills/skills/<name>`, the path the installers
  used; a user's own checkout elsewhere is kept. The new link is made first and
  swapped in, a failed swap puts the old link back, and only a move that happened
  is recorded in `<dir>.autoos-backup-<stamp>` (literal old target). The target is
  never touched. Windows: `Sync-AutoOSAgentSkillTargets` passes
  `-RetargetRetiredClone` (roots from `Get-AutoOSRetiredSkillRoots`). Linux/macOS:
  `install_agent_skill_links` passes `retarget` to `link_skill_dirs`
  (`retired_skill_link`), and detection (`agent_skill_links_current`) counts such
  a link as work still to do. `AUTOOS_RETARGET_RETIRED_SKILL_LINKS=0` opts out on
  both, inside the functions too. A second run is `skipped` with no second record.
- **RESTART spec v3.6** (RSTAMEND5, 2026-09-28): **`docs/plans/2026-09-28-restart-spec.md`** resolves the Muse FIX-FIRST on v3.5 with one correction to §5's *mechanism*, its numbers untouched. v3.5 claimed hand-off caps are keyed by **(family, role)** and that `cap_for` reads a `role` field from §4's `run.json`; lane CAPD088 builds no such thing and none is planned. §5 now states what CAPD088 does: operator **D-088** (orchestration sessions 500k, workers min(40 % of window, 400k), supersedes D-085/CAPL2's interim 250k) is implemented as **model-family rows used as the role proxy** — opus, fable and sonnet **500k** (those families only run long sessions as orchestrators), spark and gemini **400k**, and the 200k class **80k** (the worker agents) — because `tools/autoos_context.py` `cap_for(model)` takes a model id and **no role argument**. `catalog/ai-registry.json` `policy.handoff_caps` is the one home for the numbers, and the comment at `DEFAULT_CAPS` (the unreadable-registry fallback) mirrors the same rows in the same order; §4's `role` field selects a §8 MCP document and nothing else, so it is no longer an input to any cap. What follows from that: the CAPD088 delivery row says family rows rather than a (family, role) key, the lane order drops CAPD088's dependency on R4, the Order and invariant bullets speak of families (tests pin each *family's* id resolving to its own number, not the wildcard; `validate`'s `cap_tokens == window × cap_fraction` invariant is stated per family row — 400000/0.4 on a 1M-window worker family, 80000/0.4 on a 200k-class family, 500000/0.5 on a 1M-window orchestration family), and the v2 D-044 row and the v3.4 CAPD088 row stop calling the cap "per role". The only `role` left in the spec is §4/§8's: the `run.json` field that selects `configuration/mcp/<role>.json`. The three §5 obligations that survive are the row **order** (`cap_for` is first-match-wins and strips a trailing `[1m]`, so every new row goes before the `*` row), the **invariant**, and **revisit** — §8's baseline protocol (the non-sidechain first assistant turn's `input + cache_creation + cache_read` on a fixed `Reply OK` probe, pinned model and role document, 3 runs, the median, reported per role to L0) is what measures what a session pays to start, D-085's ~15 min handoff and **55.5k** fresh sonnet start stand as *why* a cap is a number at all, and the 55.5k is §8's pre-R9 anchor, not a cap. v3.5's resolutions stand: a `ready <branch> <sha>` record is an open order until §7's one closing rule closes it, so an unanswered ready stays live whatever its position and counts in `kept N records: no covering done`; §7 is that rule's one home, §3's open-readies scan points at it; every inbox writer is locked, `append_inbox_line` holds the lock for every Python caller (`autoos-agent.py ready` already writes through it), a bare `echo … >> inbox` is forbidden and R8's grep reaches shell recipes only; rotate acquires exclusive with a 30 s timeout and exits 2 having moved nothing, an appender waits 10 s then appends anyway and reports `inbox: lock timeout`.
### Added — the orchestrator's own models are known review authors (AUTHORS (S1), 2026-09-28)

- **`catalog/ai-registry.json:models`**: `claude-opus-5-5`, `claude-sonnet-5`, `claude-fable-5-1`,
  `claude-haiku-4-5`, each `family: anthropic`. Measured: `autoos-agent.py ready` and
  `review-status` refused a lane record its own orchestrator wrote — *"author claude-opus-5-5 is
  not a model, leg, route or reviewer the registry knows, and not a family it declares"* — so
  records borrowed `claude-opus-4-6`, which is a false statement about who wrote the diff and
  makes the gate's detail line useless in an audit. `author_family`
  (`tools/autoos_resolver.py`) reads the `models` table first, so the smallest fix is data, not
  code: an author row carries no provider and no route names it, so none of the four is a
  routable leg (`test_an_orchestrator_author_is_not_a_leg_of_any_route` pins that, and
  `policy.leg_rules` `deny-claude-paid-api` still keeps Claude off a paid API).
- **`tools/registry.py` rule 13**: `validate` checks `policy.handoff_caps` — every row's
  `cap_tokens == round(window * cap_fraction)`, and a `match: ["*"]` fallback row exists.
  `tools/autoos_context.py` reads `cap_tokens` and never recomputes it, so a row whose pair
  disagreed stated two caps at once and the lane handed off at the stale one; with no `*` row a
  model no other row names silently got that tool's hand-maintained `DEFAULT_CAPS` instead of
  the policy. Rule 13 red first in `HandoffCapsPolicyTests`.
### Fixed — a run id cannot carry a key, the session header carries the run too, and the containers `redact_record` missed (FLEETP0c, 2026-09-28)

Muse's review of FLEETP0 (`work/L1-routing/rev-fleetp0.out`) found six defects in
the identity FLEETP0/FLEETP0b had just added, and router D-063 asked for one more
carrier. All of them are in `tools/autoos-agent.py` unless named.

- **HIGH — the slug is scrubbed before it is cut.** `mint_run_id` slugified the raw
  title/task, and `RUN_ID_SLUG_CAP` is 24 characters — *shorter* than a vendor key,
  so the cap that was supposed to keep task text out of the id kept a pasted key in
  it whole, into the branch, the sandbox dir, the printed `run-id:` line and the
  gateway header. The slug now comes from `slug_source`: the shared
  `redact.Redactor` masks first, the mask token is dropped rather than slugged, and
  only then does `slugify` cut. A title that is nothing but a key yields `task`.
  `session_tag` — the other half of that header, cut at 40 — is scrubbed through
  the same helper, because the cap missed the leak on both sides.
- **D-063 — `x-omniroute-session-id` is now `<tag>/<run-id>`,** so OmniRoute threads
  one Conversation per run (`X-AutoOS-Run-Id` rides beside it unchanged). Measured
  read-only in the running gateway: `resolveConversationId` takes
  `header.trim().slice(0, 128)` with **no charset check** — the limit is a length,
  and it truncates silently, which would cut the run id off the end. `session_header_value`
  therefore sends the tag alone when the pair would not fit, with one warning line,
  instead of sending a value the gateway would chew.
- **`tools/autoos_usage.py` — `--by lane` is the part before the FIRST `/`,** so an
  old `<lane>/<title>` row and a new `<lane>/<title>/<run-id>` row group together,
  and **`--by run`** is the part after the last `/` when `is_run_id` recognises it
  (`(no run id)` otherwise — a tag's tail is a title slug, not a run). The run-id
  shape is written a second time here because the spawner delegates `usage` *to* this
  module and cannot be imported back; `test_the_run_id_shape_matches_the_one_the_spawner_mints`
  pins the two copies, `test_every_id_the_spawner_mints_is_a_run_to_usage` checks them
  against real mints. The key column is capped at one whole id (48), not 40.
- **MEDIUM — the two headers go to every gateway client that can stamp a request,**
  not to opencode alone. `omniroute run` has no header option (`omniroute run
  --help`), so each launched CLI has to carry them itself; measured 2026-09-28 by
  pointing the launcher at a local listener (`--remote http://127.0.0.1:<port>`)
  and reading the headers back off the request — no gateway spend, no completion:
  `codex` takes `-c 'model_providers.omniroute.http_headers.<name>="<value>"'`,
  and only *before* its `exec` subcommand (after it the override replaces the
  whole `model_providers` table and codex dies on "provider name must not be
  empty"; a quoted key segment is dropped without a word); `gemini` takes env
  `GEMINI_CLI_CUSTOM_HEADERS="name:value,name2:value2"`. Carriers live in
  `tools/autoos_clients.py` (`HEADER_CLIENTS`, `gateway_header_args`,
  `gemini_custom_headers`) and the plan's `session_tag` is set for both, so their
  dry run prints the tag it really sends. **`qwen` cannot**: its `customHeaders`
  exists only in a `settings.json` inside the temporary `QWEN_HOME` the launcher
  writes and deletes, and the one env hook (`QWEN_CODE_SYSTEM_SETTINGS_PATH`) is
  the machine-wide system file — not something one spawn may write. Those rows
  stay `session_tag = null` (`(untagged)` / `(no run id)`), are attributable
  host-side only, and get no `session-tag:` line, so nothing claims an
  attribution the gateway never received. `docs/routing.md` carries the table.
  The only gateway call the spawner makes itself is the `/api/health` probe, which
  is not a completion and has no conversation.
- **LOW — `_redact_value` recurses tuples, sets and frozensets** (it walked dicts and
  lists only, so `skipped_legs` as a tuple reached the record with its key intact) and
  **a fallthrough re-run adopts the reused clone's `agent/<suffix>` only when the suffix
  is canonical** (`is_canonical_run_id`) — before, any branch tail became a run id
  unvalidated. **A record whose caller's `AUTOOS_AGENT_RUN_ID` equals its own id now
  stores no parent edge**, which is what a self-parent is.
- **Tests** (`tests/test_autoos_spawner.py`): header value shape, the tag/run split
  back apart, the 128-char fallback and its warning, key-shaped titles, reuse of a
  non-canonical branch, the self-parent record, and a **parent-cycle `ps --tree` case**
  (A↔B: both rows listed, one entered as a root, the walk returns); plus the two
  new carriers (codex argv prefix and its position before `exec`, gemini's env value
  re-parsed with the CLI's own split rule) and the negative that keeps `qwen` honest.
- **Test hermeticity** (`tests/test_autoos_spawner.py`): `clean_env` now drops an
  ambient `AUTOOS_SESSION_TAG` the way it already dropped the ambient key, and the
  two tag classes `pop` it in `setUp`. A spawn inherits the caller's tag, so two
  `SessionTagTests` failed only inside a lane session and passed on a bare checkout
  — the suite measured the machine it ran on, not the code (R-worker-02).

### Added - `run --run-id`, so an MCP spawn and its run share one id (FLEETP0b, 2026-09-28)

- **`tools/autoos-agent.py`**: FLEET left one open item - the MCP server's `spawn`
  still minted `logs/agents/<id>` with its own local-time `stamp-hex6` (no slug),
  so an MCP spawn had two ids again (FLEETSPEC §5.1 says one). `run` now takes
  `--run-id <id>`: the caller's id is used instead of a mint, and names the same
  four places a minted one does - `agent/<id>`, the `--isolate` clone dir,
  `logs/workers/<id>.json`, the child's `AUTOOS_AGENT_RUN_ID` (and so the
  `X-AutoOS-Run-Id` header). A run id is a filename, a branch and a header value,
  so a shape that is not the canonical one is refused with exit 2 by
  `is_canonical_run_id` (a real UTC stamp, a slug within `RUN_ID_SLUG_CAP`, a
  6-hex tail) before any clone, record or client start. The `parent_run_id` edge
  is unchanged and stays the caller's own `AUTOOS_AGENT_RUN_ID`: a handed-in id
  is never read from that variable, or the child would name itself its parent.
- **`tools/autoos_agent_mcp.py`**: `spawn` mints with the agent module's own
  `mint_run_id` (the module it already loads via importlib for `route`), keeps the
  collision retry, names its run dir with that id, records it in `job.json`
  (`run_id`, beside the `id` `status()` reads) and passes it to the CLI as
  `--run-id` - built into the argv *before* the dry-run preflight, so the CLI
  checks the id it will actually be started with and a refusal still creates no
  run dir. `run_job`'s environment is untouched, so the server's own
  `AUTOOS_AGENT_RUN_ID` (when the MCP client is itself a spawned run) stays the
  parent of everything it spawns. Its local-time mint and the `secrets` import
  that only it used are gone.
- **`tests/test_autoos_spawner.py`**: `McpCanonicalRunIdTests` (canonical id for
  the run dir, `--run-id` in the argv with the task still last, `job.json`
  carrying `run_id`, a refused spawn leaving no dir, the caller's id flowing as
  the parent), plus `run --run-id` cases in `CanonicalRunIdTests` (one id in
  branch + dir + child env + header, the CLI printing the id it was given, 13
  malformed shapes refused at exit 2) and in `RunIdRecordTests` (a handed-in id
  is parented to the caller, not to itself; a top-level one leaves no parent; a
  malformed one is refused before `build_plan`). Red before: **8 failed**; green
  after: **700 passed / 111 subtests** on `tests/test_autoos_spawner.py
  tests/test_autoos_heartbeat.py`, with `test_agent_harness.py`,
  `test_autoos_context.py`, `test_autoos_inbox.py`, `test_autoos_tokenrate.py`,
  `test_autoos_track.py` and `test_autoos_usage.py` at 227 passed. An MCP dry-run
  spawn end to end prints one id: its run dir, the argv and the CLI's `run-id:`
  line agree (`20260928-102313-mcp-id-check-9362d5`).

### Added - one canonical run id per spawn, its parent edge, and the route it was scored on (FLEET, 2026-09-28)

- **`tools/autoos-agent.py`**: a spawn minted **two** ids from **two** clocks and nothing tied them
  together - the `--isolate` clone and its branch were stamped from `datetime.now()` (local time, a
  slug of the *task*), the worker record minted a separate UTC `stamp-hex6`, and the `logs/agents/`
  run dir a third, so a console could not say which record, clone, branch and request belonged to one
  run (FLEETSPEC §5.1/§10 P0). `mint_run_id` mints `YYYYMMDD-HHMMSS-<slug>-<hex6>` **once, in UTC**,
  the slug the run's title capped at 24 chars of `[a-z0-9-]` (never task text past it: a brief can
  carry a key), and it is now the clone dir suffix, `agent/<id>`, `logs/workers/<id>.json`, the
  child's `AUTOOS_AGENT_RUN_ID` and the `X-AutoOS-Run-Id` header sent beside
  `x-omniroute-session-id` (a fallthrough re-run keeps the first attempt's clone, branch and id - one
  spawn is one id). A record stores `parent_run_id` (the `AUTOOS_AGENT_RUN_ID` this spawner was
  itself spawned with, `None` at top level), `host` (`socket.gethostname()`, in the git-ignored
  record only - never in a committed fixture), `task_dir` (the `AUTOOS_TASK_DIR` run dir the child
  asks back in, which links the third id) and the resolver's whole `route_plan`; `redact_record` now
  walks nested values, because the resolver's `reason` carries whatever a probe line said.
  `ps --tree` prints the spawn tree - children indented under the run that spawned them, an orphan
  whose parent record is gone a top-level row marked `(parent <id> gone)`, an unvisited row still
  emitted so a cycle never swallows a run - and `ps --json` rows carry `parent_run_id` untruncated.
  Old records with none of these fields still list; nothing else renames.
- **`tests/test_autoos_spawner.py`**: `CanonicalRunIdTests`, `RunIdRecordTests`, `PsTreeTests`
  (22 cases: the id's shape, its UTC stamp and 24-char cap, no task text past the slug, one id in
  branch + dir + record + child env, the parent inherited from the spawner's own env, `ps --tree`
  ordering and the gone-parent row, the host (a fake one, patched in), `route_plan` persisted and
  redacted, the header injected next to the session tag, an old record still listing). Red before:
  **20 failed / 584 passed** on `tests/test_autoos_spawner.py tests/test_autoos_track.py`; green
  after: **604 passed / 0**, with `test_agent_harness.py`, `test_autoos_context.py`,
  `test_autoos_heartbeat.py`, `test_autoos_usage.py`, `test_autoos_measure.py` and
  `test_autoos_resolver.py` at 415 passed / 1 skipped. Six pre-existing fakes of `build_plan`'s
  output gained the `run_id` the plan now carries, `SandboxUniquenessTests` its `t2-` prefix (the
  slug is the title, and a titleless spawn's title *is* `tN <task head>`), and the MCP server's own
  `logs/agents/<id>` naming is deliberately untouched - it is a second file, and the record's
  `task_dir` is what links the two ids meanwhile.
### Changed — hand-off caps: orchestrators 500k, workers min(40% of window, 400k) (CAPD088, operator D-088, 2026-09-28)

- **`catalog/ai-registry.json`** `policy.handoff_caps` and **`tools/autoos_context.py`** `DEFAULT_CAPS`:
  Opus 500k (was 600k), Fable 500k (was 600k), Sonnet 500k (was CAPL2's interim 250k) - orchestration
  sessions, 1M window; Muse Spark 400k (was 300k), Gemini 400k (was 200k), the 200k class 80k (was
  150k) - worker agents. Rows key on model family as the role proxy (comment at `DEFAULT_CAPS`).
### Fixed — the key fence spells the real file names, so no leaf reads or cats them (KEYDENY, 2026-09-28)

`catalog/agent-harness.json` fenced `*api_keys*` (underscore) while the real file is
`configuration/api-keys.yml`, and the gateway keys `~/.config/autoos/ai-stack/client.key` /
`manage.key` were not fenced at all — a spawned leaf could `Read` and `cat` all three; they are
now denied for read and shell everywhere (with `*api-keys.example*` allowed through the same
deny-then-allow mechanism as `*.env.example*`), and `opencode.jsonc`'s `t3-reviewer`, which
allowed every `read`, carries the `read_deny_all` patterns as denies.

- KEYDENY2 (2026-09-28, same lane): that shell **allow** was matched against the whole command
  line, not a path, so `cat configuration/api-keys.yml configuration/api-keys.example.yml`,
  `cp configuration/api-keys.yml /tmp/api-keys.example/x` and `cat /tmp/api-keys.example/stolen`
  all resolved to `allow` — a substring allow can never fence a command line. Both shell allows
  are gone (`bash_allow_all` is empty; `*.env.example*` had the identical abuse), a leaf reads
  the template with the read tool, and the read allow is narrowed to the exact suffix
  `*configuration/api-keys.example.yml`, which also denies `/tmp/api-keys.example.yml.bak`.
- KEYDENY3 (2026-09-28, same lane): the fence covered the **`read` tool only**, and opencode
  v2.0.16 matches a *different* resource per action — a `grep`/`glob` rule is matched against the
  search **pattern**, the searched **path** is checked only by `external_directory`, and an MCP
  rule can name the **tool** and nothing else (every MCP call is asserted as
  `{action:"<server>_<tool>", resources:["*"]}`). So a leaf could still pull the key bytes with
  `grep`, with `serena_read_file`, or with a symbol tool's `include_body`. The same
  `read_deny_all` / `read_allow_all` lists now also render as `grep`, `glob` and
  `external_directory` maps, and the new `mcp_servers.serena.raw_content_tools` (serena 1.7.0:
  `read_file`, `search_for_pattern`, and `find_symbol` / `find_declaration` /
  `find_implementations` / `find_referencing_symbols` through `include_body`) is denied to every
  leaf role in the render and dropped from `t3-reviewer`'s serena allow list. Name-only tools
  (`get_symbols_overview`, `list_dir`, `find_file`, diagnostics) stay; spawning roles keep the
  readers, because there is no path scoping and denying them there would cost every file read.
- KEYDENY3b (2026-09-28, same lane): three holes left in that fence. **(1) the spawn gate was
  denied in one spelling only** — v2.0.16's rename map is `{bash: shell, task: subagent,
  apply_patch: patch}`, the `permission` object declares the alias key `task` ("Deprecated alias
  for subagent") and the rule lists assert the action `subagent`; a leaf that can spawn hands its
  work to a child that carries none of its read fences. `SPAWN_GATES` now renders both verdicts
  from one `role.spawn`, `opencode.jsonc`'s tier agents carry both rules, and every opencode run
  re-asserts them in its own overlay (which merges last), so a drifted checkout cannot re-open it.
  **(2) `grep "sk-" .` inside a working checkout reads `configuration/api-keys.yml` straight
  through the pattern fence**, so the guarantee moved to the directory: `--isolate` is mandatory
  for a leaf (`LEAF_TIERS`, tier 3 — the catalog's `leaf: true` roles and `t3-reviewer`; the run
  is refused with rc 2 before anything starts, a `--dry-run` announces the refusal), the clone
  step is one function whose containment is now tested (a temp repo with an ignored fake key:
  the clone carries no ignored, no untracked file), and no client is exempt from isolating, so
  no leaf had to lose grep/glob. Tier 2 stays allowed in place — it is a spawning role and the
  documented lane flow — and its residual pattern hole, including the native child it can launch
  into that cwd, is written down rather than claimed closed. **(3) a leaf could list `context7`**,
  and an MCP rule can never scope a resource, so `LEAF_ALLOWED_MCP` pins the leaf set to
  `serena` + `graphify` in code, `check` refuses a leaf that lists anything else, `context7` is
  out of the leaf roles and their vendored profiles, and `t3-reviewer` denies `playwright_*` and
  `context7_*` whole (`browser_navigate` takes a `file://` path — `run_code_unsafe` was never the
  only door).
### Fixed — the risk classifier's six silent `normal`s (RISKTIER-a2, 2026-09-28)

- **Cross-family review of RISKTIER-a (Muse xhigh on `d7fa2c8`), and every finding
  was red before its fix** (72 red of 108 in `tests/test_autoos_risk.py`). The one
  failure mode this module may not have is a diff that reads `normal` because the
  reader never saw it; five of the six were exactly that.
- **A rename kept only the name it moved to.** `git mv AGENTS.md docs/AGENTS.md`
  reports one changed path, so a rule on `AGENTS.md` watched a policy file walk out
  of the class that guards it. `changed_files` now carries `old_path`, every
  `path_glob` is tested against both names, and the reason line says which moved:
  `policy: AGENTS.md -> docs/AGENTS.md`.
- **Secrets were path-only.** `**/*.key` and `**/*secret*` catch a file named for
  what it holds and nothing else, so a key added to `notes.txt` was `normal`. Five
  `added_regex` content rules now read the ADDED lines for the high-confidence
  shapes (`-----BEGIN … PRIVATE KEY-----`, `AKIA…{16}`, `ghp_…{36}`, `sk-…{20,}`,
  `xox[baprs]-`), and `**/*.pem`, `**/.env`, `**/.env.*` join the path rules. A new
  `exclude` field on `path_glob` keeps the tracked `.env.example` templates out of
  it — AGENTS.md rule 1 says commit those, and a rule that punished honest
  templates is a rule that gets switched off. `tools/registry.py` rule 12 knows
  both new fields. (Test fixtures assemble these shapes at runtime, so no tracked
  file ever holds a literal key; the public scrub scan stays green.)
- **`high` risk was cheaper to review than `normal`.** The RISKTIER-a registry
  declared `review_counts.high` as 1 cross-family + the final, while operator Q-013
  (common.md, D-060) says two diverse cheap cross-family reviews and, for high, the
  same two *plus* the Sonnet final. `high` is now `cross_family: 2, final: true`,
  which also agrees with the resolver's own D2 fallback constants it had been
  overriding.
- **`--sha HEAD` exited 2, and one commit had two audit answers.** `HEAD`, a branch
  or a tag is not hex, so the draw raised while the diff classified perfectly; and
  `audit()` buckets 12 hex digits, so `28ada0a` and the 40 digits of the same commit
  could land in different buckets — a caller who learns the friendlier spelling.
  `assess` now resolves the rev with `git rev-parse --verify <rev>^{commit}` (a tree
  or blob is refused) and uses that hex everywhere, reporting it as `sha` /
  `commit:` so a lane records which commit the answer is about.
- **The diff was parsed line-and-tab, which git quotes.** `--name-status` C-quotes a
  path containing a tab or a newline — and escapes the tab with the very character
  the parser splits on, so the path arrived wrapped in quotes and matched no glob.
  `changed_files` and the new `deleted_lines` read `-z` and split on NUL (a rename's
  two paths by the arity its status declares, so a file named `A100` is never
  mistaken for a status). The same quoting hits the `+++` header of `git diff -U0`:
  an awkward filename left `current` unset for the file's whole body and dropped
  every added line in it, `sudo` included. Added lines are now unquoted too.
- **The rule table's gaps, and how much a change deletes.** `**/*.sql`,
  `**/setup*`, `**/bootstrap*`, `**/Install*` (case-sensitive, so it is not the
  existing `**/install*`) and `docs/**/*spec*.md` joined the globs; the generic
  `diff_deletion` rule gained `min_deleted_lines: 200`, because a change that
  deletes 240 lines from a file that *survives* is a large deletion and only a whole
  file disappearing used to read as one. Corpus: `f5744611` (679 lines removed, no
  file lost) flips `normal` → `high`; `967021cb8`, which edits
  `stack.env.example`, stays `normal`.
- Tests: 108 in `tests/test_autoos_risk.py` (was 82), wired into both harnesses
  already. No CLI surface changed but the `risk` verb's output, which now leads with
  the resolved commit.

### Added — the risk class of a change is decided from its diff, by code (RISKTIER-a, 2026-09-28)

- **Operator Q-013 / D-060**: `card.risk` was the writer's own typing, and the
  registry's `policy.risk_rules` were data no code read. `tools/autoos_risk.py`
  (stdlib, pure, one injectable git runner) now applies every rule to the diff at
  `merge-base(base, sha)`: `path_glob` and `diff_deletion` (the two declared
  shapes), plus the two the operator asked for — `added_regex`, an added line
  matching a pattern (`\bsudo\b`, case-sensitive and deliberately textual: a test
  that only *mentions* sudo raises the class), and `registry_policy`, which loads
  `catalog/ai-registry.json` at both ends and compares only its `policy` object,
  so a models-only edit is not a routing-policy edit. `audit(sha, percent)` is
  `int(sha[:12], 16) % 100 < percent`: a property of the commit, so re-running
  after an unlucky draw cannot shop for a friendlier bucket. Any git failure
  raises `RiskError` (exit 2) — an unreadable diff never reads as `normal`.
- **`tools/registry.py` rule 12** validates the new shapes, and refuses a rule
  field its type does not read. A `paths` on a `path_glob` looks like a scope and
  is not one; `classify()` ignores it silently, so validate says it out loud.
  `policy.risk_rules` grows the 17 operator rules; `risk_audit_percent` (20) and
  `review_counts` (normal 2 cross-family / high 1 + final) carry their source.
- **`tools/autoos_resolver.py:_review_policy()`** reads the count from
  `policy.review_counts[risk].cross_family` and the Sonnet close from `final`,
  falling back to D2's constants when a registry predates the field — tested both
  ways. The zero-reviewer case now genuinely means zero (the cap is checked
  before the pick, not after).
- **Two silent-`normal` bugs found while reviewing this** (both red before the
  fix, `tests/test_autoos_risk.py`): a developer's `diff.noprefix=true` removes
  the `b/` the `+++` header is read through, so *every* added line vanishes and a
  sudo change classifies as normal; and git escapes an added line that starts
  with a plus by doubling it, so a line of `+++++ x` in a test fixture was
  dropped as a header. The default runner now pins `core.quotepath`,
  `diff.noprefix`, `color.diff` and `--no-ext-diff`, and the parser tells a
  header from content by hunk position.
- **CLI**: `python3 tools/autoos-agent.py risk --sha <sha> [--base origin/main]
  [--repo .] [--json]`. Wiring that class into `ready`/`review-status` is the
  sibling lane (RISKTIER-b). Tests: `tests/test_autoos_risk.py`, wired into both
  harnesses.

### Fixed — one machine-wide tool_calls overlay, and a loud reason when it is missing (OVERLAYHOME, 2026-09-28)

The overlay lived at `<checkout>/logs/routing/measured.json`. At 12:5xZ the main
checkout had none, so `route` skipped every agentic leg as `tool_calls: ...
unproven` and returned `input_required` — it looked like a fleet-wide outage.

- **`tools/autoos_overlay.py`** (new): the one path — `$AUTOOS_MEASURED_OVERLAY`,
  else `${XDG_STATE_HOME:-~/.local/state}/autoos/measured.json` (Windows
  `%LOCALAPPDATA%\autoos\measured.json`) — plus load with a read-only legacy
  fallback (one stderr note; the old file is never deleted), an atomic mode-600
  save, `status` and the missing-overlay reason.
- **Readers**: `tools/autoos-agent.py` (`route`, `run`/`spawn` routing,
  `propose_reprobe`) and `tools/autoos_agent_mcp.py` read through it. With no
  overlay anywhere, an agentic card that fails on tool_calls says `no tool_calls
  overlay found at <path> (run tools/probe-toolcalls.py or set
  AUTOOS_MEASURED_OVERLAY)`. `heartbeat --json` gains `overlay: {path, present,
  age_hours}`.
- **Writers**: `tools/probe_common.py` (`probe-toolcalls`, `probe-recall`,
  `probe-effort`) default to the new path; the first run reads the legacy file so
  its verdicts carry over (only when `--overlay` is not given). The
  read-modify-write holds a lock on `<overlay>.lock` and merges only this run's
  changes into the file as it is now, so probes running at once lose nothing.
- `$AUTOOS_MEASURED_OVERLAY` is expanded (`~`, `$VAR`) and made absolute. The
  loud reason is gated on the resolver's structured `unproven_toolcalls` flag,
  not on reason text.
### Changed — Sonnet orchestrators hand off at 250k, not 150k (CAPL2, routing-00 D-085, 2026-09-28)

- **`catalog/ai-registry.json`** `policy.handoff_caps.claude-sonnet-1m` (window 1M, 0.25 = 250k) and
  **`tools/autoos_context.py`** `DEFAULT_CAPS`: a sonnet L2 no longer falls into the 200k-class row.
  Measured: L1-backlog handed off every ~15 min at 150k with a 55.5k fresh-session baseline. Temporary
  until the RESTART packs land; lowered again if a measured relaunch costs < ~20k. Haiku and unknown
  models keep 150k.
### Fixed — `codestral-latest` counts as training until a source says otherwise (MISTRALFIX3, Muse review of MISTRALFIX2, 2026-09-28)

- **`catalog/ai-registry.json`**: `models.codestral-latest.trains_on_prompts: true` (operator 12:0xZ
  "Mistral trains -> never in -clean tiers"), so `private_safe()` keeps it out of every `-clean`
  route; its limits row names both sources (10:5xZ direct headers for rpm/tpm, 11:5xZ gateway
  answers); the Mistral provider note counts three limits rows. Guard tests in `MistralReplaceTests`.

### Fixed — the codestral pair replaces `mistral-small`; the `-clean` twins stay non-training (MISTRALFIX2, 2026-09-28)

- **Operator brief (via L1-main 11:5xZ)**: `mistral-small` does not work — replace it
  with Mistral Codestral wherever it was a leg, and where both codestral siblings
  answer, take `mistral-code-latest`. Measured through the gateway (3 calls each,
  `max_tokens 4096`, `work/L1-routing/MISTRALREPL.probe.jsonl`):
  `mistral/mistral-code-latest` 3/3 200 p50 0.3 s, `mistral/codestral-latest` 3/3 200
  p50 0.3 s, `mistral/mistral-small-latest` 0/3 (429) — the gateway agrees with the
  10:5xZ direct probe (MISTRALFIX).
- **`catalog/ai-registry.json:models.codestral-latest` +
  `providers.mistral.limits.codestral-latest`**: registered with the measured 125 rpm /
  625k tpm row and the gateway-probe source, and **not** made a leg of any route — it is
  the tested alternative, which is what the brief's tie-break left it. `context_advertised`
  / `output_max` / `reasoning` are inherited from the provider's other records, not
  measured here, and the `$comment` says so; `trains_on_prompts` stays unset (inherits
  `providers.mistral`, the shape `mistral-small-latest` carried) because nobody has
  measured whether codestral trains. `mistral-code-latest` already had its measured row.
- **The requested `-clean` swap was measured and refused, not skipped**: putting
  `mistral/mistral-code-latest` in `t2-worker-clean` / `t3-driver-clean` (the position
  `mistral-small` held, which would have given each twin two live legs) makes
  `tools/registry.py validate` exit 1 twice — `privacy: <route> leg
  mistral/mistral-code-latest model trains on prompts` (spec 3.1 rule 3, `private_safe()`).
  Those are the routes a `privacy=sensitive` card lands on
  (`tests/test_autoos_spawner.py`), and `tests/linux/33-documentation.sh` independently
  bans `mistral/mistral-code` in any `-clean` combo, so the leg would have routed private
  prompts into a training pool. The twins keep MISTRALFIX's legs and their one live leg,
  the native `deepseek/deepseek-flash` head, which satisfies the route-liveness invariant.
- **`t3-driver`** needed no change and no new leg: `mistral/mistral-code-latest` was
  already its head, so the dead model's slot is covered without duplicating the leg
  (`MistralReplaceTests` pins the count at 1).
- **Tests, `tests/test_registry.py:MistralReplaceTests` (written first, red, then made
  green)**: codestral's measured row and source, codestral in no route, the probe recorded
  in the registry, `mistral-small` in no route, `t3-driver`'s single mistral-code leg — and
  the guard that the `-clean` twins carry no training leg and exactly one live leg, so a
  later lane cannot "finish" this swap by adding it. The route-liveness invariant now reads
  through the new `live_legs()` helper, shared with the count tests instead of restating the
  filter.
- **Docs**: `docs/models.md` records the gateway probe and the refused swap next to the
  MISTRALFIX plan-limits table; `docs/models-proposed.md`'s three rows for the affected
  combos stop listing `mistral-small` as a leg and name the reason. All five `render
  --check` surfaces pass unchanged — no route's legs moved and a route-less model renders
  nowhere, so `combos.json`, the litellm block, `ide-models.json` and the OpenHands
  profiles had nothing to re-derive.

### Fixed — Mistral plan limits are measured and gate the route; no route routes into a dead leg (MISTRALFIX, 2026-09-28)

- **`catalog/ai-registry.json:providers.mistral.limits`**: the plan itself, not the
  provider's uptime, is what killed `mistral-small-latest` — a direct probe
  (2026-09-28T10:5xZ, `x-ratelimit` headers on `api.mistral.ai`) returns 429 at
  **0 req/min** for it while `mistral-code-latest` serves 125 rpm / 625k tpm on the
  same key. The measured rows now live in the registry's one home for plan caps
  (`rpm`/`tpm`/`source`), and the bare-spelling models that were never registered
  (`devstral-latest`, `mistral-medium-latest`, `magistral-medium-latest`,
  `codestral-latest`, `open-mistral-nemo`, `ministral-8b-latest`, and
  `mistral-large-latest` at 403) are recorded in the provider `$comment` only —
  rule 10 rejects a limits key that does not resolve to a model of that provider.
  The gateway agrees: `mistral-small-latest` failed 51/51 calls over 7 days.
- **`tools/registry.py:plan_dead_reasons()`** (new, alongside
  `provider_plan_limits()`): a leg is plan-dead when its row says `rpm: 0` or
  `plan_available: false`; no row is never a deny. `plan_available` joined
  `$defs.provider_limits` in the schema and `_LIMITS_ENTRY_KEYS`, so rule 10 now
  rejects a non-boolean (a string would read as available).
- **`tools/autoos_resolver.py:usable_legs()`** had no plan gate at all — only a
  request-size `tpm` check — so a 0-rpm leg survived every filter and got served.
  `plan_dead_reasons` now runs immediately after the availability hard check,
  naming the reason `"plan: 0 rpm"`. The stale "rpm/rpd/tpd are data only" claims
  in that module are corrected: `rpm` gates, but only for its zero.
- **`routes.t3-driver` / `t2-worker-clean` / `t3-driver-clean`**: `mistral/
  mistral-small-latest` is out of all three (13→12, 4→3, 3→2 legs). The `-clean`
  twins do not need marking unavailable — after DSBACK they head on native
  `deepseek/deepseek-flash`, which is live and plan-ungated, so each keeps a
  serving leg. `t3-driver` keeps its paid `mistral/mistral-code-latest` leg.
- **Invariant, `tests/test_registry.py:MistralPlanLimitsTests`**: every route that
  serves traffic (`servable_route_ids`) keeps at least one leg that is
  gateway-servable *and* plan-alive, over the real registry — the check that would
  have caught this on the day the leg was added. A route with zero servable legs
  renders no combo and so serves nothing, which is why `t1-orchestrator-clean` is
  correctly outside the rule rather than exempt from it.
- **Renders** re-derived from the registry: `sync-router-tiers.py` rewrote the
  litellm managed block, `docs/models.md`'s managed block plus its mermaid chains
  and provider table, `configuration/omniroute/combos.json` by minimal hand-edit
  of the three leg arrays (its hand-written `$comment` is an intentional equality
  exception and stays); `validate` and all five `render --check` surfaces pass.
- **Stale pins re-derived, not loosened (R-worker-01)**: `RuleThreePrivacyTests`
  read the live head leg from the registry instead of hard-coding mistral-small,
  and three resolver tests that called it "the proven leg" now use
  `test_autoos_resolver.py:clean_head_leg()`; `PlanLimitsGateTests` seeds the dead
  plan inline so the gate is tested without `measured.json`.

### Fixed — `token-rate` promotion re-uses the counted usage; `--json` hides the default repo (RESTART R5A5, Muse's fix-first review of R5A3+R5A4, 2026-09-28)

- **`tools/autoos_tokenrate.py`** (HIGH): R5A4's `Totals.add` lets a second copy
  of one response add no usage and still move it *into* the subagent view — and
  the move carried the **duplicate's** numbers. The two are only equal while every
  copy of a response repeats the same usage, which this client does not do:
  measured over the R5a window, 872 of the 13,691 collapsed duplicates carry a
  different usage than the record that got counted (the growing partial usage of
  a streaming response, R5A4's own "first-wins, measured" note), so a promotion
  could put more into `subagent_weighted` than `weighted` holds for that response
  and break D-045's rule that the subagent columns are a *view onto* the
  numerator. `counted` now stores what was summed beside the claim —
  `(sidechain, weighted, naive)` per identity — and a promotion re-uses exactly
  those. **Latent on this host**: 0 promotions occur over the whole R5a window
  (parent and `subagents/` files share no response id, as R5A3 measured), so all
  three rows re-ran to R5A4's numbers to the token — L1-routing 4,964 records /
  104,768,807.8 weighted / 24,380,131.1 subagent / 23.3 % / 28 merges /
  3,741,743.1 per merge, L1-backlog 6,205 / 135,999,291.6 / 62,663,439.9 /
  46.1 % / 34 / 3,999,979.2, L1-main 4,034 / 107,119,446.4 / 45,288,117.6 /
  42.3 % / 76 / 1,409,466.4. No before-number moved; R5b still compares against
  the R5A4 table.
- **`--json`**: an unnamed `--repo` prints `"default"` instead of the resolved
  current directory. That was the same leak R5A3 closed for `--projects-dir`
  (hard rule 1 — the path carries the operator's username), reached by a
  different flag; a named `--repo` is still echoed as given, and `--no-git` still
  reports no repo at all.
- **`tests/test_autoos_tokenrate.py`**: 6 new cases — the promotion across the two
  depths with a deliberately *larger* duplicate (the streaming shape), a property
  sweep over the fixture usages at both depths asserting
  `subagent_weighted <= weighted` and `subagent_naive <= naive` for every
  ordering, and four `--json` repo cases (report-level and CLI-level, defaulted
  and named). 15 red before the fix (the promotion case, 12 of its sweep
  subtests, and the two repo echoes), **66 green + 32 subtests** after;
  `python3 -m pytest -q tests/` 2,245 passed / 4 skipped,
  `bash tests/run-tests.sh --filter token-rate` 1 passed / 0 failed.

### Changed — `token-rate` counts each API response once (RESTART R5A4, the metric owner's answer to R5A3's open caveat, 2026-09-28)

- **`tools/autoos_tokenrate.py`**: R5A3 left the numerator counting *turns x
  content blocks*, because a record's identity was its transcript `uuid` and this
  client writes one record per content block, each repeating the same
  `message.id`, the same top-level `requestId` and (usually) the same usage. The
  decision — the metric counts one **API response** — moves identity into a new
  `response_identity`: `message.id` plus `requestId` when the record carries one
  (a retried response repeats the message id and is billed again), falling back
  to the `uuid` only when there is no `message.id`, and to no identity at all
  (never deduped) when there is neither. The first record of a response wins and
  a later duplicate may only move it *into* the subagent view, which is unchanged
  from R5A3: `isSidechain` or `subagents/`-file provenance still decides
  membership, and `weighted` still contains `subagent_weighted`. Measured over
  the R5a window (`2026-09-26T07:23:17Z .. 2026-09-28T07:23:17Z`, same
  `--repo`/`--branch`/prefixes as R5A3; every response id is unique to one
  session and no response id ever spans two files, so the collapse is entirely
  same-file blocks — 1.96 / 1.94 / 1.76 records per response — and the
  cross-file dedup stays defensive):

  | row | records (before → after) | weighted (before → after) | naive (before → after) | subagent weighted (before → after) | share | merges | weighted-per-merge |
  |---|---|---|---|---|---|---|---|
  | L1-routing | 9,745 → **4,964** | 202,093,981.4 → **104,768,807.8** | 1,748,832,653 → 932,606,194 | 53,589,959.0 → 24,380,131.1 | 26.5 % → **23.3 %** | 28 | 7,217,642.2 → **3,741,743.1** |
  | L1-backlog | 12,060 → **6,205** | 262,700,682.6 → **135,999,291.6** | 2,279,918,775 → 1,201,840,938 | 124,163,141.7 → 62,663,439.9 | 47.3 % → **46.1 %** | 34 | 7,726,490.7 → **3,999,979.2** |
  | L1-main | 7,089 → **4,034** | 190,405,176.5 → **107,119,446.4** | 1,733,132,396 → 989,705,170 | 85,499,036.7 → 45,288,117.6 | 44.9 % → **42.3 %** | 76 | 2,505,331.3 → **1,409,466.4** |

  The denominators are untouched, so every row drops ~43–49 % and R5b must
  compare against these numbers, not R5A3's. L1-backlog's subagent weighted
  tokens grouped by `message.model` (counts only, never message text):
  3,343 responses / 61,138,502.0 weighted = **97.6 %** `claude-sonnet-5` plus
  212 / 1,524,937.9 = 2.4 % `claude-haiku-4-5` — R5A3's reading survives the
  re-key: the share is the Sonnet reviewer legs, not Haiku first passes.
- **first-wins, measured**: 242 of the 4,964 L1-routing responses do *not*
  repeat their usage — the client writes the growing partial usage as blocks
  land, so the last record of a message carries the most. The rule stays
  first-wins (a duplicate never adds usage); keying on the largest copy instead
  would move the L1-routing numerator by 132,582.0 weighted, 0.13 %.
- **`tests/test_autoos_tokenrate.py`**: `ResponseDedupTests` (new, 10 cases) and
  R5A3's `test_the_blocks_of_one_turn_are_not_deduped…` reversed to pin the new
  rule. Covers three records sharing a message id counting once, distinct message
  ids counting separately, the same message id under another request id being
  *two* responses, the uuid fallback, a record with neither id staying its own
  response, first-wins against a growing duplicate, and a subagent copy of a
  parent message counting once and landing in the subagent view. The fixture
  `usage_line` now writes a `requestId` (derived from the message id, as the real
  transcript always agrees; `rid=` sets it explicitly) — 8 red before the change,
  **60 green** after.

### Changed — `token-rate` discovery reaches `<session>/subagents/*.jsonl` (RESTART R5A3, the router's answer to D-045's open caveat, 2026-09-28)

- **`tools/autoos_tokenrate.py`**: D-045 kept an in-session subagent turn in the
  numerator and reported its share, but all three measured rows printed `0.0%`
  because this host's client writes those turns one level below the session
  files `discover_transcripts` scanned. Discovery reads both depths now, so the
  subagent records are in `records`/`weighted`/`naive` *and* in the subagent
  columns: a record is a subagent turn when its own `isSidechain` flag is set
  **or** it was read out of a `subagents/` file — the flag is the record's claim,
  the directory is the client's. A turn written to both depths is still one cost:
  `Totals.add` dedups by transcript `uuid`, falling back to `message.id` for a
  record that carries none, counts it once, and lets the second copy move it
  *into* the subagent view only. Measured over the R5a window
  (`2026-09-26T07:23:17Z .. 2026-09-28T07:23:17Z`): L1-routing 5,462 → 9,745
  records / 148,504,022.4 → 202,093,981.4 weighted / share 26.5 %, L1-backlog
  4,907 → 12,060 / 138,537,540.9 → 262,700,682.6 / 47.3 %, L1-main 2,959 → 7,089
  / 104,906,139.8 → 190,405,176.5 / 44.9 %. The before-numbers still reproduce to
  the token and nothing deduped on this host — parent and subagent files share
  neither uuid nor message id, so the dedup is defensive. That corrects the third
  row of the R5a caveat (417 records / 4.6 %): that probe counted only
  `subagents/` dirs sitting beside a same-named session file in the *same*
  project dir, and an L1-main subagent turn routinely runs in a worktree under
  the main checkout while its parent session is filed under a lane's dir — its
  records belong to the main row by `cwd`, and the real figure is 4,130.
- **`--json`**: an unnamed `--projects-dir` prints `"default"` instead of the
  resolved transcript root. That path carries the operator's username, and the
  repository is public (hard rule 1); an explicit flag is echoed as it was given.
- **`tests/test_autoos_tokenrate.py`**: `SubagentFileDiscoveryTests` (new, 11
  cases) plus 3 CLI cases, on a fixture tree holding a session file and its
  `subagents/` dir — both depths in the numerator, provenance alone counts as a
  subagent turn, a shared uuid (or message id, without a uuid) counts once and
  lands in the subagent view, the blocks of one turn keep their own uuids, an
  agent file invents no session of its own, the window and `cwd` filters still
  apply one level down, and `--json` says `default`. Fixtures now give every
  record its own uuid, as the real transcript does; they had keyed the uuid off
  the token count, which is exactly what the dedup now collapses. 9 red before
  the change, 50 green after.
### Fixed — OpenCode no longer loads a managed skill twice (WS-HARNESS)

- **`lib/agent_harness.py`**: `desired_opencode` drops a managed skill's
  `SKILL.md` entry that still points into an `agent-skills` clone (the skills
  home before `.agents/skills`, retired 2026-09-25) when it writes the
  replacement entry. Before, the merge appended the new entry and kept the old
  one, so OpenCode loaded two diverged copies of the same skill. Only the
  harness's own skills are touched: a user's other `agent-skills` paths, and any
  entry at all when there is no skills source to replace it, are kept.

### Changed — `agent-skills` is a tombstone on Linux and macOS (SPEC-OMNI A7)

A7b retired the component's *work* and left a live row holding a pointer: the
catalog entry still named a `postInstall`, still sat in two profiles, and still
answered "installed" from a hand-written detection branch. A7a shipped the
mechanism that says all three from data, so the last step is to stop saying them
in code. Windows (`catalog/windows.json`, `lib/windows`) is untouched — its entry
still clones, and its retirement is a later lane.

- **`catalog/linux.json`, `catalog/macos.json`**: `agent-skills` is
  `"tombstone": true` with the note *its work moved to agent-skill-links,
  omnigraph-client and the mcp-\* components* and `replaced_by` naming the six ids
  that took it (`agent-skill-links`, `omnigraph-client`, `mcp-graphify`,
  `mcp-serena`, `mcp-playwright`, `mcp-context7` — all present in both catalogs).
  It is dropped from `workstation` and `ai-coding`: a profile pre-selects what
  should get *installed*. `postInstall` goes with the installer, and the long
  `notes` line goes because `note` now carries the same fact — one home per fact.
  The `uv` entry's note named `agent-skills` as its consumer; it names the
  `mcp-*` components, which is what actually runs `uv`.
- **`lib/linux/install.sh`**: `install_agent_skills` deleted (setup.sh reports a
  retired row before it ever asks the provider, so nothing could call it), and the
  `agent-skills` branch of `custom_is_installed` deleted with it
  (`catalog_probe_installed` answers *not installed* for a tombstone before it
  probes, so the branch was unreachable too). Leaving either would have kept a
  second owner for a fact the catalog holds.
- **Behaviour**, on the paths A7's verify row names: a `--profile workstation` /
  `ai-coding` dry run plans no `agent-skills` row at all and still plans the
  successors (they are profile members in their own right); `--only
  agent-skills` announces `agent-skills is retired: replaced by …`, plans the six
  beside the retired row, and the row reports `skipped: retired (its work moved
  to …)`. A state file saved before the retirement replays the same way. The
  retired row is no longer in the "Installed apps" line, no longer prints
  "✓ Already installed", and no longer hunted for a launcher in the landing
  report.
- **Tests** (all five red before the catalogs flipped, for exactly those
  reasons): `tests/linux/13-end-to-end-dry-run-only.sh` drives the real entry
  point over the shipped catalogs — `--only` expands, a profile plans no retired
  id, `--from-state` replays a hand-authored pre-retirement file to its
  successors — and its three scratch-home cases now share one `e2e_setup` helper
  instead of restating the trick. `tests/linux/18-mcp-wiring.sh` asserts the
  catalog shape (the boolean flag, the note, the exact successor set, no profile,
  no `postInstall`/`prompt`/`requires`/`verify`), that the retirement leaves no
  installer or detection branch behind, and — with detection stubbed to "everything
  is here" — that the retired id is never reported installed while its
  successors are. The two A7b cases that drove `install_agent_skills` and
  `custom_is_installed agent-skills` directly are gone: the function they tested
  no longer exists, and what replaces them is reached the way a run reaches it.
- **`docs/catalog.md`**: the field table and the retirement section say what a
  tombstone now *is* in this repo — no profile, no installer, the note and the
  successors as the only prose — with the shipped `agent-skills` entry as the
  `replaced_by` example. `docs/plans/2026-09-27-omnigraph-mcp-catalog-plan.md`
  marks A7 done on Linux and macOS.

Verified: `bash tests/run-tests.sh --filter='agent-skills,tombstone,skill,catalog,end-to-end,from-state'`
121 passed / 0 failed; `bash setup.sh --check-catalog` exits 0; shellcheck clean
on the touched `.sh`; `python3 -m pytest -q tests/` green.

### Fixed — a retired row is locked in both terminal menus, and every selection path expands it (A7a review 2, 2026-09-28)

Muse's re-check found the `replaced_by` expansion sitting behind the wrong
condition. It ran only for a selection named by ids (`--from-state`, `--only`, and
the browser payload that arrives as `--only`), while **neither terminal menu locked
a retired row**: `setup.sh` set `MENU_DISABLED` for the `manual` provider only, and
`New-AutoOSMenuItem` set `Locked` for the `manual` provider only. So a person who
highlighted a retired row and pressed space got exactly the failure the previous
commit was meant to close — a plan holding the `skipped: retired` row and none of
the work that replaced it — and the row's `(retired)` label said nothing about
where the work had gone.

Both halves, because either one alone leaves a hole:

- **The row**: `catalog_menu_rows` (new in `lib/linux/catalog.sh`, the twin of
  `New-AutoOSMenuItem`, which the menu now calls instead of building its own arrays)
  locks a retired row and labels it `(retired: replaced by <ids>)`, naming only the
  successors this machine offers, the way the announcement line does. On Windows
  `New-AutoOSMenuItem` gained `-OfferedIds` and the same lock and label, plus a
  `Reason`. `ui_menu`'s and `Show-AutoOSMenu`'s non-interactive fallback read only
  the tick, so a locked row could still leave the selector with nobody at a
  keyboard; both now honour the lock, the same rule the key handlers apply.
- **The selection**: `setup.sh` and `setup.ps1` expand on the way to the plan,
  unconditionally, so no path — menu, profile, replay, `--only`, browser, or one
  added later — can plan a tombstone without its replacements.
- **Guard order** (the review's LOW): resolve asked whether AutoOS could install
  the *provider* before it asked whether the row had *retired*, so a retired
  `manual` component failed the whole run with "use its vendor link". Retirement is
  now asked first, the row resolves and reports `skipped: retired`, and the plan no
  longer prints its stale homepage as an "Action required" step.

Tests: the Linux suite (`tests/linux/39-catalog-tombstone.sh`) covers the row flags
and label, the real `ui_menu` key handlers, the locked row against the
non-interactive fallback, the retired-`manual` resolve, and — over a `script(1)`
pty, skipped where none exists — the reviewer's own scenario through the real
`setup.sh`: highlight a retired row, press space, and see that it never reaches the
plan. The PowerShell suite (`tests/run-tests.ps1 -Filter tombstone`) covers the same
row through `New-AutoOSMenuItem`, the same lock through `Show-AutoOSMenu`, the same
guard order in `Resolve-AutoOSPlan`, and `-Only` of a retired `manual` id through
`setup.ps1`. `docs/catalog.md` said a hand-chosen retired id "is still planned"; it
no longer is, and the document says so where it describes the row.

### Fixed — a tombstone names what replaced it, so a replayed state keeps the work (A7a)

Sonnet's final review of the A7b lane found the retirement mechanism keeping the
id and losing the job. A state file saved **before** a component was retired names
the retired id and cannot name the ids that inherited its work, and `--from-state`
took the selection verbatim: the tombstone was known, skipped, and nothing else was
planned. The machine came back without that work and the run reported `skipped`,
which reads like success.

- **`replaced_by`** (catalog field, tombstone only): the ids that took the retired
  component's work on. Both validators reject one that is empty, that sits on a live
  entry, or that names an unknown id or another tombstone — a successor that installs
  nothing would replay into a second `skipped` row, the same defect one step later.
- **`lib/linux/catalog.sh`** (`catalog_expand_replacements`) and
  **`lib/windows/AutoOS.Catalog.psm1`** (`Expand-AutoOSTombstoneReplacements`):
  expand a retired id into its successors, once per id, and are asked by every
  selection built from explicit ids — `--from-state` / `-FromState`, `--only` /
  `-Only`, and a browser run, whose payload the server passes in as `--only`. The
  retired row stays (it is still what reports `skipped: retired`), a successor the
  selection already lists is not added twice, a successor this machine does not
  offer is left out of both the plan and the announcement, and a successor retired
  since is expanded in turn. Profiles never name a tombstone, so they never expand.
- **`setup.sh` / `setup.ps1`**: one muted line per expanded tombstone — `agent-skills
  is retired: replaced by agent-skill-links, omnigraph-client` — before the plan, so
  nothing is substituted silently.
- **`lib/linux/serve.py`, `lib/windows/AutoOS.Serve.psm1`, `web/index.html`**: the
  payload carries `replaced_by` and the retired row shows it, the way it shows the
  note. The page displays the successors; the installer is the one that plans them.
- **`docs/catalog.md`**: the field and the replay rules.
- Tests: the Linux suite (`tests/linux/39-catalog-tombstone.sh`), the PowerShell
  suite (`tests/run-tests.ps1 -Filter tombstone`) and the page checks
  (`tests/test-web-progress.js`) cover the validator, the loader column, the
  expansion (dedupe, unoffered successor, chain), a hand-written pre-retirement
  state file replayed to a plan that installs the successors, and `-Only` /
  `--only` of a retired id.

### Fixed — tombstones reach the browser, and a requirement on a retired id fails out loud (A7a review, 2026-09-28)

Muse's review of the A7a commits found the retirement mechanism stopping at the
terminal: the served payload never carried the flag, so the page — which resolves
dependencies, pre-ticks profiles and collects prompts **in the browser** — did the
opposite of `setup.sh` with the same catalog. And the rule "no entry may require a
retired id" lived only in the validators, which a normal run never calls.

- **`lib/linux/serve.py`**: `build_state`'s inline projection became a module-level
  pure `state_components(catalog, platform, platforms, arch, headless, installed)`
  that adds `tombstone` and `note`, so a test can ask the server's own function
  without a server; `installed names` is derived from the same rows now instead of
  being collected beside them.
- **`lib/windows/AutoOS.Serve.psm1`** (`Get-AutoOSServeState`): the same two fields,
  read from the projection's `Tombstone` / `RetireNote`.
- **`web/index.html`**: retirement is one answer in one place — `canInstall()` —
  which the profile pre-tick, the dependency closure, the questions card, the
  quick-install button and the tick box all already ask. A retired row is **shown,
  disabled and labelled**, not hidden: hiding it removes the only place a reader
  learns the id went away and what replaced it, which is the reason the catalog
  still carries it. `retiredChip` and the `(retired)` description match the terminal
  wording, `installedChip` never says `✓ Installed` for something AutoOS no longer
  installs, and the Configure button for a prompt the page will never ask is gone.
- **`lib/linux/catalog.sh` + `setup.sh`**, **`lib/windows/AutoOS.Catalog.psm1` +
  `setup.ps1`**: resolve now records a refusal (`PLAN_BLOCKED` /
  `catalog_resolve_blocked` on Linux, `BlockedReason` on Windows) for an entry whose
  `requires` names a tombstone, and spreads it to that entry's own dependents until
  the set stops growing. The plan warns, the questions loop asks nothing, execution
  records the component as failed, and the run exits 1 — the retired row stays in
  the plan so the report can point at it. Nothing is installed without a dependency
  it asked for.
- **`setup.ps1`** plan tag: `(dependency)` and `(retired)` are two independent tags,
  as in `setup.sh`; the `elseif` hid the retirement the moment a tombstone arrived
  as a dependency rather than a hand-pick.
- Tests (`tests/linux/39-catalog-tombstone.sh` +8, `tests/run-tests.ps1` +7,
  `tests/test-web-progress.js` +1 block): the serve projection through
  `state_components` itself, refusal and cascade at resolve, the ordinary resolve
  recording nothing, `setup.sh`/`setup.ps1` end-to-end over the fixture tree
  (non-zero exit, `Failed 1`, no prompt asked, both tags on one row), and the
  shipped page functions run in node — `canInstall`, `closure`,
  `profileClosureDirect` and `itemHtml` — so a page that merely stops drawing the
  row cannot pass.
- Docs: `docs/catalog.md`'s retirement section states the resolve-time refusal and
  the browser's shown/disabled/labelled choice.

### Added — a retired component keeps its id and installs nothing (A7a, 2026-09-28)

An `id` is a contract (AGENTS.md §3): saved state files, `--only` / `-Only` flags
and a user's last selection all name it. Deleting a component that stops being
installable turned all of those into "Unknown component id" and a red run, and
there was no way to say "this used to be a thing, here is what replaced it". The
catalog can now mark one instead: `"tombstone": true` with an optional `"note"`.
**Mechanism only — no real component is retired by this change**; deciding which
ids become tombstones is a separate, catalog-only edit.

- **`lib/linux/catalog.sh`**: the field is read in one place — `catalog_is_tombstone`
  — which the profile expansion, the dependency walk, the loader and the installed
  probe all ask rather than each re-deriving it. `catalog_load` carries
  `CAT_TOMBSTONE` / `CAT_RETIRE_NOTE` beside the other columns;
  `catalog_profile_defaults` never pre-ticks a retired id; `catalog_resolve` keeps
  the id in the plan but drags nothing in behind it; `catalog_detect_installed`
  reports a tombstone as not installed. `catalog_validate` accepts the field (a
  tombstone may omit `postInstall`, `prompt`, `requires` and `verify` — they only
  mean something for something that installs) and rejects `"tombstone"` that is
  not the boolean `true`, an empty `note`, a `note` on a live entry, and any entry
  that `requires` a tombstone.
- **`lib/linux/install.sh`** (`catalog_probe_installed`): skipped for a retired id,
  so the `✓` and the "Installed apps" line cannot answer for a product AutoOS no
  longer offers — the same reason the cache in Detect is gated on Windows.
- **`setup.sh`**: `--list` and the menu mark the row `(retired)`, the plan row is
  tagged, the questions loop asks a tombstone nothing, and execution prints
  `skipped: retired (<note>)` and counts it as already present — never installed,
  never failed, identical on the second run. A retired component is not in the
  post-run "where to find them" hunt, because for it "no launcher found yet" would
  be a wrong answer rather than a blank one.
- **`lib/windows/AutoOS.Catalog.psm1`** — the twin: `Test-AutoOSTombstone` (reads
  the raw JSON field and the flattened projection's, and tests the type because
  PowerShell would coerce `'1'` to `$true`), `Format-AutoOSTombstoneSkip` (one home
  for the skip wording, so `setup.ps1` and the suite cannot drift),
  `Test-AutoOSProfileDefault` + `Get-AutoOSProfileDefaults` (the profile rule the
  menu row and the `-Yes` expansion shared by copy-paste now share as code), plus
  the same four validator rules, the `Tombstone`/`RetireNote` projection fields,
  the menu's `(retired)` label, and the dependency walk that expands nothing behind
  a retired id.
- **`lib/windows/AutoOS.Detect.psm1`** (`Get-AutoOSInstalledStatus`): returns
  `not-detected` for a tombstone before the cache and before any probe — one gate
  that `Set-AutoOSInstalledStatus`, `Test-AutoOSInstalled`,
  `Get-AutoOSInstalledComponents`, `-Installed`, `-ListComponents` and the
  installer's own skip check all read through.
- **`setup.ps1`**: `-ListComponents` marks `(retired)`, the plan row is tagged, the
  prompt collection and the `-Yes` expansion skip retired ids, execution prints the
  skip line into `results.skipped`, and the "Where to find them" report excludes them.
- Tests (**`tests/fixtures/catalog-tombstone.json`** — a fixture, so no real catalog
  was touched — `tests/linux/39-catalog-tombstone.sh` 18 cases, 21 cases in
  `tests/run-tests.ps1`'s `catalog tombstone` group): validator accepts and rejects,
  projection keeps the facts, profile expansion and menu tick leave it alone, a
  retired id is never detected as installed *while an ordinary id with the same
  package still is*, the plan keeps the known id and pulls no dependency, the skip
  line is pinned with and without a note, and the Linux side runs `setup.sh` itself
  against a scratch tree — `--only`, a state file written by a real run replayed
  with `--from-state`, `--list`, the summary counts and a second run.
- Docs: **`docs/catalog.md`** gained the two Fields rows and a
  *Retiring a component (tombstone)* section — the rules above, and why `note` and
  `notes` are different fields.
### Added — the MEMSPEC P1 memory facade: five methods, one file-backed graph, `schema: 2` events (MEMSPEC P1, 2026-09-30)

- **`tools/memory_facade_mcp.py`** — the typed facade MCP server (D-037): the
  five §4 methods `recall`/`remember`/`link`/`supersede`/`context_pack`
  (spec:62-68), the write rules on EVERY write (hub link, closed relation
  list, ≤600-char body, secret scan — all validation before any write), and
  the receipts spec:77-80 requires: entity id, the NEW `version_id`, author
  session + restart generation id (D-042), source. Every successful write
  appends the §5 `schema: 2` event envelope agreed with L2-general (fleet
  console spec §4.3): ULID, `memory.<kind>.<created|updated|merged|
  superseded|redirected>` plus `memory.edge.linked`, `prev_version_id` only
  on updated|superseded, append-only, never a body or a secret; refused
  writes and no-ops append nothing. Single design per the L1-backlog
  arbitration: links are `{"rel","id"}` pairs (bare ids refused by name),
  `version_id` is an int, env is one `AUTOOS_MEMORY_*` family, and store +
  events are file-backed with defaults under the git-ignored `logs/` tree.
- **`tools/memory_facade_engine.py`** — the swappable engine seam (D-067,
  spec:255-260): file-backed JSON store (R0 fixture seed + canonical saved
  shape, atomic temp-rename), int versions with a readable history
  (spec:79), the alias ladder with order-insensitive normalisation so rung 2
  fires (spec:66), recall ranked by text + hub distance inside the 600-token
  budget (spec:84), `context_pack` with hub facts, open contradictions and
  recent events inside 1.5k (spec:88-91), and the spec:73 closed relation
  list as the minimal spec-grounded set `about`/`part_of`/`supersedes` (the
  spec mandates a list and enumerates none — verified against spec:73/116,
  the fleet console §4.3 and PLAN §14; `related_to` is not on it).
- **`tests/test_memory_facade_mcp.py`** (33 tests), **`tests/test_memory_events.py`**
  (13), the R0 fixture `tests/fixtures/memory/` (7 nodes, deliberate hub
  distances) and both `tests/linux/33-documentation.sh` wiring blocks — red
  first (24 + 13 failing at 1324ad8), green after 4c06a9c. The events suite
  pins the scripted sequence created → updated → merged+redirected → edge
  linked → superseded + `supersedes` edge, the envelope, and byte-prefix
  append-only growth. P1 exit (spec:217) met: the five methods round-trip
  and the events validate against §4.3.

### Fixed — `policy.leg_rules` match case-insensitively, so no DeepSeek Pro spelling escapes the deny (DSAMEND2, 2026-09-28)

- **`tools/registry.py:leg_rule_for()`** (Muse review 1 of DSAMEND, MEDIUM): the
  matcher used `fnmatch.fnmatchcase`, which binds a rule to the one casing it was
  written in. `deny-deepseek-pro` (`*deepseek*pro*`) therefore did not match
  `samba/DeepSeek-V4-Pro` — the same model, spelled with capitals — and matched
  nothing else either, so the leg fell through to the *no-match = allowed*
  default and the catch-all `deny-deepseek` never saw it. `openrouter/deepseek/
  DeepSeek-V4-PRO` was only ever caught by the unrelated `deny-openrouter`.
  Pattern and leg are now both casefolded before the match, first-match-wins
  order unchanged. One matcher, so every consumer moved with it (R-orch-11):
  `_check_leg_rules` (rule 9), the gateway renders via `gateway_legs`,
  `autoos_resolver.usable_legs` and `probe_common._skip_reason` all import
  `leg_denied`/`leg_rule_for` from here — none re-implements the comparison, and
  `audit-router.py` / `sync-*.py` do not match legs at all. `resolve_leg` stays
  case-sensitive: it is the providers catalog's own contract, not this matcher's.
- **No verdict moves for any leg the registry names.** Swept over every route
  leg, every `unavailable_legs` key and every provider spelling × model id
  (1421 legs, 30 of them real): the rule that fires and its `allow` are identical
  before and after, for the one mixed-case leg `samba/MiniMax-M3` included — the
  fold only closes spellings that previously matched *nothing*. Kept as
  `LegRulesTests.test_no_committed_verdict_changes_when_matching_folds_case`.
- **`tests/test_autoos_resolver.py`** (review 2, LOW):
  `test_only_deepseek_v41_flash_survives_of_the_deepseek_family` asserted
  `"deepseek/deepseek-v4-flash" not in models` — a *leg* string tested against a
  map keyed by model id, so that limb could never fail. It now asserts what
  DSAMEND actually left behind: the bare `deepseek-v4-flash` row still exists for
  its reseller leg and carries **no** native `direct` block.
- Tests: `LegRulesTests.test_matching_is_case_insensitive` (was red: the three
  Pro spellings above came back allowed); `validate` and all five
  `render --check` surfaces unchanged.

### Fixed — the native DeepSeek id is `deepseek-flash` only, and V4 Pro is denied by name (DSAMEND, 2026-09-28)

- **`catalog/ai-registry.json`**: routing-00 measured `GET /models` on
  `api.deepseek.com` at 10:0xZ — the live catalog is exactly
  `['deepseek-flash', 'deepseek-v4-pro']` — so the `direct` row that used to
  hang off `models.'deepseek-v4-flash'` (`model: deepseek/deepseek-v4-flash`,
  `base_url: https://api.deepseek.com`) was sending an **alias**, not a catalog
  model: it answers 200 but `served=deepseek-flash`, while the v4.1 spelling is a
  flat 400. The native `direct` block now belongs to `models.'deepseek-flash'`,
  the only entry that names a model the vendor actually serves, and
  `models.'deepseek-v4-flash'` keeps its reseller leg
  (`cheaperinference/deepseek-v4-flash`) with no native row. The other
  providers' spellings — `openrouter/deepseek/deepseek-v4.1-flash`,
  opencode-zen's bare `deepseek-v4.1-flash` — are their ids, not ours, and were
  left alone.
- **`policy.leg_rules`**: a new **first** rule `deny-deepseek-pro`
  (`match: *deepseek*pro*`, `allow: false`) makes the operator's standing "never
  route or fall back to V4 Pro" a structural gate instead of a side effect of
  rule order. Before this, `deepseek/deepseek-v4-pro` was denied only by the
  catch-all `deny-deepseek`, which sits *after* `allow-deepseek-native-flash` —
  one future allow rule (a wildcard, a BYOK exception) would have opened the
  paid model. First in the list, no DeepSeek allow can reach it: it denies the
  native, `openrouter/`, `cheaperinference/` and `opencode-zen/` spellings alike
  (`source`: operator via routing-00 2026-09-28T10:0xZ; Server 719cee9).
- **Consumers of that row** moved with it (R-orch-11: an id change is grepped
  through every reader): `openhands/profiles/deepseek-v4-flash.json` →
  `deepseek-flash.json` with the native model's real window (131072/32768,
  `reasoning_effort: high`), `lib/linux/install.sh` and
  `lib/windows/AutoOS.Install.psm1` (`_profile_for('deepseek-flash', …)`),
  `openhands/agent-profiles/worker.json` and `catalog/agent-harness.json`
  (`leaf-reviewer`: opencode `deepseek/deepseek-flash`, profile ref
  `deepseek-flash`), and the two fixtures that pin those projections
  (`tests/fixtures/legacy-models.golden.json`,
  `tests/fixtures/agent-harness/opencode.expected.json`).
- **Effort ladder, measured rather than assumed** (19 charged one-word calls
  plus one rejected value, ~0.0005 USD total): the wire parameter for `models.'deepseek-flash'.effort_ladder` is
  `reasoning_effort`, and a bad value names the accepted set — `none, minimal,
  low, medium, high, xhigh, ultra, max` — so all four declared rungs are real
  and the ladder needed no re-mapping. What the measurement *did* overturn is
  the assumption behind expressing rung `none` as an omission: with no parameter
  the model **reasons** (20 of 22 completion tokens), and it is
  `reasoning_effort: "none"` (or `thinking: {"type": "disabled"}`) that turns
  thinking off. Recorded on the leg, pinned by
  `tests/test_registry.py::DeepSeekNativeEffortLadderTests`, and left as an open
  item for the emitter (see below).
- **`tests/`**: `DeepSeekNativeIdAndProDenialTests` (13 cases) — no render or
  config sends a non-canonical id to the native API (registry `direct` rows,
  route legs, the vendored profiles, and every render and committed config
  scanned as text), `deny-deepseek-pro` exists, precedes every DeepSeek allow,
  is the rule that answers for each pro spelling, and no pro id appears in any
  render, `router_settings.fallbacks` stays empty and DeepSeek-free, and the
  registry still passes `check` with the new rule. Written failing first: 8 of
  the 13 red before the change, green after, no other case moved.


### Added — the usage report prices the operator's DeepSeek cap (DSGUARD, 2026-09-28)

- **`tools/autoos_usage.py`** (spend guard): a `paid_spend` section, asked for
  by `--spend-since [DATE]` (bare flag: the 1st of the current month UTC) or
  `--balance-usd N` — `autoos-agent.py usage --since 1h --spend-since
  --balance-usd 19.99`. It bills the watched paid provider's rows at the
  registry's per-token `price_in`/`price_out`
  (`catalog/ai-registry.json:234-235`: 3e-07 / 1.2e-06, i.e. $0.30 in / $1.20
  out per 1M — the data was already there, nothing was added to the registry)
  times the factor of `providers.deepseek.windows`
  (`catalog/ai-registry.json:1857-1955`, source
  `https://api-docs.deepseek.com/quick_start/pricing`) **at each row's own
  timestamp**, through `autoos_resolver.price_factor` — the same function the
  router uses to pick a cheap hour, so the guard and the router cannot
  disagree about what an hour costs. `WARN` at spend >= 20 USD (the operator's
  monthly cap) or a caller-measured balance below 5 USD, in the text and in
  `warnings`. The spend window reaches the row fetch back past `--since` (the
  month so far versus the last hour of traffic); when the walk stops at the
  page cap the block says `incomplete`, so a partial window reads as a floor
  and not as a total. The threshold comparison uses the rounded, reported
  figure: a cap missed by 1e-15 of float drift is a cap the operator believed
  was held. Still opt-in — `usage --json` without either flag keeps the shape
  it has always had, and no deeper paging happens for nothing.
  `load_prices` became `price_source_name` + `prices_from_registry` +
  `read_registry` so the price table and the windows come from one parse.
- **`docs/routing.md`**: the two flags and what they warn about.
- **`tests/`**: `SpendTests` in `tests/test_autoos_usage.py` (21 cases, injected
  rows only — the gateway is never contacted, and no test reads a real key):
  the factor math below/at/above 20 USD, the off-peak half price versus the
  peak hour, an uncovered hour billed at full price, another provider's rows
  excluded, an unpriced model counted as a gap rather than as free, the default
  month-start window, a spend window deeper than `--since`, the paging cap
  making the figure a floor, the balance floor at and above 5 USD, both warnings
  at once, bad `--spend-since`/`--balance-usd` exiting 2 before any fetch, the
  key never echoed, and two pins on the shipped registry (the per-token price
  and the `price_factor` window set).
- Lesson: the unit is the whole bug in a spend guard — the registry is USD
  *per token*, so the off-peak factor is what a naive per-1M reading would
  silently double or halve, and only a test that names the peak hour and its
  complement catches it.
- Open: only DeepSeek is guarded (the provider is a constant, `SPEND_PROVIDER`);
  `price_cache_read` is not billed; the balance must be measured by the caller —
  the gateway's own `GET /user/balance` is not read here.
### Fixed — DeepSeek answers again, and the resolver's effort rung finally reaches the client (DSBACK, 2026-09-28)

- **`catalog/ai-registry.json`**: `providers.deepseek.available` flips
  `false` → `true` — operator top-up 2026-09-28T07:4xZ, the router's
  `GET /user/balance` measured `is_available=true` at 19.99 USD, reversing the
  402 Insufficient Balance of 2026-09-27T16:4xZ. Nothing else moved: every
  deepseek model keeps its `effort_ladder` (`deepseek-v4-flash` keeps the empty
  one and `reasoning: false`), the per-leg `opencode-zen/deepseek-v4.1-flash`
  gate stays `available: false` (measured 402/429 by
  `tools/probe-toolcalls.py`), and `providers.openrouter` stays off (DSMAX).
- Renders regenerated by the repo's own commands and re-checked at rc 0
  (`render <target> --check`, `tools/sync-router-tiers.py`,
  `tools/sync-ide-models.py`): `configuration/omniroute/combos.json` (the
  `deepseek-v4.1-flash` combo returns, `deepseek/deepseek-flash` heads
  `t2-worker-clean`/`t3-driver-clean` and joins `t2-worker`/`t3-driver`, and it
  leaves `omitted`), `configuration/litellm/config.yaml` (managed block back,
  deployments re-added to those four routes), `catalog/ide-models.json`,
  `configuration/openhands/tier-profiles.json`, `docs/models.md` and
  `opencode.jsonc` (the `#low`/`#high`/`#max` `variants` return with the leg).
  `fallbacks: []` stays empty — re-adding a chain is an operator call, not a
  dead-leg verdict.
- **`tools/autoos-agent.py`**: **`apply_effort_rung()`** / `declared_variants()`
  (new) and one call in `_resolve_route_v2`. The rung the resolver scored used
  to reach only the track record (`track_entry`), so `opencode run --model
  omniroute/deepseek-v4.1-flash` was the same argv at `low` and at `max` and the
  generated `variants` were dead weight. It is now stamped as the opencode model
  variant that carries it (`…#high` → `settings.reasoningEffort` →
  `reasoning_effort=high`); `none`/None emit no suffix — no reasoning param at
  all — a rung the model declares no variant for is dropped rather than
  invented, and an explicit `--model x#low` keeps the operator's rung. Gateway
  clients are unchanged on purpose: an OmniRoute combo has no per-effort alias
  (pinned in `tests/test_registry_render.py`), so their argv stays the bare
  combo and the rung stays record-only.
- **`tools/autoos_resolver.py`**: `_score_candidates` honours spec 4's
  `override.effort` — normalize_v2 parsed and validated the pin, then nothing
  read it, so a card pinning `effort=max` ran the bucket's rung. The pin
  replaces it, clamped to the answering leg's ladder like any other wanted rung,
  so a pin cannot invent a rung and cannot make a non-reasoning leg reason.
- Tests: **`DeepSeekBackTests`** (`tests/test_registry.py`, new, 10 cases — the
  flip, the source note, every ladder unchanged, the zen leg still gated,
  `t2-worker-clean` heading with the native leg, the models-doc row, validate
  clean), **`EffortRungPlumbingTests`** (`tests/test_autoos_spawner.py`, new, 7
  cases — one per rung, none/None, the clamp, the explicit-variant precedence,
  the v1 path carrying no hard-coded rung, the gateway argv staying bare) and
  four pinned-effort cases in `PlanTests`
  (`tests/test_autoos_resolver.py`). Stale "deepseek is unavailable" pins were
  re-derived from the render, not loosened:
  `tests/test_registry_render.py`, `tests/test_sync_ide_models.py`,
  `tests/linux/33-documentation.sh`, `tests/linux/34-ai-services.sh` and
  `tests/run-tests.ps1` (whose `apply prune` orphan example moved to
  `t1-orchestrator-clean`); `test_autoos_spawner.py`'s provider-attribution case
  now derives the provider from the registry instead of hard-coding a name.
  1209 passed, 1 skipped, 150 subtests on the six suites (baseline before the
  work: 1078 passed on four of them).
- Docs: `docs/models.md` — the `deepseek-v4.1-flash` bullet, the three-doors
  table, the mermaid direct legs and the funded-provider row now say what
  answers; the effort section documents that the suffix is applied by the
  spawner, not only chosen by a caller, and corrects the render's derivation
  (the served head leg, not `legs[0]`).
### Changed — four orchestration rules: L0 never executes, the ready window, the worktree-copy trap, corpus-accepted detectors (FOLD5, 2026-09-28)

- **`.agents/skills/unattended-orchestration/SKILL.md`**: 31 rules → 33. New `R-router-03` — *Route, decide, ask, verify; never run project work, cleanups or setup - hand them to the L1 coordinator* (operator 2026-09-28T10:4xZ via routing-00: a router that picks up a shovel stops routing). The Levels table's **L0** Job cell and the levels paragraph after the table name that target too — **L1 coordinator**, not "main orchestrator", which `references/main-orchestrator.md` reserves for a different level. Replaced `R-coord-01` to pin the **ready order** — merge main before spawn and CI, *not* between green CI and `ready` (L1-backlog 2026-09-28T08:52:57Z: a lane was marked `ready` on a tip CI had never tested; `mutex` and `freeze parent` survive from the old line, `2c3e4f7`). Replaced `R-worker-08`: verify on a detached copy — `git clone --no-hardlinks` **or** `git worktree add --detach` — while `cp -r` of a worktree is forbidden outright, because the copy shares the original's index: a reviewer's `cp -r` plus `git checkout` mutated it (L1-backlog/ci7, Sonnet final 08:53:30Z). New `R-worker-10`: a detector or redactor is accepted against the **real output corpus**, and every new consumer of raw data gets its own redaction test (REDACTFIX3 07:24:13Z, SPAWNFIX3d 08:18:21Z: fixtures stayed green while seven real reports and one secret went out).
- **Every rule line is under the 200-character gate `tools/skill-rules.py check` enforces** — `R-router-03` 198, `R-coord-01` 199, `R-worker-08` 190 — so each text carries the shortest wording that still states the lesson, and each `source:` pointer keeps only the form that resolves (lane, file or sha). `check` reports `ok: 33 rules`.
- **Muse's review of FOLD5 (FOLD5b)**: `R-worker-10` moved to the end of the worker block so the ids read in numeric order; the "`tar` only if no test reads git" clause came back into `R-worker-08` (dropping it discarded the 37-fixture lesson, which `R-tests-14` still records); the L0 hand-off target is spelled **L1 coordinator** in the rule, the Levels row and the paragraph; `R-coord-01` went back to "before spawn and CI, not between green CI and `ready`" once that wording measured 199. Two FOLD4 rows in **`references/rule-map.md`** that restate these rules were updated to the new meaning — `R-coord-01` (the ready window, `2c3e4f7`) and `R-worker-08` (`worktree add --detach`, the cp-shares-index trap, `ci7`, tar/37-fixture history kept). No id was retired or renumbered.

### Fixed — Muse's slow first byte: a 180 s response-start ceiling, and the combo probe streams (MUSETIME, 2026-09-28)

- **`configuration/docker/ai-stack/compose.yml`**: the gateway gives up on a provider call whose response has not *started* within `OMNIROUTE_DIRECT_HEADERS_TIMEOUT_MS`, and unset resolves to 30000 (`resolveDirectHeadersTimeoutMs` reads the env and returns 3e4 when absent — `autoos-omniroute:/app/.build/next/server/chunks/_0117s88._.js` @900, the env name @965; `directFetchWithBoundedResponseStart` is exported at @758 and its `code: "DIRECT_RESPONSE_START_TIMEOUT"` const at @47, carrying the text "Direct response did not start within 30000ms"). Muse spark-1.3's first byte runs 3-32 s at minimal/low/medium and 4-58 s straight to the provider (routing-00 09:2xZ, image `autoos/omniroute:3.8.50-autoos2`), so the `high` leg 504'd. That 504 is also what opened the breaker for the *next* call: `shouldTripProviderBreakerForResult` requires the status to be in `new Set([408,500,502,503,504])` (all three in one module: the set `_0gq2i23._.js` @54054, `isProviderBreakerFailureStatus` @54261, `shouldTripProviderBreakerForResult` @54549), so two slow legs at the apikey `failureThreshold=2` `apply.sh` pins produced the measured `503 ALL_TARGETS_SKIPPED`. compose now pins 180000, operator-overridable, documented as a commented default in `stack.env.example`. **An `ai-stack` recreate of the omniroute service is required for the env to take effect.**
- **No per-provider breaker threshold exists, so `apply.sh`'s resilience block is unchanged**: `providerBreaker` validates as `z.object({oauth:…,apikey:…}).strict()` (`src_shared_validation_1g05z7_._.js` @55692) — only the two auth-kind profiles, and `.strict()` rejects a provider id as a key. The `circuitBreakerThreshold` that looks per-connection is a field of those profiles (`OMNIROUTE_CIRCUIT_BREAKER_{API_KEY,OAUTH,LOCAL}_THRESHOLD`, defaults 12/8/2, `[root-of-the-server]__0qa-6gn._.js` @3307/@4015/@4742) and the settings→profile resolver feeds it back as `providerBreaker.apikey.failureThreshold` (`src_lib_resilience_settings_ts_1cmviqa._.js` @8531). Breaker *instances* are keyed by provider name (`getCircuitBreaker(name, opts)` accepts a `failureThreshold` in `opts`, `[root-of-the-server]__01edmvm._.js`), but every call site in the server bundle passes the name only — the four `getCircuitBreaker(…)` calls are all `getCircuitBreaker(provider)` — so nothing operator-settable reaches one. Slowing the 504 down is therefore the whole fix; the global 2 stays (it is there for the dead free promo, not for Muse).
- **`configuration/omniroute/apply.sh`**: the `--probe` request is `"stream": true`, and `first_model(resp)` returns the model of the first chunk that names one (an SSE `data:` line, or a single JSON document if a gateway ignores `stream`) instead of blocking on `resp.read()`. Clients stream; the probe measured the one path nobody uses, and waited out the whole generation for a one-word answer. Proven against a local stand-in gateway: the SSE leg, a non-stream leg and a 503 leg all report correctly and the server sees `stream=True` on every request. The spawner (`tools/autoos-agent.py`) sends no chat request at all — its only HTTP call is `GET /api/health` (:660) — so worker and reviewer legs are the CLI clients' own request shape, which is not ours to set.
- **`tests/linux/34-ai-services.sh` + `tests/linux/33-documentation.sh`**: the compose case (retitled so `--filter omniroute` reaches it) pins the new `OMNIROUTE_DIRECT_HEADERS_TIMEOUT_MS: ${…:-180000}` line inside the omniroute service block, a new case pins the commented default in `stack.env.example`, and a new case pins the probe's streaming body, its SSE read and the absence of the whole-body read. Red before the fix: `--filter omniroute` 24 passed / **3 failed** (the two compose/env cases and the probe case). Green after: **27 / 0**; `--filter aistack,compose` 129 / 0, `--filter apply,combos,resilience,registry` 63 / 0, `--filter static,shellcheck` 16 / 0; shellcheck clean at `-S warning` on `configuration/omniroute/apply.sh`, all four of its python heredoc blocks compile.

### Added — `token-rate`: orchestrator tokens per merged change (RESTART R5a, 2026-09-28)

- **`tools/autoos_tokenrate.py`** (new, stdlib, read-only): the §5 metric the
  before/after cap comparison needs. Its numerator iterates **every** usage
  record of the orchestrator's Claude Code transcripts and weights them
  `input + output + cache_creation + 0.1 * cache_read` (`CACHE_READ_WEIGHT`);
  the naive unweighted sum is printed beside it as a labelled diagnostic.
  `tools/autoos_context.py` `fill_from_transcript` is deliberately not reused —
  it keeps only the last usage record, which is a *snapshot*, and a rate needs
  the sum over all of them. The denominator is first-parent merges into `main`
  in the same window (by committer date) whose subject names the orchestrator's
  branch prefix, so numerator and denominator are the same actor; sessions are
  selected by the record's `cwd` on whole path segments, which keeps
  `/home/s/code/AutoOS` and `/home/s/code/AutoOS-lanes/...` apart. The output
  states the known bias: it rewards shorter sessions and prices the
  orchestrator only.
- **`tools/autoos-agent.py`**: a `token-rate` verb forwards to it, the way
  `usage` forwards to `autoos_usage` — the metric needs no gateway.
- **`tests/test_autoos_tokenrate.py`** (new, 27 cases, wired into
  `tests/linux/33-documentation.sh`): fixtures reproduce the real transcript
  record key-for-key (including `usage.iterations`, which must *not* be summed
  twice) against a throwaway projects dir and a temp git repo. Covers the four
  summed fields, the all-records-vs-last-record regression guard, the
  half-open window, cwd scoping, merge attribution by prefix, and the
  zero-merge window printing `n/a` instead of dividing. 27 red before the tool
  existed, green after.

### Changed — `token-rate` reports the in-session subagent share (RESTART R5a follow-up, router D-045, 2026-09-28)

- **`tools/autoos_tokenrate.py`**: an `isSidechain` record — a turn of an
  in-session subagent the orchestrator spawned — is orchestrator cost, so D-045
  keeps it in the numerator and reports how big that part is instead of
  filtering it out. Four new labelled lines in the text report and four keys in
  `--json` (`subagent_records`, `subagent_weighted`, `subagent_naive`,
  `subagent_share_pct`, the share over the *weighted* numerator, `n/a`/None when
  the numerator is empty). The split is a view onto the same records:
  `weighted` still includes `subagent_weighted`, so the R5a before-numbers are
  unchanged — re-measured over the same 48 h window
  (`2026-09-26T07:23:17Z .. 2026-09-28T07:23:17Z`), L1-routing 5,462 records /
  148,504,022.4 weighted / 28 merges, L1-backlog 4,907 / 138,537,540.9 / 34,
  L1-main 2,959 / 104,906,139.8 / 76 — every row reproduced the R5a report to
  the token. The measured caveat the operator has to decide: this client writes
  sidechain usage records to `<project>/<session>/subagents/*.jsonl`, one level
  below what `discover_transcripts` scans, so all three rows print `0.0%` while
  those files hold 4,283 / 7,153 / 417 in-window records (26.5 % / 47.3 % /
  4.6 % of their numerator *if* discovery reached them). Widening discovery is a
  before-number change and therefore an operator call, not a metric-reporting
  one.
- **`tests/test_autoos_tokenrate.py`**: `SidechainTests` (new, 6 cases) plus
  three report/CLI assertions, on fixtures that mix `isSidechain` true and false
  records in one transcript — the flag parses, the sidechain turn is *not*
  dropped from the numerator, the split is exact, the share is over weighted,
  and an empty numerator prints `n/a` rather than dividing. 10 red before the
  change (9 new cases + the `--json` key test), 36 green after.
### Fixed — the REST temp files are removed on every exit path, and the gateway's reason survives to the log (MUSEFIX, 2026-09-28)

- **`configuration/omniroute/apply.sh`**: `omni_rest` writes the manage key into a `mktemp` curl `--config` file and the response into a second `mktemp` — a `GET /api/providers` answer is every provider's live `apiKey` — and both were removed only by the statement *after* the call. A run interrupted while a call was in flight therefore left a 0600 file holding the key in `/tmp` on the user's machine, which is what an onboarding run a user gets impatient with looks like. The removal is now trapped for the duration of the call (`trap 'rm -f -- "$cfg" "$out"' INT TERM EXIT`, cleared immediately after — the script sets no trap of its own, so clearing restores "no trap") in the shape `ai-stack.sh` uses for its edge-webhook header file. Measured against the interrupt case, with two temp files live mid-call: before the trap, a `SIGTERM` to the run's process group — the shape of Ctrl-C — killed it at rc 143 with **both files on disk**; with the trap, nothing survives, in either signal shape (a `SIGTERM` aimed at only the script's pid is cleaned up too, by the call's own shell finishing behind it). Outside a call the script still has no trap, so Ctrl-C keeps the default disposition there, exactly as in `ai-stack.sh`.
  The same change also had to undo the callers' `$( )`: `provider_node_id`, `provider_connection_exists` and `ensure_provider_node` read their helpers by command substitution, so `omni_rest` ran in a *subshell* — the key file belonged to a process the script itself was not, and a command substitution one level up silently swallowed `REST_ERROR` as well. The helpers now hand back through globals (`REST_BODY`, `NODE_ID`, `NODE_NOTE`) and are called in the caller's own shell. That is what the reserved-prefix case tests: with the trap alone it still failed, because a refused create printed only the fallback `the gateway refused the request` instead of what the gateway said; it now prints `HTTP 400 for /api/provider-nodes: … is a reserved provider prefix` through `print_cli_error`, with the key redacted. Comments and the changelog entry below claimed `createProviderNodeSchema` *demands* `baseUrl`: it does not — the schema requires it only of the `vibeproxy-openai` preset. apply sends it anyway (a node without the endpoint the registry names routes nowhere), and the wording now says so.
- **`tests/linux/34-ai-services.sh`**: three cases on the existing stand-in CLI + stand-in curl. The interrupt case points `TMPDIR` at a directory only it owns, holds the stand-in's first `provider-nodes` answer for two seconds, asserts the temp files *exist* mid-call (so the case cannot pass vacuously), sends `SIGTERM` to the run's whole process group — monitor mode around the launch, because signalling only the script's pid lets the call finish and clean up behind a dead parent, which proves nothing — and asserts the directory is empty once the window closes. The REST-error case has the stand-in answer the POST with the reserved-prefix refusal *and the bearer token it read out of apply's own curl config file* — the worst case the redaction has to survive, a 4xx body that repeats the request's credential — and pins that the reason is printed, both keys are absent from the output and from every recorded argv, and nothing was stored. The third case seeds the state a dashboard leaves behind (node present, no connection bound) and requires the key to be bound to *that* node with no second node POSTed. Red before: `--filter apply` 46 passed / **2 failed** (the interrupt case, and the reserved-prefix case failing on the swallowed reason). Green after: **48 / 0**; shellcheck clean at `-S style` on the touched `.sh`.

### Fixed — a non-built-in OpenAI-compatible provider becomes a gateway provider node; the CLI's reason is no longer discarded (MUSEREG, 2026-09-28)

- **`configuration/omniroute/apply.sh`**: a live run printed `! meta-api registration failed - register it in the dashboard` and threw the CLI's stderr away (`>/dev/null 2>&1`), so the reason never reached the log (routing-00 04:5xZ). The failure path now prints the CLI's own words through `print_cli_error` — ANSI stripped, three lines at 200 chars, every secret replaced by `[REDACTED]` by `redact_secrets`, whose *quoted* bash pattern substitution matters: a key containing `*` or `?` is stripped literally instead of being read as a glob. The reason itself: `catalog/ai-registry.json` gives `meta_api` the `omniroute_id` `meta-api` and an `api_base` of its own, and `meta-api` is not one of the built-ins `omniroute providers available` knows (352 ids and aliases, measured against omniroute 3.8.51) — so `providers add meta-api` had nothing to attach a credential to. Such a provider now reaches the gateway as a **provider node**: `builtin_provider` reads the CLI's catalog once per run and caches it, `ensure_provider_node` GETs `/api/provider-nodes` first and POSTs what `createProviderNodeSchema` asks for — name and prefix, an `apiType` for `type: "openai-compatible"`, and a `baseUrl` that the schema requires only of the `vibeproxy-openai` preset but apply always sends, because a node without the endpoint the registry names routes nowhere (omniroute `src/shared/validation/schemas/provider.ts:307-385`; the CLI's own `post-api-provider-nodes` sends *no* body at all, `bin/cli/api-commands/provider-nodes.mjs:18-25`, so this has to be the REST call) — then binds the key to the node the gateway reports back. The created node is read from the list rather than trusted from the POST response, and a prefix that collides with a built-in is the gateway's own refusal (`reservedProviderPrefixes.ts`), which is why an id is only ever a node when the catalog says it is not a built-in. The manage key travels in a private `mktemp` curl `--config` file (0600) and the body on stdin — never argv, where `ps` reads it for the lifetime of the call. Idempotency is measured against the gateway, not against `providers list`: a node-bound connection's provider is the node's `<type>-<uuid>` id (`src/lib/db/providers/nodes.ts:61-71`), which the existing hex-id scan can never match, so `provider_connection_exists` reads `GET /api/providers` (`{"connections":[…],"total":N}`, measured live 2026-09-28) and the second run prints `= meta-api already registered` — reading only the id and the name, never the `apiKey` every row carries. `--dry-run` prints both plan lines and calls nothing.
- **`tests/linux/34-ai-services.sh`**: five bash cases on a fake `omniroute` + fake `curl` in a sandbox directory (`_node_sandbox`) that reproduce the measured contract — the CLI's real `providers list` column shape, its `.env` banner before the JSON, the gateway's `{"nodes":[…]}` and `{"connections":[…]}` bodies: the redacted stderr (a key with glob characters, asserted absent from the output *and* from every recorded argv), node-create-then-connection-add for a non-built-in, a second run reporting `already registered` with exactly one node row, `--dry-run` creating and adding nothing, and an unreadable catalog treated as all-built-in (never a node created on a guess). Built-ins keep the byte-identical call they made before. The pwsh twin runs the real `apply.ps1` in a child shell against a loopback stand-in gateway (`Start-AutoOSNodeGateway`, a `TcpListener` job) and pins the same contract in text for Windows, where the stand-in CLI needs `sh`. Red before: `--filter apply` 40 passed / **5 failed**, `-Filter 'keeps the provider-node path'` 0 / **1 failed** (`no provider-node request`). Green after: 45 / 0 and the wider `--filter 'svc:,apply,combo,routing,registry'` 130 / 0 on bash; `-Filter apply.ps1` 53 / 0 on pwsh. shellcheck clean at `-S style` on both touched `.sh`; PSScriptAnalyzer reports nothing new on `apply.ps1` beyond the categories the suite already excludes.
### Fixed - parsed `uv` output is read colour-free (GFX, 2026-09-28)

- **`lib/linux/install.sh`**: new **`uv_plain()`** (`NO_COLOR=1 uv --color never "$@"`),
  and the two uv calls whose *output* is parsed now go through it -
  `graphify_mcp_link_prepare`'s `uv tool dir` and `graphify_installed_version`'s
  `uv tool list`. uv colourises stdout on `FORCE_COLOR`/`CLICOLOR_FORCE` with no tty
  at all, and measured on uv 0.12.18 that wraps the answers in escapes
  (`\e[36m/home/u/.local/share/uv/tools\e[39m`, `\e[1mgraphifyy v0.9.63\e[0m`). The
  link classifier prefix-matches the path, so on such a host uv's OWN shim at
  `~/.local/bin/graphify-mcp` was classified `blocked` ("yours to move"), the graphify
  step returned 1, and every `setup.sh --profile ai-coding|workstation --dry-run`
  exited 1 once the pinned tool had been installed once - measured here:
  `FORCE_COLOR=3 bash setup.sh --only mcp-graphify --dry-run` rc 1 before, rc 0 and the
  plan byte-identical to the plain run after. The version read failed the same quiet
  way: nothing parsed, so the pin "installed" on every run. `uv tool install` is
  deliberately *not* routed through the helper - its output is for the user, and only
  its exit code is read.
- Tests (`tests/linux/18-mcp-wiring.sh`): the five per-driver uv stubs collapse into
  one **`gfy_uv()`** that mirrors the measured contract - argv recording, the tools
  dir, the `<dist> v<ver>` line, the writes `tool install` performs, and colourisation
  on uv's own precedence (`--color never` beats `NO_COLOR` beats `FORCE_COLOR` beats a
  tty), so a caller reproduces a coloured host with `FORCE_COLOR=3 <driver> …`. Four
  new cases: uv's own link is still `uv` when `tool dir` is coloured, the user's own
  link is still `blocked` then (colour must not become a rubber stamp), the pinned
  tool stays `skipped` with rc 0 and no reinstall, and the version parser reads the
  catalog pin out of a coloured `tool list`; plus a grep guard that no `$(uv …)` /
  `< <(uv …)` in `lib/linux` bypasses the helper. 5 red on a scratch copy with only
  the two callsites reverted, green here: 41 passed / 0 failed with and without
  `FORCE_COLOR=3`, 46 passed / 0 failed for `--filter='dry run'` both ways,
  `shellcheck -S warning` clean.

### Changed — the user-scope `homelab` MCP entry is never removed, only reported (NOHL, 2026-09-28)

Operator decision 2026-09-28 06:2xZ (via L0): the homelab MCP switch is **not** to
be done now — the server-side homelab MCP is not finished, so no step may take the
user-scope `homelab` entry away. It is the entry the homelab server will be
configured through, not a leftover to clean up.

- **`lib/linux/install.sh`**: `remove_stale_homelab_mcp_entry` is gone and
  `report_stale_homelab_mcp_entry` replaces it. The whole removal path went with
  it — the `backup_file` call, `claude mcp remove homelab --scope user`, the
  post-removal re-check, the `has_cmd claude` precondition and the dry-run
  branch (a check that writes nothing has no separate dry-run behaviour to
  announce, so dry and live now print the same line). What is left: classify the
  config with `homelab_user_entry` (kept — it is what the report is about), and
  when it says `agent-skills`, print one muted line naming the retired tree and
  saying the entry is left in place. No backup, no `claude` call, no file write,
  no recorded failure. `none` and `other` say nothing at all: a line on every run
  about an absent entry, or about a server the user wrote themselves, is noise —
  and the old `other` branch ended in `claude mcp remove homelab --scope user`,
  which is now an order AutoOS must not give. The `$SYS_HOME` guard stays: a
  config outside the home this run configures belongs to another session (setup
  under sudo reads root's), and reporting on it would describe a file the run was
  never pointed at.
- **`install_omnigraph_client`**: calls the report where it called the removal,
  still before the URL/token/npm gates and still exactly once; the case that
  guards against an orphan or a second caller now pins the new name.
- Tests (`tests/linux/18-mcp-wiring.sh`): the removal cases are replaced by
  report cases. A recognised stale entry leaves the config byte-identical with no
  backup file, no `claude` invocation and no `backup_file` attempt (both stubs
  record, so the absence is proven, not assumed) and prints the line; a second run
  prints the same line again, because nothing changed in between — the old shape
  converged to *skipped* precisely because the first run had mutated the machine;
  dry and live are identical; an unrecognised entry, an absent entry and a
  project-scope entry each get no line and an untouched file; a config outside this
  run's home is not reported on. One case greps `lib/` for `mcp remove homelab` and
  for the old helper name, so the removal cannot come back under a new caller.
- Docs: `docs/catalog.md`, the A4b row and spec §C/D14 of
  `docs/plans/2026-09-27-omnigraph-mcp-catalog-{plan,spec}.md` say the removal is
  deferred by operator decision rather than done. `homelab_user_entry` still
  recognises the shape, and `docs/omnigraph.md` never described this duty, so
  neither needed a change.

### Changed — the `agent-skills` step is a tombstone; its twelve duties have homes (A7b, Linux/macOS, 2026-09-28)

- **`catalog/linux.json`, `catalog/macos.json`**: new `agent-skill-links`
  component (`provider: custom`, `postInstall: install_agent_skill_links`,
  profiles `workstation` + `ai-coding`). It carries no `requires` — linking a
  directory of markdown needs neither git nor nodejs, and naming them would drag
  both into a skills-only run — and no `prompt`, which was never its question.
  `agent-skills` keeps its id (a saved selection, a state file and
  `--only agent-skills` all still resolve) and loses the `omnigraph_url` prompt
  and the `git`/`nodejs` requires it no longer spends.
- **`lib/linux/install.sh`** (`install_agent_skill_links`,
  `agent_skill_link_dests`, `agent_skill_links_current`): the skills-linking duty
  moved here and now goes through `link_skill_dirs` for every destination,
  replacing the step's own hand-rolled loop. Two behaviour differences, both
  intended: only a directory holding a `SKILL.md` is a skill (the old loop linked
  any child directory), and a dangling link this checkout created is *repaired*
  where the old loop left it — a moved checkout used to mean silently no skills
  in Antigravity and Claude Code. Nothing is ever *copied* any more: the loop's
  `ln -snf … || cp -r …` fallback would have written a second, unversioned copy of
  the skills into the user's home if `ln` failed, and no test ever reached it.
  `agent_skill_link_dests` is the one home for the destination list, so the
  writer and the detection gate cannot disagree about what "done" covers, and
  `~/.codex/skills` is created only where codex is installed or its home exists.
  `~/.openhands/skills` stays owned by `setup_openhands_config`.
- **`lib/linux/install.sh`** (`install_omnigraph_client`): took the wiring duties
  that named this checkout's servers — approve the `.mcp.json` project servers
  (`omnigraph`, `autoos-agent`), warn about a shadowing user-scope `omnigraph`
  instead of ever writing one, write Antigravity's `omnigraph` entry (its config
  has no project scope) and report — never remove — the retired tree's user-scope
  `homelab` entry (that removal was deferred, see the NOHL entry below).
  The first, second and fourth run **before** the URL/token/npm gates, because a
  machine that never answered the prompt still wants a clean, working Claude Code;
  the entry that *carries* the URL and token stays behind them, which is A3's
  "nothing configured, write nothing" rule. Its refusal to touch a file the run
  could not back up is recorded under the id being installed, so the summary
  cannot read "done" over a change that never happened.
- **Removed from the step that used to do all of this**: the four
  `install_mcp_*` calls (each is a catalog postInstall with its own profile and
  `requires`; a second caller double-ran them and counted one broken wiring
  twice), the retired-clone hint (the clone is a detection fallback in
  `autoos_skills_source`, not something to advertise) and `omnigraph_readiness`
  together with its caller — the check that can name a missing token, a rejected
  token or a missing graph is `tools/check-omnigraph.py`, which the healthchecks
  already call; an installer guessing at Docker state across a machine it cannot
  see was the weaker copy of that. The "restart Claude Code and Antigravity" line
  moved with the work it describes. `docs/catalog.md` follows in the same change.
- **Detection**: `custom_is_installed` gains `agent-skill-links` (delegating to
  `agent_skill_links_current`, the same function the postInstall asks) and the
  retired `agent-skills` id now reports done whatever is on disk, because there
  is never work left under it. A destination that is itself a symlink — the old
  whole-directory layout, which AGENTS.md section 8 says is left with one warning
  — counts as settled: it is a directory of the user's, `link_skill_dirs` will not
  write through it, and holding it against the machine would re-plan the component
  every run and then report it *installed* having done nothing.
- Tests (`tests/linux/18-mcp-wiring.sh`, `13-end-to-end-dry-run-only.sh`,
  `14-state-verify-and-undo.sh`, `38-omnigraph-client.sh`): the linking cases
  moved to the new component and gained second-run, keep-yours, dry-run,
  moved-checkout and one-destination-list assertions; the omnigraph-client block
  asserts the approvals, the warning-only rule, the homelab report and the
  blank-URL machine that still gets the repo-scope duties and writes no bridge
  config; one case fails the suite if any installer calls an `mcp-*` postInstall
  from another component; and the Antigravity merge is now proven on a real
  `mcp_config.json` that already holds the user's own server.
### Fixed - the raw provider-stop line stops being recorded (REDACTFIX3, 2026-09-28)

- **`tools/autoos-agent.py`**: `record_reset_stop` stored the stop line it is
  handed as the `reason` of a `logs/routing/provider-state.json` row. Since
  REDACTFIX item 2 that line is the child's **raw** text — deliberately, because
  redaction can mask the very marker the classification reads — so a worker that
  echoed an injected key on its stop line wrote that key, verbatim, into a file
  that outlives the run. The classification (`parse_reset`, `stop_provider_id`)
  still reads the raw line; the stored dict is redacted, like every other copy
  that leaves the process. The four other sinks of the same string (the
  `PROVIDER-STOP` and `HEADLESS-REFUSAL` prints, the WIP commit message, the
  fall-through line) already redacted and are unchanged.
- **`tests/test_autoos_spawner.py`**:
  `ProviderResetStateTests.test_a_stop_line_carrying_a_secret_records_a_redacted_reason`
  (fails first: the key was in the stored JSON) and `REDACT_SAMPLES` now builds
  its three `sk-`/`sk-or-`/`sk-ant-` fakes by concatenation, like the `ghp_`/
  `gho_`/`github_pat_` samples already did, so the shared-pattern table holds no
  contiguous token-shaped literal.

### Fixed - one home for the secret patterns survives the merge to main (REDACTMERGE, 2026-09-28)

- **`tools/autoos_redact.py`**: `origin/main` hardened hostexec's stored-argv
  redaction while this lane moved the same pattern set out of `audit.py`, so the
  two sides met in conflict. The resolution keeps one home: main's
  `_argv_text` (a non-str argv element — MCP JSON can carry a number/bool/null,
  and a *refused* call is still recorded, so redaction may never raise on one —
  renders through `json.dumps`, falling back to `repr`) moved into the shared
  module and is what `redact_argv` runs per element, so the spawner's argv view
  gets the same guarantee instead of only the audit's. The module's docstring
  no longer claims the digest is over the raw argv: main's item 5 is correct and
  kept — `audit.hash_argv` hashes the **redacted** form, because a digest of a
  raw secret is offline-guessable.
- **`tools/hostexec/audit.py`**: keeps only what is audit-specific — the `"***"`
  mask its own tests pin, and `argv_sha256` — and imports the carriers
  (`sanitize_text`, `redact_argv`) from the shared module. Nothing main tightened
  was dropped: the widened `*KEY*`/`*TOKEN*` name rule, `Bearer` separate and
  inside one token, `x-api-key:`, `user:pass@`, mysql `-pSECRET` / `-uUSER:PASS`,
  the `--token`/`--password`/… flag list, LF/CR stripping, the redacted-form hash
  and the non-str rendering all reach the audit path, and the audit path
  additionally gains this lane's wider prefix set — `github_pat_`/`AIza`,
  case-insensitive (REDACTFIX item 3), which is a widening and never a
  narrowing.
- **`tools/autoos-agent.py`**: both sides kept. The provider-stop classification
  stays on the **raw** tail (`raw_tail`, falling back to the redacted tail) — a
  stop line whose secret was masked must still be recognised (SPAWNREDACT item 2)
  — and main's REVROUTE reset-window recording (`record_reset_stop`) now runs on
  that same classification. The LEAK line's payload goes through `redact_output`
  (worker text) and keeps main's LEAKFP2 procedural note.
- Tests: no test on `origin/main` was changed. `tests/test_autoos_spawner.py`
  gains **`SharedRedactPatternTests.test_non_string_argv_elements_render_from_the_shared_module`**
  and **`test_audit_holds_no_pattern_set_of_its_own`** (the one-home guard:
  `audit.redact_argv is autoos_redact.redact_argv`, plus a source scan that no
  pattern constant reappears in `audit.py`); both were mutation-checked against a
  scratch copy that redefined `redact_argv` in `audit.py` — they fail there and
  pass here. No test that exists on `origin/main` was edited to make this merge
  pass; the only hostexec-test edits are the lane's own pre-merge fixture splits
  (`"gh" "p_abc123"` is the same string as `"ghp_abc123"`, written so GitHub push
  protection sees no literal). Verified on a fresh clone of the merged lane
  (`set -o pipefail`, each job under `systemd-run --user --scope -p
  MemoryMax=2G`): the targeted set (`tests/test_autoos_spawner.py tests/ -k
  'hostexec or redact or audit'`) 197 passed / 3 skipped / 4402 subtests,
  `tests/test_autoos_spawner.py` 557 passed + 73 subtests, and the whole python
  suite `2060 passed, 4 skipped, 4596 subtests passed`, exit 0.

### Fixed — the leak check stays strict; only another worktree's own branch move is exempt (LEAKFP2, 2026-09-28)


- **`tools/autoos-agent.py`**: 75f2866 required three signals before blaming a commit on the worker — a write visible in this worktree's HEAD reflog, the worker's own identity, and a committer timestamp inside the run window — and then exempted anything that looked like another lane's work (made on a ref created during the run, or contained in a new or sibling-worktree ref). Sonnet's review of that commit demonstrated each as an *evasion of a real leak* against live repositories: a decoy `git branch` laid on the worker's own tip, a backdated `GIT_COMMITTER_DATE`, a `git switch -c` + commit + fast-forward back. The window and both exemptions are gone and the 75f2866~1 detection is back — HEAD first-parent range, the checked-out branch's own reflog for a commit-then-reset, every ref that existed at the snapshot and moved, author OR committer = the worker, plus the new-dirt porcelain leg — and one narrow exemption is kept, the measured cause of false positive B: a ref that is the checked-out branch of ANOTHER worktree of the same repository at *both* the snapshot and the check, and is neither this worktree nor this run's sandbox (`_lane_worktree_moved`). False positive A — the orchestrator fast-forwarding this parent onto another lane while the child runs — is deliberately not exempted in code, because nothing distinguishes it from a worker write; the exit-7 report now says so on its own line (`if you moved this branch yourself during the run (merge/ff), this is expected - do not move a parent while its child runs (skill R-coord-01)`). Consequence, and intended: a pre-run lane commit brought in mid-run and a moved ref checked out in no worktree (another writer's *clone*) report LEAK 7 where 75f2866 stayed silent.
- **`tests/test_autoos_spawner.py`**: **`LeakStrictnessTests`** (new, 8 cases) drives the real `parent_snapshot`/`parent_leak` against real temp repositories across the exemption boundary — a sibling worktree's own branch moving is exempt; a worktree added mid-run is not; a worktree inside the sandbox is not; a commit on a branch created during the run is a leak either way HEAD then goes. `IsolateContainmentTests` gains four fake-worker modes for the evasions (decoy branch, backdated committer date, `switch -c` + ff back, commit on a new branch) and the three flipped expectations above. 13 red before the fix, 417 green after on the file; `python3 -m pytest -q tests/` 1831 passed, 4 skipped.
### Changed - `omnigraph-client` joins the Linux `server` profile (Q-001, 2026-09-28)

- **`catalog/linux.json`**: the operator lifted the Q-001 hold at 04:50Z, so a
  headless server pre-ticks the Omnigraph bridge like every other profile. The
  component still skips with a hint when the `omnigraph_url` answer or the
  `omnigraph_token` key is missing, so a server with no graph configured gains a
  `skipped` line and nothing else — no failure, no file written. `catalog/macos.json`
  is untouched: the macOS catalog has no `server` profile.
- Tests (`tests/linux/38-omnigraph-client.sh`): the catalog case asserted the
  opposite ("the server profile is on hold") and now asserts the exact profile
  list per catalog, so the Linux/macOS difference is stated rather than implied;
  one new end-to-end case runs `--profile server --dry-run --yes` with no URL
  configured and requires the plan to carry the component, the skip-with-hint line
  to appear, and exit 0.
- Docs: `docs/omnigraph.md` names the four Linux profiles and says why macOS has
  three; the open question in `docs/plans/2026-09-27-omnigraph-mcp-catalog-spec.md`
  is marked resolved with its reasoning.

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
### Fixed — the redactor's matching cost, the classification tail, the prefix scope (REDACTFIX, 2026-09-27)

S2 fix round on SPAWNREDACT (findings S1/S2/S3 of
`logs/handoff-sessions/20260925/work/L1-routing/review-spawnredact.md`).

- **`tools/autoos_redact.py`** (S1, HIGH — matching cost): the
  `key|token|secret… = value` carrier is no longer one regex. Its two unbounded
  quantifiers overlapped the keyword literals, so a line of 200 KB of
  `keytokensecretpasswordcredential` with no `:`/`=` backtracked for **104 s** —
  in `run_client`'s output pump, one call per line of a stream whose line length
  *the worker* chooses, so the run hung rather than finished. One charset scan
  now finds the separators, the name run in front of each is walked back over
  characters that cannot themselves be a separator (the walks of one line never
  overlap, so they sum to its length) and Python decides keyword membership:
  the same line costs **3.7 ms**, a 1 MB line streams in ~20 ms.
  `ASSIGNMENT_SCAN_CAP` (4 MiB) bounds what one line can cost at all; above it
  only that line's assignment carriers are skipped — the bearer/URL/prefix/PEM
  and injected-literal patterns still run over the whole line.
- **`tools/autoos-agent.py`** (S2, MEDIUM — classification was blind):
  `run_client` keeps the last `TAIL_LIMIT` bytes *twice*. `ClientExit.tail`
  stays redacted (the caller's stream, the record, the commit message); the new
  `ClientExit.raw_tail` holds the child's own text and is read by nothing but
  the headless-refusal and provider-stop checks — never printed, never recorded,
  never committed. Redaction can mask the very marker a check looks for
  (`error: retry_key = 429` is a stop line and a secret carrier at once), and
  while the docstring and the entry below promised classification on raw text,
  the code passed the redacted tail. The announced `PROVIDER-STOP` line and the
  WIP commit message redact it as before.
- **prefix scope** (S3/H1, LOW — behaviour change, previously unpinned):
  `_SECRET_PREFIX_RE`, shared with hostexec's stored argv since SPAWNREDACT,
  masks an argv token that *starts* with a known prefix, **case-insensitively**,
  and the set is `sk-`/`ghp_`/`gho_`/`github_pat_`/`aiza`/`xox`/`glpat-` — so a
  `gcloud AIzaSy…` line is now masked whole where hostexec used to store it
  verbatim. Pinned by
  `test_hostexec_log.py::RedactionTests::test_the_shared_prefix_set_masks_any_case_and_starts_a_token`.
  Its deliberate asymmetry with the text stream (`AIza` stays case-exact there —
  Google's own keys always are, review H1) is pinned by
  `test_autoos_spawner.py::SharedRedactPatternTests::test_the_text_prefix_rule_stays_case_exact_on_aiza`.

### Fixed — the spawner redacts its worker's output (SPAWNREDACT, 2026-09-27)

- **`tools/autoos_redact.py`** (new): one home for the secret patterns. hostexec's
  set (bearer, `api_key:`/`KEY=value` carriers, `user:pass@` URLs, key prefixes,
  the control-character strip) plus what a worker's report actually contains —
  vendor prefixes (`sk-`, `sk-or-`, `sk-ant-`, `ghp_`/`gho_`/`github_pat_`,
  `AIza`, `xox[bp]-`, `glpat-`), `key|token|secret|password = value`
  assignments, PEM private-key blocks (masked across streamed lines), and the
  exact values of secret-named variables the spawner injects into the child env
  (`AUTOOS_OMNIROUTE_KEY` and friends, ≥ 12 chars). `Redactor` is line-oriented
  so a stream can be masked as it arrives; `redact_argv` keeps hostexec's `***`.
- **`tools/hostexec/audit.py`**: deletes its private copy of the patterns and
  aliases `sanitize_text`/`redact_argv` to the shared module — stored-argv
  behaviour and its tests unchanged, now with `github_pat_`/`AIza`/`sk-or-`
  prefixes recognised too.
- **`tools/autoos-agent.py`**: every stream the spawner writes of a worker's
  output goes through it — the live pass-through and the captured tail in
  `run_client` (a line is classified for refusal while still the child's own
  text, so redaction can never blind the check), the `HEADLESS-REFUSAL`,
  `PROVIDER-STOP` and fall-through lines, the sandbox `changed`/`commits`/`LEAK`
  summary, the worker registry record, every track entry (`record_run`, the one
  choke point) and the WIP commit message. Prints
  `autoos-agent: redacted N secret(s) from worker output` once when N > 0, so a
  masked report does not read as the worker's own words.
- **`tests/test_autoos_spawner.py`**: 14 tests — each pattern class masked in
  text and in argv, PEM across lines, ordinary output untouched, injected env
  values by exact match, and through `run_client` that the caller's stream and
  tail carry none of the samples while `provider_stop` still reads
  "Rate limit exceeded" next to a redacted line and the refusal still exits 6.

Evidence: lesson inbox 2026-09-27T18:58:28Z — a worker's REPORT printed a secret
it had found, verbatim, despite its brief naming the file it came from.
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
