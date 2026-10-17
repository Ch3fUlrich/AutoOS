import pytest
from provider_executor import ProviderExecutor

def test_determine_response_strategy_tool_calling():
    executor = ProviderExecutor({})
    caps = {"tool_calling": True, "structured_output": True}
    tools = [{"name": "my_tool"}]
    expected_schema = None
    
    strategy = executor._determine_response_strategy(caps, tools, expected_schema)
    assert strategy == "tool_calling"

def test_determine_response_strategy_strict_schema():
    executor = ProviderExecutor({})
    caps = {"strict_schema": True, "json_mode": True, "prompt_json_fallback": True}
    tools = []
    expected_schema = {"properties": {"foo": {}}}
    
    strategy = executor._determine_response_strategy(caps, tools, expected_schema)
    assert strategy == "strict_schema"

def test_determine_response_strategy_json_mode():
    executor = ProviderExecutor({})
    caps = {"strict_schema": False, "json_mode": True, "prompt_json_fallback": True}
    tools = []
    expected_schema = {"properties": {"foo": {}}}
    
    strategy = executor._determine_response_strategy(caps, tools, expected_schema)
    assert strategy == "json_mode"

def test_determine_response_strategy_prompt_fallback():
    executor = ProviderExecutor({})
    caps = {"strict_schema": False, "json_mode": False, "prompt_json_fallback": True}
    tools = []
    expected_schema = {"properties": {"foo": {}}}
    
    strategy = executor._determine_response_strategy(caps, tools, expected_schema)
    assert strategy == "prompt_fallback"

def test_determine_response_strategy_plain_text():
    executor = ProviderExecutor({})
    caps = {"strict_schema": False, "json_mode": False, "prompt_json_fallback": False}
    tools = []
    expected_schema = {"properties": {"foo": {}}}
    
    strategy = executor._determine_response_strategy(caps, tools, expected_schema)
    assert strategy == "plain_text"

def test_determine_response_strategy_no_tools_no_schema():
    executor = ProviderExecutor({})
    caps = {"tool_calling": True, "strict_schema": True}
    tools = []
    expected_schema = None
    
    strategy = executor._determine_response_strategy(caps, tools, expected_schema)
    assert strategy == "plain_text"
