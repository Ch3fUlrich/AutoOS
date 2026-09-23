import json

import pytest

from cao.config import load_config, load_secrets, pool_available


def test_load_secrets_parses_key_value_ignoring_comments(tmp_path):
    f = tmp_path / "api_keys.conf"
    f.write_text("# comment\n\ndeepseek=sk-abc123\n\n", encoding="utf-8")
    assert load_secrets(f) == {"deepseek": "sk-abc123"}


def test_load_secrets_missing_file_is_empty_not_error(tmp_path):
    assert load_secrets(tmp_path / "nope.conf") == {}


def test_load_secrets_keeps_value_verbatim_including_equals(tmp_path):
    f = tmp_path / "api_keys.conf"
    f.write_text("deepseek=sk-a=b=c\n", encoding="utf-8")
    assert load_secrets(f) == {"deepseek": "sk-a=b=c"}


def _write_cfg(tmp_path, cao_block):
    (tmp_path / "handoff.config.json").write_text(
        json.dumps({"baseBranch": "main", "cao": cao_block}), encoding="utf-8"
    )
    return tmp_path


FULL = {
    "maxDepth": 3,
    "server": "http://localhost:9889",
    "worktreeRoot": "$HOME/cao-worktrees",
    "levels": [{"level": 1, "role": "orchestrator", "canSpawn": True}],
    "pools": {
        "anthropic": {"via": "claude"},
        "deepseek": {"via": "opencode", "apiKeyEnv": "DEEPSEEK_API_KEY"},
    },
    "routing": {},
    "reviewPairing": "cross-family",
    "resume": {"enabled": True, "leaseMinutes": 30, "maxAttempts": 3},
}


def test_load_config_reads_cao_block(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, FULL))
    assert cfg.max_depth == 3
    assert cfg.resume["maxAttempts"] == 3
    assert cfg.review_pairing == "cross-family"


def test_load_config_missing_cao_block_raises_with_actionable_message(tmp_path):
    (tmp_path / "handoff.config.json").write_text(
        json.dumps({"baseBranch": "main"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no 'cao' block"):
        load_config(tmp_path)


def test_repr_never_leaks_pool_contents(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, FULL))
    assert "DEEPSEEK_API_KEY" not in repr(cfg)


def test_pool_unavailable_when_api_key_absent(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, FULL))
    assert pool_available(cfg, "deepseek", secrets={}) is False
    assert pool_available(cfg, "deepseek", secrets={"deepseek": "sk-x"}) is True


def test_pool_without_apikeyenv_is_available(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, FULL))
    assert pool_available(cfg, "anthropic", secrets={}) is True


def test_unknown_pool_is_not_available(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, FULL))
    assert pool_available(cfg, "nonesuch", secrets={}) is False


def test_find_secrets_walks_up_to_the_repo_root(tmp_path):
    """A false 'NO CREDENTIALS' makes a working pool look dead."""
    from cao.config import find_secrets

    (tmp_path / ".git").mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "api_keys.conf").write_text("deepseek=sk-x\n", encoding="utf-8")
    nested = tmp_path / "skills" / "unattended-orchestration"
    nested.mkdir(parents=True)
    assert find_secrets(nested) == tmp_path / "secrets" / "api_keys.conf"


def test_find_secrets_prefers_the_nearest_file(tmp_path):
    from cao.config import find_secrets

    (tmp_path / ".git").mkdir()
    for d in (tmp_path, tmp_path / "sub"):
        (d / "secrets").mkdir(parents=True, exist_ok=True)
        (d / "secrets" / "api_keys.conf").write_text("deepseek=sk-x\n", encoding="utf-8")
    assert find_secrets(tmp_path / "sub") == tmp_path / "sub" / "secrets" / "api_keys.conf"


def test_find_secrets_does_not_escape_the_repository(tmp_path):
    """Reaching outside the repo would pick up another project's keys."""
    from cao.config import find_secrets

    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "api_keys.conf").write_text("deepseek=sk-outside\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert find_secrets(repo) == repo / "secrets" / "api_keys.conf"
    assert not find_secrets(repo).is_file()


def test_find_secrets_returns_a_path_even_when_absent(tmp_path):
    from cao.config import find_secrets

    (tmp_path / ".git").mkdir()
    assert find_secrets(tmp_path).name == "api_keys.conf"


def test_env_then_user_scope_then_repo(tmp_path, monkeypatch):
    from cao.config import find_secrets
    home = tmp_path / "home"; (home / ".config" / "autoos").mkdir(parents=True)
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True); (repo / "secrets").mkdir()
    (repo / "secrets" / "api_keys.conf").write_text("deepseek=repo\n")
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AUTOOS_SECRETS", raising=False)
    assert find_secrets(repo) == repo / "secrets" / "api_keys.conf"          # legacy only
    user = home / ".config" / "autoos" / "api_keys.conf"; user.write_text("deepseek=user\n")
    assert find_secrets(repo) == user                                         # user scope wins
    explicit = tmp_path / "x.conf"; explicit.write_text("deepseek=env\n")
    monkeypatch.setenv("AUTOOS_SECRETS", str(explicit))
    assert find_secrets(repo) == explicit                                     # env wins


def test_legacy_secrets_print_a_notice_and_the_user_scope_does_not(tmp_path, monkeypatch, capsys):
    from cao.config import find_secrets
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True); (repo / "secrets").mkdir()
    (repo / "secrets" / "api_keys.conf").write_text("deepseek=repo\n")
    find_secrets(repo)
    assert "notice: using legacy secrets file" in capsys.readouterr().err
    user = tmp_path / ".config" / "autoos"; user.mkdir(parents=True)
    (user / "api_keys.conf").write_text("deepseek=user\n")
    find_secrets(repo)
    assert capsys.readouterr().err == ""


def test_explicit_missing_autoos_secrets_fails_loudly(tmp_path, monkeypatch):
    """An explicit AUTOOS_SECRETS that is not a file must raise, never fall through (D4).

    Found by L1's Sonnet review of S13 (2026-09-18): the lookup silently returned the legacy
    repo file and labelled it with the legacy notice.
    """
    from cao.config import find_secrets
    home = tmp_path / "home"; (home / ".config" / "autoos").mkdir(parents=True)
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True); (repo / "secrets").mkdir()
    (repo / "secrets" / "api_keys.conf").write_text("deepseek=repo\n")
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home))
    missing = tmp_path / "nope.conf"
    monkeypatch.setenv("AUTOOS_SECRETS", str(missing))
    with pytest.raises(FileNotFoundError, match="AUTOOS_SECRETS"):
        find_secrets(repo)
