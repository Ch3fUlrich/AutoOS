from __future__ import annotations

from typing import Dict

from providers.antigravity import AntigravityAdapter
from providers.claude_code import ClaudeCodeAdapter
from providers.codex import CodexAdapter
from providers.deepseek_tui import DeepSeekTUIAdapter
from providers.local_glm import LocalGLMAdapter
from providers.openhands import OpenHandsAdapter
from providers.ollama import OllamaAdapter


class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, object] = {
            "claude_code": ClaudeCodeAdapter(),
            "antigravity": AntigravityAdapter(),
            "codex": CodexAdapter(),
            "openhands": OpenHandsAdapter(),
            "deepseek_tui": DeepSeekTUIAdapter(),
            "local_glm": LocalGLMAdapter(),
            "ollama": OllamaAdapter(),
        }

    def get(self, name: str):
        if name not in self._providers:
            raise KeyError(f"Unknown provider: {name}")
        return self._providers[name]

    def list(self):
        return list(self._providers.keys())