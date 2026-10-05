#!/usr/bin/env python3
"""oc_l1_http.py - HTTP and state helpers for the O1-LITE launcher.

Small v2 API client (Basic auth, user 'opencode'), health poll, atomic state
file read/write, password scrub, and raw lane option lookup.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_l1  # noqa: E402

AUTH_USER = "opencode"
HOST = "127.0.0.1"


class ServerDown(Exception):
    """The opencode server on 127.0.0.1:<port> does not answer at all."""


def _scrub(text, password):
    """Remove the Basic-auth password from any text we may emit."""
    if password:
        text = text.replace(password, "***")
    return text


def _basic_header(password):
    return "Basic " + base64.b64encode(
        ("%s:%s" % (AUTH_USER, password)).encode("utf-8")
    ).decode("ascii")


def _request(port, method, path, body=None, password=""):
    """One HTTP call to 127.0.0.1:<port>; returns (status, json-or-None).

    Connection-level failures raise ServerDown (scrubbed); HTTP error
    statuses are returned, not raised.
    """
    url = "http://127.0.0.1:%d%s" % (port, path)
    headers = {"Authorization": _basic_header(password)}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ServerDown(_scrub(str(getattr(e, "reason", e)), password))


def _data(payload):
    if isinstance(payload, dict):
        return payload.get("data")
    return payload


def _wait_healthy(port, password, timeout_s):
    """Poll GET /api/session/active every 0.5 s until it answers 200."""
    deadline = time.monotonic() + max(0.1, timeout_s)
    while True:
        try:
            if _request(port, "GET", "/api/session/active",
                        password=password)[0] == 200:
                return True
        except ServerDown:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _read_state(path):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(path, state):
    """Atomic write: temp file in the same directory + os.replace."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise oc_l1.LaneError("cannot write state file %s: %s" % (p, e))


def _num(value, default):
    try:
        if isinstance(value, bool):
            return default
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def _lane_opt(lane, args, key, default):
    """Read an optional RAW lane key (the A1 validator drops them)."""
    try:
        cfg = Path(args.config) if args.config else oc_l1.default_config_path()
        raw = oc_l1.load_config(cfg).get(lane["name"])
    except (oc_l1.LaneError, OSError):
        return default
    return _num(raw.get(key), default) if isinstance(raw, dict) else default
