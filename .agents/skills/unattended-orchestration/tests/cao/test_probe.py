"""A provider being installed is not the same as a provider being usable.

Measured 2026-09-10: CAO's GET /health reported "claude":"ok" while the WSL
Claude Code install was not logged in, so every spawned Claude worker would have
died on its first turn with "Not logged in · Please run /login". Health checked
the binary, not the account.

These tests pin the distinction so it cannot regress into a silent all-Gemini
hierarchy.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cao.probe import (
    PANE_BEGIN,
    PROBE_TOKEN,
    classify,
    closes_pool,
    probe_argv,
    probe_command,
)


def test_a_reply_containing_the_token_is_ok():
    assert classify(f"...{PROBE_TOKEN}...", "", 0) == "ok"


def test_the_exact_claude_not_logged_in_message_is_detected():
    """Verbatim stdout measured from `claude -p` in WSL."""
    assert classify("Not logged in · Please run /login\n", "", 1) == "not_authenticated"


def test_other_auth_phrasings_are_detected():
    for text in (
        "Invalid API key provided",
        "401 Unauthorized",
        "Please run `claude login` first",
        "authentication failed",
    ):
        assert classify(text, "", 1) == "not_authenticated", text


def test_quota_messages_are_classified_separately_from_auth():
    assert classify("429 rate limit exceeded", "", 1) == "quota"
    assert classify("RESOURCE_EXHAUSTED", "", 1) == "quota"


def test_auth_wins_over_quota_when_both_appear():
    """Auth is actionable by the user; a cooldown on an unauthenticated pool is a lie."""
    assert classify("401 Unauthorized (rate limit)", "", 1) == "not_authenticated"


def test_missing_binary_is_its_own_state():
    assert classify("", "bash: agy: command not found", 127) == "not_installed"


def test_an_unrecognised_failure_is_not_silently_ok():
    assert classify("something went sideways", "", 1) == "failed"


def test_a_zero_exit_without_the_token_is_not_ok():
    """A CLI that prints a banner and exits 0 has not answered the probe."""
    assert classify("welcome to the tool!", "", 0) == "failed"


def test_probe_argv_uses_a_login_shell():
    """`bash -c` cannot see ~/.local/bin; that is the original %PATH% bug."""
    assert probe_argv("anthropic")[:2] == ["bash", "-lc"]


def test_probe_argv_wraps_in_wsl_only_when_a_distro_is_given():
    """On a Linux server CAO and its agents are native; there is no wsl to call."""
    assert probe_argv("anthropic", wsl_distro="Ubuntu")[:3] == ["wsl", "-d", "Ubuntu"]
    assert "wsl" not in probe_argv("anthropic")


def test_probe_argv_passes_the_command_as_one_element():
    """Quoting the command into the wrapper is what caused an unexpected EOF."""
    argv = probe_argv("deepseek", wsl_distro="Ubuntu")
    assert argv[:6] == ["wsl", "-d", "Ubuntu", "-e", "bash", "-lc"]
    assert len(argv) == 7
    assert argv[-1] == probe_command("deepseek")


def test_probe_command_names_the_right_binary_per_pool():
    assert "claude" in probe_command("anthropic")
    assert "agy" in probe_command("antigravity")
    assert "opencode" in probe_command("deepseek")


def test_probe_command_asks_for_a_cheap_model():
    """A liveness probe must never burn a premium model -- but cheapest is only
    right if it answers: measured 2026-09-11, gemini-3.8-flash-low returned
    nothing in 120s across three runs while -high answered in ~22s."""
    assert "--model gemini-3.8-flash" in probe_command("antigravity")
    assert "-pro-" not in probe_command("antigravity")  # never a premium tier
    assert "haiku" in probe_command("anthropic")


def test_deepseek_probe_loads_the_key_without_embedding_it():
    cmd = probe_command("deepseek")
    assert "api_keys.conf" in cmd
    assert "sk-" not in cmd


# ---------------------------------------------------------------------------
# A probe answers two different questions and they must not be conflated:
# "is this pool unusable" and "could the harness ask". Measured 2026-09-11:
# `agy -p '...' --model gemini-3.8-flash-high` answers PROBE_OK with exit 0
# inside tmux, and "error: interrupted" when stdout is a pipe -- which is what
# the probe always does. The pool was healthy, answering, and reported dead.
# ---------------------------------------------------------------------------


def test_no_terminal_is_inconclusive_not_failed():
    assert classify("error: interrupted", "", 0) == "inconclusive"


def test_other_no_tty_wordings_are_caught():
    for text in ("stdin is not a terminal", "inappropriate ioctl for device",
                 "not a tty"):
        assert classify(text, "", 1) == "inconclusive", text


def test_only_proof_closes_a_pool():
    assert closes_pool("not_installed") is True
    assert closes_pool("not_authenticated") is True


def test_nothing_else_closes_a_pool():
    for verdict in ("inconclusive", "failed", "quota", "ok"):
        assert closes_pool(verdict) is False, verdict


def test_quota_does_not_close_because_cooling_is_the_budget_ledgers_job():
    """A quota wall expires; closure here has no expiry and would outlive it."""
    assert closes_pool("quota") is False


def test_a_signed_out_provider_is_still_proof():
    assert classify("agy: not signed in", "", 1) == "not_authenticated"


def test_the_pane_probe_slices_the_echo_out():
    """A capture holds the echoed command AND the answer. Matching the token
    anywhere in it made `PROBE_TOKEN in stdout` true however the model behaved
    -- a probe that could only ever say ok. Measured live 2026-09-11: a
    genuinely silent agy was reported `ok` because the pane quoted the
    question back. The sentinel is printed AFTER the echo, so the slice after
    its last occurrence is the CLI's own output."""
    command = probe_command("antigravity")
    assert PANE_BEGIN in command
    assert "awk" in command and f"/{PANE_BEGIN}/" in command


def test_the_slice_starts_after_the_last_sentinel():
    """The echoed command line carries the sentinel too; the executed echo
    comes later, so only the LAST occurrence bounds the answer."""
    command = probe_command("antigravity")
    assert "{b=NR}" in command and "i=b+1" in command


def test_the_probe_model_is_one_that_answers():
    """`-low` returned nothing in 120s across three runs; `-high` answered in
    ~22s. The cheapest model is only the right probe if it replies."""
    assert "gemini-3.8-flash-high" in probe_command("antigravity")


def _posix_bash():
    """A bash that sees this host's paths, or None.

    On Windows a bare ``bash`` (or ``shutil.which`` with System32 first on PATH)
    is WSL's launcher, which cannot open ``C:/...`` paths or use a Windows HOME:
    measured, the lookup then returned ``''``. Prefer Git's own bash, found from
    ``git --exec-path``; else the first PATH bash outside System32/WindowsApps.
    """
    if os.name != "nt":
        return shutil.which("bash")
    try:
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True,
                                   text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        exec_path = ""
    if exec_path:
        git_root = Path(exec_path).parents[2]  # <git>/mingw64/libexec/git-core
        for candidate in (git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return str(candidate)
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        low = folder.lower()
        if "system32" in low or "windowsapps" in low:
            continue
        candidate = Path(folder) / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


@pytest.mark.skipif(_posix_bash() is None,
                    reason="needs a bash that sees host paths (Git bash on Windows), not WSL's")
def test_probe_secrets_lookup_order_runs_in_the_shell(tmp_path):
    """The probe resolves the key where it reads it: env, user scope, then the repo."""
    from cao.probe import secrets_lookup

    home = tmp_path / "home"; (home / ".config" / "autoos").mkdir(parents=True)
    repo = tmp_path / "repo"; (repo / "secrets").mkdir(parents=True)
    (repo / "secrets" / "api_keys.conf").write_text("deepseek=repo\n")

    def which(env_extra):
        env = {k: v for k, v in os.environ.items() if k != "AUTOOS_SECRETS"}
        env.update(HOME=home.as_posix(), **env_extra)
        # From a file, not `bash -c`: Windows argv quoting mangles the embedded
        # quotes for Git's bash. Builtins only: that bash may have no grep on PATH.
        script = tmp_path / "lookup.sh"
        script.write_text(secrets_lookup(repo.as_posix()) + 'read -r l < "$s"; echo "${l#deepseek=}"\n',
                          newline="\n")
        done = subprocess.run(
            [_posix_bash(), script.as_posix()],
            capture_output=True, text=True, env=env, timeout=30,
        )
        return done.stdout.strip(), done.stderr

    key, err = which({})
    assert key == "repo" and "notice: using legacy secrets file" in err
    (home / ".config" / "autoos" / "api_keys.conf").write_text("deepseek=user\n")
    key, err = which({})
    assert key == "user" and "notice" not in err
    explicit = tmp_path / "x.conf"; explicit.write_text("deepseek=env\n")
    assert which({"AUTOOS_SECRETS": explicit.as_posix()})[0] == "env"


@pytest.mark.skipif(_posix_bash() is None,
                    reason="needs a bash that sees host paths (Git bash on Windows), not WSL's")
def test_probe_explicit_missing_autoos_secrets_fails_loudly(tmp_path):
    """The shell lookup exits 2 on an explicit AUTOOS_SECRETS that is not a file (D4).

    Before the fix it fell through to the legacy repo file (L1's Sonnet review, 2026-09-18).
    """
    from cao.probe import secrets_lookup

    home = tmp_path / "home"; (home / ".config" / "autoos").mkdir(parents=True)
    repo = tmp_path / "repo"; (repo / "secrets").mkdir(parents=True)
    (repo / "secrets" / "api_keys.conf").write_text("deepseek=repo\n")
    env = {k: v for k, v in os.environ.items() if k != "AUTOOS_SECRETS"}
    env.update(HOME=home.as_posix(), AUTOOS_SECRETS=(tmp_path / "nope.conf").as_posix())
    script = tmp_path / "lookup.sh"
    script.write_text(secrets_lookup(repo.as_posix()) + 'echo "reached:$s"\n', newline="\n")
    done = subprocess.run([_posix_bash(), script.as_posix()],
                          capture_output=True, text=True, env=env, timeout=30)
    assert done.returncode == 2
    assert "secrets file not found" in done.stderr and "AUTOOS_SECRETS" in done.stderr
    assert "reached:" not in done.stdout
