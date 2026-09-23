from __future__ import annotations

from typing import Any, Dict

from providers.base import AgentRequest, AgentResponse
from providers.common import normalize_tool_names


class CodexAdapter:
    name = "codex"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "native_checkpointing": False,
            "explicit_cache_control": False,
            "subagents": False,
            "skills": True,
            "mcp": True,
            "terminal": True,
            "structured_output": True,
            "strict_schema": True,
            "tool_calling": True,
            "json_mode": True,
            "prompt_json_fallback": True,
            "plain_text": True,
        }

    def supports_native_checkpointing(self) -> bool:
        return False

    def supports_explicit_cache_control(self) -> bool:
        return False

    def build_request(self, request: AgentRequest) -> Dict[str, Any]:
        # Maps to OpenAI Chat Completions payload
        messages = []
        system_prompt = request.system_prompt
        
        if request.allow_prompt_fallback and request.response_strategy == "prompt_fallback":
            from providers.common import get_prompt_fallback_instruction
            fallback_instruction = get_prompt_fallback_instruction(request.response_schema)
            system_prompt = (system_prompt or "") + fallback_instruction

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        payload = {
            "model": request.model or "gpt-4o",
            "messages": messages,
        }
        
        if request.response_strategy == "strict_schema" and request.response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_format_name or "structured_output",
                    "strict": True,
                    "schema": request.response_schema
                }
            }
        elif request.response_strategy == "json_mode":
            payload["response_format"] = {"type": "json_object"}

        if request.response_strategy == "tool_calling" and request.tool_schemas:
            payload["tools"] = [{"type": "function", "function": t} for t in request.tool_schemas]
            
        return payload

    def invoke(self, request: AgentRequest) -> AgentResponse:
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError
        from keys import KeyManager

        api_key = KeyManager.get("codex", "OPENAI_API_KEY", "OPENAI_API_KEY")
        if not api_key:
            return AgentResponse(
                agent_id=request.agent_id,
                role=request.role,
                status="failed",
                content="Missing OPENAI_API_KEY from agent_keys.yaml or environment for codex provider.",
            )

        base_url = KeyManager.get("codex", "OPENAI_BASE_URL", "OPENAI_BASE_URL") or "https://api.openai.com/v1"
        url = f"{base_url}/chat/completions"

        payload = self.build_request(request)
        data = json.dumps(payload).encode("utf-8")
        
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                
                content = ""
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"].get("content", "")
                
                usage = result.get("usage", {})
                
                return AgentResponse(
                    agent_id=request.agent_id,
                    role=request.role,
                    status="completed",
                    content=content,
                    token_usage={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                    raw=result,
                )
        except HTTPError as e:
            error_body = e.read().decode("utf-8")
            return AgentResponse(
                agent_id=request.agent_id,
                role=request.role,
                status="failed",
                content=f"HTTP Error {e.code}: {error_body}",
                raw=payload,
            )
        except URLError as e:
            return AgentResponse(
                agent_id=request.agent_id,
                role=request.role,
                status="failed",
                content=f"Network Error: {e.reason}",
                raw=payload,
            )

    def resume(self, request: AgentRequest) -> AgentResponse:
        return self.invoke(request)