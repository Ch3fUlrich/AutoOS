"""tests/test_oc_l1.py - step A1 tests for tools/oc_l1.py (render only).

unittest, stdlib only, no real opencode. All values are placeholders.
The hint-line / localhost-argv / password-argv mutants of the full A2 plan
cannot be proved yet: `start` is a stub in A1 (exit 3); the two mutants
that touch A1 code (bind, env-var refusal) ARE proved against temp copies.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import oc_l1  # noqa: E402

SECRET_ENV_VALUE = "sk-TEST-SECRET-DO-NOT-LEAK-000"
FAKE_SECRETS = (SECRET_ENV_VALUE, "s3cr3t-value", "supersecretkey123")


def make_lane(tmp, **over):
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (tmp / "plugins").mkdir(parents=True, exist_ok=True)
    # fake binary: an absolute, never-existing placeholder (platform-native)
    fake_bin = os.path.join(tmp, "oc-bin-placeholder")
    lane = {
        "name": "l1test",
        "cwd": str(proj),
        "handoff": str(proj / "handoff.md"),
        "opencode_bin": fake_bin,
        "password_env": "AUTOOS_OCL1_TEST_PW",
        "model": {
            "provider": "omniroute",
            "key": "placeholder-direct-model",
            "modelID": "placeholderprovider/PlaceholderModel",
            "fallback_key": "placeholder-direct-fallback",
            "fallback_modelID": "placeholderprovider/PlaceholderFallback",
        },
        "mcp": ["autoos-agent"],
        "instructions": ["AGENTS.md", str(proj / "handoff.md")],
        "serve_port": 47255,
        "plugins": [str(tmp / "plugins" / "placeholder-guard.js")],
        "state_file": str(tmp / "state" / "l1test.state.json"),
        "scratch_dir": str(tmp / "scratch"),
    }
    lane.update(over)
    return lane


def run_main(argv):
    """oc_l1.main with stdout/stderr captured; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = oc_l1.main(argv)
    return rc, out.getvalue(), err.getvalue()


def write_cfg(tmp, lane):
    p = tmp / "config.json"
    p.write_text(json.dumps({lane["name"]: lane}), encoding="utf-8")
    return p


class RenderGoldenTest(unittest.TestCase):
    def setUp(self):
        # If a future render() starts inlining env values, the golden file
        # must still contain only the env var NAMES, never these values.
        os.environ["AUTOOS_OMNIROUTE_URL"] = "http://127.0.0.1:20128"
        os.environ["AUTOOS_OMNIROUTE_KEY"] = SECRET_ENV_VALUE

    def tearDown(self):
        for k in ("AUTOOS_OMNIROUTE_URL", "AUTOOS_OMNIROUTE_KEY"):
            os.environ.pop(k, None)

    def test_render_golden(self):
        with tempfile.TemporaryDirectory(prefix="oc_l1_test_") as td:
            tmp = Path(td)
            lane = make_lane(tmp)
            cfg = write_cfg(tmp, lane)
            rc, _, _ = run_main(["render", "--name", "l1test", "--config", str(cfg)])
            self.assertEqual(rc, 0)
            out = tmp / "scratch" / "opencode.json"
            self.assertTrue(out.is_file())
            text = out.read_text(encoding="utf-8")
            rendered = json.loads(text)

            # 1) no secret value anywhere - only the env var NAMES
            for s_ in FAKE_SECRETS:
                self.assertNotIn(s_, text)
            self.assertIn("AUTOOS_OMNIROUTE_URL", text)
            self.assertIn("AUTOOS_OMNIROUTE_KEY", text)
            self.assertEqual(
                set(rendered), {"$schema", "model", "provider", "instructions", "mcp", "permission", "plugins"})
            prov = rendered["provider"]["omniroute"]
            self.assertEqual(prov["npm"], "@ai-sdk/openai-compatible")
            self.assertEqual(prov["options"]["baseURL"], "{env:AUTOOS_OMNIROUTE_URL}/v1")
            self.assertEqual(prov["options"]["apiKey"], "{env:AUTOOS_OMNIROUTE_KEY}")
            # the start-time baseURL default is not embedded in the file
            self.assertNotIn("http://127.0.0.1:20128", text)
            # the Basic password env var name is a start concern, not a render one
            self.assertNotIn("AUTOOS_OCL1_TEST_PW", text)

            # 2) model pin: <provider>/<modelID>; the models map is keyed by the gateway model ids
            self.assertEqual(rendered["model"], "omniroute/placeholderprovider/PlaceholderModel")
            models = prov["models"]
            self.assertEqual(
                set(models),
                {"placeholderprovider/PlaceholderModel", "placeholderprovider/PlaceholderFallback"},
            )
            self.assertEqual(models["placeholderprovider/PlaceholderModel"]["name"], "placeholder-direct-model")
            self.assertEqual(models["placeholderprovider/PlaceholderModel"]["limit"], {"context": 131072, "output": 16000})

            # 3) mcp: a FLAT map name -> entry, enabled only for the lane's list (lean rule)
            servers = rendered["mcp"]
            self.assertNotIn("servers", servers)
            self.assertEqual(
                set(servers),
                {"serena", "graphify", "playwright", "context7", "omnigraph", "autoos-agent"},
            )
            self.assertTrue(servers["autoos-agent"]["enabled"])
            self.assertTrue(servers["autoos-agent"]["command"])
            for name in ("serena", "graphify", "playwright", "context7", "omnigraph"):
                self.assertFalse(servers[name]["enabled"])

            # 4) no server block (hostname/port are command-line flags of `opencode serve`)
            for old_key in ("server", "providers", "plugin"):
                self.assertNotIn(old_key, rendered)

            # 5) instructions list as configured
            self.assertEqual(
                rendered["instructions"], ["AGENTS.md", str(tmp / "proj" / "handoff.md")]
            )

            # 6) plugins: the configured directories
            self.assertEqual(
                rendered["plugins"], [str(tmp / "plugins" / "placeholder-guard.js")]
            )


class PortDerivationTest(unittest.TestCase):
    def test_stable_and_in_range(self):
        for name in ("l1test", "l1-pilot", "orch", "a", "x" * 50):
            p = oc_l1.derive_port(name)
            self.assertIsInstance(p, int)
            self.assertTrue(47200 <= p <= 47299, "port %d out of range" % p)
        self.assertEqual(oc_l1.derive_port("l1test"), oc_l1.derive_port("l1test"))

    def test_default_port_applied_when_absent(self):
        with tempfile.TemporaryDirectory(prefix="oc_l1_test_") as td:
            tmp = Path(td)
            lane = make_lane(tmp)
            del lane["serve_port"]
            cfg = write_cfg(tmp, lane)
            rc, _, _ = run_main(["render", "--name", "l1test", "--config", str(cfg)])
            self.assertEqual(rc, 0)
            rendered = json.loads((tmp / "scratch" / "opencode.json").read_text())
            self.assertNotIn("server", rendered)
            self.assertEqual(oc_l1.validate_lane(lane, "l1test")["serve_port"], oc_l1.derive_port("l1test"))

    def test_explicit_port_out_of_range_refused(self):
        for bad in (47199, 47300, "47255", 47255.0):
            with tempfile.TemporaryDirectory(prefix="oc_l1_test_") as td:
                tmp = Path(td)
                lane = make_lane(tmp, serve_port=bad)
                cfg = write_cfg(tmp, lane)
                rc, _, _ = run_main(["render", "--name", "l1test", "--config", str(cfg)])
                self.assertEqual(rc, 2, "serve_port=%r must be refused" % (bad,))
                self.assertFalse((tmp / "scratch").exists())


class DefaultConfigPathTest(unittest.TestCase):
    def test_per_platform(self):
        d = oc_l1.default_config_path()
        self.assertEqual(d.name, "oc-l1.json")
        self.assertEqual(d.parent.name, "autoos")
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            self.assertEqual(d, Path(base) / "autoos" / "oc-l1.json")
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
            self.assertEqual(d, Path(base) / "autoos" / "oc-l1.json")

    def test_env_override_honoured(self):
        env_key = "LOCALAPPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"
        saved = os.environ.get(env_key)
        with tempfile.TemporaryDirectory(prefix="oc_l1_cfg_") as td:
            os.environ[env_key] = td
            try:
                d = oc_l1.default_config_path()
            finally:
                if saved is None:
                    del os.environ[env_key]
                else:
                    os.environ[env_key] = saved
        self.assertEqual(d, Path(td) / "autoos" / "oc-l1.json")


class StubSubcommandsTest(unittest.TestCase):
    """A2: `start`/`status` are implemented (step A2) - these commands now
    reach the config-loading stage, so with no host config they exit 2
    with 'config not found' (or with the A1 lane-validation error when a
    config is given but the lane is bad); they must NOT hit the old A1
    stub text."""

    def test_start_and_status_not_stubbed(self):
        for cmd in ("start", "status"):
            rc, out, err = run_main([cmd, "--name", "l1test"])
            self.assertEqual(rc, 2)
            self.assertNotIn("not implemented in step A1", out)
            self.assertIn("oc_l1: error:", err)


class ExampleConfigTest(unittest.TestCase):
    def test_example_lane_validates(self):
        example = ROOT / "configuration" / "oc-l1.example.json"
        self.assertTrue(example.is_file(), "configuration/oc-l1.example.json missing")
        data = json.loads(example.read_text(encoding="utf-8"))
        lanes = {k: v for k, v in data.items() if not k.startswith("_")}
        self.assertTrue(lanes, "example must define at least one lane")
        with tempfile.TemporaryDirectory(prefix="oc_l1_ex_") as td:
            tmp = Path(td)
            (tmp / "proj").mkdir()
            for name, lane in lanes.items():
                # The template ships platform-neutral placeholder paths; the
                # validator (correctly) requires paths absolute on the host
                # that runs it, so fill those in before validating the shape.
                filled = dict(lane)
                for key, sub in (
                    ("cwd", str(tmp / "proj")),
                    ("handoff", str(tmp / "proj" / "handoff.md")),
                    ("opencode_bin", str(tmp / "oc-bin")),
                    ("scratch_dir", str(tmp / "scratch")),
                    ("state_file", str(tmp / "state.json")),
                ):
                    filled[key] = sub
                filled["instructions"] = ["AGENTS.md", str(tmp / "proj" / "handoff.md")]
                resolved = oc_l1.validate_lane(filled, name)  # raises on a bad entry
                self.assertEqual(resolved["name"], name)
                self.assertTrue(47200 <= resolved["serve_port"] <= 47299)


# --- mutation proofs on TEMP COPIES of the tool ------------------------------

DRIVER = '''
import json, sys
from pathlib import Path
td = Path(sys.argv[1])
repo_cfg = sys.argv[2]
sys.path.insert(0, str(td / "mod"))
import oc_l1
cfg = td / "cfg.json"
lane = json.loads(cfg.read_text())["l1test"]
rc = oc_l1.main(["render", "--name", "l1test", "--config", str(cfg),
                 "--repo-config", repo_cfg])
rendered = None
if (td / "scratch" / "opencode.json").is_file():
    rendered = json.loads((td / "scratch" / "opencode.json").read_text())
__CHECK__
'''


if __name__ == "__main__":
    unittest.main()
