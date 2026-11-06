"""Hermetic lane password source and reader-routing contracts."""
import ast
import os
import sys
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import oc_l1


def test_file_wins(tmp_path, capsys, suffix):
    source = tmp_path / "password"
    source.write_text("placeholder-file" + suffix)
    source.chmod(0o600)
    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source),
                                 "TEST_PW": "placeholder-env"}, clear=True):
        assert oc_l1.read_lane_password("TEST_PW") == "placeholder-file" + suffix.removesuffix("\n")
    assert capsys.readouterr() == ("", "")


def test_bad_file_refuses_without_disclosure(tmp_path, capsys, kind):
    source = tmp_path / "password"
    if kind == "directory":
        source.mkdir()
    elif kind != "missing":
        source.write_bytes(b"\xff" if kind == "invalid" else b"placeholder-file")
        source.chmod(0o644 if kind == "loose" else 0o600)
    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source),
                                 "TEST_PW": "placeholder-env"}, clear=True):
        with unittest.TestCase().assertRaises(SystemExit) as exc:
            oc_l1.read_lane_password("TEST_PW")
    assert exc.exception.code == 2
    assert capsys.readouterr() == ("", str(source) + "\n")
    assert exc.exception.__context__ is None


def test_env_fallback(capsys):
    with patch.dict(os.environ, {"TEST_PW": "placeholder-env"}, clear=True):
        assert oc_l1.read_lane_password("TEST_PW") == "placeholder-env"
        assert oc_l1.read_lane_password("ABSENT") is None
    assert capsys.readouterr() == ("", "")


def test_path_is_forwarded_not_a_credential():
    import oc_l2
    assert oc_l2.oc_l1 is oc_l1
    start = ast.unparse(ast.parse((ROOT / "tools/oc_l2.py").read_text()))
    assert "run_oc_l1('start', name, cfg)" in start
    with patch.object(oc_l1, "main", return_value=0) as main:
        assert oc_l2.run_oc_l1("start", "placeholder-lane", "placeholder-config")[0] == 0
    main.assert_called_once_with(["start", "--name", "placeholder-lane", "--config", "placeholder-config"])
    import oc_l1_serve
    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": "placeholder-path",
                                 "AUTOOS_OCL1_PW": "placeholder-secret"}, clear=True):
        env = oc_l1_serve._child_env({"scratch_dir": "scratch",
                                     "password_env": "AUTOOS_OCL1_PW"}, "placeholder-config", "placeholder-secret")
    assert env["AUTOOS_OCL1_PW_FILE"] == "placeholder-path"
    assert "AUTOOS_OCL1_PW" not in env
    assert not oc_l1.env_is_credential_name("AUTOOS_OCL1_PW_FILE")
    assert oc_l1.env_is_allowed("AUTOOS_OCL1_PW_FILE")
    tree = ast.parse((ROOT / "tools/autoos_agent_mcp.py").read_text())
    keys = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_OC_L1_ENV_KEYS" for t in n.targets))
    assert "AUTOOS_OCL1_PW_FILE" in [n.value for n in ast.walk(keys) if isinstance(n, ast.Constant)]
    l2_keys = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "_OC_L2_ENV_KEYS" for t in n.targets))
    assert isinstance(l2_keys, ast.BinOp) and l2_keys.left.id == "_OC_L1_ENV_KEYS"


def tracked_paths():
    # Spawner tests leave ignored sandbox clones: they are not repository source.
    return [ROOT / os.fsdecode(p) for p in subprocess.check_output(
        ['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0') if p]


def configured_password_names():
    names = {"AUTOOS_OCL1_PW"}

    def visit(value):
        if isinstance(value, dict):
            name = value.get("password_env")
            if isinstance(name, str):
                names.add(name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in tracked_paths():
        if path.suffix == '.json':
            text = path.read_text()
            if '"password_env"' in text:
                visit(json.loads(text))
    return names


PASSWORD_NAMES = configured_password_names()


def password_readers(tree):
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
            key = node.slice
        elif isinstance(node, ast.Call) and ast.unparse(node.func) in (
                "os.environ.get", "os.getenv") and node.args:
            key = node.args[0]
        if key is not None and (any((isinstance(n, ast.Name) and (
                "password_env" in n.id or "ENV_PW" in n.id)) or (
                isinstance(n, ast.Attribute) and n.attr == "password_env") or (
                isinstance(n, ast.Constant) and isinstance(n.value, str) and
                n.value in PASSWORD_NAMES | {"password_env", "ENV_PW"}) for n in ast.walk(key))):
            yield node.lineno


def test_scan_rejects_planted_reader(reader):
    assert list(password_readers(ast.parse(reader))) == [1]


def test_every_reader_uses_helper():
    expected = {"oc_l1_recycle.py": 1, "oc_l1_serve.py": 2, "oc_l2.py": 7}
    for path in tracked_paths():
        if path.suffix != '.py':
            continue
        tree = ast.parse(path.read_text())
        if path != ROOT / "tools/oc_l1_password.py":
            assert not list(password_readers(tree)), str(path.relative_to(ROOT))
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "read_lane_password":
                count += 1
        if path.name in expected:
            assert count == expected[path.name], path.name


def test_windows_acl_is_checked(tmp_path, capsys, returncode):
    import subprocess
    from types import SimpleNamespace
    import oc_l1_password
    source = tmp_path / "password"
    source.write_text("placeholder-file")
    real_open = open

    def open_then_change_env(*args, **kwargs):
        opened = real_open(*args, **kwargs)
        os.environ["AUTOOS_OCL1_PW_FILE"] = "placeholder-other-path"
        return opened

    with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source)}, clear=True):
        with patch("builtins.open", side_effect=open_then_change_env), patch.object(oc_l1_password.os, "name", "nt"), patch.object(
                subprocess, "run", return_value=SimpleNamespace(returncode=returncode)) as run:
            if returncode:
                with unittest.TestCase().assertRaises(SystemExit) as exc:
                    oc_l1.read_lane_password("TEST_PW")
                assert exc.exception.code == 2
            else:
                assert oc_l1.read_lane_password("TEST_PW") == "placeholder-file"
    argv = run.call_args.args[0]
    assert argv[-1] == str(source)
    script = argv[-2]
    assert "Get-Acl -LiteralPath $args[0]" in script and "GetOwner" in script
    assert "$env:AUTOOS_OCL1_PW_FILE" not in script
    assert "GetAccessRules" in script and "IdentityReference -ne $o" in script
    assert "placeholder-file" not in repr(run.call_args)
    assert capsys.readouterr() == ("", str(source) + "\n" if returncode else "")


class PasswordFileTests(unittest.TestCase):
    def captured(self, test, *args):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            class Capture:
                def readouterr(self):
                    return out.getvalue(), err.getvalue()
            test(Path(tmp), Capture(), *args)

    def test_file_wins(self):
        for suffix in ("", "\n", "\n\n"):
            with self.subTest(suffix=suffix):
                self.captured(test_file_wins, suffix)

    def test_bad_file(self):
        for kind in ("missing", "loose", "invalid", "directory"):
            with self.subTest(kind=kind):
                self.captured(test_bad_file_refuses_without_disclosure, kind)

    def test_env_fallback(self):
        self.captured(lambda tmp, cap: test_env_fallback(cap))

    def test_path_is_forwarded_not_a_credential(self):
        test_path_is_forwarded_not_a_credential()

    def test_every_reader_uses_helper(self):
        test_every_reader_uses_helper()

    def test_scan_rejects_planted_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'lane.json'
            config.write_text('{"lane": {"password_env": "CUSTOM_LANE_AUTH"}}')
            with patch(__name__ + '.tracked_paths', return_value=[config]):
                names = configured_password_names()
            with patch(__name__ + '.PASSWORD_NAMES', names):
                for read in ('os.environ[KEY]', 'os.environ.get(KEY)', 'os.getenv(KEY)'):
                    test_scan_rejects_planted_reader(read.replace('KEY', '"CUSTOM_LANE_AUTH"'))
        assert not list(password_readers(ast.parse('os.getenv("AUTOOS_OCL1_PW_FILE")')))
        for read in ('os.environ[KEY]', 'os.environ.get(KEY)', 'os.getenv(KEY)'):
            for key in ('lane["password_env"]', 'mapping.get("password_env")',
                        'mapping.get("ENV_PW")', 'mapping.get("password_env", ENV_PW)',
                        '"AUTOOS_OCL1_PLACEHOLDER_PW"'):
                with self.subTest(read=read, key=key):
                    test_scan_rejects_planted_reader(read.replace('KEY', key))
        for reader in ('os.environ["AUTOOS_OCL1_PW"]', 'os.getenv("AUTOOS_OCL1_PW")',
                       'os.environ.get("AUTOOS_OCL1_PW")', 'os.environ[password_env]',
                       'os.getenv(ENV_PW)', 'os.environ.get(lane.password_env)'):
            with self.subTest(reader=reader):
                test_scan_rejects_planted_reader(reader)

    def test_windows_acl_is_checked(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                self.captured(test_windows_acl_is_checked, returncode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
