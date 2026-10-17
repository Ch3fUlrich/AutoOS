# Lane ApplyJson — case-sensitive, duplicate-tolerant registry parse (ws-applyjson-20260930)

Branch: `L1-backlog/ws-applyjson-20260930`
Worktree: `AutoOS-ws-applyjson` (path shown as its leaf name only, per repo rule 1).
Base sha: `c3e139ca98607c2cfe04d7e207956c73959c066b` — the admission-fix tip
(carries the launcher shim fix).
Code commit: `f3c8b0e9bc56f8adb659cd4cbc4cf3849f88ebdb`.
No gateway was restarted at any point. No secrets, usernames or user-home paths below.

## 1. Defect

`configuration/omniroute/apply.ps1` parsed `catalog/ai-registry.json` with
`ConvertFrom-Json` at both registry parse sites (the provider-map builder and the
`api_base` reader). PowerShell's `ConvertFrom-Json` maps a JSON object to a
case-**insensitive** `PSObject`, so a document carrying two keys that differ only
in case throws:

- Windows PowerShell 5.1: `Cannot convert the JSON string because a dictionary
  that was converted from the string contains the duplicated keys '<a>' and '<b>'.`
  (`FullyQualifiedErrorId : DuplicateKeysInJsonString,...`)
- pwsh 7: `Cannot convert the JSON string because it contains keys with different
  casing. Please use the -AsHashTable switch instead. ...`

The throw is inside `Get-AutoOSProviderMap`, i.e. **before** any provider is
registered, and `apply.ps1` runs with `$ErrorActionPreference = 'Continue'` — so
the statement failed, the script carried on, and the whole run's provider
registration was skipped while the existing connections and the combos still
applied (fail-open). Pre-existing at the baseline.

### Collision count in the live registry

A raw-text, case-insensitive duplicate-key scan (Python `object_pairs_hook`, so
literal duplicates that `json.loads` would silently dedupe are still seen):

```
raw-text case-insensitive duplicate key count: 0
exact duplicate key count: 0 []
```

The live `catalog/ai-registry.json` has **0** case-differing duplicate keys today
(`python tools/registry.py validate` → `ok: registry 2026-09-28, 25 routes, 71
models, 33 providers`). The defect is therefore **latent**: it arms the moment a
registry edit adds a vendor spelling that differs from an existing model id only
by case (the FREEWIRE lane's transient `Qwen/Qwen3.8-27B` vs `qwen/qwen3.8-27b`,
or `Mistral-Small-…` vs `mistral-small-…`). A robustness fix, not a live outage.

## 2. Reproduction (failing-first)

Minimal probe, both shells, on `{"models":{"Mistral-Small-X":{},
"mistral-small-x":{}}}` parsed with the current `ConvertFrom-Json` path:

```
--- pwsh 7.5.8 ---
FAIL: Cannot convert the JSON string because it contains keys with different casing. Please use the -AsHashTable switch instead. The key that was attempted to be added to the existing key 'Mistral-Small-X' was 'mistral-small-x'.
--- Windows PowerShell 5.1 ---
FAIL: Cannot convert the JSON string because a dictionary that was converted from the string contains the duplicated keys 'Mistral-Small-X' and 'mistral-small-x'.
```

The new test (`tests/run-tests.ps1`, `Get-AutoOSProviderMap tolerates registry
keys differing only by case`) run against the **pre-fix** function:

```
================ pwsh 7.5.8 (pre-fix) ================
  X Get-AutoOSProviderMap tolerates registry keys differing only by case
      Cannot convert the JSON string because it contains keys with different casing. Please use the -AsHashTable switch instead. The key that was attempted to be added to the existing key 'Qwen/Qwen3.8-27B' was 'qwen/qwen3.8-27b'.
      at Get-AutoOSProviderMap, <No file>: line 3
  passed 1   failed 1   skipped 0
================ Windows PowerShell 5.1 (pre-fix) ================
  X Get-AutoOSProviderMap tolerates registry keys differing only by case
      Cannot convert the JSON string because a dictionary that was converted from the string contains the duplicated keys 'Qwen/Qwen3.8-27B' and 'qwen/qwen3.8-27b'.
      at Get-AutoOSProviderMap, <No file>: line 3
  passed 1   failed 1   skipped 0
```

### Launcher-level before/after

A sandbox with a colliding `catalog/ai-registry.json`, the pre-fix launcher
(`git show HEAD:configuration/omniroute/apply.ps1`, byte-exact) and the post-fix
launcher, each run with `-DryRun`, a closed gateway port and no key file:

```
======== PS5.1 : apply-prefix.ps1 ========
Gateway is down; dry run continues with the static plan ...
ConvertFrom-Json : Cannot convert the JSON string because a dictionary that was converted from the string contains the
duplicated keys 'Mistral-Small-2406' and 'mistral-small-2406'.
At ...\apply-prefix.ps1:133 char:59
+ ... doc = Get-Content $CatalogPath -Raw -Encoding utf8 | ConvertFrom-Json
    + FullyQualifiedErrorId : DuplicateKeysInJsonString,Microsoft.PowerShell.Commands.ConvertFromJsonCommand
The property 'providers' cannot be found on this object. Verify that the property exists.
  [exit=0]
======== pwsh7 : apply-prefix.ps1 ========
ConvertFrom-Json: ...:133
Cannot convert the JSON string because it contains keys with different casing ...
PropertyNotFoundException: ...:134  $providers = $doc.providers
  [exit=0]
======== PS5.1 : apply-postfix.ps1 ========
Providers:
  - groq : no key in api-keys.yml, skipped
...
Done. Apps pick this up on next start (configuration\start-stack.ps1).
  [exit=0]
======== pwsh7 : apply-postfix.ps1 ========
Providers:
  - groq : no key in api-keys.yml, skipped
...
Done. Apps pick this up on next start (configuration\start-stack.ps1).
  [exit=0]
```

Pre-fix, the launcher exits 0 with **no `Providers:` section at all** — every
provider silently skipped. Post-fix, the section is present in both shells.

## 3. Approach and why

Two helpers, defined once in `apply.ps1`, called at both parse sites:

- `ConvertFrom-AutoOSRegistryJson -Text <raw>`:
  - **pwsh 7**: `ConvertFrom-Json -AsHashtable` → `OrderedHashtable`, which keeps
    every key case-sensitively and does not throw on a case-only pair (measured:
    `keys=a,A,b`, `$h['a']=1`, `$h['A']=2`).
  - **Windows PowerShell 5.1**: `-AsHashtable` does not exist, so
    `System.Web.Script.Serialization.JavaScriptSerializer.DeserializeObject`
    (.NET Framework) → `Dictionary<string,object>`, also case-sensitive and
    duplicate-tolerant (measured). `MaxJsonLength` raised from its 2 MB default
    so a future registry can never be silently truncated.
- `ConvertTo-AutoOSRegistryObject`: recursively projects the case-sensitive tree
  onto the `PSObject` shape the existing callers already use (`.PSObject.Properties`,
  nested member access). `Add-Member` preserves each key's exact spelling.

Why this split: `-AsHashtable` is the repo's already-established answer to this
exact class of collision (see `infra/mcp-servers/omnigraph-setup/setup-agent-memory.ps1:111`
and `infra/mcp-servers/scripts/windows/check-graphify-scope.ps1:78`) but exists
only on pwsh 7, and `setup.ps1` requires 5.1. `JavaScriptSerializer` is the only
first-class, no-dependency JSON parser on the .NET Framework box. The rejected
alternative — textually renaming the later colliding key before parsing — mutates
ids and could silently mis-register a provider, which is worse than the bug.

**Duplicate policy:** `PSObject` member names are case-insensitive by
construction, so a case-only pair cannot both survive the projection. The first
key wins. This cannot affect a registration decision: `providers` and `routes`
keys are lower-case by contract, and the only section where such a pair occurs
(`models`) is never read by this launcher. The test asserts this explicitly
(exact-case first key survives; the case-only duplicate does not replace it).

## 4. Verification

### 4.1 New test passes post-fix (both shells)

```
================ pwsh 7.5.8 (post-fix) ================
  + Get-AutoOSProviderMap tolerates registry keys differing only by case   (x3 assertions)
  passed 5   failed 0   skipped 0
================ Windows PowerShell 5.1 (post-fix) ================
  + Get-AutoOSProviderMap tolerates registry keys differing only by case   (x3 assertions)
  passed 5   failed 0   skipped 0
```

### 4.2 Focused provider/apply tests (both shells)

`tests/run-tests.ps1 -Filter 'Get-AutoOSProviderMap,provider data JSON,apply
scripts carry,provider registry is the single source,tolerates registry keys'`:

```
pwsh 7.5.8              passed 59   failed 0   skipped 0
Windows PowerShell 5.1  passed 59   failed 0   skipped 0
```

### 4.3 Full Windows suite — no new failures

```
pwsh 7.5.8              passed 1762   failed 5   skipped 13
Windows PowerShell 5.1  passed 1757   failed 6   skipped 14
```

Every failing test is pre-existing at the base sha `c3e139c`, confirmed by
running the same failing filters in a detached baseline worktree (same commands,
same names):

```
baseline c3e139c, pwsh:  failed 5 -> registry: no generated file drifts |
  autoos-agent spawner unit tests | agent harness: the generator's unit tests pass |
  combos.json is valid... | apply scripts refresh the catalog...  (identical set)
baseline c3e139c, 5.1:   failed 6 -> the same 6, incl. autoos-agent plans tier runs...
```

None touch the changed code (two are Python unit-test suites; the render/combos
drift checks run `python tools/registry.py`; the "refresh the catalog" check
inspects `apply.sh`). **New failures from this lane: 0.**

### 4.4 PowerShell parse errors = 0 (touched .ps1)

```
configuration\omniroute\apply.ps1  ParseErrors=0   (pwsh 7.5.8 and 5.1)
tests\run-tests.ps1                ParseErrors=0   (pwsh 7.5.8 and 5.1)
```

BOM + CRLF preserved on both files.

### 4.5 `Invoke-ScriptAnalyzer` parity

Same invocation as the repo's own gate (`-Severity Error,Warning -ExcludeRule
PSUseShouldProcessForStateChangingFunctions,PSAvoidUsingWriteHost,PSUseSingularNouns`),
fix vs baseline:

```
apply.ps1      fix=1  base=1   (PSUseDeclaredVarsMoreThanAssignments KeysMissing, line 52 both)
run-tests.ps1  fix=17 base=17  (same rules/counts; only line numbers moved)
```

No new finding, no rule crash.

### 4.6 `python tools/registry.py validate`

```
ok: registry 2026-09-28, 25 routes, 71 models, 33 providers
```

### 4.7 `apply.ps1 -DryRun` → gateway OK, no casing error

Live gateway (already up; never restarted), both shells, tail the same and no
`DuplicateKeysInJsonString` / "different casing" anywhere:

```
This is a dry run - nothing is registered, created or started.
No configuration\api-keys.yml yet - copy configuration\api-keys.example.yml and fill it in.
Continuing with combos only.
Gateway OK on http://127.0.0.1:20128
Providers:
  ...
  = antigravity already registered
  ...
Combos:
  - t1-orchestrator: would create [priority] with ...
Overrides:
  ...
Done. Apps pick this up on next start (configuration\start-stack.ps1).
```

## 5. Review (different family, nonce-gated)

Nonce placed for the reviewer: `ws-applyjson-20261001-nonce-K7Q9Z2`.
A `t3-reviewer` subagent (route `omniroute/t3-driver-clean`, a different model
family from this writer's `deepseek-v4.1-flash`) must echo the nonce back.

- Reviewer route/model: `omniroute/t3-driver-clean` (subagent session
  `ses_f0a3654beffempX6cVdVHWfRkp`).
- Nonce returned: `ws-applyjson-20261001-nonce-K7Q9Z2` (exact).
- Verdict: **PASS**.
- Findings: one, on this doc and not on the code — the original draft carried an
  absolute user-home worktree path, which contradicts repo hard rule 1. Fixed in
  place before the docs commit (path now shown as the worktree leaf name only).
  Otherwise "none": it confirmed both shells green on the focused test,
  independently re-probed the helpers on an `{A,a,B}` fixture (both spellings
  handled, no throw, first-wins deterministic), verified the other
  `ConvertFrom-Json` calls in apply.ps1 are unrelated inputs (api-keys, combos,
  overrides), and found no secret/host/home path in the diff.
