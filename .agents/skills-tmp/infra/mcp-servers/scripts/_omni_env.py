"""Detect the live Omnigraph stack's docker wiring, so scripts work on any host.

The helper scripts were written against the **local** stack (compose project
`mcp-server`, network `mcp-server_mcp-net`, MinIO named volume). **Central**
(`coding.example.internal`) runs compose project `mcp-servers` → network `mcp-servers_default`,
with MinIO on a **bind mount** at `$APPS_ROOT/omnigraph/minio`. Hard-coding either
one breaks the other, and the failure is quiet: a wrong `--network` makes the CLI
container unable to resolve `omnigraph-server`, and a `docker volume rm` aimed at a
bind mount is a silent no-op that leaves the store intact.

So: ask docker what is actually there, and fall back to the local defaults when the
containers aren't on this host. Explicit flags/env always win over detection.

Nothing here mutates anything — `docker inspect` only.
"""

import os
import subprocess

LOCAL_NET = "mcp-server_mcp-net"        # agent-skills docker-compose.server.yml
LOCAL_MINIO_VOLUME = "mcp-server_omnigraph_minio"

DEFAULT_SITE_ENV = "~/.config/autoos/site.env"


def site_env(name: str, default: str | None = None, path: str | None = None) -> str | None:
    """A site-specific value, resolved first-hit-wins: environment, then `path`
    (default `~/.config/autoos/site.env`), then `default`.

    The file is KEY=VALUE lines (blank/# lines ignored, whitespace trimmed, one pair of
    matching surrounding quotes stripped). A missing file is not an error. Values are
    returned exactly as written — expansion is the caller's business.
    """
    if name in os.environ:
        return os.environ[name]
    if path is None:
        path = DEFAULT_SITE_ENV
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                return value
    except OSError:
        pass
    return default


def _inspect(container: str, fmt: str) -> str | None:
    """`docker inspect` one container, or None if docker/the container is absent."""
    try:
        r = subprocess.run(["docker", "inspect", container, "--format", fmt],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def detect_network(default: str = LOCAL_NET, container: str = "omnigraph-server") -> str:
    """The docker network the live omnigraph-server is attached to.

    Returns `default` when the container isn't on this host (e.g. authoring on a
    laptop while the stack runs on coding.example.internal).
    """
    out = _inspect(container, "{{range $n,$_ := .NetworkSettings.Networks}}{{$n}} {{end}}")
    nets = out.split() if out else []
    return nets[0] if nets else default


def detect_minio_store(container: str = "omnigraph-minio", dest: str = "/data"):
    """How MinIO's data dir is backed: ('bind', <host path>) or ('volume', <name>).

    Returns (None, None) when the container isn't on this host — callers should then
    keep their own default. Clearing a bind mount needs an `rm -rf` in a container;
    clearing a named volume needs `docker volume rm`. Using the wrong one silently
    does nothing, so the *type* matters as much as the value.
    """
    out = _inspect(container, "{{range .Mounts}}{{.Type}}|{{.Name}}|{{.Source}}|{{.Destination}}\n{{end}}")
    if not out:
        return (None, None)
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 4 or parts[3].strip() != dest:
            continue
        mtype, name, source = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if mtype == "bind":
            return ("bind", source)
        return ("volume", name or source)
    return (None, None)


def describe(network: str, minio_kind: str | None, minio_val: str | None) -> str:
    """One-line provenance for logs — say what was detected vs assumed."""
    store = f"{minio_kind}={minio_val}" if minio_kind else "minio=<not detected>"
    return f"network={network} {store}"


if __name__ == "__main__":  # quick probe: python scripts/_omni_env.py
    net = detect_network()
    kind, val = detect_minio_store()
    print(describe(net, kind, val))
