"""hostexec policy: a broad DENY-LIST + audit boundary, not a narrow allow-list
(operator decision Q2, 2026-09-26: "ALL: read AND mutating, for all actors").

Loads a TOML policy (stdlib tomllib) declaring actors (token_sha256 -> actor
name + tier), hosts (alias -> local|ssh, forbid) and a fixed PATH. decide()
runs every built-in deny rule against one call (actor, host, argv, cwd) and
returns a Decision. No sudo is ever allowed (operator decision Q3); the
mutating tier is inert today (Q2 decision) but the `tier` field is kept in
the data model so a later narrowing needs no schema change.

This is NOT containment (see configuration/hostexec/README.md): a host CLI
that already holds docker/sudo group membership can bypass hostexec entirely
by running a shell directly (spec F1). hostexec's job is a sanctioned path,
a real audit trail, and denying the worst argv patterns it CAN see.

Rule ids (fixed; every one has rows in tests/fixtures/hostexec-decisions.tsv):
    unknown-actor         token/actor not declared in the policy
    empty-argv             argv is empty or every element is blank
    argv-caps               too many args, or one argument too long
    forbid-host              host not declared, or declared with forbid=true
    env-injection            argv[0] is a NAME=value assignment, or
                             LD_PRELOAD/LD_LIBRARY_PATH/BASH_ENV appear anywhere
    path-hijack              argv[0] is a relative path, or a bare name that
                             does not resolve on the policy's FIXED PATH, or
                             an absolute path under a world-writable directory
    no-sudo                  sudo/su/doas/pkexec/run0, directly or behind
                             env/nice/nohup/timeout/xargs/ionice/stdbuf/setsid
    no-inline-shell          sh/bash/zsh/dash/fish -c, python* -c, perl -e/-E,
                             node -e/-p, ruby -e, awk with "system(" in a
                             program argument -- argv only, scripts by path
                             are allowed
    destructive              rm -r/-f on a root-ish path or glob of one,
                             mkfs*/wipefs, dd of=/dev/*, shred /dev/*,
                             shutdown/reboot/poweroff/halt, init 0|6,
                             systemctl poweroff/reboot/halt, chmod/chown -R /,
                             git push --force/-f/--mirror/--delete or a
                             +refspec, docker system prune, docker volume
                             rm/prune, iptables -F, nft flush, crontab -r
    git-option-injection     git -c/-C/--git-dir/--work-tree/--exec-path/
                             --output/--upload-pack/--receive-pack/
                             --config-env/--exec (review F2: these read
                             .git/config or run arbitrary programs even for
                             "read" verbs like status/log/diff)
"""
from __future__ import annotations

import dataclasses
import os
import re
import stat
import tomllib
from typing import Mapping, Sequence


class PolicyError(ValueError):
    """Malformed or missing policy. The server MUST refuse to start on this
    (review F6) -- never fall back to allow-all."""


@dataclasses.dataclass(frozen=True)
class Actor:
    name: str
    tier: str


@dataclasses.dataclass(frozen=True)
class HostEntry:
    alias: str
    kind: str  # "local" | "ssh"
    target: str | None = None  # ssh alias/hostname; defaults to `alias`
    forbid: bool = False


@dataclasses.dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str | None
    problems: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.problems) if self.problems else (self.rule or "")


@dataclasses.dataclass(frozen=True)
class Policy:
    actors: Mapping[str, Actor]      # token_sha256 -> Actor
    hosts: Mapping[str, HostEntry]   # alias -> HostEntry
    path: tuple[str, ...]            # fixed PATH directories, in order
    max_args: int = 64
    max_arg_length: int = 4096

    def actor_names(self) -> frozenset[str]:
        return frozenset(a.name for a in self.actors.values())

    def actor_by_token_sha256(self, token_sha256: str) -> Actor | None:
        return self.actors.get(token_sha256)


def load(path: str) -> Policy:
    """Load and validate a policy TOML file. Raises PolicyError on anything
    missing or malformed -- the caller must treat that as fatal (fail closed,
    never allow-all)."""
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise PolicyError(f"policy file not found: {path}") from exc
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"malformed policy TOML in {path}: {exc}") from exc
    return _build(raw, source=path)


def loads(text: str, *, source: str = "<string>") -> Policy:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"malformed policy TOML in {source}: {exc}") from exc
    return _build(raw, source=source)


def _build(raw: object, *, source: str) -> Policy:
    if not isinstance(raw, dict):
        raise PolicyError(f"{source}: the policy must be a TOML table")

    actors_raw = raw.get("actors")
    hosts_raw = raw.get("hosts")
    path_raw = raw.get("path")
    limits_raw = raw.get("limits", {})

    if not isinstance(actors_raw, dict) or not actors_raw:
        raise PolicyError(f"{source}: [actors] must declare at least one token_sha256")
    if not isinstance(hosts_raw, dict) or not hosts_raw:
        raise PolicyError(f"{source}: [hosts] must declare at least one host")
    if not isinstance(path_raw, list) or not path_raw or not all(isinstance(p, str) for p in path_raw):
        raise PolicyError(f"{source}: top-level 'path' must be a non-empty list of directory strings")
    if not isinstance(limits_raw, dict):
        raise PolicyError(f"{source}: [limits] must be a table")

    actors: dict[str, Actor] = {}
    for token_sha, entry in actors_raw.items():
        if not isinstance(entry, dict) or "name" not in entry or "tier" not in entry:
            raise PolicyError(f"{source}: actors.{token_sha!r} needs 'name' and 'tier'")
        actors[str(token_sha)] = Actor(name=str(entry["name"]), tier=str(entry["tier"]))

    hosts: dict[str, HostEntry] = {}
    for alias, entry in hosts_raw.items():
        if not isinstance(entry, dict) or "kind" not in entry:
            raise PolicyError(f"{source}: hosts.{alias!r} needs 'kind'")
        kind = str(entry["kind"])
        if kind not in ("local", "ssh"):
            raise PolicyError(f"{source}: hosts.{alias!r}.kind must be 'local' or 'ssh', got {kind!r}")
        target = entry.get("target")
        hosts[str(alias)] = HostEntry(alias=str(alias), kind=kind,
                                       target=str(target) if target is not None else None,
                                       forbid=bool(entry.get("forbid", False)))

    try:
        max_args = int(limits_raw.get("max_args", 64))
        max_arg_length = int(limits_raw.get("max_arg_length", 4096))
    except (TypeError, ValueError) as exc:
        raise PolicyError(f"{source}: [limits] values must be integers") from exc
    if max_args < 1 or max_arg_length < 1:
        raise PolicyError(f"{source}: [limits] values must be positive")

    return Policy(actors=actors, hosts=hosts, path=tuple(str(p) for p in path_raw),
                  max_args=max_args, max_arg_length=max_arg_length)


# ─── decision ────────────────────────────────────────────────────────────

def decide(policy: Policy, actor: str, host: str, argv: Sequence[str], cwd: str) -> Decision:
    """allow/deny one call. Rules are checked in a fixed priority order so
    the reported `rule` is deterministic when more than one would match."""
    if not actor or actor not in policy.actor_names():
        return Decision(False, "unknown-actor", (f"actor not declared in policy: {actor!r}",))

    argv = list(argv)
    if not argv or not any(a.strip() for a in argv if isinstance(a, str)):
        return Decision(False, "empty-argv", ("argv is empty",))

    if len(argv) > policy.max_args:
        return Decision(False, "argv-caps",
                         (f"{len(argv)} arguments exceeds max_args={policy.max_args}",))
    over = [a for a in argv if len(a) > policy.max_arg_length]
    if over:
        return Decision(False, "argv-caps",
                         (f"an argument is longer than max_arg_length={policy.max_arg_length}",))

    host_entry = policy.hosts.get(host)
    if host_entry is None:
        return Decision(False, "forbid-host", (f"host not declared in policy: {host!r}",))
    if host_entry.forbid:
        return Decision(False, "forbid-host", (f"host is forbidden: {host!r}",))

    env_problem = _env_injection_problem(argv)
    if env_problem:
        return Decision(False, "env-injection", (env_problem,))

    hijack_problem = _path_hijack_problem(argv[0], policy)
    if hijack_problem:
        return Decision(False, "path-hijack", (hijack_problem,))

    stripped = _strip_wrappers(argv)
    if stripped and _basename(stripped[0]) in _SUDO_FAMILY:
        return Decision(False, "no-sudo", (f"{_basename(stripped[0])} is never allowed (Q3)",))

    shell_problem = _inline_shell_problem(argv)
    if shell_problem:
        return Decision(False, "no-inline-shell", (shell_problem,))

    destructive_problem = _destructive_problem(argv)
    if destructive_problem:
        return Decision(False, "destructive", (destructive_problem,))

    git_opt_problem = _git_option_injection_problem(argv)
    if git_opt_problem:
        return Decision(False, "git-option-injection", (git_opt_problem,))

    return Decision(True, None, ())


# ─── helpers ─────────────────────────────────────────────────────────────

def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


_WRAPPERS = {"env", "nice", "nohup", "timeout", "xargs", "ionice", "stdbuf", "setsid"}
_SUDO_FAMILY = {"sudo", "su", "doas", "pkexec", "run0"}


def _looks_like_duration(token: str) -> bool:
    body = token[:-1] if token and token[-1] in "smhd" else token
    return bool(body) and body.replace(".", "", 1).isdigit()


def _looks_like_assignment(token: str) -> bool:
    if "=" not in token:
        return False
    name = token.split("=", 1)[0]
    return bool(name) and not name[0].isdigit() and all(c.isalnum() or c == "_" for c in name)


def _strip_wrappers(argv: Sequence[str]) -> list[str]:
    """Peel off leading env/nice/nohup/timeout/xargs/ionice/stdbuf/setsid
    invocations -- and their own flags/values -- to find the real command.
    Best-effort: used for the no-sudo check only, never to grant an allow."""
    rest = list(argv)
    while rest:
        base = _basename(rest[0])
        if base not in _WRAPPERS:
            break
        rest.pop(0)
        while rest and rest[0].startswith("-"):
            flag = rest.pop(0)
            # A short flag (nice -n 10, ionice -c 3, stdbuf -o L) often takes
            # its value as a separate token. Consume it too, UNLESS that
            # would swallow the real command we are looking for.
            if (base != "env" and "=" not in flag and not flag.startswith("--")
                    and rest and not rest[0].startswith("-")
                    and _basename(rest[0]) not in _WRAPPERS
                    and _basename(rest[0]) not in _SUDO_FAMILY):
                rest.pop(0)
        if base == "env":
            while rest and not rest[0].startswith("-") and _looks_like_assignment(rest[0]):
                rest.pop(0)
        elif base == "timeout" and rest and not rest[0].startswith("-") and _looks_like_duration(rest[0]):
            rest.pop(0)
    return rest


_DANGEROUS_ENV_VARS = ("LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV")


def _env_injection_problem(argv: Sequence[str]) -> str | None:
    if argv and _looks_like_assignment(argv[0]):
        return f"argv[0] is an environment assignment, not a command: {argv[0]!r}"
    for tok in argv:
        if "=" in tok:
            name = tok.split("=", 1)[0]
            if name in _DANGEROUS_ENV_VARS:
                return f"dangerous environment assignment: {name}"
    return None


def _is_world_writable_dir(directory: str) -> bool:
    try:
        st = os.stat(directory)
    except OSError:
        return False  # cannot verify; do not deny on a corner case unrelated to hijack
    return bool(st.st_mode & stat.S_IWOTH)


def _path_hijack_problem(argv0: str, policy: Policy) -> str | None:
    if not argv0:
        return None  # empty-argv already covers this
    if "/" in argv0:
        if not argv0.startswith("/"):
            return f"argv[0] is a relative path: {argv0!r}"
        parent = argv0.rsplit("/", 1)[0] or "/"
        if _is_world_writable_dir(parent):
            return f"argv[0] lives under a world-writable directory: {parent!r}"
        return None
    for directory in policy.path:
        candidate = f"{directory.rstrip('/')}/{argv0}"
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return None
    return f"argv[0] {argv0!r} does not resolve on the policy's fixed PATH"


_SHELL_NAMES = {"sh", "bash", "zsh", "dash", "fish"}
_PYTHON_RE = re.compile(r"^python[0-9.]*$")


def _short_opt_cluster_has(argv_tail: Sequence[str], letters: str) -> bool:
    for tok in argv_tail:
        if tok.startswith("--") or not tok.startswith("-") or len(tok) < 2:
            continue
        if any(ch in tok[1:] for ch in letters):
            return True
    return False


def _inline_shell_problem(argv: Sequence[str]) -> str | None:
    if not argv:
        return None
    base = _basename(argv[0])
    tail = argv[1:]
    if base in _SHELL_NAMES and _short_opt_cluster_has(tail, "c"):
        return f"{base} -c runs a shell string, not an argv; put the script on disk"
    if _PYTHON_RE.match(base) and _short_opt_cluster_has(tail, "c"):
        return f"{base} -c runs inline code, not a script path"
    if base == "perl" and _short_opt_cluster_has(tail, "eE"):
        return "perl -e/-E runs inline code, not a script path"
    if base == "node" and _short_opt_cluster_has(tail, "ep"):
        return "node -e/-p runs inline code, not a script path"
    if base == "ruby" and _short_opt_cluster_has(tail, "e"):
        return "ruby -e runs inline code, not a script path"
    if base in ("awk", "gawk", "mawk", "nawk") and any("system(" in a for a in tail):
        return "awk program calls system(); argv only, no shell escape"
    return None


_ROOT_PATHS = ("/", "~", "/home", "/etc", "/var", "/usr", "/boot")


def _is_dangerous_rm_target(arg: str) -> bool:
    p = arg.rstrip("/") or "/"
    if p in _ROOT_PATHS:
        return True
    for root in _ROOT_PATHS:
        prefix = "" if root == "/" else root
        if p == f"{prefix}/*":
            return True
    return False


def _destructive_problem(argv: Sequence[str]) -> str | None:
    if not argv:
        return None
    base = _basename(argv[0])
    tail = argv[1:]

    if base == "rm":
        has_r = _short_opt_cluster_has(tail, "rR") or "--recursive" in tail
        has_f = _short_opt_cluster_has(tail, "f") or "--force" in tail
        if has_r or has_f:
            for a in tail:
                if not a.startswith("-") and _is_dangerous_rm_target(a):
                    return f"rm -r/-f on a root-ish path: {a!r}"

    if base.startswith("mkfs"):
        return f"{base} formats a filesystem"
    if base == "wipefs":
        return "wipefs erases filesystem signatures"
    if base == "dd" and any(a.startswith("of=/dev/") for a in tail):
        return "dd writing directly to a /dev/* device"
    if base == "shred" and any(a.startswith("/dev/") for a in tail):
        return "shred targeting a /dev/* device"
    if base in ("shutdown", "reboot", "poweroff", "halt"):
        return f"{base} stops the host"
    if base == "init" and ("0" in tail or "6" in tail):
        return "init 0|6 stops/reboots the host"
    if base == "systemctl" and any(a in ("poweroff", "reboot", "halt") for a in tail):
        return "systemctl poweroff|reboot|halt stops the host"
    if base in ("chmod", "chown"):
        has_R = _short_opt_cluster_has(tail, "R") or "--recursive" in tail
        if has_R and any(a.rstrip("/") in ("", "/") for a in tail if not a.startswith("-")):
            return f"{base} -R on /"
    if base == "git" and "push" in tail:
        idx = tail.index("push")
        after = tail[idx + 1:]
        if any(a in ("--force", "-f", "--mirror", "--delete") for a in after) or \
                any(a.startswith("+") for a in after if not a.startswith("--")):
            return "git push --force/-f/--mirror/--delete or a +refspec"
    if base == "docker":
        if "system" in tail and "prune" in tail:
            return "docker system prune"
        if "volume" in tail and ("rm" in tail or "prune" in tail):
            return "docker volume rm|prune"
    if base == "iptables" and ("-F" in tail or "--flush" in tail):
        return "iptables -F flushes the firewall"
    if base == "nft" and "flush" in tail:
        return "nft flush"
    if base == "crontab" and "-r" in tail:
        return "crontab -r deletes the crontab"
    return None


_GIT_FLAGGED_OPTIONS = ("-c", "-C", "--git-dir", "--work-tree", "--exec-path",
                         "--output", "--upload-pack", "--receive-pack",
                         "--config-env", "--exec")


def _git_option_injection_problem(argv: Sequence[str]) -> str | None:
    if not argv or _basename(argv[0]) != "git":
        return None
    for tok in argv[1:]:
        for flag in _GIT_FLAGGED_OPTIONS:
            if tok == flag or tok.startswith(flag + "="):
                return (f"git {flag} can read .git/config or run an arbitrary program "
                         "even for a read verb (review F2)")
    return None
