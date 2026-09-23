from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class AgentRequest:
    agent_id: str
    role: str
    model: str
    system_prompt: str
    user_prompt: str
    tools: List[Dict[str, Any]] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)
    skill_ids: List[str] = field(default_factory=list)
    working_directory: Optional[str] = None
    branch: Optional[str] = None
    worktree: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    cache_control: Dict[str, Any] = field(default_factory=dict)
    checkpoint_hint: Dict[str, Any] = field(default_factory=dict)
    response_strategy: str = "plain_text"
    response_schema: Optional[Dict[str, Any]] = None
    tool_schemas: Optional[List[Dict[str, Any]]] = None
    allow_prompt_fallback: bool = False
    response_format_name: Optional[str] = None


@dataclass
class AgentResponse:
    agent_id: str
    role: str
    status: str
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    token_usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    name: str

    def capabilities(self) -> Dict[str, Any]:
        """
        Returns a dictionary of provider capabilities:
        - supports_structured_output: bool
        - supports_strict_schema: bool
        - supports_tool_calling: bool
        - supports_json_mode: bool
        - supports_prompt_json_fallback: bool
        - supports_plain_text: bool
        """
        ...

    def build_request(self, request: AgentRequest) -> Dict[str, Any]:
        ...

    def invoke(self, request: AgentRequest) -> AgentResponse:
        ...

    def resume(self, request: AgentRequest) -> AgentResponse:
        ...

    def supports_native_checkpointing(self) -> bool:
        ...

    def supports_explicit_cache_control(self) -> bool:
        ...

    def normalize_response(self, raw_content: str) -> Dict[str, Any]:
        """
        Parses raw text from the model into a structured format (e.g. extracting tool calls or JSON blocks).
        By default, returns a dictionary with 'text' containing the raw content to preserve plain-text behavior.
        """
        return {"text": raw_content}