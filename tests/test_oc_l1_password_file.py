"""Hermetic lane password source and reader-routing contracts."""
import ast
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import oc_l1


@pytest.mark.parametrize("suffix", ["", "\n", "\n\n"])
def test_file_wins(tmp_path, capsys, suffix):
    source = tmp_path / "password"
    source.write_text("placeholder-file" + suffix)
    source.chmod(0o600)
    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source),
                                 "TEST_PW": "placeholder-env"}, clear=True):
        assert oc_l1.read_lane_password("TEST_PW") == "placeholder-file" + suffix.removesuffix("\n")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["missing", "loose", "invalid", "directory"])
def test_bad_file_refuses_without_disclosure(tmp_path, capsys, kind):
    source = tmp_path / "password"
    if kind == "directory":
        source.mkdir()
    elif kind != "missing":
        source.write_bytes(b"\xff" if kind == "invalid" else b"placeholder-file")
        source.chmod(0o644 if kind == "loose" else 0o600)
    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source),
                                 "TEST_PW": "placeholder-env"}, clear=True):
        with pytest.raises(SystemExit) as exc:
            oc_l1.read_lane_password("TEST_PW")
    assert exc.value.code == 2
    assert capsys.readouterr() == ("", str(source) + "\n")
    assert exc.value.__context__ is None


def test_env_fallback(capsys):
    with patch.dict(os.environ, {"TEST_PW": "placeholder-env"}, clear=True):
        assert oc_l1.read_lane_password("TEST_PW") == "placeholder-env"
        assert oc_l1.read_lane_password("ABSENT") is None
    assert capsys.readouterr() == ("", "")


def test_path_is_forwarded_not_a_credential():
    assert not oc_l1.env_is_credential_name("AUTOOS_OCL1_PW_FILE")
    assert oc_l1.env_is_allowed("AUTOOS_OCL1_PW_FILE")
    tree = ast.parse((ROOT / "tools/autoos_agent_mcp.py").read_text())
    keys = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_OC_L1_ENV_KEYS" for t in n.targets))
    assert "AUTOOS_OCL1_PW_FILE" in [n.value for n in ast.walk(keys) if isinstance(n, ast.Constant)]


def test_every_reader_uses_helper():
    expected = {"oc_l1_recycle.py": 1, "oc_l1_serve.py": 2, "oc_l2.py": 7}
    for path in (ROOT / "tools").glob("*.py"):
        tree = ast.parse(path.read_text())
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            text = ast.unparse(node)
            if text.startswith("os.environ.get(") and path.name != "oc_l1_password.py":
                assert "password_env" not in text and "ENV_PW" not in text, (path.name, node.lineno)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "read_lane_password":
                count += 1
        if path.name in expected:
            assert count == expected[path.name], path.name


@pytest.mark.parametrize("returncode", [0, 1])
def test_windows_acl_is_checked(tmp_path, capsys, returncode):
    import subprocess
    from types import SimpleNamespace
    import oc_l1_password
    source = tmp_path / "password"
    source.write_text("placeholder-file")
    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source)}, clear=True):
        with patch.object(oc_l1_password.os, "name", "nt"), patch.object(
                subprocess, "run", return_value=SimpleNamespace(returncode=returncode)) as run:
            if returncode:
                with pytest.raises(SystemExit) as exc:
                    oc_l1.read_lane_password("TEST_PW")
                assert exc.value.code == 2
            else:
                assert oc_l1.read_lane_password("TEST_PW") == "placeholder-file"
    argv = run.call_args.args[0]
    assert "Get-Acl" in argv[-1] and "GetOwner" in argv[-1]
    assert "GetAccessRules" in argv[-1] and "IdentityReference -ne $o" in argv[-1]
    assert "placeholder-file" not in repr(run.call_args)
    assert capsys.readouterr() == ("", str(source) + "\n" if returncode else "")
