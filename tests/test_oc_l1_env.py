"""tests/test_oc_l1_env.py - AO-OC-RECYCLE-FOLLOWUPS: the Windows system names a lane child needs.

A child without SystemDrive resolved `%SystemDrive%` literally and created a stray
`<cwd>/%SystemDrive%/ProgramData/...` tree. The name list is one source of truth
(tools/oc_l1_env.py); `oc_l1_serve._child_env` forwards it on Windows, matching
case-insensitively (os.environ upper-cases names there). Hermetic: os.environ is
replaced per case and `_needs_systemroot` is mocked, as in test_oc_l1_serve."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_env  # noqa: E402
import oc_l1_serve  # noqa: E402
from _oc_l1_fakes import PW_VALUE, make_lane  # noqa: E402

WIN = {"SYSTEMROOT": "C:/Windows", "SYSTEMDRIVE": "C:", "PROGRAMDATA": "C:/ProgramData",
       "PROGRAMFILES": "C:/Program Files", "PROGRAMFILES(X86)": "C:/Program Files (x86)",
       "COMMONPROGRAMFILES": "C:/Program Files/Common Files"}


class NameListTest(unittest.TestCase):
    def test_systemdrive_and_the_program_dirs_pass_the_allowlist(self):
        for name in ("SystemDrive", "ProgramData", "ProgramFiles", "ProgramFiles(x86)",
                     "CommonProgramFiles", "SystemRoot"):
            self.assertTrue(oc_l1.env_is_allowed(name), name)

    def test_a_credential_shaped_name_still_does_not(self):
        for name in ("GITHUB_TOKEN", "SystemDrive_TOKEN", "ProgramData_SECRET", "PROGRAMFILES_KEY",
                     "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD"):
            self.assertFalse(oc_l1.env_is_allowed(name), name)

    def test_no_name_in_the_list_is_credential_shaped(self):
        for name in oc_l1_env.LANE_ENV_WINDOWS:
            self.assertFalse(oc_l1.env_is_credential_name(name), name)

    def test_one_source_of_truth(self):
        self.assertIs(oc_l1.LANE_ENV_ALLOW, oc_l1_env.LANE_ENV_ALLOW)
        self.assertIs(oc_l1.LANE_ENV_WINDOWS, oc_l1_env.LANE_ENV_WINDOWS)
        self.assertTrue(set(oc_l1_env.LANE_ENV_WINDOWS) <= set(oc_l1_env.LANE_ENV_ALLOW))


class ChildEnvTest(unittest.TestCase):
    def _env(self, parent, windows, **lane_over):
        with tempfile.TemporaryDirectory(prefix="oc_l1_envnames_") as td:
            lane = make_lane(Path(td), 47256)
            lane.update(lane_over)
            with mock.patch.dict(os.environ, parent, clear=True), \
                    mock.patch.object(oc_l1_serve, "_needs_systemroot", return_value=windows):
                return oc_l1_serve._child_env(lane, Path(td) / "opencode.json", PW_VALUE)

    def test_windows_names_reach_the_child_in_canonical_spelling(self):
        env = self._env(dict(WIN), True)
        for name in oc_l1_env.LANE_ENV_WINDOWS:
            self.assertEqual(env.get(name), WIN[name.upper()], name)
        self.assertNotIn("SYSTEMDRIVE", env)

    def test_a_name_the_launcher_lacks_is_not_invented(self):
        env = self._env({"SYSTEMROOT": "C:/Windows"}, True)
        self.assertIn("SystemRoot", env)
        for name in ("SystemDrive", "ProgramData", "ProgramFiles", "ProgramFiles(x86)"):
            self.assertNotIn(name, env)

    def test_an_empty_value_is_not_forwarded(self):
        self.assertNotIn("SystemDrive", self._env({"SYSTEMDRIVE": ""}, True))

    def test_not_forwarded_off_windows(self):
        env = self._env(dict(WIN), False)
        self.assertEqual([n for n in env if n.upper() in WIN], [])

    def test_a_credential_beside_them_stays_behind(self):
        env = self._env(dict(WIN, GITHUB_TOKEN="ghp_x", SYSTEMDRIVE_SECRET="s"), True)
        self.assertIn("SystemDrive", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("SYSTEMDRIVE_SECRET", env)

    def test_a_lane_that_declares_the_name_keeps_its_own_entry(self):
        env = self._env(dict(WIN), True, child_env=["SYSTEMDRIVE"])
        self.assertEqual(env.get("SYSTEMDRIVE"), "C:")
        self.assertNotIn("SystemDrive", env)


if __name__ == "__main__":
    unittest.main()
