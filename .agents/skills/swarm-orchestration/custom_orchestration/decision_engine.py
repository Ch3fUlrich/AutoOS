from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from response_parser import NormalizedResponse

@dataclass
class HandoffDecision:
    decision: str  # 'continue', 'escalate', 'block', 'fail', 'human_review'
    reviewer_required: bool = True
    architect_required: bool = False
    human_review_required: bool = False
    reason: str = ""
    evidence_sources: List[str] = field(default_factory=list)
    policy_rule: str = ""

class DecisionEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("handoff_policy", {})

    def evaluate(self, role: str, verification_bundle: Optional[Dict[str, Any]], normalized_response: NormalizedResponse) -> HandoffDecision:
        # Defaults
        decision = "continue"
        reviewer_required = True
        architect_required = False
        human_review_required = False
        reason = "Standard flow."
        evidence_sources = []
        policy_rule = "default"
        
        # Don't try to route architect outputs via engineer rules
        if role == "architect":
            return HandoffDecision("continue", False, False, False, "Architect completed", [], "default")

        # 1. Verification Evidence Precedence
        if verification_bundle and verification_bundle.get("status") == "failed":
            evidence_sources.append("verification_bundle")
            if self.config.get("escalate_verification_failures_immediately", True):
                decision = "escalate"
                reviewer_required = False
                architect_required = True
                reason = "Verification critically failed; escalating to Architect to rescope before Review."
                policy_rule = "escalate_verification_failures_immediately"
                # If policy says do not bypass reviewer, we enforce that
                if not self.config.get("allow_reviewer_bypass", True):
                    decision = "continue"
                    reviewer_required = True
                    architect_required = False
                    reason = "Verification failed, but policy forbids Reviewer bypass."
                    policy_rule = "forbid_reviewer_bypass"
                return HandoffDecision(decision, reviewer_required, architect_required, human_review_required, reason, evidence_sources, policy_rule)

        # 2. Validated structured handoff signal
        if normalized_response.handoff_signal:
            signal = normalized_response.handoff_signal
            evidence_sources.append("normalized_response.handoff_signal")
            
            if signal.signal_type in ["needs_architect", "cannot_complete"]:
                decision = "escalate"
                reviewer_required = False
                architect_required = True
                reason = f"Agent explicitly signaled: {signal.signal_type}. Reason: {signal.reason}"
                policy_rule = "explicit_escalation_signal"
                
                if not self.config.get("allow_reviewer_bypass", True):
                    decision = "continue"
                    reviewer_required = True
                    architect_required = False
                    reason = "Agent signaled escalation, but policy forbids Reviewer bypass."
                    policy_rule = "forbid_reviewer_bypass"
                return HandoffDecision(decision, reviewer_required, architect_required, human_review_required, reason, evidence_sources, policy_rule)
                
            elif signal.signal_type == "blocked":
                decision = "block"
                reviewer_required = False
                reason = f"Agent is blocked. Reason: {signal.reason}"
                if self.config.get("require_human_on_blocked", False):
                    decision = "human_review"
                    human_review_required = True
                    policy_rule = "require_human_on_blocked"
                else:
                    policy_rule = "block_signal"
                return HandoffDecision(decision, reviewer_required, architect_required, human_review_required, reason, evidence_sources, policy_rule)

        # 3. Plain text fallback inference (low confidence)
        if normalized_response.kind in ["plain_text", "mixed", "unparseable"]:
            text = normalized_response.text.lower()
            if "i am blocked" in text or "cannot complete" in text:
                if self.config.get("allow_low_confidence_text_escalation", False):
                    decision = "escalate"
                    reviewer_required = False
                    architect_required = True
                    reason = "Low-confidence text inference suggests agent is blocked."
                    evidence_sources.append("plain_text_inference")
                    policy_rule = "allow_low_confidence_text_escalation"

        return HandoffDecision(decision, reviewer_required, architect_required, human_review_required, reason, evidence_sources, policy_rule)
