#!/usr/bin/env python3
"""Keep every client's gateway model list in sync with catalog/ide-models.json.

catalog/ide-models.json is the single source of truth for which gateway
model ids each surface offers, their display names and their token windows
(combos.json keeps owning leg ORDER). Before it existed the same list was
hand-maintained in about eight places and drifted: the 1M tier was 1000000
in opencode.jsonc, 1048576 in the Zed writers and tier profiles, 128000 in
configuration/openhands/config.toml, with three different output budgets.

This tool regenerates the static copies; the Zed and V1 OpenCode writers in
lib/ read the catalog at run time and need no sync:

  opencode.jsonc
      ONLY the lines between two whole-line markers inside each gateway
      provider's "models" object:

          // AUTOOS-MANAGED-START <gateway>
          ...
          // AUTOOS-MANAGED-END <gateway>

      Whole-line `//` on purpose: the suites and tools/audit-router.py strip
      comments with ^\\s*//.*$, so a marker must never share a line with JSON.
      The region is part of a JSON object, so commas are the hazard: the last
      generated entry gets a trailing comma only when a hand-written entry
      follows the END marker, and the line before START must already end in
      `{` or `,`. The result is parsed before anything is written.
  configuration/openhands/tier-profiles.json
      max_input_tokens / max_output_tokens of every omniroute-/litellm- tier.
      Membership is checked both ways against the catalog's `openhands`
      surface but never rewritten: the spec's ORDER is the OpenHands push
      priority (the app keeps at most 10 profiles), a human decision.
  configuration/openhands/config.toml
      the max_input_tokens / max_output_tokens lines of every [llm] /
      [llm.*] table whose model is openai/<catalog id>. Tables without those
      lines are left without them; a table naming a model the catalog does
      not know is reported (WARNING, stderr) and left untouched.

Everything else in those files - comments, prose, hand entries, whitespace -
is left byte-for-byte untouched, and the newline style is preserved, so a
re-run is byte-identical.

Usage:
    python3 tools/sync-ide-models.py [--check] [--quiet] [--catalog PATH]
        [--opencode PATH] [--tier-profiles PATH] [--openhands-toml PATH]

    (default)   rewrite the managed parts in place; report what changed
    --check     change nothing; exit 1 with a unified diff when drifted

Reads no key and prints none: model ids, names and numbers only.

Exit codes:
    0   already in sync (--check), or the write succeeded
    1   drifted (--check only)
    2   unusable input: a marker missing, doubled or mismatched, a comma the
        region cannot be spliced next to, a catalog entry that fails
        validation, opencode.jsonc or the tier spec naming a model the
        catalog does not offer there, or an unreadable file. Nothing is
        written. (A config.toml table with an unknown model only warns.)
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWAYS = ("omniroute", "litellm")
SURFACES = ("opencode", "zed", "openhands")
REQUIRED = {"id": str, "name": str, "context": int, "output": int, "surfaces": dict}

START = "// AUTOOS-MANAGED-START"
END = "// AUTOOS-MANAGED-END"
_MARKER_RE = re.compile(r"^[ \t]*//[ \t]*AUTOOS-MANAGED-(START|END)(?:[ \t]+(\S+))?[ \t]*$")
_COMMENT_RE = re.compile(r"^[ \t]*//")


class ConfigError(RuntimeError):
    """An input cannot be safely synced."""


# --------------------------------------------------------------------- catalog


def load_catalog(path):
    """The validated model list, in picker order."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        models = doc["models"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(models, list) or not models:
        raise ConfigError(f"{path}: models must be a non-empty list")
    seen = set()
    for m in models:
        if not isinstance(m, dict):
            raise ConfigError(f"{path}: every model must be an object")
        label = m.get("id", "<no id>")
        for key, kind in REQUIRED.items():
            value = m.get(key)
            # bool is an int subclass; a true/false window is still a typo.
            if not isinstance(value, kind) or isinstance(value, bool):
                raise ConfigError(f"{path}: {label}: {key} must be a {kind.__name__}")
        if m["context"] <= 0 or m["output"] <= 0:
            raise ConfigError(f"{path}: {label}: context and output must be positive")
        if label in seen:
            raise ConfigError(f"{path}: duplicate id {label}")
        seen.add(label)
        effort = m.get("reasoning_effort")
        if effort is not None and not isinstance(effort, str):
            raise ConfigError(f"{path}: {label}: reasoning_effort must be a string")
        if not m["surfaces"]:
            raise ConfigError(f"{path}: {label}: surfaces is empty")
        for gateway, surfaces in m["surfaces"].items():
            if gateway not in GATEWAYS:
                raise ConfigError(f"{path}: {label}: unknown gateway {gateway!r} (known: {', '.join(GATEWAYS)})")
            if not isinstance(surfaces, list) or not surfaces:
                raise ConfigError(f"{path}: {label}: surfaces.{gateway} must be a non-empty list")
            for surface in surfaces:
                if surface not in SURFACES:
                    raise ConfigError(f"{path}: {label}: unknown surface {surface!r} (known: {', '.join(SURFACES)})")
    return models


def offered(models, gateway, surface):
    """Catalog models `surface` lists through `gateway`, in picker order."""
    return [m for m in models if surface in m["surfaces"].get(gateway, ())]


# ------------------------------------------------------------ opencode.jsonc


def marker_at(line):
    m = _MARKER_RE.match(line)
    return (m.group(1), m.group(2)) if m else None


def locate_blocks(lines):
    """gateway -> (start_idx, end_idx) of its managed block, both inclusive."""
    blocks = {}
    i = 0
    while i < len(lines):
        marker = marker_at(lines[i])
        if marker and marker[0] == "START":
            name = marker[1]
            if name is None:
                raise ConfigError(f"{START} at line {i + 1} names no gateway")
            if name in blocks:
                raise ConfigError(f"duplicate {START} {name}")
            end = None
            for j in range(i + 1, len(lines)):
                other = marker_at(lines[j])
                if other and other[0] == "START":
                    raise ConfigError(f"{START} {name} is not closed before line {j + 1}")
                if other and other[0] == "END":
                    if other[1] != name:
                        raise ConfigError(f"{START} {name} is closed by {END} {other[1]} at line {j + 1}")
                    end = j
                    break
            if end is None:
                raise ConfigError(f"{START} {name} has no matching {END}")
            blocks[name] = (i, end)
            i = end + 1
            continue
        if marker and marker[0] == "END":
            raise ConfigError(f"stray {END} at line {i + 1}")
        i += 1
    return blocks


def _significant(lines, index, step):
    """The nearest line from `index` in direction `step` that is JSON, not blank/comment."""
    while 0 <= index < len(lines):
        text = lines[index].strip()
        if text and not _COMMENT_RE.match(lines[index]):
            return text
        index += step
    return ""


def render_entries(models, indent, trailing_comma):
    lines = []
    for n, m in enumerate(models):
        limit = '{ "context": %d, "output": %d }' % (m["context"], m["output"])
        last = n == len(models) - 1
        lines += [
            f"{indent}{json.dumps(m['id'], ensure_ascii=False)}: {{",
            f'{indent}  "modelID": {json.dumps(m["id"], ensure_ascii=False)},',
            f'{indent}  "name": {json.dumps(m["name"], ensure_ascii=False)},',
            f'{indent}  "limit": {limit}',
            f"{indent}}}" + ("," if (not last or trailing_comma) else ""),
        ]
    return lines


def strip_jsonc(text):
    """The suites' comment strip: whole-line // comments only."""
    return re.sub(r"(?m)^\s*//.*$", "", text)


def rewrite_opencode(text, models, warn=None):
    """(new_text, changed) for opencode.jsonc; keeps the newline style."""
    lines = text.splitlines()
    blocks = locate_blocks(lines)
    for name in blocks:
        if name not in GATEWAYS:
            raise ConfigError(f"opencode.jsonc: managed block for unknown gateway {name!r}")
    wanted = {g: offered(models, g, "opencode") for g in GATEWAYS}
    for gateway, members in wanted.items():
        if members and gateway not in blocks:
            raise ConfigError(
                f"opencode.jsonc has no managed block for {gateway} (wrap providers.{gateway}.models "
                f"in {START} {gateway} / {END} {gateway})")
        if gateway in blocks and not members:
            raise ConfigError(f"no catalog model lists opencode for {gateway}, but opencode.jsonc manages it")

    changed = False
    # Bottom-up: a splice shifts every index below it.
    for gateway in sorted(blocks, key=lambda g: blocks[g][0], reverse=True):
        start, end = blocks[gateway]
        before = _significant(lines, start - 1, -1)
        if not (before.endswith("{") or before.endswith(",")):
            raise ConfigError(
                f"opencode.jsonc: the line before {START} {gateway} must end in '{{' or ',' "
                f"(found {before[-40:]!r}) - a hand entry above the region needs its comma")
        after = _significant(lines, end + 1, 1)
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        body = render_entries(wanted[gateway], indent, trailing_comma=not after.startswith("}"))
        block = [lines[start]] + body + [lines[end]]
        if lines[start : end + 1] != block:
            changed = True
        lines[start : end + 1] = block

    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = newline if text.endswith(("\n", "\r\n")) else ""
    new_text = newline.join(lines) + trailing

    # Never hand back a file the clients cannot load: parse it the way the
    # suites do and confirm every generated entry landed in its provider.
    try:
        parsed = json.loads(strip_jsonc(new_text))
    except ValueError as exc:
        raise ConfigError(f"opencode.jsonc would not parse after the sync: {exc}") from exc
    for gateway, members in wanted.items():
        got = ((parsed.get("providers") or {}).get(gateway) or {}).get("models") or {}
        for m in members:
            entry = got.get(m["id"]) or {}
            if entry.get("modelID") != m["id"]:
                raise ConfigError(
                    f"opencode.jsonc: {START} {gateway} is not inside providers.{gateway}.models")
    return new_text, changed


# ------------------------------------------------------- tier-profiles.json


def rewrite_tier_profiles(text, models, warn=None):
    """(new_text, changed): mirror token windows, check membership both ways."""
    try:
        spec = json.loads(text)
        tiers = spec["tiers"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"tier-profiles.json: {exc}") from exc
    by_id = {m["id"]: m for m in models}
    seen = set()
    for tier in tiers:
        gateway = tier.get("gateway", "omniroute")
        if gateway not in GATEWAYS:
            continue  # a direct-provider tier (openrouter): catalog/llm-models.json owns it
        model_ref = str(tier.get("model", ""))
        mid = model_ref[len("openai/"):] if model_ref.startswith("openai/") else None
        m = by_id.get(mid)
        if m is None or "openhands" not in m["surfaces"].get(gateway, ()):
            raise ConfigError(
                f"tier-profiles.json: {tier.get('id')} names {model_ref!r}, which "
                f"catalog/ide-models.json does not offer to openhands through {gateway}")
        if tier.get("id") != f"{gateway}-{mid}":
            raise ConfigError(f"tier-profiles.json: {tier.get('id')} should be named {gateway}-{mid}")
        tier["max_input_tokens"] = m["context"]
        tier["max_output_tokens"] = m["output"]
        seen.add(tier["id"])
    for gateway in GATEWAYS:
        for m in offered(models, gateway, "openhands"):
            if f"{gateway}-{m['id']}" not in seen:
                raise ConfigError(
                    f"tier-profiles.json has no {gateway}-{m['id']} but the catalog offers it to "
                    "openhands - add it where it belongs in the push order (or drop the surface)")
    newline = "\r\n" if "\r\n" in text else "\n"
    new_text = json.dumps(spec, indent=2, ensure_ascii=False).replace("\n", newline) + newline
    return new_text, new_text != text


# ---------------------------------------------------------------- config.toml

_TABLE_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
_MODEL_RE = re.compile(r'^\s*model\s*=\s*"openai/([^"]+)"\s*(#.*)?$')
_TOKENS_RE = re.compile(r"^(\s*(max_input_tokens|max_output_tokens)\s*=\s*)\d+(.*)$")


def rewrite_openhands_toml(text, models, warn=None):
    """(new_text, changed): the token lines of [llm] / [llm.*] tables.

    A table whose openai/<id> the catalog does not know is the user's own
    dev-path entry: `warn` is told and the table is left untouched - one
    unknown model must not stop the sync of every other surface.
    """
    by_id = {m["id"]: m for m in models}
    lines = text.splitlines()
    # Pass 1: which catalog model each llm table names.
    table_model, current = {}, None
    for n, line in enumerate(lines):
        table = _TABLE_RE.match(line)
        if table:
            name = table.group(1).strip()
            current = name if name == "llm" or name.startswith("llm.") else None
            continue
        model = _MODEL_RE.match(line) if current else None
        if model:
            if model.group(1) in by_id:
                table_model[current] = by_id[model.group(1)]
            elif warn is not None:
                warn(f"[{current}] line {n + 1}: openai/{model.group(1)} is not in "
                     "catalog/ide-models.json - that table is left untouched")
    # Pass 2: rewrite the numbers in place.
    changed, current = False, None
    for n, line in enumerate(lines):
        table = _TABLE_RE.match(line)
        if table:
            current = table.group(1).strip()
            continue
        tokens = _TOKENS_RE.match(line)
        m = table_model.get(current) if tokens else None
        if m is None:
            continue
        value = m["context"] if tokens.group(2) == "max_input_tokens" else m["output"]
        new = f"{tokens.group(1)}{value}{tokens.group(3)}"
        if new != line:
            lines[n] = new
            changed = True
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = newline if text.endswith(("\n", "\r\n")) else ""
    return newline.join(lines) + trailing, changed


# ----------------------------------------------------------------------- CLI

TARGETS = (
    ("opencode", "opencode.jsonc", rewrite_opencode),
    ("tier_profiles", "configuration/openhands/tier-profiles.json", rewrite_tier_profiles),
    ("openhands_toml", "configuration/openhands/config.toml", rewrite_openhands_toml),
)


def diff_text(old, new, path):
    try:
        label = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        label = str(path)
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{label}", tofile=f"b/{label}"))


def _utf8_streams():
    """A diff of em-dashes must not crash a cp1252 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Sync every client's gateway model list from catalog/ide-models.json.")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 with a diff when a surface has drifted; change nothing")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing on success (errors and a --check diff still print)")
    parser.add_argument("--catalog", default=None, help="default: catalog/ide-models.json")
    parser.add_argument("--opencode", default=None, help="default: opencode.jsonc")
    parser.add_argument("--tier-profiles", default=None,
                        help="default: configuration/openhands/tier-profiles.json")
    parser.add_argument("--openhands-toml", default=None,
                        help="default: configuration/openhands/config.toml")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _utf8_streams()
    catalog = Path(args.catalog) if args.catalog else ROOT / "catalog" / "ide-models.json"
    paths = {
        "opencode": Path(args.opencode) if args.opencode else ROOT / "opencode.jsonc",
        "tier_profiles": Path(args.tier_profiles) if args.tier_profiles
        else ROOT / "configuration" / "openhands" / "tier-profiles.json",
        "openhands_toml": Path(args.openhands_toml) if args.openhands_toml
        else ROOT / "configuration" / "openhands" / "config.toml",
    }
    # Every target is computed before any is written: one unusable file
    # leaves all of them untouched.
    results, warnings = [], []
    try:
        models = load_catalog(catalog)
        for key, label, rewrite in TARGETS:
            # newline="" keeps \r\n visible, so the newline style survives.
            with open(paths[key], encoding="utf-8", newline="") as fh:
                original = fh.read()
            try:
                updated, changed = rewrite(original, models,
                                           lambda msg, where=paths[key]: warnings.append(f"{where}: {msg}"))
            except ConfigError as exc:
                raise ConfigError(f"{paths[key]}: {exc}") from exc
            results.append((paths[key], label, original, updated, changed))
    except (OSError, ConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    # Reported in every mode, --quiet included: each one is a table left alone.
    for message in warnings:
        print(f"WARNING: {message}", file=sys.stderr)

    drifted = [r for r in results if r[4]]
    if args.check:
        if drifted:
            for path, _, original, updated, _ in drifted:
                print(diff_text(original, updated, str(path)), end="")
            print("DRIFT: %s out of sync with %s (run python3 tools/sync-ide-models.py)"
                  % (", ".join(r[1] for r in drifted), catalog.name), file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"OK: {', '.join(r[1] for r in results)} match {catalog.name}")
        return 0

    for path, _, _, updated, _ in drifted:
        # newline="" disables the Windows \n -> \r\n translation: a LF file
        # stays LF, a CRLF file stays CRLF.
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
    if not args.quiet:
        if drifted:
            print(f"Synced {', '.join(r[1] for r in drifted)} from {catalog.name}")
        else:
            print(f"Already in sync ({', '.join(r[1] for r in results)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
