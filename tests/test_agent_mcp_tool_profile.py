"""tests/test_agent_mcp_tool_profile.py - what the autoos-agent MCP exposes (D-665).

AO-L2-LAUNCH merge criteria 2 and 3.

Criterion 2 is about LISTING. The same MCP server answers an L1 and an L2, and
`AUTOOS_AGENT_LAYER=L2` already makes it refuse the lane-control tools - but a
refusal is a tool the model can still see, reach for, and waste a turn on, and
opencode shows an L2 the whole menu it is going to be denied. So the L2's
server registers the spawner's own tools and nothing else; the lane config
renders the marker into the MCP server's environment so the selection happens
where the server starts, not in a prompt.

Criterion 3 is the next layer of the same fence: inside profile l2 a `spawn`
may only be a tier-3 worker. An L2 that could spawn tier 1 or an orchestrate
card would own a session that can write, which is the thing the whole lane
exists to prevent. (The full ROLE-GATE is a later lane; this is the minimal
check that an L2 cannot climb.)

The tool-set pin drives the real registration (`build_server(...).list_tools()`),
so it is evidence about the server an L2 actually talks to, not about a list
kept beside it.

Run from the repo root:

    python3 -m unittest tests.test_agent_mcp_tool_profile
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import autoos_agent_mcp as mcp_server  # noqa: E402
import oc_l1_render  # noqa: E402

_HAVE_MCP = True
try:
    import mcp.server.fastmcp  # noqa: F401
except ImportError:
    _HAVE_MCP = False


def registered_tool_names(profile):
    """The names the server registers when it serves `profile`."""
    app = mcp_server.build_server(profile=profile)
    return {t.name for t in asyncio.run(app.list_tools())}


class ProfileSelectionTest(unittest.TestCase):
    """The env marker, read once, names the profile - nothing else does."""

    def _profile(self, **env):
        base = dict(os.environ)
        base.pop(mcp_server.ENV_AGENT_LAYER, None)
        with mock.patch.dict(os.environ, base, clear=True):
            for k, v in env.items():
                if v is not None:
                    os.environ[k] = v
            return mcp_server.mcp_tool_profile()

    def test_the_l2_marker_selects_the_l2_profile(self):
        self.assertEqual(self._profile(**{mcp_server.ENV_AGENT_LAYER: "L2"}), "l2")

    def test_the_marker_is_read_case_insensitively_and_padded(self):
        # the value is a lane's own metadata, and a lane that wrote " l2 "
        # still means the same lane
        self.assertEqual(self._profile(**{mcp_server.ENV_AGENT_LAYER: " l2 "}), "l2")

    def test_any_other_layer_or_none_is_the_full_profile(self):
        self.assertEqual(self._profile(), mcp_server.FULL_PROFILE)
        for value in ("L1", "L3", "worker", ""):
            self.assertEqual(
                self._profile(**{mcp_server.ENV_AGENT_LAYER: value}),
                mcp_server.FULL_PROFILE, value)

    def test_the_l2_profile_tool_set_is_the_spawner_and_its_read_only_companions(self):
        # Pin, verbatim: the criterion names these eight and no others.
        self.assertEqual(set(mcp_server.MCP_TOOL_PROFILES["l2"]), {
            "spawn", "status", "result", "ps", "list_clients", "route",
            "context", "heartbeat",
        })
        self.assertTrue(set(mcp_server.MCP_TOOL_PROFILES["l2"])
                        <= set(mcp_server.MCP_TOOL_NAMES))

    def test_the_marker_is_one_variable_name_across_the_three_sides(self):
        # The launcher exports it to the lane child, the renderer writes it into
        # the MCP's own environment, the server reads it back - three files, one
        # string, and a mismatch would silently restore the full menu.
        import oc_l1
        import oc_l1_render
        import oc_l1_serve
        self.assertEqual(oc_l1.ENV_AGENT_LAYER, oc_l1_render.ENV_AGENT_LAYER)
        self.assertEqual(oc_l1.ENV_AGENT_LAYER, mcp_server.ENV_AGENT_LAYER)
        child = oc_l1_serve._child_env(
            {"agent_layer": "L2", "scratch_dir": tempfile.mkdtemp()},
            "/tmp/opencode.json", "unused-password")
        self.assertEqual(child.get(oc_l1.ENV_AGENT_LAYER), "L2")


@unittest.skipUnless(_HAVE_MCP, "mcp package not installed")
class ToolRegistrationTest(unittest.TestCase):
    """What the running server actually exposes, read off its own registry."""

    def test_profile_l2_lists_only_the_spawner_tools(self):
        self.assertEqual(registered_tool_names("l2"),
                         set(mcp_server.MCP_TOOL_PROFILES["l2"]))

    def test_profile_l2_hides_every_lane_control_tool(self):
        seen = registered_tool_names("l2")
        self.assertEqual(seen & set(mcp_server.LANE_CONTROL_TOOLS), set(),
                         "an L2 must not even see the tools it is refused")
        for hidden in ("oc_status", "l2_status", "cancel", "respond", "list_agents"):
            self.assertNotIn(hidden, seen)

    def test_the_full_profile_registers_every_named_tool(self):
        # MCP_TOOL_NAMES is the list the profiles are subtracted from, so a
        # tool added to serve() without being named here is what this catches.
        self.assertEqual(registered_tool_names(mcp_server.FULL_PROFILE),
                         set(mcp_server.MCP_TOOL_NAMES))

    def test_an_unknown_profile_fails_closed_to_the_narrow_one(self):
        # A typo in a lane config ("L2" rendered as "l2-lane") must not hand
        # back the full menu: the narrowest profile is the safe answer.
        seen = registered_tool_names("no-such-profile")
        self.assertEqual(seen, set(mcp_server.MCP_TOOL_PROFILES["l2"]))

    def test_the_server_serves_the_profile_its_environment_names(self):
        with mock.patch.dict(os.environ, {mcp_server.ENV_AGENT_LAYER: "L2"}):
            seen = registered_tool_names(None)
        self.assertEqual(seen, set(mcp_server.MCP_TOOL_PROFILES["l2"]))

    # Each advertised tool is a thin wrapper over one module function, and the
    # CLI calls those directly - hiding a tool must not have moved its
    # implementation or renamed it.
    PROFILE_TOOL_TARGETS = {"list_clients": "list_clients", "spawn": "spawn",
                            "status": "status", "result": "result",
                            "ps": "ps", "route": "route_plan",
                            "context": "context_info",
                            "heartbeat": "heartbeat_info"}

    def test_a_profile_tool_wraps_the_module_function_callers_use(self):
        self.assertEqual(set(self.PROFILE_TOOL_TARGETS),
                         set(mcp_server.MCP_TOOL_PROFILES["l2"]))
        for tool, fn in self.PROFILE_TOOL_TARGETS.items():
            self.assertTrue(callable(getattr(mcp_server, fn, None)),
                            "%s -> %s" % (tool, fn))


class LaneRendersTheMarkerTest(unittest.TestCase):
    """criterion 2's other half: the L2 lane config renders the env for the
    MCP server it starts, so the profile is chosen at registration."""

    def test_build_lane_marks_the_layer_and_the_read_only_role(self):
        import oc_l2
        built = oc_l2.build_lane("l2-proj-ab12cd-spawn", "/srv/proj", "spawn",
                                 Path("/srv/brief.md"), "l2-orchestrator",
                                 Path("/srv/inbox.md"),
                                 opencode_bin="/usr/local/bin/opencode")
        self.assertEqual(built["agent_layer"], "L2")
        self.assertEqual(built["guard_role"], "l2")

    def _render(self, **over):
        lane = {"name": "l2-x", "cwd": "/srv/proj", "mcp": ["autoos-agent"],
                "instructions": [], "plugins": [], "scratch_dir": "/tmp/unused",
                "model": {"provider": "omniroute", "modelID": "combo"},
                "agent_layer": "L2", "guard_role": "l2"}
        lane.update(over)
        with tempfile.TemporaryDirectory() as td:
            lane["scratch_dir"] = td
            path = oc_l1_render.render(lane, ROOT / "opencode.jsonc")
            return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_the_renderer_marks_the_mcp_server_it_starts(self):
        # opencode starts the MCP as its own child: the marker has to be in the
        # server's environment, or the L2's list is chosen by whatever the
        # calling shell happened to export.
        env = self._render()["mcp"]["autoos-agent"]["environment"]
        self.assertEqual(env["AUTOOS_AGENT_LAYER"], "L2")

    def test_an_unmarked_lane_renders_no_marker(self):
        # An L1's MCP keeps every tool; a marker invented for a lane that never
        # set one would hide the L1's own lane-control tools.
        env = self._render(agent_layer=None)["mcp"]["autoos-agent"]["environment"]
        self.assertNotIn("AUTOOS_AGENT_LAYER", env)


if __name__ == "__main__":
    unittest.main()
