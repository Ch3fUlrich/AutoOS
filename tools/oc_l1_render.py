#!/usr/bin/env python3
"""oc_l1_render.py - scratch opencode config renderer for the O1-LITE lane.

Writes a SCRATCH opencode config (<scratch_dir>/opencode.json) in the shape
that opencode 2.0.12 actually reads (its OWN schema, not the AutoOS
repo-file shape):

  * "model" pins <provider>/<modelID>; "provider" (SINGULAR) declares the
    provider with npm "@ai-sdk/openai-compatible" and a "models" map whose
    KEYS are the gateway model ids (the lane's modelID, plus the fallback
    modelID when set). Each model entry is {name, limit}; the display
    "name" is the lane's key / fallback_key when set, else the part of the
    modelID after the last "/".
  * Provider options hold environment variable NAMES only
    ({env:AUTOOS_OMNIROUTE_URL}/v1, {env:AUTOOS_OMNIROUTE_KEY}); no secret
    value is ever written. The start environment carries the gateway base
    URL WITHOUT /v1, so the rendered baseURL resolves to <base>/v1.
  * "instructions": the configured path list (default: AGENTS.md + handoff).
  * "mcp": a FLAT map (NO "servers" level) of every server of the repo
    opencode.jsonc (which nests them under "servers"), enabled: true only
    for the lane's "mcp" list (default ["autoos-agent"]); an enabled name
    missing from the repo file is a config error.
  * "plugins": the configured plugin DIRECTORIES (key omitted when empty).
  * "permission": bash/edit/read plus autoos-agent_* allowed; an optional
    lane "external_directory" list of path globs is rendered as allow entries
    (lane c 2026-10-05: the outside-folder access moves from a hand-edited
    host overlay into the lane config).
  * "compaction" / "tool_output": lane c (2026-10-05) pins prune-on
    compaction with a small verbatim tail and an orchestrator-sized
    tool-output clamp - opencode's defaults (prune off, 2000 lines / 50 KB
    per tool result) are how an orchestrator's per-turn input grew to 794k
    tokens. An optional lane model.limit {"context","output"} overrides the
    128k default so a 1M leg renders its real window.
  * NO "server" block: hostname and port go on the `opencode serve`
    command line (tools/oc_l1_serve.py), not in the rendered file.
"""

import json
import os
import re
from pathlib import Path

ENV_URL = "AUTOOS_OMNIROUTE_URL"
ENV_KEY = "AUTOOS_OMNIROUTE_KEY"
RENDERED_FILENAME = "opencode.json"
PROVIDER_NPM = "@ai-sdk/openai-compatible"
PROVIDER_NAME = "workstation gateway"
MODEL_LIMIT = {"context": 131072, "output": 16000}
PERMISSIONS = {"bash": "allow", "edit": "allow", "read": "allow",
               "autoos-agent_*": "allow"}
# Lane c (render keys, 2026-10-05): opencode prunes old tool outputs ONLY on
# opt-in (compaction.prune default false) and truncates tool output at 2000
# lines / 51200 bytes by default - the defaults are how an orchestrator
# session's per-turn input grew to 794k tokens (measured 2026-10-05). The
# lane render pins the tight shape instead: prune on, a small verbatim tail,
# and an orchestrator-sized tool-output clamp (the full text still lands in
# opencode's truncation directory; only the model-visible preview shrinks).
COMPACTION = {"auto": True, "prune": True, "tail_turns": 4}
TOOL_OUTPUT = {"max_lines": 400, "max_bytes": 16384}


class LaneError(Exception):
    """Config/validation problem: reported on stderr, exit 2."""


def _strip_jsonc(text):
    """Drop // and /* */ comments (string-aware) plus trailing commas.

    Sufficient for this repo's opencode.jsonc; not a general JSONC parser.
    """
    out = []
    i, n, in_str, esc = 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _display_name(model_id, key):
    """A model entry's "name": the lane's key when set, else the part of
    the modelID after the last "/"."""
    if isinstance(key, str) and key:
        return key
    return model_id.rsplit("/", 1)[-1]


def _repo_servers(repo):
    """The repo opencode.jsonc's mcp servers, whether nested under a
    "servers" key (this repo's shape) or flat."""
    block = repo.get("mcp") if isinstance(repo, dict) else None
    if isinstance(block, dict):
        if isinstance(block.get("servers"), dict):
            return block["servers"]
        return block
    return None


def render(lane, repo_config_path):
    """Write the scratch opencode config; return the output path."""
    if not repo_config_path.is_file():
        raise LaneError("repo opencode config not found: %s" % repo_config_path)
    try:
        repo = json.loads(_strip_jsonc(repo_config_path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as e:
        raise LaneError("cannot parse repo opencode config %s: %s" % (repo_config_path, e))

    servers = _repo_servers(repo)
    if not isinstance(servers, dict) or not servers:
        raise LaneError("repo opencode config %s has no mcp servers" % repo_config_path)

    enabled = set(lane["mcp"])
    missing = sorted(n for n in enabled if n not in servers)
    if missing:
        raise LaneError(
            "lane '%s': unknown mcp server name(s) %s (not in %s)"
            % (lane["name"], ", ".join(missing), repo_config_path)
        )
    mcp_out = {}
    for sname, entry in servers.items():
        e = dict(entry) if isinstance(entry, dict) else {}
        e["enabled"] = sname in enabled
        if sname == "autoos-agent" and e["enabled"]:
            # Without AUTOOS_WORKERS_DIR the MCP tools run `git rev-parse` WITHOUT stdin=DEVNULL: under opencode's
            # stdio transport that git inherits the MCP pipe and the first tool call (ps) hangs (measured: 600 s).
            # A pinned dir skips that git call; default = the clone's own logs/workers.
            env = dict(e.get("environment") or {})
            env.setdefault("AUTOOS_WORKERS_DIR", str(lane.get("workers_dir") or Path(lane["cwd"]) / "logs" / "workers"))
            e["environment"] = env
        mcp_out[sname] = e

    m = lane["model"]
    provider = m.get("provider") or "omniroute"
    mid = m["modelID"]
    # Lane c (2026-10-05): the client limit follows the lane's model, not a
    # hardcoded 128k - a 1M leg (gemini, the t1/t2-orchestrator combos) that
    # renders as 131072 is the "wrongly defined token size" failure: the
    # session compacts long before its model's real window. A lane may pin
    # model.limit {"context","output"} explicitly (the watcher's set_lane_model
    # does this when it swaps gemini <-> deepseek); the default stays the safe
    # 128k the 128k fallback legs need.
    limit = m.get("limit") if isinstance(m.get("limit"), dict) else MODEL_LIMIT
    models = {
        mid: {
            "name": _display_name(mid, m.get("key")),
            "limit": dict(limit),
        }
    }
    if m.get("fallback_modelID"):
        fid = m["fallback_modelID"]
        models[fid] = {
            "name": _display_name(fid, m.get("fallback_key")),
            "limit": dict(limit),
        }

    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "model": "%s/%s" % (provider, mid),
        "provider": {
            provider: {
                "npm": PROVIDER_NPM,
                "name": PROVIDER_NAME,
                "options": {
                    # env var NAMES only: the values exist in the start
                    # environment, never in this file.
                    "baseURL": "{env:%s}/v1" % ENV_URL,
                    "apiKey": "{env:%s}" % ENV_KEY,
                },
                "models": models,
            }
        },
        "instructions": list(lane["instructions"]),
        "mcp": mcp_out,
        "permission": dict(PERMISSIONS),
        "compaction": dict(COMPACTION),
        "tool_output": dict(TOOL_OUTPUT),
    }
    ext = lane.get("external_directory")
    if ext:
        if (not isinstance(ext, list) or not all(isinstance(p, str) and p for p in ext)):
            raise LaneError(
                "lane '%s': 'external_directory' must be a list of non-empty path strings"
                % lane["name"]
            )
        cfg["permission"]["external_directory"] = {p: "allow" for p in ext}
    if lane["plugins"]:
        cfg["plugins"] = list(lane["plugins"])

    out_dir = Path(lane["scratch_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / RENDERED_FILENAME
    tmp = out.with_name(out.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.replace(tmp, out)
    except OSError as e:
        raise LaneError("cannot write %s: %s" % (out, e))
    return out
