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


def is_local_gateway(url: str) -> bool:
    """True when the gateway URL points to the local machine (loopback)."""
    if not url:
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # If no scheme, it's not a valid gateway URL -> treat as non-local
        if not parsed.scheme:
            return False
        host = parsed.hostname or ""
    except Exception:
        # If we can't parse it, treat as non-local to be safe
        return False
    if not host:
        return False
    host_l = host.lower()
    return host_l in ("127.0.0.1", "localhost", "::1")


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
            for line in host_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("host_name:"):
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
    print(
        f"AutoOS: using hostname '{normalized}' for omniroute key field "
        f"(set AUTOOS_HOST_NAME or host_name in {host_file} to override)",
        file=sys.stderr,
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


def _read_flat_key(keys_file: Path, field: str) -> Optional[str]:
    """Read a single `field: value` line from a flat key file (case-insensitive key match)."""
    try:
        for line in keys_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            if key.strip().lower() == field.lower():
                value = value.strip().strip("\"'")
                if value and not value.startswith("REPLACE_WITH_"):
                    return value
    except OSError:
        pass
    return None


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

    # 2. New field
    if keys_file and keys_file.is_file():
        key = _read_flat_key(keys_file, field)
        if key:
            return key

    # 3. Legacy fallback (one release, read-only)
    legacy_field = "omniroute" if is_local else f"omniroute_client_{host_name(env)}"
    if keys_file and keys_file.is_file():
        legacy_key = _read_flat_key(keys_file, legacy_field)
        if legacy_key:
            print(
                f"api-keys.yml: '{legacy_field}' is deprecated, rename it to '{field}'",
                file=sys.stderr,
            )
            return legacy_key

    # 4. Missing - clear error
    context = "a local gateway" if is_local else "a non-local gateway"
    host_file = _host_config_path(env)
    raise KeyError(
        f"No OmniRoute client key for {context}. Expected field '{field}' in "
        f"{keys_file} (or set AUTOOS_OMNIROUTE_KEY). "
        f"Host name from AUTOOS_HOST_NAME or {host_file} (host_name:), "
        f"falling back to short hostname."
    )


if __name__ == "__main__":
    # Quick manual test
    import json
    env = dict(os.environ)
    print(json.dumps({
        "is_local": is_local_gateway(env.get("AUTOOS_OMNIROUTE_URL", "")),
        "host_name": host_name(env),
        "field": client_key_field(env),
    }, indent=2))