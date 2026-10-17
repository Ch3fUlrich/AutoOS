#!/usr/bin/env python3
"""FF1 (D-106) item 4: the render step must not write outside the home it is
given, and must never bake a lane-sandbox path into a user-level config.

A lane sandbox (`<...>/AutoOS-lanes/<lane>/logs/sandboxes/<run>`) is a throwaway
clone a spawned worker edits. `lib/agent_harness.py` renders the agent configs a
user's shell later reads (`.config/opencode/opencode.json`, `.openhands/…`, and
by the same rule `.claude.json`, `.codex/`, `.gemini/`); rendering one of those
from a lane bakes a path that is deleted with the sandbox into the user's home.

Nothing here runs the real installer and nothing writes to the real home: HOME
and XDG_CONFIG_HOME point at a temp dir, and every write the module makes is
recorded and checked against it.

Run from the repo root:

    python3 tests/test_user_config_fence.py
"""
import atexit
import builtins
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "lib" / "agent_harness.py"

# The shapes that mark a path as a checkout that is going away.
LANE_MARKERS = ("AutoOS-lanes", os.path.join("logs", "sandboxes"))
# User-level configs the agents read out of the home directory.
USER_CONFIG_NAMES = (".claude.json", ".codex", ".gemini", ".openhands",
                     os.path.join(".config", "opencode"))


def load_module():
    spec = importlib.util.spec_from_file_location("agent_harness_fence", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SYNTHETIC_LANE = []


def lane_checkout():
    """A real lane sandbox path if this suite is running in one (it usually is:
    every spawned worker runs it there), else a synthetic one with the same shape.

    The synthetic one is *created*, under a temp dir rather than an invented
    /home/user: a test that chdirs into it (a relative path judged from a lane
    cwd) has to find a directory, and the suite must pass on a host that is not
    a lane.
    """
    here = str(ROOT)
    if any(marker in here for marker in LANE_MARKERS):
        return here
    if not _SYNTHETIC_LANE:
        base = tempfile.mkdtemp(prefix="autoos-synthetic-lane-")
        path = os.path.join(base, "code", "AutoOS-lanes", "L1-routing-FF1",
                            "logs", "sandboxes",
                            "L1-routing-FF1-20260928-000000-ff1-abc123")
        os.makedirs(path, exist_ok=True)
        atexit.register(shutil.rmtree, base, True)
        _SYNTHETIC_LANE.append(path)
    return _SYNTHETIC_LANE[0]


def neutral_checkout():
    """A checkout path with no lane shape: /home/user/AutoOS is the real one.

    Carries the files the render step reads out of the checkout, so the render
    runs for real against a root the fence can accept.
    """
    tmp = tempfile.mkdtemp(prefix="autoos-fence-repo-")
    harness = load_module().load_harness(str(MODULE.parent.parent / "catalog" /
                                            "agent-harness.json"))
    for role in (harness.get("roles") or {}).values():
        profile = (role.get("openhands") or {}).get("profile")
        if not profile:
            continue
        path = os.path.join(tmp, "openhands", "agent-profiles", profile + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"profile": profile}, fh)
    return tmp


class _RenderCase(unittest.TestCase):
    """Shared: render into a temp home with the module's own CLI entry point,
    recording every file the render opens for writing."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-user-config-fence-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.xdg = os.path.join(self.home, ".config")
        os.makedirs(self.xdg)
        self.writes = []

    def opencode_path(self):
        return os.path.join(self.xdg, "opencode", "opencode.json")

    def render_opencode(self, repo_root, config_path=None, skills_source=None):
        argv = ["opencode",
                "--config", config_path or self.opencode_path(),
                "--repo-root", repo_root,
                "--skills-source", skills_source or os.path.join(repo_root, ".agents", "skills")]
        return self._run(argv)

    def render_openhands(self, repo_root, directory=None):
        argv = ["openhands",
                "--openhands-dir", directory or os.path.join(self.home, ".openhands"),
                "--repo-root", repo_root]
        return self._run(argv)

    def _run(self, argv):
        real_open = builtins.open
        sink = self.writes

        def spy(file, mode="r", *args, **kwargs):
            try:
                sink.append((os.path.abspath(str(file)), mode))
            except (TypeError, ValueError):
                pass
            return real_open(file, mode, *args, **kwargs)

        out, err = io.StringIO(), io.StringIO()
        env = {"HOME": self.home, "XDG_CONFIG_HOME": self.xdg}
        with mock.patch.dict(os.environ, env):
            with mock.patch("builtins.open", spy):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = self.module.main(argv)
        return rc, out.getvalue() + err.getvalue()

    def written_paths(self):
        return [p for p, mode in self.writes if any(c in mode for c in "wax+")]

    def files_under_home(self):
        found = []
        for dirpath, _dirs, names in os.walk(self.home):
            for name in names:
                found.append(os.path.join(dirpath, name))
        return found

    def assertNoLanePathInUserConfigs(self):
        for path in self.files_under_home():
            if not os.path.isfile(path) or path.endswith((".pyc",)):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for marker in LANE_MARKERS:
                self.assertNotIn(marker, text,
                                 "%s bakes the lane path %r into %s"
                                 % (self.module_name, marker, path))

    @property
    def module_name(self):
        return type(self).__name__


class RenderContainmentTests(_RenderCase):
    """The render writes where it was told to write, and nowhere else."""

    def test_opencode_render_writes_only_under_the_temp_home(self):
        repo = neutral_checkout()
        self.addCleanup(shutil.rmtree, repo, True)
        rc, output = self.render_opencode(repo)
        self.assertEqual(0, rc, output)
        outside = [p for p in self.written_paths()
                   if not p.startswith(self.home + os.sep)]
        self.assertEqual([], outside, "the render wrote outside the temp home")
        self.assertTrue(os.path.exists(self.opencode_path()), output)

    def test_the_render_touches_no_other_user_config(self):
        # .claude.json / .codex / .gemini are written by other components; this
        # step must not create any of them as a side effect.
        repo = neutral_checkout()
        self.addCleanup(shutil.rmtree, repo, True)
        rc, output = self.render_opencode(repo)
        self.assertEqual(0, rc, output)
        for name in USER_CONFIG_NAMES:
            if name.endswith(os.path.join(".config", "opencode")):
                continue
            self.assertFalse(os.path.exists(os.path.join(self.home, name)),
                             "the render created %s" % name)

    def test_openhands_render_writes_only_under_the_temp_home(self):
        repo = neutral_checkout()
        self.addCleanup(shutil.rmtree, repo, True)
        rc, output = self.render_openhands(repo)
        self.assertEqual(0, rc, output)
        outside = [p for p in self.written_paths()
                   if not p.startswith(self.home + os.sep)]
        self.assertEqual([], outside, "the render wrote outside the temp home")

    def test_a_clean_render_bakes_no_lane_path(self):
        repo = neutral_checkout()
        self.addCleanup(shutil.rmtree, repo, True)
        rc, output = self.render_opencode(repo)
        self.assertEqual(0, rc, output)
        self.assertNoLanePathInUserConfigs()


class LaneConfigFenceTests(_RenderCase):
    """FF1 (D-106) item 4: rendering a USER-level config from a lane sandbox is
    refused outright — the paths it would write die with the sandbox."""

    def test_opencode_from_a_lane_is_refused_and_writes_nothing(self):
        rc, output = self.render_opencode(lane_checkout())
        self.assertEqual(1, rc, output)
        self.assertIn("refused", output)
        self.assertFalse(os.path.exists(self.opencode_path()),
                         "a lane render wrote into the home directory")
        self.assertEqual([], self.written_paths(), output)

    def test_openhands_from_a_lane_is_refused(self):
        rc, output = self.render_openhands(lane_checkout())
        self.assertEqual(1, rc, output)
        self.assertIn("refused", output)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".openhands")))

    def test_a_lane_may_still_render_a_staged_target(self):
        # The fence is about the home directory, not about lanes in general:
        # `--config` outside any home is a staging directory the caller owns.
        stage = os.path.join(self.tmp, "stage", "opencode.json")
        rc, output = self.render_opencode(lane_checkout(), config_path=stage)
        self.assertEqual(0, rc, output)
        self.assertTrue(os.path.exists(stage))

    def test_a_real_checkout_still_renders_into_the_home(self):
        repo = neutral_checkout()
        self.addCleanup(shutil.rmtree, repo, True)
        rc, output = self.render_opencode(repo)
        self.assertEqual(0, rc, output)
        self.assertIn("opencode.json", self.opencode_path())

    def test_the_refusal_names_the_rule(self):
        _rc, output = self.render_opencode(lane_checkout())
        self.assertIn("lane sandbox", output)
        self.assertIn("real checkout", output)


class LanePathIdentityTests(unittest.TestCase):
    """FF1b item 5: the fence compared paths by string, on a checkout path it had
    only abspath()'d. `os.path.abspath` does not resolve symlinks and folds ".."
    lexically, so a lane reached through a link (or through a link plus "..")
    read as a real checkout; and on Windows/macOS, where the filesystem is
    case-insensitive, 'autoos-lanes' opens the same directory as 'AutoOS-lanes'
    while the string comparison says they are unrelated."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-fence-identity-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def link(self, name, target):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        return path

    def test_a_symlink_into_a_lane_is_a_lane(self):
        # The link lives under the temp dir, so a lexical fold of the ".." that
        # walks back up through it must not be trusted either.
        deep = os.path.join(self.tmp, "a", "b")
        os.makedirs(deep)
        lane_link = os.path.join(deep, "lane")
        os.symlink(lane_checkout(), lane_link)
        via_dotdot = os.path.join(lane_link, "..", "lane")
        for path in (lane_link, via_dotdot):
            self.assertTrue(self.module.is_lane_checkout(path), path)

    def test_the_fence_bites_through_a_symlinked_checkout(self):
        lane_link = self.link("lane", lane_checkout())
        with mock.patch.dict(os.environ, {"HOME": "/home/dev",
                                          "XDG_CONFIG_HOME": "/home/dev/.config"}):
            ok, reason = self.module.user_config_fence(
                lane_link, "/home/dev/.claude.json")
        self.assertFalse(ok, "a symlinked lane opened the home directory: %s" % reason)

    def test_a_symlinked_home_still_counts_as_user_level(self):
        # The other half: HOME reached through a link is still somebody's home.
        home = os.path.join(self.tmp, "real-home")
        os.makedirs(os.path.join(home, ".config"))
        home_link = self.link("home", home)
        with mock.patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": ""}):
            self.assertTrue(self.module.is_user_level_target(
                os.path.join(home_link, ".config", "opencode", "opencode.json")))
            ok, _reason = self.module.user_config_fence(lane_checkout(), home_link)
            self.assertFalse(ok)

    def test_case_folding_follows_the_platform(self):
        # Only the `/AutoOS-lanes/` marker differs in case here, so a fold that
        # is not the platform's own cannot borrow the other marker.
        lane = "/home/dev/AutoOS-lanes/L1-routing/work"
        lower = "/home/dev/autoos-lanes/l1-routing/work"
        self.assertTrue(self.module.is_lane_checkout(lane))
        self.assertTrue(self.module.is_lane_checkout(lower, fold=True))
        self.assertFalse(self.module.is_lane_checkout(lower, fold=False))
        self.assertEqual(self.module.case_insensitive_paths(),
                         self.module.is_lane_checkout(lower))
        with mock.patch.dict(os.environ, {"HOME": "/home/dev",
                                          "XDG_CONFIG_HOME": "/home/dev/.config"}):
            ok, reason = self.module.user_config_fence(lower, "/home/dev/.claude.json",
                                                       fold=True)
            self.assertFalse(ok, reason)
            ok, _r = self.module.user_config_fence(lower, "/home/dev/.claude.json",
                                                   fold=False)
            self.assertTrue(ok)

    def test_the_comparator_is_injectable_for_the_suites_platform(self):
        # Linux is case-sensitive, so the Windows/macOS behaviour is proven by
        # forcing the fold, and the negative half by a path that is not a lane
        # in any case.
        self.assertTrue(self.module.is_lane_checkout(
            "/home/dev/AUTOOS-LANES/L1/logs/sandboxes/x", fold=True))
        self.assertFalse(self.module.is_lane_checkout("/home/dev/AutoOS", fold=True))
        self.assertFalse(self.module.is_lane_checkout(
            "/home/dev/autoos-lanes-clone/L1", fold=True))


class LaneShapeTests(unittest.TestCase):
    """The recognisers, on the path shapes the installer actually meets."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_lane_shapes(self):
        for path in (lane_checkout(),
                     "C:/Users/dev/code/AutoOS-lanes/L1/flow",
                     "/home/dev/AutoOS-lanes"):
            self.assertTrue(self.module.is_lane_checkout(path), path)

    def test_a_real_checkout_is_not_a_lane(self):
        for path in ("/home/dev/code/AutoOS",
                     "C:/Users/dev/Documents/AutoOS",
                     "/home/dev/AutoOS-worktrees/fix"):
            self.assertFalse(self.module.is_lane_checkout(path), path)

    def test_a_relative_path_resolves_against_a_lane_cwd(self):
        # The installer passes an absolute ROOT, but a relative one run from
        # inside a lane is still a lane: it is resolved before it is judged.
        cwd = os.getcwd()
        try:
            os.chdir(lane_checkout())
            self.assertTrue(self.module.is_lane_checkout("AutoOS"))
        finally:
            os.chdir(cwd)

    def test_home_shapes(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/dev",
                                          "XDG_CONFIG_HOME": "/home/dev/.config"}):
            self.assertTrue(self.module.is_user_level_target(
                "/home/dev/.config/opencode/opencode.json"))
            self.assertTrue(self.module.is_user_level_target("/home/dev/.claude.json"))
            self.assertTrue(self.module.is_user_level_target("/home/dev/.openhands"))
            self.assertFalse(self.module.is_user_level_target("/tmp/stage/opencode.json"))
            self.assertFalse(self.module.is_user_level_target("/opt/autoos/stage/x.json"))

    def test_the_fence_only_bites_on_both_halves(self):
        lane, home = lane_checkout(), "/home/dev/.claude.json"
        with mock.patch.dict(os.environ, {"HOME": "/home/dev", "XDG_CONFIG_HOME": "/home/dev/.config"}):
            ok, reason = self.module.user_config_fence(lane, home)
            self.assertFalse(ok)
            self.assertIn("lane sandbox", reason)
            self.assertTrue(self.module.user_config_fence(lane, "/tmp/stage/x.json")[0])
            self.assertTrue(self.module.user_config_fence("/home/dev/AutoOS", home)[0])


if __name__ == "__main__":
    unittest.main()
