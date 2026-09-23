import sys
from pathlib import Path

# tests/cao/ -> tests/ -> unattended-orchestration/  (the package root for `cao`)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture(autouse=True)
def _isolated_secrets_scope(tmp_path, monkeypatch):
    """Keep the host's real secrets out of every test.

    ``find_secrets`` reads ``$AUTOOS_SECRETS`` and ``~/.config/autoos/api_keys.conf``
    before the repository walk-up, so a machine that already has a user-scope file
    would otherwise shadow every walk-up test's fixture.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("AUTOOS_SECRETS", raising=False)
