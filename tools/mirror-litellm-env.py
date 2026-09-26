#!/usr/bin/env python3
"""Mirror provider keys from api-keys.yml into the LiteLLM .env file.

configuration/api-keys.yml is the single source of truth for keys (one
`name: value` map). configuration/litellm/.env needs the same keys under
LiteLLM's conventional names (GROQ_API_KEY, ...) plus a random
LITELLM_MASTER_KEY. This tool regenerates the .env from the yml so the two
never drift apart by hand-editing. The name mapping itself comes from
catalog/ai-registry.json's `providers` section (task A5c, spec 3.2 phase 2 -
this used to read catalog/providers.json directly; same field names,
mapping doc section 2, only the source file changed). --registry overrides
the registry path (a test fixture, mainly); catalog/providers.json itself is
untouched and unread by this tool now, but stays in the repo (spec 3.2: no
old catalog is deleted before every consumer has moved off it).

    python3 tools/mirror-litellm-env.py [--check] [--registry PATH]

    (default)   rewrite the .env in place (missing keys stay placeholders)
    --check     change nothing; exit 1 when a present key is stale/missing

Never prints a value: reports key NAMES only (mirrored/missing/stale).

The .env file is git-ignored; api-keys.yml is git-ignored. Nothing here
touches a tracked file. Exit 0 = in sync / written, 1 = drifted (--check),
2 = unusable input.
"""
from __future__ import annotations

import argparse
import importlib.util
import secrets
import sys
from pathlib import Path

# catalog/ai-registry.json - the single registry shared with apply.ps1/
# apply.sh and tools/sync-router-tiers.py (spec 3.2 phase 2, task A5c).
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "catalog" / "ai-registry.json"
REGISTRY_TOOL_PATH = Path(__file__).resolve().parent / "registry.py"


def _load_registry_tool():
    """Import tools/registry.py by path - the same importlib-by-path
    technique tools/sync-ide-models.py's _load_registry_tool() and
    tools/registry.py's own _load_sync_router_tiers() already use, so this
    tool carries no second copy of the loader."""
    spec = importlib.util.spec_from_file_location("autoos_registry", REGISTRY_TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_key_map(path=None) -> dict:
    """api-keys.yml name -> litellm .env name, from catalog/ai-registry.json's
    `providers` section (tools/registry.py's provider_field_map()).

    Keys keep the registry's spelling - api-keys.yml spells SambaNova with a
    capital S/N and this lookup is case-sensitive. Value order is the order
    lines are emitted into .env, so it follows the registry exactly. A
    provider with no litellm_env (an OAuth/subscription bridge - `cc`,
    `antigravity`) is skipped: it never had an api-keys.yml entry either.
    """
    registry = _load_registry_tool()
    doc = registry.load(path or DEFAULT_REGISTRY_PATH)
    return registry.provider_field_map(doc["providers"], "litellm_env")


# api-keys.yml name -> litellm .env name.
KEY_MAP = load_key_map()

HEADER = "# AutoOS LiteLLM keys - generated from api-keys.yml, do not commit."


def read_flat_map(path: Path) -> dict:
    """Parse a flat `key: value` file, skipping comments and placeholders."""
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or ":" not in text:
            continue
        name, _, value = text.partition(":")
        name, value = name.strip(), value.strip().strip("\"'")
        if name and value and "REPLACE" not in value:
            out.setdefault(name, value)
    return out


def render(keys: dict, keep_master: str | None) -> tuple[str, list, list]:
    """Build the .env text. Returns (text, mirrored, missing).

    Skips any KEY_MAP entry with a falsy destination - defense in depth
    alongside load_key_map()'s own filter, since KEY_MAP is settable
    directly (module global, task A5c's --registry reload path) and a null
    litellm_env (an OAuth/subscription bridge's provider entry) must never
    reach a written line ("None=REPLACE_WITH_..." is not a valid env var)."""
    lines = [HEADER]
    mirrored, missing = [], []
    for src, dst in KEY_MAP.items():
        if not dst:
            continue
        if src in keys:
            lines.append("%s=%s" % (dst, keys[src]))
            mirrored.append(dst)
        else:
            lines.append("%s=REPLACE_WITH_%s" % (dst, src.upper()))
            missing.append(dst)
    master = keep_master or "REPLACE_WITH_A_RANDOM_LOCAL_PASSWORD"
    lines.append("LITELLM_MASTER_KEY=%s" % master)
    return "\n".join(lines) + "\n", mirrored, missing


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Mirror api-keys.yml provider keys into litellm/.env.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when a present key is stale/missing; change nothing",
    )
    parser.add_argument("--keys", default=None)
    parser.add_argument("--env", default=None)
    parser.add_argument(
        "--registry",
        default=None,
        help="path to catalog/ai-registry.json (default: catalog/ai-registry.json)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent.parent
    keys_path = Path(args.keys) if args.keys else root / "configuration" / "api-keys.yml"
    env_path = Path(args.env) if args.env else root / "configuration" / "litellm" / ".env"
    registry_path = Path(args.registry) if args.registry else DEFAULT_REGISTRY_PATH
    global KEY_MAP
    try:
        KEY_MAP = load_key_map(registry_path)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: cannot read {registry_path}: {exc}", file=sys.stderr)
        return 2
    try:
        keys = read_flat_map(keys_path)
    except OSError as exc:
        print(f"ERROR: cannot read {keys_path}: {exc}", file=sys.stderr)
        return 2
    current_master = None
    if env_path.is_file():
        try:
            current = read_flat_map(env_path)
            current_master = current.get("LITELLM_MASTER_KEY")
        except OSError:
            pass
    if current_master is None:
        current_master = secrets.token_urlsafe(32)
    text, mirrored, missing = render(keys, current_master)
    if args.check:
        if not env_path.is_file():
            print(f"DRIFT: {env_path} missing", file=sys.stderr)
            return 1
        current_text = env_path.read_text(encoding="utf-8")
        # Compare modulo the master key: rotation must not read as drift.
        # (A missing master key below is reported, not diffed.)
        norm = lambda t: "\n".join(
            l for l in t.splitlines() if not l.startswith("LITELLM_MASTER_KEY="))
        problems = []
        if norm(current_text) != norm(text):
            problems.append("provider keys differ")
        if "LITELLM_MASTER_KEY=REPLACE" in current_text:
            problems.append("master key is a placeholder")
        if problems:
            print(f"DRIFT: {'; '.join(problems)}", file=sys.stderr)
            return 1
        print(f"OK: {len(mirrored)} keys mirrored, {len(missing)} placeholders ({', '.join(missing) or 'none missing'})")
        return 0
    env_path.write_text(text, encoding="utf-8")
    print(f"wrote {env_path}: {len(mirrored)} mirrored, {len(missing)} placeholders ({', '.join(missing) or 'none missing'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
