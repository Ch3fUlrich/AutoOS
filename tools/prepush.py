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

GREEN LEAVES A RECORD: ``<git-dir>/autoos-prepush.log``, one line per push —
``<sha> <utc> green: <commands>``. The log lives in the git dir, so it is never
committed and every worktree keeps its own. ``--check-ready <sha>`` (rule D-110)
answers for exactly that sha, which is how a lane that pushed with
``git push --no-verify`` — outside any hook's reach — is caught later, when the
orchestrator tries to declare it ready: ``autoos-agent.py ready`` calls
``local_green()`` as its fifth gate, and an orchestrator that means to waive it
names a reason with ``--allow-unverified``.

OVERRIDE: ``AUTOOS_PREPUSH_OVERRIDE="<reason>"`` skips the checks, prints a loud
line, and logs ``<sha> OVERRIDE <reason>`` in the same file. It is for an
orchestrator that has run the checks some other way; a worker pushing its own lane
has no reason that fits. An override is never green, so ``--check-ready`` still
refuses it.

USAGE
-----
    python3 tools/prepush.py                    # the gate, on this checkout's HEAD
    python3 tools/prepush.py --repo DIR --base REF
    python3 tools/prepush.py --check-ready <sha>

Exit 0 green (or overridden), 1 a gate refused, 2 the gate itself could not run.

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

DEFAULT_BASE = "origin/main"
OVERRIDE_ENV = "AUTOOS_PREPUSH_OVERRIDE"
LOG_NAME = "autoos-prepush.log"
#: The second field of an override record. It is what makes an override readable as
#: "the gate was stepped over" a month later, and what --check-ready refuses.
OVERRIDE_MARKER = "OVERRIDE"
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
        sys.exit("prepush: %s is not inside a git checkout" % (path or os.getcwd()))
    return Path(out)


def log_path(repo) -> Path:
    """``<git-dir>/autoos-prepush.log`` — per worktree, and never a tracked file."""
    gitdir = _git(repo, "rev-parse", "--absolute-git-dir")
    if gitdir is None:
        sys.exit("prepush: %s is not a git repository" % repo)
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
        sys.exit("prepush: %s has no HEAD commit" % repo)
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


def read_records(repo):
    """``[(sha, kind, rest)]`` in file order; kind is the second field."""
    path = log_path(repo)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split(None, 2)
        if len(fields) >= 2:
            records.append((fields[0], fields[1], fields[2] if len(fields) > 2 else ""))
    return records


def local_green(sha, repo=None) -> bool:
    """True when this checkout ran a green gate for exactly ``sha`` (D-110).

    An ``OVERRIDE`` record is never green — that is the point: the lane stepped
    over the gate, and a ready claim must not inherit that. A later green record
    for the same sha beats an earlier override.
    """
    target = resolve_sha(repo or Path.cwd(), str(sha))
    for recorded, kind, _rest in read_records(repo or Path.cwd()):
        if recorded == target and kind != OVERRIDE_MARKER:
            return True
    return False


def ready_gate(repo, sha: str):
    """``(ok, message)`` for ``--check-ready <sha>`` (rule D-110)."""
    target = resolve_sha(repo, sha)
    records = read_records(repo)
    if any(r[0] == target and r[1] != OVERRIDE_MARKER for r in records):
        utc = next((r[1] for r in records if r[0] == target and r[1] != OVERRIDE_MARKER), "")
        return True, "prepush: %s is ready — a green gate record exists (%s)" % (
            target[:12], utc or "no timestamp")
    overridden = any(r[0] == target and r[1] == OVERRIDE_MARKER for r in records)
    why = ("its only record is an OVERRIDE, which means the gate was stepped over"
           if overridden else "no gate record for it was ever written")
    return False, "prepush: %s NOT READY — %s (D-110)" % (target[:12], why)


def green_line(sha: str, ran=()) -> str:
    """The record a green gate writes — the only shape ``--check-ready`` accepts.

    Lives here rather than inline at the call site because a lane that claims ready
    and a test that stages a ready lane must agree on it byte for byte; a second
    copy of the format is how a green record stops being readable.
    """
    return "%s %s green: %s" % (sha, now_utc(), "; ".join(ran))


def record(repo, line: str) -> Path:
    """Append one line to the gate log (the file may not exist yet)."""
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


def gate(repo, base: str):
    """Run the gate. Returns 0 green, 1 refused, 2 the gate could not run."""
    override = oneline(os.environ.get(OVERRIDE_ENV) or "")
    sha = head_sha(repo)
    if override:
        record(repo, "%s %s %s" % (sha, OVERRIDE_MARKER, override))
        print("prepush: CHECKS SKIPPED by AUTOOS_PREPUSH_OVERRIDE — %s\n"
              "prepush: nothing was run for %s; --check-ready will refuse this sha, "
              "and CI will not be told otherwise." % (override, sha[:12]))
        return 0
    ok, message = base_gate(repo, base)
    if not ok:
        print(message)
        return 1
    py = python_executable()
    plan, error = build_plan(repo, base, py)
    if error:
        print(error)
        return 1
    commands = commands_for(repo, plan, py)
    ran = []
    failures = []
    for argv, extra in commands:
        text = render(argv, extra)
        ran.append(text)
        good, out = run_command(repo, argv, extra)
        if good and is_suite_run(argv) and suite_selected_cases(out) == 0:
            # Exit 0 and an empty tally: the selection matched no case, so nothing
            # was examined. Refuse it as the failure it is.
            good = False
            out += ("\n[the run examined no case: passed 0 failed 0 skipped 0 -- "
                    "a selection that matches no test is not a green test]\n")
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
        return 1
    record(repo, green_line(sha, ran))
    print("prepush: green — %d check(s) for %s" % (len(ran), sha[:12]))
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
                    help="exit 0 only if a green (not OVERRIDE) gate record exists "
                         "for exactly this sha (D-110)")
    args = ap.parse_args(argv)
    repo = repo_root(args.repo)
    if args.check_ready:
        ok, message = ready_gate(repo, args.check_ready)
        print(message)
        return 0 if ok else 1
    return gate(repo, args.base)


if __name__ == "__main__":
    sys.exit(main())
