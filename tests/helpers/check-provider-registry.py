#!/usr/bin/env python3
"""Fail when a provider map drifts from catalog/ai-registry.json.

catalog/ai-registry.json's `providers` section is the single source of
truth (task A5e deleted the legacy catalog/providers.json): sections 1/2
below check that section's own schema completeness and contracts, sections
3/4 compare each tool's in-memory map against it, section 5 asserts
apply.sh/apply.ps1 read it, and section 6 asserts docs/api-keys.md names
it as its source.

Run from the repository root (the suites do)::

    python3 tests/helpers/check-provider-registry.py

Exit 0 (prints "ok") when they agree, 1 (with the differences on stderr) when
they do not. Never prints a key value: provider names, ids and env-var names
only.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "catalog" / "ai-registry.json"

REQUIRED_FIELDS = ("omniroute_id", "litellm_env", "litellm_prefix", "api_base", "provider_data")


def load_registry_providers() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["providers"]


def load_module(relative_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_by_omni(providers: dict):
    """(prefix, api_base, env_key, seen) keyed by provider name and OmniRoute id.

    Both are valid leg prefixes (tools/registry.py's resolve_leg accepts
    either), so provider_maps_from_dict() keys both; this mirrors it."""
    prefix, api_base, env_key, seen = {}, {}, {}, {}
    rows = [(name, entry) for name, entry in providers.items()
            if isinstance(entry, dict) and entry.get("omniroute_id")]
    # Name keys first, then omniroute_id keys only when no name claims them -
    # the same name-first precedence as provider_maps_from_dict() (PROV finding 4).
    for name, entry in rows:
        seen[entry["omniroute_id"]] = name
        for dest, field in ((prefix, "litellm_prefix"), (api_base, "api_base"),
                            (env_key, "litellm_env")):
            if entry.get(field):
                dest[name] = entry[field]
    for name, entry in rows:
        omni = entry["omniroute_id"]
        for dest, field in ((prefix, "litellm_prefix"), (api_base, "api_base"),
                            (env_key, "litellm_env")):
            if entry.get(field) and omni not in dest:
                dest[omni] = entry[field]
    return prefix, api_base, env_key, seen


def main() -> int:
    problems: list[str] = []
    providers = load_registry_providers()

    # 1. schema completeness, no duplicate OmniRoute ids, unique env names.
    seen_ids: dict = {}
    seen_envs: dict = {}
    for name, entry in providers.items():
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            problems.append(f"{name}: missing fields {missing}")
            continue
        omni = entry.get("omniroute_id")
        if omni:
            if omni in seen_ids:
                problems.append(f"duplicate omniroute_id {omni}: {seen_ids[omni]}, {name}")
            seen_ids[omni] = name
        env = entry.get("litellm_env")
        if env:
            if env in seen_envs:
                problems.append(f"duplicate litellm_env {env}: {seen_envs[env]}, {name}")
            seen_envs[env] = name

    # 1b. a provider name must never equal ANOTHER provider's omniroute_id:
    # provider_maps_from_dict()/expected_by_omni() key both spellings onto one
    # string, so the collision would let one provider's transport overwrite
    # another's (PROV finding 4).
    for name, entry in providers.items():
        omni = entry.get("omniroute_id")
        if omni and omni != name and omni in providers:
            problems.append(
                f"provider name {omni!r} is also provider {name!r}'s omniroute_id")

    # 2. the case quirk and the two null rows are contracts, not trivia.
    if "SambaNova" not in providers:
        problems.append("api-keys.yml name 'SambaNova' is missing (capital S/N matters)")
    if providers.get("meta", {}).get("omniroute_id") is not None:
        problems.append("meta must have omniroute_id null (unregistered 2026-09-23)")
    if providers.get("meta", {}).get("litellm_env") != "META_API_KEY":
        problems.append("meta must still mirror META_API_KEY")
    if providers.get("omniroute", {}).get("omniroute_id") is not None:
        problems.append("omniroute is the client key, not a provider")
    for name in ("groq", "cerebras"):
        ua = (providers.get(name, {}).get("provider_data") or {}).get("customUserAgent")
        if ua != "curl/8.7.1":
            problems.append(f"{name} provider_data must carry the Cloudflare UA fix")

    # 3. mirror tool: name -> env, in registry order (the .env line order).
    registry_providers = load_registry_providers()
    mirror = load_module("tools/mirror-litellm-env.py", "mirror_litellm_env")
    want_env = {name: entry["litellm_env"] for name, entry in registry_providers.items()
                if entry.get("litellm_env")}
    if mirror.KEY_MAP != want_env:
        problems.append(f"mirror KEY_MAP != registry: {mirror.KEY_MAP}")
    elif list(mirror.KEY_MAP) != list(want_env):
        problems.append("mirror KEY_MAP order != registry order (would reorder litellm/.env)")

    # 4. tier sync tool: its three maps, keyed by OmniRoute id.
    sync = load_module("tools/sync-router-tiers.py", "sync_router_tiers")
    want_prefix, want_api_base, want_env_key, _ = expected_by_omni(registry_providers)
    got_prefix, got_api_base, got_env_key = sync.provider_maps()
    if got_prefix != want_prefix:
        problems.append("sync-router PROVIDER_PREFIX != registry")
    if got_api_base != want_api_base:
        problems.append("sync-router API_BASE != registry")
    if got_env_key != want_env_key:
        problems.append("sync-router ENV_KEY != registry")

    # 5. apply scripts read the registry (shell/PowerShell, so asserted from
    #    source; the Windows suite executes Get-AutoOSProviderMap for real).
    for rel in ("configuration/omniroute/apply.sh", "configuration/omniroute/apply.ps1"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "ai-registry.json" not in text:
            problems.append(f"{rel} does not read catalog/ai-registry.json")

    # 6. the docs table names the registry as its source.
    docs = (ROOT / "docs" / "api-keys.md").read_text(encoding="utf-8")
    if "catalog/ai-registry.json" not in docs:
        problems.append("docs/api-keys.md does not name catalog/ai-registry.json")

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
