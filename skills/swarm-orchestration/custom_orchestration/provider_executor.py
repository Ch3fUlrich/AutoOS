from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from providers.base import AgentRequest, AgentResponse
from provider_factory import ProviderFactory, ProviderSelection
from response_parser import ResponseParser, NormalizedResponse


@dataclass
class ExecutionResult:
    success: bool
    provider_name: str
    model: str
    response: AgentResponse
    normalized_response: Optional[NormalizedResponse] = None
    fallback_used: bool = False
    error: Optional[str] = None


class ProviderExecutor:
    def __init__(self, config: dict):
        self.config = config
        self.factory = ProviderFactory(config)
        self.parser = ResponseParser(config)

    def execute(
        self,
        role: str,
        agent_id: str,
        system_prompt: str,
        user_prompt: str,
        tools: List[Dict[str, Any]],
        mcp_servers: List[str],
        skill_ids: List[str],
        metadata: Dict[str, Any],
        working_directory: Optional[str] = None,
        branch: Optional[str] = None,
        worktree: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        cache_control: Optional[Dict[str, Any]] = None,
        checkpoint_hint: Optional[Dict[str, Any]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        allow_prompt_fallback: bool = False,
        response_format_name: Optional[str] = None,
    ) -> ExecutionResult:
        selection = self.factory.select_for_role(role, preferred_provider, preferred_model)
        return self._try_selection(
            selection=selection,
            agent_id=agent_id,
            role=role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            mcp_servers=mcp_servers,
            skill_ids=skill_ids,
            metadata=metadata,
            working_directory=working_directory,
            branch=branch,
            worktree=worktree,
            cache_control=cache_control or {},
            checkpoint_hint=checkpoint_hint or {},
            response_schema=response_schema,
            allow_prompt_fallback=allow_prompt_fallback,
            response_format_name=response_format_name,
        )

    def _try_selection(
        self,
        selection: ProviderSelection,
        agent_id: str,
        role: str,
        system_prompt: str,
        user_prompt: str,
        tools: List[Dict[str, Any]],
        mcp_servers: List[str],
        skill_ids: List[str],
        metadata: Dict[str, Any],
        working_directory: Optional[str],
        branch: Optional[str],
        worktree: Optional[str],
        cache_control: Dict[str, Any],
        checkpoint_hint: Dict[str, Any],
        response_schema: Optional[Dict[str, Any]],
        allow_prompt_fallback: bool,
        response_format_name: Optional[str],
    ) -> ExecutionResult:
        providers_to_try = [selection.provider_name] + selection.fallback_providers
        models_to_try = [selection.model] + selection.fallback_models

        last_error = None

        for idx, provider_name in enumerate(providers_to_try):
            adapter = self.factory.adapter_for(provider_name)
            model = models_to_try[min(idx, len(models_to_try) - 1)]

            caps = adapter.capabilities()
            response_strategy = self._determine_response_strategy(caps, tools, response_schema)

            request = AgentRequest(
                agent_id=agent_id,
                role=role,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=tools,
                mcp_servers=mcp_servers,
                skill_ids=skill_ids,
                working_directory=working_directory,
                branch=branch,
                worktree=worktree,
                metadata=metadata,
                cache_control=cache_control if adapter.supports_explicit_cache_control() else {},
                checkpoint_hint=checkpoint_hint if adapter.supports_native_checkpointing() else {},
                response_strategy=response_strategy,
                response_schema=response_schema,
                tool_schemas=tools,
                allow_prompt_fallback=allow_prompt_fallback,
                response_format_name=response_format_name,
            )

            try:
                execution_mode = metadata.get("execution_mode", "stub")
                if execution_mode == "stub":
                    response = AgentResponse(
                        agent_id=agent_id,
                        role=role,
                        status="stubbed",
                        content=f"{provider_name} adapter invocation placeholder (stub mode).",
                        raw=request.metadata,
                    )
                else:
                    response = adapter.invoke(request)
                    
                normalized = self.parser.parse(
                    response.content, 
                    response.raw, 
                    response_strategy=response_strategy, 
                    expected_schema=response_schema
                )
                    
                return ExecutionResult(
                    success=True,
                    provider_name=provider_name,
                    model=model,
                    response=response,
                    normalized_response=normalized,
                    fallback_used=(idx > 0),
                )
            except Exception as e:
                last_error = str(e)

        return ExecutionResult(
            success=False,
            provider_name=selection.provider_name,
            model=selection.model,
            response=AgentResponse(
                agent_id=agent_id,
                role=role,
                status="failed",
                content="Provider execution failed.",
            ),
            fallback_used=True,
            error=last_error,
        )

    def _determine_response_strategy(self, caps: Dict[str, Any], tools: List[Dict[str, Any]], expected_schema: Optional[Dict[str, Any]]) -> str:
        if tools and caps.get("tool_calling"):
            return "tool_calling"
        if expected_schema:
            if caps.get("strict_schema"):
                return "strict_schema"
            elif caps.get("json_mode"):
                return "json_mode"
            elif caps.get("prompt_json_fallback"):
                return "prompt_fallback"
        return "plain_text"