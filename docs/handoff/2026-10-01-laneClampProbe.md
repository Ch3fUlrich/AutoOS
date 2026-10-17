# Lane `clamp-probe` — live qwen3-235b output-cap clamp probe

**Date:** 2026-10-01
**Branch:** `L1-backlog/ws-clamp-probe-20260930` (base `origin/main` @ `e58274a8`)
**Worktree:** `AutoOS-ws-clampprobe`
**Tool:** `tools/probe-clamp.py` (new)
**Reviewer nonce:** `CLAMP-ca9d1ccd` (see §8 — must be read back verbatim)

## 1. Why

The operator's post-restart checklist has three items: clamp replay, vertex probe,
deepseek probe. Probes exist for vertex (`tools/probe-vertex.py`, branch
`L1-backlog/ws-f1-vertex-20260930`) and deepseek (`tools/probe-reasoning-repro.py`,
branch `L1-backlog/ws-verify-activate-20260930`), but **no clamp probe existed**.

The clamp patch adds one entry to the gateway's compiled static output-cap array
(see `configuration/omniroute/qwen-clamp-reapply.ps1`) so that Scaleway's
`qwen3-235b-a22b-instruct-2507` gets an explicit 16384 ceiling and the gateway
**clamps** an over-limit `max_tokens` instead of forwarding it:

```js
{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}
```

Unclamped, the request fails with a 400 and a combo leg falls through:

```
400: max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507
```

Measured before the patch (2026-09-30): `max_tokens=32768` → 400, `max_tokens=16` → 200.

## 2. What `tools/probe-clamp.py` does

House shape: direct to the gateway (no opencode), **plain model names**, key from
`AUTOOS_OMNIROUTE_KEY` or `configuration/api-keys.yml` read in memory — never
printed, never in argv.

1. Lists the live catalog (`GET /v1/models`) and discovers a
   `scaleway/qwen3-235b-a22b-instruct-2507` leg (prefers the `scaleway/` prefix,
   then `scw/`).
2. Sends one chat request with `max_tokens=20000` (above the 16384 cap).
3. Sends the same shape to a non-clamped contrast model when one is available.
4. Prints one verdict line.

### Verdict semantics and exit codes

| Verdict | Meaning | Exit |
|---|---|---|
| **PASS** | clamp leg answered 200 to the over-cap request; no cap-limit 400 | 0 |
| **FAIL** | gateway returned the *unclamped* cap 400 (`max_completion_tokens is limited to ...`) | 1 |
| **SKIP** | scaleway unavailable or catalog unreadable (401/402/403/429, connection, timeout, no scaleway qwen3-235b leg) | 0 |

**SKIP is honest** — it means the probe learned nothing about the clamp, not that
the clamp is broken. Non-zero exit happens **only** on FAIL; argparse keeps its own
exit 2 for usage errors.

Unavailability is handled gracefully: any non-200 that is not the cap-limit 400 is
classified (`401 auth`, `402 credit/quota`, `403 forbidden`, `429 rate limited`,
transport) and reported as SKIP. A 429 or a body containing `chat_admission_busy` /
`Rate limit exceeded` is logged with a UTC timestamp and retried with backoff
(default 90 s, in the requested 60–120 s band).

### Key handling (a real finding)

Candidate keys are tried in order — env, then the file's `omniroute_server` and
`omniroute` fields — and the **first the gateway accepts** is used, so a stale env
key cannot mask a working file field, or the reverse. On this gateway
`omniroute_server` **401s** while `omniroute` **authenticates** (verified by hashed
comparison, values never printed), so the naive "read `omniroute_server` only" rule
would SKIP with a false 401. No key material appears in any output (§7b).

## 3. Live discovery (verbatim)

`GET /v1/models` (authenticated) legs whose model segment is
`qwen3-235b-a22b-instruct-2507`:

```
novita/qwen/qwen3-235b-a22b-instruct-2507
scw/qwen3-235b-a22b-instruct-2507
scaleway/qwen3-235b-a22b-instruct-2507
```

The `scaleway/`-prefixed leg is the one the clamp rule targets.

## 4. Verbatim live run

Run A — env key, with read-only compiled-table corroboration:

```
probe-clamp - http://127.0.0.1:20128 (UTC 2026-10-01T07:02:52Z)
key source: env AUTOOS_OMNIROUTE_KEY
catalog legs matching qwen3-235b-a22b-instruct-2507: novita/qwen/qwen3-235b-a22b-instruct-2507, scw/qwen3-235b-a22b-instruct-2507, scaleway/qwen3-235b-a22b-instruct-2507
clamp leg: scaleway/qwen3-235b-a22b-instruct-2507 (max_tokens=20000, cap=16384)
clamp-leg status: 200
unclamped cap 400 seen: False
contrast leg: openrouter/openai/gpt-4o-mini status: 200
compiled-table evidence: clamp rule present in 6/6 chunks
VERDICT: PASS - over-cap qwen3-235b-a22b-instruct-2507 request answered 200; no max_completion_tokens cap 400 (clamped, not rejected)
```

Run B — no env key, the `api-keys.yml` fallback (proves the main-checkout key path
and that the working field is selected):

```
probe-clamp - http://127.0.0.1:20128 (UTC 2026-10-01T07:02:54Z)
key source: api-keys.yml:omniroute
catalog legs matching qwen3-235b-a22b-instruct-2507: novita/qwen/qwen3-235b-a22b-instruct-2507, scw/qwen3-235b-a22b-instruct-2507, scaleway/qwen3-235b-a22b-instruct-2507
clamp leg: scaleway/qwen3-235b-a22b-instruct-2507 (max_tokens=20000, cap=16384)
clamp-leg status: 200
unclamped cap 400 seen: False
contrast leg: openrouter/openai/gpt-4o-mini status: 200
VERDICT: PASS - over-cap qwen3-235b-a22b-instruct-2507 request answered 200; no max_completion_tokens cap 400 (clamped, not rejected)
```

Extra clamp evidence (not a probe mode, an exploration): the same leg answered
**200** to `max_tokens=1000000` — no provider accepts a 1,000,000-token completion,
so the gateway is clamping rather than forwarding. The six live compiled chunks
carry `clampToModelMaxOutput` (the probe's `--package-dir`
`%APPDATA%\npm\node_modules\omniroute` printed `6/6`), corroborating that the static
ceiling is installed.

## 5. Exit-code proof (fake gateway, no live side effects)

`tools/probe-clamp.py` driven against a throwaway local HTTP server:

```
scenario=fail  exit=1  VERDICT: FAIL - gateway returned the unclamped cap 400: {"error": {"message": "max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507"}}
scenario=skip  exit=0  VERDICT: SKIP - scaleway qwen3-235b-a22b-instruct-2507 leg returned HTTP 402 (credit/quota): {"error": {"message": "Insufficient credits"}}
scenario=pass  exit=0  VERDICT: PASS - over-cap qwen3-235b-a22b-instruct-2507 request answered 200; no max_completion_tokens cap 400 (clamped, not rejected)
```

So exit is non-zero **only** on the genuine FAIL.

## 6. Rate-limit and determinism

```
(a) rate-limit: posts=2 elapsed=1.3s exit=0
    logged: rate-limit (UTC 2026-10-01T07:03:04Z): {"error": {"message": "chat_admission_busy"}}; backoff 1s, retry 1/2
    final:  VERDICT: PASS - over-cap qwen3-235b-a22b-instruct-2507 request answered 200; no max_completion_tokens cap 400 (clamped, not rejected)
(b) key material in output: False (checked 3 candidate key(s))
(c) two runs identical (timestamp line excluded): True
    run1: VERDICT: PASS - ...
    run2: VERDICT: PASS - ...
```

The `chat_admission_busy` path was exercised with a 1 s backoff override in the
test; the shipped default is 90 s (60–120 s band). Two consecutive live runs
produced byte-identical output apart from the UTC timestamp line.

## 7. Constraints honoured

* No live gateway restart; no package file modified; no other lane's files touched.
* No push / merge / rebase / checkout.
* No work in the primary checkout (`Documents\Code\AutoOS`); all work was in the
  `AutoOS-ws-clampprobe` worktree.
* No secret material in the tool, this doc, or any output: key values are read in
  memory and only labels are printed.

## 8. Reviewer (nonce-gated, different family)

**Nonce:** `CLAMP-ca9d1ccd`

A `t3-reviewer` subagent on **`omniroute/t3-driver-clean`** (a different family
from the lane author) was asked to read this doc, read the nonce back verbatim,
and give a verdict on `tools/probe-clamp.py` only. Reviewed commit: `5b9b7dd7`.

* Nonce read back: **`CLAMP-ca9d1ccd`** ✅ (doc lines 7 and 161)
* Family: **`omniroute/t3-driver-clean`**
* **Verdict: APPROVE**
* Findings: all non-blocking nits; none addressed after review so the approved
  bytes stay frozen (the nits are known and accepted):
  * FAIL matches only the literal `max_completion_tokens is limited to` phrase, so
    a differently-worded rejection would report SKIP, not FAIL — the server string
    is the measured one and §2 states the design (false-negative, not false-PASS).
  * `discover_legs(models)` is called twice (harmless duplicate work).
  * the `key source` label hardcodes `api-keys.yml:<field>` even for a
    `--keys-file`/`AUTOOS_KEYS_FILE` override (cosmetic; no leak).
  * `--max-tokens` ≤ the cap only warns and proceeds, so an override could yield a
    trivially-true PASS; the shipped default (20000) is unaffected.
* Reviewer evidence included: live `VERDICT: PASS ... exit=0`; connection-refused
  `VERDICT: SKIP ... exit=0`; `--bogus-flag` argparse `exit=2`; key-material check
  `False`; two runs byte-identical apart from the timestamp.
