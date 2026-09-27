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
    use-host-alias           on every host kind, ssh/scp/sftp always, rsync
                             with host:path, and parallel with --sshlogin/-S/
                             --sshloginfile/--transfer/--return
                             (checked on every head)
    docker-root              docker/podman run|create binding / or a system
                             dir (/etc, /root, /var, /usr, /boot, /run,
                             /home itself or a bare /home/<user>) in any
                             spelling (-v, attached -v, --volume[=],
                             --mount[=]; deeper HOME projects allowed),
                             --privileged/--pid=host/--userns=host/
                             --cap-add/--device; exec with --privileged or
                             -u 0/root (checked on every head)
    no-sudo                  sudo/sudo-rs/su/doas/pkexec/run0 as any argv
                             element's basename, anywhere (accepted false
                             positive: `grep sudo file` denies); heads cover
                             wrappers
    no-inline-shell          sh/bash/ksh/mksh/csh/tcsh/ash/dash/zsh/fish/
                             busybox -c, python* -c, perl -e/-E,
                             node -e/-p, ruby -e, php -r, lua -e,
                             Rscript -e, julia -e/-E, awk with "system(" or
                             a pipe to a command, tar exec hooks
                             (--checkpoint-action=exec, --to-command,
                             --use-compress-program, -I), editors/pagers
                             with argv commands (vim/vi/nvim/view/ex -c/+!
                             with `!`, less/more +!, man -P/--pager), a bare
                             shell via a wrapper, script/systemd-run/at/
                             batch/setpriv/chroot/unshare/nsenter/runuser/sg/
                             tmux/screen/dtach always -- scripts by
                             path are allowed (checked on every head)
    destructive              rm -r/-f on a root-ish path or glob of one,
                             mkfs*/wipefs, dd of=/dev/*, shred /dev/*,
                             shutdown/reboot/poweroff/halt, init 0|6,
                             systemctl poweroff/reboot/halt, chmod/chown -R /,
                             git push --force/-f/--mirror/--delete/
                             --force-with-lease/--force-if-includes or a
                             +refspec, docker system prune, docker volume
                             rm/prune, iptables -F, nft flush, crontab
                             anything but a pure list (-l)
                             (checked on every command head)
    git-option-injection     git -c/-C/--git-dir/--work-tree/--exec-path/
                             --output/--upload-pack/--receive-pack/
                             --config-env/--exec (review F2) plus git config
                             writing alias.*/core.pager/editor/sshCommand/
                             fsmonitor/hooksPath/*.sshcommand/*uploadpack/
                             *receivepack/*gitproxy/*.helper/include.path/
                             url.* plus submodule foreach, bisect run,
                             rebase --exec/-x (checked on every command head)

A deny-list can never be complete: every new exec-capable tool is a
bypass until listed here. hostexec is an audit + guard boundary.

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
    if not argv:
        return Decision(False, "empty-argv", ("argv is empty",))
    # Caller-supplied argv (MCP JSON) can carry non-string elements
    # (numbers, null, nested arrays). Treat that as a caps violation instead
    # of a TypeError: decide() must never crash on caller input.
    if any(not isinstance(a, str) for a in argv):
        return Decision(False, "argv-caps", ("an argv element is not a string",))
    if not any(a.strip() for a in argv):
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

    # A transparent launcher with an unknown/ambiguous long option cannot be
    # parsed, so the wrapped command is unknowable: fail closed before any
    # later rule could mis-identify a head.
    for head in heads:
        if not head:
            continue
        wrapper_problem = _wrapper_option_problem(head)
        if wrapper_problem:
            return Decision(False, "no-inline-shell", (wrapper_problem,))

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
        split_problem = _env_split_string_problem(head)
        if split_problem:
            return Decision(False, "no-inline-shell", (split_problem,))
        # flock -c/--command (or an abbreviation) runs its argument via a
        # shell (sh -c).
        if base == "flock" and _flock_runs_shell(head):
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


_SUDO_FAMILY = {"sudo", "su", "doas", "pkexec", "run0", "sudo-rs"}
# Exec wrappers that are always denied as no-inline-shell (L1 high):
# script allocates a pty, systemd-run/at/batch create scheduled/transient
# jobs that outlive the audited call, setpriv/chroot/unshare/nsenter change
# privilege/root/namespaces, runuser/sg change uid/gid like sudo. Benign
# resource wrappers (nice/timeout/xargs/find/busybox/...) stay transparent:
# their wrapped command ("head") is checked instead, so `find . -name x`
# and `xargs -a f echo` still allow while `find -exec sh -c` denies.
_ALWAYS_DENY_WRAPPERS = {"script", "systemd-run", "at", "batch", "setpriv",
                         "chroot", "unshare", "nsenter", "runuser", "sg",
                         "tmux", "screen", "dtach"}


def _looks_like_duration(token: str) -> bool:
    body = token[:-1] if token and token[-1] in "smhd" else token
    return bool(body) and body.replace(".", "", 1).isdigit()


def _looks_like_assignment(token: str) -> bool:
    if "=" not in token:
        return False
    name = token.split("=", 1)[0]
    return bool(name) and not name[0].isdigit() and all(c.isalnum() or c == "_" for c in name)


# ─── command heads: transparent exec launchers (brief A) ─────────────────
# _command_heads(argv) returns [argv, child, grandchild, ...] where each
# child is the wrapped command's own argv slice. Every deny rule runs on
# every head, so a wrapper cannot hide sudo/shell/destructive/git flags.
#
# GNU getopt_long accepts any unambiguous abbreviation of a long option and a
# value-taking option swallows the following argv token. The old parsers
# compared long options with == and so mis-parsed abbreviations: "env --ch
# /tmp sudo id" treated /tmp as the wrapped command, leaving sudo as an
# ordinary argument invisible to no-sudo. These tables drive one prefix-aware
# scanner. Unknown or ambiguous long options fail closed (no-inline-shell)
# instead of guessing which token is the command.

_LONG_NONE = "none"
_LONG_REQUIRED = "required"
_LONG_OPTIONAL = "optional"

_ENV_LONGS = {
    "ignore-environment": _LONG_NONE,
    "null": _LONG_NONE,
    "unset": _LONG_REQUIRED,
    "chdir": _LONG_REQUIRED,
    "split-string": _LONG_REQUIRED,
    "argv0": _LONG_REQUIRED,
    "debug": _LONG_NONE,
    "block-signal": _LONG_OPTIONAL,
    "default-signal": _LONG_OPTIONAL,
    "ignore-signal": _LONG_OPTIONAL,
    "list-signal-handling": _LONG_NONE,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_NICE_LONGS = {
    "adjustment": _LONG_REQUIRED,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_TIMEOUT_LONGS = {
    "foreground": _LONG_NONE,
    "kill-after": _LONG_REQUIRED,
    "signal": _LONG_REQUIRED,
    "verbose": _LONG_NONE,
    "preserve-status": _LONG_NONE,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_STDBUF_LONGS = {
    "input": _LONG_REQUIRED,
    "output": _LONG_REQUIRED,
    "error": _LONG_REQUIRED,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_IONICE_LONGS = {
    "class": _LONG_REQUIRED,
    "classdata": _LONG_REQUIRED,
    "ignore": _LONG_NONE,
    "pid": _LONG_REQUIRED,
    "pgid": _LONG_REQUIRED,
    "uid": _LONG_REQUIRED,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_XARGS_LONGS = {
    "null": _LONG_NONE,
    "arg-file": _LONG_REQUIRED,
    "delimiter": _LONG_REQUIRED,
    "eof": _LONG_OPTIONAL,
    "replace": _LONG_OPTIONAL,
    "max-lines": _LONG_OPTIONAL,
    "max-args": _LONG_REQUIRED,
    "max-chars": _LONG_REQUIRED,
    "max-procs": _LONG_REQUIRED,
    "process-slot-var": _LONG_REQUIRED,
    "interactive": _LONG_NONE,
    "no-run-if-empty": _LONG_NONE,
    "open-tty": _LONG_NONE,
    "verbose": _LONG_NONE,
    "exit": _LONG_NONE,
    "show-limits": _LONG_NONE,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_FLOCK_LONGS = {
    "shared": _LONG_NONE,
    "exclusive": _LONG_NONE,
    "unlock": _LONG_NONE,
    "nonblock": _LONG_NONE,
    "timeout": _LONG_REQUIRED,
    "conflict-exit-code": _LONG_REQUIRED,
    "close": _LONG_NONE,
    "command": _LONG_REQUIRED,
    "no-fork": _LONG_NONE,
    "verbose": _LONG_NONE,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_SETSID_LONGS = {
    "ctty": _LONG_NONE,
    "fork": _LONG_NONE,
    "wait": _LONG_NONE,
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}

_NOHUP_LONGS = {
    "help": _LONG_NONE,
    "version": _LONG_NONE,
}


@dataclasses.dataclass(frozen=True)
class _WrapperSpec:
    """How one transparent launcher's leading options parse. ``stops`` holds
    short chars (e.g. env "S", flock "c") *and* canonical long names that
    make the wrapped command statically unknowable; a stop yields no head."""

    label: str
    longs: Mapping[str, str]
    short_value: frozenset[str] = frozenset()
    short_optional: frozenset[str] = frozenset()
    stops: frozenset[str] = frozenset()
    trailing: str = "none"  # none | assignments | duration | file


_ENV_SPEC = _WrapperSpec("env", _ENV_LONGS,
                         short_value=frozenset("uCa"),
                         stops=frozenset(("S", "split-string")),
                         trailing="assignments")
_NICE_SPEC = _WrapperSpec("nice", _NICE_LONGS, short_value=frozenset("n"))
_TIMEOUT_SPEC = _WrapperSpec("timeout", _TIMEOUT_LONGS, short_value=frozenset("sk"),
                             trailing="duration")
_STDBUF_SPEC = _WrapperSpec("stdbuf", _STDBUF_LONGS, short_value=frozenset("ioe"))
_IONICE_SPEC = _WrapperSpec("ionice", _IONICE_LONGS, short_value=frozenset("cn"),
                            stops=frozenset(("p", "pid", "P", "pgid")))
_XARGS_SPEC = _WrapperSpec("xargs", _XARGS_LONGS,
                           short_value=frozenset("adEeILnsP"),
                           short_optional=frozenset("il"))
_FLOCK_SPEC = _WrapperSpec("flock", _FLOCK_LONGS, short_value=frozenset("wE"),
                           stops=frozenset(("c", "command")), trailing="file")
_SETSID_SPEC = _WrapperSpec("setsid", _SETSID_LONGS)
_NOHUP_SPEC = _WrapperSpec("nohup", _NOHUP_LONGS)

_WRAPPER_SPECS: dict[str, _WrapperSpec] = {
    spec.label: spec for spec in (
        _ENV_SPEC, _NICE_SPEC, _TIMEOUT_SPEC, _STDBUF_SPEC, _IONICE_SPEC,
        _XARGS_SPEC, _FLOCK_SPEC, _SETSID_SPEC, _NOHUP_SPEC,
    )
}


def _resolve_long_option(name: str, longs: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Resolve a long option NAME (no leading --) the way getopt_long does:
    an exact match wins, otherwise a unique prefix. Returns (canonical, None)
    or (None, problem) when unknown or ambiguous -- callers fail closed."""
    if not name:
        return None, "an empty long option name"
    if name in longs:
        return name, None
    matches = sorted(opt for opt in longs if opt.startswith(name))
    if not matches:
        return None, f"unknown option --{name}"
    if len(matches) > 1:
        return None, (f"ambiguous option --{name} (could be "
                      f"{', '.join('--' + m for m in matches)})")
    return matches[0], None


def _scan_wrapper_options(cur: Sequence[str], spec: _WrapperSpec) -> tuple[int | None, str | None]:
    """Walk one launcher's leading options. Returns (index_of_command,
    problem): index is None when no wrapped command is statically derivable
    (a stop like env -S or flock -c, or options ran off the end); problem is
    set for an unknown/ambiguous long option so decide() denies instead of
    guessing which token is the command."""
    i, n = 1, len(cur)
    problem: str | None = None
    while i < n:
        tok = cur[i]
        if not isinstance(tok, str):
            i += 1
            continue
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            name, eq, _val = tok[2:].partition("=")
            canonical, prob = _resolve_long_option(name, spec.longs)
            if prob is not None:
                if problem is None:
                    problem = (f"{spec.label}: {prob}; refusing to guess "
                               "the wrapped command")
                i += 1
                continue
            if canonical in spec.stops:
                return None, problem
            mode = spec.longs[canonical]
            i += 1 if (eq or mode != _LONG_REQUIRED) else 2
            continue
        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            rest = tok[1:]
            consumed_next = False
            for k, ch in enumerate(rest):
                if ch in spec.stops:
                    return None, problem
                if ch in spec.short_value:
                    consumed_next = k == len(rest) - 1
                    break
                if ch in spec.short_optional:
                    break
                # An unrecognised short flag does not end the cluster; env
                # -vS must keep scanning past -v to reach -S.
            i += 2 if consumed_next else 1
            continue
        break
    if spec.trailing == "assignments":
        while i < n and _looks_like_assignment(cur[i]):
            i += 1
    elif spec.trailing == "duration":
        if i < n and not cur[i].startswith("-") and _looks_like_duration(cur[i]):
            i += 1
    elif spec.trailing == "file":
        if i < n and not cur[i].startswith("-"):
            i += 1
    return (i if i < n else None), problem


def _wrapper_option_problem(head: Sequence[str]) -> str | None:
    """Unknown/ambiguous long option on a transparent launcher: deny rather
    than assume where the wrapped command starts."""
    if not head:
        return None
    spec = _WRAPPER_SPECS.get(_basename(head[0]))
    if spec is None:
        return None
    return _scan_wrapper_options(head, spec)[1]


def _idx_after_env(s: Sequence[str]) -> int | None:
    return _scan_wrapper_options(s, _ENV_SPEC)[0]


def _env_split_string_problem(argv: Sequence[str]) -> str | None:
    """env -S/--split-string (coreutils) splits a single argument into argv
    and execs the result, e.g. ["env","-S","sudo id"] runs sudo. decide()
    only sees one opaque token, so no wrapper-transparency head exists and
    the real command is invisible to every other rule. Deny it outright
    (same fail-closed posture as _ALWAYS_DENY_WRAPPERS)."""
    if not argv or _basename(argv[0]) != "env":
        return None
    i, n = 1, len(argv)
    while i < n:
        tok = argv[i]
        if not isinstance(tok, str):
            i += 1
            continue
        if tok == "--":
            break
        if tok.startswith("--"):
            name, eq, _val = tok[2:].partition("=")
            canonical, prob = _resolve_long_option(name, _ENV_LONGS)
            if canonical == "split-string":
                return ("env --split-string splits a string into argv and hides the "
                        "real command; run the command directly")
            if prob is not None or canonical in _ENV_SPEC.stops:
                return None  # unknown/ambiguous is _wrapper_option_problem's job
            mode = _ENV_LONGS[canonical]
            i += 1 if (eq or mode != _LONG_REQUIRED) else 2
            continue
        if tok.startswith("-") and len(tok) > 1:
            # Scan the whole cluster left to right. A value-taking char
            # (u/C/a) ends it; an unrecognised flag (-v, -0, ...) does not
            # stop the scan, so -vS and -0vS still reach their -S.
            for ch in tok[1:]:
                if ch == "S":
                    return ("env -S splits a string into argv and hides the "
                            "real command; run the command directly")
                if ch in "uCa":
                    break  # the rest of the token is this option's value
            i += 1
            continue
        break
    return None


def _idx_after_nice(s: Sequence[str]) -> int | None:
    return _scan_wrapper_options(s, _NICE_SPEC)[0]


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
    return _scan_wrapper_options(s, _TIMEOUT_SPEC)[0]


def _idx_after_xargs(s: Sequence[str]) -> int | None:
    return _scan_wrapper_options(s, _XARGS_SPEC)[0]


def _idx_after_ionice(s: Sequence[str]) -> int | None:
    return _scan_wrapper_options(s, _IONICE_SPEC)[0]


def _idx_after_stdbuf(s: Sequence[str]) -> int | None:
    return _scan_wrapper_options(s, _STDBUF_SPEC)[0]


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
    return _scan_wrapper_options(s, _FLOCK_SPEC)[0]


def _flock_runs_shell(head: Sequence[str]) -> bool:
    """flock -c/--command (and any unambiguous abbreviation) runs its argument
    through a shell, so no transparent head exists and decide() denies it."""
    if not head or _basename(head[0]) != "flock":
        return False
    for tok in head[1:]:
        if not isinstance(tok, str):
            continue
        if tok == "--":
            return False
        if tok.startswith("--"):
            name = tok[2:].split("=", 1)[0]
            canonical, _prob = _resolve_long_option(name, _FLOCK_LONGS)
            if canonical == "command":
                return True
        elif tok.startswith("-") and len(tok) > 1 and tok != "-":
            if "c" in tok[1:]:
                return True
        else:
            return False
    return False


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
    # Residual check-then-exec race (item 6, accepted): every probe below is
    # a separate os.* call on the path, while the actual exec happens later
    # in runner._exec. A writer to argv[0]'s own file or an immediate parent
    # can swap the file (or repoint a symlink) in between, so this is a
    # best-effort guard against a statically-hijacked PATH, not a
    # TOCTOU-proof one -- the same posture as the rest of the deny-list
    # (README: an audit boundary, not containment). Closing it would need
    # execveat()/O_PATH on a pinned fd, which Python's subprocess does not
    # expose.
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
    """Brief D + hop: on EVERY host kind, ssh/scp/sftp and rsync-with-remote
    always bypass the policy's host table (forbid-hosts, audit host field) --
    deny with "call host_run with host=<alias>". A second hop from an
    ssh-kind host must go through the policy's host table instead of a raw
    ssh/scp/sftp/rsync/parallel command."""
    _ = host_entry
    if not head:
        return None
    base = _basename(head[0])
    if base in ("ssh", "scp", "sftp"):
        return (f"{base} bypasses the host table "
                f"(forbid-host, audit host); call host_run with host=<alias>")
    if base == "rsync":
        for tok in head[1:]:
            if _looks_like_rsync_remote(tok):
                return (f"rsync with a remote spec {tok!r} bypasses "
                        f"the host table; call host_run with host=<alias>")
    if base == "parallel":
        for tok in head[1:]:
            if tok in (":::", "::::", "--"):
                break  # flags end here; the rest are the command and inputs
            if tok in ("--sshlogin", "--sshloginfile", "--transfer", "--return",
                       "--ssh", "-S"):
                return (f"parallel {tok} bypasses the host table "
                        f"(forbid-host, audit host); call host_run with host=<alias>")
            if tok.startswith(("--sshlogin=", "--sshloginfile=", "--transfer=",
                               "--return=", "--ssh=")):
                return (f"parallel {tok.split('=', 1)[0]} bypasses the host table; "
                        f"call host_run with host=<alias>")
            if tok.startswith("--sshlogin"):
                return ("parallel --sshlogin bypasses the host table; "
                        "call host_run with host=<alias>")
            if tok.startswith("-") and not tok.startswith("--") and "S" in tok[1:]:
                return ("parallel -S bypasses the host table; "
                        "call host_run with host=<alias>")
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
_LUA_RE = re.compile(r"^lua[0-9.]*$")
# awk pipe-to-command: print | "cmd" and "cmd" | getline (r2). Single-pipe
# only ((?<!\|)\|(?!\|)) so logical-or `||` never matches.
_AWK_PIPE_QUOTE_RE = re.compile(r"(?<!\|)\|(?!\|)\s*[\"']")
_AWK_PIPE_GETLINE_RE = re.compile(r"(?<!\|)\|(?!\|)\s*getline\b")
_AWK_QUOTE_PIPE_RE = re.compile(r"[\"']\s*(?<!\|)\|(?!\|)")


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
    if base == "php" and _short_opt_cluster_has(tail, "r"):
        return "php -r runs inline code, not a script path"
    if (base == "lua" or base == "luajit" or _LUA_RE.match(base)) \
            and _short_opt_cluster_has(tail, "e"):
        return "lua -e runs inline code, not a script path"
    if base.lower() == "rscript" and _short_opt_cluster_has(tail, "e"):
        return "Rscript -e runs inline code, not a script path"
    if base == "julia" and _short_opt_cluster_has(tail, "eE"):
        return "julia -e/-E runs inline code, not a script path"
    if base in ("awk", "gawk", "mawk", "nawk"):
        for prog in tail:
            if "system(" in prog:
                return "awk program calls system(); argv only, no shell escape"
            if _AWK_PIPE_QUOTE_RE.search(prog) or _AWK_PIPE_GETLINE_RE.search(prog) \
                    or _AWK_QUOTE_PIPE_RE.search(prog):
                return "awk program pipes to a command; argv only, no shell escape"
    if base in ("vim", "vi", "nvim", "view", "ex"):
        for idx, tok in enumerate(tail):
            if tok == "-c":
                nxt = tail[idx + 1] if idx + 1 < len(tail) else ""
                if "!" in nxt:
                    return f"{base} -c with '!' runs a shell command"
                continue
            if tok.startswith("-c") and len(tok) > 2:
                if "!" in tok[2:]:
                    return f"{base} -c with '!' runs a shell command"
                continue
            if tok.startswith("+") and len(tok) > 1 and "!" in tok:
                return f"{base} +cmd with '!' runs a shell command"
    if base in ("less", "more"):
        for tok in tail:
            if tok.startswith("+") and "!" in tok:
                return f"{base} +! runs a shell command"
    if base == "man":
        for tok in tail:
            if tok == "-P" or tok.startswith("--pager"):
                return "man -P/--pager runs a pager command"
            if tok.startswith("-P") and len(tok) > 2:
                return "man -P/--pager runs a pager command"
    if base == "tar":
        for idx, tok in enumerate(tail):
            if tok.startswith("--checkpoint-action"):
                rest_val = tok.split("=", 1)[1] if "=" in tok else (
                    tail[idx + 1] if idx + 1 < len(tail) else "")
                if "exec" in rest_val.lower():
                    return "tar --checkpoint-action=exec runs a command via a shell"
                if tok == "--checkpoint-action":
                    # Bare --checkpoint-action with a separate exec= value is
                    # still an exec hook; fail closed on the flag itself when
                    # the value is missing (git would error, but deny first).
                    continue
            if tok == "--to-command" or tok.startswith("--to-command="):
                return "tar --to-command runs a command via a shell"
            if tok == "--use-compress-program" or tok.startswith("--use-compress-program="):
                return "tar --use-compress-program runs a program"
            if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1 \
                    and "I" in tok[1:]:
                return "tar -I runs a program"
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
    if base == "crontab":
        # r2: a crontab file install (or `-`/edit) schedules commands outside
        # the audited call window, like at/batch. Only a pure list stays allowed.
        if "-r" in tail:
            return "crontab -r deletes the crontab"
        asking_list = "-l" in tail or "--list" in tail
        # Strip flag values (-u user) before looking for a positional file.
        tmp = list(tail)
        i = 0
        while i < len(tmp):
            if tmp[i] in ("-u", "--user") and i + 1 < len(tmp):
                del tmp[i:i + 2]
                continue
            if tmp[i].startswith("--user="):
                del tmp[i]
                continue
            i += 1
        has_positional = any(t == "-" or not t.startswith("-") for t in tmp
                             if t not in ("-l", "--list"))
        # Remove the list flags themselves from the positional test above;
        # anything else positional (a file, `-`, a username without -u) denies.
        if has_positional:
            return "crontab installs a file that runs outside the audited call"
        if asking_list:
            return None
        return "crontab installs a file that runs outside the audited call"
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
    sub_problem = _git_exec_subcommand_problem(argv)
    if sub_problem:
        return sub_problem
    cfg_problem = _git_config_write_problem(argv)
    if cfg_problem:
        return cfg_problem
    return None


def _git_exec_subcommand_problem(argv: Sequence[str]) -> str | None:
    """Subcommands that execute argv via a shell (r2): `git submodule
    foreach <cmd>`, `git bisect run <cmd>`, `git rebase --exec/-x <cmd>`."""
    tail = list(argv[1:])
    # First non-flag token is the subcommand (globals like --no-pager skipped).
    sub = None
    for tok in tail:
        if tok == "--":
            continue
        if tok.startswith("-") and tok != "-":
            continue
        sub = tok
        break
    # submodule foreach <cmd>: deny when a non-flag command follows foreach.
    if sub == "submodule" and "foreach" in tail:
        fi = tail.index("foreach")
        for tok in tail[fi + 1:]:
            if tok == "--":
                continue
            if tok.startswith("-") and tok != "-":
                continue
            return "git submodule foreach runs its command via a shell"
    # bisect run <cmd>: deny when a non-flag command follows run.
    if sub == "bisect" and "run" in tail:
        ri = tail.index("run")
        for tok in tail[ri + 1:]:
            if tok == "--":
                continue
            if tok.startswith("-") and tok != "-":
                continue
            return "git bisect run runs its command via a shell"
    # rebase --exec/-x <cmd>.
    if sub == "rebase":
        for tok in tail:
            if tok == "--exec" or tok.startswith("--exec="):
                return "git rebase --exec runs its command via a shell"
            if tok == "-x":
                return "git rebase -x runs its command via a shell"
            if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2 \
                    and "x" in tok[1:]:
                return "git rebase -x runs its command via a shell"
    return None


def _is_dangerous_git_config_key(tok: str) -> bool:
    """Brief F: alias.*, core.pager/editor/sshCommand/fsmonitor/hooksPath,
    *.helper, include.path, url.*, any *.sshcommand (incl.
    remote.*.sshCommand), *uploadpack/*receivepack (incl. remote.*),
    *gitproxy (core.gitProxy) -- case-insensitive on the key."""
    if not tok or tok.startswith("-"):
        return False
    key = tok.split("=", 1)[0].lower()
    if key.startswith("alias."):
        return True
    if key in ("core.pager", "core.editor", "core.sshcommand",
               "core.fsmonitor", "core.hookspath", "include.path"):
        return True
    if key.endswith(".sshcommand") or key.endswith("sshcommand"):
        return True
    if key.endswith("uploadpack") or key.endswith("receivepack"):
        return True
    if key.endswith("gitproxy"):
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
