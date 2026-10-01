#!/usr/bin/env python3
"""Gateway key resolution for OmniRoute clients.

Single source of truth for how every AutoOS tool picks the client key from
configuration/api-keys.yml (or AUTOOS_OMNIROUTE_KEY env) when talking to a
gateway. The field name depends on WHICH gateway the client targets:

  - Non-local gateway (AUTOOS_OMNIROUTE_URL set and not loopback) -> omniroute_server
  - Local gateway (unset, 127.0.0.1, localhost, [::1] any port) -> omniroute_<host>

Host name resolution order:
  1. AUTOOS_HOST_NAME env (explicit override)
  2. host_name: from machine-wide host.yml (path from AUTOOS_HOST_CONFIG or platform default)
  3. Short hostname (lower-cased, first label, [^a-z0-9_] -> _)

Legacy fallback (one release, read-only):
  - Local:  `omniroute` (prints one deprecation line naming old + new field)
  - Server: `omniroute_client_<host>` (prints one deprecation line)

Precedence:
  - AUTOOS_OMNIROUTE_KEY env always wins over any file field.
  - Missing key -> clear error naming the EXPECTED field and why (local/non-local),
    mentioning host.yml / AUTOOS_HOST_NAME if the name may be wrong.
    Never prints a key value or the gateway URL.
"""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path
from typing import Optional

# Import the shared key parser that handles both name=value and name: value formats
sys.path.insert(0, str(Path(__file__).resolve().parent))
from keys_file import read_keys

# Notices (hostname fallback, legacy-field deprecation) go to stderr once per process: a
# program that resolves the key twice (one installer, two config writers) must not nag twice.
_NOTICED: set = set()
_QUIET = False


def _notice(message: str) -> None:
    if _QUIET or message in _NOTICED:
        return
    _NOTICED.add(message)
    print(message, file=sys.stderr)


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_ASCII_WS = " " + chr(9) + chr(10) + chr(11) + chr(12) + chr(13)


def is_local_gateway(url: Optional[str]) -> bool:
    """True when the gateway URL points to the local machine (loopback).

    ONE rule, spelled the same in lib/windows/AutoOS.Install.psm1 (and its copy in
    configuration/start-stack.ps1); bash calls this function through the CLI. It is a plain
    string parse on purpose: urlparse, System.Uri and a shell glob each read odd URLs
    differently, and a local key must never be sent to a remote gateway because they disagreed.

      - unset (exactly empty)                          -> local
      - ASCII whitespace around the URL is trimmed; anything left that is not printable ASCII
        (control characters, spaces inside, non-ASCII) or is a backslash -> NON-local
      - no `scheme://`                                 -> non-local
      - the authority ends at the first / ? or #; userinfo is cut at the LAST @
      - a port, when present, is digits only and at most 65535 (an empty port is allowed)
      - local only for the literals 127.0.0.1 and localhost (any case), or a bracketed [::1];
        127.1, 0x7f.1, 2130706433, 127.0.0.2, 0.0.0.0, a trailing dot, an unbracketed ::1 are NOT.
    """
    if url is None or url == "":
        return True
    url = url.strip(_ASCII_WS)
    if not url:
        return False
    if any(not (0x21 <= ord(ch) <= 0x7E) or ch == chr(92) for ch in url):
        return False
    scheme, sep, rest = url.partition("://")
    if not sep or not _SCHEME_RE.match(scheme):
        return False
    for i, ch in enumerate(rest):
        if ch in "/?#":
            rest = rest[:i]
            break
    authority = rest.rsplit("@", 1)[-1]
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return False
        host, tail = authority[1:end], authority[end + 1:]
        bracketed = True
    else:
        host, colon, port_text = authority.partition(":")
        if ":" in port_text:
            return False  # an IPv6 literal without brackets
        tail = colon + port_text
        bracketed = False
    if tail:
        if not tail.startswith(":"):
            return False
        port = tail[1:]
        if port and (not port.isdigit() or not port.isascii() or len(port) > 5 or int(port) > 65535):
            return False
    host = host.lower()
    if bracketed:
        return host == "::1"
    return host in ("127.0.0.1", "localhost")


def _host_config_path(env: Optional[dict] = None) -> Path:
    """Path to the machine-wide host.yml config file.

    Mirrors tools/autoos_overlay.py default_path's platform logic but for
    a SEPARATE file (host.yml, not measured.json).
    """
    env = env or os.environ
    if env.get("AUTOOS_HOST_CONFIG"):
        return Path(os.path.expanduser(os.path.expandvars(env["AUTOOS_HOST_CONFIG"])))
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or os.path.join(
            env.get("USERPROFILE") or os.path.expanduser("~"), "AppData", "Local"
        )
    else:
        base = env.get("XDG_CONFIG_HOME") or os.path.join(
            env.get("HOME") or os.path.expanduser("~"), ".config"
        )
    return Path(base) / "autoos" / "host.yml"


def _normalize_hostname(name: str) -> str:
    """Normalize a hostname to the field-safe form: lower, first label, [^a-z0-9_] -> _."""
    if not name:
        return ""
    # Lowercase first
    name = name.lower()
    # Cut at first '.'
    name = name.split(".", 1)[0]
    # Replace any char outside [a-z0-9_] with _
    return re.sub(r"[^a-z0-9_]", "_", name)


def host_name(env: Optional[dict] = None) -> str:
    """Resolve the host name for the local gateway field.

    Order:
      1. AUTOOS_HOST_NAME env
      2. host_name: from host.yml
      3. Short hostname (with a notice printed to stderr)
    """
    env = env or os.environ

    # 1. Explicit env override
    if env.get("AUTOOS_HOST_NAME"):
        return _normalize_hostname(env["AUTOOS_HOST_NAME"])

    # 2. host.yml
    host_file = _host_config_path(env)
    if host_file.is_file():
        try:
            for line in host_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if re.match(r"^host_name\s*:", line):
                    _, _, value = line.partition(":")
                    val = value.strip().strip("\"'")
                    if val:
                        return _normalize_hostname(val)
        except OSError:
            pass

    # 3. Short hostname (with notice)
    try:
        fqdn = socket.gethostname()
    except Exception:
        fqdn = "localhost"
    normalized = _normalize_hostname(fqdn)
    _notice(
        f"AutoOS: using hostname '{normalized}' for omniroute key field "
        f"(set AUTOOS_HOST_NAME or host_name in {host_file} to override)"
    )
    return normalized


def client_key_field(env: Optional[dict] = None) -> str:
    """Return the api-keys.yml field name for the client key.

    Non-local gateway -> omniroute_server
    Local gateway -> omniroute_<host>
    """
    env = env or os.environ
    url = env.get("AUTOOS_OMNIROUTE_URL", "")
    if not is_local_gateway(url):
        return "omniroute_server"
    host = host_name(env)
    return f"omniroute_{host}"


def resolve_client_key(
    env: Optional[dict] = None,
    keys_file: Optional[Path] = None,
) -> str:
    """Resolve the OmniRoute client key.

    Precedence:
      1. AUTOOS_OMNIROUTE_KEY env (explicit, wins always)
      2. New field from api-keys.yml (omniroute_server or omniroute_<host>)
      3. Legacy field (one release read-only fallback, prints deprecation)
      4. Error naming the expected field

    Never prints a key value or the gateway URL.
    """
    env = env or os.environ

    # 1. Explicit env always wins
    if env.get("AUTOOS_OMNIROUTE_KEY"):
        return env["AUTOOS_OMNIROUTE_KEY"]

    # Determine which field to look for
    field = client_key_field(env)
    is_local = is_local_gateway(env.get("AUTOOS_OMNIROUTE_URL", ""))

    # Default keys file location
    if keys_file is None:
        root = Path(__file__).resolve().parent.parent
        keys_file = root / "configuration" / "api-keys.yml"

    # Read all keys using the shared parser (handles both name=value and name: value)
    keys = read_keys(keys_file) if keys_file and keys_file.is_file() else {}

    # 2. New field (case-insensitive match per keys_file.py behavior)
    # read_keys preserves case, so we need case-insensitive lookup
    for k, v in keys.items():
        if k.lower() == field.lower():
            return v

    # 3. Legacy fallback (one release, read-only)
    legacy_field = "omniroute" if is_local else f"omniroute_client_{host_name(env)}"
    for k, v in keys.items():
        if k.lower() == legacy_field.lower():
            _notice(f"api-keys.yml: '{legacy_field}' is deprecated, rename it to '{field}'")
            return v

    # 4. Missing - clear error
    context = "a local gateway" if is_local else "a non-local gateway"
    host_file = _host_config_path(env)
    raise KeyError(
        f"No OmniRoute client key for {context}. Expected field '{field}' in "
        f"{keys_file} (or set AUTOOS_OMNIROUTE_KEY). "
        f"Host name from AUTOOS_HOST_NAME or {host_file} (host_name:), "
        f"falling back to short hostname."
    )


def main(argv: Optional[list] = None) -> int:
    """CLI used by the shell and PowerShell callers, so there is ONE resolver.

      autoos_gateway_key.py resolve [--optional] [--no-notice] [KEYS_FILE]
          key on stdout (no trailing noise); notices and errors on stderr.
          A missing key exits 1 with the message, or with --optional exits 0 and prints nothing.
      autoos_gateway_key.py is-local URL
          exit 0 when the URL is a loopback gateway, 1 when not (the one classifier bash uses).
      autoos_gateway_key.py info
          which gateway class, host name and field this environment resolves to (no key).
    """
    global _QUIET
    import json
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args.pop(0) if args else "info"
    if cmd == "resolve":
        optional = "--optional" in args
        _QUIET = "--no-notice" in args
        rest = [a for a in args if not a.startswith("--")]
        keys_file = Path(rest[0]) if rest and rest[0] else None
        try:
            key = resolve_client_key(os.environ, keys_file)
        except KeyError as exc:
            if optional:
                return 0
            print(str(exc.args[0]) if exc.args else str(exc), file=sys.stderr)
            return 1
        sys.stdout.write(key + chr(10))
        return 0
    if cmd == "is-local":
        # exit 0 = local, 1 = not local; the URL is the one argument ("" = unset = local)
        return 0 if is_local_gateway(args[0] if args else "") else 1
    if cmd == "info":
        env = dict(os.environ)
        print(json.dumps({
            "is_local": is_local_gateway(env.get("AUTOOS_OMNIROUTE_URL", "")),
            "host_name": host_name(env),
            "field": client_key_field(env),
        }, indent=2))
        return 0
    print("usage: autoos_gateway_key.py resolve [--optional] [--no-notice] [KEYS_FILE] | is-local URL | info", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
