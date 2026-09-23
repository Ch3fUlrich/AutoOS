#!/usr/bin/env python3
"""Keep the LiteLLM fallback's tier legs in sync with OmniRoute's combos.

configuration/omniroute/combos.json is the single source of truth for the
model-list order of each tier (docs/models.md). configuration/litellm/config.yaml
is the manual fallback and carries a static copy of those legs -- a copy that
drifts the moment someone re-curates a combo (that already happened once:
t2-worker led with a 2-RPM Mistral leg while OmniRoute led with Gemini, and
t3-driver listed model ids OmniRoute no longer had).

This tool removes the drift by regenerating ONLY the block between the two
markers in config.yaml:

    # AUTOOS-MANAGED-START <tier>
    ...
    # AUTOOS-MANAGED-END <tier>

Everything outside the markers -- the header prose, t1-orchestrator,
t1-orchestrator-paid, t2-worker-paid, t3-driver-paid, router_settings,
litellm_settings, every comment and
the exact whitespace between them -- is left byte-for-byte untouched. Inside a
managed block the legs are machine-owned, so they are regenerated in full:
reordering, adding or dropping a leg is exactly the drift this tool exists to
remove.

Usage:
    python3 tools/sync-router-tiers.py [--check] [--combos PATH] [--config PATH]

    (default)   rewrite the managed blocks in place; report what changed
    --check     change nothing; exit 1 with a unified diff when drifted

Never prints or reads a secret: only model *names* and env-var *names*
(os.environ/FOO_API_KEY) LiteLLM expects. configuration/litellm/.env is never
opened.

Exit codes:
    0   already in sync (--check), or the write succeeded
    1   drifted (--check only)
    2   the config is unusable: a marker is missing, doubled or mismatched, or
        an input file is unreadable
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS_FILE = ROOT / "catalog" / "providers.json"
# Tiers mirrored from combos.json. t1-orchestrator / *-paid are deliberately
# absent: combos.json has no t1-orchestrator-paid, and docs/models.md keeps
# t1-orchestrator a hand-curated spark-only chain with an explicit xhigh note.
# t2-worker-paid / t3-driver-paid are LiteLLM-only fallback chains with no
# OmniRoute equivalent. Regenerating any of those here would lose information
# rather than remove drift.
SYNCED_TIERS = ("t2-worker", "t3-driver")

# Filled from catalog/providers.json by main(); Leg reads them at call time.
# They are module state because Leg is constructed in several code paths and
# does not carry a registry around.
PROVIDER_PREFIX: dict = {}
API_BASE: dict = {}
ENV_KEY: dict = {}

START = "# AUTOOS-MANAGED-START"
END = "# AUTOOS-MANAGED-END"

_MARKER_RE = re.compile(
    r"^[ \t]*#[ \t]*AUTOOS-MANAGED-(START|END)(?:[ \t]+(\S+))?[ \t]*$"
)


class ConfigError(RuntimeError):
    """The config file cannot be safely rewritten."""


def provider_maps(path=None):
    """(prefix, api_base, env_key) keyed by OmniRoute provider id.

    catalog/providers.json is the single source of truth shared with
    apply.ps1/apply.sh and tools/mirror-litellm-env.py. The OmniRoute provider
    id is the key because it is the first segment of a combos.json model ref;
    entries with no omniroute_id (meta, the client key) have no refs and are
    skipped.

    Only ids whose LiteLLM transport differs from the OmniRoute id appear in
    the prefix map: OmniRoute's <provider>/<model> is already LiteLLM's shape,
    so anything else passes through. api_base covers the OpenAI-compatible
    gateways, which would otherwise silently point at api.openai.com. env_key
    names the conventional env vars; Leg falls back to <PROVIDER>_API_KEY.
    """
    try:
        doc = json.loads(Path(path or PROVIDERS_FILE).read_text(encoding="utf-8"))
        providers = doc["providers"]
    except (OSError, ValueError, KeyError) as exc:
        raise ConfigError(f"cannot read {path or PROVIDERS_FILE}: {exc}") from exc
    prefix, api_base, env_key = {}, {}, {}
    for entry in providers.values():
        omni = entry.get("omniroute_id")
        if not omni:
            continue
        if entry.get("litellm_prefix"):
            prefix[omni] = entry["litellm_prefix"]
        if entry.get("api_base"):
            api_base[omni] = entry["api_base"]
        if entry.get("litellm_env"):
            env_key[omni] = entry["litellm_env"]
    return prefix, api_base, env_key


class Leg:
    """One tier leg, resolved from an OmniRoute model ref."""

    __slots__ = ("api_base", "env_key", "model", "prefix", "provider", "ref")

    def __init__(self, ref):
        if "/" not in ref:
            raise ConfigError(f"model ref has no provider prefix: {ref!r}")
        self.ref = ref
        self.provider, self.model = ref.split("/", 1)
        self.prefix = PROVIDER_PREFIX.get(self.provider, self.provider)
        self.api_base = API_BASE.get(self.provider)
        self.env_key = ENV_KEY.get(self.provider)
        if self.env_key is None:
            self.env_key = self.provider.upper().replace("-", "_") + "_API_KEY"

    @property
    def llm_model(self):
        return f"{self.prefix}/{self.model}"

    def yaml_lines(self, tier):
        lines = [
            f"  - model_name: {tier}",
            "    litellm_params:",
            f"      model: {self.llm_model}",
        ]
        if self.api_base:
            lines.append(f"      api_base: {self.api_base}")
        lines.append(f"      api_key: os.environ/{self.env_key}")
        return lines


def marker_at(line):
    """Return (kind, tier) when line is a marker line, else None."""
    m = _MARKER_RE.match(line)
    if not m:
        return None
    return m.group(1), m.group(2)


def locate_blocks(lines):
    """Map tier -> (start_idx, end_idx) of its managed block, both inclusive."""
    blocks = {}
    i = 0
    while i < len(lines):
        marker = marker_at(lines[i])
        if marker and marker[0] == "START":
            tier = marker[1]
            if tier is None:
                raise ConfigError(f"{START} at line {i + 1} has no tier name")
            if tier in blocks:
                raise ConfigError(f"duplicate {START} {tier}")
            end = None
            for j in range(i + 1, len(lines)):
                other = marker_at(lines[j])
                if other and other[0] == "END":
                    if other[1] != tier:
                        raise ConfigError(
                            f"{START} {tier} is closed by {END} {other[1]} at line {j + 1}"
                        )
                    end = j
                    break
            if end is None:
                raise ConfigError(f"{START} {tier} has no matching {END}")
            blocks[tier] = (i, end)
            i = end + 1
            continue
        if marker and marker[0] == "END":
            raise ConfigError(f"stray {END} at line {i + 1}")
        i += 1
    return blocks


def combos_refs(combos_path, tiers=SYNCED_TIERS):
    """Ordered model refs per tier, read from combos.json."""
    try:
        data = json.loads(Path(combos_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {combos_path}: {exc}") from exc
    by_name = {}
    for combo in data.get("combos", []):
        name = combo.get("name")
        if name:
            by_name[name] = list(combo.get("models", []))
    missing = [t for t in tiers if t not in by_name]
    if missing:
        raise ConfigError(f"{combos_path} has no combo(s): {', '.join(missing)}")
    return {t: by_name[t] for t in tiers}


def render_block(tier, refs, indent="", extras=None):
    """The full managed block for one tier, marker comments included.

    `extras` maps an already-present LiteLLM model string to the raw
    litellm_params lines (rpm caps and their comments) that leg carried, so a
    leg that survives a resync keeps its hand-tuned values. Everything outside
    model/api_key/api_base is preserved this way; those three are regenerated
    because they are exactly what drifts.
    """
    extras = extras or {}
    lines = [f"{indent}{START} {tier}"]
    for ref in refs:
        leg = Leg(ref)
        leg_lines = leg.yaml_lines(tier)
        for extra in extras.get(leg.llm_model, ()):
            leg_lines.append(extra)
        lines.extend(leg_lines)
    lines.append(f"{indent}{END} {tier}")
    return lines


# Keys under litellm_params this tool owns and regenerates. Any other key
# found in an existing leg is a hand-tuned override and is carried across.
_MANAGED_PARAM_KEYS = ("model", "api_key", "api_base")


def parse_block(lines, start, end):
    """Map LiteLLM model string -> extra litellm_params lines for one block.

    Only used to preserve hand-tuned keys (an rpm cap and its comment); the
    managed keys are dropped on purpose so the rewrite can replace them.
    """
    extras = {}
    current = None
    for line in lines[start + 1 : end]:
        m = re.match(r"^[ \t]*-[ \t]*model_name:[ \t]*(\S+)[ \t]*$", line)
        if m:
            current = None  # reset; a leg is keyed by its model, seen below
            continue
        m = re.match(r"^[ \t]*model:[ \t]*(\S+)[ \t]*$", line)
        if m:
            current = m.group(1)
            extras.setdefault(current, [])
            continue
        if current is None:
            continue
        key_match = re.match(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*):", line)
        if key_match and key_match.group(1) not in _MANAGED_PARAM_KEYS:
            extras[current].append(line)
    return extras


def leading_indent(line):
    return line[: len(line) - len(line.lstrip())]


def rewrite(text, combos):
    """Return (synced_text, [tiers changed]); keeps the file's newline style."""
    lines = text.splitlines()
    blocks = locate_blocks(lines)
    missing = [t for t in combos if t not in blocks]
    if missing:
        raise ConfigError(
            "config.yaml has no managed block for: "
            + ", ".join(missing)
            + f" (wrap each tier's legs in {START} <tier> / {END} <tier>)"
        )

    changed = []
    # Replace bottom-up: each splice changes the indices of every block below
    # it, so a top-down loop would corrupt the second block.
    for tier in sorted(combos, key=lambda t: blocks[t][0], reverse=True):
        start, end = blocks[tier]
        indent = leading_indent(lines[start])
        extras = parse_block(lines, start, end)
        new_block = render_block(tier, combos[tier], indent, extras)
        if lines[start : end + 1] != new_block:
            changed.append(tier)
        lines[start : end + 1] = new_block
    changed.reverse()

    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = newline if text.endswith(("\n", "\r\n")) else ""
    return newline.join(lines) + trailing, changed


def diff_text(old, new, label):
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def _utf8_streams():
    """Make stdout/stderr UTF-8 so a diff of the config's box-drawing/em-dash
    characters does not crash on a Windows console whose code page is cp1252.
    Unknown glyphs degrade to '?', which is fine for a diff."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Sync LiteLLM tier legs from the OmniRoute combos (single source of truth).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 with a diff when the managed blocks have drifted; change nothing",
    )
    parser.add_argument(
        "--combos",
        default=None,
        help="path to combos.json (default: configuration/omniroute/combos.json)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="path to the LiteLLM config.yaml (default: configuration/litellm/config.yaml)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on success (errors and a --check diff still print)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _utf8_streams()
    combos_path = (
        Path(args.combos)
        if args.combos
        else ROOT / "configuration" / "omniroute" / "combos.json"
    )
    config_path = (
        Path(args.config)
        if args.config
        else ROOT / "configuration" / "litellm" / "config.yaml"
    )

    try:
        # Populate the module maps Leg reads, from the shared registry.
        global PROVIDER_PREFIX, API_BASE, ENV_KEY
        PROVIDER_PREFIX, API_BASE, ENV_KEY = provider_maps()
        combos = combos_refs(combos_path)
        original = config_path.read_text(encoding="utf-8")
        updated, changed = rewrite(original, combos)
    except (OSError, ConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if changed:
            print(diff_text(original, updated, str(config_path)), end="")
            print(
                f"DRIFT: {', '.join(changed)} out of sync with {combos_path}",
                file=sys.stderr,
            )
            return 1
        if not args.quiet:
            print(f"OK: {', '.join(combos)} match {combos_path.name}")
        return 0

    if changed:
        # newline="" disables the Windows \n -> \r\n translation in text mode,
        # so a LF-only config stays LF-only and the diff stays honest.
        with open(config_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
        if not args.quiet:
            print(f"Synced {', '.join(changed)} in {config_path}")
    elif not args.quiet:
        print(f"Already in sync ({', '.join(combos)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
