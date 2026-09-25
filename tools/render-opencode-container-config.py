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


def render(src, code_dir, gateway, serena_url, host_alias):
    notes = []
    cfg = json.loads(json.dumps(src))
    if "provider" in cfg:
        cfg["provider"] = rewrite_providers(cfg["provider"], gateway, notes, "V1")
    if "providers" in cfg:
        cfg["providers"] = rewrite_providers(cfg["providers"], gateway, notes, "V2")
    if "mcp" in cfg:
        cfg["mcp"] = rewrite_mcp(cfg["mcp"], serena_url, host_alias, notes)
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
