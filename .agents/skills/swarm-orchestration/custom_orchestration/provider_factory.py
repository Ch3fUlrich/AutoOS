from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from providers.registry import ProviderRegistry


@dataclass
class ProviderSelection:
    role: str
    provider_name: str
    model: str
    fallback_providers: List[str]
    fallback_models: List[str]


class ProviderFactory:
    def __init__(self, config: dict):
        self.config = config
        self.registry = ProviderRegistry()

    def select_for_role(
        self,
        role: str,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
    ) -> ProviderSelection:
        role_cfg = self.config.get("role_provider_routing", {}).get(role, {})
        provider_name = preferred_provider or role_cfg.get("provider", "claude_code")
        model = preferred_model or role_cfg.get("model", "default-model")
        fallback_providers = role_cfg.get("fallback_providers", [])
        fallback_models = role_cfg.get("fallback_models", [])
        return ProviderSelection(
            role=role,
            provider_name=provider_name,
            model=model,
            fallback_providers=fallback_providers,
            fallback_models=fallback_models,
        )

    def adapter_for(self, provider_name: str):
        return self.registry.get(provider_name)