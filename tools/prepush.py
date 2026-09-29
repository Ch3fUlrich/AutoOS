#!/usr/bin/env python3
"""Refuse a push whose tests were never run — the pre-push gate (operator D-154).

WHY THIS EXISTS
---------------
Three lanes went red in CI while green on the developer's host, and each red is a
shape this gate refuses:

  * SCOPECLI (CI 36517453134) cited rule ``R-orch-17``, which existed only on a
    newer ``main``: the lane never merged main, so it never ran the tests that
    merged main would have run.  Refusal (a) — the fetched ``origin/main`` must be
    an ancestor of HEAD before a push, and (c) always includes the cheap dangling
    -id check ``tests/test_skill_rules.py``.
  * SBA (CI 36493098467): a fixture ``git commit`` returned 128 on the runner,
    which has no git identity, while the dev host's global ``user.name``/
    ``user.email`` made the same test pass at home.  Refusal (d) — the suite runs
    in CI's git env, so a test that depends on an identity fails here first.
  * FREEKEYS2 (CI 36506339556 shard e): 17 render tests, because the worker ran
    "the pytest files it thought relevant".  Refusal (c) — the run list is derived
    from the changed files by ``tools/affected-tests.py``, not guessed.

WHAT IT DOES, IN ORDER
----------------------
  a. ``origin/main`` (the local fetched ref — the hook runs offline) is an ancestor
     of HEAD, or the push is refused with "merge origin/main first (R-coord-01)".
  b. CI's own plan job: ``tests/test_ci_shards.py`` and ``tests/ci-shards.py``
     (``.github/workflows/ci.yml`` linux-plan). A shard map that drops a part fails
     the lane here, not in the plan job.
  c. The changed files vs the base map to tests — pytest files, whole
     ``tests/linux/NN-*.sh`` parts, and ``--filter`` terms — through
     ``tools/affected-tests.py --changed-files-from <base> --format plan``.
  d. Those run under CI's git env with ``/usr/bin/python3`` (``python3`` on PATH is
     often a uv venv with no pytest) and the bash suite only ever narrowed by a
     filter or a part selection (an unfiltered whole-suite run is the host-OOM
     shape, R-host-08).
  e. A bash run that examined nothing is red, not green: ``run-tests.sh`` exits 0
     and prints ``passed 0 failed 0 skipped 0`` when its filter matches no case, so
     the tally is read as well as the status (R-worker-05 — "no tests ran" exited 0
     and was pushed once already).

GREEN LEAVES A RECORD: one JSON certificate per commit in the runner-private
store at ``<state-dir>/prepush/<sha>.json`` (rule D-154). ``store_dir()`` is the
sibling of the spawner's kill record — same ``clients.state_dir()`` base, same
0700 dir and 0600 files — and the location is imported from there, not copied,
so the two runner-private stores move together. A record binds: the commit sha,
that commit's *tree* hash, the sorted manifest of the commands the gate ran,
each command's result and parsed ``N passed`` count, and a UTC stamp. It is
written atomically (temp + rename). ``--check-ready <sha>`` (rule D-110)
recomputes the sha's tree and accepts only a record whose sha AND tree match and
whose every result is ok; an ``OVERRIDE`` record is never green. The store lives
outside every worktree because the worktree is the worker's: the old record
file sat in ``<git-dir>/autoos-prepush.log``, and anything that could write the
checkout could certify its own push. That log stays — one line per push, the
``green_line`` shape — as a human-readable annotation, and readiness never reads
it. ``autoos-agent.py ready`` calls ``local_green()`` as its fifth gate, and an
orchestrator that means to waive it names a reason with ``--allow-unverified``.

OVERRIDE: ``AUTOOS_PREPUSH_OVERRIDE="<reason>"`` skips the checks, prints a loud
line, and stores a ``kind: OVERRIDE`` record for the sha (and the same line in
the human log). It is for an orchestrator that has run the checks some other way;
a worker pushing its own lane has no reason that fits. An override is never
green, so ``--check-ready`` still refuses it.

USAGE
-----
    python3 tools/prepush.py                    # the gate, on this checkout's HEAD
    python3 tools/prepush.py --repo DIR --base REF
    python3 tools/prepush.py --check-ready <sha>

Exit 0 green (or overridden), 1 a gate refused, 2 the gate itself could not run.
1 is a verdict the lane can act on (merge the base, fix the red check); 2 is the
absence of one — no checkout, no HEAD commit, no ``tools/affected-tests.py`` to
derive the run list from, or a mapper that died — and a caller that waits on 1
must not wait forever on it.

The pre-push hook is a three-line shim that calls this file; it is installed by
``.agents/skills/unattended-orchestration/trust_worktree.py``, which chains rather
than overwrites any hook that was already there.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# The store's base location is the spawner's own state tree; the import is the
# one-home rule (Principle 1) — `clients.state_dir()` decides where runner state
# lives, and `kill_store_dir()` in tools/autoos-agent.py reads from the same
# function. Insert the tools dir first so the import resolves when this file is
# loaded by path (tests) as well as run as a script or imported by the agent.
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
try:
    import autoos_clients as clients  # noqa: E402
except ImportError:  # the pre-push hook's shim may run a copy of this file
    clients = None                     # alone; store_root() falls back to the
                                       # same formula from the script's own dir

DEFAULT_BASE = "origin/main"
OVERRIDE_ENV = "AUTOOS_PREPUSH_OVERRIDE"
LOG_NAME = "autoos-prepush.log"
#: The gate's record store: ``<state-dir>/prepush``, the sibling of the
#: spawner's kill store (``kill_store_dir`` names ``<state-dir>/kill``).
STORE_SUBDIR = "prepush"
#: Set by the spawner for every worker it launches (tools/autoos-agent.py).
#: The gate still runs and reports under it, but it records nothing: a green
#: certificate is an orchestrator's act, not a worker's (D-154).
RUN_ID_ENV = "AUTOOS_AGENT_RUN_ID"
#: The ``kind`` of an override record. It is what makes an override readable as
#: "the gate was stepped over" a month later, and what --check-ready refuses.
OVERRIDE_MARKER = "OVERRIDE"
#: The ``kind`` of a green record, as ``green_record`` writes and
#: ``record_is_green`` reads it.
GREEN = "green"
#: The third field of the human-readable log line, written by ``green_line``.
#: The log annotates the push; readiness reads the store, not this (D-154).
GREEN_MARKER = "green:"
#: A commit's full id, exactly as ``git rev-parse HEAD`` prints it — the sha a
#: record binds and the only name a store file may take.
SHA_HEX = re.compile(r"[0-9a-f]{40}\Z")
#: A record's timestamp, exactly as ``now_utc`` stamps it.
UTC_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
#: The CI plan job's two commands (.github/workflows/ci.yml, linux-plan).
PLAN_CHECKS = ("tests/test_ci_shards.py", "tests/ci-shards.py")
#: Identity variables a developer host carries and CI does not (the SBA red).
GIT_IDENTITY_PREFIXES = ("GIT_AUTHOR_", "GIT_COMMITTER_")
#: How much of a failing command's output is printed; the tail holds the assertion,
#: the head holds only the banner the runner prints first.
TAIL_LINES = 60
#: The bash suite's tally, and the colour escapes that may wrap its numbers when
#: the runner is not writing to a terminal. See ``suite_selected_cases``.
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SH_SUMMARY = re.compile(r"passed\s+(\d+)\s+failed\s+(\d+)\s+skipped\s+(\d+)")
#: pytest's own summary — ``5 passed``, ``1 passed, 2 warnings in 0.4s``. What
#: ``parse_passed`` reads off a run, alongside the bash suite's tally.
PYTEST_PASSED = re.compile(r"(\d+) passed")

#: The gate's answers. ``REFUSED`` is a verdict the lane can act on — fix the tree,
#: merge the base, run the check. ``COULD_NOT_RUN`` is the absence of a verdict: no
#: command was named, no check was asked, because the gate itself had nothing to
#: work with. They are different codes because they are different next actions, and
#: a caller that treats "wait, the lane is not ready yet" as rc 1 must not wait
#: forever on a checkout that cannot answer.
REFUSED = 1
COULD_NOT_RUN = 2


class GateCouldNotRun(Exception):
    """The gate never got to ask its question — raised by the helpers that locate
    the checkout, its HEAD and the store path, and turned into ``COULD_NOT_RUN``
    by ``main``. Library callers (``tools/autoos-agent.py``) read the record API
    instead, and a failure here is theirs to decide about, not a silent exit."""


def _git(repo, *args, check=False):
    """Run git in ``repo`` and return stdout, or None on a non-zero exit."""
    proc = subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        if check:
            sys.stderr.write("prepush: git %s failed: %s\n"
                             % (" ".join(args), proc.stderr.strip()))
        return None
    return proc.stdout.strip()


def repo_root(path=None) -> Path:
    """The top level of the checkout ``path`` (default: the cwd) lives in."""
    out = _git(path or Path.cwd(), "rev-parse", "--show-toplevel")
    if out is None:
        raise GateCouldNotRun("prepush: %s is not inside a git checkout"
                              % (path or os.getcwd()))
    return Path(out)


def log_path(repo) -> Path:
    """``<git-dir>/autoos-prepush.log`` — the *human-readable* log: one line per
    push, kept because an operator reading a worktree wants to see what the gate
    did there. Readiness never reads it (D-154): the worker owns this file."""
    gitdir = _git(repo, "rev-parse", "--absolute-git-dir")
    if gitdir is None:
        raise GateCouldNotRun("prepush: %s is not a git repository" % repo)
    return Path(gitdir) / LOG_NAME


def python_executable() -> str:
    """The interpreter children run under: a system python3, never a venv shim.

    ``python3`` on a lane worker's PATH is frequently a uv virtualenv that has no
    pytest installed, and the suite it then "runs" is not the suite CI runs.
    """
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return sys.executable or "python3"


def ci_env(extra=None) -> dict:
    """This host's env with the git identity CI does not have (rule d)."""
    env = dict(os.environ)
    for key in [k for k in env if k.startswith(GIT_IDENTITY_PREFIXES)]:
        del env[key]
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.update(extra or {})
    return env


def head_sha(repo) -> str:
    sha = _git(repo, "rev-parse", "HEAD")
    if sha is None:
        raise GateCouldNotRun("prepush: %s has no HEAD commit" % repo)
    return sha


def resolve_sha(repo, sha: str) -> str:
    """The full hex ``sha`` names, or the string itself when nothing resolves."""
    full = _git(repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % sha)
    return full or sha


def base_gate(repo, base: str):
    """``(ok, message)`` — the fetched base must be an ancestor of HEAD (a)."""
    if _git(repo, "rev-parse", "--verify", "--quiet", base) is None:
        return False, ("prepush: no fetched copy of %s — this gate never reaches the "
                       "network, so fetch it (or name the real base with --base) "
                       "before pushing (R-coord-01)" % base)
    if _git(repo, "merge-base", "--is-ancestor", base, "HEAD") is None:
        return False, ("prepush: %s is not an ancestor of HEAD — merge %s first "
                       "(R-coord-01). A lane that never took in main tests a tree "
                       "CI never builds." % (base, base))
    return True, None


def build_plan(repo, base: str, py: str):
    """``(plan, error)`` — the run list derived from the changed files (c)."""
    tool = Path(repo) / "tools" / "affected-tests.py"
    if not tool.is_file():
        return None, "prepush: no %s to derive the run list from" % tool
    cmd = [py, str(tool), "--changed-files-from", base, "--format", "plan",
           "--root", str(repo)]
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    if proc.returncode != 0:
        return None, "prepush: $ %s\n%s" % (shlex.join(cmd), proc.stderr.strip())
    try:
        return json.loads(proc.stdout), None
    except ValueError:
        return None, ("prepush: %s printed no JSON plan:\n%s"
                      % (tool.name, proc.stdout[:500]))


def commands_for(repo, plan: dict, py: str):
    """The ordered ``([argv], extra_env)`` list a green run is made of.

    The bash suite is only ever invoked narrowed — by a ``--filter`` term list, or
    by a part selection the way CI's own shard runs one part. An unfiltered
    whole-suite run is the shape that OOM-killed a 16 GB host (R-host-08), and a
    filter and a part selection are never intersected: the intersection of two
    narrowings silently drops coverage instead of adding it.
    """
    cmds = [([py, rel], {}) for rel in PLAN_CHECKS]
    files = sorted(set(plan.get("pytest") or []))
    if files:
        cmds.append(([py, "-m", "pytest", "-q"] + files, {}))
    terms = (plan.get("terms") or "").strip()
    if terms:
        cmds.append((["bash", "tests/run-tests.sh", "--filter", terms], {}))
    parts = sorted(set(plan.get("parts") or []))
    if parts:
        cmds.append((["bash", "tests/run-tests.sh"],
                     {"AUTOOS_TEST_PARTS": ",".join(parts), "AUTOOS_FULL_SUITE": "1"}))
    return cmds


def render(argv, extra_env) -> str:
    """The command as an operator would type it, selection included."""
    text = shlex.join(argv)
    if "AUTOOS_TEST_PARTS" in extra_env:
        text = "AUTOOS_TEST_PARTS=%s AUTOOS_FULL_SUITE=1 bash tests/run-tests.sh" % (
            extra_env["AUTOOS_TEST_PARTS"])
    return text


def run_command(repo, argv, extra_env):
    """``(ok, output)`` for one check, under CI's git env (d)."""
    proc = subprocess.run(argv, cwd=str(repo), env=ci_env(extra_env),
                          capture_output=True, text=True, errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        out += "\n[exit status %d]\n" % proc.returncode
    return proc.returncode == 0, out


def tail(text: str, lines: int = TAIL_LINES) -> str:
    keep = text.splitlines()
    return "\n".join(keep[-lines:]) if len(keep) > lines else text


def store_dir() -> Path:
    """The runner-private home of the gate's records: ``<state-dir>/prepush``.

    The sibling of the spawner's kill store — ``kill_store_dir()`` in
    tools/autoos-agent.py names ``<state-dir>/kill`` off the same
    ``clients.state_dir()`` this imports, so the location has one home and the
    two stores that must not live in a worker's checkout move together.

    WHY not the git dir (D-154): the record used to be a line in
    ``<git-dir>/autoos-prepush.log``, inside the worktree the worker owns, and
    any process that could write the checkout could certify its own push. A
    certificate the certified party holds is not one. (Residual, stated as the
    kill store states its own: a same-uid worker that goes looking can find
    this directory through its inherited ``AUTOOS_STATE_DIR`` — what it cannot
    do is get the gate to write a green record into it; see ``RUN_ID_ENV``.)
    """
    if clients is not None:
        return Path(clients.state_dir()) / STORE_SUBDIR
    # A copy of this file with no tools/autoos_clients.py beside it (the hook's
    # shim run from a stripped checkout). The defaulting formula belongs to
    # autoos_clients alone; the only state root this file may name for itself
    # is the one the environment already names.
    root = (os.environ.get("AUTOOS_STATE_DIR") or "").strip()
    if not root:
        raise GateCouldNotRun("prepush: no tools/autoos_clients.py and no "
                              "AUTOOS_STATE_DIR — the record store's location "
                              "has no owner here")
    return Path(root) / STORE_SUBDIR


def record_path(sha: str) -> Path:
    """The one record file for ``sha``, named so no value escapes the store —
    the rule ``kill_store_path`` applies to run ids, with the gate's own
    40-hex test. A non-sha is not a commit, so it is not a record."""
    if not SHA_HEX.match(str(sha)):
        raise GateCouldNotRun("prepush: %r is not a full commit sha" % (sha,))
    return store_dir() / ("%s.json" % sha)


def write_store_record(rec: dict) -> Path:
    """Write one record atomically: temp file + rename, 0700 dir, 0600 file —
    the kill store's shape, because a record that appears half-written is a
    record nothing can read, and a store any process can rename into is not
    private."""
    path = record_path(rec["sha"])
    directory = path.parent
    os.makedirs(directory, exist_ok=True)
    os.chmod(directory, 0o700)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_store_record(sha: str):
    """The stored record for a full sha, or None: no file, JSON that does not
    parse, or a file that does not name the sha it is filed under. None is the
    shape of every refusal here — a garbage record certifies nothing and never
    raises."""
    try:
        path = record_path(sha)
    except GateCouldNotRun:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("sha") != path.stem:
        return None
    return rec


def tree_of(repo, commit: str):
    """The tree hash ``commit`` points at, or None when it resolves to no
    commit. A record binds it (D-154 item 2) because a rebase or an amend
    re-mints history and a certificate must name the content that was tested,
    not only an id that floats."""
    return _git(repo, "rev-parse", "--verify", "--quiet", "%s^{tree}" % commit)


def green_record(sha: str, tree: str, results) -> dict:
    """The record a green gate writes: the sha, its tree, the UTC stamp, the
    sorted manifest, and each command's result and parsed count in run order.

    Lives here rather than inline at the call site because a lane that claims
    ready and a test that stages a ready lane must agree on the shape field for
    field: this is the writer, ``record_is_green`` is its one reader, and a
    test pins the two together — a change to either shape fails there instead
    of unreadying every lane quietly.
    """
    return {"kind": GREEN, "sha": sha, "tree": tree, "utc": now_utc(),
            "commands": sorted(r["command"] for r in results),
            "results": list(results)}


def override_record(sha: str, tree: str, reason: str) -> dict:
    """The record an override writes. Stored with the same bindings as a green
    one, and never accepted as one."""
    return {"kind": OVERRIDE_MARKER, "sha": sha, "tree": tree,
            "utc": now_utc(), "reason": oneline(reason)}


def record_is_green(rec, sha: str, tree) -> bool:
    """The one predicate (D-154 item 3): does this record certify ``sha``?

    Only a complete green record does: it names this sha, binds this exact
    tree, carries the manifest the gate ran and a result per command, every
    one ok, and the UTC stamp only ``now_utc`` writes. An ``OVERRIDE`` never
    is — the lane stepped over the gate and a ready claim must not inherit
    that. A record missing any of that is not a record the gate wrote, and
    nothing half-shaped gets to certify a push.
    """
    if not isinstance(rec, dict) or rec.get("sha") != sha:
        return False
    if rec.get("kind") != GREEN or not tree or rec.get("tree") != tree:
        return False
    if not UTC_STAMP.match(str(rec.get("utc") or "")):
        return False
    commands = rec.get("commands")
    results = rec.get("results")
    if not isinstance(commands, list) or not commands:
        return False
    if not isinstance(results, list) or not results:
        return False
    return all(isinstance(r, dict) and r.get("command") in commands
               and r.get("ok") is True for r in results)


def local_green(sha, repo=None) -> bool:
    """True when the runner-private store holds a green, tree-matching record
    for exactly ``sha`` (D-110, D-154). The worktree's own log is not read —
    a worker can write its checkout."""
    repo = repo or Path.cwd()
    target = resolve_sha(repo, str(sha))
    return record_is_green(read_store_record(target), target,
                           tree_of(repo, target))


def ready_gate(repo, sha: str):
    """``(ok, message)`` for ``--check-ready <sha>`` (rule D-110): recompute
    the sha's tree, accept only the store's record for that sha whose tree
    matches and whose every result is ok."""
    target = resolve_sha(repo, sha)
    tree = tree_of(repo, target)
    rec = read_store_record(target)
    if record_is_green(rec, target, tree):
        return True, "prepush: %s is ready — a green gate record binds this commit and tree (%s)" % (
            target[:12], rec["utc"])
    if rec and rec.get("kind") == OVERRIDE_MARKER:
        why = ("its record is an OVERRIDE, which means the gate was stepped over")
    elif tree is None:
        why = "the sha is no commit in this checkout, so its tree cannot be read"
    elif rec is None:
        why = "no gate record for it was ever written"
    else:
        why = ("the record does not bind this commit's tree, or does not carry "
               "this shape, or not every result is ok")
    return False, "prepush: %s NOT READY — %s (D-110)" % (target[:12], why)


def green_line(sha: str, ran=()) -> str:
    """The line a green gate leaves in the worktree's human-readable log.

    An annotation, nothing more: the certificate is ``green_record``'s store
    file, and no reader of readiness ever opens this one (D-154 item 4).
    """
    return "%s %s %s %s" % (sha, now_utc(), GREEN_MARKER, "; ".join(ran))


def record(repo, line: str) -> Path:
    """Append one human-readable line to the worktree's log (the file may not
    exist yet). Never read for readiness."""
    path = log_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
    return path


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def oneline(text: str) -> str:
    """Flatten a caller-supplied reason: one log line per record, always."""
    return " ".join(text.split())


def suite_selected_cases(output: str):
    """How many cases ``run-tests.sh`` says it looked at, or None if it said nothing.

    R-worker-05 in this tool's own shape: a filtered run whose terms match no case
    prints ``passed 0 failed 0 skipped 0`` and exits 0. The exit code cannot tell
    "the change is fine" from "nothing was examined", and a derived filter that
    selected nothing would hand a free pass to exactly the lane the gate exists for.
    Cases that all *skipped* count as examined: on a host where a part's cases are
    platform-skipped, refusing that would send a lane off to fix a thing that is
    not broken.
    """
    last = None
    for line in output.splitlines():
        found = SH_SUMMARY.search(ANSI.sub("", line))
        if found:
            last = found
    if last is None:
        return None
    return sum(int(group) for group in last.groups())


def is_suite_run(argv) -> bool:
    """True for the bash suite, the only runner that reports a case tally."""
    return any(str(part).endswith("run-tests.sh") for part in argv)


def parse_passed(output: str):
    """The ``N passed`` a run printed — the bash suite's tally or pytest's
    summary line — or None when it printed neither.

    Stored per command (D-154 item 2) so a reader sees what was examined, not
    only that the exit code was 0: a plan-check script that exits 0 in silence
    legitimately prints neither, and the gate's own zero-case refusal for the
    bash suite reads ``suite_selected_cases``, not this.
    """
    text = ANSI.sub("", output or "")
    last = None
    for line in text.splitlines():
        found = SH_SUMMARY.search(line)
        if found:
            last = found
    if last is not None:
        return int(last.group(1))
    for line in reversed(text.splitlines()):
        found = PYTEST_PASSED.search(line)
        if found:
            return int(found.group(1))
    return None


def gate(repo, base: str):
    """Run the gate. Returns 0 green, 1 refused, 2 the gate could not run."""
    override = oneline(os.environ.get(OVERRIDE_ENV) or "")
    sha = head_sha(repo)
    tree = tree_of(repo, sha)
    if tree is None:
        raise GateCouldNotRun("prepush: cannot read the tree of %s" % sha)
    if override:
        write_store_record(override_record(sha, tree, override))
        record(repo, "%s %s %s" % (sha, OVERRIDE_MARKER, override))
        print("prepush: CHECKS SKIPPED by AUTOOS_PREPUSH_OVERRIDE — %s\n"
              "prepush: nothing was run for %s; --check-ready will refuse this sha, "
              "and CI will not be told otherwise." % (override, sha[:12]))
        return 0
    ok, message = base_gate(repo, base)
    if not ok:
        print(message)
        return REFUSED
    py = python_executable()
    plan, error = build_plan(repo, base, py)
    if error:
        # No command was ever named, so there is no verdict here —
        # not even a red one.
        print(error)
        return COULD_NOT_RUN
    commands = commands_for(repo, plan, py)
    results = []
    failures = []
    for argv, extra in commands:
        text = render(argv, extra)
        good, out = run_command(repo, argv, extra)
        if good and is_suite_run(argv) and suite_selected_cases(out) == 0:
            # Exit 0 and an empty tally: the selection matched no case, so nothing
            # was examined. Refuse it as the failure it is.
            good = False
            out += ("\n[the run examined no case: passed 0 failed 0 skipped 0 -- "
                    "a selection that matches no test is not a green test]\n")
        results.append({"command": text, "ok": good, "passed": parse_passed(out)})
        if good:
            print("prepush: ok   %s" % text)
        else:
            print("prepush: FAIL %s" % text)
            print(tail(out))
            failures.append(text)
    if failures:
        print("\nprepush: refused — %d check(s) red. Fix, or run the checks yourself "
              "and push with AUTOOS_PREPUSH_OVERRIDE=\"<reason>\" (orchestrators only; "
              "it is logged and --check-ready will still refuse the sha)."
              % len(failures))
        for text in failures:
            print("  failed: %s" % text)
        return REFUSED
    try:
        write_store_record(green_record(sha, tree, results))
    except OSError as exc:
        # The checks passed and the certificate cannot be written: there is no
        # verdict the ready gate could read, so the push is not certified —
        # the same refusal a missing record answers with later.
        print("prepush: green for %s, but the record could not be written (%s) — "
              "nothing was certified; --check-ready will refuse this sha"
              % (sha[:12], exc))
        return COULD_NOT_RUN
    record(repo, green_line(sha, [r["command"] for r in results]))
    print("prepush: green — %d check(s) for %s" % (len(results), sha[:12]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="prepush", description=__doc__.splitlines()[0],
        epilog="see the module docstring for what each gate refuses and why")
    ap.add_argument("--repo", default=None,
                    help="checkout to gate (default: the one the cwd is in)")
    ap.add_argument("--base", default=DEFAULT_BASE,
                    help="the fetched base ref HEAD must carry (default: %s)"
                         % DEFAULT_BASE)
    ap.add_argument("--check-ready", metavar="SHA", default=None,
                    help="exit 0 only if the runner-private store holds a green "
                         "gate record binding exactly this sha and its tree, with "
                         "every result ok (D-110, D-154)")
    args = ap.parse_args(argv)
    try:
        repo = repo_root(args.repo)
        if args.check_ready:
            ok, message = ready_gate(repo, args.check_ready)
            print(message)
            return REFUSED if not ok else 0
        return gate(repo, args.base)
    except GateCouldNotRun as exc:
        print(exc)
        return COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
