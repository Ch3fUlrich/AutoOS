from __future__ import annotations

from typing import Any, Dict

from providers.base import AgentRequest, AgentResponse, ProviderAdapter
from providers.common import normalize_tool_names


class ClaudeCodeAdapter:
    name = "claude_code"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "native_checkpointing": True,
            "explicit_cache_control": True,
            "subagents": True,
            "skills": True,
            "mcp": True,
            "terminal": True,
            "structured_output": False,
            "strict_schema": False,
            "tool_calling": True,
            "json_mode": False,
            "prompt_json_fallback": True,
            "plain_text": True,
        }

    def supports_native_checkpointing(self) -> bool:
        return True

    def supports_explicit_cache_control(self) -> bool:
        return True

    def build_request(self, request: AgentRequest) -> Dict[str, Any]:
        system_prompt = request.system_prompt
        
        if request.allow_prompt_fallback and request.response_strategy == "prompt_fallback":
            from providers.common import get_prompt_fallback_instruction
            fallback_instruction = get_prompt_fallback_instruction(request.response_schema)
            system_prompt = (system_prompt or "") + fallback_instruction

        payload = {
            "model": request.model,
            "system": system_prompt,
            "prompt": request.user_prompt,
            "tools": normalize_tool_names(request.tools) if request.response_strategy == "tool_calling" else [],
            "mcp_servers": request.mcp_servers,
            "skills": request.skill_ids,
            "metadata": request.metadata,
            "response_strategy": request.response_strategy,
        }
        if request.cache_control:
            payload["cache_control"] = request.cache_control
        if request.checkpoint_hint:
            payload["checkpoint_hint"] = request.checkpoint_hint
        return payload

    def invoke(self, request: AgentRequest) -> AgentResponse:
        payload = self.build_request(request)
        return AgentResponse(
            agent_id=request.agent_id,
            role=request.role,
            status="stubbed",
            content="Claude Code adapter invocation placeholder.",
            raw=payload,
        )

    def resume(self, request: AgentRequest) -> AgentResponse:
        return self.invoke(request)