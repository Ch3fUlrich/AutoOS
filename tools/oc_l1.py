#!/usr/bin/env python3
"""oc_l1.py - OpenCode L1 launch profile (O1-LITE).

One project, one `opencode serve` (localhost only), one session, relaunched
from a HANDOFF card. Subcommands: `render`, `start` (tools/oc_l1_serve.py,
with the bash-guard canary of tools/oc_l1_canary.py) and `status`.

render
  Loads the host-local lane config (never committed):
    POSIX:   ${XDG_CONFIG_HOME:-~/.config}/autoos/oc-l1.json
    Windows: %LOCALAPPDATA%\\autoos\\oc-l1.json
    (override: --config PATH)
  and writes a SCRATCH opencode config <scratch_dir>/opencode.json - never
  the user's global config:

  * The file uses opencode 2.0.12's OWN schema (tools/oc_l1_render.py): "model" pins
    <provider>/<modelID>; the singular "provider" block declares ONLY the pinned model(s) (models keyed by the
    gateway model id; "key"/"fallback_key" are optional display names).
  * Provider options hold environment variable NAMES only ({env:AUTOOS_OMNIROUTE_URL}/v1,
    {env:AUTOOS_OMNIROUTE_KEY}); no secret value is ever written. The base URL default
    http://127.0.0.1:20128 is applied at start time, not embedded in the rendered file.
  * "instructions": the configured path list (default: AGENTS.md + handoff).
  * "mcp": a FLAT map of every server of the repo opencode.jsonc, enabled: true only for the lane's "mcp" list
    (default ["autoos-agent"]; its environment pins AUTOOS_WORKERS_DIR); an enabled name missing from the repo
    file is a config error.
  * "plugins": the configured plugin DIRECTORIES (key omitted when empty); "permission" as in the render module.
  * No "server" block: the hostname (127.0.0.1) and the lane port (default: stable sha256-of-name hash in
    47200-47299) are command-line flags of `opencode serve`.

start order: spawn, health, create the pilot session, run the bash-guard canary, and ONLY after a DENIED canary
post the pilot's first prompt. Canary not denied -> the pilot session stays idle, the server stays up for
supervised use, exit 5 (also for a second `start`).

Exit codes: 0 ok - 2 config/validation error - 4 health timeout - 5 canary not denied
(UNATTENDED-REFUSED); `status`: 0 live - 1 silent - 2 dead.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:20128"  # start-time default (step A2)
PORT_MIN = 47200
PORT_MAX = 47299
NOT_IMPLEMENTED = "not implemented in step A1"
_IDENT_RE = re.compile(r"[A-Z_][A-Z0-9_]*\Z")

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_l1_render import (ENV_KEY, ENV_URL, RENDERED_FILENAME, LaneError, _strip_jsonc,  # noqa: E402,F401
                          render)


def default_config_path():
    """Host-local config path, per platform. Never lives in the repo."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autoos" / "oc-l1.json"


def derive_port(name):
    """Stable per-name port in 47200-47299 (sha256, platform independent)."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return PORT_MIN + int.from_bytes(digest[:4], "big") % (PORT_MAX - PORT_MIN + 1)


def load_config(path):
    """Load the lane table; keys starting with '_' are comment entries."""
    if not path.is_file():
        raise LaneError(
            "config not found: %s (copy configuration/oc-l1.example.json there)" % path
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise LaneError("cannot read config %s: %s" % (path, e))
    if not isinstance(data, dict) or not data:
        raise LaneError("config %s must be a non-empty JSON object of lanes" % path)
    lanes = {}
    for key, val in data.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, dict):
            raise LaneError("lane '%s' in %s must be a JSON object" % (key, path))
        lanes[key] = val
    return lanes


def validate_lane(lane, name):
    """Validate one lane, apply defaults, return the resolved lane dict."""
    if "name" in lane and lane["name"] != name:
        raise LaneError("lane '%s': 'name' field must equal the lane key" % name)

    def need_str(key):
        v = lane.get(key)
        if not isinstance(v, str) or not v.strip():
            raise LaneError(
                "lane '%s': '%s' is required (non-empty string)" % (name, key)
            )
        return v

    cwd = need_str("cwd")
    handoff = need_str("handoff")
    opencode_bin = need_str("opencode_bin")
    if not os.path.isabs(opencode_bin):
        raise LaneError(
            "lane '%s': 'opencode_bin' must be an absolute path to the opencode "
            "binary (explicit binary; never a PATH lookup, never the desktop "
            "service)" % name
        )
    password_env = need_str("password_env")
    if not _IDENT_RE.match(password_env):
        raise LaneError(
            "lane '%s': 'password_env' must be an environment variable name "
            "matching [A-Z_][A-Z0-9_]* (a value or a path here would be a "
            "secret leak)" % name
        )

    model = lane.get("model")
    if not isinstance(model, dict):
        raise LaneError("lane '%s': 'model' object is required" % name)
    if model.get("provider") != "omniroute":
        raise LaneError("lane '%s': model.provider must be 'omniroute'" % name)
    mkey, mid = model.get("key"), model.get("modelID")
    if not isinstance(mkey, str) or not mkey or not isinstance(mid, str) or not mid:
        raise LaneError("lane '%s': model.key and model.modelID are required" % name)
    fkey, fid = model.get("fallback_key"), model.get("fallback_modelID")
    if (fkey is None) != (fid is None):
        raise LaneError(
            "lane '%s': model.fallback_key and model.fallback_modelID must be "
            "set together" % name
        )
    if fkey is not None and (
        not isinstance(fkey, str) or not fkey or not isinstance(fid, str) or not fid
    ):
        raise LaneError(
            "lane '%s': model.fallback_key and model.fallback_modelID must be "
            "non-empty strings when set" % name
        )

    mcp = lane.get("mcp", ["autoos-agent"])
    if (
        not isinstance(mcp, list)
        or not mcp
        or not all(isinstance(x, str) and x for x in mcp)
        or len(set(mcp)) != len(mcp)
    ):
        raise LaneError(
            "lane '%s': 'mcp' must be a non-empty list of unique server names" % name
        )

    port = lane.get("serve_port")
    if port is None:
        port = derive_port(name)
    elif isinstance(port, bool) or not isinstance(port, int) or not (
        PORT_MIN <= port <= PORT_MAX
    ):
        raise LaneError(
            "lane '%s': 'serve_port' must be an integer in %d-%d"
            % (name, PORT_MIN, PORT_MAX)
        )

    instructions = lane.get("instructions", ["AGENTS.md", handoff])
    if not isinstance(instructions, list) or not instructions or not all(
        isinstance(x, str) and x for x in instructions
    ):
        raise LaneError("lane '%s': 'instructions' must be a non-empty list of paths" % name)

    plugins = lane.get("plugins", [])
    if not isinstance(plugins, list) or not all(isinstance(x, str) and x for x in plugins):
        raise LaneError("lane '%s': 'plugins' must be a list of paths" % name)

    scratch_dir = lane.get("scratch_dir")
    if scratch_dir is None:
        scratch_dir = str(Path(tempfile.gettempdir()) / "autoos-oc-l1" / name)
    elif not isinstance(scratch_dir, str) or not scratch_dir or not os.path.isabs(scratch_dir):
        raise LaneError("lane '%s': 'scratch_dir' must be an absolute path" % name)

    state_file = lane.get("state_file")
    if state_file is None:
        state_file = str(Path(scratch_dir) / ("oc-l1-%s.state.json" % name))
    elif not isinstance(state_file, str) or not state_file or not os.path.isabs(state_file):
        raise LaneError("lane '%s': 'state_file' must be an absolute path" % name)

    workers_dir = lane.get("workers_dir")
    if workers_dir is not None and (
        not isinstance(workers_dir, str) or not workers_dir or not os.path.isabs(workers_dir)
    ):
        raise LaneError("lane '%s': 'workers_dir' must be an absolute path" % name)

    hb = lane.get("heartbeat_file")
    if hb is not None and (
        not isinstance(hb, str) or not hb or not (os.path.isabs(hb) or hb.startswith("/") or hb.startswith("\\"))
    ):
        raise LaneError("lane '%s': 'heartbeat_file' must be an absolute path" % name)
    tout = lane.get("canary_timeout_s")
    if tout is not None and (not isinstance(tout, (int, float)) or tout <= 0):
        raise LaneError("lane '%s': 'canary_timeout_s' must be positive" % name)

    return {
        "name": name,
        "cwd": cwd,
        "handoff": handoff,
        "opencode_bin": opencode_bin,
        "password_env": password_env,
        "model": model,
        "mcp": list(mcp),
        "serve_port": port,
        "instructions": list(instructions),
        "plugins": list(plugins),
        "scratch_dir": scratch_dir,
        "state_file": state_file,
        "heartbeat_file": hb or str(Path(scratch_dir) / "heartbeat.json"),
        "canary_timeout_s": tout if tout is not None else 120,
        "workers_dir": workers_dir or str(Path(cwd) / "logs" / "workers"),
        # Lane c (2026-10-05): optional outside-folder allowlist, rendered
        # into permission.external_directory by oc_l1_render.py (validated
        # there); absent by default - the host overlay stays authoritative
        # for the live lanes.
        "external_directory": lane.get("external_directory"),
    }


def build_parser():
    ap = argparse.ArgumentParser(
        prog="oc_l1.py",
        description=(
            "OpenCode L1 launch profile (O1-LITE): one project, one "
            "localhost-only opencode serve, one session."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{render,start,status}")
    for cmd, help_ in (
        ("render", "write the scratch opencode config for one lane"),
        ("start", "start serve, create the session, post the first prompt"),
        ("status", "print live | silent | dead for the lane's session"),
    ):
        p = sub.add_parser(cmd, help=help_)
        p.add_argument("--name", required=True, help="lane name (top-level config key)")
        p.add_argument(
            "--config",
            default=None,
            help="host-local config path (default: %s)" % default_config_path(),
        )
        if cmd == "render":
            p.add_argument(
                "--repo-config",
                default=None,
                help="repo opencode.jsonc to copy mcp entries from "
                "(default: %s)" % (REPO_ROOT / "opencode.jsonc"),
            )
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd in ("start", "status"):
        import oc_l1_serve  # lazy: step A2 pipeline (next to this file)

        try:
            lanes = load_config(
                Path(args.config) if args.config else default_config_path()
            )
        except LaneError as e:
            print("oc_l1: error: %s" % e, file=sys.stderr)
            return 2
        if args.name not in lanes:
            available = ", ".join(sorted(lanes)) or "(none)"
            print(
                "oc_l1: error: no lane named '%s' (available: %s)"
                % (args.name, available),
                file=sys.stderr,
            )
            return 2
        try:
            lane = validate_lane(lanes[args.name], args.name)
        except LaneError as e:
            print("oc_l1: error: %s" % e, file=sys.stderr)
            return 2
        if args.cmd == "start":
            return oc_l1_serve.cmd_start(lane, args)
        return oc_l1_serve.cmd_status(lane, args)
    cfg_path = Path(args.config) if args.config else default_config_path()
    try:
        lanes = load_config(cfg_path)
        if args.name not in lanes:
            available = ", ".join(sorted(lanes)) or "(none)"
            raise LaneError(
                "no lane named '%s' in %s (available: %s)" % (args.name, cfg_path, available)
            )
        lane = validate_lane(lanes[args.name], args.name)
        repo_cfg = Path(args.repo_config) if args.repo_config else REPO_ROOT / "opencode.jsonc"
        out = render(lane, repo_cfg)
    except LaneError as e:
        print("oc_l1: error: %s" % e, file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
