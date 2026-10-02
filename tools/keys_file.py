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

import re
import sys
from pathlib import Path

# A value that contains this anywhere is a placeholder, not a key: REPLACE_WITH_X, sk-REPLACE_ME,
# <REPLACE_WITH_X>. (It was a prefix test for a while; that read sk-REPLACE_ME as a real key.)
PLACEHOLDER = "REPLACE"


def read_keys(path) -> dict:
    """Return {name: value} from a flat keys file, first occurrence winning.

    Missing or unreadable files read as no keys: a machine with nothing
    configured is the common case, not an error. Values carrying the
    ``REPLACE`` placeholder (the shape every tracked ``.example`` uses) are
    dropped — they are truthy and would be written into a client config as if
    they were real keys.

    Parsing rules:
    - Lines starting with # are comments; format is name=value or name: value
    - Quoted values (single or double): the content between the first and the matching quote
    - Unquoted values: cut at the first # preceded by a space or tab (an inline comment), then
      trimmed. This is a change from the plain strip the file used to do: ``key  # note`` is
      ``key``, and a value that really contains " #" must be quoted.
    - A name that appears twice: the FIRST filled-in value wins. The bash launchers that used to
      take the last line (``tail -n1``) now read through this function, so they agree with it.
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
        name = row[:cut].strip()
        raw_val = row[cut + 1:].strip()
        if not name or not raw_val:
            continue
        value = parse_value(raw_val)
        if value and PLACEHOLDER not in value:
            out.setdefault(name, value)
    return out


def parse_value(raw: str) -> str:
    """Parse a value following the original bash keys_value rules."""
    if not raw:
        return ""
    # Quoted values: "..." or '...' - extract between first and matching quote
    if raw[0] == '"':
        end = raw.find('"', 1)
        if end > 0:
            return raw[1:end]
        return raw[1:]  # no closing quote, return rest
    if raw[0] == "'":
        end = raw.find("'", 1)
        if end > 0:
            return raw[1:end]
        return raw[1:]  # no closing quote, return rest
    # Unquoted: cut at first # preceded by space or tab, then trim
    m = re.search(r'([ \t])#', raw)
    if m:
        raw = raw[:m.start() + 1]  # keep the space/tab before #
    return raw.rstrip()


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
