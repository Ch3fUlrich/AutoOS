# PACKET fixes-L2-runnerpool gen4 (AO-WORKST-RUNNER-POOL), 2026-10-15

Lane lane/ao-workst-runner-pool, local, NOT pushed (D-1349: no GH runner; push after maingreen merge). origin/main b33e0fb39 merged (bfcc55898).

## Commits
- d6848f916 W2 pool core/gate/proc/slot/CLI | a80a6fb63 W1 admission by kind | 38cca458a W4 api client + e2e
- 7f0347db8 4 spawner reds fixed (api in matrix, flag-drift reads allowed api model)
- edbcfe449 D-1349 host admission floor 4608/3072 -> 1024 MB (failing-first test)
- 57937b11b Deps.risk_of default fail-safe R2 (R0 only for tool-less api)
- e23f81e1c handoff gen4 + follow-up item for workst-L1-autoos

## Local checks
- Full spawner suite (before floor commit) 1803 tests: 1 flake (InProcessEquivalence `list`, agy probe), passes alone. HostAdmissionTests 40 ok after floor.
- runner_pool* / api_client / e2e files individually ok; `run-tests.sh --filter documentation` passed 2/0.
- Known red NOT ours: tests/test_suite_wiring (origin/main lacks wiring of test_benchmark_toolcalls.py, test_sync_openhands_hdi.py) until maingreen.
- Unfiltered run-tests.sh is refused locally (R-host-08, CI sets AUTOOS_FULL_SUITE=1): CI run id pending push.

## Writer families (code under review) vs seats
| Role | Model | Family | Evidence |
|---|---|---|---|
| writer | deepseek-v4.1-flash (best), ovh/gpt-oss-120b, qoder/glm-5.2 | deepseek / openai-oss / zhipu | prior runs |
| orchestrator edits | Claude Sonnet 5.5 | anthropic | this session |
| seat A (admission change a80a6fb63+edbcfe449, attacker brief admission/memory guard) | ovh/Qwen3.5-397B-A17B | qwen | run 20261015-202128-runnerpool-seata-3dae70, corr 115d6535-22e3-4015-a46d-d5f4dd05453f, review.txt sha256 36f66b154b9596da64e0860fd5930af15adf08c6e857bdb28f7c137759b9e2a2, prompt sha256 b7dfade96113e3b772ce3b77ec7a49233643266ebbc686af1c7e7ae6c6c9d4ef |
| seat B (runner_pool_gate.py incl. authorized_keys line, runner_pool_proc.py kill/spawn) | ovh/Meta-Llama-3_3-70B-Instruct | meta | run 20261015-203117-runnerpool-seatb-e2a95d, corr 704cc183-9000-473f-803e-55a2bdcc273c, review.txt sha256 7ccc90d82daaf8f13a5522d6cc11a09c5dd62722f89c09144abe6607c2477242, prompt sha256 20c796ba7972e1670e034227f292a1e525afe2bb51cd0d351856e1179521c53e |
Raw outputs: docs/plans/evidence/2026-10-15-runnerpool-seat{A-qwen,B-llama}-{review.txt,evidence.json}. Both tool-less (review-call.py via gateway key exec). Failed routes tried first: free-ai (credits exhausted), groq (7k ITPM), hf (402), ainative (model_cooldown 429 repeatedly), dva (agentic bridge), cfp (browser bridge), mimo-v2.6-flash (reasoning exhausts 8000 tokens, 24000 times out).

## Seat verdicts (both fail-with-findings) and my triage
Seat A (qwen, substantive):
1. cap/floor unbounded from registry: needs repo write to exploit; registry is not a trust boundary. Accepted/low.
2. 1024 MB floor on ~4 GB host: floor is MemAvailable required at launch, not per-worker reservation (seat's workers*floor math is wrong), but 1 GB leaves little headroom for 4 toolless workers. Operator decision D-1349; flagged, not changed.
3. nt GlobalMemoryStatusEx failure -> fail-closed refusal: intended (a80a6fb63 comment); transient refusal is requeue, not loss. Accepted.
TEST-GAMING yes quote = unchanged context line `if os.environ.get(ADMISSION_OFF_ENV) == "1":` (pre-existing test-only escape, not in the diff's added lines). No gaming added.
Seat B (llama 70B, shallow): 1,2 message-wording nits; 3 gate_env None check (low); 4 filetime-mismatch no-kill is the pid-reuse guard by design; 5/6 handle leaks: checked, false (_nt_filetime closes in finally; job handle is caller-owned). No real exploit found. Weak seat: recommend a stronger second pass by workst-L1-autoos before live step; mimo too slow here.

## Not done / follow-up (workst-L1-autoos, see HANDOFF)
host:workstation dispatch, nt Job Object wiring, live scoped spawn + Scheduled Tasks + authorized_keys evidence + >3-parallel measurement (all CUT here by fixes-L1, D-1297).
Questions: api model allowlist is a constant in tools/api_client.py (registry schema additionalProperties=false); argv brief limited <30000 chars by job_argv.
