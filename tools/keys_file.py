"""Read a flat AutoOS keys file — the one home for that parse.

Two shapes exist and both are live:

* ``name=value``  — the ``api_keys.conf`` format the installers read
  (``~/.config/autoos/api_keys.conf``, or the legacy
  ``agent-skills/secrets/api_keys.conf`` a machine may still hold);
* ``name: value`` — ``configuration/api-keys.yml``, the git-ignored file the
  browser page writes and ``tools/mirror-litellm-env.py`` mirrors.

Every consumer used to carry its own reader, and each one understood only its
own shape. The installers' conf reader handed a ``.yml`` returned an empty dict
— a machine with keys that behaves, down to the log output, like a machine
without them. So the parse lives here and consumers call it.

Keys keep their case (``catalog/ai-registry.json`` spells SambaNova with
capitals and the LiteLLM mirror looks it up case-sensitively); callers that
want a case-insensitive lookup normalise on their side.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLACEHOLDER = "REPLACE"


def read_keys(path) -> dict:
    """Return {name: value} from a flat keys file, first occurrence winning.

    Missing or unreadable files read as no keys: a machine with nothing
    configured is the common case, not an error. Values carrying the
    ``REPLACE`` placeholder (the shape every tracked ``.example`` uses) are
    dropped — they are truthy and would be written into a client config as if
    they were real keys.
    """
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        row = line.strip()
        if not row or row.startswith("#"):
            continue
        eq, colon = row.find("="), row.find(":")
        if eq < 0 and colon < 0:
            continue
        cut = eq if colon < 0 else (colon if eq < 0 else min(eq, colon))
        name, value = row[:cut].strip(), row[cut + 1:].strip().strip("\"'")
        if name and value and PLACEHOLDER not in value:
            out.setdefault(name, value)
    return out


def main(argv=None) -> int:
    """`keys_file.py <file> <key>` prints one value, or nothing.

    The shell side of the installers needs a single lookup and cannot import
    this module; printing the empty string for a key that is not configured
    keeps every caller's `[[ -n ... ]]` honest instead of failing a `set -e`
    script over a machine that has no keys.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: keys_file.py <file> <key>", file=sys.stderr)
        return 2
    print(read_keys(argv[0]).get(argv[1], ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
