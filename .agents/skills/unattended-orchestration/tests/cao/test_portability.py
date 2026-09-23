"""The skill must survive being copied into another repository.

`unattended-orchestration` is portable by design — you drop the folder into any
git repo and run it. That promise is easy to break silently: a hard-coded path to
this repo, an import of something that only exists here, or a test module that
errors instead of skipping and takes the whole suite down with it.

These tests copy the skill into a throwaway repo and run it there.
"""

import getpass
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]

#: A real user home: a drive-letter or WSL ``Users`` folder, or ``/home/<name>/``.
#: A name starts with a word character, so placeholders such as ``C:\Users\<you>``
#: and ``C:\Users\...`` do not match; ``you`` and ``Public`` are allowed by name.
USER_HOME = re.compile(
    r"[A-Z]:[\\/]Users[\\/](?!(?:Public|you)\b)\w[\w.-]*"
    r"|/mnt/[a-z]/Users/\w[\w.-]*"
    r"|/home/\w[\w.-]*/",
    re.I,
)

#: Anything naming this machine, this user, or this repository. A copied skill
#: carrying one of these works here and nowhere else.
FOREIGN = re.compile(USER_HOME.pattern + r"|agent-skills", re.I)

SOURCE_SUFFIXES = {".py", ".ps1", ".psm1", ".md", ".json"}


def _sources():
    for path in SKILL.rglob("*"):
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        if path.suffix in SOURCE_SUFFIXES and path.is_file():
            yield path


def test_no_runtime_module_hardcodes_this_machine():
    """Docs may cite measurements from this host; code may not depend on it."""
    offenders = []
    for path in (SKILL / "cao").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith(('"', "'")):
                continue  # comments and docstrings cite measurements on purpose
            if FOREIGN.search(line):
                offenders.append(f"{path.name}:{number}: {stripped[:70]}")
    assert not offenders, "runtime code names this machine:\n" + "\n".join(offenders)


def test_the_example_config_carries_no_absolute_local_path():
    text = (SKILL / "handoff.config.example.json").read_text(encoding="utf-8")
    home = USER_HOME.search(text)
    assert home is None, home.group(0)
    user = getpass.getuser().lower()
    if len(user) >= 4 and user not in {"user", "runner", "root", "admin"}:
        assert user not in text.lower()


@pytest.fixture(scope="module")
def foreign_repo(tmp_path_factory):
    """A throwaway git repo with only the skill copied into it."""
    root = tmp_path_factory.mktemp("foreign") / "someone-elses-repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    shutil.copytree(
        SKILL,
        root / "unattended-orchestration",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    return root


def _run(repo, *args, env_path=None):
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(env_path or (repo / "unattended-orchestration"))
    return subprocess.run(
        [sys.executable, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def test_the_suite_runs_in_a_foreign_repo(foreign_repo):
    """Regression: test_setup.py imported infra/, which a foreign repo lacks,
    and ERRORED rather than skipping — taking every other test down with it."""
    done = _run(
        foreign_repo,
        "-m",
        "pytest",
        str(foreign_repo / "unattended-orchestration/tests/cao"),
        "-q",
        # Excluding THIS module is not a convenience: without it the nested run
        # re-runs the portability suite, which copies the skill and runs it
        # again, forever. Measured as a 600s timeout.
        "--ignore=" + str(foreign_repo / "unattended-orchestration/tests/cao/test_portability.py"),
        "-p", "no:cacheprovider",
    )
    assert "error" not in done.stdout.lower().split("passed")[0], done.stdout[-1500:]
    assert " passed" in done.stdout, done.stdout[-1500:]


def test_check_refuses_clearly_without_a_config(foreign_repo):
    done = _run(foreign_repo, "-m", "cao", "--repo", ".", "check")
    assert done.returncode == 2
    assert "handoff.config.json" in done.stderr
    assert "Traceback" not in done.stderr


def test_launch_refuses_without_a_plan_in_a_foreign_repo(foreign_repo):
    """The gate must hold in someone else's repo, not just ours."""
    shutil.copy(
        SKILL / "handoff.config.example.json", foreign_repo / "handoff.config.json"
    )
    done = _run(foreign_repo, "-m", "cao", "--repo", ".", "launch", "--phase", "p1",
                "--dry-run")
    assert done.returncode == 3
    assert "no plan contract" in done.stderr


def test_the_scaffold_still_does_not_validate_in_a_foreign_repo(foreign_repo):
    _run(foreign_repo, "-m", "cao", "--repo", ".", "plan", "--force")
    done = _run(foreign_repo, "-m", "cao", "--repo", ".", "launch", "--phase", "p1",
                "--dry-run")
    assert done.returncode == 3
    assert "placeholder" in done.stderr


# --------------------------------------------------------------------------
# Repo hygiene. A tool expecting POSIX that receives a Windows path creates it
# as a RELATIVE directory in the checkout; 1926 such files were staged by a
# `git add -A` before being caught.
# --------------------------------------------------------------------------


def test_no_windows_shaped_path_directory_is_tracked():
    r"""A tracked 'C:\Users\...' directory means a path leaked into the repo."""
    repo = SKILL.parents[1]
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    offenders = [
        line for line in listing.splitlines()
        if re.match(r'^"?[A-Za-z][:\uf03a]', line)
    ]
    assert not offenders, "windows-path directories are tracked:\n" + "\n".join(offenders[:5])


def test_the_plugin_block_travels_with_the_skill():
    """Measured 2026-09-11: libtmux's pytest plugin (a CAO dependency) crashes
    pytest 9.1.1 at startup, so the suite cannot even collect. `pytest.ini` beside
    the skill blocks it. Moved to the repo root it would still fix THIS repo and
    silently stop fixing a copied skill -- which is the only place portability
    is claimed."""
    ini = SKILL / "pytest.ini"
    assert ini.is_file(), "pytest.ini must live in the skill, not above it"
    assert "no:libtmux" in ini.read_text(encoding="utf-8")


def test_the_foreign_copy_carries_it(foreign_repo):
    assert (foreign_repo / "unattended-orchestration" / "pytest.ini").is_file()


def test_run_state_is_not_tracked():
    """`--state-dir` defaults to output/cao, inside the repo. Its NDJSON holds
    verdicts, quota records and captured agent output; a `git add -A` after a
    run would commit all of it. Measured after this lane's first live run."""
    repo = SKILL.parents[1]
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "output/cao", ".cao-leases"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert not tracked, f"run state is tracked:\n{tracked}"


def test_run_state_is_actually_ignored():
    """Untracked is not enough -- it has to be ignored, or the next add -A
    stages it. `git check-ignore` answers with the rule that matched."""
    repo = SKILL.parents[1]
    for path in ("output/cao/verify.ndjson", ".cao-leases/p1.json"):
        done = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", path],
            capture_output=True,
        )
        assert done.returncode == 0, f"{path} is not gitignored"


def test_no_tracked_blob_is_stored_with_crlf():
    r"""This repo declares `* text=auto`, so blobs belong in the index as LF.

    A CRLF blob checks out unchanged on Linux and CRLF-on-CRLF on Windows, and
    the two disagree after git's clean filter. Measured 2026-09-11:
    Ledger.Tests.ps1 was stored CRLF, the Windows working copy had grown a
    stray CR per line (\r\r\n) which normalised back to the blob and so read as
    CLEAN, while every CAO worktree checked out the honest bytes and showed a
    474-line phantom diff. An agent seeing that is one `git commit -a` away from
    rewriting a file it never touched.
    """
    repo = SKILL.parents[1]
    names = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                           capture_output=True).stdout.split(b"\0")
    text_suffixes = (".ps1", ".psm1", ".py", ".md", ".json", ".sh", ".yml", ".yaml")
    offenders = []
    for raw in names:
        if not raw:
            continue
        name = raw.decode("utf-8", "replace")
        if not name.endswith(text_suffixes):
            continue
        blob = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{name}"],
                              capture_output=True).stdout
        if b"\r\n" in blob:
            offenders.append(name)
    assert not offenders, (
        "blobs stored with CRLF (fix: git add --renormalize <file>):\n"
        + "\n".join(offenders[:10])
    )
