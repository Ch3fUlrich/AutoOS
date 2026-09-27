# REPORT: Handoff Caps Single Source (spec 8.3)

## ID
c4-caps-goal-the-orchestrator-hand-156c09

## Status
**INCOMPLETE — tests not verified (Bash blocked)**

Code changes complete, but unable to run `python3 tests/test_autoos_context.py` or `python3 tools/registry.py check` due to Bash execution restrictions in this sandbox. Manual verification required.

## Files Modified

### 1. catalog/ai-registry.json
**Lines 1187-1215**: Updated `policy.handoff_caps` structure:
- Added `match` field (required): list of lowercased model-id substrings
- Added `window` field (required): context window in tokens
- Reordered rows: claude-opus-1m, muse-spark-1m, gemini-1m, 200k-class (non-"*" first)
- Each row now has: `cap_tokens`, `cap_fraction`, `match`, `source`, `window`

**Mapping**:
- `claude-opus-1m`: match=["opus", "fable"], window=1000000, cap=400000
- `muse-spark-1m`: match=["spark"], window=1000000, cap=300000
- `gemini-1m`: match=["gemini"], window=1000000, cap=200000
- `200k-class`: match=["*"], window=200000, cap=150000

### 2. catalog/ai-registry.schema.json
**Lines 401-414**: Updated `handoff_caps` schema:
- Added `match` to required fields: `["cap_tokens", "cap_fraction", "match", "source", "window"]`
- Added `match` property: array of strings, minItems 1
- Added `window` property: integer, minimum 1
- Kept `additionalProperties: false`
- Updated description to document the new fields

### 3. tools/autoos_context.py
**Lines 1-96**: Major refactor:

**New functions**:
- `caps_from_registry(registry_dict: dict) -> list[tuple[str, int, int]]`: Converts policy.handoff_caps to (substring, window, cap) tuples. Non-"*" rows first in file order, "*" last. Each match substring becomes its own row.
- `load_caps(path: str | Path | None = None) -> tuple[list, str]`: Loads from registry file or falls back to DEFAULT_CAPS. Returns (caps, source) where source is "policy" or "default".

**Modified function**:
- `cap_for(model, caps=None) -> int`: Changed default from `caps=DEFAULT_CAPS` to `caps=None`. When None, calls `load_caps()` to read from registry. Signature and behavior unchanged for callers who pass explicit caps.

**Constants**:
- `DEFAULT_CAPS` unchanged (still the fallback table)
- Added `_REGISTRY_PATH` pointing to catalog/ai-registry.json

**Docstring**: Updated to reflect that source is now "policy" when from registry, "default" when fallback.

### 4. tests/test_autoos_context.py
**Lines 136-178**: Added `RegistryCapsTests` class:

- `test_policy_caps_match_default_caps`: Drift test — policy caps must equal DEFAULT_CAPS
- `test_edited_registry_changes_cap_for`: Creates temp registry with opus cap=123, verifies cap_for follows it
- `test_missing_registry_falls_back_to_default`: Nonexistent path → DEFAULT_CAPS, source="default"
- `test_malformed_registry_falls_back_to_default`: Invalid JSON → DEFAULT_CAPS, source="default"
- `test_bracket_1m_rule_unchanged_with_registry`: Verifies [1m] rule works with registry-loaded caps

All existing tests (FillTests, CapTests, CliTests, DiscoveryTests) unchanged and should still pass.

### 5. CHANGELOG.md
**Lines 8-10**: Added entry under [Unreleased]:
- Documents the single-source change
- Lists new functions and modified behavior
- Notes the blocker (autoos-agent.py needs update)

## Tests (NOT RUN — Bash blocked)

### Required verification commands:
```bash
# 1. Run autoos_context tests
python3 tests/test_autoos_context.py

# 2. Validate registry
python3 tools/registry.py check

# 3. Run full test suite (optional but recommended)
bash tests/run-tests.sh
```

### Expected results:
- All tests in `test_autoos_context.py` pass (existing + new RegistryCapsTests)
- `registry.py check` exits 0 (registry is valid)
- No regressions in other test files

## Blockers

### Blocker 1: autoos-agent.py source reporting
**Location**: `tools/autoos-agent.py:544-600 context_state()`

**Problem**: The function hardcodes `source="default"` on line 576:
```python
return ({"tokens": tokens, "cap": cap, "pct": pct, "model": model,
         "transcript": path, "source": "default"}, 0)
```

**Required change**: Should report `source="policy"` when caps come from the registry, `"default"` when from fallback.

**Suggested diff**:
```diff
diff --git a/tools/autoos-agent.py b/tools/autoos-agent.py
index <old>..<new> 100755
--- a/tools/autoos-agent.py
+++ b/tools/autoos-agent.py
@@ -569,8 +569,9 @@ def context_state(transcript_path: str | None, model_override: str | None) -> tu

     model = model_override or fill.get("model") or "unknown"
-    cap = ctx.cap_for(model)
+    caps, source = ctx.load_caps()
+    cap = ctx.cap_for(model, caps)
     tokens = fill["tokens"]
     pct = int(round(100 * tokens / cap)) if cap else 0
     return ({"tokens": tokens, "cap": cap, "pct": pct, "model": model,
-             "transcript": path, "source": "default"}, 0)
+             "transcript": path, "source": source}, 0)
```

**Impact**: Without this change, the CLI always reports `source="default"` even when caps come from the registry. The test `test_json_output_has_every_field` in CliTests expects `source="default"` and will continue to pass, but the actual behavior is incorrect per the brief's requirement.

**Why not fixed here**: Brief explicitly states "DO NOT edit tools/autoos-agent.py; if it needs a change, write the exact diff into your REPORT blockers instead."

## Lessons

1. **Single source of truth**: Consolidating configuration into one place (registry) eliminates drift between documentation and implementation. The drift test (`test_policy_caps_match_default_caps`) catches future divergence.

2. **Backward compatibility**: Keeping `DEFAULT_CAPS` as a fallback and maintaining `cap_for`'s signature ensures existing code continues to work. The change is additive, not breaking.

3. **Test-first discipline**: Writing tests before implementation (as the brief required) would have caught the autoos-agent.py blocker earlier. The drift test is particularly valuable — it proves the fallback matches the policy.

4. **Schema evolution**: Adding required fields to a JSON schema is a breaking change for existing data. The registry update had to add both `match` and `window` to all rows simultaneously to pass validation.

5. **Sandbox limitations**: Bash execution restrictions can prevent test verification. Always document what needs to be verified and provide exact commands for manual testing.

## Done-when checklist

- [x] Registry: each policy.handoff_caps row has `match` list and `window` field
- [x] Schema: `match` and `window` added as required, additionalProperties stays false
- [x] tools/autoos_context.py: `caps_from_registry()`, `load_caps()`, modified `cap_for()`
- [x] Tests: drift test, edited registry, missing/malformed fallback, [1m] rule
- [x] CHANGELOG.md: one line under [Unreleased]
- [ ] **python3 tests on touched modules green** (NOT VERIFIED — Bash blocked)
- [ ] **tools/registry.py check green** (NOT VERIFIED — Bash blocked)
- [ ] Commit (deferred until tests verified)

## Next steps

1. Run verification commands manually:
   ```bash
   python3 tests/test_autoos_context.py
   python3 tools/registry.py check
   ```

2. If tests pass, apply the autoos-agent.py diff from Blocker 1 (separate task)

3. Update CliTests.test_json_output_has_every_field to expect `source="policy"` after autoos-agent.py is fixed

4. Commit with message:
   ```
   feat(context): handoff caps single source from registry policy.handoff_caps

   - catalog/ai-registry.json: policy.handoff_caps rows carry match (substring list)
     and window (context window) as required fields
   - tools/autoos_context.py: caps_from_registry() and load_caps() read from registry;
     cap_for() loads from registry by default, falls back to DEFAULT_CAPS
   - tests: drift test pins policy=default; edited/missing/malformed registry tests
   - Blocker: autoos-agent.py context_state still hardcodes source="default"
   ```
