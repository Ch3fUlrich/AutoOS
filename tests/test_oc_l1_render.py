"""tests/test_oc_l1_render.py - pins the shape of the scratch config that `oc_l1.py render` writes.

opencode 2.0.12 reads its OWN schema (a live proof showed a session that failed in 0.3 s with 0 tokens when the
AutoOS repo-file shape was rendered). This file pins the shape field by field: singular `provider`, the models map
keyed by the gateway model id, a flat `mcp` map, `plugins` (directories), `permission`, and no `server` block.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from test_oc_l1 import make_lane, run_main, write_cfg  # noqa: E402


def render(tmp, **over):
    lane = make_lane(tmp, **over)
    cfg = write_cfg(tmp, lane)
    rc, _, err = run_main(["render", "--name", "l1test", "--config", str(cfg)])
    assert rc == 0, err
    text = (tmp / "scratch" / "opencode.json").read_text(encoding="utf-8")
    return json.loads(text), text


class RenderShapeTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_render_")
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_top_level_keys_exact(self):
        cfg, _ = render(self.tmp)
        self.assertEqual(set(cfg), {"$schema", "model", "provider", "instructions", "mcp", "permission", "plugins",
                                    "compaction", "tool_output"})
        self.assertEqual(cfg["$schema"], "https://opencode.ai/config.json")

    def test_compaction_prunes_with_a_small_tail(self):
        # Lane c (2026-10-05): opencode's prune defaults to FALSE and the tool
        # output default is 2000 lines / 50 KB - the defaults under which the
        # L1 session's per-turn input grew to 794k tokens. The render pins the
        # orchestrator shape.
        cfg, _ = render(self.tmp)
        self.assertEqual(cfg["compaction"], {"auto": True, "prune": True, "tail_turns": 4})
        self.assertEqual(cfg["tool_output"], {"max_lines": 400, "max_bytes": 16384})

    def test_model_limit_follows_the_lane_when_pinned(self):
        # Lane c (2026-10-05): a 1M leg rendered at a hardcoded 131072 is the
        # "wrongly defined token size" failure - the client compacts long
        # before the model's real window. Without a pin the safe 128k default
        # stands (the deepseek fallback catalog claims 128000).
        cfg, _ = render(self.tmp)
        self.assertEqual(cfg["provider"]["omniroute"]["models"]["placeholderprovider/PlaceholderModel"]["limit"],
                         {"context": 131072, "output": 16000})
        cfg, _ = render(self.tmp, model={
            "provider": "omniroute", "key": "k", "modelID": "gemini/gemini-3.8-flash",
            "limit": {"context": 1048576, "output": 16000}})
        self.assertEqual(cfg["provider"]["omniroute"]["models"]["gemini/gemini-3.8-flash"]["limit"],
                         {"context": 1048576, "output": 16000})

    def test_external_directory_renders_when_pinned(self):
        # Lane c (2026-10-05): the outside-folder allowlist moves from a
        # hand-edited host overlay into the lane config.
        cfg, _ = render(self.tmp, external_directory=["/home/s/code/AutoOS-worktrees/other/**"])
        self.assertEqual(cfg["permission"]["external_directory"],
                         {"/home/s/code/AutoOS-worktrees/other/**": "allow"})

    def test_external_directory_rejects_non_strings(self):
        lane = make_lane(self.tmp, external_directory=[1, 2])
        cfg = write_cfg(self.tmp, lane)
        rc, _, err = run_main(["render", "--name", "l1test", "--config", str(cfg)])
        self.assertEqual(rc, 2)
        self.assertIn("external_directory", err)

    def test_old_repo_file_keys_are_gone(self):
        cfg, _ = render(self.tmp)
        for old in ("providers", "plugin", "server"):
            self.assertNotIn(old, cfg)
        self.assertNotIn("servers", cfg["mcp"])

    def test_provider_block(self):
        cfg, _ = render(self.tmp)
        self.assertEqual(list(cfg["provider"]), ["omniroute"])
        prov = cfg["provider"]["omniroute"]
        self.assertEqual(prov["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(prov["options"], {"baseURL": "{env:AUTOOS_OMNIROUTE_URL}/v1", "apiKey": "{env:AUTOOS_OMNIROUTE_KEY}"})

    def test_model_pin_and_models_keyed_by_gateway_id(self):
        cfg, _ = render(self.tmp)
        self.assertEqual(cfg["model"], "omniroute/placeholderprovider/PlaceholderModel")
        models = cfg["provider"]["omniroute"]["models"]
        self.assertEqual(set(models), {"placeholderprovider/PlaceholderModel", "placeholderprovider/PlaceholderFallback"})
        for entry in models.values():
            self.assertEqual(set(entry), {"name", "limit"})
            self.assertEqual(entry["limit"], {"context": 131072, "output": 16000})

    def test_display_name_from_key_else_last_path_part(self):
        cfg, _ = render(self.tmp)
        models = cfg["provider"]["omniroute"]["models"]
        self.assertEqual(models["placeholderprovider/PlaceholderModel"]["name"], "placeholder-direct-model")
        with tempfile.TemporaryDirectory(prefix="oc_l1_render2_") as td2:
            tmp2 = Path(td2)
            lane_model = {"provider": "omniroute", "key": "k", "modelID": "vendor/Some-Model-1"}
            cfg2, _ = render(tmp2, model=lane_model)
            self.assertEqual(set(cfg2["provider"]["omniroute"]["models"]), {"vendor/Some-Model-1"})

    def test_mcp_is_a_flat_map_with_the_lean_rule(self):
        cfg, _ = render(self.tmp)
        mcp = cfg["mcp"]
        self.assertEqual(set(mcp), {"serena", "graphify", "playwright", "context7", "omnigraph", "autoos-agent"})
        self.assertTrue(mcp["autoos-agent"]["enabled"])
        self.assertEqual(mcp["autoos-agent"]["type"], "local")
        for name in ("serena", "graphify", "playwright", "context7", "omnigraph"):
            self.assertFalse(mcp[name]["enabled"], name)

    def test_autoos_agent_mcp_pins_the_workers_dir(self):
        # live proof: without AUTOOS_WORKERS_DIR the MCP `ps` call hung for 600 s under opencode (git inherits the stdio pipe)
        cfg, _ = render(self.tmp)
        self.assertEqual(cfg["mcp"]["autoos-agent"]["environment"],
                         {"AUTOOS_WORKERS_DIR": str(self.tmp / "proj" / "logs" / "workers")})
        for name in ("serena", "graphify", "playwright", "context7", "omnigraph"):
            self.assertNotIn("AUTOOS_WORKERS_DIR", cfg["mcp"][name].get("environment", {}))
        with tempfile.TemporaryDirectory(prefix="oc_l1_render4_") as td4:
            cfg4, _ = render(Path(td4), workers_dir=str(Path(td4) / "w"))
            self.assertEqual(cfg4["mcp"]["autoos-agent"]["environment"]["AUTOOS_WORKERS_DIR"], str(Path(td4) / "w"))

    def test_permission_block(self):
        cfg, _ = render(self.tmp)
        self.assertEqual(cfg["permission"], {"bash": "allow", "edit": "allow", "read": "allow", "autoos-agent_*": "allow"})

    def test_plugins_key_is_plural_and_omitted_when_empty(self):
        cfg, _ = render(self.tmp)
        self.assertEqual(cfg["plugins"], [str(self.tmp / "plugins" / "placeholder-guard.js")])
        with tempfile.TemporaryDirectory(prefix="oc_l1_render3_") as td3:
            cfg3, _ = render(Path(td3), plugins=[])
            self.assertNotIn("plugins", cfg3)
            self.assertNotIn("plugin", cfg3)

    def test_only_env_references_no_secret_values(self):
        os.environ["AUTOOS_OMNIROUTE_KEY"] = "sk-TEST-SECRET-DO-NOT-LEAK-000"
        os.environ["AUTOOS_OMNIROUTE_URL"] = "http://127.0.0.1:20128"
        try:
            _, text = render(self.tmp)
        finally:
            os.environ.pop("AUTOOS_OMNIROUTE_KEY", None)
            os.environ.pop("AUTOOS_OMNIROUTE_URL", None)
        self.assertNotIn("sk-TEST-SECRET-DO-NOT-LEAK-000", text)
        self.assertNotIn("http://127.0.0.1:20128", text)
        self.assertIn("{env:AUTOOS_OMNIROUTE_KEY}", text)


if __name__ == "__main__":
    unittest.main()
