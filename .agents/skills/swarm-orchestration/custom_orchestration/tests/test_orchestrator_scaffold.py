import pytest
from pathlib import Path
from typing import Any, Dict
from orchestrator_scaffold import Config, GateRunner, ReviewerPanel, TriagePipeline, BabysitState, Narrator

class MockConfig(Config):
    def __init__(self, data: Dict[str, Any]):
        self.data = data

@pytest.fixture
def base_config():
    return MockConfig({
        "safety_gates": {
            "enabled": True,
            "fail_closed": True,
            "size_ceiling": {
                "max_files_changed": 5,
                "max_lines_changed": 100,
                "exemptions": [".*\\.md$"]
            },
            "deny_list": {
                "auth": "(?i)(auth|login)",
                "crypto": "(?i)(crypto|hash)",
                "migrations": "(?i)(migrations)"
            }
        },
        "triage_policy": {
            "human_participation_gate": {"enabled": True},
        },
        "narration": {
            "mode": "chatty",
            "bluf_required": True,
            "attribute_findings": True,
            "prefix_autofixable": "[AUTO]"
        }
    })

# --- GateRunner Tests (10 tests) ---
def test_gate_runner_disabled():
    cfg = MockConfig({"safety_gates": {"enabled": False}})
    runner = GateRunner(cfg)
    assert runner.run_gates({}, ["auth.py"]) == {"passed": True}

def test_gate_runner_auth_deny(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({}, ["src/auth.py"])
    assert not res["passed"]
    assert res["reason"] == "DENY_LIST_MATCH"
    assert res["category"] == "auth"

def test_gate_runner_crypto_deny(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({}, ["lib/crypto_utils.go"])
    assert not res["passed"]
    assert res["category"] == "crypto"

def test_gate_runner_migrations_deny(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({}, ["db/migrations/001.sql"])
    assert not res["passed"]
    assert res["category"] == "migrations"

def test_gate_runner_pass_deny_list(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({}, ["src/main.py"])
    assert res["passed"]

def test_gate_runner_size_ceiling_files_exceeded(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({"lines_changed": 50}, ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"])
    assert not res["passed"]
    assert res["reason"] == "SIZE_CEILING_EXCEEDED"
    assert res["metric"] == "files"

def test_gate_runner_size_ceiling_lines_exceeded(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({"lines_changed": 150}, ["a.py"])
    assert not res["passed"]
    assert res["reason"] == "SIZE_CEILING_EXCEEDED"
    assert res["metric"] == "lines"

def test_gate_runner_size_ceiling_exemptions(base_config):
    runner = GateRunner(base_config)
    # 6 files, but 2 are markdown (exempt) -> 4 non-exempt files, should pass files limit
    res = runner.run_gates({"lines_changed": 50}, ["a.py", "b.py", "c.py", "d.py", "docs1.md", "docs2.md"])
    assert res["passed"]

def test_gate_runner_size_ceiling_lines_pass(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({"lines_changed": 99}, ["a.py"])
    assert res["passed"]

def test_gate_runner_fail_closed_invariant(base_config):
    runner = GateRunner(base_config)
    res = runner.run_gates({"lines_changed": 150}, ["auth.py"])
    # Should catch deny list first or size, but must fail
    assert not res["passed"]

# --- TriagePipeline Tests (10 tests) ---
def test_triage_human_gate_present(base_config):
    pipeline = TriagePipeline(base_config)
    comments = [{"is_bot": True}, {"is_bot": False}, {"is_bot": True}]
    assert pipeline.is_human_participating(comments)

def test_triage_human_gate_absent(base_config):
    pipeline = TriagePipeline(base_config)
    comments = [{"is_bot": True}, {"is_bot": True}]
    assert not pipeline.is_human_participating(comments)

def test_triage_verdict_critical(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "CRITICAL"}, {"severity": "LOW"}]
    assert pipeline.calculate_verdict(findings) == "BLOCKED"

def test_triage_verdict_2_high(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "HIGH"}, {"severity": "HIGH"}]
    assert pipeline.calculate_verdict(findings) == "REQUEST_CHANGES"

def test_triage_verdict_1_high_2_medium(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "HIGH"}, {"severity": "MEDIUM"}, {"severity": "MEDIUM"}]
    assert pipeline.calculate_verdict(findings) == "REQUEST_CHANGES"

def test_triage_verdict_1_high(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "HIGH"}, {"severity": "LOW"}]
    assert pipeline.calculate_verdict(findings) == "APPROVE_WITH_NITS"

def test_triage_verdict_3_medium(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "MEDIUM"}, {"severity": "MEDIUM"}, {"severity": "MEDIUM"}]
    assert pipeline.calculate_verdict(findings) == "APPROVE_WITH_NITS"

def test_triage_verdict_approve_low(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "LOW"}, {"severity": "LOW"}]
    assert pipeline.calculate_verdict(findings) == "APPROVE"

def test_triage_verdict_approve_nit(base_config):
    pipeline = TriagePipeline(base_config)
    findings = [{"severity": "NIT"}]
    assert pipeline.calculate_verdict(findings) == "APPROVE"

def test_triage_verdict_approve_empty(base_config):
    pipeline = TriagePipeline(base_config)
    assert pipeline.calculate_verdict([]) == "APPROVE"

# --- Narrator Tests (5 tests) ---
def test_narrator_chatty(base_config):
    narrator = Narrator(base_config)
    findings = [{"description": "Fix typo", "autofixable": True, "perspective": "XP"}]
    out = narrator.format_review_comment("APPROVE_WITH_NITS", findings)
    assert "**Verdict:** APPROVE_WITH_NITS" in out
    assert "[AUTO] *(From XP)* Fix typo" in out

def test_narrator_silent_approve():
    cfg = MockConfig({"narration": {"mode": "silent"}})
    narrator = Narrator(cfg)
    findings = [{"description": "Fix typo"}]
    assert narrator.format_review_comment("APPROVE", findings) is None

def test_narrator_silent_approve_with_nits():
    cfg = MockConfig({"narration": {"mode": "silent"}})
    narrator = Narrator(cfg)
    findings = [{"description": "Fix typo"}]
    assert narrator.format_review_comment("APPROVE_WITH_NITS", findings) is None

def test_narrator_silent_blocked():
    cfg = MockConfig({"narration": {"mode": "silent", "bluf_required": True}})
    narrator = Narrator(cfg)
    findings = [{"description": "Bad"}]
    out = narrator.format_review_comment("BLOCKED", findings)
    assert out is not None
    assert "BLOCKED" in out

def test_narrator_no_attribution():
    cfg = MockConfig({"narration": {"mode": "chatty", "attribute_findings": False, "bluf_required": False}})
    narrator = Narrator(cfg)
    findings = [{"description": "Bad", "perspective": "Sec"}]
    out = narrator.format_review_comment("REQUEST_CHANGES", findings)
    assert "From Sec" not in out
    assert "Bad" in out

# --- BabysitState Tests (5 tests) ---
class MockStateStore:
    def __init__(self, tmp_path):
        self.state_dir = tmp_path

def test_babysit_state_success(tmp_path):
    store = MockStateStore(tmp_path)
    cfg = MockConfig({"evidence_bundle": {"max_retries_per_failure": 2}})
    state = BabysitState(cfg, store)
    res = state.track_ci_status("pr1", "success")
    assert res["state"]["last_status"] == "success"
    assert not res["exceeds_retries"]

def test_babysit_state_failed_once(tmp_path):
    store = MockStateStore(tmp_path)
    cfg = MockConfig({"evidence_bundle": {"max_retries_per_failure": 2, "capture_logs": True}})
    state = BabysitState(cfg, store)
    res = state.track_ci_status("pr1", "failed", "log error")
    assert res["state"]["last_status"] == "failed"
    assert res["state"]["retries"] == 1
    assert "log error" in res["state"]["evidence_bundles"]
    assert not res["exceeds_retries"]

def test_babysit_state_failed_exceeds(tmp_path):
    store = MockStateStore(tmp_path)
    cfg = MockConfig({"evidence_bundle": {"max_retries_per_failure": 1}})
    state = BabysitState(cfg, store)
    state.track_ci_status("pr2", "failed")
    res = state.track_ci_status("pr2", "failed")
    assert res["exceeds_retries"]
    assert res["state"]["retries"] == 2

def test_babysit_state_running(tmp_path):
    store = MockStateStore(tmp_path)
    state = BabysitState(MockConfig({}), store)
    res = state.track_ci_status("pr3", "running")
    assert res["state"]["last_status"] == "running"
    assert "retries" not in res["state"]

def test_babysit_state_logs_capture_disabled(tmp_path):
    store = MockStateStore(tmp_path)
    cfg = MockConfig({"evidence_bundle": {"capture_logs": False}})
    state = BabysitState(cfg, store)
    res = state.track_ci_status("pr4", "failed", "log error")
    assert "evidence_bundles" not in res["state"]
