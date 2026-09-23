import pytest
from providers.common import (
    normalize_tool_names,
    make_cache_metadata,
    make_checkpoint_metadata,
    get_prompt_fallback_instruction,
)


def test_normalize_tool_names_empty():
    assert normalize_tool_names([]) == []


def test_normalize_tool_names_missing_optional():
    tools = [
        {"name": "tool_1"},
        {"name": "tool_2", "description": "desc 2"}
    ]
    expected = [
        {"name": "tool_1", "description": "", "schema": {}},
        {"name": "tool_2", "description": "desc 2", "schema": {}},
    ]
    assert normalize_tool_names(tools) == expected


def test_normalize_tool_names_fully_populated():
    tools = [
        {
            "name": "tool_full",
            "description": "Full tool",
            "schema": {"type": "object", "properties": {"prop1": {"type": "string"}}},
        }
    ]
    expected = [
        {
            "name": "tool_full",
            "description": "Full tool",
            "schema": {"type": "object", "properties": {"prop1": {"type": "string"}}},
        }
    ]
    assert normalize_tool_names(tools) == expected


def test_make_cache_metadata():
    result = make_cache_metadata(enabled=True, stable_blocks=["block1", "block2"])
    expected = {
        "enabled": True,
        "stable_blocks": ["block1", "block2"],
    }
    assert result == expected


def test_make_checkpoint_metadata():
    result = make_checkpoint_metadata(
        agent_id="agent_123", task_id="task_456", branch="main", worktree="/tmp/worktree"
    )
    expected = {
        "agent_id": "agent_123",
        "task_id": "task_456",
        "branch": "main",
        "worktree": "/tmp/worktree",
    }
    assert result == expected


def test_get_prompt_fallback_instruction_no_schema():
    instruction = get_prompt_fallback_instruction()
    assert "--- OUTPUT FORMAT INSTRUCTIONS ---" in instruction
    assert "You must respond with valid JSON only." in instruction
    assert "Follow this schema exactly:" not in instruction


def test_get_prompt_fallback_instruction_with_schema():
    schema = {"type": "object", "properties": {"field": {"type": "string"}}}
    instruction = get_prompt_fallback_instruction(schema)
    assert "--- OUTPUT FORMAT INSTRUCTIONS ---" in instruction
    assert "Follow this schema exactly:" in instruction
    assert '"field"' in instruction
