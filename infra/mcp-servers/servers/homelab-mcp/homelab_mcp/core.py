"""Decisions and execution. Contains no site facts — everything comes from Config.

This is where the skill's reasoning becomes executable: "the roles are a
capability matrix, not a hierarchy" is `plan()`, and the error decoder is
`diagnose()`.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import Config, ConfigError


@dataclass
class Plan:
    host: str
    role: str | None
    alias: str
    user: str
    reason: str
    warnings: list[str]
    recorded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host, "role": self.role, "alias": self.alias,
            "user": self.user, "why": self.reason, "recorded": self.recorded,
            "warnings": self.warnings,
        }


def plan(cfg: Config, host: str, intent: str) -> Plan:
    """Pick the LEAST-capable role that satisfies the intent.

    Least-capable on purpose: over-privileged access is the failure mode that
    does damage, and on a recorded role it also generates audit noise that
    hides the sessions that mattered.
    """
    h = cfg.host(host)
    warnings: list[str] = []

    if h.status != "ok":
        warnings.append(f"host status is {h.status!r}")
    for e in cfg.excluded(host):
        warnings.append(f"excluded from {e.get('from')!r}: {e.get('reason')}")
    for q in h.quirks:
        spec = cfg.quirks.get(q, {})
        warnings.append(f"quirk {q}: {spec.get('means', '')} — {spec.get('workaround', '')}".strip(" —"))

    if intent not in cfg.intents:
        raise ConfigError(
            f"unknown intent {intent!r}. Configured intents: {', '.join(sorted(cfg.intents)) or '<none>'}"
        )
    need = cfg.intents[intent].get("needs")

    # Enforced, not advisory: a forbidden intent never reaches a shell.
    if intent in (h.forbid.get("intents") or []):
        raise ConfigError(
            f"intent {intent!r} is forbidden on {host!r}: "
            f"{h.forbid.get('reason') or 'declared under hosts[].forbid'}"
        )

    # A host with no role accounts is addressed directly as its login user.
    # It can still declare what that account may do — otherwise an intent would
    # be silently ignored here, which is how the wrong thing gets run on the one
    # host that has no second account to fall back to.
    if not h.roles:
        if not h.login_user:
            raise ConfigError(f"host {host!r} declares no roles and no login_user")
        if h.login_capabilities and need and need not in h.login_capabilities:
            raise ConfigError(
                f"host {host!r} has a single account ({h.login_user}) whose declared capabilities "
                f"{h.login_capabilities} do not include {need!r} (needed by intent {intent!r})"
            )
        if not h.login_capabilities:
            warnings.append(
                f"{host} declares no login_capabilities, so intent {intent!r} was not "
                f"capability-checked — add login_capabilities to enforce it"
            )
        return Plan(host, None, cfg.alias(host, None), h.login_user,
                    f"{host} has no role accounts; logging in as {h.login_user}", warnings)

    candidates = [r for r in (cfg.role(n) for n in h.roles) if not need or need in r.capabilities]
    if not candidates:
        have = sorted({c for n in h.roles for c in cfg.role(n).capabilities})
        raise ConfigError(
            f"no role on {host!r} provides {need!r} (needed by intent {intent!r}). "
            f"Available capabilities there: {', '.join(have) or '<none>'}"
        )

    # Fewest capabilities == least privilege. Stable tie-break by name.
    chosen = sorted(candidates, key=lambda r: (len(r.capabilities), r.name))[0]
    if chosen.is_recorded:
        warnings.append(
            f"role {chosen.name!r} is recorded"
            + (f" (io_log {chosen.recorded.get('io_log')})" if chosen.recorded.get("io_log") else "")
            + (f"; {chosen.use_when}" if chosen.use_when else "")
        )
    return Plan(host, chosen.name, cfg.alias(host, chosen.name), chosen.user,
                f"intent {intent!r} needs {need!r}; {chosen.name} is the least-capable role providing it",
                warnings, recorded=chosen.is_recorded)


def diagnose(cfg: Config, text: str) -> list[dict[str, Any]]:
    """Decode an error message. Substring match, case-insensitive."""
    low = (text or "").lower()
    out = []
    for d in cfg.diagnostics:
        m = str(d.get("match", ""))
        if m and m.lower() in low:
            out.append({"matched": m, "means": d.get("means"), "fix": d.get("fix")})
    for qname, q in cfg.quirks.items():
        sym = str(q.get("symptom", ""))
        if sym and any(part.strip().lower() in low for part in sym.split("/") if part.strip()):
            out.append({"matched": f"quirk:{qname}", "means": q.get("means"), "fix": q.get("workaround")})
    return out


def _pattern_hits(pat: str, low: str) -> bool:
    """Match a deny/forbid pattern against a lower-cased command.

    A bare word ("apt", "reboot") matches only as a whole token — as a plain
    substring "apt" would refuse `grep capture` and "pve" would refuse
    `pvesm status`, which is exactly the kind of false positive that teaches an
    agent to route around the guardrail. Anything with spaces or punctuation
    ("rm -rf /", "dd if=/dev/") stays a substring match: it is specific already,
    and `rm -rf /home` must still trip "rm -rf /".
    """
    p = pat.lower().strip()
    if not p:
        return False
    if re.fullmatch(r"[a-z0-9_]+", p):
        return re.search(rf"(?<![a-z0-9_./-]){re.escape(p)}(?![a-z0-9_-])", low) is not None
    return p in low


def check_safety(cfg: Config, command: str, role: str | None, reason: str,
                 host: str | None = None) -> list[str]:
    """Return refusal messages; empty list means allowed.

    Two scopes on purpose: safety.deny_patterns applies everywhere, a host's
    forbid.patterns applies only there. Both are UX guardrails for a
    well-behaved agent; the security boundary is the target host's sudoers and
    authorized_keys, never this function.
    """
    safety = cfg.safety or {}
    problems = []
    if not safety.get("allow_run", True):
        problems.append("safety.allow_run is false in the config — homelab_run is disabled")
    low = command.lower()
    for pat in safety.get("deny_patterns") or []:
        if _pattern_hits(str(pat), low):
            problems.append(f"command matches deny_pattern {pat!r}")
    if host:
        fb = cfg.host(host).forbid
        for pat in fb.get("patterns") or []:
            if _pattern_hits(str(pat), low):
                problems.append(
                    f"command matches {host!r}'s forbid pattern {pat!r}: "
                    f"{fb.get('reason') or 'declared under hosts[].forbid'}"
                )
    if role and role in (safety.get("require_reason_for") or []) and not reason.strip():
        problems.append(f"role {role!r} requires a non-empty `reason` (it is privileged/recorded)")
    return problems


def wrap_for_shell(cfg: Config, host: str, command: str) -> str:
    """Non-POSIX login shells cannot parse bash syntax; wrap via sh -c."""
    h = cfg.host(host)
    if h.shell and h.shell not in ("bash", "sh", "zsh", "dash"):
        return f"sh -c {shlex.quote(command)}"
    return command


def ssh_argv(cfg: Config, p: Plan, command: str) -> list[str]:
    ssh = cfg.ssh or {}
    argv = ["ssh"]
    if ssh.get("batch_mode", True):
        argv += ["-o", "BatchMode=yes"]
    argv += ["-o", f"ConnectTimeout={int(ssh.get('connect_timeout', 8))}"]
    identity = ssh.get("identity")
    if identity:
        argv += ["-i", os.path.expanduser(str(identity))]
    argv.append(p.alias)
    if command:
        argv.append(command)
    return argv


def run(cfg: Config, host: str, intent: str, command: str, reason: str = "",
        timeout: int = 60, dry_run: bool = False) -> dict[str, Any]:
    p = plan(cfg, host, intent)
    problems = check_safety(cfg, command, p.role, reason, host=host)
    if problems:
        return {"refused": True, "problems": problems, "plan": p.as_dict()}

    wrapped = wrap_for_shell(cfg, host, command)
    argv = ssh_argv(cfg, p, wrapped)
    if dry_run:
        return {"dry_run": True, "plan": p.as_dict(), "argv": argv}

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"plan": p.as_dict(), "timeout": timeout, "error": f"timed out after {timeout}s"}
    result = {
        "plan": p.as_dict(), "exit_code": r.returncode,
        "stdout": r.stdout[-8000:], "stderr": r.stderr[-4000:],
    }
    if r.returncode != 0:
        hints = diagnose(cfg, r.stderr)
        if hints:
            result["diagnosis"] = hints
    return result
