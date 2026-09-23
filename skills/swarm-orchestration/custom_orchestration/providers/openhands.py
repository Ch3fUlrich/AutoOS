from __future__ import annotations

from typing import Any, Dict

from providers.base import AgentRequest, AgentResponse
from providers.common import normalize_tool_names


class OpenHandsAdapter:
    name = "openhands"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "native_checkpointing": False,
            "explicit_cache_control": False,
            "subagents": False,
            "skills": False,
            "mcp": True,
            "terminal": True,
            "browser": True,
            "structured_output": False,
            "strict_schema": False,
            "tool_calling": True,
            "json_mode": False,
            "prompt_json_fallback": True,
            "plain_text": True,
        }

    def supports_native_checkpointing(self) -> bool:
        return False

    def supports_explicit_cache_control(self) -> bool:
        return False

    def build_request(self, request: AgentRequest) -> Dict[str, Any]:
        system_prompt = request.system_prompt
        
        if request.allow_prompt_fallback and request.response_strategy == "prompt_fallback":
            from providers.common import get_prompt_fallback_instruction
            fallback_instruction = get_prompt_fallback_instruction(request.response_schema)
            system_prompt = (system_prompt or "") + fallback_instruction

        return {
            "model": request.model,
            "system_prompt": system_prompt,
            "task": request.user_prompt,
            "tools": normalize_tool_names(request.tools) if request.response_strategy == "tool_calling" else [],
            "mcp_servers": request.mcp_servers,
            "metadata": request.metadata,
            "working_directory": request.working_directory,
            "response_strategy": request.response_strategy,
        }

    def invoke(self, request: AgentRequest) -> AgentResponse:
        import json
        import time
        import urllib.request
        from urllib.error import HTTPError, URLError
        from keys import KeyManager

        api_key = KeyManager.get("openhands", "OPENHANDS_API_KEY", "OPENHANDS_API_KEY") or ""
        base_url = KeyManager.get("openhands", "OPENHANDS_BASE_URL", "OPENHANDS_BASE_URL") or "http://localhost:3000"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        # 1. Create conversation
        payload = self.build_request(request)
        
        # Combine system prompt and user prompt for OpenHands, as it prefers a single initial message
        combined_prompt = ""
        if payload.get("system_prompt"):
            combined_prompt += payload["system_prompt"] + "\n\n---\n\n"
        combined_prompt += payload.get("task", "")

        create_data = json.dumps({
            "initial_user_msg": combined_prompt
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{base_url}/api/v1/app-conversations",
                data=create_data,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                conv_id = result.get("conversation_id")
                
                if not conv_id:
                    return AgentResponse(
                        agent_id=request.agent_id,
                        role=request.role,
                        status="failed",
                        content="Failed to parse conversation_id from OpenHands API",
                        raw=result,
                    )
        except HTTPError as e:
            return AgentResponse(
                agent_id=request.agent_id,
                role=request.role,
                status="failed",
                content=f"HTTP Error creating OpenHands conversation {e.code}: {e.read().decode('utf-8')}",
                raw=payload,
            )
        except URLError as e:
            return AgentResponse(
                agent_id=request.agent_id,
                role=request.role,
                status="failed",
                content=f"Network Error reaching OpenHands: {e.reason}",
                raw=payload,
            )

        # 2. Poll for completion
        timeout_seconds = 600  # 10 minutes
        start_time = time.time()
        poll_interval = 1.0  # Start with 1 second
        max_interval = 5.0
        final_status = "unknown"
        
        while time.time() - start_time < timeout_seconds:
            try:
                status_req = urllib.request.Request(
                    f"{base_url}/api/v1/app-conversations/{conv_id}",
                    headers=headers,
                    method="GET"
                )
                with urllib.request.urlopen(status_req) as response:
                    status_res = json.loads(response.read().decode("utf-8"))
                    final_status = status_res.get("status")
                    if final_status == "STOPPED":
                        break
            except Exception:
                pass  # Ignore transient polling errors
                
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, max_interval)

        if final_status != "STOPPED":
            return AgentResponse(
                agent_id=request.agent_id,
                role=request.role,
                status="failed",
                content="OpenHands task timed out waiting for STOPPED status.",
                raw={"conversation_id": conv_id, "last_status": final_status},
            )

        # 3. Retrieve results
        content = "OpenHands task completed."
        files_touched = []
        raw_events = []

        try:
            # Try to get events
            events_req = urllib.request.Request(
                f"{base_url}/api/v1/conversation/{conv_id}/events/search",
                headers=headers,
                method="GET"
            )
            with urllib.request.urlopen(events_req) as response:
                raw_events = json.loads(response.read().decode("utf-8"))
                # Extract the last agent message if available
                agent_messages = [e for e in raw_events if e.get("source") == "agent" and e.get("message")]
                if agent_messages:
                    content = agent_messages[-1].get("message", content)

            # Try to get modified files
            changes_req = urllib.request.Request(
                f"{base_url}/api/v1/app-conversations/{conv_id}/git/changes",
                headers=headers,
                method="GET"
            )
            with urllib.request.urlopen(changes_req) as response:
                changes = json.loads(response.read().decode("utf-8"))
                # assuming response is a list of strings or dicts
                if isinstance(changes, list):
                    for c in changes:
                        if isinstance(c, str):
                            files_touched.append(c)
                        elif isinstance(c, dict) and "path" in c:
                            files_touched.append(c["path"])
        except Exception as e:
            content += f"\n(Note: Failed to fetch full event history: {str(e)})"

        return AgentResponse(
            agent_id=request.agent_id,
            role=request.role,
            status="completed",
            content=content,
            files_touched=files_touched,
            raw={"conversation_id": conv_id, "events": raw_events},
        )

    def resume(self, request: AgentRequest) -> AgentResponse:
        return self.invoke(request)