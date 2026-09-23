import pytest
import json
from response_parser import ResponseParser, NormalizedResponse, HandoffSignal

def test_parse_plain_text():
    parser = ResponseParser({})
    resp = parser.parse("Just some plain text.")
    assert resp.kind == "plain_text"
    assert resp.text == "Just some plain text."
    assert not resp.structured_data
    assert not resp.handoff_signal

def test_parse_unparseable():
    parser = ResponseParser({})
    resp = parser.parse("   \n ")
    assert resp.kind == "unparseable"

def test_parse_strict_json_missing_keys():
    parser = ResponseParser({"parser_policy": {"strict_schema_validation": True, "fallback_to_plain_text": False}})
    json_str = json.dumps({"something_else": 123})
    # Since it's strict json missing keys but no expected_schema, it might pass as structured json if we don't pass expected schema.
    # Actually wait, test checks for missing keys, so it must use expected_schema and "strict_schema" strategy
    schema = {"properties": {"text": {}}}
    resp = parser.parse(json_str, response_strategy="strict_schema", expected_schema=schema)
    assert resp.kind == "unparseable"
    assert len(resp.validation_errors) > 0

def test_parse_strict_json_with_text():
    parser = ResponseParser({"parser_policy": {"strict_schema_validation": True}})
    json_str = json.dumps({"text": "Hello world", "extra": "field"})
    resp = parser.parse(json_str, response_strategy="structured_json")
    assert resp.kind == "structured_json"
    assert resp.text == "Hello world"
    assert resp.structured_data["extra"] == "field"

def test_parse_json_markdown_block():
    parser = ResponseParser({})
    content = "Here is my JSON:\n```json\n{\"text\": \"From block\"}\n```\nAnd more text."
    resp = parser.parse(content, response_strategy="json_mode")
    assert resp.kind == "structured_json"
    assert resp.text == "From block"
    assert resp.raw_payload == content

def test_parse_tool_call():
    parser = ResponseParser({})
    json_str = json.dumps({
        "text": "Using a tool",
        "tool_calls": [{"name": "my_tool", "arguments": {"foo": "bar"}}]
    })
    resp = parser.parse(json_str, response_strategy="tool_calling")
    assert resp.kind == "tool_call"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["name"] == "my_tool"

def test_parse_handoff_signal():
    parser = ResponseParser({})
    json_str = json.dumps({
        "handoff_signal": {
            "signal_type": "needs_architect",
            "reason": "Too hard",
            "confidence": 0.9
        }
    })
    resp = parser.parse(json_str, response_strategy="json_mode")
    assert resp.kind == "handoff_signal"
    assert resp.handoff_signal is not None
    assert resp.handoff_signal.signal_type == "needs_architect"

def test_parse_schema_validation():
    parser = ResponseParser({"parser_policy": {"strict_schema_validation": True, "fallback_to_plain_text": False}})
    json_str = json.dumps({"text": "response", "other": "value"})
    schema = {"properties": {"text": {}, "required_field": {}}}
    resp = parser.parse(json_str, response_strategy="strict_schema", expected_schema=schema)
    assert resp.kind == "unparseable"
    assert any("required_field" in err for err in resp.validation_errors)

def test_parse_malformed_json_fallback():
    parser = ResponseParser({})
    content = '{"text": "broken json"'
    resp = parser.parse(content, response_strategy="json_mode")
    assert resp.kind == "plain_text"
    assert len(resp.validation_errors) > 0
