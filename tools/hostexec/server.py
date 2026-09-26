"""hostexec MCP server: streamable HTTP over the policy/audit/runner core
(spec status/L1-backlog.spec-host-shell.md section 4 item 4).

Bearer token -> sha256 -> actor (constant-time compare against every
actor.token_sha256 declared in the policy); tokens are never accepted on
argv (review F7) and the policy never stores a raw token, only its sha256.

`mcp`/`starlette`/`uvicorn` are imported LAZILY, inside build_asgi_app() and
serve(), so policy.py/audit.py/runner.py and every hermetic test in this
package run without any of them installed (brief item 4).

Tools:
    host_run(host, argv, cwd=None, reason=None, session=None)
    host_policy()
    host_log_tail(n=20)           -- the caller's own actor only (review F11)

A denied/refused call raises a tool error whose message is the JSON object
    {"refused": true, "rule": "...", "problems": [...],
     "hint": "host_policy() lists what is refused"}
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import ipaddress
import json
import os
import sys
from typing import Callable, Sequence

from hostexec import audit, policy, runner

DEFAULT_PORT = 8765  # not 9121/24282/3000/4096/8080/20128 -- already used by the stack
_LOOPBACK_NAMES = {"localhost"}

# Set by the auth middleware in build_asgi_app() from the verified Bearer
# token, read by each tool function. contextvars propagate through the
# anyio/asyncio task(s) FastMCP spawns to handle one request, since each
# is created from within this call's own async context.
_current_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hostexec_actor", default=None)


class Refused(Exception):
    """A denied or otherwise refused call. The plain host_run()/host_log_tail()
    functions raise this directly (tests catch it); the MCP-facing tool
    wrappers turn it into a tool error whose text is to_dict()'s JSON."""

    def __init__(self, rule: str | None, problems: Sequence[str]):
        super().__init__(rule or "refused")
        self.rule = rule
        self.problems = list(problems)

    def to_dict(self) -> dict:
        return {"refused": True, "rule": self.rule, "problems": self.problems,
                "hint": "host_policy() lists what is refused"}


def verify_token(pol: policy.Policy, token: str | None) -> str | None:
    """Bearer token -> sha256 -> actor name, constant-time. None if the
    token is missing or matches no actor in the policy."""
    if not token:
        return None
    token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
    matched = None
    for known_sha, actor in pol.actors.items():
        # constant-time over every entry: don't short-circuit on the first
        # match, so a timing side channel can't reveal which token_sha256
        # (of possibly many) the caller is close to guessing.
        if hmac.compare_digest(known_sha, token_sha256):
            matched = actor.name
    return matched


# ─── plain tool implementations (no mcp import; directly unit-testable) ────

def host_run(pol: policy.Policy, log: audit.AuditLog, *, actor: str, host: str,
             argv: list[str], cwd: str | None = None, reason: str | None = None,
             session: str | None = None, via: str = "http",
             run_fn: Callable | None = None) -> dict:
    """decide() -> fail-closed audit preflight -> (deny: log+raise) or
    (allow: run, then log the outcome). Raises Refused on denial or on an
    unwritable audit log (spec section 5: FAIL CLOSED, nothing runs)."""
    cwd = cwd or "/"
    try:
        log.preflight()
    except audit.AuditWriteError as exc:
        raise Refused(None, [f"audit log unavailable, refusing (fail closed): {exc}"]) from exc

    decision = policy.decide(pol, actor, host, argv, cwd)
    if not decision.allow:
        log.write(actor=actor, session=session, via=via, host=host, argv=argv, cwd=cwd,
                   run_as=actor, decision="deny", rule=decision.rule, reason=reason,
                   exit_code=None, duration_ms=None, out_bytes=None, truncated=False)
        raise Refused(decision.rule, list(decision.problems))

    host_entry = pol.hosts[host]  # decide() already proved this exists and is not forbidden
    run = run_fn or _default_run
    result = run(pol, host_entry, argv, cwd)

    log.write(actor=actor, session=session, via=via, host=host, argv=argv, cwd=cwd,
              run_as=actor, decision="allow", rule=None, reason=reason,
              exit_code=result.exit_code, duration_ms=result.duration_ms,
              out_bytes=result.out_bytes, truncated=result.truncated)
    return {"exit": result.exit_code, "duration_ms": result.duration_ms,
            "out_bytes": result.out_bytes, "truncated": result.truncated,
            "output": result.output, "timed_out": result.timed_out}


def _default_run(pol: policy.Policy, host_entry: policy.HostEntry, argv: list[str], cwd: str):
    if host_entry.kind == "ssh":
        return runner.run_remote(argv, alias=host_entry.target or host_entry.alias,
                                  cwd=cwd, path_dirs=pol.path)
    return runner.run_local(argv, cwd=cwd, path_dirs=pol.path)


_RULE_IDS = ("unknown-actor", "empty-argv", "argv-caps", "forbid-host", "env-injection",
             "path-hijack", "use-host-alias", "docker-root", "no-sudo", "no-inline-shell",
             "destructive", "git-option-injection")


def host_policy(pol: policy.Policy) -> dict:
    return {
        "hosts": {alias: {"kind": h.kind, "forbid": h.forbid} for alias, h in pol.hosts.items()},
        "rules": list(_RULE_IDS),
        "max_args": pol.max_args,
        "max_arg_length": pol.max_arg_length,
        "note": ("hostexec is a broad DENY-LIST + audit boundary, not containment -- "
                 "see configuration/hostexec/README.md"),
    }


def host_log_tail(state_dir: str, *, actor: str, n: int = 20) -> dict:
    """The CALLER's own actor only (review F11): host_log_tail must never
    hand one actor another actor's audit lines, and must not become a
    free oracle for probing the whole allow/deny surface."""
    lines = list(audit.tail(state_dir, actor=actor))
    return {"lines": lines[-n:] if n > 0 else []}


# ─── bind address policy (spec section 4 item 4) ───────────────────────────

def parse_bind(env_value: str | None) -> list[str]:
    raw = env_value or "127.0.0.1"
    return [b.strip() for b in raw.split(",") if b.strip()]


def is_loopback(addr: str) -> bool:
    if addr in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def check_bind_allowed(addresses: Sequence[str], *, allow_lan: bool) -> None:
    """Refuse 0.0.0.0/:: (or any non-loopback address) unless
    AUTOOS_EXEC_ALLOW_LAN=1 (spec section 4 item 4)."""
    if allow_lan:
        return
    bad = [a for a in addresses if not is_loopback(a)]
    if bad:
        raise ValueError(f"refusing to bind non-loopback address(es) {bad!r} "
                          "without AUTOOS_EXEC_ALLOW_LAN=1")


# ─── the MCP layer (imported lazily) ───────────────────────────────────────

def build_asgi_app(pol: policy.Policy, *, audit_log: audit.AuditLog):
    """Build the auth-wrapped ASGI app. Imports `mcp`/`starlette` here, not
    at module scope, so every other function in this module (and every
    test that only needs those) works without either installed."""
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import JSONResponse

    app = FastMCP("hostexec")

    @app.tool(name="host_run")
    def _host_run(host: str, argv: list[str], cwd: str | None = None,
                  reason: str | None = None, session: str | None = None) -> dict:
        """Run one command on `host` if the policy allows it. argv is an
        array, never a shell string. hostexec is a deny-list + audit
        boundary, not containment (host_policy() explains). A denied call
        raises a tool error: {refused, rule, problems, hint}."""
        actor = _current_actor.get()
        if actor is None:  # pragma: no cover -- the auth middleware should have 401'd first
            raise ValueError(json.dumps(Refused("unknown-actor", ["no authenticated actor"]).to_dict()))
        try:
            return host_run(pol, audit_log, actor=actor, host=host, argv=argv,
                             cwd=cwd, reason=reason, session=session)
        except Refused as exc:
            raise ValueError(json.dumps(exc.to_dict())) from None

    @app.tool(name="host_policy")
    def _host_policy() -> dict:
        """What hostexec refuses today: hosts, rule ids, and the argv caps."""
        return host_policy(pol)

    @app.tool(name="host_log_tail")
    def _host_log_tail(n: int = 20) -> dict:
        """Your own actor's most recent audit lines -- never another actor's."""
        actor = _current_actor.get()
        if actor is None:  # pragma: no cover
            raise ValueError(json.dumps(Refused("unknown-actor", ["no authenticated actor"]).to_dict()))
        return host_log_tail(str(audit_log.state_dir), actor=actor, n=n)

    inner = app.streamable_http_app()

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw_auth = headers.get(b"authorization", b"").decode("latin-1", "replace")
        supplied = raw_auth[len("Bearer "):] if raw_auth.lower().startswith("bearer ") else None
        actor = verify_token(pol, supplied)
        if actor is None:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            await resp(scope, receive, send)
            return
        reset = _current_actor.set(actor)
        try:
            await inner(scope, receive, send)
        finally:
            _current_actor.reset(reset)

    return wrapped


def serve(pol: policy.Policy, *, policy_path: str, state_dir: str | None = None) -> int:
    """Blocking: start the broker and serve until interrupted. Reads
    AUTOOS_EXEC_BIND (comma list, default 127.0.0.1) and AUTOOS_EXEC_PORT
    (default DEFAULT_PORT); refuses a non-loopback bind unless
    AUTOOS_EXEC_ALLOW_LAN=1 (spec section 4 item 4)."""
    import asyncio
    import uvicorn

    addresses = parse_bind(os.environ.get("AUTOOS_EXEC_BIND"))
    port = int(os.environ.get("AUTOOS_EXEC_PORT", str(DEFAULT_PORT)))
    allow_lan = os.environ.get("AUTOOS_EXEC_ALLOW_LAN") == "1"
    try:
        check_bind_allowed(addresses, allow_lan=allow_lan)
    except ValueError as exc:
        print(f"refused to start: {exc}", file=sys.stderr)
        return 2

    state_dir = state_dir or audit.default_state_dir()
    with open(policy_path, "rb") as fh:
        policy_sha256 = hashlib.sha256(fh.read()).hexdigest()
    audit_log = audit.AuditLog(state_dir, policy_sha256=policy_sha256)
    try:
        audit_log.preflight()
    except audit.AuditWriteError as exc:
        print(f"refused to start: audit log unavailable: {exc}", file=sys.stderr)
        return 2

    app = build_asgi_app(pol, audit_log=audit_log)
    servers = [uvicorn.Server(uvicorn.Config(app, host=addr, port=port, log_level="warning"))
               for addr in addresses]
    try:
        asyncio.run(_serve_all(servers))
    except KeyboardInterrupt:
        pass
    return 0


async def _serve_all(servers) -> None:
    import asyncio
    await asyncio.gather(*(s.serve() for s in servers))
