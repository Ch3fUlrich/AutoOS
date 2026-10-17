import os
import yaml
from pathlib import Path

class KeyManager:
    _instance = None
    _keys = None

    @classmethod
    def load(cls):
        if cls._keys is not None:
            return

        cls._keys = {}
        # Try loading from the same directory as this file
        script_dir = Path(__file__).parent
        key_path = script_dir / "agent_keys.yaml"

        if key_path.exists():
            try:
                with open(key_path, 'r', encoding='utf-8') as f:
                    cls._keys = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"Warning: Failed to load agent_keys.yaml: {e}")

    @classmethod
    def get(cls, provider: str, key_name: str, fallback_env_var: str = None) -> str | None:
        cls.load()

        # Check YAML first
        if provider in cls._keys and key_name in cls._keys[provider]:
            return str(cls._keys[provider][key_name])

        # Fallback to env var
        if fallback_env_var:
            return os.environ.get(fallback_env_var)

        return None
