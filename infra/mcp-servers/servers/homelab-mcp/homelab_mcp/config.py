"""Load and validate homelab.yaml.

Every site-specific fact — hosts, users, capabilities, quirks — comes from here.
The rest of the server contains no hostnames, usernames, products or paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATHS = [
    "~/.config/homelab/homelab.yaml",
    "~/homelab.yaml",
]


class ConfigError(Exception):
    """Raised with an actionable message — never a bare KeyError."""


@dataclass
class Role:
    name: str
    user: str
    role_alias: str
    capabilities: list[str] = field(default_factory=list)
    sudo: dict[str, Any] = field(default_factory=dict)
    recorded: dict[str, Any] | None = None
    notes: str = ""
    use_when: str = ""

    @property
    def is_recorded(self) -> bool:
        return bool(self.recorded)


@dataclass
class Host:
    name: str
    address: str = ""
    alias: str = ""            # explicit ssh alias, overrides ssh.alias_template
    roles: list[str] = field(default_factory=list)
    login_user: str = ""
    login_capabilities: list[str] = field(default_factory=list)
    shell: str = "bash"
    status: str = "ok"
    tags: list[str] = field(default_factory=list)
    quirks: list[str] = field(default_factory=list)
    notes: str = ""
    # Per-host prohibition. Unlike `exclusions` (a warning) this is enforced:
    # plan() refuses the listed intents and run() refuses commands matching the
    # listed patterns, on THIS host only. A global deny_pattern would block the
    # same command everywhere, which is the wrong shape for "never update the
    # host that serves the others" — updates elsewhere are fine.
    forbid: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    path: Path
    ssh: dict[str, Any]
    roles: dict[str, Role]
    hosts: dict[str, Host]
    intents: dict[str, dict[str, Any]]
    quirks: dict[str, dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    unreachable: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    safety: dict[str, Any]

    # ── lookups ──────────────────────────────────────────────────────────────
    def host(self, name: str) -> Host:
        if name not in self.hosts:
            raise ConfigError(
                f"unknown host {name!r}. Known hosts: {', '.join(sorted(self.hosts)) or '<none>'}"
            )
        return self.hosts[name]

    def role(self, name: str) -> Role:
        if name not in self.roles:
            raise ConfigError(
                f"unknown role {name!r}. Known roles: {', '.join(sorted(self.roles)) or '<none>'}"
            )
        return self.roles[name]

    def roles_with(self, capability: str) -> list[Role]:
        return [r for r in self.roles.values() if capability in r.capabilities]

    def alias(self, host: str, role: str | None) -> str:
        """Resolve the SSH target for a host+role.

        Precedence: an explicit `alias:` on the host wins; then the role template;
        then, for a host with no role accounts, `login_user@address` if we have
        both, else the bare name. Not every host follows the template — some have
        a differently-named alias, and some have no ssh_config entry at all.
        """
        h = self.host(host)
        if h.alias:
            return h.alias
        if role is None or not h.roles:
            if h.login_user and h.address:
                return f"{h.login_user}@{h.address}"
            return h.name
        r = self.role(role)
        tmpl = self.ssh.get("alias_template", "{host}-{role_alias}")
        return tmpl.format(host=h.name, role_alias=r.role_alias, role=r.name, user=r.user)

    def excluded(self, host: str) -> list[dict[str, Any]]:
        return [e for e in self.exclusions if e.get("host") == host]


def _require(d: Any, key: str, where: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise ConfigError(f"{where}: missing required key {key!r}")
    return d[key]


def resolve_path(explicit: str | None = None) -> Path:
    """HOMELAB_CONFIG wins, then the documented default locations."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("HOMELAB_CONFIG"):
        candidates.append(os.environ["HOMELAB_CONFIG"])
    candidates.extend(DEFAULT_PATHS)
    for c in candidates:
        p = Path(os.path.expanduser(c))
        if p.is_file():
            return p
    raise ConfigError(
        "no homelab config found. Set HOMELAB_CONFIG=/path/to/homelab.yaml, or create "
        + " or ".join(DEFAULT_PATHS)
        + ". Start from homelab.example.yaml."
    )


def load(explicit: str | None = None) -> Config:
    path = resolve_path(explicit)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    roles: dict[str, Role] = {}
    for name, spec in (raw.get("roles") or {}).items():
        where = f"{path}: roles.{name}"
        roles[name] = Role(
            name=name,
            user=_require(spec, "user", where),
            role_alias=spec.get("role_alias", name),
            capabilities=list(spec.get("capabilities") or []),
            sudo=spec.get("sudo") or {},
            recorded=spec.get("recorded"),
            notes=spec.get("notes", ""),
            use_when=spec.get("use_when", ""),
        )

    hosts: dict[str, Host] = {}
    for spec in raw.get("hosts") or []:
        where = f"{path}: hosts[]"
        name = _require(spec, "name", where)
        for r in spec.get("roles") or []:
            if r not in roles:
                raise ConfigError(f"{path}: host {name!r} references unknown role {r!r}")
        for q in spec.get("quirks") or []:
            if q not in (raw.get("quirks") or {}):
                raise ConfigError(f"{path}: host {name!r} references unknown quirk {q!r}")
        hosts[name] = Host(
            name=name,
            address=spec.get("address", ""),
            alias=spec.get("alias", ""),
            roles=list(spec.get("roles") or []),
            login_user=spec.get("login_user", ""),
            login_capabilities=list(spec.get("login_capabilities") or []),
            shell=spec.get("shell", "bash"),
            status=spec.get("status", "ok"),
            tags=list(spec.get("tags") or []),
            quirks=list(spec.get("quirks") or []),
            notes=spec.get("notes", ""),
            forbid=dict(spec.get("forbid") or {}),
        )
        fb = hosts[name].forbid
        for i in fb.get("intents") or []:
            if i not in (raw.get("intents") or {}):
                raise ConfigError(f"{path}: host {name!r} forbids unknown intent {i!r}")

    intents = raw.get("intents") or {}
    known_caps = {c for r in roles.values() for c in r.capabilities}
    for iname, spec in intents.items():
        need = (spec or {}).get("needs")
        if need and need not in known_caps:
            raise ConfigError(
                f"{path}: intent {iname!r} needs capability {need!r}, which no role provides. "
                f"Capabilities in use: {', '.join(sorted(known_caps)) or '<none>'}"
            )

    return Config(
        path=path,
        ssh=raw.get("ssh") or {},
        roles=roles,
        hosts=hosts,
        intents=intents,
        quirks=raw.get("quirks") or {},
        diagnostics=list(raw.get("diagnostics") or []),
        unreachable=list(raw.get("unreachable") or []),
        exclusions=list(raw.get("exclusions") or []),
        safety=raw.get("safety") or {},
    )
