#!/usr/bin/env python3
"""Hold the web UI and the shipped config schema to one set of keys.

The first `claude-autostart` implementation had the card writing
`resume_prompt_mode` and `interval_minutes` while the scripts read `resume_mode`
and `snapshot_interval_mins`. Nothing failed — the settings were simply saved to
keys no reader ever looked at. This makes that class of drift a test failure.

The web UI declares its keys once, in a `CLAUDE_CFG_KEYS` object literal, which
is the list this compares against `autoos.config.example.json`.

Exit 0 when they agree, 1 (with the difference on stderr) when they do not.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def ui_keys():
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"const CLAUDE_CFG_KEYS = \{(.*?)\};", page, re.S)
    if not match:
        print("web/index.html does not declare CLAUDE_CFG_KEYS", file=sys.stderr)
        sys.exit(1)
    # Keys are the object's property names: `snapshot_interval_mins: "…"`.
    return set(re.findall(r"^\s*([a-z_]+)\s*:", match.group(1), re.M))


def schema_keys():
    example = json.loads((ROOT / "autoos.config.example.json").read_text(encoding="utf-8"))
    if "claude_autostart" not in example:
        print("autoos.config.example.json has no claude_autostart block", file=sys.stderr)
        sys.exit(1)
    return set(example["claude_autostart"])


def main():
    ui, schema = ui_keys(), schema_keys()
    if not ui:
        print("CLAUDE_CFG_KEYS is empty", file=sys.stderr)
        return 1

    unread = ui - schema
    if unread:
        print(f"the UI writes keys nothing reads: {sorted(unread)}", file=sys.stderr)
    # The schema may legitimately carry keys the card does not expose (fallback_cwd
    # is CLI-only), so only the UI -> schema direction is an error.
    return 1 if unread else 0


if __name__ == "__main__":
    sys.exit(main())
