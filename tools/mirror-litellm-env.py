#!/usr/bin/env python3
"""Mirror provider keys from api-keys.yml into the LiteLLM .env file.

configuration/api-keys.yml is the single source of truth for keys (one
`name: value` map). configuration/litellm/.env needs the same keys under
LiteLLM's conventional names (GROQ_API_KEY, ...) plus a random
LITELLM_MASTER_KEY. This tool regenerates the .env from the yml so the two
never drift apart by hand-editing. The name mapping itself comes from
catalog/ai-registry.json's `providers` section (task A5c, spec 3.2 phase 2 -
this used to read the legacy provider catalog directly; same field names,
mapping doc section 2, only the source file changed). --registry overrides
the registry path (a test fixture, mainly); the legacy provider catalog it
replaced was deleted in task A5e (spec 3.2: no old catalog is deleted before
every consumer has moved off it).

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
import secrets
import sys
from pathlib import Path

# The shared by-path loader for tools/registry.py; tools/ is added to sys.path
# only when it is missing, so importing this module from another tool (or the
# test suite loading THIS file by path) still resolves it.
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
from registry_loader import load_registry_tool  # noqa: E402 - tools/ added above

# catalog/ai-registry.json - the single registry shared with apply.ps1/
# apply.sh and tools/sync-router-tiers.py (spec 3.2 phase 2, task A5c).
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "catalog" / "ai-registry.json"


def load_key_map(path=None) -> dict:
    """api-keys.yml name -> litellm .env name, from catalog/ai-registry.json's
    `providers` section (tools/registry.py's provider_field_map()).

    The KEY is the name the value is read from in api-keys.yml, which is the
    provider id except when the entry carries `key_name` (MUSEAPI step 4:
    `meta_api` reuses the `meta` key). Keyed by provider id, `meta_api` would
    look up a 'meta_api' nobody has and emit a second META_API_KEY= line —
    a REPLACE_WITH_ placeholder that the env parser's last-line-wins rule puts
    over the real key. Two entries that resolve to the same name and the same
    env var collapse into that one line; the same name with two different env
    vars is refused rather than silently dropping one key.

    Registry order is kept, so the lines' order in .env is unchanged. Names
    keep the registry's spelling - api-keys.yml spells SambaNova with a capital
    S/N and this lookup is case-sensitive. A provider with no litellm_env
    (an OAuth/subscription bridge - `cc`, `antigravity`) is skipped: it never
    had an api-keys.yml entry either.
    """
    registry = load_registry_tool()
    doc = registry.load(path or DEFAULT_REGISTRY_PATH)
    providers = doc["providers"]
    key_map = {}
    for pid, env in registry.provider_field_map(providers, "litellm_env").items():
        src = (providers[pid].get("key_name") or pid)
        if src in key_map and key_map[src] != env:
            raise ValueError(
                "api-keys.yml name %r would feed both %s and %s - give one of "
                "them its own key entry" % (src, key_map[src], env))
        key_map[src] = env
    return key_map


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
