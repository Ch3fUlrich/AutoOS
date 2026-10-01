# DONE — lane `clamp-probe` (ws-clamp-probe)

**Worktree:** `AutoOS-ws-clampprobe` (branch `L1-backlog/ws-clamp-probe-20260930`, base `origin/main` @ `e58274a8`)
**Date:** 2026-10-01
**Handoff:** `docs/handoff/2026-10-01-laneClampProbe.md`
**Tool:** `tools/probe-clamp.py` (new; commit `5b9b7dd7`)

## What was asked

The post-restart checklist has three items — clamp replay, vertex probe, deepseek
probe. Probes exist for vertex (`tools/probe-vertex.py`) and deepseek
(`tools/probe-reasoning-repro.py`), but there was **no clamp probe**. Write one,
verify it against the live gateway, commit it with a handoff doc + this note, and
get one nonce-gated review from a different family (`omniroute/t3-driver-clean`).

## What was done

`tools/probe-clamp.py` — direct-gateway probe, plain model names, key from
`AUTOOS_OMNIROUTE_KEY` or `api-keys.yml` in memory (never printed):

1. lists the live catalog and discovers a `scaleway/qwen3-235b-a22b-instruct-2507`
   leg (prefers `scaleway/`, then `scw/`);
2. sends `max_tokens=20000` (above the 16384 cap) and asserts the gateway clamps
   rather than returning the unclamped `max_completion_tokens is limited to 16384`
   400;
3. sends the same shape to a non-clamped contrast model when available;
4. prints a single `VERDICT: PASS|FAIL|SKIP` line.

Verdicts/exit codes: PASS and SKIP exit 0; **FAIL exits 1** (only on the real
unclamped cap 400); argparse keeps exit 2 for usage errors. Unavailability
(401/402/403/429/connection/no leg) is SKIP with a verbatim reason. A 429 /
`chat_admission_busy` / `Rate limit exceeded` is logged with a UTC timestamp and
retried with a 60–120 s-band backoff. Candidate keys are tried in order and the
first the gateway accepts is used.

## Evidence (verbatim)

Live run (env key), `--package-dir` corroboration:

```
clamp leg: scaleway/qwen3-235b-a22b-instruct-2507 (max_tokens=20000, cap=16384)
clamp-leg status: 200
unclamped cap 400 seen: False
contrast leg: openrouter/openai/gpt-4o-mini status: 200
compiled-table evidence: clamp rule present in 6/6 chunks
VERDICT: PASS - over-cap qwen3-235b-a22b-instruct-2507 request answered 200; no max_completion_tokens cap 400 (clamped, not rejected)
```

`api-keys.yml` fallback (env cleared): `key source: api-keys.yml:omniroute` →
same PASS. (Finding: this gateway 401s on the file's `omniroute_server` field and
accepts `omniroute`; the probe tries both, redacted, values never printed.)

Extra clamp evidence: the same leg answered **200** to `max_tokens=1000000`, and
all six live compiled chunks carry `clampToModelMaxOutput`.

Exit-code proof against a throwaway fake gateway:

```
scenario=fail  exit=1  VERDICT: FAIL - gateway returned the unclamped cap 400: {"error": {"message": "max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507"}}
scenario=skip  exit=0  VERDICT: SKIP - scaleway qwen3-235b-a22b-instruct-2507 leg returned HTTP 402 (credit/quota): {"error": {"message": "Insufficient credits"}}
scenario=pass  exit=0  VERDICT: PASS - over-cap qwen3-235b-a22b-instruct-2507 request answered 200; no max_completion_tokens cap 400 (clamped, not rejected)
```

Rate-limit / no-leak / determinism:

```
(a) rate-limit: posts=2 elapsed=1.3s exit=0
    logged: rate-limit (UTC 2026-10-01T07:03:04Z): {"error": {"message": "chat_admission_busy"}}; backoff 1s, retry 1/2
(b) key material in output: False (checked 3 candidate key(s))
(c) two runs identical (timestamp line excluded): True
```

## Verdict

**PASS** today: the live gateway clamps the over-cap qwen3-235b request (200, no
cap 400) and the six compiled chunks carry the clamp rule. The tool also handles
the credit-exhausted/unavailable case as an honest SKIP.

## Reviewer

Different family: **`omniroute/t3-driver-clean`**, nonce-gated. Nonce
`CLAMP-ca9d1ccd` read back verbatim from the handoff doc. **Verdict: APPROVE**
(only non-blocking nits; the approved bytes at `5b9b7dd7` are frozen). Recorded in
the handoff doc §8.

## Constraints honoured

No gateway restart; no package or other lane's file modified; no push/merge/rebase/
checkout; no work in the primary checkout. No secret, binary or absolute
username path in a tracked file.

## Rate-limit / admission events

None observed on the live gateway. The `chat_admission_busy` backoff path was
exercised deliberately against the fake gateway (1 s override); shipped default is
90 s.
