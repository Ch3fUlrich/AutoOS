#!/usr/bin/env python3
"""Derive the opencode config for the docker AI stack from the host's.

The host's ~/.config/opencode/opencode.json (projected from the repo's
opencode.jsonc by setup_opencode_config) is the one source of truth; the
container gets a rewritten copy, never a hand-kept second config:

  * the gateway URL (127.0.0.1/localhost:20128) becomes http://omniroute:20128,
    the compose service name;
  * any other provider on a loopback URL (litellm :4000, ollama :11434) is
    dropped: it binds 127.0.0.1 on the host, which no container can reach;
  * serena runs as the shared SSE service (serena-mcp container, attached to
    the autoos-ai network by ai-stack.sh up) instead of a per-process uvx;
  * loopback URLs in MCP environments (omnigraph :8080) point at the host
    gateway alias, where omnigraph-server is published;
  * playwright is disabled: it needs a browser the image does not ship;
  * instructions outside the mounted code tree are dropped (not visible).

Keys stay {env:...} references - the values come from opencode.env. Writes
only when the content changes; prints what it changed, never a value.

    render-opencode-container-config.py --source ~/.config/opencode/opencode.json \
        --out <data>/opencode-home/.config/opencode/opencode.json --code-dir <code>
"""
import argparse
import json
import os
import sys
from urllib.parse import urlsplit, urlunsplit

LOOPBACK = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def is_loopback(url):
    try:
        return urlsplit(url).hostname in LOOPBACK
    except ValueError:
        return False


def with_host(url, host, port=None):
    parts = urlsplit(url)
    # Guard against malformed ports that cause ValueError
    try:
        orig_port = parts.port
    except ValueError:
        # If the URL has an invalid port, leave it unchanged
        return url
    netloc = host + (":%d" % (port if port is not None else orig_port) if (port or orig_port) else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def base_url_of(provider):
    for block in ("options", "settings"):
        url = (provider.get(block) or {}).get("baseURL")
        if isinstance(url, str):
            return block, url
    return None, None


def rewrite_providers(block, gateway, notes, label):
    if not isinstance(block, dict):
        return block
    out = {}
    for name, prov in block.items():
        where, url = base_url_of(prov) if isinstance(prov, dict) else (None, None)
        if url and is_loopback(url):
            # Guard against malformed ports that raise ValueError
            try:
                port = urlsplit(url).port
            except ValueError:
                notes.append("ignored malformed URL for %s provider %s: %s" % (label, name, url))
                out[name] = prov
                continue
            if port == 20128:
                gw = urlsplit(gateway)
                prov = json.loads(json.dumps(prov))
                prov[where]["baseURL"] = urlunsplit((gw.scheme, gw.netloc, urlsplit(url).path, "", ""))
            else:
                notes.append("dropped %s provider %s: %s is loopback-only on the host" % (label, name, url))
                continue
        out[name] = prov
    return out


def rewrite_mcp(block, serena_url, host_alias, notes):
    if not isinstance(block, dict):
        return block
    out = {}
    for name, srv in block.items():
        srv = json.loads(json.dumps(srv))
        flag = "disabled" if "disabled" in srv else "enabled"
        on = (not srv.get("disabled", False)) if flag == "disabled" else srv.get("enabled", True)
        if name == "serena" and serena_url:
            srv = {"type": "remote", "url": serena_url, flag: on if flag == "enabled" else not on}
            notes.append("serena -> shared SSE service %s" % serena_url)
        elif name == "playwright":
            srv[flag] = False if flag == "enabled" else True
            notes.append("playwright disabled: no browser in the container")
        if srv.get("type") == "remote" and isinstance(srv.get("url"), str) and is_loopback(srv["url"]):
            srv["url"] = with_host(srv["url"], host_alias)
        env = srv.get("environment")
        if isinstance(env, dict):
            for k, v in env.items():
                if isinstance(v, str) and v.startswith("http") and is_loopback(v):
                    env[k] = with_host(v, host_alias)
                    notes.append("%s: %s -> %s" % (name, k, env[k]))
        out[name] = srv
    return out


# Fleet pins (2026-09-30, routing-00): settings the fleet depends on that must
# survive every `ai-stack.sh init` render regardless of the host source file.
# Add-only semantics: missing keys are set, explicit host choices are kept.
GH_READONLY = {
    "gh repo sync *": "allow",
    "gh workflow run *": "allow",
    "gh workflow view *": "allow",
    "gh run list *": "allow",
    "gh run view *": "allow",
    "gh run watch *": "allow",
    "gh pr list *": "allow",
    "gh pr view *": "allow",
    "gh pr status *": "allow",
    "gh api *": "allow",
    "gh release view *": "allow",
    "gh *": "deny",
}


# Fleet model policy (operator 2026-09-30): orchestration runs on the paid
# DeepSeek combo at max effort (opencode-zen and the spark gateway leg were the
# rate-limited / 502 heads), and leaves spawn on the Vertex AI provider in
# OmniRoute (fast, not rate-limited). These are pins because the host source
# (~/.config/opencode/opencode.json) is projected from an older opencode.jsonc
# and would otherwise revert the live fixes on the next `ai-stack.sh init`.
# D657-D2 (AO-DENYLEGS D2, operator D-657) supersedes the orchestrator choice:
# `deepseek/deepseek-flash` answers in neither probe-d657 TSV, so the
# `deepseek-v4.1-flash` route renders no combo and apply.sh prunes it from the
# gateway store — an agent pinned to it would start against a combo the gateway
# no longer serves. It moves to `l1-orchestrator`, the D-657 L1 chain (1M, head
# `bzl/deepseek/deepseek-v4-flash-0731free:free` ack ok + tool ok, priced
# `vertex/gemini-3.8-flash` tail), which is also the route `tools/autoos-agent.py`
# TIERS[1] spawns. The variant/context pinning below still applies to any host
# that declares the deepseek row.
FLEET_AGENT_MODELS = {
    "orchestrator": "omniroute/l1-orchestrator",
    "suborchestrator": "omniroute/l1-orchestrator",
    # Operator D-255 (2026-10-01): NEVER a Gemini Pro model; only Gemini
    # 3.6/3.7/3.8 Flash. leaf-implementer is re-pinned from the old
    # vertex-gemini-3.1-pro-preview to the Vertex 3.8 Flash leg. The 2026-09-30
    # probe still stands for the other legs: `vertex/claude-sonnet-4-5` is 501
    # ("not implemented, or supported, or enabled" -- the Vertex project has no
    # Claude entitlement) and `vertex/DeepSeek-V4-Flash` is 400 (gateway payload
    # bug). Pointing the reviewer at a dead leg would fail every leaf review, so
    # it defaults to a different family that is up now; free-first (NVIDIA nemotron).
    "leaf-implementer": "omniroute/vertex-gemini-3.8-flash",
    "leaf-reviewer": "omniroute/nemotron-3-ultra-free",
}
# Reviewer fallback (paid, funded, 1M, always up): omniroute/deepseek-v4.1-flash.
# If the operator enables Claude in the Vertex Model Garden, the reviewer can
# move back to omniroute/vertex-claude-sonnet-4-5.
DEEPSEEK_VARIANTS = ("low", "high", "max")
# id -> (gateway modelID, context window). The gateway (7647-model catalogue
# 2026-09-30) serves a `vertex/` provider; these are the fleet's picks.
# D-255 (2026-10-01): the off-list Gemini rows (3.1-pro-preview, 2.5-flash) are
# dropped; 3.8-flash is added with the registry's advertised window
# (catalog/ai-registry.json models.gemini-3.8-flash context_advertised 1048576).
FLEET_VERTEX_MODELS = {
    "vertex-gemini-3.8-flash": ("vertex/gemini-3.8-flash", 1048576),
    "vertex-claude-sonnet-4-5": ("vertex/claude-sonnet-4-5", 200000),
    "vertex-deepseek-v4-flash": ("vertex/DeepSeek-V4-Flash", 1000000),
}
# Non-Vertex fleet aliases the agents reference (same shape, same pinning).
FLEET_EXTRA_MODELS = {
    "nemotron-3-ultra-free": ("nvidia/nemotron-3-ultra-550b-a55b:free", 1048576),
}
FLEET_ALIAS_MODELS = dict(FLEET_VERTEX_MODELS, **FLEET_EXTRA_MODELS)


def pin_fleet_overrides(cfg, notes):
    """Force fleet-critical settings; record each change in notes."""
    agents = cfg.get("agent")
    if isinstance(agents, dict):
        sub = agents.get("suborchestrator")
        # Dev/testing: orchestrator sessions must stay promptable in Home.
        # Flip back to "subagent" (here AND live) when testing ends (D-163).
        if isinstance(sub, dict) and sub.get("mode") != "primary":
            sub["mode"] = "primary"
            notes.append("suborchestrator.mode pinned primary (fleet dev, D-163)")
        # Model pins are add-if-present: a source without the fleet agents is
        # left alone, so the renderer stays usable for the generic AutoOS config.
        for name, model in FLEET_AGENT_MODELS.items():
            a = agents.get(name)
            if isinstance(a, dict) and a.get("model") != model:
                a["model"] = model
                notes.append("agent.%s model pinned %s" % (name, model))
    scopes = []
    if isinstance(cfg.get("permission"), dict):
        scopes.append(("top-level", cfg["permission"]))
    if isinstance(agents, dict):
        for name, a in agents.items():
            if isinstance(a, dict) and isinstance(a.get("permission"), dict):
                scopes.append(("agent:" + name, a["permission"]))
    for label, perm in scopes:
        bash = perm.get("bash")
        if isinstance(bash, dict):
            added = [k for k in GH_READONLY if k not in bash]
            for k in added:
                bash[k] = GH_READONLY[k]
            if added:
                notes.append("%s: pinned %d gh rules" % (label, len(added)))
    for provkey in ("provider", "providers"):
        block = cfg.get(provkey)
        if not isinstance(block, dict):
            continue
        # The block maps provider-name -> {models: {...}} (live shape). Tolerate
        # a flat {models: {...}} too. The earlier version read `<block>.models`
        # directly, matched nothing, and silently applied no model pins.
        entries = []
        if isinstance(block.get("models"), dict):
            entries.append((provkey, block["models"]))
        for pname, prov in block.items():
            if isinstance(prov, dict) and isinstance(prov.get("models"), dict):
                entries.append(("%s.%s" % (provkey, pname), prov["models"]))
        for label, models in entries:
            m = models.get("deepseek-v4.1-flash")
            # Operator 2026-09-30: the model serves ~1M, not 128k.
            if isinstance(m, dict) and isinstance(m.get("limit"), dict):
                if m["limit"].get("context") != 1048576:
                    m["limit"]["context"] = 1048576
                    notes.append("%s.models.deepseek-v4.1-flash context pinned 1M" % label)
            # Operator 2026-09-30: the combo offered no effort variants, so #max
            # could not be selected. Restore the registry ladder (none is implicit).
            if isinstance(m, dict):
                have = [v.get("id") for v in m.get("variants") or []
                        if isinstance(v, dict)]
                if not all(v in have for v in DEEPSEEK_VARIANTS):
                    m["variants"] = [
                        {"id": v, "settings": {"reasoningEffort": v}}
                        for v in DEEPSEEK_VARIANTS
                    ]
                    notes.append("%s.models.deepseek-v4.1-flash variants pinned %s"
                                 % (label, "/".join(DEEPSEEK_VARIANTS)))
            # Operator 2026-09-30: expose the Vertex AI provider so leaves can
            # spawn on a fast, non-rate-limited leg.
            for mid, (model_id, ctx) in FLEET_ALIAS_MODELS.items():
                if mid in models:
                    continue
                models[mid] = {
                    "modelID": model_id,
                    "name": model_id,
                    "limit": {"context": ctx, "output": 32768},
                }
                notes.append("%s.models added %s -> %s" % (label, mid, model_id))


def render(src, code_dir, gateway, serena_url, host_alias):
    notes = []
    cfg = json.loads(json.dumps(src))
    if "provider" in cfg:
        cfg["provider"] = rewrite_providers(cfg["provider"], gateway, notes, "V1")
    if "providers" in cfg:
        cfg["providers"] = rewrite_providers(cfg["providers"], gateway, notes, "V2")
    if "mcp" in cfg:
        cfg["mcp"] = rewrite_mcp(cfg["mcp"], serena_url, host_alias, notes)
    pin_fleet_overrides(cfg, notes)
    if isinstance(cfg.get("instructions"), list):
        root = os.path.realpath(code_dir).rstrip("/") + "/"
        keep = []
        for item in cfg["instructions"]:
            if isinstance(item, str) and item.startswith("/") and not item.startswith(root):
                notes.append("dropped instruction outside the code tree: %s" % item)
                continue
            keep.append(item)
        cfg["instructions"] = keep
    return cfg, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--gateway-url", default="http://omniroute:20128")
    ap.add_argument("--serena-url", default="http://serena-mcp:9121/sse")
    ap.add_argument("--host-alias", default="host.docker.internal")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        src = json.load(open(a.source, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("cannot read %s: %s" % (a.source, exc), file=sys.stderr)
        return 2
    cfg, notes = render(src, a.code_dir, a.gateway_url, a.serena_url, a.host_alias)
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    for n in notes:
        print("  - " + n)
    try:
        current = open(a.out, encoding="utf-8").read()
    except OSError:
        current = None
    if current == text:
        print("  = %s unchanged (skipped)" % a.out)
        return 0
    if a.dry_run:
        print("  - would write %s" % a.out)
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, a.out)
    print("  + wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
