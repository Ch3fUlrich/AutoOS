#!/usr/bin/env python3
"""Regenerate OpenHands gateway tier profiles from the spec, with local keys.

configuration/openhands/tier-profiles.json is the single source of truth for
the tier profile SHAPE (profile ids, model ids, token windows, reasoning
flags). This tool projects it into real profiles (omniroute-tier*.json +
litellm-tier*.json) by injecting the client keys, which never live in the repo:

    python3 tools/sync-openhands-profiles.py --openhands-dir ~/.openhands [--keys-file configuration/api-keys.yml]

Key resolution: env AUTOOS_OMNIROUTE_KEY first, then the `omniroute:` entry of
the keys file (omniroute tiers); env LITELLM_MASTER_KEY, then
AUTOOS_LITELLM_API_KEY, then the LITELLM_MASTER_KEY entry of
configuration/litellm/.env (litellm tiers); env OPENROUTER_API_KEY, then the
`openrouter:` entry of the keys file (a direct-provider tier, e.g. the
OpenRouter spark profile used for the full effort ladder). A gateway with no
key has its tiers skipped (the installer covers the keyless path separately);
with no key at all the tool reports the skip and exits 0.

Run by configuration/start-stack.* on every `openhands` start, so a reapplied
spec, a rotated key, or a hand-edited profile converges back automatically.
Idempotent: byte-identical output on repeat runs (reports skipped instead).
Byte-identical with the installer-embedded writer for the same keys.

With --push-url the profiles are also saved into a running OpenHands app
(its own settings store, /api/v1/settings/profiles): the docker app of
2026-09 (SDK 1.36) does not read profiles/*.json at all, so without the push
its UI offers no tier profile. The push is idempotent too (an unchanged
profile is skipped), sends only the fields the app's StrictLLM schema allows,
seeds the app's settings with the first tier when it has none, and stops at
the app's per-user profile cap (spec order decides which tiers make it).

Never prints a key. Exit 0 = written/skipped cleanly, 2 = unusable input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def read_flat_value(path: Path, name: str) -> str | None:
    """First non-placeholder `name: value` from a flat key file, else None."""
    try:
        # utf-8-sig: PowerShell Out-File -Encoding utf8 writes a BOM,
        # which would otherwise poison the first key name.
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or ":" not in text and "=" not in text:
                continue
            if "=" in text and ":" not in text.split("=", 1)[0]:
                key, _, value = text.partition("=")
            else:
                key, _, value = text.partition(":")
            if key.strip() == name:
                value = value.strip().strip("\"'")
                if value and "REPLACE" not in value:
                    return value
    except OSError:
        pass
    return None


def read_omni_key(keys_file: Path | None) -> str | None:
    if os.environ.get("AUTOOS_OMNIROUTE_KEY"):
        return os.environ["AUTOOS_OMNIROUTE_KEY"]
    if keys_file and keys_file.is_file():
        return read_flat_value(keys_file, "omniroute")
    return None


def read_litellm_key(env_path: Path | None) -> str | None:
    if os.environ.get("LITELLM_MASTER_KEY"):
        return os.environ["LITELLM_MASTER_KEY"]
    if os.environ.get("AUTOOS_LITELLM_API_KEY"):
        return os.environ["AUTOOS_LITELLM_API_KEY"]
    if env_path and env_path.is_file():
        return read_flat_value(env_path, "LITELLM_MASTER_KEY")
    return None


def read_openrouter_key(keys_file: Path | None) -> str | None:
    """A DIRECT-provider tier (effort-ladder surface) has its own key."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    if keys_file and keys_file.is_file():
        return read_flat_value(keys_file, "openrouter")
    return None


def build_profile(tier: dict, base_url: str, key: str) -> dict:
    profile = {
        "auth_type": "api_key",
        "api_mode": "auto",
        "stream": False,
        "drop_params": True,
        "modify_params": True,
        "disable_stop_word": False,
        "caching_prompt": True,
        "log_completions": False,
        "native_tool_calling": True,
        "is_subscription": False,
        "capability_overrides": {},
        "litellm_extra_body": {},
        "model": tier["model"],
        "base_url": base_url,
        "max_input_tokens": tier["max_input_tokens"],
        "max_output_tokens": tier["max_output_tokens"],
        "input_cost_per_token": 0,
        "output_cost_per_token": 0,
        "api_key": key,
    }
    if tier.get("reasoning"):
        profile["reasoning_effort"] = "high"
    else:
        profile["reasoning_effort"] = "none"
        profile["enable_encrypted_reasoning"] = False
        profile["extended_thinking_budget"] = None
    return profile


def tier_base_url(tier: dict, spec: dict) -> str:
    gateway = tier.get("gateway")
    if gateway == "litellm":
        return spec.get("litellm_base_url", spec["gateway_base_url"])
    if gateway == "openrouter":
        # A direct-provider tier names its own endpoint in the spec.
        return tier.get("base_url", spec["gateway_base_url"])
    return spec["gateway_base_url"]


CONTAINER_HOST = "host.docker.internal"


def container_host_resolves() -> bool:
    """True when host.docker.internal resolves on THIS host (Docker Desktop).

    AUTOOS_FAKE_HDI_RESOLVES=0/1 stands in for the lookup in tests.
    """
    fake = os.environ.get("AUTOOS_FAKE_HDI_RESOLVES")
    if fake is not None:
        return fake == "1"
    import socket
    try:
        socket.getaddrinfo(CONTAINER_HOST, None)
        return True
    except OSError:
        return False


def for_consumer(url: str, consumer: str) -> str:
    """The spec's container-side URL, adjusted for who reads the profile.

    container: unchanged - the docker app resolves host.docker.internal
    (Docker Desktop natively, native Linux via --add-host host-gateway).
    native: a host process (agent-canvas). It keeps the name where it
    resolves (Docker Desktop) and gets 127.0.0.1 where it does not (native
    Linux) - the same rule as resolve_ollama_base_url.
    """
    if consumer == "native" and CONTAINER_HOST in url and not container_host_resolves():
        return url.replace(CONTAINER_HOST, "127.0.0.1")
    return url


def _http(method: str, url: str, body=None, timeout: float = 20.0):
    """(status, parsed JSON or None). Never raises for HTTP or socket errors."""
    import urllib.error
    import urllib.request
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except (OSError, ValueError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def push_profiles(push_url: str, profiles: list) -> int:
    """Save (name, profile) pairs into a running OpenHands app. Returns 0."""
    base = push_url.rstrip("/")
    status, spec = _http("GET", base + "/openapi.json")
    if status != 200 or not isinstance(spec, dict):
        print(f"sync-openhands-profiles: push skipped ({base} is not answering)")
        return 0
    schemas = spec.get("components", {}).get("schemas", {})
    allowed = set((schemas.get("StrictLLM") or {}).get("properties", {}))
    if not allowed:
        print("sync-openhands-profiles: push skipped (the app has no StrictLLM schema)")
        return 0

    def strict(profile: dict) -> dict:
        return {k: v for k, v in profile.items() if k in allowed}

    status, _ = _http("GET", base + "/api/v1/settings")
    if status == 404 and profiles:
        first = strict(profiles[0][1])
        status, _ = _http("POST", base + "/api/v1/settings", {"agent_settings_diff": {"llm": first}})
        print("sync-openhands-profiles: app settings seeded with %s%s"
              % (profiles[0][0], "" if status == 200 else " FAILED (HTTP %s)" % status))
    capped = []
    for name, profile in profiles:
        want = strict(profile)
        status, have = _http("GET", f"{base}/api/v1/settings/profiles/{name}")
        if capped and status != 200:
            # Past the app's cap every further NEW profile is refused too;
            # ones it already holds are still compared and updated.
            capped.append(name)
            continue
        if status == 200 and isinstance(have, dict) and have.get("api_key_set"):
            current = dict(have.get("config") or {})
            current.pop("api_key", None)
            compare = {k: v for k, v in want.items() if k != "api_key"}
            if all(current.get(k) == v for k, v in compare.items()):
                print(f"sync-openhands-profiles: app profile {name} skipped (up to date)")
                continue
        status, reply = _http("POST", f"{base}/api/v1/settings/profiles/{name}", {"llm": want})
        if status in (200, 201):
            print(f"sync-openhands-profiles: app profile {name} saved")
        elif status == 409:
            capped.append(name)
        else:
            detail = json.dumps(reply)[:160] if reply is not None else ""
            print(f"sync-openhands-profiles: app profile {name} FAILED (HTTP {status}) {detail}")
    if capped:
        print("sync-openhands-profiles: the app's profile cap is reached - not in the app: %s"
              % ", ".join(capped))
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Regenerate OpenHands gateway tier profiles from the spec.",
    )
    parser.add_argument(
        "--openhands-dir",
        required=True,
        help="target OpenHands directory (profiles/ is created inside it)",
    )
    parser.add_argument(
        "--keys-file",
        default=None,
        help="api-keys.yml fallback for the OmniRoute client key (default: configuration/api-keys.yml)",
    )
    parser.add_argument(
        "--litellm-env",
        default=None,
        help="litellm .env fallback for the master key (default: configuration/litellm/.env)",
    )
    parser.add_argument(
        "--consumer",
        choices=("container", "native"),
        default="container",
        help="who reads the profiles: the docker app (default) or a host process such as agent-canvas",
    )
    parser.add_argument(
        "--push-url",
        default=None,
        help="also save the profiles into this running OpenHands app (e.g. http://127.0.0.1:3000)",
    )
    parser.add_argument(
        "--spec",
        default=None,
        help="tier-profiles.json path (default: configuration/openhands/tier-profiles.json)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent.parent
    spec_path = Path(args.spec) if args.spec else root / "configuration" / "openhands" / "tier-profiles.json"
    keys_path = Path(args.keys_file) if args.keys_file else root / "configuration" / "api-keys.yml"
    litellm_env = Path(args.litellm_env) if args.litellm_env else root / "configuration" / "litellm" / ".env"
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        tiers = spec["tiers"]
        spec["gateway_base_url"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: cannot read spec {spec_path}: {exc}", file=sys.stderr)
        return 2
    omni_key = read_omni_key(keys_path)
    litellm_key = read_litellm_key(litellm_env)
    openrouter_key = read_openrouter_key(keys_path)
    if not omni_key and not litellm_key and not openrouter_key:
        print("sync-openhands-profiles: no client key (env/key file/.env) - tier profiles skipped")
        return 0
    profiles_dir = Path(args.openhands_dir) / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    keys = {"litellm": litellm_key, "openrouter": openrouter_key}
    built = []
    for tier in tiers:
        key = keys.get(tier.get("gateway"), omni_key)
        name = "%s.json" % tier["id"]
        if not key:
            print(f"sync-openhands-profiles: {name} skipped (no key for its gateway)")
            continue
        # No trailing newline: byte-identical with the installer-embedded
        # writer, so install-time and start-time outputs never flap.
        base_url = for_consumer(tier_base_url(tier, spec), args.consumer)
        profile = build_profile(tier, base_url, key)
        built.append((tier["id"], profile))
        text = json.dumps(profile, indent=2)
        target = profiles_dir / name
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            print(f"sync-openhands-profiles: {name} skipped (up to date)")
        else:
            target.write_text(text, encoding="utf-8")
            print(f"sync-openhands-profiles: {name} written")
    if args.push_url:
        return push_profiles(args.push_url, built)
    return 0


if __name__ == "__main__":
    sys.exit(main())
