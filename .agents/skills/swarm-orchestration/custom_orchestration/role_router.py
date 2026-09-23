from __future__ import annotations

from typing import List


class RoleRouter:
    def __init__(self, config: dict):
        self.config = config

    def provider_for_role(self, role: str) -> str:
        return self.config.get("role_provider_routing", {}).get(role, {}).get("provider", "claude_code")

    def model_for_role(self, role: str) -> str:
        return self.config.get("role_provider_routing", {}).get(role, {}).get("model", "default-model")

    def fallback_providers_for_role(self, role: str) -> List[str]:
        return self.config.get("role_provider_routing", {}).get(role, {}).get("fallback_providers", [])

    def fallback_models_for_role(self, role: str) -> List[str]:
        return self.config.get("role_provider_routing", {}).get(role, {}).get("fallback_models", [])