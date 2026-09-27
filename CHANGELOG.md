# Changelog

All notable changes to AutoOS are recorded here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed — probe-toolcalls uses the shared leg selection (ptc)

- **`tools/probe-toolcalls.py`**: deleted its local `_skip_reason`/`legs_to_probe` copies and imports both from **`tools/probe_common.py`**, so a `policy.leg_rules`-denied leg is skipped with `policy: denied by <id>` and only free legs are probed (D18); its own `make_post`/400 body classification is unchanged. Tests: new `DenyRuleTests` in `tests/test_probe_toolcalls.py`; existing leg fixtures now declare `tier: free` (no skip-reason assertion needed rewording — the shared wording matches).

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
