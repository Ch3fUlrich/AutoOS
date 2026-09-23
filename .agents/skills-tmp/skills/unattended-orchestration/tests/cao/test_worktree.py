import subprocess

import pytest

from cao.worktree import (
    DEFAULT_ROOT,
    hydrate,
    is_slow_path,
    is_dirty,
    orphans,
    parse_worktrees,
    phase_of,
    provision,
    provision_script,
    resolve_root,
    teardown,
    warnings_for,
    worktree_path,
)


def test_default_root_is_under_home_not_the_shared_filesystem():
    assert "$HOME" in DEFAULT_ROOT
    assert not is_slow_path(DEFAULT_ROOT)


def test_resolve_root_expands_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    assert resolve_root("$HOME/cao-worktrees") == "/home/someone/cao-worktrees"


def test_slow_paths_are_recognised():
    assert is_slow_path("/mnt/c/Users/x/Code/repo") is True
    assert is_slow_path("/c/Users/x/Code/repo") is True
    assert is_slow_path(r"C:\Users\x\Code\repo".replace("\\", "/")) is False


def test_native_paths_are_not_slow():
    assert is_slow_path("/home/you/cao-worktrees") is False
    assert is_slow_path("/tmp/wt") is False


def test_worktree_root_on_the_shared_filesystem_is_warned_about(monkeypatch):
    """A misplaced root fails nothing — it just makes everything 20x slower."""
    warns = warnings_for("/mnt/c/Users/x/wt", "/mnt/c/Users/x/repo")
    assert any("Windows-shared filesystem" in w and "3.021s" in w for w in warns)


def test_the_recommended_layout_is_explained_not_flagged_as_wrong(monkeypatch):
    monkeypatch.setenv("HOME", "/home/you")
    warns = warnings_for("$HOME/cao-worktrees", "/mnt/c/Users/x/repo")
    assert len(warns) == 1
    assert "expected, recommended layout" in warns[0]


def test_a_fully_native_layout_is_silent(monkeypatch):
    monkeypatch.setenv("HOME", "/home/you")
    assert warnings_for("$HOME/cao-worktrees", "/home/you/repo") == []


def test_worktree_path_names_by_repo_and_session(monkeypatch):
    monkeypatch.setenv("HOME", "/home/you")
    p = worktree_path("$HOME/cao-worktrees", "/mnt/c/Users/x/agent-skills", "phase1")
    assert p.as_posix().endswith("cao-worktrees/agent-skills-phase1")


def _git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "file.txt").write_text("hello\n", encoding="utf-8")
    (r / ".env").write_text("SECRET_SWITCH=1\n", encoding="utf-8")
    _git(r, "add", "file.txt")
    _git(r, "commit", "-qm", "init")
    return r


def test_provision_creates_a_usable_checkout(repo, tmp_path):
    wt = provision(repo, "s1", root=str(tmp_path / "wt"))
    assert (wt / "file.txt").read_text(encoding="utf-8") == "hello\n"


def test_provision_is_idempotent(repo, tmp_path):
    root = str(tmp_path / "wt")
    first = provision(repo, "s1", root=root)
    second = provision(repo, "s1", root=root)
    assert first == second
    assert (second / "file.txt").exists()


def test_worktree_is_detached_so_lanes_cannot_race_for_a_branch(repo, tmp_path):
    wt = provision(repo, "s1", root=str(tmp_path / "wt"))
    status = _git(wt, "status", "-sb").stdout
    assert "HEAD (no branch)" in status or "detached" in status.lower()


def test_hydrate_copies_the_untracked_env(repo, tmp_path):
    wt = provision(repo, "s1", root=str(tmp_path / "wt"))
    assert not (wt / ".env").exists()  # worktrees carry TRACKED files only
    done = hydrate(wt, repo)
    assert (wt / ".env").read_text(encoding="utf-8").startswith("SECRET_SWITCH=1")
    assert any("copied .env" in d for d in done)


def test_hydrate_pins_the_graph_id_to_the_repo_folder(repo, tmp_path):
    wt = provision(repo, "s1", root=str(tmp_path / "wt"))
    hydrate(wt, repo)
    assert "OMNIGRAPH_GRAPH_ID=repo" in (wt / ".env").read_text(encoding="utf-8")


def test_hydrate_does_not_duplicate_an_existing_graph_pin(repo, tmp_path):
    wt = provision(repo, "s1", root=str(tmp_path / "wt"))
    hydrate(wt, repo)
    hydrate(wt, repo)
    text = (wt / ".env").read_text(encoding="utf-8")
    assert text.count("OMNIGRAPH_GRAPH_ID") == 1


def test_hydrate_accepts_an_explicit_graph_id(repo, tmp_path):
    wt = provision(repo, "s1", root=str(tmp_path / "wt"))
    hydrate(wt, repo, graph_id="agent-skills")
    assert "OMNIGRAPH_GRAPH_ID=agent-skills" in (wt / ".env").read_text(encoding="utf-8")


def test_teardown_removes_it_and_is_safe_to_repeat(repo, tmp_path):
    root = str(tmp_path / "wt")
    wt = provision(repo, "s1", root=root)
    assert teardown(repo, "s1", root=root) is True
    assert not wt.exists()
    assert teardown(repo, "s1", root=root) is False


def test_teardown_survives_a_dirty_worktree(repo, tmp_path):
    """An agent leaves a dirty tree by definition."""
    root = str(tmp_path / "wt")
    wt = provision(repo, "s1", root=root)
    (wt / "scratch.txt").write_text("half-finished\n", encoding="utf-8")
    assert teardown(repo, "s1", root=root) is True
    assert not wt.exists()


# ---------------------------------------------------------------------------
# The provisioning shell, RUN rather than asserted on as a string. A test that
# only greps the generated command proves the command was generated -- which is
# exactly what was true of `hydrate` while nothing called it and every worktree
# went out without a .env.
# ---------------------------------------------------------------------------

import shutil as _shutil
import subprocess as _sp

import pytest as _pytest

_HAVE_SH = bool(_shutil.which("bash") and _shutil.which("git"))
needs_shell = _pytest.mark.skipif(not _HAVE_SH, reason="needs bash and git")


def _bash_sees(path) -> bool:
    """Can the bash on PATH reach this filesystem at all?

    On a Windows host `bash` is frequently WSL's, which cannot see a
    ``C:/Users/...`` temp directory -- the same host/guest split that made the
    agy shim look broken. Skipping with that reason stated is honest; failing
    would blame the code for the harness.
    """
    return _sp.run(["bash", "-c", f'test -d "{path}"'],
                   capture_output=True).returncode == 0


@_pytest.fixture
def seeded_repo(tmp_path):
    if not _HAVE_SH or not _bash_sees(tmp_path.as_posix()):
        _pytest.skip("the bash on PATH cannot see this filesystem "
                     "(WSL bash on a Windows host); these run in WSL")
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: _sp.run(["git", "-C", str(repo), *a], check=True,
                             capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "a.txt").write_text("hi", encoding="utf-8")
    (repo / ".env").write_text("OMNIGRAPH_TOKEN=secret123\n", encoding="utf-8")
    run("add", "a.txt")
    run("commit", "-qm", "init")
    return repo


def _provision(repo, target, graph_id="demo-graph"):
    script = provision_script(repo.as_posix(), target.as_posix(),
                              "/nonexistent-skill", graph_id=graph_id)
    _sp.run(["bash", "-c", script], capture_output=True, text=True)
    return target


@needs_shell
def test_the_script_actually_creates_a_worktree(seeded_repo, tmp_path):
    wt = _provision(seeded_repo, tmp_path / "wt")
    assert (wt / "a.txt").read_text(encoding="utf-8") == "hi"


@needs_shell
def test_the_untracked_env_arrives(seeded_repo, tmp_path):
    """A worktree holds tracked files only; .env is not one of them."""
    wt = _provision(seeded_repo, tmp_path / "wt")
    assert "OMNIGRAPH_TOKEN=secret123" in (wt / ".env").read_text(encoding="utf-8")


@needs_shell
def test_the_graph_is_pinned_in_the_file_not_only_the_environment(seeded_repo, tmp_path):
    """Children a supervisor spawns inherit the worktree, not our environment."""
    wt = _provision(seeded_repo, tmp_path / "wt")
    assert "OMNIGRAPH_GRAPH_ID=demo-graph" in (wt / ".env").read_text(encoding="utf-8")


@needs_shell
def test_re_provisioning_neither_duplicates_nor_clobbers(seeded_repo, tmp_path):
    """Resume re-runs this; a second pin or a lost token would be silent."""
    wt = _provision(seeded_repo, tmp_path / "wt")
    _provision(seeded_repo, wt)
    text = (wt / ".env").read_text(encoding="utf-8")
    assert text.count("OMNIGRAPH_GRAPH_ID") == 1
    assert "secret123" in text


@needs_shell
def test_a_repo_without_an_env_still_gets_its_pin(seeded_repo, tmp_path):
    (seeded_repo / ".env").unlink()
    wt = _provision(seeded_repo, tmp_path / "wt")
    assert (wt / "a.txt").exists()
    assert "OMNIGRAPH_GRAPH_ID=demo-graph" in (wt / ".env").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Worktrees outlive the sessions that made them. Measured 2026-09-11: three
# survived four live runs and had to be found by hand. They are REPORTED, never
# removed -- a worktree holds an agent's uncommitted work, and "no session" can
# also mean the server was restarted.
# ---------------------------------------------------------------------------

PORCELAIN = (
    "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n"
    "worktree /home/u/cao-worktrees/demo-p1\nHEAD def\ndetached\n\n"
    "worktree /home/u/cao-worktrees/demo-p2\nHEAD 123\ndetached\n"
)


def test_worktree_paths_are_parsed():
    assert parse_worktrees(PORCELAIN) == [
        "/repo", "/home/u/cao-worktrees/demo-p1", "/home/u/cao-worktrees/demo-p2",
    ]


def test_the_main_checkout_is_never_an_orphan():
    """It has no phase, so it can never look abandoned."""
    assert phase_of("/repo", "demo") is None
    assert "/repo" not in orphans(parse_worktrees(PORCELAIN), [], "demo", "demo")


def test_a_phase_with_a_live_session_is_not_orphaned():
    found = orphans(parse_worktrees(PORCELAIN), ["cao-demo-p1"], "demo", "demo")
    assert found == ["/home/u/cao-worktrees/demo-p2"]


def test_a_rework_session_still_counts_as_live():
    """The session is named -r2 after a rework; the worktree is reused."""
    found = orphans(parse_worktrees(PORCELAIN), ["cao-demo-p1-r2"], "demo", "demo")
    assert "/home/u/cao-worktrees/demo-p1" not in found


def test_a_similarly_named_phase_does_not_shield_another():
    """cao-demo-p1x must not make p1 look live."""
    found = orphans(parse_worktrees(PORCELAIN), ["cao-demo-p1x"], "demo", "demo")
    assert "/home/u/cao-worktrees/demo-p1" in found


def test_another_projects_worktrees_are_left_alone():
    other = "worktree /home/u/cao-worktrees/otherrepo-p9\ndetached\n"
    assert orphans(parse_worktrees(other), [], "demo", "demo") == []


def test_an_unreadable_worktree_counts_as_dirty():
    """Unknown must never read as "safe to delete"."""
    assert is_dirty("/nonexistent-path-for-this-test") is True


@needs_shell
def test_a_clean_worktree_reports_clean(seeded_repo, tmp_path):
    wt = _provision(seeded_repo, tmp_path / "wt")
    (wt / ".env").unlink()  # hydration's own file is not the agent's work
    assert is_dirty(wt) is False


@needs_shell
def test_a_worktree_with_agent_work_reports_dirty(seeded_repo, tmp_path):
    wt = _provision(seeded_repo, tmp_path / "wt")
    (wt / "agent_output.txt").write_text("half-finished", encoding="utf-8")
    assert is_dirty(wt) is True
