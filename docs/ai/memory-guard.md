# Memory guard

**What it does:** every 10 seconds, look at `MemAvailable`, and *only* when it is
under the floor (default 1 GiB), stop the **newest** running agent scope — one
unit, one shot — then say so on one line. Nothing else in the system is
signalled.

**What it never touches:** your login session, `sshd`, `tailscaled`, the
OpenCode gateway, the MCP gateway, and any unit that is not an agent run. There
is no `kill(1)` call in the guard at all; the only privileged thing it ever asks
for is `systemctl --user stop <one autoos-worker scope>`.

It is the answer to "my workstation froze because an agent run ate the RAM",
without the two usual fixes: killing the whole session (you lose the terminal
typed in), or running `earlyoom` as root (it decides per *process*, not per run,
and it would happily stop `sshd`).

---

## Install

`memguard` is a normal Linux catalog component:

```bash
./setup.sh --only memguard        # or pick "Memory guard" in the menu
```

That stages the guard script itself at
`~/.local/share/autoos/memguard.sh` (a byte-identical, `cmp`-checked copy, mode
`0755`), renders the two user units from their templates in
[`lib/linux/systemd/user/`](../../lib/linux/systemd/user/) into
`~/.config/systemd/user/`, and enables the timer
([`install_memguard`](../../lib/linux/install.sh)):

| Unit | Role |
|---|---|
| [`memguard.service`](../../lib/linux/systemd/user/memguard.service) | `Type=oneshot`, `ExecStart=%h/.local/share/autoos/memguard.sh` — the **installed** copy, never the checkout, so moving or deleting the repository cannot leave the timer with a dead `ExecStart` |
| [`memguard.timer`](../../lib/linux/systemd/user/memguard.timer) | `OnBootSec=30s`, `OnUnitActiveSec=10s`, `AccuracySec=1s` — a pressure alarm on the wall clock, not a daily chore |

Detection is **both halves** — the timer file *and* the installed guard script —
so "installed" means "the timer an operator can see exists and it has a script to
run". A second run reports `skipped`, never `failed`, and neither the script nor
an unchanged unit is rewritten (an unchanged rewrite would restart the timer on
every idempotent run).

Remove it the ordinary way:

```bash
systemctl --user disable --now memguard.timer
```

The units are yours once installed, so AutoOS backs them up before replacing a
locally edited copy and leaves them alone when they are current.

## The rule, in one paragraph

`memguard.sh` reads `MemAvailable` (kiB). If it is `>= AUTOOS_MEMGUARD_MIN_KIB`
(default `1048576` = 1 GiB) the script exits 0 with **no side effects**: no
`systemctl` call, no log line, no state file. Below the floor it asks
`systemctl --user list-units --state=active` for `autoos-worker-*.scope` —
**only running scopes are candidates**, because an inactive or failed scope
keeps its `ActiveEnterTimestampMonotonic` and would otherwise win "newest" while
the real memory hog runs untouched — orders them by that timestamp (the boot
tick at which each scope went active, so "newest" is stable even with two guards
racing), and stops the **first** one that passes the name gate. At most one unit
per invocation: the point is to give a runaway run one hard nudge, not to empty
the queue.

A stop additionally needs **two consecutive low samples**: the first sample
below the floor only records the verdict in
`~/.local/state/autoos/memguard.state`, and the stop fires only when the next
sample is still low at most 30 s later. A sample at or above the floor — or a
missing, garbled or stale verdict — breaks the pair, so a single moment of
pressure can never take a run down. Before the stop is issued the pair is
consumed: the verdict is reset to "none" and written to the state file so the
next stop requires two fresh consecutive low samples before memory has had time
to recover, and no failure during or after the stop can leave a stale low
verdict. Any `systemctl` failure is a log line and nothing else: if `show` fails or
returns a non-numeric timestamp for ANY listed unit, that tick is ambiguous
and stops nothing (logging at most one rate-limited line `timestamp unknown for <n> unit(s), nothing stopped`);
a tie for the newest numeric timestamp between two or more units also stops
nothing (logging `newest scope ambiguous`); a failed `list-units` writes one
rate-limited line and stops nothing; a failed `stop` writes one rate-limited line
`stop of <unit> failed, nothing claimed`; and `state_write` never aborts
(an unwritable directory does not crash-loop the timer).

The name gate is two checks, in [`memguard.sh`](../../lib/linux/memguard.sh):

```
^autoos-worker-[A-Za-z0-9_.-]+\.scope$
```

The prefix/suffix shape (the contract with the launcher) **and** a character
whitelist, applied to each name even though systemd produced the list. A name
with a `;`, a space or a `/` in it is rejected before it can be handed to
`systemctl`, so a corrupted or crafted unit name can never be stopped.

Every stop is one journal line and one `~/.local/state/autoos/memguard.log` line
(the guard writes **both**: the file, and stdout, so the unit's
`StandardOutput=journal` claim is true):

```
2026-10-02T09:15:10Z MemAvailable=512100 stopped autoos-worker-20261002-090000-task-abcdef.scope
```

The file is bounded: past 256 KiB it is rotated to `memguard.log.1` (one
generation — the old file is replaced, so repeated offenders cannot fill the
disk; the 256 KiB threshold is a soft cap) before the next line is appended.

- Log write failures are swallowed, the stdout/journal line remains (an
  uncreatable or unwritable log path or full disk never aborts the guard).
- The guard stops only when it could record the consumed pair: if the state
  directory is unwritable or the disk is full, it logs a warning and stops
  nothing (fails closed, so an unconsumed pair cannot repeatedly trigger stops).

When the machine is under the floor and there is **no** agent run to stop (or
none of the units passes the gate), it writes

```
2026-10-02T09:15:10Z MemAvailable=512100 below threshold, no agent run to stop
```

**once per 10 minutes** and nothing at all on the ticks in between — a guard that
prints every 10 s into a journal is its own load.

## Why only agent runs

A memory alarm keyed to `MemAvailable` has to answer "what is the least
expensive useful thing to give back?" On a workstation running AutoOS that is
one agent run, not the desktop session around it: runs are individually
expensive (a model conversation plus its MCP children), individually cheap to
lose (the spawner can start it again), and they are already grouped so that one
name names the whole tree. See [why not earlyoom](#why-not-earlyoom) for the
alternative.

### When a run is *not* in a scope

The guard can only stop what systemd can name. `tools/autoos-agent.py` puts a
run into an `autoos-worker-*.scope` on both of its launch paths — the CLI `run`
(which also inherits and keeps a scope it already sits in) and the MCP detached
runner (`tools/autoos_agent_mcp.py`) — but only when `systemd-run --user` is
usable on the host (a probe launches a real scope around a no-op; containers and
bare CI runners have no user manager).

Where scoping is unavailable, the launcher is **required to say so out loud**
before the run starts, on stderr and in the run's output log:

```
autoos-agent: WARNING client runs UNSCOPED (systemd-run --user is not usable here): cancel falls back to the process-group kill
```

That line is the contract: an unscoped run has no cgroup for memguard to stop,
so on such a host the guard's "no agent run to stop" line is a *true* statement,
not a failure — the load has to be relieved some other way (add swap, close the
run yourself, or fix the user manager). `tests/test_worker_scope_spawn_paths.py`
asserts both halves: every spawn path goes through the scope builder, and the
unsupported reason is announced rather than swallowed.

## Report and seams

Read the guard's own decisions — one line per stop, and one "nothing to stop"
line per 10 minutes at most:

```bash
tail -f ~/.local/state/autoos/memguard.log
```

The timer's lifecycle and any `systemctl` error go to the journal (the units set
`StandardOutput=journal` for exactly that):

```bash
journalctl --user -u memguard -f
```

Seams for testing and for one-off experiments — never set on a real machine:

| Variable | Default | Meaning |
|---|---|---|
| `AUTOOS_MEMGUARD_MIN_KIB` | `1048576` | The floor, in kiB |
| `AUTOOS_MEMGUARD_MEMINFO` | `/proc/meminfo` | Read `MemAvailable` from here instead |
| `AUTOOS_MEMGUARD_UNITS` | *(from `systemctl`)* | Newline-separated unit names to consider, newest first |
| `AUTOOS_MEMGUARD_LOG` | `~/.local/state/autoos/memguard.log` | The guard's own log |
| `AUTOOS_MEMGUARD_STOP_LOG` | *(same as `LOG`)* | Where `stopped` / `no agent run` lines land (rotated at 256 KiB) |
| `AUTOOS_MEMGUARD_STATE` | `~/.local/state/autoos/memguard.state` | One line: `<verdict> <sample epoch> <told epoch>` — the two-sample verdict and the "already told you recently" marker |
| `AUTOOS_MEMGUARD_DISABLE` | *(unset)* | Any non-empty value turns the guard off |

With `AUTOOS_MEMGUARD_UNITS` set the script never calls `systemctl` for
discovery, and with a synthetic `MemAvailable` it never reads the machine — that
is how the tests drive it with a stub `systemctl` on `PATH` and never touch a
real cgroup.

## Why not earlyoom

- **`earlyoom` needs root** and is installed as a system daemon; memguard runs
  as you (`systemd --user`), so it needs no privileged install step.
- **It decides for processes, not runs.** A single `opencode` CLI process is not
  the payload — its runner, its MCP children and the whole conversation are — so
  per-process selection either misses the run or lands inside one half way.
- **It would kill `sshd`.** Under real pressure `sshd` and the compositor are
  exactly the residents you must not lose; memguard's name gate cannot select
  them because they are not `autoos-worker-*.scope` units.
- **It runs all the time.** memguard is a no-op above the floor, so an
  unpressured machine pays one `awk` of a timer tick and nothing else.

## Tests

- [`tests/linux/44-memguard.sh`](../../tests/linux/44-memguard.sh) — drives the
  guard with a synthetic `meminfo` and a stub `systemctl`: above threshold is a
  silent no-op, the newest of two scopes is the one stopped, a non-worker name is
  never stopped, the quiet window holds, and the catalog/units/docs wiring is
  present. It also pins the failure-mode contract directly: one low sample stops
  nothing, low-then-high-then-low never adds up to a pair, a stop attempt consumes
  the pair (resetting verdict to "none" so the next stop needs two fresh low samples),
  an inactive scope is never chosen, a failing `show` stops nothing and logs one
  rate-limited line, a tie for newest timestamp is ambiguous and stops nothing,
  a failing `stop` logs one rate-limited line without aborting, an unwritable state
  directory never aborts or leaves tmp files, a failing `list-units` logs one line
  and stops nothing without aborting, the log rotates at 256 KiB to a single `.1`
  generation, log write failures are swallowed with stdout/journal output preserved,
  the state file is written before the stop, and the installer's copy is byte-identical
  to the checkout's script at mode 0755 with `ExecStart` naming the installed path.
- [`tests/test_worker_scope_spawn_paths.py`](../../tests/test_worker_scope_spawn_paths.py)
  — every spawn path in the launcher reaches the scope builder, and
  `SCOPE_REASON_UNSUPPORTED` is warned loudly.

```bash
bash tests/run-tests.sh --filter memguard
python3 tests/test_worker_scope_spawn_paths.py
```
