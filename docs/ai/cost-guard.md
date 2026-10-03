# Cost Guard — Mechanical Cost Discipline for Google-Paid Models

## Purpose

Cost Guard implements mechanical cost discipline and token volume limits for Google-paid models and legs (`vertex` and `gemini`/AI Studio).

During high-context autonomous runs, repeated agent calls can submit large prompt histories (e.g. 60–300k tokens per call), accumulating massive cached and uncached input token volumes across turn iterations. Cost Guard introduces:
1. **Per-run limits (`run`)**: hard stop at $2.00 or 1M uncached input tokens, context-window rotation at 150k input tokens, and warnings on trailing high-volume calls.
2. **Daily budget gate (`day`)**: daily budget aggregation across central and workstation call logs with blocking at $25/day (warn at $20/day) on Google-paid spend.
3. **Spawner integration**: `tools/autoos-agent.py` refuses Google-paid starts when the daily gate blocks, while failing open if the gate report is unavailable or stale (>2 hours).

---

## D-543 Thresholds and Verdicts

### Per-Run Verdicts (`run_budget.py run`)

Evaluates calls associated with a run or session tag:

| Verdict | Exit Code | Condition | Action |
|---|---|---|---|
| `stop` | **12** | Uncached input > 1,000,000 tokens **OR** estimated cost > $2.00 | Hard stop: abort the run to prevent runaway spend. |
| `rotate` | **11** | Last call input tokens >= 150,000 | Context cap: cleanly commit state and report; continuation starts fresh from the committed state. |
| `flag` | **10** | Last 3 calls each had > 60,000 input tokens | Report only: alert on escalating prompt context. |
| `ok` | **0** | None of the above conditions met | Proceed normally. |

### Daily Gate Verdicts (`run_budget.py day`)

Aggregates Google-paid spend (`vertex` and `gemini`/AI Studio) for the UTC day across row exports:

| Verdict | Exit Code | Condition (Default) | Spawner Effect |
|---|---|---|---|
| `ok` | **0** | Total Google-paid spend < $20.00 | Spawner allows launch. |
| `warn` | **20** | Total Google-paid spend >= $20.00 and < $25.00 | Spawner allows launch (warn changes nothing at the spawner; `run_budget.py day` prints verdict `warn` and exits 20, but the spawner reads only verdict `block` from the gate file). |
| `block` | **21** | Total Google-paid spend >= $25.00 | Spawner **refuses** Google-paid launch. |

*Exclusions:* Antigravity (`agy`, `antigravity`) and OVH (`ovh`, `ovhcloud`) models are never counted toward Google-paid spend and **never refused** by this gate — wholly agy/ovh/free legs pass the gate, but per model string: an `ovh/` or `agy/` leg does not stand the check down when another model or leg of the same start is Google-paid. A plain `--free` run uses the free chain and is not refused; `--free --model <google-paid>` launches that model and **is** refused like any other Google-paid start.

---

## CLI Usage for Runners

### Evaluating A Single Run

Runners and stall detectors check per-run token accumulation:

```bash
# Evaluate from a row export file (JSON or NDJSON)
python tools/run_budget.py run --rows <export_file.json> --tag <session_tag>

# Or evaluate directly from the central gateway
python tools/run_budget.py run --gateway --tag <session_tag>
```

When `run` is given both `--rows` and `--gateway`, it reads `--rows` only (the gateway is not queried), unlike `day` which queries both and merges them.

Example JSON output:
```json
{
  "calls": 12,
  "input": 450000,
  "cache_read": 380000,
  "uncached": 70000,
  "output": 12000,
  "est_usd": 0.16875,
  "max_input_per_call": 65000,
  "last3_input": [
    62000,
    64000,
    65000
  ],
  "bad_rows": 0,
  "unpriced_models": [],
  "unpriced_default_used": false,
  "verdict": "flag",
  "unverified": true,
  "truncated": false
}
```

Field descriptions:
- `bad_rows`: counts malformed NDJSON lines, non-object items of a JSON array, rows with non-numeric/NaN/inf/list/bool token fields and rows with a missing or garbage timestamp.
- `unpriced_models`: lists any unpriced Google-paid models encountered along with their call counts.
- `unpriced_default_used`: boolean indicating whether default fallback rates were used for unpriced models.
- `truncated`: boolean indicating whether gateway row fetching hit the server row limit.

A mistyped `--rows` path fails with exit code 2 (it names the unreadable file), so a typo cannot silently evaluate an empty day.

### Evaluating Daily Spend

Launchers run the daily aggregation before starting paid workflows:

```bash
python tools/run_budget.py day \
  --rows /path/to/central-call-log.json /path/to/workstation-export.ndjson \
  --budget 25.0 \
  --warn 20.0 \
  > /tmp/daily_gate.json
```

Example output:
```json
{
  "day": "2026-10-03",
  "usd": 25.42,
  "by_provider": {
    "gemini": 4.32,
    "vertex": 21.1
  },
  "verdict": "block",
  "unverified": true,
  "budget": 25.0,
  "bad_rows": 0,
  "unpriced_models": [],
  "unpriced_default_used": false,
  "truncated": false
}
```

### Writing The Gate File On Windows PowerShell 5

`run_budget.py day` has no `--out` flag; it prints the JSON and the redirection decides the encoding. A plain `>` in Windows PowerShell 5 writes **UTF-16**, and `Out-File -Encoding utf8` / `Set-Content -Encoding utf8` add a UTF-8 BOM. The spawner tolerates all of them (it reads UTF-8 with or without a BOM, then UTF-16), so any of these works:

```powershell
# UTF-16 (PowerShell 5 default) - tolerated (expand wildcards before passing to native executable)
python tools/run_budget.py day --rows (Get-ChildItem .\call-logs\*.json | ForEach-Object FullName) > .\daily_gate.json
# UTF-8 with BOM - tolerated
python tools/run_budget.py day --rows (Get-ChildItem .\call-logs\*.json | ForEach-Object FullName) | Out-File -Encoding utf8 .\daily_gate.json
```

On Linux/macOS, `> /tmp/daily_gate.json` writes plain UTF-8, the cleanest case.

### Spawner Integration (`autoos-agent.py`)

Set the environment variable `AUTOOS_DAILY_GATE_FILE` to point to the output written by `run_budget.py day`:

```bash
export AUTOOS_DAILY_GATE_FILE="/tmp/daily_gate.json"
python tools/autoos-agent.py run --client opencode --model omniroute/vertex-gemini-3.8-flash "..."
```

The gate runs at **three** points inside `autoos-agent.py`:

1. **Before cloning or planning** (the only point that precedes both): a Google-paid `--model` pin is refused here.
2. **After the plan is built**: the gate re-reads the model the plan actually answers with, because a re-resolved route can rewrite the pin since point 1.
3. **In the fallthrough re-run**: after a provider stop, the re-planned next leg is checked before the second attempt starts.

The MCP spawn path is gated the same way: the MCP server's preflight and detached runner re-execute the CLI as a child process, and `AUTOOS_DAILY_GATE_FILE` is on the child-environment passlist, so a Google-paid MCP start is refused with the same message as the direct CLI.

**What is not gated:** runs whose model the spawner never sees — combos/aliases resolved inside the gateway, and unpinned router-pick runs whose legs only become known to the gateway at request time. The gate sees the model string this process plans to launch, and only that.

- If `verdict == "block"`: the spawner refuses the start with exit code 2:
  `autoos-agent: daily budget blocked: day total $25.42 exceeds budget $25.00`
  Under a `block` verdict:
  * A falsy `usd` value (empty string, empty list/dict, `false`, or `null`) is refused as `$0.00` (the owner's rule):
    `autoos-agent: daily budget blocked: day total $0.00 exceeds budget $25.00`
  * Garbage values (non-numeric strings, `NaN`, `inf`, huge integers, non-empty lists/dicts, `true`) fail open with `daily gate unavailable: garbage value` and permit the start.
  * A gate value given as the string `"0"` is a number (it is not falsy), so a budget of `"0"` means a budget of $0.00 while the number 0 means the default 25.
- If `AUTOOS_DAILY_GATE_FILE` is unset, missing, unreadable (including a non-numeric `usd` or `budget`), older than 2 hours, dated in the future, or its `day` field is not today's UTC day:
  The spawner fails **OPEN**, prints exactly one line to stderr: `daily gate unavailable: <reason>` where `<reason>` is one of:
    * `env var not set`
    * `file unreadable`
    * `file stale (age)`
    * `file stale (not today's UTC day)`
    * `file stale (future mtime)`
    * `garbage value`
    * `not a JSON object`
  and permits the start. A block written for another UTC day stays stale even inside the 2-hour mtime window: yesterday's block must not ride into today.
- The daily budget cannot be overridden at the spawner. To raise the daily budget, the operator must write a new gate file using `run_budget.py day --budget N`.
- `agy`/`antigravity` and `ovh`/`ovhcloud` model strings are never blocked by this gate; wholly agy/ovh/free legs pass the gate. The gate never blocks an `agy` client: it is asked first and returns no refusal for one (`is_google_paid_start` is false for the agy/antigravity client), and agy and ovh legs add $0 to the day total. The capability refusal ('lacks shell and write') comes after the gate and only fires for tasks that need shell and write (for example with `--isolate`); a plain dry run with `--client agy` goes through.

---

## Price Table and Billing SKUs

Pricing is loaded by `run_budget.load_price_table()`, first checking registry model price fields, and falling back to `configuration/google-prices.json`.

### Current Rates (as of 2026-10-03)

| Provider / Model | Uncached Input (per 1M) | Cache-Read Input (per 1M) | Output (per 1M) | Verification Status | Citation |
|---|---|---|---|---|---|
| `gemini/gemini-3.8-flash` (AI Studio) | $0.75 | $0.075 | $3.75 | Verified | `ai.google.dev/gemini-api/docs/pricing` (2026-10-03) |
| `vertex/gemini-3.8-flash` (Vertex AI) | $0.75 | $0.1875 | $3.75 | **Unverified** (Conservative) | Estimated rate until GCP billing SKUs published |

The 2027 step-up is mirrored from `configuration/google-prices.json`. In `configuration/google-prices.json`, the AI Studio `gemini-3.8-flash` rates carry `"notes": "2027 step-up doubles"` (stepping up to input $1.50, cache-read $0.15, output $7.50 per 1M). For Vertex, `price_in` and `price_out` carry `"notes": "same numbers as AI Studio intro"`, while `price_cache_read` is the conservative UNVERIFIED 0.1875 (`"notes": "CONSERVATIVE 0.1875 until the GCP billing SKUs are known"`), so doubled it would be 0.375 only if that rule applied; vertex cache-read is unknown until the billing SKUs are known. When that step-up lands, both providers' rows in the fallback table (and any registry copies) must move with it.

### Updating Rates When Billing SKUs Arrive

When official Google Cloud Platform billing SKUs or verified invoice numbers are confirmed:
1. Open `configuration/google-prices.json`.
2. Locate the model entry under `"vertex"`.
3. Update `"price_cache_read"`, `"price_in"`, or `"price_out"`. Both `"per_million"` and `"per_token"` must BOTH be changed (a mismatch makes `run_budget.py` exit 2).
4. Update `"url"` with the GCP SKU documentation URL.
5. Set `"unverified": false`.
6. Update `"date"` to the verification date (e.g. `YYYY-MM-DD`).
7. Run the suites (plain unittest scripts, no pytest): `python tests/test_run_budget.py`, `python tests/test_run_budget_more.py`, `python tests/test_run_budget_v3.py`, `python tests/test_run_budget_v3b.py`, `python tests/test_run_budget_spawner.py`, and `python tests/test_run_budget_spawner_v3.py`.

---

## Known Limits

- A 1M-row file takes about 22 s for `day`, about 13 s for `run --tag <tag>` and about 31 to 33 s for `run` over all rows.
- When `run` is given both `--rows` and `--gateway`, it reads rows only (the gateway is not queried).
- Duplicate rows across two row files are counted twice (errs towards blocking).
- Behaviours that are true but not pinned by a test: the `run` path counting a row with a missing or garbage timestamp as a bad row, a gate value given as the number `0` (it is falsy, so it means the default), and a gate value given as the string `"0"` (it is a number, so a budget of `"0"` means $0.00).
- Combos, aliases and router-pick runs are not gated.
- The gate file must be written by `run_budget.py day` (UTF-8 with or without BOM, or UTF-16 with a BOM, are read).
- A missing or mistyped rows path is an error (exit 2), and so is giving neither `--rows` nor `--gateway`.
