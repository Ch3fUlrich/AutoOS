from __future__ import annotations

from typing import Any, Dict
import json
import urllib.request
from urllib.error import HTTPError, URLError

from providers.base import AgentRequest, AgentResponse
from keys import KeyManager

class OllamaAdapter:
    name = "ollama"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "native_checkpointing": False,
            "explicit_cache_control": False,
            "subagents": False,
            "skills": False,
            "mcp": True,
            "terminal": False,
            "local_runtime": True,
            "structured_output": False,
            "strict_schema": False,
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
        messages = []
        system_prompt = request.system_prompt
        
        if request.allow_prompt_fallback and request.response_strategy == "prompt_fallback":
            from providers.common import get_prompt_fallback_instruction
            fallback_instruction = get_prompt_fallback_instruction(request.response_schema)
            system_prompt = (system_prompt or "") + fallback_instruction

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        # Use yaml default if provided, else request.model, else llama3
        default_model = KeyManager.get("ollama", "DEFAULT_MODEL") or "llama3"
        model = request.model
        if not model or "-class" in model:
            model = default_model

        payload = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        
        if request.response_strategy in ("json_mode", "strict_schema", "structured_json"):
            payload["format"] = "json"
            
        if request.response_strategy == "tool_calling" and request.tool_schemas:
            payload["tools"] = [{"type": "function", "function": t} for t in request.tool_schemas]
            
        return payload

    def invoke(self, request: AgentRequest) -> AgentResponse:
        base_url = KeyManager.get("ollama", "OLLAMA_BASE_URL", "OLLAMA_BASE_URL") or "http://localhost:11434"
        url = f"{base_url}/api/chat"

        payload = self.build_request(request)
        data = json.dumps(payload).encode("utf-8")
        
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                
                content = result.get("message", {}).get("content", "")
                
                return AgentResponse(
                    agent_id=request.agent_id,
                    role=request.role,
                    status="completed",
                    content=content,
                    token_usage={
                        "prompt_tokens": result.get("prompt_eval_count", 0),
                        "completion_tokens": result.get("eval_count", 0),
                        "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
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
                content=f"Network Error: Ollama unreachable at {base_url}. {e.reason}",
                raw=payload,
            )

    def resume(self, request: AgentRequest) -> AgentResponse:
        return self.invoke(request)
