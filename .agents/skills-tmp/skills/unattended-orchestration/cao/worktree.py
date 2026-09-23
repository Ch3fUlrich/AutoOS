"""Provision CAO worktrees on a fast filesystem.

Measured on this Windows host, same repo, same WSL Ubuntu::

    repo on /mnt/c (9p)                       git status  3.021 s
    ext4 worktree, .git still on /mnt/c       git status  0.146 s   20x
    fully ext4                                git status  0.017 s  178x

An agent runs ``git status``, file reads and test discovery constantly, so the
first row is a tax on every turn of a long-horizon run.

Why worktrees and not a full clone
----------------------------------
A full ext4 clone is another 8x faster, and is still not what this does. A
worktree shares the main checkout's object store, so commits an agent makes are
already in your repo — nothing to push back, nothing to diverge. The clone would
buy 0.146 s -> 0.017 s at the cost of a synchronisation step that can fail
halfway and leave work in a directory you later delete. The bottleneck is gone
at 20x; the remaining 8x is not worth a class of failure that loses work.

On Linux this module is a no-op improvement: ``$HOME`` is already ext4, the
default root lands there, and :func:`warnings_for` stays quiet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

#: Paths under these are Windows-shared (9p under WSL) and slow.
SLOW_PREFIXES = ("/mnt/", "/c/")

DEFAULT_ROOT = "$HOME/cao-worktrees"


def resolve_root(root: str | None = None) -> str:
    """Expand ``$HOME``/``~`` in a configured worktree root."""
    return os.path.expanduser(os.path.expandvars(root or DEFAULT_ROOT))


def is_slow_path(path) -> bool:
    """Is this path on the Windows-shared filesystem?"""
    normalised = str(path).replace("\\", "/")
    return normalised.startswith(SLOW_PREFIXES)


def warnings_for(root, repo) -> list:
    """Preflight warnings about worktree placement.

    A misplaced worktree root does not fail anything — it just makes every
    subsequent operation ~20x slower, silently. That is exactly the kind of
    problem that needs to be said out loud at preflight.
    """
    out = []
    if is_slow_path(resolve_root(root)):
        out.append(
            f"cao.worktreeRoot resolves to {resolve_root(root)!r}, which is on the "
            "Windows-shared filesystem. Measured: git status costs 3.021s there "
            "versus 0.146s on a native path. Point it at $HOME/cao-worktrees."
        )
    if is_slow_path(repo) and not is_slow_path(resolve_root(root)):
        out.append(
            f"main checkout {str(repo)!r} is on the Windows-shared filesystem, so "
            "object reads still cross it (measured 0.146s vs 0.017s fully native). "
            "This is the expected, recommended layout: the IDE keeps the checkout, "
            "agents get fast worktrees."
        )
    return out


def worktree_path(root, repo, name: str) -> Path:
    """``<root>/<repo folder>-<name>`` — same shape as the batch lane's."""
    return Path(resolve_root(root)) / f"{Path(str(repo).rstrip('/')).name}-{name}"


def provision_script(repo_posix: str, worktree_quoted: str, skill_posix: str,
                     ref: str = "HEAD", graph_id: str | None = None) -> str:
    """The shell that provisions, hydrates and trusts a worktree, as one string.

    This is the single source of truth for "how a worktree is made ready".
    :func:`provision` runs it locally; :mod:`cao.launch` embeds it in the command
    it sends to the distro. Keeping two copies -- one Python, one shell -- is how
    the two quietly stop agreeing about what "ready" means.

    Written to run in the DISTRO, so paths arrive already quoted by the caller:
    a worktree root may carry an unexpanded ``$HOME`` that only the distro can
    resolve, and quoting it here would stop it expanding.

    It also hydrates, for the same reason :func:`hydrate` does: a worktree holds
    tracked files only, so it has no ``.env``, and an agent without
    ``OMNIGRAPH_TOKEN`` fails in ways that look like code bugs.

    Trusting is not optional. Measured: an untrusted worktree leaves Claude Code
    sitting at an approval prompt until CAO deletes the terminal, and agy 1.2.1
    at "Do you trust the contents of this project?".
    """
    steps = [
        f'mkdir -p "$(dirname {worktree_quoted})"',
        f"[ -d {worktree_quoted} ] || "
        f"git -C {repo_posix} worktree add -q --detach {worktree_quoted} {ref}",
        # Hydrate. A worktree holds TRACKED files only, so it has no .env -- and
        # an agent without OMNIGRAPH_TOKEN fails in ways that read as code bugs.
        # `cp -n` so re-provisioning an existing worktree never clobbers one.
        f"[ -f {repo_posix}/.env ] && cp -n {repo_posix}/.env {worktree_quoted}/.env "
        f"2>/dev/null; true",
    ]
    if graph_id:
        # The pin belongs in the FILE, not only in the launch --env: children a
        # supervisor spawns inherit the worktree, not this process's environment,
        # and an unpinned child writes to whatever graph is globally configured.
        steps.append(
            f"grep -q OMNIGRAPH_GRAPH_ID {worktree_quoted}/.env 2>/dev/null || "
            f"echo OMNIGRAPH_GRAPH_ID={graph_id} >> {worktree_quoted}/.env"
        )
    steps.append(
        f"python3 {skill_posix}/trust_worktree.py {worktree_quoted} "
        f"--repo {repo_posix} --mcpjson omnigraph >/dev/null 2>&1 || true"
    )
    return "; ".join(steps)


def parse_worktrees(porcelain: str) -> list:
    """Paths from ``git worktree list --porcelain``, main checkout included."""
    return [
        line.split(" ", 1)[1].strip()
        for line in (porcelain or "").splitlines()
        if line.startswith("worktree ")
    ]


def phase_of(path, graph_id: str):
    """``<root>/<graph_id>-<phase>`` -> ``phase``; None if it is not ours.

    Named this way by :func:`cao.launch.plan_launch`, so anything else under the
    root belongs to someone else and is never reported as an orphan of ours.
    """
    name = str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    prefix = f"{graph_id}-"
    return name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else None


def orphans(worktree_paths, session_names, graph_id: str, project: str) -> list:
    """Worktrees of this repo whose phase has no CAO session any more.

    Reported, never removed. A worktree is where an agent's uncommitted work
    lives, and "no session" can also mean "the server was restarted" -- deleting
    on that inference would throw away work to tidy up. Measured 2026-09-11:
    three worktrees survived four live runs and had to be cleaned by hand, so
    without this they simply accumulate.
    """
    live = tuple(session_names or ())
    out = []
    for path in worktree_paths:
        phase = phase_of(path, graph_id)
        if phase is None:
            continue
        prefix = f"cao-{project}-{phase}"
        if not any(name == prefix or name.startswith(prefix + "-r") for name in live):
            out.append(path)
    return out


def is_dirty(path) -> bool:
    """Does this worktree hold anything uncommitted? Cheap, never raises."""
    try:
        done = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # unknown counts as dirty: never imply it is safe to delete
    if done.returncode != 0:
        # git ANSWERED, unhappily -- a missing or broken worktree exits non-zero
        # with empty stdout, which read as "clean" and so as safe to remove.
        return True
    return bool(done.stdout.strip())


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def provision(repo, name: str, root=None, ref: str = "HEAD") -> Path:
    """Create a detached worktree for ``name`` on a fast filesystem.

    Detached on purpose: several agents cutting from the same ref must not race
    for one branch name, and a leaf's work is committed and merged by its
    supervisor, not by the worktree owning a branch.
    """
    target = worktree_path(root, repo, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        teardown(repo, name, root)
    _git(repo, "worktree", "add", "-q", "--detach", str(target), ref)
    return target


def hydrate(worktree, repo, graph_id: str | None = None) -> list:
    """Copy the untracked things a worktree needs to actually run.

    A worktree holds TRACKED files only: no .env, no generated graph. An agent in
    a bare worktree fails in ways that look like code bugs.
    """
    done = []
    src_env = Path(repo) / ".env"
    if src_env.exists():
        shutil.copy2(src_env, Path(worktree) / ".env")
        done.append("copied .env")

    gid = graph_id or Path(str(repo).rstrip("/\\")).name
    env_file = Path(worktree) / ".env"
    existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    if "OMNIGRAPH_GRAPH_ID" not in existing:
        with env_file.open("a", encoding="utf-8", newline="\n") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"OMNIGRAPH_GRAPH_ID={gid}\n")
        done.append(f"pinned OMNIGRAPH_GRAPH_ID={gid}")
    return done


def teardown(repo, name: str, root=None) -> bool:
    """Remove the worktree. Returns whether anything was removed.

    ``--force`` because an agent leaves a dirty tree behind by definition; the
    branch is not deleted because these are detached checkouts with none.
    """
    target = worktree_path(root, repo, name)
    if not target.exists():
        return False
    _git(repo, "worktree", "remove", "--force", str(target), check=False)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)
    return True
