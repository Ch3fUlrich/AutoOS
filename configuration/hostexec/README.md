# hostexec

A host-enforced **deny-list + audit boundary** for agent shell access --
not containment. Code: `tools/hostexec/` (policy/audit/runner/server) and
`tools/hostexec.py` (the `check`/`log`/`serve` CLI). Tests:
`tests/test_hostexec_*.py`, driven by `tests/fixtures/hostexec-decisions.tsv`.

## What this is (and isn't)

Every host-native agent CLI on a typical AutoOS workstation (claude,
codex, qoder, agy, opencode) already runs as the operator's own uid,
which is normally in the `docker` group -- and `docker run -v /:/host
...` is root-equivalent. So **for those CLIs, hostexec is a convention,
not a fence**: anything that can already run a shell directly can bypass
hostexec entirely by doing exactly that. hostexec does not change that,
and does not pretend to.

What it DOES give you:

- a single, real, **fail-closed** audit trail (append-only JSONL, one
  line per call, 90-day retention) that direct shell use never produces;
- a broad, **host-side** deny-list (sudo in any form, inline shell
  escapes, the worst destructive commands, forbidden hosts, PATH
  hijacking, environment injection) that a well-behaved or merely
  careless agent can't cross by accident, and that a hijacked CLIENT
  (not the underlying uid) can't easily talk its way around either,
  since the checks run on the broker's side of the call, not the
  caller's;
- the **only** sanctioned path in for an actor that does NOT already
  have a host shell -- OpenHands' sandbox container, opencode's
  container -- where this genuinely is the boundary, because those
  actors have no other route to the host at all.

Say this plainly to anyone reading a live policy file: **enabling
hostexec on a real host is an operator step**, done outside this public
repository, and it does not substitute for host-side containment
(removing `docker`/`sudo` group membership, forced-command SSH,
restricted shells) for the CLIs that already have a host shell.

## Quick start

```bash
mkdir -p ~/.config/autoos/exec && chmod 700 ~/.config/autoos/exec
cp configuration/hostexec/policy.example.toml ~/.config/autoos/exec/policy.toml   # git-ignored path, edit it there
```

1. **One token per actor** -- never reuse a token across two actors:

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.config/autoos/exec/claude.token
   chmod 600 ~/.config/autoos/exec/claude.token
   ```

2. **Hash it into the policy.** The policy file holds only the sha256 --
   never the raw token:

   ```bash
   python3 -c 'import hashlib; print(hashlib.sha256(open("/home/you/.config/autoos/exec/claude.token").read().strip().encode()).hexdigest())'
   ```

   Use that hex string as the `[actors."<here>"]` table key in
   `policy.toml`.

3. **Fill in your real hosts** under `[hosts.*]`, replacing every
   `<placeholder>`. List anything that must never be reachable with
   `forbid = true` explicitly -- don't just leave it out, so a caller
   gets a named, audited `forbid-host` refusal instead of an ambiguous
   "host not declared" one.

4. **Check a call without running anything** (dry; never touches the
   audit log or a process):

   ```bash
   python3 tools/hostexec.py check --policy ~/.config/autoos/exec/policy.toml \
       --actor claude --host coding-host -- git status
   ```

5. **Start the broker** (foreground; this repository ships no live
   systemd unit -- see "Out of scope"):

   ```bash
   AUTOOS_EXEC_PORT=8765 python3 tools/hostexec.py serve --policy ~/.config/autoos/exec/policy.toml
   ```

   Binds `127.0.0.1` by default (`AUTOOS_EXEC_BIND`, comma-separated for
   more than one address). A non-loopback bind -- including a docker
   bridge gateway IP, needed for OpenHands/opencode-container -- is
   refused unless you also set `AUTOOS_EXEC_ALLOW_LAN=1`; only set that
   once you understand the firewall state around that bridge (it is
   named "not loopback", not literally "the LAN").

6. **Wire a client** by giving it the token file/env var and the
   broker's URL. This repository does not perform that wiring yet
   (installer/client config for OpenHands/opencode/claude/codex/qoder is
   a separate, later change) -- see "Out of scope".

## The rules

`host_policy()` (and `hostexec.py check`) name the exact rule that
refused a call. All ten are deny-list checks against one call's
`(actor, host, argv, cwd)` -- **there is no allow-list**. That is a
deliberate choice (operator decision, 2026-09-26: "ALL: read AND
mutating, for all actors" -- a broad deny-list + audit boundary, not a
narrow allow-list), not an oversight.

| Rule | Refuses |
|---|---|
| `unknown-actor` | the bearer token (or, for `check`, the `--actor` name) is not declared in the policy |
| `empty-argv` | argv is empty, or every element is blank |
| `argv-caps` | too many arguments, or one argument longer than `[limits]` allows |
| `forbid-host` | the host is not declared in the policy, or is declared with `forbid = true` |
| `env-injection` | argv[0] is a `NAME=value` assignment, or a dangerous `NAME=value` anywhere in argv: `LD_*`, `DYLD_*`, `BASH_ENV`, `ENV`, `PROMPT_COMMAND`, `NODE_OPTIONS`, `PYTHONPATH`/`PYTHONSTARTUP`/`PYTHONHOME`, `PERL5OPT`/`PERL5LIB`, `RUBYOPT`/`RUBYLIB`, `GIT_SSH_COMMAND`, `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`, `GIT_CONFIG_*`, `GIT_EXEC_PATH` -- checked on every command head (see below) |
| `path-hijack` | argv[0] is a relative path, an absolute path whose resolved file or immediate parent (original or `realpath`) is writable by the current uid or world-writable, a symlink whose target dir is outside the policy's fixed `path`, or (for a bare name) does not resolve on the policy's own fixed `path` -- never the caller's `$PATH` -- checked on every command head; resolved symlink targets re-enter every basename rule (so a symlink `id -> sudo` still denies as `no-sudo`) |
| `use-host-alias` | on a `local` host, `ssh`/`scp`/`sftp` always, and `rsync` with a remote spec (`host:path`) -- these skip the policy's host table (`forbid-host`, audit `host`); call `host_run` with `host=<alias>` instead -- checked on every command head |
| `docker-root` | `docker`/`podman` `run`/`create` binding `/` or a system dir (`/etc`, `/root`, `/var`, `/usr`, `/boot`, `/home`, `/var/run/docker.sock`, `/run`), `--privileged`, `--pid=host`, `--userns=host`, `--cap-add`, `--device`; `docker exec` with `--privileged` or `-u 0`/`root` -- checked on every command head |
| `no-sudo` | `sudo`/`su`/`doas`/`pkexec`/`run0` as any argv element's basename, anywhere (accepted false positive: `grep sudo file` is denied too -- fail-closed over a token that spells a privilege boundary), directly or hidden behind any exec-capable launcher. There is no sudo/mutating tier today -- one week of soak before it is re-asked |
| `no-inline-shell` | `sh`/`bash`/`ksh`/`mksh`/`csh`/`tcsh`/`ash`/`dash`/`zsh`/`fish`/`busybox -c`, `python* -c`, `perl -e`/`-E`, `node -e`/`-p`, `ruby -e`, or an `awk` program that calls `system(`; a bare shell via a wrapper without a script path (`xargs -a f sh`); `script`/`systemd-run`/`at`/`batch`/`setpriv`/`chroot`/`unshare`/`nsenter`/`runuser`/`sg` always (exec wrappers -- run the command directly). A script invoked BY PATH (`bash /opt/tool.sh`) is fine -- only *inline* code is refused. Checked on every command head |
| `destructive` | `rm -r`/`-f` on `/`, `~`, `/home`, `/etc`, `/var`, `/usr`, `/boot` (or a glob directly under one), `mkfs*`, `wipefs`, `dd of=/dev/*`, `shred /dev/*`, `shutdown`/`reboot`/`poweroff`/`halt`, `init 0\|6`, `systemctl poweroff\|reboot\|halt`, `chmod`/`chown -R /`, `git push --force`/`-f`/`--mirror`/`--delete`/`--force-with-lease`/`--force-if-includes`/a `+refspec`, `docker system prune`, `docker volume rm\|prune`, `iptables -F`, `nft flush`, `crontab -r` -- checked on every command head |
| `git-option-injection` | `git -c`/`-C`/`--git-dir`/`--work-tree`/`--exec-path`/`--output`/`--upload-pack`/`--receive-pack`/`--config-env`/`--exec` -- these read `.git/config` or run an arbitrary external program even behind a "read" verb like `status`/`log`/`diff`; plus `git config` writing `alias.*`, `core.pager`/`core.editor`/`core.sshCommand`/`core.fsmonitor`/`core.hooksPath`, `*.helper`, `include.path`, `url.*` -- checked on every command head |

Command heads: `argv[0]`, then recursively the command after any
exec-capable launcher (`env` incl. `NAME=val` and `-i`/`-u`, `nice`,
`nohup`, `timeout`, `xargs`, `ionice`, `stdbuf`, `setsid`, `chrt`,
`flock`, `taskset`, `time`, `watch`, `unbuffer`, `parallel`,
`busybox <applet>`, `find -exec`/`-execdir`/`-ok`/`-okdir`, `ssh`
remote). Every rule above runs on every head, not on raw `argv[0]`
only.

A denied MCP call comes back as a tool error whose text is a JSON object:

```json
{"refused": true, "rule": "no-sudo", "problems": ["sudo is never allowed (Q3)"],
 "hint": "host_policy() lists what is refused"}
```

## The audit log

One JSONL line per call, at
`$XDG_STATE_HOME/autoos-exec/audit-YYYY-MM-DD.jsonl` by default (state
dir `0700`, files `0600`), written with one `os.write()` per line on an
`O_APPEND` fd under an `flock` (so concurrent writers never interleave),
plus a best-effort copy to journald (`logger -t autoos-exec`). **The
journald copy is advisory only**: anyone who can run `logger` with that
tag can forge or flood it. The `0700`/`0600` file is authoritative.
`seq` is a per-file monotonic counter that correctly resumes across a
broker restart (it is re-derived from the file's own last line under the
same lock as every write, never cached in memory); a gap in `seq` means
loss or tampering, and the log is never silently renumbered.

**Fail closed**: if a call's audit line cannot be written, for any
reason -- including an otherwise-ALLOWED call -- the call is refused and
nothing runs. A broker that cannot write its own audit log refuses to
start at all.

Retention is 90 days (operator decision, 2026-09-26), pruned by filename
date only, never by file mtime (so a touched or copied file cannot dodge
it):

```bash
python3 -c 'import sys; sys.path.insert(0, "tools"); from hostexec import audit; \
print(audit.prune(audit.default_state_dir(), days=90))'
```

Read it with the CLI, not raw `cat`/`journalctl -o cat` on an
agent-controlled field -- the reader strips/escapes control characters at
both write time and render time, so an injected `reason` or `argv`
element can never forge terminal output or a fake extra log line:

```bash
python3 tools/hostexec.py log --since 1h --actor openhands --decision deny
```

Argv is redacted before it is ever written to disk (`KEY=`/`--token`/
`--password`/`Bearer <token>`/`-p<secret>`/`-u user:pass` -> `***`); a
separate `argv_sha256`, over the RAW unredacted argv, lets you still
correlate two identical calls without the secret ever touching disk in
clear.

`host_log_tail(n)` (the MCP tool) only ever returns the CALLING actor's
own lines -- never another actor's -- so it cannot become a cross-actor
disclosure channel or a free oracle for probing the whole allow/deny
surface.

## Out of scope here (see AGENTS.md rule 1 and the lane split)

This package is generic, stdlib-only (except the MCP layer) and public.
It does **not** ship, and this README is not asking you to build:

- a live systemd unit, the real root-owned `/var/log/autoos-exec`, `chattr
  +a`, or a root-owned retention timer -- those are host operations for a
  private, root-owned playbook (the Server repo), not something a public
  repo installs for you. This package uses `$XDG_STATE_HOME` instead, and
  ships `hostexec.py log`'s own `prune()` as a plain function you (or a
  root-owned caller) can invoke;
- any client wiring -- OpenHands `agent_settings.mcp_config`,
  `opencode.json` (host + container render), `claude mcp add --transport
  http`, `~/.codex/config.toml`, qoder/agy `mcp add-json` -- that is a
  separate, later lane (`lib/linux/install.sh` and friends, out of scope
  for this one);
- a real policy instance, or any real hostname, IP, username or token.
  `policy.example.toml` is a template only; every value in angle brackets
  is a placeholder, and the loader (`policy.load`) refuses to start on a
  malformed or missing file rather than falling back to allow-all;
- sudo wrappers, or anything for a "mutating" tier beyond the plain
  deny-list above -- there is no sudo tier yet (operator decision: one
  week of soak on the read+deny-list tier before that is re-asked).

Enabling any of the above on a live host is an operator step, taken
outside this repository (the private Server repo, or by hand) -- never
something that happens just because this package exists in git history.
