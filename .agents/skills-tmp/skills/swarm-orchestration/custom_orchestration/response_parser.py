import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class HandoffSignal:
    signal_type: str
    reason: str = ""
    confidence: float = 0.0
    blocking_issues: List[str] = field(default_factory=list)
    suggested_next_role: str = ""
    suggested_action: str = ""
    source: str = ""

@dataclass
class NormalizedResponse:
    kind: str  # 'plain_text', 'structured_json', 'tool_call', 'handoff_signal', 'mixed', 'unparseable'
    raw_payload: str
    text: str
    structured_data: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    handoff_signal: Optional[HandoffSignal] = None
    validation_errors: List[str] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)

class ResponseParser:
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("parser_policy", {})
        self.strict = self.config.get("strict_schema_validation", False)

    def parse(
        self, 
        raw_content: str, 
        provider_metadata: Dict[str, Any] = None,
        response_strategy: str = "plain_text",
        expected_schema: Optional[Dict[str, Any]] = None
    ) -> NormalizedResponse:
        """
        Parses raw text from a provider into a NormalizedResponse.
        Attempts JSON block extraction, tool call detection, and signal extraction.
        """
        if not raw_content or not raw_content.strip():
            return NormalizedResponse(kind="unparseable", raw_payload=raw_content, text="")

        # Try to parse as direct JSON first
        structured_data, is_json = self._extract_json(raw_content)
        
        kind = "plain_text"
        text = raw_content
        tool_calls = []
        handoff_signal = None
        validation_errors = []
        parse_warnings = []
        
        if response_strategy == "plain_text":
            return NormalizedResponse(
                kind="plain_text",
                raw_payload=raw_content,
                text=raw_content,
            )

        if is_json and structured_data:
            if response_strategy == "tool_calling":
                kind = "tool_call"
                if "name" in structured_data and "arguments" in structured_data:
                    tool_calls.append(structured_data)
                elif "tool_calls" in structured_data and isinstance(structured_data["tool_calls"], list):
                    tool_calls.extend(structured_data["tool_calls"])
                else:
                    parse_warnings.append("Expected tool calls but structure did not match known tool-call formats.")
                    kind = "mixed"
                text = structured_data.get("text", structured_data.get("content", raw_content))
            else:
                kind = "structured_json"
                # Check for handoff signals
                if "handoff_signal" in structured_data:
                    signal_data = structured_data["handoff_signal"]
                    if isinstance(signal_data, dict) and "signal_type" in signal_data:
                        kind = "handoff_signal"
                        handoff_signal = HandoffSignal(
                            signal_type=signal_data["signal_type"],
                            reason=signal_data.get("reason", ""),
                            confidence=float(signal_data.get("confidence", 1.0)),
                            blocking_issues=signal_data.get("blocking_issues", []),
                            suggested_next_role=signal_data.get("suggested_next_role", ""),
                            suggested_action=signal_data.get("suggested_action", ""),
                            source="structured_extraction"
                        )
                
                text = structured_data.get("text", structured_data.get("content", ""))
                
                # Validation against expected schema
                if expected_schema and self.strict:
                    # simplistic validation check - if expected keys are entirely missing
                    missing_keys = [k for k in expected_schema.get("properties", {}).keys() if k not in structured_data]
                    if missing_keys:
                        validation_errors.append(f"Schema validation failed. Missing keys: {missing_keys}")
                        kind = "unparseable" if not self.config.get("fallback_to_plain_text", True) else "mixed"
                
                if not text:
                    text = raw_content
        else:
            if response_strategy in ("strict_schema", "json_mode", "prompt_fallback"):
                validation_errors.append("Expected structured JSON but failed to parse valid JSON.")
                kind = "unparseable" if not self.config.get("fallback_to_plain_text", True) else "plain_text"
            else:
                parse_warnings.append("No valid JSON structure found; treated as plain text.")
            
        return NormalizedResponse(
            kind=kind,
            raw_payload=raw_content,
            text=text if isinstance(text, str) else str(text),
            structured_data=structured_data or {},
            tool_calls=tool_calls,
            handoff_signal=handoff_signal,
            validation_errors=validation_errors,
            parse_warnings=parse_warnings
        )

    def _extract_json(self, text: str) -> tuple[Optional[Dict[str, Any]], bool]:
        """Attempt to parse JSON directly or from markdown blocks."""
        try:
            return json.loads(text), True
        except json.JSONDecodeError:
            pass
            
        # Try finding a markdown block
        block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if block_match:
            try:
                return json.loads(block_match.group(1)), True
            except json.JSONDecodeError:
                pass
                
        return None, False
