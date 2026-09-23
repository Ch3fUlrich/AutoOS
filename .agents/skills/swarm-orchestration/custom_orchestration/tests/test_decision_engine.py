import pytest
from decision_engine import DecisionEngine, HandoffDecision
from response_parser import NormalizedResponse, HandoffSignal

def get_config(bypass=True, fast_fail=True, require_human=False, low_conf=False):
    return {
        "handoff_policy": {
            "allow_reviewer_bypass": bypass,
            "escalate_verification_failures_immediately": fast_fail,
            "require_human_on_blocked": require_human,
            "allow_low_confidence_text_escalation": low_conf
        }
    }

def test_architect_bypasses_eval():
    engine = DecisionEngine(get_config())
    nr = NormalizedResponse("plain_text", "hello", "hello")
    dec = engine.evaluate("architect", None, nr)
    assert dec.decision == "continue"
    assert not dec.architect_required
    assert not dec.reviewer_required

def test_green_verification_standard_flow():
    engine = DecisionEngine(get_config())
    nr = NormalizedResponse("plain_text", "all good", "all good")
    bundle = {"status": "passed"}
    dec = engine.evaluate("engineer", bundle, nr)
    assert dec.decision == "continue"
    assert dec.reviewer_required
    assert not dec.architect_required

def test_failed_verification_escalates():
    engine = DecisionEngine(get_config(bypass=True, fast_fail=True))
    nr = NormalizedResponse("plain_text", "all good", "all good")
    bundle = {"status": "failed"}
    dec = engine.evaluate("engineer", bundle, nr)
    assert dec.decision == "escalate"
    assert not dec.reviewer_required
    assert dec.architect_required
    assert "verification_bundle" in dec.evidence_sources

def test_failed_verification_respects_no_bypass_policy():
    engine = DecisionEngine(get_config(bypass=False, fast_fail=True))
    nr = NormalizedResponse("plain_text", "all good", "all good")
    bundle = {"status": "failed"}
    dec = engine.evaluate("engineer", bundle, nr)
    assert dec.decision == "continue"  # Continue to Reviewer
    assert dec.reviewer_required
    assert not dec.architect_required
    assert dec.policy_rule == "forbid_reviewer_bypass"

def test_explicit_cannot_complete_escalates():
    engine = DecisionEngine(get_config(bypass=True))
    nr = NormalizedResponse("handoff_signal", "payload", "payload")
    nr.handoff_signal = HandoffSignal(signal_type="cannot_complete", reason="Missing API")
    dec = engine.evaluate("engineer", None, nr)
    assert dec.decision == "escalate"
    assert not dec.reviewer_required
    assert dec.architect_required

def test_explicit_blocked():
    engine = DecisionEngine(get_config(bypass=True, require_human=False))
    nr = NormalizedResponse("handoff_signal", "payload", "payload")
    nr.handoff_signal = HandoffSignal(signal_type="blocked", reason="Dependency missing")
    dec = engine.evaluate("engineer", None, nr)
    assert dec.decision == "block"
    assert not dec.reviewer_required

def test_blocked_with_human_review_policy():
    engine = DecisionEngine(get_config(bypass=True, require_human=True))
    nr = NormalizedResponse("handoff_signal", "payload", "payload")
    nr.handoff_signal = HandoffSignal(signal_type="blocked", reason="Dependency missing")
    dec = engine.evaluate("engineer", None, nr)
    assert dec.decision == "human_review"
    assert dec.human_review_required

def test_low_confidence_escalation():
    engine = DecisionEngine(get_config(low_conf=True))
    nr = NormalizedResponse("plain_text", "i am blocked by something", "i am blocked by something")
    dec = engine.evaluate("engineer", None, nr)
    assert dec.decision == "escalate"
    assert dec.architect_required
    
def test_low_confidence_ignored_by_default():
    engine = DecisionEngine(get_config(low_conf=False))
    nr = NormalizedResponse("plain_text", "i am blocked by something", "i am blocked by something")
    dec = engine.evaluate("engineer", None, nr)
    assert dec.decision == "continue"
    assert dec.reviewer_required
