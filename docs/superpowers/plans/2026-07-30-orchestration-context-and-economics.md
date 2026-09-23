# Orchestration Context Firewall & Model Economics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the orchestration policy enforceable on the Claude Code native surface, and fix the eight verified defects in the Python scaffold — including a swarm review that currently fails open.

**Architecture:** `agent_orchestration.config.yaml` becomes the single runtime source for every orchestration fact. The Python scaffold reads it at runtime; the `.claude/` adapter is *generated* from it (workflow scripts have no filesystem access, so generation is the only way to keep one source). A `--check` mode gates drift.

**Tech Stack:** Python 3.13 + pytest (scaffold, generator), PyYAML, plain JavaScript (Claude Code workflow scripts), Claude Code agent/hook markdown + JSON.

## Global Constraints

- **Spec:** [`docs/superpowers/specs/2026-07-30-orchestration-context-and-economics-design.md`](../specs/2026-07-30-orchestration-context-and-economics-design.md). Its §2 defect table (D1–D8) is the authority on what is broken.
- **ADR:** [`docs/decisions/0004-executable-orchestration-policy.md`](../../decisions/0004-executable-orchestration-policy.md).
- **TDD is rigid** (`coding-principles` Principle 2): write the failing test, watch it fail, then implement. Never the reverse.
- **One home per fact.** `skills/swarm-orchestration/SKILL.md` must contain **no numbers**. Every threshold, tier, matrix, and cap lives in `agent_orchestration.config.yaml`. Adding a number to a `SKILL.md` fails review.
- **Line endings:** `.gitattributes` pins `*.py` to `eol=lf` and `*.ps1` to `eol=crlf`. Task 9 adds `*.js text eol=lf`. Never re-save a script as CRLF.
- **Scratch scripts** go in `.claude/scratch/` — never the repo root (`AGENTS.md` hard rule).
- **All test commands run from** `skills/swarm-orchestration/custom_orchestration/`. The modules use flat imports (`from orchestrator_scaffold import ...`) and there is no `conftest.py`, so `python -m pytest` (which puts CWD on `sys.path`) is required — plain `pytest` will fail with `ModuleNotFoundError`.
- **Baseline:** 61 tests pass before any change. Every task must leave the suite green.
- **Tier aliases** are `opus` / `sonnet` / `haiku`. Never pin a dated model id — that is how `sonnet-3.5` and `haiku-3` rotted in place.

---

### Task 1: Restore the provider fallback chain (D1)

`adapter_for()` sits at the top of the retry loop, **outside** the `try:` that begins at `execution_mode = ...`. `ProviderRegistry.get()` raises `KeyError` for an unknown name, so iteration 0 propagates out and fallback candidates 1–3 are never tried. This breaks the 3-deep fallback design for *any* unknown provider.

**Files:**
- Modify: `skills/swarm-orchestration/custom_orchestration/provider_executor.py:94-96`
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_provider_executor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ProviderExecutor._try_selection` now records an unresolvable provider in `last_error` and advances. No signature change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provider_executor.py`:

```python
def test_unknown_provider_falls_through_to_next_candidate():
    config = {
        "role_provider_routing": {
            "engineer": {
                "provider": "not-a-real-provider",
                "model": "m0",
                "fallback_providers": ["claude_code"],
                "fallback_models": ["m1"],
            }
        }
    }
    executor = ProviderExecutor(config)
    result = executor.execute(
        role="engineer",
        agent_id="a1",
        system_prompt="s",
        user_prompt="u",
        tools=[],
        mcp_servers=[],
        skill_ids=[],
        metadata={"execution_mode": "stub"},
    )
    assert result.success is True
    assert result.provider_name == "claude_code"
    assert result.fallback_used is True


def test_all_providers_unknown_returns_failure_not_exception():
    config = {
        "role_provider_routing": {
            "engineer": {
                "provider": "nope-a",
                "model": "m0",
                "fallback_providers": ["nope-b"],
                "fallback_models": ["m1"],
            }
        }
    }
    executor = ProviderExecutor(config)
    result = executor.execute(
        role="engineer",
        agent_id="a1",
        system_prompt="s",
        user_prompt="u",
        tools=[],
        mcp_servers=[],
        skill_ids=[],
        metadata={"execution_mode": "stub"},
    )
    assert result.success is False
    assert "nope-b" in result.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_provider_executor.py -v`
Expected: FAIL with `KeyError: 'Unknown provider: not-a-real-provider'` (the exception escapes rather than being recorded).

- [ ] **Step 3: Write minimal implementation**

In `provider_executor.py`, replace the top of the loop body:

```python
        for idx, provider_name in enumerate(providers_to_try):
            adapter = self.factory.adapter_for(provider_name)
            model = models_to_try[min(idx, len(models_to_try) - 1)]
```

with:

```python
        for idx, provider_name in enumerate(providers_to_try):
            model = models_to_try[min(idx, len(models_to_try) - 1)]

            # Resolution must not escape the loop: an unknown provider is a
            # skip-to-next-candidate, not an abort. Leaving adapter_for()
            # outside the retry defeated the whole fallback chain (D1).
            try:
                adapter = self.factory.adapter_for(provider_name)
            except Exception as e:
                last_error = f"{provider_name}: {e}"
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `63 passed`

- [ ] **Step 5: Commit**

```bash
git add skills/swarm-orchestration/custom_orchestration/provider_executor.py skills/swarm-orchestration/custom_orchestration/tests/test_provider_executor.py
git commit -m "fix(orchestration): unknown provider no longer aborts the fallback chain (D1)"
```

---

### Task 2: Ordered, config-owned verdict matrix (D4, D4a, D4b)

The matrix lives in three places: `qa-swarm/SKILL.md` prose, `triage_policy.verdict_matrix` (read by no code), and hardcoded in `TriagePipeline.calculate_verdict`. The YAML copy is an unordered mapping, so the 1 HIGH + 2 MEDIUM overlap has no defined precedence, and 1–2 MEDIUM with 0 HIGH matches no rule at all.

**Files:**
- Modify: `skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml:205-222`
- Modify: `skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py:374-392`
- Modify: `skills/swarm-orchestration/custom_orchestration/tests/test_orchestrator_scaffold.py:11-35` (fixture)
- Modify: `skills/qa-swarm/SKILL.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `TriagePipeline.calculate_verdict(findings: List[Dict]) -> str` unchanged in signature but now evaluates `triage_policy.verdict_matrix` from config. `TriagePipeline.__init__` raises `ValueError` when the matrix is absent. Config gains `triage_policy.severities: List[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_scaffold.py`:

```python
def test_triage_verdict_two_medium_is_nits_not_silent_approve(base_config):
    # D4b: the declared matrix had a hole here -- >=3 MEDIUM failed and
    # only_low_or_nit was false, so no rule fired. Silent APPROVE meant
    # 1-2 MEDIUM findings vanished under narration.mode=silent.
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "MEDIUM"}, {"severity": "MEDIUM"}]
    assert pipeline.calculate_verdict(findings) == "APPROVE_WITH_NITS"


def test_triage_verdict_one_medium_is_nits(base_config):
    pipeline = TriagePipeline(base_config)
    assert pipeline.calculate_verdict([{"severity": "MEDIUM"}]) == "APPROVE_WITH_NITS"


def test_triage_missing_matrix_raises(base_config):
    # One home per fact: no built-in copy to silently fall back to.
    with pytest.raises(ValueError, match="verdict_matrix"):
        TriagePipeline(MockConfig({"triage_policy": {}}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_orchestrator_scaffold.py -k "two_medium or one_medium or missing_matrix" -v`
Expected: FAIL — the two medium tests return `APPROVE`; `test_triage_missing_matrix_raises` fails because no `ValueError` is raised.

- [ ] **Step 3: Change the config to an ordered sequence with structured conditions**

In `agent_orchestration.config.yaml`, replace the whole `triage_policy:` block:

```yaml
triage_policy:
  human_participation_gate:
    enabled: true
    never_autofix_if_human_present: true
    never_autoresolve_if_human_present: true

  # Severity vocabulary, most severe first. Owned here; qa-swarm/SKILL.md links.
  severities: [CRITICAL, HIGH, MEDIUM, LOW, NIT]

  # ORDERED — first match wins. A mapping had no order, which left the
  # 1 HIGH + 2 MEDIUM overlap undefined (D4a). Conditions are structured
  # rather than English so one rule set drives both Python and generated JS
  # without an expression parser. The final entry is the terminal
  # fall-through and must have an empty `when`.
  verdict_matrix:
    - verdict: BLOCKED
      when: {critical: ">=1"}
      action: escalate_to_architect
    - verdict: REQUEST_CHANGES
      any_of:
        - {high: ">=2"}
        - {high: ">=1", medium: ">=2"}
      action: return_to_engineer
    - verdict: APPROVE_WITH_NITS
      any_of:
        - {high: ">=1"}
        - {medium: ">=3"}
      action: engineer_autofix_or_ignore
    - verdict: APPROVE
      when: {critical: "==0", high: "==0", medium: "==0"}
      action: proceed_to_merge
    - verdict: APPROVE_WITH_NITS
      when: {}
      action: engineer_autofix_or_ignore
```

- [ ] **Step 4: Update the test fixture to carry the matrix**

In `tests/test_orchestrator_scaffold.py`, replace the `"triage_policy"` key inside `base_config`:

```python
        "triage_policy": {
            "human_participation_gate": {"enabled": True},
            "severities": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NIT"],
            "verdict_matrix": [
                {"verdict": "BLOCKED", "when": {"critical": ">=1"}},
                {"verdict": "REQUEST_CHANGES", "any_of": [
                    {"high": ">=2"},
                    {"high": ">=1", "medium": ">=2"},
                ]},
                {"verdict": "APPROVE_WITH_NITS", "any_of": [
                    {"high": ">=1"},
                    {"medium": ">=3"},
                ]},
                {"verdict": "APPROVE", "when": {"critical": "==0", "high": "==0", "medium": "==0"}},
                {"verdict": "APPROVE_WITH_NITS", "when": {}},
            ],
        },
```

- [ ] **Step 5: Implement the evaluator**

In `orchestrator_scaffold.py`, replace the whole `TriagePipeline` class:

```python
class TriagePipeline:
    _OPS = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }

    def __init__(self, config: Config):
        self.policy = config.get("triage_policy", default={})
        self.matrix = self.policy.get("verdict_matrix")
        if not self.matrix:
            raise ValueError(
                "triage_policy.verdict_matrix is required; it is the single home "
                "for the verdict rules and has no built-in fallback copy."
            )
        self.severities = [s.lower() for s in self.policy.get("severities", [])]

    def is_human_participating(self, thread_comments: List[Dict[str, Any]]) -> bool:
        gate = self.policy.get("human_participation_gate", {})
        if not gate.get("enabled", True):
            return False
        for comment in thread_comments:
            if not comment.get("is_bot", False):
                return True
        return False

    def _match(self, clause: Dict[str, str], counts: Dict[str, int]) -> bool:
        # An empty clause is the terminal fall-through and always matches.
        for severity, expr in clause.items():
            for op, fn in self._OPS.items():
                if expr.startswith(op):
                    if not fn(counts.get(severity, 0), int(expr[len(op):])):
                        return False
                    break
            else:
                raise ValueError(f"unparseable verdict condition: {severity}: {expr}")
        return True

    def calculate_verdict(self, swarm_findings: List[Dict[str, Any]]) -> str:
        counts = {s: 0 for s in self.severities}
        for finding in swarm_findings:
            severity = str(finding.get("severity", "")).lower()
            if severity in counts:
                counts[severity] += 1

        # First match wins -- ordering is the precedence, and it is explicit.
        for rule in self.matrix:
            clauses = rule.get("any_of") or [rule.get("when", {})]
            if any(self._match(c, counts) for c in clauses):
                return rule["verdict"]
        raise ValueError("verdict_matrix has no terminal fall-through entry")
```

- [ ] **Step 6: Run the full suite**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `66 passed`. All ten pre-existing verdict tests still pass — none of them covered the 1–2 MEDIUM gap, so this is a new behaviour, not a changed one.

- [ ] **Step 7: Point qa-swarm/SKILL.md at the config**

In `skills/qa-swarm/SKILL.md`, replace the whole of `## 2. Verdict Scoring Matrix` with:

```markdown
## 2. Verdict Scoring Matrix

The severity vocabulary and the verdict rules live in **one** place:
`skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml` →
`triage_policy.severities` and `triage_policy.verdict_matrix`.

The matrix is an **ordered** sequence and **first match wins** — that ordering *is* the
precedence. Do not restate the rules here: they previously existed in three places, which left
1 HIGH + 2 MEDIUM ambiguous and 1–2 MEDIUM undefined entirely.

`TriagePipeline.calculate_verdict` evaluates that config directly, and the generated
`.claude/workflows/qa-swarm.js` compiles the same rules into JavaScript.
```

- [ ] **Step 8: Commit**

```bash
git add skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py skills/swarm-orchestration/custom_orchestration/tests/test_orchestrator_scaffold.py skills/qa-swarm/SKILL.md
git commit -m "fix(orchestration): ordered config-owned verdict matrix; close the MEDIUM hole (D4)"
```

---

### Task 3: Risk score is a raw sum with asserted indicators (D5, D6)

`RiskEngine.score()` divides by Σweights (1.50), making it a mean while the docs describe a sum — so the three artifacts disagreed and `very_high` needed ~10 of 11 signals. `RiskSignals` fields are bare floats with no documented encoding, so passing a weight instead of an indicator silently squares it.

**Files:**
- Modify: `skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py:19-30` (`RiskSignals` docstring), `:236-250` (`score`)
- Modify: `skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml:66-70` (comment only)
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_risk_engine.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `RiskEngine.score(signals: RiskSignals) -> float` returns the raw weighted sum and raises `ValueError` on a non-indicator value. `RiskEngine.classify` and `execution_mode` are unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_risk_engine.py`:

```python
import pytest
from orchestrator_scaffold import Config, RiskEngine, RiskSignals


class MockConfig(Config):
    def __init__(self, data):
        self.data = data


@pytest.fixture
def risk_config():
    return MockConfig({
        "risk": {
            "thresholds": {"low": 0.30, "medium": 0.60, "high": 0.85},
            "signals": {
                "touches_more_than_3_files": 0.15,
                "modifies_public_api": 0.15,
                "ambiguous_acceptance_criteria": 0.20,
                "weak_or_missing_test_coverage": 0.10,
                "cross_module_interface_change": 0.10,
            },
        }
    })


def test_score_is_a_raw_sum_not_a_mean(risk_config):
    engine = RiskEngine(risk_config)
    signals = RiskSignals(touches_more_than_3_files=1.0, modifies_public_api=1.0)
    assert engine.score(signals) == pytest.approx(0.30)


def test_no_signals_scores_zero(risk_config):
    assert RiskEngine(risk_config).score(RiskSignals()) == pytest.approx(0.0)


def test_four_signals_reach_the_high_band(risk_config):
    engine = RiskEngine(risk_config)
    signals = RiskSignals(
        touches_more_than_3_files=1.0,
        modifies_public_api=1.0,
        ambiguous_acceptance_criteria=1.0,
        weak_or_missing_test_coverage=1.0,
    )
    score = engine.score(signals)
    assert score == pytest.approx(0.60)
    assert engine.classify(score) == "high"
    assert engine.execution_mode(score) == "best-of-3"


def test_band_boundaries(risk_config):
    engine = RiskEngine(risk_config)
    assert engine.classify(0.29) == "low"
    assert engine.classify(0.30) == "medium"
    assert engine.classify(0.59) == "medium"
    assert engine.classify(0.60) == "high"
    assert engine.classify(0.84) == "high"
    assert engine.classify(0.85) == "very_high"


def test_passing_a_weight_instead_of_an_indicator_raises(risk_config):
    # D6: value * weight silently squares the weight if a caller passes 0.15.
    engine = RiskEngine(risk_config)
    with pytest.raises(ValueError, match="0.0 or 1.0"):
        engine.score(RiskSignals(touches_more_than_3_files=0.15))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_risk_engine.py -v`
Expected: FAIL — `test_score_is_a_raw_sum_not_a_mean` gets `0.2` (0.30 ÷ 1.50 over the 5 declared weights summing 0.70... the exact value depends on the fixture, but it is not 0.30), and the indicator test raises nothing.

- [ ] **Step 3: Implement**

In `orchestrator_scaffold.py`, replace `RiskEngine.score`:

```python
    def score(self, signals: RiskSignals) -> float:
        total = 0.0
        for field_name, weight in self.weights.items():
            value = float(getattr(signals, field_name, 0.0))
            if value not in (0.0, 1.0):
                raise ValueError(
                    f"RiskSignals.{field_name}={value}: signals are indicators and must be "
                    f"0.0 or 1.0. Passing the weight ({weight}) squares it silently."
                )
            total += value * float(weight)
        return total
```

Add a docstring to `RiskSignals`, directly under the `class RiskSignals:` line:

```python
    """Risk trigger signals as 0.0/1.0 INDICATORS -- a signal fired or it did not.

    The weight for each signal lives in agent_orchestration.config.yaml under
    risk.signals; passing that weight here instead of 1.0 double-weights it.
    RiskEngine.score() sums value * weight, so the result is a RAW SUM whose
    bands are risk.thresholds. Adding a signal therefore shifts every band and
    the thresholds must be re-tuned in the same change.
    """
```

- [ ] **Step 4: Document the consequence beside the weights**

In `agent_orchestration.config.yaml`, directly above `risk:`:

```yaml
# Risk is a RAW WEIGHTED SUM of 0.0/1.0 indicator signals -- not a mean.
# Bands below map to roughly: 2 signals -> medium, 4 -> high (Best-of-3),
# 6 -> very_high (Best-of-5). Because it is a sum and not a mean, ADDING A
# SIGNAL SHIFTS EVERY BAND: re-tune these thresholds in the same change.
```

- [ ] **Step 5: Run the full suite**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `71 passed`

- [ ] **Step 6: Commit**

```bash
git add skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml skills/swarm-orchestration/custom_orchestration/tests/test_risk_engine.py
git commit -m "fix(orchestration): risk score is a raw sum with asserted indicators (D5, D6)"
```

---

### Task 4: Swarm review stops failing open (D2, D3)

`ReviewerPanel.execute_swarm` passes `preferred_provider=details.get("model_tier")` — the string `"sonnet-3.5"` into a parameter whose domain is provider ids — and returns perspective envelopes that carry no `severity`, which `calculate_verdict` then counts as all-`low`. Both paths end at `APPROVE`, silently.

**Files:**
- Modify: `skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml` (`model_routing`, `swarm_review.perspectives`)
- Modify: `skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py:322-370` (`ReviewerPanel`)
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_reviewer_panel.py` (new)

**Interfaces:**
- Consumes: `TriagePipeline.calculate_verdict(findings)` from Task 2.
- Produces: `ReviewerPanel.execute_swarm(diff_content, metadata) -> Dict[str, Any]` returning `{"findings": List[Dict], "failures": List[Dict], "lens_count": int}`. Each finding carries `severity` and `perspective`. `ReviewerPanel.all_lenses_failed(result) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reviewer_panel.py`:

```python
import pytest
from orchestrator_scaffold import Config, ReviewerPanel


class MockConfig(Config):
    def __init__(self, data):
        self.data = data


class StubExecutor:
    """Returns one finding per lens; records what it was called with."""

    def __init__(self, findings_by_agent=None, raiser=False):
        self.calls = []
        self.findings_by_agent = findings_by_agent or {}
        self.raiser = raiser

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.raiser:
            raise RuntimeError("provider exploded")

        class _Norm:
            structured_data = {
                "findings": self.findings_by_agent.get(kwargs["agent_id"], [])
            }

        class _Res:
            success = True
            normalized_response = _Norm()

        return _Res()


@pytest.fixture
def swarm_config():
    return MockConfig({
        "swarm_review": {
            "enabled": True,
            "timeout_seconds": 5,
            "perspectives": {
                "security_auditor": {"focus": "injection"},
                "xp_reviewer": {"focus": "naming"},
            },
        },
        "model_routing": {
            "tier_aliases": {"strong_mid_tier": "sonnet", "mechanical": "haiku"},
            "security_auditor": {"preferred_tier": "strong_mid_tier"},
            "xp_reviewer": {"preferred_tier": "mechanical"},
        },
    })


def test_never_passes_a_model_tier_as_a_provider(swarm_config):
    # D2: "sonnet-3.5" went into preferred_provider, whose domain is provider ids.
    stub = StubExecutor()
    ReviewerPanel(swarm_config, stub).execute_swarm("diff", {})
    assert stub.calls, "no lens was executed"
    for call in stub.calls:
        assert call.get("preferred_provider") is None
    tiers = {c["preferred_model"] for c in stub.calls}
    assert tiers == {"sonnet", "haiku"}


def test_returns_flat_findings_carrying_severity_and_perspective(swarm_config):
    # D3: envelopes had no `severity`, so calculate_verdict counted everything low.
    stub = StubExecutor(findings_by_agent={
        "swarm_security_auditor": [{"severity": "HIGH", "summary": "sqli"}],
        "swarm_xp_reviewer": [{"severity": "NIT", "summary": "naming"}],
    })
    result = ReviewerPanel(swarm_config, stub).execute_swarm("diff", {})
    assert len(result["findings"]) == 2
    assert {f["severity"] for f in result["findings"]} == {"HIGH", "NIT"}
    assert {f["perspective"] for f in result["findings"]} == {
        "security_auditor", "xp_reviewer"
    }


def test_total_lens_failure_is_detectable_not_silent_approve(swarm_config):
    panel = ReviewerPanel(swarm_config, StubExecutor(raiser=True))
    result = panel.execute_swarm("diff", {})
    assert result["findings"] == []
    assert len(result["failures"]) == 2
    assert panel.all_lenses_failed(result) is True


def test_partial_failure_is_not_total_failure(swarm_config):
    stub = StubExecutor(findings_by_agent={
        "swarm_security_auditor": [{"severity": "HIGH", "summary": "sqli"}],
    })
    panel = ReviewerPanel(swarm_config, stub)
    result = panel.execute_swarm("diff", {})
    assert panel.all_lenses_failed(result) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_reviewer_panel.py -v`
Expected: FAIL — `execute_swarm` returns a list, not a dict, so `result["findings"]` raises `TypeError`, and `preferred_provider` is populated.

- [ ] **Step 3: Add lens tiers to config, remove the duplicate model_tier**

In `agent_orchestration.config.yaml`, replace `swarm_review.perspectives` with:

```yaml
  perspectives:
    # model_tier deliberately absent: tiers are owned by model_routing (one home).
    security_auditor:
      agent: reviewer-security
      focus: "vulnerabilities, auth bypass, injection"
    performance_expert:
      agent: reviewer-performance
      focus: "n+1 queries, memory leaks, algorithmic complexity"
    xp_reviewer:
      agent: reviewer-xp
      focus: "naming, readability, DRY, small methods"
```

and extend `model_routing` with tier aliases plus a per-lens entry:

```yaml
model_routing:
  # Aliases, never dated model ids -- pinned ids are how sonnet-3.5/haiku-3 rotted.
  tier_aliases:
    flagship_reasoning: opus
    strong_mid_tier: sonnet
    strong_mid_tier_coder: sonnet
    mechanical: haiku

  architect:
    preferred_tier: flagship_reasoning
    fallback_tiers: [strong_mid_tier, local_glm]
  engineer:
    preferred_tier: strong_mid_tier_coder
    fallback_tiers: [flagship_reasoning, local_glm]
  reviewer:
    # Native Claude Code cannot honour a different FAMILY -- see SKILL.md section 6.
    preferred_tier: different_family_from_engineer
    fallback_tiers: [flagship_reasoning, strong_mid_tier]
  security_auditor:
    preferred_tier: strong_mid_tier
  performance_expert:
    preferred_tier: strong_mid_tier
  xp_reviewer:
    preferred_tier: mechanical
```

- [ ] **Step 4: Implement**

In `orchestrator_scaffold.py`, replace the whole `ReviewerPanel` class:

```python
class ReviewerPanel:
    def __init__(self, config: Config, provider_executor: ProviderExecutor):
        self.config = config
        self.provider_executor = provider_executor
        self.swarm_config = config.get("swarm_review", default={})
        self.model_routing = config.get("model_routing", default={})

    def _model_for_lens(self, lens: str) -> Optional[str]:
        aliases = self.model_routing.get("tier_aliases", {})
        tier = self.model_routing.get(lens, {}).get("preferred_tier")
        return aliases.get(tier, tier)

    def execute_swarm(self, diff_content: str, metadata: dict) -> Dict[str, Any]:
        """Run every lens and return FLAT findings.

        Returns {"findings": [...], "failures": [...], "lens_count": int}.
        Each finding carries `severity` and `perspective` -- the shape
        TriagePipeline.calculate_verdict consumes. Returning perspective
        envelopes instead made every finding count as `low` (D3).
        """
        perspectives = self.swarm_config.get("perspectives", {})
        if not self.swarm_config.get("enabled", True) or not perspectives:
            return {"findings": [], "failures": [], "lens_count": 0}

        import concurrent.futures

        findings: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_perspective = {}
            for name, details in perspectives.items():
                future = executor.submit(
                    self.provider_executor.execute,
                    role="reviewer",
                    agent_id=f"swarm_{name}",
                    system_prompt=f"You are a {name}. Focus on: {details.get('focus')}",
                    user_prompt=f"Review this diff:\n{diff_content}",
                    tools=[],
                    mcp_servers=[],
                    skill_ids=[],
                    metadata=metadata,
                    # A tier is not a provider id. Route the MODEL here and let
                    # role_provider_routing pick the provider (D2).
                    preferred_model=self._model_for_lens(name),
                )
                future_to_perspective[future] = name

            timeout = self.swarm_config.get("timeout_seconds", 600)
            done, not_done = concurrent.futures.wait(
                future_to_perspective.keys(), timeout=timeout
            )

            for future in done:
                name = future_to_perspective[future]
                try:
                    res = future.result()
                    raw = (res.normalized_response.structured_data or {}) \
                        if res.normalized_response else {}
                    for finding in raw.get("findings", []):
                        findings.append({**finding, "perspective": name})
                except Exception as exc:
                    failures.append({"perspective": name, "error": str(exc)})

            for future in not_done:
                failures.append(
                    {"perspective": future_to_perspective[future], "error": "timeout"}
                )

        return {
            "findings": findings,
            "failures": failures,
            "lens_count": len(perspectives),
        }

    def all_lenses_failed(self, result: Dict[str, Any]) -> bool:
        """True when no lens produced a verdict -- callers MUST NOT read that as APPROVE."""
        return result["lens_count"] > 0 and len(result["failures"]) >= result["lens_count"]
```

- [ ] **Step 5: Run the full suite**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `75 passed`

- [ ] **Step 6: Commit**

```bash
git add skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml skills/swarm-orchestration/custom_orchestration/tests/test_reviewer_panel.py
git commit -m "fix(orchestration): swarm review no longer fails open (D2, D3)"
```

---

### Task 5: Repo-wide prune becomes architect-only (D7)

`finalize_task_state()` calls `git.prune_worktrees(dry_run=False)` — a repository-wide operation — and runs **per agent**. With concurrent agents this is precisely the operation the orchestrator tax article bans.

**Files:**
- Modify: `skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py:539-580`
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_finalize_task_state.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Orchestrator.finalize_task_state(agent_id, status)` no longer prunes. New `Orchestrator.architect_teardown() -> Dict[str, Any]` returns `{"pruned": bool, "reason": str}` and refuses while any lock file exists.

- [ ] **Step 1: Write the failing test**

Create `tests/test_finalize_task_state.py`:

```python
import json
import pytest
from orchestrator_scaffold import Orchestrator


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "paths:\n"
        "  state_dir: .agent-state\n"
        "  checkpoint_dir: .agent-state/checkpoints\n"
        "  locks_dir: .agent-state/locks\n"
        "  worktrees_dir: .agent-state/worktrees\n"
        "risk: {thresholds: {low: 0.3, medium: 0.6, high: 0.85}, signals: {}}\n"
        "token_budget: {enabled: true, states: {green: 0.6, yellow: 0.9, red: 1.0}}\n"
        "cleanup: {remove_temp_worktrees_on_success: true, preserve_failed_worktrees: true,"
        " prune_stale_metadata: true}\n"
        # Orchestrator.__init__ builds a VerificationRunner from the raw dict.
        "verification: {}\n"
        "role_provider_routing: {}\n",
        encoding="utf-8",
    )
    return Orchestrator(str(cfg), str(tmp_path))


def test_finalize_does_not_prune_repo_wide(orch, monkeypatch):
    calls = []
    monkeypatch.setattr(orch.git, "prune_worktrees", lambda dry_run=False: calls.append(1))
    orch.finalize_task_state("agent-1", "success")
    assert calls == [], "finalize_task_state must not run a repo-wide prune (D7)"


def test_architect_teardown_refuses_while_a_lock_is_held(orch, monkeypatch):
    monkeypatch.setattr(orch.git, "prune_worktrees", lambda dry_run=False: "pruned")
    lock = orch.state.locks_dir / "src_events_py.lock"
    lock.write_text(json.dumps({"agent_id": "other-agent"}), encoding="utf-8")
    result = orch.architect_teardown()
    assert result["pruned"] is False
    assert "lock" in result["reason"].lower()


def test_architect_teardown_prunes_when_quiet(orch, monkeypatch):
    monkeypatch.setattr(orch.git, "prune_worktrees", lambda dry_run=False: "pruned")
    result = orch.architect_teardown()
    assert result["pruned"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_finalize_task_state.py -v`
Expected: FAIL — `test_finalize_does_not_prune_repo_wide` records a call; `architect_teardown` does not exist (`AttributeError`).

- [ ] **Step 3: Implement**

In `orchestrator_scaffold.py`, inside `finalize_task_state`, delete these three lines:

```python
        if cleanup_cfg.get("prune_stale_metadata", True):
            results["prune_result"] = self.git.prune_worktrees(dry_run=False)
```

and change the initial `results` dict so it no longer advertises a key it never sets:

```python
        results = {"status": status, "locks_released": 0, "worktree_removed": None}
```

Then add a new method to `Orchestrator`, directly after `finalize_task_state`:

```python
    def architect_teardown(self) -> Dict[str, Any]:
        """Repo-wide prune. ARCHITECT ONLY, and only when no agent is running.

        `git worktree prune` mutates state shared by every worktree, so a leaf
        agent running it mid-wave corrupts its peers. Held locks are the proxy
        for "someone is still working" (D7).
        """
        held = list(self.state.locks_dir.glob("*.lock"))
        if held:
            return {
                "pruned": False,
                "reason": f"refused: {len(held)} lock(s) still held: "
                          f"{[p.name for p in held]}",
            }
        self.git.prune_worktrees(dry_run=False)
        return {"pruned": True, "reason": "no locks held"}
```

- [ ] **Step 4: Run the full suite**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `78 passed`

- [ ] **Step 5: Commit**

```bash
git add skills/swarm-orchestration/custom_orchestration/orchestrator_scaffold.py skills/swarm-orchestration/custom_orchestration/tests/test_finalize_task_state.py
git commit -m "fix(orchestration): repo-wide prune is architect-only and lock-gated (D7)"
```

---

### Task 6: Wave caps and the concurrent-git deny list in config

Both are new facts and both need exactly one home before Tasks 7–9 consume them.

**Files:**
- Modify: `skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml`
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_config_contract.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `orchestration.wave_caps.context_sharing: int`, `orchestration.wave_caps.isolated_candidate: int`, `orchestration.forbidden_concurrent_git: List[str]` (regex strings).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_contract.py`:

```python
import re
from pathlib import Path
import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "agent_orchestration.config.yaml"


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_wave_caps_present_and_two_tier(cfg):
    caps = cfg["orchestration"]["wave_caps"]
    assert isinstance(caps["context_sharing"], int)
    assert isinstance(caps["isolated_candidate"], int)
    # Isolated candidates never enter the orchestrator's context, so their cap
    # is bound by cost rather than attention and may exceed the sharing cap.
    assert caps["isolated_candidate"] >= caps["context_sharing"]


def test_lens_count_fits_the_context_sharing_cap(cfg):
    lenses = cfg["swarm_review"]["perspectives"]
    assert len(lenses) <= cfg["orchestration"]["wave_caps"]["context_sharing"]


def test_forbidden_concurrent_git_patterns_compile(cfg):
    patterns = cfg["orchestration"]["forbidden_concurrent_git"]
    assert patterns
    for p in patterns:
        re.compile(p)


def test_forbidden_git_matches_the_operations_that_bit_us(cfg):
    patterns = [re.compile(p) for p in cfg["orchestration"]["forbidden_concurrent_git"]]
    for command in [
        "git worktree prune",
        "git gc --aggressive",
        "git reset --hard origin/main",
        "git clean -fd",
        "git checkout .",
        "git stash",
    ]:
        assert any(p.search(command) for p in patterns), command


def test_benign_git_is_not_blocked(cfg):
    patterns = [re.compile(p) for p in cfg["orchestration"]["forbidden_concurrent_git"]]
    for command in ["git status", "git add src/events.py", "git commit -m 'x'", "git diff"]:
        assert not any(p.search(command) for p in patterns), command


def test_every_perspective_has_a_model_routing_entry(cfg):
    aliases = cfg["model_routing"]["tier_aliases"]
    for lens in cfg["swarm_review"]["perspectives"]:
        tier = cfg["model_routing"][lens]["preferred_tier"]
        assert tier in aliases, f"{lens} tier {tier} has no alias"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_config_contract.py -v`
Expected: FAIL with `KeyError: 'orchestration'`.

- [ ] **Step 3: Add the config block**

Append to `agent_orchestration.config.yaml`:

```yaml
orchestration:
  # Two tiers because two different things bind them. A context-sharing wave
  # returns into the orchestrator's permanent working memory, so ATTENTION is
  # the constraint. An isolated candidate (worktree + schema + deterministic
  # arbitration) never enters that memory, so worker-tier COST is.
  wave_caps:
    context_sharing: 4
    isolated_candidate: 5

  # Repo-wide git mutates state shared by every worktree. One writer running
  # these mid-wave corrupts its peers, so they are blocked in subagent prompts.
  forbidden_concurrent_git:
    - "git\\s+worktree\\s+prune"
    - "git\\s+gc\\b"
    - "git\\s+reset\\s+--hard"
    - "git\\s+clean\\s+-[a-z]*[fd]"
    - "git\\s+checkout\\s+\\.(?:\\s|$)"
    - "git\\s+stash\\b"
```

- [ ] **Step 4: Run the full suite**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `84 passed`

- [ ] **Step 5: Commit**

```bash
git add skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml skills/swarm-orchestration/custom_orchestration/tests/test_config_contract.py
git commit -m "feat(orchestration): two-tier wave caps and concurrent-git deny list in config"
```

---

### Task 7: Generator emits the Claude agent definitions

Workflow scripts have no filesystem access and no `require`, so they cannot read the YAML at runtime. Generation is the only mechanism that keeps one source.

**Files:**
- Create: `skills/swarm-orchestration/tools/gen_claude_adapter.py`
- Create (generated): `.claude/agents/{architect,engineer,reviewer-security,reviewer-performance,reviewer-xp}.md`
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_gen_claude_adapter.py` (new)

**Interfaces:**
- Consumes: `model_routing.tier_aliases`, `model_routing.<role>.preferred_tier`, `roles.<role>`, `swarm_review.perspectives` (Tasks 4, 6).
- Produces: `render_agents(cfg: dict) -> Dict[str, str]` mapping relative path → file content. `main(argv) -> int` supporting `--check`. Constant `GENERATED_HEADER: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gen_claude_adapter.py`:

```python
import sys
from pathlib import Path
import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import gen_claude_adapter as gen  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "agent_orchestration.config.yaml"


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_emits_one_file_per_role_and_lens(cfg):
    files = gen.render_agents(cfg)
    assert set(files) == {
        ".claude/agents/architect.md",
        ".claude/agents/engineer.md",
        ".claude/agents/reviewer-security.md",
        ".claude/agents/reviewer-performance.md",
        ".claude/agents/reviewer-xp.md",
    }


def test_every_file_is_marked_generated(cfg):
    for content in gen.render_agents(cfg).values():
        assert "DO NOT EDIT" in content
        assert "agent_orchestration.config.yaml" in content


def test_model_tiers_are_aliases_never_dated_ids(cfg):
    files = gen.render_agents(cfg)
    assert "model: opus" in files[".claude/agents/architect.md"]
    assert "model: sonnet" in files[".claude/agents/engineer.md"]
    assert "model: haiku" in files[".claude/agents/reviewer-xp.md"]
    for content in files.values():
        assert "sonnet-3.5" not in content
        assert "haiku-3" not in content


def test_architect_cannot_write_code(cfg):
    architect = files_line(gen.render_agents(cfg)[".claude/agents/architect.md"], "tools:")
    assert "Edit" not in architect and "Write" not in architect


def test_engineer_can_write_code_and_gets_serena(cfg):
    engineer = files_line(gen.render_agents(cfg)[".claude/agents/engineer.md"], "tools:")
    assert "Edit" in engineer
    assert "mcp__serena__*" in engineer


def test_body_points_at_the_skill_and_restates_nothing(cfg):
    body = gen.render_agents(cfg)[".claude/agents/engineer.md"]
    assert "skills/swarm-orchestration/SKILL.md" in body
    # Numbers belong to the config; an agent file must not carry thresholds.
    assert "0.85" not in body


def files_line(content: str, prefix: str) -> str:
    for line in content.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no line starting with {prefix!r}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_gen_claude_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gen_claude_adapter'`.

- [ ] **Step 3: Implement the generator**

Create `skills/swarm-orchestration/tools/gen_claude_adapter.py`:

```python
#!/usr/bin/env python3
"""Generate the Claude Code adapter from agent_orchestration.config.yaml.

The config is the single source for every orchestration fact. Claude Code
workflow scripts have no filesystem access and no import, so they cannot read
the YAML at runtime -- generation is the only way to avoid a second, drifting
copy of the tier and lens lists. Run with --check in pre-commit.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = (
    REPO_ROOT / "skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml"
)

GENERATED_HEADER = (
    "<!-- DO NOT EDIT. Generated by skills/swarm-orchestration/tools/gen_claude_adapter.py\n"
    "     from skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml.\n"
    "     Change the config and regenerate; hand edits are reverted by --check. -->"
)

# The one fact this generator owns: MCP server name -> Claude Code tool names.
MCP_TOOL_MAP = {
    "serena": ["mcp__serena__*"],
    "graphify": ["mcp__graphify__*"],
    "omnigraph": ["mcp__omnigraph__*"],
    "context7": ["mcp__context7__*"],
    "playwright": ["mcp__playwright__*"],
    "superpowers": ["Skill"],
    "fetch": ["WebFetch"],
    "sentry": [],
    "datadog": [],
    "sequential_thinking": [],
}
BASE_TOOLS = ["Read", "Grep", "Glob"]
WRITE_TOOLS = ["Edit", "Write"]

SKILL_REF = "skills/swarm-orchestration/SKILL.md"


def _tier(cfg: dict, key: str) -> str:
    aliases = cfg["model_routing"]["tier_aliases"]
    tier = cfg["model_routing"][key]["preferred_tier"]
    return aliases.get(tier, tier)


def _tools_for(cfg: dict, role: str) -> str:
    role_cfg = cfg["roles"][role]
    tools = list(BASE_TOOLS)
    if role_cfg.get("can_write_code"):
        tools += WRITE_TOOLS
    for mcp in role_cfg.get("allowed_mcps", []):
        tools += MCP_TOOL_MAP.get(mcp, [])
    seen, ordered = set(), []
    for t in tools:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ", ".join(ordered)


def _agent_file(name: str, description: str, model: str, tools: str, body: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"model: {model}\n"
        f"tools: {tools}\n"
        "---\n\n"
        f"{GENERATED_HEADER}\n\n"
        f"{body}\n"
    )


def render_agents(cfg: dict) -> dict[str, str]:
    files: dict[str, str] = {}

    for role, description in (
        ("architect", "Plans, decomposes, scores risk, and arbitrates completion. Writes no application code."),
        ("engineer", "Implements scoped code changes inside an assigned contract. Never changes architecture."),
    ):
        files[f".claude/agents/{role}.md"] = _agent_file(
            name=role,
            description=description,
            model=_tier(cfg, role),
            tools=_tools_for(cfg, role),
            body=(
                f"Read `{SKILL_REF}` and follow the `@{role}` role exactly. That file is\n"
                "the source of truth for your duties, your must-nots, and the handoff contract.\n\n"
                "Every threshold, weight, tier, and matrix lives in\n"
                "`skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml`.\n"
                "Do not infer a number from anywhere else.\n\n"
                "Return structured findings, never a transcript, intermediate reasoning, or\n"
                "progress narration. See the skill's Subagent return contract."
            ),
        )

    for lens, details in cfg["swarm_review"]["perspectives"].items():
        agent_name = details["agent"]
        files[f".claude/agents/{agent_name}.md"] = _agent_file(
            name=agent_name,
            description=f"Review lens: {details['focus']}",
            model=_tier(cfg, lens),
            tools=_tools_for(cfg, "reviewer"),
            body=(
                f"You are the **{lens}** review lens. Focus strictly on: {details['focus']}.\n\n"
                f"Read `{SKILL_REF}` for the `@reviewer` role and `skills/qa-swarm/SKILL.md`\n"
                "for how findings are aggregated.\n\n"
                "Report each finding with a severity from `triage_policy.severities`, the file\n"
                "and line, a one-sentence summary, and a concrete failure scenario. Do not\n"
                "compute a verdict -- that is deterministic and happens outside you. Never\n"
                "approve on another agent's claim alone."
            ),
        )

    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    args = parser.parse_args(argv)

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    files = render_agents(cfg)

    drift = []
    for rel, content in sorted(files.items()):
        path = REPO_ROOT / rel
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == content:
            continue
        if args.check:
            drift.append("".join(difflib.unified_diff(
                current.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"{rel} (on disk)",
                tofile=f"{rel} (generated)",
            )))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {rel}")

    if drift:
        print("\n".join(drift), file=sys.stderr)
        print(f"\n{len(drift)} generated file(s) differ. Run without --check.", file=sys.stderr)
        return 1
    if args.check:
        print(f"ok: {len(files)} generated file(s) match the config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the generator and the tests**

```bash
python skills/swarm-orchestration/tools/gen_claude_adapter.py
cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q
```
Expected: five `wrote .claude/agents/*.md` lines, then `90 passed`.

- [ ] **Step 5: Verify --check detects a hand edit**

```bash
printf '\nhand edit\n' >> .claude/agents/engineer.md
python skills/swarm-orchestration/tools/gen_claude_adapter.py --check; echo "exit=$?"
python skills/swarm-orchestration/tools/gen_claude_adapter.py
python skills/swarm-orchestration/tools/gen_claude_adapter.py --check; echo "exit=$?"
```
Expected: first `--check` prints a diff and `exit=1`; after regeneration, `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add skills/swarm-orchestration/tools/gen_claude_adapter.py skills/swarm-orchestration/custom_orchestration/tests/test_gen_claude_adapter.py .claude/agents/
git commit -m "feat(orchestration): generate Claude role subagents from the config"
```

---

### Task 8: Generator emits the workflows

`agent(prompt, {schema})` forces a `StructuredOutput` tool call, so a subagent physically cannot return a transcript. That is the context firewall — enforced by construction rather than by discipline.

**Files:**
- Modify: `skills/swarm-orchestration/tools/gen_claude_adapter.py`
- Create (generated): `.claude/workflows/qa-swarm.js`, `.claude/workflows/best-of-n.js`
- Modify: `skills/swarm-orchestration/custom_orchestration/tests/test_gen_claude_adapter.py`

**Interfaces:**
- Consumes: `render_agents` and `GENERATED_HEADER` from Task 7; `triage_policy.verdict_matrix` from Task 2; `orchestration.wave_caps` from Task 6.
- Produces: `render_workflows(cfg: dict) -> Dict[str, str]`, `compile_verdict_js(matrix: list) -> str`, and `render_all(cfg) -> Dict[str, str]` combining agents and workflows. `main` switches to `render_all`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gen_claude_adapter.py`:

```python
def test_render_all_includes_both_workflows(cfg):
    files = gen.render_all(cfg)
    assert ".claude/workflows/qa-swarm.js" in files
    assert ".claude/workflows/best-of-n.js" in files


def test_verdict_js_preserves_matrix_order(cfg):
    js = gen.compile_verdict_js(cfg["triage_policy"]["verdict_matrix"])
    order = [
        js.index("'BLOCKED'"),
        js.index("'REQUEST_CHANGES'"),
        js.index("'APPROVE_WITH_NITS'"),
    ]
    assert order == sorted(order), "first-match-wins ordering was not preserved"


def test_qa_swarm_asserts_the_wave_cap(cfg):
    js = gen.render_all(cfg)[".claude/workflows/qa-swarm.js"]
    cap = cfg["orchestration"]["wave_caps"]["context_sharing"]
    assert f"WAVE_CAP = {cap}" in js
    assert "throw new Error" in js


def test_qa_swarm_binds_every_lens_to_its_generated_agent(cfg):
    js = gen.render_all(cfg)[".claude/workflows/qa-swarm.js"]
    for details in cfg["swarm_review"]["perspectives"].values():
        assert f"'{details['agent']}'" in js


def test_qa_swarm_uses_a_schema_and_logs_wave_cost(cfg):
    js = gen.render_all(cfg)[".claude/workflows/qa-swarm.js"]
    assert "schema: FINDINGS_SCHEMA" in js
    assert "budget.spent()" in js


def test_best_of_n_isolates_candidates_in_worktrees(cfg):
    js = gen.render_all(cfg)[".claude/workflows/best-of-n.js"]
    assert "isolation: 'worktree'" in js
    cap = cfg["orchestration"]["wave_caps"]["isolated_candidate"]
    assert f"MAX_CANDIDATES = {cap}" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_gen_claude_adapter.py -k workflow -v`
Expected: FAIL with `AttributeError: module 'gen_claude_adapter' has no attribute 'render_all'`.

- [ ] **Step 3: Implement the workflow renderers**

Append to `gen_claude_adapter.py`, before `main`:

```python
JS_HEADER = (
    "// DO NOT EDIT. Generated by skills/swarm-orchestration/tools/gen_claude_adapter.py\n"
    "// from skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml.\n"
)

_JS_OPS = {">=": ">=", "<=": "<=", "==": "===", ">": ">", "<": "<"}


def _clause_js(clause: dict) -> str:
    if not clause:
        return "true"
    parts = []
    for severity, expr in clause.items():
        for op in (">=", "<=", "==", ">", "<"):
            if expr.startswith(op):
                parts.append(f"c.{severity} {_JS_OPS[op]} {int(expr[len(op):])}")
                break
        else:
            raise ValueError(f"unparseable verdict condition: {severity}: {expr}")
    return " && ".join(parts)


def compile_verdict_js(matrix: list) -> str:
    """Compile the ordered matrix into an if-chain. Order IS the precedence."""
    lines = ["function verdictFor(c) {"]
    for rule in matrix:
        clauses = rule.get("any_of") or [rule.get("when", {})]
        cond = " || ".join(f"({_clause_js(x)})" for x in clauses)
        lines.append(f"  if ({cond}) return '{rule['verdict']}'")
    lines.append("  throw new Error('verdict_matrix has no terminal fall-through')")
    lines.append("}")
    return "\n".join(lines)


def render_workflows(cfg: dict) -> dict[str, str]:
    lenses = cfg["swarm_review"]["perspectives"]
    caps = cfg["orchestration"]["wave_caps"]
    severities = cfg["triage_policy"]["severities"]

    # Python's repr of a str/list is a valid JS literal, so it is used directly.
    lens_js = ",\n".join(
        f"  {{ key: {k!r}, agent: {v['agent']!r}, focus: {v['focus']!r} }}"
        for k, v in lenses.items()
    )

    qa = f"""export const meta = {{
  name: 'qa-swarm',
  description: 'Parallel lens review with schema-bounded findings and a deterministic verdict',
  phases: [
    {{ title: 'Review', detail: 'one agent per review lens' }},
    {{ title: 'Verify', detail: 'adversarially refute CRITICAL and HIGH findings' }},
  ],
}}
{JS_HEADER}
const LENSES = [
{lens_js}
]

// Context-sharing wave: findings enter the orchestrator's reasoning, so ATTENTION
// is the constraint. Consolidate lenses rather than raising this.
const WAVE_CAP = {caps['context_sharing']}
if (LENSES.length > WAVE_CAP) {{
  throw new Error(`qa-swarm has ${{LENSES.length}} lenses but the context-sharing wave cap is ${{WAVE_CAP}}`)
}}

const SEVERITIES = {severities!r}

const FINDINGS_SCHEMA = {{
  type: 'object',
  required: ['findings'],
  properties: {{
    findings: {{
      type: 'array',
      items: {{
        type: 'object',
        required: ['severity', 'file', 'summary', 'failure_scenario'],
        properties: {{
          severity: {{ type: 'string', enum: SEVERITIES }},
          file: {{ type: 'string' }},
          line: {{ type: 'integer' }},
          summary: {{ type: 'string' }},
          failure_scenario: {{ type: 'string' }},
        }},
      }},
    }},
  }},
}}

const VERDICT_SCHEMA = {{
  type: 'object',
  required: ['refuted', 'reason'],
  properties: {{
    refuted: {{ type: 'boolean' }},
    reason: {{ type: 'string' }},
  }},
}}

{compile_verdict_js(cfg['triage_policy']['verdict_matrix'])}

const before = budget.spent()

const perLens = await pipeline(
  LENSES,
  lens => agent(
    `Review the working diff through your lens only. Focus: ${{lens.focus}}.`,
    {{ label: `review:${{lens.key}}`, phase: 'Review', agentType: lens.agent, schema: FINDINGS_SCHEMA }}
  ),
  (result, lens) => {{
    const found = (result && result.findings) || []
    const serious = found.filter(f => f.severity === 'CRITICAL' || f.severity === 'HIGH')
    return parallel(serious.map(f => () =>
      agent(
        `Try to REFUTE this finding. Default to refuted=true if uncertain.\\n` +
        `${{f.severity}} in ${{f.file}}: ${{f.summary}}\\nScenario: ${{f.failure_scenario}}`,
        {{ label: `verify:${{f.file}}`, phase: 'Verify', agentType: lens.agent, schema: VERDICT_SCHEMA }}
      ).then(v => ({{ ...f, refuted: v ? v.refuted : true }}))
    )).then(checked => {{
      const byKey = new Map(checked.filter(Boolean).map(f => [`${{f.file}}:${{f.summary}}`, f]))
      return found
        .map(f => byKey.get(`${{f.file}}:${{f.summary}}`) || f)
        .filter(f => !f.refuted)
        .map(f => ({{ ...f, perspective: lens.key }}))
    }})
  }}
)

const findings = perLens.filter(Boolean).flat()
const lensesReporting = perLens.filter(Boolean).length

// A lens that died is NOT a clean lens. Failing open is the defect this replaces.
if (lensesReporting === 0) {{
  log('every review lens failed - reporting BLOCKED rather than approving by default')
  return {{ verdict: 'BLOCKED', reason: 'all lenses failed', findings: [] }}
}}

const counts = {{}}
for (const s of SEVERITIES) counts[s.toLowerCase()] = 0
for (const f of findings) {{
  const k = String(f.severity || '').toLowerCase()
  if (k in counts) counts[k] += 1
}}

const verdict = verdictFor(counts)
log(`qa-swarm: ${{lensesReporting}}/${{LENSES.length}} lenses, ${{findings.length}} findings, ` +
    `verdict ${{verdict}}, wave cost ${{budget.spent() - before}} output tokens`)

return {{ verdict, counts, findings, lensesReporting, lensTotal: LENSES.length }}
"""

    best = f"""export const meta = {{
  name: 'best-of-n',
  description: 'Run N isolated engineer candidates and arbitrate deterministically',
  phases: [
    {{ title: 'Implement', detail: 'one worktree-isolated candidate each' }},
    {{ title: 'Arbitrate', detail: 'rank by verification, no LLM tie-break' }},
  ],
}}
{JS_HEADER}
// Isolated-candidate wave: each candidate runs in its own worktree and returns a
// structured result, so it never enters the orchestrator's context. What bounds
// this is worker-tier COST, not attention -- hence a different cap from qa-swarm.
const MAX_CANDIDATES = {caps['isolated_candidate']}

const {{ n = 3, brief, verifyCommand }} = args || {{}}
if (!brief) throw new Error('best-of-n requires args.brief (the architecture contract excerpt)')
if (!verifyCommand) throw new Error('best-of-n requires args.verifyCommand for objective arbitration')
if (n > MAX_CANDIDATES) throw new Error(`n=${{n}} exceeds the isolated-candidate cap ${{MAX_CANDIDATES}}`)

const CANDIDATE_SCHEMA = {{
  type: 'object',
  required: ['passed', 'testsPassed', 'testsFailed', 'filesTouched', 'contractCompliant'],
  properties: {{
    passed: {{ type: 'boolean' }},
    testsPassed: {{ type: 'integer' }},
    testsFailed: {{ type: 'integer' }},
    filesTouched: {{ type: 'array', items: {{ type: 'string' }} }},
    contractCompliant: {{ type: 'boolean' }},
    notes: {{ type: 'string' }},
  }},
}}

const before = budget.spent()

// A barrier is correct here: arbitration needs every candidate at once to rank them.
const candidates = await parallel(
  Array.from({{ length: n }}, (_, i) => () =>
    agent(
      `Implement the contract below in your own worktree, then run \\`${{verifyCommand}}\\` ` +
      `and report the result.\\n\\n${{brief}}`,
      {{
        label: `candidate:${{i + 1}}`,
        phase: 'Implement',
        agentType: 'engineer',
        isolation: 'worktree',
        schema: CANDIDATE_SCHEMA,
      }}
    ).then(r => (r ? {{ ...r, candidate: i + 1 }} : null))
  )
)

const viable = candidates.filter(Boolean)
if (viable.length === 0) {{
  log('no candidate returned a result')
  return {{ winner: null, reason: 'all candidates failed', candidates: [] }}
}}

// arbitration_order: verification pass rate first, then contract compliance,
// then scope. Deterministic -- no model is asked to pick.
viable.sort((a, b) =>
  (b.testsPassed - b.testsFailed) - (a.testsPassed - a.testsFailed) ||
  Number(b.contractCompliant) - Number(a.contractCompliant) ||
  a.filesTouched.length - b.filesTouched.length
)

const winner = viable[0]
log(`best-of-n: ${{viable.length}}/${{n}} candidates returned, winner #${{winner.candidate}}, ` +
    `wave cost ${{budget.spent() - before}} output tokens`)

return {{ winner, candidates: viable }}
"""

    return {
        ".claude/workflows/qa-swarm.js": qa,
        ".claude/workflows/best-of-n.js": best,
    }


def render_all(cfg: dict) -> dict[str, str]:
    return {**render_agents(cfg), **render_workflows(cfg)}
```

Then change the one line in `main` that calls the renderer:

```python
    files = render_all(cfg)
```

- [ ] **Step 4: Regenerate and run the suite**

```bash
python skills/swarm-orchestration/tools/gen_claude_adapter.py
cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q
```
Expected: seven `wrote` lines, then `96 passed`.

- [ ] **Step 5: Sanity-check the emitted JavaScript parses**

```bash
node --check .claude/workflows/qa-swarm.js && node --check .claude/workflows/best-of-n.js && echo "both parse"
```
Expected: `both parse`. If `node` is unavailable on this host, skip and record the skip — the workflow runtime will surface a syntax error on first run.

- [ ] **Step 6: Commit**

```bash
git add skills/swarm-orchestration/tools/gen_claude_adapter.py skills/swarm-orchestration/custom_orchestration/tests/test_gen_claude_adapter.py .claude/workflows/
git commit -m "feat(orchestration): generate qa-swarm and best-of-n workflows with return schemas"
```

---

### Task 9: The concurrent-git hook and repository plumbing

Counting in-flight agents needs shared mutable state that races and leaks, so the wave cap stays a workflow assertion (Task 8). The repo-wide-git rule is genuinely stateless and is the one place a hook is correct.

**Files:**
- Create: `.claude/hooks/block-repo-wide-git.py`
- Create: `.claude/settings.json`
- Modify: `.gitattributes`
- Test: `skills/swarm-orchestration/custom_orchestration/tests/test_git_hook.py` (new)

**Interfaces:**
- Consumes: `orchestration.forbidden_concurrent_git` from Task 6.
- Produces: hook module exposing `check(prompt: str) -> Optional[str]` returning a block reason or `None`, and a `main()` reading a `PreToolUse` payload on stdin.

- [ ] **Step 1: Write the failing test**

Create `tests/test_git_hook.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / ".claude/hooks/block-repo-wide-git.py"
sys.path.insert(0, str(HOOK.parent))


@pytest.fixture(scope="module")
def hook():
    import importlib.util
    spec = importlib.util.spec_from_file_location("block_repo_wide_git", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("prompt", [
    "First run git worktree prune, then implement the feature",
    "clean up with git gc --aggressive",
    "git reset --hard origin/main before starting",
    "run git clean -fd",
])
def test_blocks_repo_wide_git(hook, prompt):
    assert hook.check(prompt) is not None


@pytest.mark.parametrize("prompt", [
    "Implement normalize_events() and run pytest",
    "git status to confirm, then git add src/events.py",
    "Commit with git commit -m 'feat: x'",
])
def test_allows_benign_prompts(hook, prompt):
    assert hook.check(prompt) is None


def test_cli_blocks_with_exit_2(hook):
    payload = json.dumps({
        "tool_name": "Agent",
        "tool_input": {"prompt": "please git worktree prune first"},
    })
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True
    )
    assert proc.returncode == 2
    assert "worktree" in (proc.stderr + proc.stdout)


def test_cli_allows_with_exit_0(hook):
    payload = json.dumps({
        "tool_name": "Agent",
        "tool_input": {"prompt": "implement the parser and run the tests"},
    })
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True
    )
    assert proc.returncode == 0


def test_ignores_non_agent_tools(hook):
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "git worktree prune"},
    })
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True
    )
    assert proc.returncode == 0, "the hook governs subagent prompts, not your own shell"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/test_git_hook.py -v`
Expected: FAIL — the hook file does not exist, so the fixture raises `FileNotFoundError`.

- [ ] **Step 3: Write the hook**

Create `.claude/hooks/block-repo-wide-git.py`:

```python
#!/usr/bin/env python3
"""PreToolUse hook: refuse subagent prompts that ask for repo-wide git.

`git worktree prune`, `git gc`, `git reset --hard` and friends mutate state
shared by every worktree. One agent running them mid-wave corrupts its peers.
This is checkable without state -- unlike counting in-flight agents, which is
why the wave cap is asserted inside the workflow instead of here.

Patterns are owned by agent_orchestration.config.yaml; this file holds none.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    REPO_ROOT / "skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml"
)

_PATTERNS: list[re.Pattern] | None = None


def _patterns() -> list[re.Pattern]:
    global _PATTERNS
    if _PATTERNS is None:
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        raw = cfg.get("orchestration", {}).get("forbidden_concurrent_git", [])
        _PATTERNS = [re.compile(p, re.IGNORECASE) for p in raw]
    return _PATTERNS


def check(prompt: str) -> str | None:
    """Return a block reason, or None when the prompt is acceptable."""
    for pattern in _patterns():
        match = pattern.search(prompt or "")
        if match:
            return (
                f"Blocked: this subagent prompt contains {match.group(0)!r}, a "
                "repository-wide git operation. Concurrent agents share the "
                "worktree state it mutates. Run it yourself after the wave "
                "finishes (Orchestrator.architect_teardown), not inside an agent."
            )
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Agent":
        return 0

    reason = check(payload.get("tool_input", {}).get("prompt", ""))
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Register the hook**

Create `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/block-repo-wide-git.py\""
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Pin JavaScript line endings**

Append to `.gitattributes`, directly under the `*.py` rule:

```
*.js            text eol=lf
```

- [ ] **Step 6: Run the full suite**

Run: `cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q`
Expected: `106 passed` (the two parametrized hook tests contribute 7 cases, not 2)

- [ ] **Step 7: Commit**

```bash
git add .claude/hooks/ .claude/settings.json .gitattributes skills/swarm-orchestration/custom_orchestration/tests/test_git_hook.py
git commit -m "feat(orchestration): PreToolUse hook blocks repo-wide git in subagent prompts"
```

---

### Task 10: Close the loop — pre-commit gate and doc status

Generated files rot the moment nothing checks them, and `SKILL.md` currently says the adapter is planned.

**Files:**
- Modify: `skills/swarm-orchestration/SKILL.md` (§16 table and closing paragraph)
- Modify: `docs/agent-compatibility.md` (adapter status row)
- Modify: `CHANGELOG.md`
- Create: `.githooks/pre-commit`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: `gen_claude_adapter.py --check` from Task 7.
- Produces: no code interfaces. Documentation reflects shipped state.

- [ ] **Step 1: Add the drift gate**

Create `.githooks/pre-commit`:

```sh
#!/bin/sh
# The .claude/ adapter is generated from agent_orchestration.config.yaml.
# Without this gate a hand edit survives until someone regenerates and
# silently reverts it.
set -e
python skills/swarm-orchestration/tools/gen_claude_adapter.py --check
```

- [ ] **Step 2: Verify the gate works**

```bash
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
sh .githooks/pre-commit && echo "gate passes"
```
Expected: `ok: 7 generated file(s) match the config` then `gate passes`.

- [ ] **Step 3: Document how to enable it**

Append to `CONTRIBUTING.md`:

```markdown
## Generated files

`.claude/agents/`, `.claude/workflows/` are generated from
`skills/swarm-orchestration/custom_orchestration/agent_orchestration.config.yaml`.
Never edit them by hand — change the config and regenerate:

```bash
python skills/swarm-orchestration/tools/gen_claude_adapter.py
```

Enable the drift gate once per clone:

```bash
git config core.hooksPath .githooks
```
```

- [ ] **Step 4: Flip the status in SKILL.md**

In `skills/swarm-orchestration/SKILL.md` §16, replace the third table row and the closing paragraph with:

```markdown
| Generated `.claude/` adapter — role subagents with model tiers, workflows carrying return schemas, a hook blocking repo-wide git in subagent prompts | §5, §6, §9 on the Claude Code native surface | live — generated by `tools/gen_claude_adapter.py`, drift-gated in pre-commit |

Best-of-N executes via `.claude/workflows/best-of-n.js`; the wave caps in §5 are asserted inside
the generated workflows, and §9's return contract is enforced by the response schemas they carry.
```

- [ ] **Step 5: Update the adapter status row**

In `docs/agent-compatibility.md`, change the `.claude/` row's status from **planned** to:

```markdown
| `.claude/agents/`, `.claude/workflows/`, `.claude/hooks/` | Claude Code native `Agent`/`Workflow` | live — generated from `agent_orchestration.config.yaml`, drift-gated; see [ADR 0004](decisions/0004-executable-orchestration-policy.md) |
```

- [ ] **Step 6: Record the change**

Add to `CHANGELOG.md` under `## [Unreleased]`:

```markdown
### Fixed — Orchestration: swarm review no longer fails open; policy is now enforced (2026-07-30)

Implements [ADR 0004](docs/decisions/0004-executable-orchestration-policy.md) and the
[design spec](docs/superpowers/specs/2026-07-30-orchestration-context-and-economics-design.md).

- **Swarm review failed open.** A model tier was passed where a provider id was expected, and
  `adapter_for()` sat outside its retry block, so the `KeyError` aborted the whole fallback chain;
  findings were returned in envelopes carrying no `severity`. Every review returned `APPROVE`,
  silently under `narration.mode: silent`. Fixed, with a total-lens-failure path that reports
  `BLOCKED` rather than approving by default.
- **Risk scoring** is the raw weighted sum the docs always described, with 0.0/1.0 indicators
  asserted. Best-of-3 is reachable for the first time — it previously needed ~10 of 11 signals.
- **The verdict matrix** is an ordered sequence in config, read at runtime and compiled into the
  generated workflow. The 1 HIGH + 2 MEDIUM precedence collision and the 1–2 MEDIUM hole are
  closed; the fall-through is now `APPROVE_WITH_NITS`, so those findings stop vanishing.
- **Repo-wide `git worktree prune`** moved out of the per-agent teardown into a lock-gated,
  architect-only `architect_teardown()`, and a `PreToolUse` hook blocks repo-wide git in subagent
  prompts.
- **The `.claude/` adapter is generated** from the config with a `--check` drift gate, so model
  tiers and review lenses have exactly one home.
```

- [ ] **Step 7: Run everything one last time**

```bash
cd skills/swarm-orchestration/custom_orchestration && python -m pytest tests/ -q
cd ../../.. && python skills/swarm-orchestration/tools/gen_claude_adapter.py --check
grep -nE '[0-9]+\.[0-9]{2}' skills/swarm-orchestration/SKILL.md || echo "no thresholds leaked into SKILL.md"
```
Expected: `106 passed` (the two parametrized hook tests contribute 7 cases, not 2), `ok: 7 generated file(s) match the config`, and the grep reporting no decimal thresholds.

- [ ] **Step 8: Commit**

```bash
git add .githooks/ CONTRIBUTING.md skills/swarm-orchestration/SKILL.md docs/agent-compatibility.md CHANGELOG.md
git commit -m "docs(orchestration): flip adapter status to live; add generated-file drift gate"
```

---

## Coverage against the spec

| Spec item | Task |
|---|---|
| D1 fallback chain | 1 |
| D2 tier-as-provider | 4 |
| D3 finding shape / fail-open | 4 |
| D4, D4a, D4b verdict matrix | 2 |
| D5 risk thresholds | 3 |
| D6 signal encoding | 3 |
| D7 repo-wide prune | 5 |
| D8 Best-of-N never executed | 8 |
| §4.1 generator with `--check` | 7, 10 |
| §4.2 role subagents with tiers | 7 |
| §4.3 qa-swarm workflow, schemas, wave cost log | 8 |
| §4.4 best-of-n workflow, worktree isolation | 8 |
| §4.5 two-tier wave caps | 6, 8 |
| §4.6 concurrent-git hook | 9 |
| §4.8 `.gitattributes` `*.js` | 9 |
| §4.8 `.gitignore` `settings.local.json` | already landed in commit `543c1f7` |
| §4.9 config schema changes | 2, 3, 4, 6 |
| §5 testing table | every task |
| §8 CHANGELOG + ADR | 10 (ADR 0004 already landed in `543c1f7`) |

**Deferred by design, per spec §6:** omnigraph run-telemetry (needs a cluster schema change and its own ADR), and Cursor's reconciler agent, megafile decomposer, Field Guide, and custom VCS.
