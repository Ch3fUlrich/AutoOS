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
                             a dangerous NAME=value anywhere (LD_*, DYLD_*,
                             BASH_ENV, ENV, PROMPT_COMMAND, NODE_OPTIONS,
                             PYTHONPATH/STARTUP/HOME, PERL5OPT/LIB,
                             RUBYOPT/LIB, GIT_SSH_COMMAND/EXTERNAL_DIFF/
                             PAGER/EDITOR/CONFIG_*/EXEC_PATH)
                             (checked on every command head)
    path-hijack              argv[0] is a relative path, or a bare name that
                             does not resolve on the policy's FIXED PATH, or
                             an absolute path whose resolved file/immediate
                             parent is writable by the uid or world-writable,
                             or a symlink outside the fixed PATH (resolved
                             targets re-enter basename rules)
                             (checked on every command head)
    use-host-alias           on a local host, ssh/scp/sftp always and rsync
                             with host:path (checked on every head)
    docker-root              docker/podman run|create binding / or a system
                             dir, --privileged/--pid=host/--userns=host/
                             --cap-add/--device; exec with --privileged or
                             -u 0/root (checked on every head)
    no-sudo                  sudo/su/doas/pkexec/run0 as any argv element's
                             basename, anywhere (accepted false positive:
                             `grep sudo file` denies); heads cover wrappers
    no-inline-shell          sh/bash/ksh/mksh/csh/tcsh/ash/dash/zsh/fish/
                             busybox -c, python* -c, perl -e/-E,
                             node -e/-p, ruby -e, awk with "system(" in a
                             program argument, bare shell via a wrapper,
                             script/systemd-run/at/batch/setpriv/chroot/
                             unshare/nsenter/runuser/sg always -- scripts by
                             path are allowed (checked on every head)
    destructive              rm -r/-f on a root-ish path or glob of one,
                             mkfs*/wipefs, dd of=/dev/*, shred /dev/*,
                             shutdown/reboot/poweroff/halt, init 0|6,
                             systemctl poweroff/reboot/halt, chmod/chown -R /,
                             git push --force/-f/--mirror/--delete/
                             --force-with-lease/--force-if-includes or a
                             +refspec, docker system prune, docker volume
                             rm/prune, iptables -F, nft flush, crontab -r
                             (checked on every command head)
    git-option-injection     git -c/-C/--git-dir/--work-tree/--exec-path/
                             --output/--upload-pack/--receive-pack/
                             --config-env/--exec (review F2) plus git config
                             writing alias.*/core.pager/editor/sshCommand/
                             fsmonitor/hooksPath/*.helper/include.path/url.*
                             (checked on every command head)

Command heads (brief A): argv[0], then recursively the command after any
transparent launcher (env, nice, nohup, timeout, xargs, ionice, stdbuf,
setsid, chrt, flock, taskset, time, watch, unbuffer, parallel,
busybox <applet>, find -exec/-execdir/-ok/-okdir, ssh remote). Every rule
above runs on every head.
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
    # H/Qoder-8: duplicate actor names break host_log_tail isolation and
    # run_as attribution -- refuse at load (e.g. token rotation overlap).
    seen_names: set[str] = set()
    for actor in actors.values():
        if actor.name in seen_names:
            raise PolicyError(f"{source}: duplicate actor name {actor.name!r}")
        seen_names.add(actor.name)

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
    the reported `rule` is deterministic when more than one would match.

    Wrapper transparency (review Qoder-1, L1 high): argv is expanded into
    "command heads" -- argv[0], then recursively the command after any
    exec-capable launcher (env, nice, timeout, xargs, find -exec, busybox
    applet, ssh remote, ...). EVERY rule below runs on EVERY head, so
    ["env","rm","-rf","/"] trips destructive via its "rm" head just like a
    direct ["rm","-rf","/"] would. See _command_heads()."""
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

    heads = _command_heads(argv)
    # G: symlink targets become extra heads so basename rules re-apply to
    # the resolved file (Qoder-3).
    heads = heads + _resolved_extra_heads(heads)

    for head in heads:
        env_problem = _env_injection_problem(head)
        if env_problem:
            return Decision(False, "env-injection", (env_problem,))

    for head in heads:
        if not head:
            continue
        hijack_problem = _path_hijack_problem(head[0], policy)
        if hijack_problem:
            return Decision(False, "path-hijack", (hijack_problem,))

    for head in heads:
        if not head:
            continue
        alias_problem = _use_host_alias_problem(head, host_entry)
        if alias_problem:
            return Decision(False, "use-host-alias", (alias_problem,))

    for head in heads:
        if not head:
            continue
        dock_problem = _docker_root_problem(head)
        if dock_problem:
            return Decision(False, "docker-root", (dock_problem,))

    # no-sudo: any argv element whose basename is exactly sudo/su/doas/
    # pkexec/run0 denies, anywhere (review L1 high). Accepted false
    # positive: `grep sudo file` is denied too -- documented here and in
    # configuration/hostexec/README.md; the operator chose fail-closed
    # over allowing a token that spells a privilege boundary.
    for head in heads:
        for tok in head:
            if isinstance(tok, str) and _basename(tok) in _SUDO_FAMILY:
                return Decision(False, "no-sudo", (f"{_basename(tok)} is never allowed (Q3)",))

    for idx, head in enumerate(heads):
        if not head:
            continue
        base = _basename(head[0])
        if base in _ALWAYS_DENY_WRAPPERS:
            return Decision(False, "no-inline-shell",
                             (f"{base} is an exec wrapper; run the command directly",))
        # flock -c/--command runs its argument via a shell (sh -c).
        if base == "flock" and any(
                t == "-c" or t == "--command" or t.startswith("--command=")
                for t in head[1:]):
            return Decision(False, "no-inline-shell",
                             ("flock -c runs its command via a shell; put the script on disk",))
        shell_problem = _inline_shell_problem(head)
        if shell_problem:
            return Decision(False, "no-inline-shell", (shell_problem,))
        if idx > 0 and _is_wrapped_bare_shell(head):
            return Decision(False, "no-inline-shell",
                             (f"{base} via a wrapper without a script path runs a shell; "
                              "run the command directly",))

    for head in heads:
        destructive_problem = _destructive_problem(head)
        if destructive_problem:
            return Decision(False, "destructive", (destructive_problem,))

    for head in heads:
        git_opt_problem = _git_option_injection_problem(head)
        if git_opt_problem:
            return Decision(False, "git-option-injection", (git_opt_problem,))

    return Decision(True, None, ())


# ─── helpers ─────────────────────────────────────────────────────────────

def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


_WRAPPERS = {"env", "nice", "nohup", "timeout", "xargs", "ionice", "stdbuf", "setsid"}
_SUDO_FAMILY = {"sudo", "su", "doas", "pkexec", "run0"}
# Exec wrappers that are always denied as no-inline-shell (L1 high):
# script allocates a pty, systemd-run/at/batch create scheduled/transient
# jobs that outlive the audited call, setpriv/chroot/unshare/nsenter change
# privilege/root/namespaces, runuser/sg change uid/gid like sudo. Benign
# resource wrappers (nice/timeout/xargs/find/busybox/...) stay transparent:
# their wrapped command ("head") is checked instead, so `find . -name x`
# and `xargs -a f echo` still allow while `find -exec sh -c` denies.
_ALWAYS_DENY_WRAPPERS = {"script", "systemd-run", "at", "batch", "setpriv",
                         "chroot", "unshare", "nsenter", "runuser", "sg"}


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


# ─── command heads: transparent exec launchers (brief A) ─────────────────
# _command_heads(argv) returns [argv, child, grandchild, ...] where each
# child is the wrapped command's own argv slice. Every deny rule runs on
# every head, so a wrapper cannot hide sudo/shell/destructive/git flags.

def _idx_after_env(s: Sequence[str]) -> int | None:
    i, n = 1, len(s)
    longs_with_val = {"--unset", "--chdir", "--split-string", "--argv0",
                      "--block-signal", "--default-signal", "--ignore-signal"}
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            if "=" in tok:
                i += 1
                continue
            i += 2 if tok in longs_with_val else 1
            continue
        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            if tok in ("-u", "-C", "-S", "-a"):
                i += 2
                continue
            if len(tok) > 2 and tok[1] in "uCSa":
                i += 1
                continue
            i += 1
            continue
        if _looks_like_assignment(tok):
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_nice(s: Sequence[str]) -> int | None:
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-n", "--adjustment"):
            i += 2
            continue
        if tok.startswith("--adjustment=") or tok.startswith("--"):
            i += 1
            continue
        if re.match(r"^-\d+$", tok) or (tok.startswith("-n") and len(tok) > 2):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_simple_flags(s: Sequence[str]) -> int | None:
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_timeout(s: Sequence[str]) -> int | None:
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-s", "--signal", "-k", "--kill-after"):
            i += 2
            continue
        if tok.startswith("--signal=") or tok.startswith("--kill-after=") or tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            if len(tok) > 2 and tok[1] in "sk":
                i += 1
                continue
            i += 1
            continue
        break
    if i < n and not s[i].startswith("-") and _looks_like_duration(s[i]):
        i += 1
    return i if i < n else None


def _idx_after_xargs(s: Sequence[str]) -> int | None:
    shorts_val = set("adEeILnsP")
    longs_val = {"--arg-file", "--delimiter", "--eof", "--replace", "--max-lines",
                 "--max-args", "--max-chars", "--max-procs", "--process-slot-var"}
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            if "=" in tok:
                i += 1
                continue
            i += 2 if tok in longs_val else 1
            continue
        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            if tok in ("-a", "-d", "-E", "-e", "-I", "-L", "-n", "-s", "-P"):
                i += 2
                continue
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_ionice(s: Sequence[str]) -> int | None:
    for tok in s[1:]:
        if tok == "--":
            break
        if tok in ("-p", "--pid") or tok.startswith("--pid="):
            return None  # pid mode: operates on a pid, runs nothing
        if tok.startswith("-"):
            continue
        break
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-c", "--class", "-n", "--classdata"):
            i += 2
            continue
        if tok.startswith("--class=") or tok.startswith("--classdata=") or tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_stdbuf(s: Sequence[str]) -> int | None:
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-i", "--input", "-o", "--output", "-e", "--error"):
            i += 2
            continue
        if tok.startswith(("--input=", "--output=", "--error=")) or tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_chrt(s: Sequence[str]) -> int | None:
    for tok in s[1:]:
        if tok == "--":
            break
        if tok in ("-p", "--pid") or tok.startswith("--pid="):
            return None  # pid mode: no wrapped command
        if tok.startswith("-"):
            continue
        break
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    if i < n and not s[i].startswith("-") and s[i].lstrip("-").isdigit():
        i += 1
    return i if i < n else None


def _idx_after_flock(s: Sequence[str]) -> int | None:
    for tok in s[1:]:
        if tok in ("-c", "--command") or tok.startswith("--command="):
            return None  # shell mode: denied separately, no transparent head
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-w", "--timeout", "-E", "--conflict-exit-code"):
            i += 2
            continue
        if tok.startswith(("--timeout=", "--conflict-exit-code=")) or tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    if i < n and not s[i].startswith("-"):
        i += 1  # the locked file
    return i if i < n else None


def _idx_after_taskset(s: Sequence[str]) -> int | None:
    for tok in s[1:]:
        if tok == "--":
            break
        if tok in ("-p", "--pid") or tok.startswith("--pid="):
            return None  # pid mode: no wrapped command
        if tok.startswith("-"):
            continue
        break
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-c", "--cpu-list"):
            i += 2
            continue
        if tok.startswith("--cpu-list=") or tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    if i < n and not s[i].startswith("-") and re.match(r"^[0-9a-fA-Fx,\-]+$", s[i]):
        i += 1  # the cpu mask
    return i if i < n else None


def _idx_after_watch(s: Sequence[str]) -> int | None:
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-n", "--interval"):
            i += 2
            continue
        if tok.startswith("--interval=") or tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 1
            continue
        break
    return i if i < n else None


def _idx_after_parallel(s: Sequence[str]) -> tuple[int | None, int | None]:
    shorts_val = set("adEjLmnsST")
    longs_val = {"--arg-file", "--delimiter", "--jobs", "--load", "--timeout",
                 "--delay", "--joblog", "--results", "--sshlogin", "--sshloginfile",
                 "--transfer", "--return", "--workdir", "--ssh", "--tagstring",
                 "--header", "--colsep", "--argfilesep", "--max-args", "--number-of-args"}
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok in (":::", "::::", "--"):
            break
        if tok.startswith("--"):
            if "=" in tok:
                i += 1
                continue
            i += 2 if tok in longs_val else 1
            continue
        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            if len(tok) == 2 and tok[1] in shorts_val:
                i += 2
                continue
            i += 1
            continue
        break
    if i >= n:
        return None, None
    if s[i] in (":::", "::::", "--"):
        return None, None
    j = i
    while j < n and s[j] not in (":::", "::::", "--"):
        j += 1
    return i, j


def _idx_after_ssh_remote(s: Sequence[str]) -> int | None:
    shorts_val = set("pilmFoJWDmLRbESQwceI")
    i, n = 1, len(s)
    while i < n:
        tok = s[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            if tok.startswith("--"):
                i += 1
                continue
            if len(tok) == 2 and tok[1] in shorts_val:
                i += 2
                continue
            if len(tok) > 2 and tok[1] in shorts_val:
                i += 1
                continue
            i += 1
            continue
        break
    if i >= n:
        return None
    i += 1  # skip the destination
    if i >= n or s[i].startswith("-"):
        return None
    return i


def _direct_child_heads(cur: Sequence[str]) -> list[list[str]]:
    if not cur:
        return []
    base = _basename(cur[0])
    cur = list(cur)
    if base == "env":
        idx = _idx_after_env(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "nice":
        idx = _idx_after_nice(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base in ("nohup", "setsid", "time", "unbuffer"):
        idx = _idx_after_simple_flags(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "timeout":
        idx = _idx_after_timeout(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "xargs":
        idx = _idx_after_xargs(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "ionice":
        idx = _idx_after_ionice(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "stdbuf":
        idx = _idx_after_stdbuf(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "chrt":
        idx = _idx_after_chrt(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "flock":
        idx = _idx_after_flock(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "taskset":
        idx = _idx_after_taskset(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "watch":
        idx = _idx_after_watch(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] and not cur[idx].startswith("-") else []
    if base == "parallel":
        i, j = _idx_after_parallel(cur)
        if i is None or j is None:
            return []
        child = cur[i:j]
        return [child] if child and not child[0].startswith("-") else []
    if base == "busybox":
        if len(cur) > 1 and not cur[1].startswith("-"):
            return [cur[1:]]
        return []
    if base == "find":
        kids: list[list[str]] = []
        i, n = 1, len(cur)
        while i < n:
            if cur[i] in ("-exec", "-execdir", "-ok", "-okdir"):
                j = i + 1
                buf: list[str] = []
                while j < n and cur[j] not in (";", "+"):
                    buf.append(cur[j])
                    j += 1
                if buf and not buf[0].startswith("-"):
                    kids.append(buf)
                i = j + 1
            else:
                i += 1
        return kids
    if base in ("ssh", "scp"):
        # scp runs no remote command; ssh remote heads feed no-sudo/shell
        # checks (D denies ssh/scp on local hosts separately).
        if base != "ssh":
            return []
        idx = _idx_after_ssh_remote(cur)
        return [cur[idx:]] if idx is not None and cur[idx:] else []
    return []


def _command_heads(argv: Sequence[str]) -> list[list[str]]:
    """All command heads: argv itself plus, recursively, the command after
    any transparent launcher, busybox applet, find -exec, or ssh remote."""
    if not argv:
        return []
    heads: list[list[str]] = [list(argv)]
    queue: list[list[str]] = [list(argv)]
    seen = {tuple(argv)}
    while queue:
        cur = queue.pop(0)
        for child in _direct_child_heads(cur):
            t = tuple(child)
            if not child or t in seen:
                continue
            seen.add(t)
            heads.append(child)
            queue.append(child)
            if len(heads) > 32:  # argv-caps bounds length; this bounds heads
                return heads
    return heads


_DANGEROUS_ENV_VARS = ("LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV")


def _is_dangerous_env_name(name: str) -> bool:
    """Brief C: LD_*, DYLD_*, BASH_ENV, ENV, PROMPT_COMMAND, NODE_OPTIONS,
    PYTHONPATH/STARTUP/HOME, PERL5OPT/LIB, RUBYOPT/LIB, GIT_SSH_COMMAND,
    GIT_EXTERNAL_DIFF, GIT_PAGER/EDITOR, GIT_CONFIG_*, GIT_EXEC_PATH."""
    if name.startswith("LD_") or name.startswith("DYLD_"):
        return True
    if name.startswith("GIT_CONFIG_"):
        return True
    return name in {
        "BASH_ENV", "ENV", "PROMPT_COMMAND", "NODE_OPTIONS",
        "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
        "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB",
        "GIT_SSH_COMMAND", "GIT_EXTERNAL_DIFF", "GIT_PAGER",
        "GIT_EDITOR", "GIT_EXEC_PATH",
    }


def _env_injection_problem(argv: Sequence[str]) -> str | None:
    if argv and _looks_like_assignment(argv[0]):
        return f"argv[0] is an environment assignment, not a command: {argv[0]!r}"
    for tok in argv:
        if "=" in tok:
            name = tok.split("=", 1)[0]
            if name in _DANGEROUS_ENV_VARS or _is_dangerous_env_name(name):
                return f"dangerous environment assignment: {name}"
    return None


def _is_world_writable_dir(directory: str) -> bool:
    try:
        st = os.stat(directory)
    except OSError:
        return False  # cannot verify; do not deny on a corner case unrelated to hijack
    return bool(st.st_mode & stat.S_IWOTH)


def _is_writable_by_me_or_world(path: str) -> bool:
    """G: world-writable (stat) or writable by the current uid (os.access,
    skipped for root where access is always True). Missing paths do not
    deny -- exec would fail anyway, and this rule is about hijack."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if bool(st.st_mode & stat.S_IWOTH):
        return True
    try:
        if os.geteuid() == 0:
            # Root can write everything; check owner-write instead so this
            # rule still means "attacker-writable", not "everything".
            return False
    except AttributeError:
        pass
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _parent_chain(directory: str) -> list[str]:
    """[/a/b, /a, /] for /a/b (absolute only)."""
    out: list[str] = []
    cur = directory
    while True:
        out.append(cur or "/")
        if cur in ("", "/"):
            break
        cur = cur.rsplit("/", 1)[0] or "/"
        if len(out) > 64:
            break
    return out


def _path_hijack_problem(argv0: str, policy: Policy) -> str | None:
    if not argv0:
        return None  # empty-argv already covers this
    if "/" in argv0:
        if not argv0.startswith("/"):
            return f"argv[0] is a relative path: {argv0!r}"
        # G: realpath + uid/world-writable on the resolved file and the
        # immediate parents (original and resolved), plus symlink target
        # outside the fixed PATH. Only immediates -- not the full chain to
        # / -- so system sticky dirs like /tmp don't deny everything under
        # them (tests use /tmp temp dirs; real hijack is the writable leaf).
        try:
            resolved = os.path.realpath(argv0)
        except OSError:
            resolved = argv0
        for p in (resolved, resolved.rsplit("/", 1)[0] or "/",
                  argv0.rsplit("/", 1)[0] or "/"):
            if _is_writable_by_me_or_world(p):
                return f"argv[0] resolves under a writable path: {p!r}"
        if resolved != argv0:
            rdir = resolved.rsplit("/", 1)[0] or "/"
            allowed = {d.rstrip("/") or "/" for d in policy.path}
            if rdir not in allowed:
                return (f"argv[0] is a symlink to {resolved!r}, outside the "
                        f"policy's fixed PATH")
        return None
    for directory in policy.path:
        candidate = f"{directory.rstrip('/')}/{argv0}"
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return None
    return f"argv[0] {argv0!r} does not resolve on the policy's fixed PATH"


def _resolved_extra_heads(heads: list[list[str]]) -> list[list[str]]:
    """G/Qoder-3: for every absolute head, re-apply basename rules to the
    realpath target (e.g. /home/op/bin/id -> /usr/bin/sudo denies as
    no-sudo even though the spelled basename is `id`)."""
    extra: list[list[str]] = []
    seen = {tuple(h) for h in heads}
    for h in heads:
        if not h or "/" not in h[0] or not h[0].startswith("/"):
            continue
        try:
            resolved = os.path.realpath(h[0])
        except OSError:
            continue
        if resolved == h[0]:
            continue
        nh = [resolved] + list(h[1:])
        if tuple(nh) not in seen:
            seen.add(tuple(nh))
            extra.append(nh)
    return extra


def _looks_like_rsync_remote(token: str) -> bool:
    """host:path (incl. user@host:path). Excludes options (-*), local paths
    (/...), and bare filenames without a colon. `a/b:c`? contains a slash
    before the colon -- still remote if the host part has no slash."""
    if not token or token.startswith("-") or token.startswith("/"):
        return False
    if ":" not in token:
        return False
    head, _, tail = token.partition(":")
    if not head or not tail or "/" in head or " " in token:
        return False
    return True


def _use_host_alias_problem(head: Sequence[str], host_entry: HostEntry) -> str | None:
    """Brief D: on a local host, ssh/scp/sftp and rsync-with-remote always
    bypass the policy's host table (forbid-hosts, audit host field) -- deny
    with "call host_run with host=<alias>". Only local hosts: an ssh-kind
    host already runs via `ssh -o BatchMode=yes -T <target>` in runner.py."""
    if host_entry.kind != "local":
        return None
    if not head:
        return None
    base = _basename(head[0])
    if base in ("ssh", "scp", "sftp"):
        return (f"{base} on a local host bypasses the host table "
                f"(forbid-host, audit host); call host_run with host=<alias>")
    if base == "rsync":
        for tok in head[1:]:
            if _looks_like_rsync_remote(tok):
                return (f"rsync with a remote spec {tok!r} on a local host bypasses "
                        f"the host table; call host_run with host=<alias>")
    return None


def _is_system_bind_src(src: str) -> bool:
    if not src:
        return False
    if len(src) > 1 and src.endswith("/"):
        src = src.rstrip("/")
    if src == "/":
        return True
    for sysdir in ("/etc", "/root", "/var", "/usr", "/boot", "/run"):
        if src == sysdir or src.startswith(sysdir + "/"):
            return True
    # /home itself and a bare top-level entry (/home/<user>) stay denied;
    # deeper project dirs (/home/<user>/code/...) are allowed (r2).
    if src == "/home":
        return True
    if src.startswith("/home/"):
        rest = src[len("/home/"):]
        if "/" not in rest:
            return True
        return False
    return False


def _docker_root_problem(head: Sequence[str]) -> str | None:
    """Brief E (L1 high docker-root): docker/podman run|create binding / or
    a system dir, --privileged, --pid=host, --userns=host, --cap-add,
    --device; docker exec with --privileged or -u 0/root."""
    if not head:
        return None
    base = _basename(head[0])
    if base not in ("docker", "podman"):
        return None
    # subcommand: first non-flag token (skip global -H/--config/...).
    i, n = 1, len(head)
    while i < n:
        tok = head[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and tok != "-":
            if tok in ("--config", "-H", "--host", "--context", "--log-level", "-c"):
                i += 2
                continue
            if tok.startswith(("--config=", "--host=", "--context=", "--log-level=")):
                i += 1
                continue
            if len(tok) > 2 and tok[1] == "H":
                i += 1
                continue
            i += 1
            continue
        break
    if i >= n:
        return None
    sub, rest = head[i], list(head[i + 1:])
    if sub in ("run", "create"):
        m = len(rest)
        j = 0
        while j < m:
            tok = rest[j]
            if tok == "--privileged" or tok.startswith("--privileged="):
                return f"{base} {sub} --privileged is host root"
            if (tok == "--pid" and j + 1 < m and rest[j + 1] == "host") or \
                    (tok.startswith("--pid=") and tok.split("=", 1)[1] == "host"):
                return f"{base} {sub} --pid=host"
            if (tok == "--userns" and j + 1 < m and rest[j + 1] == "host") or \
                    (tok.startswith("--userns=") and tok.split("=", 1)[1] == "host"):
                return f"{base} {sub} --userns=host"
            if tok == "--cap-add" or tok.startswith("--cap-add="):
                return f"{base} {sub} --cap-add"
            if tok == "--device" or tok.startswith("--device="):
                return f"{base} {sub} --device"
            vol_val = None
            is_mount = False
            if tok == "-v" and j + 1 < m:
                vol_val = rest[j + 1]
            elif tok.startswith("-v") and len(tok) > 2 and not tok.startswith("--"):
                # Attached short form: -v/src:dst, -vX (r2 high).
                vol_val = tok[2:].lstrip("=")
            elif tok == "--volume" and j + 1 < m:
                vol_val = rest[j + 1]
            elif tok.startswith("--volume="):
                vol_val = tok.split("=", 1)[1]
            elif tok == "--mount" and j + 1 < m:
                vol_val, is_mount = rest[j + 1], True
            elif tok.startswith("--mount="):
                vol_val, is_mount = tok.split("=", 1)[1], True
            if vol_val:
                if is_mount:
                    src = None
                    for part in vol_val.split(","):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            if k in ("src", "source"):
                                src = v
                                break
                    if src and _is_system_bind_src(src):
                        return f"{base} {sub} bind of system path {src!r}"
                else:
                    src = vol_val.split(":")[0]
                    if src and _is_system_bind_src(src):
                        return f"{base} {sub} bind of system path {src!r}"
            j += 1
        return None
    if sub == "exec":
        m = len(rest)
        j = 0
        while j < m:
            tok = rest[j]
            if tok == "--privileged" or tok.startswith("--privileged="):
                return f"{base} exec --privileged"
            if tok == "-u" and j + 1 < m:
                if rest[j + 1].split(":")[0] in ("0", "root"):
                    return f"{base} exec -u 0/root"
            elif tok.startswith("-u") and len(tok) > 2 and not tok.startswith("--"):
                if tok[2:].lstrip("=").split(":")[0] in ("0", "root"):
                    return f"{base} exec -u 0/root"
            elif tok == "--user" and j + 1 < m:
                if rest[j + 1].split(":")[0] in ("0", "root"):
                    return f"{base} exec --user 0/root"
            elif tok.startswith("--user="):
                if tok.split("=", 1)[1].split(":")[0] in ("0", "root"):
                    return f"{base} exec --user 0/root"
            j += 1
        return None
    return None


_SHELL_NAMES = {"sh", "bash", "zsh", "dash", "fish",
                "busybox", "ksh", "mksh", "csh", "tcsh", "ash"}
_PYTHON_RE = re.compile(r"^python[0-9.]*$")


def _is_wrapped_bare_shell(head: Sequence[str]) -> bool:
    """A shell via a wrapper without a script path (e.g. `xargs -a f sh`)
    runs with hidden input (the wrapper's file/found-files) that decide()
    cannot see -- deny even without an explicit -c flag. A shell WITH an
    explicit script path (`env bash /opt/tool.sh`) or a --version/--help
    query still allows."""
    if not head:
        return False
    base = _basename(head[0])
    if base not in _SHELL_NAMES or base == "busybox":
        return False
    tail = list(head[1:])
    if not tail:
        return True
    if any(t in ("--version", "--help", "-h", "-V") for t in tail):
        return False
    for t in tail:
        if t.startswith("-"):
            continue
        if t in ("{}", ";", "+", ":::", "::::", "--"):
            continue
        # Any other positional arg is treated as an explicit script path.
        return False
    return True


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
        if any(a in ("--force", "-f", "--mirror", "--delete",
                     "--force-with-lease", "--force-if-includes") for a in after) or \
                any(a.startswith("--force-with-lease") or a.startswith("--force-if-includes")
                    for a in after) or \
                any(a.startswith("+") for a in after if not a.startswith("--")):
            return "git push --force/-f/--mirror/--delete/--force-with-lease/--force-if-includes or a +refspec"
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
    cfg_problem = _git_config_write_problem(argv)
    if cfg_problem:
        return cfg_problem
    return None


def _is_dangerous_git_config_key(tok: str) -> bool:
    """Brief F: alias.*, core.pager/editor/sshCommand/fsmonitor/hooksPath,
    *.helper, include.path, url.* (case-insensitive on the key)."""
    if not tok or tok.startswith("-"):
        return False
    key = tok.split("=", 1)[0].lower()
    if key.startswith("alias."):
        return True
    if key in ("core.pager", "core.editor", "core.sshcommand",
               "core.fsmonitor", "core.hookspath", "include.path"):
        return True
    if key.endswith(".helper"):
        return True
    if key.startswith("url."):
        return True
    return False


def _git_config_write_problem(argv: Sequence[str]) -> str | None:
    # Find the `config` subcommand (first non-flag token after `git`,
    # skipping safe globals like --no-pager). `git -c ...` already denied
    # above, so any remaining -c is not reached here.
    tail = list(argv[1:])
    idx = None
    for i, tok in enumerate(tail):
        if tok == "--":
            continue
        if tok.startswith("-") and tok != "-":
            continue
        idx = i
        break
    if idx is None or tail[idx] != "config":
        return None
    after = tail[idx + 1:]
    # Read-only modes stay allowed: --get/--get-all/--get-regexp/--list/-l.
    for tok in after:
        if tok in ("--get", "--get-all", "--get-regexp", "--list", "-l",
                   "--get-color", "--print", "--get-urlmatch"):
            return None
        if tok.startswith("--get=") or tok.startswith("--get-all="):
            return None
    for tok in after:
        if _is_dangerous_git_config_key(tok):
            return (f"git config writes dangerous key {tok!r} "
                    "(alias/core.pager/editor/sshCommand/fsmonitor/hooksPath/helper/include/url)")
    return None
