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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "catalog" / "ai-registry.json"

REQUIRED_FIELDS = ("omniroute_id", "litellm_env", "litellm_prefix", "api_base", "provider_data")


def key_name_of(providers: dict, name: str) -> str:
    """The api-keys.yml entry whose value a provider's key is read from: its own
    name, unless the registry says otherwise with key_name (MUSEAPI step 4 -
    meta_api reuses the 'meta' key)."""
    entry = providers.get(name)
    if not isinstance(entry, dict):
        return name
    return entry.get("key_name") or name


def litellm_env_problems(providers: dict) -> list:
    """Contracts on providers.<id>.litellm_env, as data rather than a
    provider-name allowlist (an allowlist would let the second share through
    without anyone restating the rule):

    - One env var may be written by several providers only when they all read
      the SAME api-keys.yml name. Two different names feeding one variable is
      how the .env ended up with a real key on one line and a REPLACE_WITH_
      placeholder on the next (last line wins) - so the rule is the reason the
      duplicate is safe, and it keeps being the rule for any future share.
    - A key_name must name another provider's api-keys name, and must not
      change what that provider itself reads.
    """
    problems = []
    seen_envs: dict = {}
    for name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        env = entry.get("litellm_env")
        if not env:
            continue
        src = key_name_of(providers, name)
        if env in seen_envs and seen_envs[env][0] != src:
            first = seen_envs[env]
            problems.append(
                f"duplicate litellm_env {env}: {first[1]} and {name} read "
                f"different api-keys.yml names ({first[0]!r} vs {src!r}) - "
                f"share it with key_name or give one entry its own key")
        seen_envs.setdefault(env, (src, name))
    for name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        claimed = entry.get("key_name")
        if not claimed:
            continue
        if claimed == name:
            problems.append(f"{name}: key_name repeats its own provider id")
        elif claimed not in providers:
            problems.append(f"{name}: key_name {claimed!r} is no provider's name")
    return problems


def model_prefix_problems(providers: dict) -> list:
    """Contracts on providers.<id>.model_prefix - the spelling gateway_ref()
    puts into combos.json (MUSEAPI: the 'meta' prefix a second Meta record
    already owned, which made tools/sync-router-tiers.py --combos render the
    contributor leg with no api_base).

    - A prefix must never be ANOTHER provider's registry key. Both
      tools/registry.py's resolve_leg() and sync-router-tiers' transport maps
      key legs by provider name first, so a borrowed name silently hands the
      leg that other provider's LiteLLM transport (finding 4 says the same of
      omniroute_id; model_prefix is the third spelling of the same namespace).
    - A provider that LiteLLM can address (it has a litellm_env or
      litellm_prefix) must spell its gateway prefix with one of its OWN two
      keys, name or omniroute_id, because those are the only keys
      provider_maps_from_dict() emits. A gateway-only bridge (no transport at
      all - antigravity/agy) is exempt: its legs never enter a managed block
      and sync-router-tiers carries both of its spellings in GATEWAY_ONLY by
      hand.
    """
    problems = []
    names = {name for name, entry in providers.items() if isinstance(entry, dict)}
    for name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        prefix = entry.get("model_prefix")
        if not prefix:
            continue
        if prefix in names and prefix != name:
            problems.append(
                f"{name}: model_prefix {prefix!r} is another provider's registry "
                f"key - a combos leg with that prefix resolves that provider's transport")
        if entry.get("litellm_env") or entry.get("litellm_prefix"):
            own = {name, entry.get("omniroute_id")}
            if prefix not in own:
                problems.append(
                    f"{name}: model_prefix {prefix!r} is neither its own key nor its "
                    f"omniroute_id, so tools/sync-router-tiers.py --combos cannot "
                    f"resolve the rendered leg back to this provider's transport")
    return problems


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

    # 1. schema completeness, no duplicate OmniRoute ids, env names shared only
    # through the rule in 1c.
    seen_ids: dict = {}
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

    # 1b. a provider name must never equal ANOTHER provider's omniroute_id:
    # provider_maps_from_dict()/expected_by_omni() key both spellings onto one
    # string, so the collision would let one provider's transport overwrite
    # another's (PROV finding 4).
    for name, entry in providers.items():
        omni = entry.get("omniroute_id")
        if omni and omni != name and omni in providers:
            problems.append(
                f"provider name {omni!r} is also provider {name!r}'s omniroute_id")

    # 1c. the litellm_env sharing rule and what a key_name may claim.
    problems.extend(litellm_env_problems(providers))

    # 1d. the combos/gateway spelling (model_prefix) must resolve back to the
    # provider that declared it - same namespace as 1b.
    problems.extend(model_prefix_problems(providers))

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

    # 3. mirror tool: api-keys.yml name -> env, in registry order (the .env
    #    line order). Keyed by the NAME THE VALUE IS READ FROM, so a provider
    #    that shares another's key (key_name) collapses into that one line
    #    instead of asking for an api-keys.yml entry nobody has.
    registry_providers = load_registry_providers()
    mirror = load_module("tools/mirror-litellm-env.py", "mirror_litellm_env")
    want_env = {}
    for name, entry in registry_providers.items():
        if not (isinstance(entry, dict) and entry.get("litellm_env")):
            continue
        want_env[key_name_of(registry_providers, name)] = entry["litellm_env"]
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

    # 6. the docs table names the registry as its source AND its provider-id
    #    column matches every omniroute_id (PROV finding 5: the human view of
    #    exactly what apply reads had drifted - free_ai vs free-ai - and
    #    nothing compared it). A provider with a null omniroute_id must show a
    #    dash, never a bare id.
    docs = (ROOT / "docs" / "api-keys.md").read_text(encoding="utf-8")
    if "catalog/ai-registry.json" not in docs:
        problems.append("docs/api-keys.md does not name catalog/ai-registry.json")
    table_ids = {}
    for line in docs.splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            table_ids[m.group(1)] = m.group(2).strip()
    for name, entry in registry_providers.items():
        if name not in table_ids:
            continue
        cell = table_ids[name]
        omni = entry.get("omniroute_id")
        if omni is None:
            if not cell.startswith("—"):
                problems.append(
                    f"docs/api-keys.md row {name}: expected — for a null "
                    f"omniroute_id, got {cell!r}")
        elif cell != f"`{omni}`":
            problems.append(
                f"docs/api-keys.md row {name}: provider id {cell!r} != "
                f"omniroute_id {omni!r}")

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
