import pytest
import yaml

from cao.profiles import (
    ALL_TOOLS,
    CAO_VOCAB,
    LEAF_TOOLS,
    MCP_CAO,
    PROVIDER_BY_POOL,
    SPAWNER_TOOLS,
    VALID_PROVIDERS,
    build_profile,
    generate,
    read_profile,
    render_markdown,
)
from cao.routing import Candidate


class FakeCfg:
    """Levels shaped like the real config: 1 orchestrator, N-2 supervisors, 1 leaf."""

    def __init__(self, max_depth, repo_root="/home/u/code/demo"):
        self.max_depth = max_depth
        self.repo_root = repo_root
        roles = ["orchestrator"] + ["supervisor"] * (max_depth - 2) + ["worker"]
        self.levels = [
            {"level": i + 1, "role": role, "canSpawn": (i + 1) < max_depth}
            for i, role in enumerate(roles)
        ]
        self.routing = {}


GEM = Candidate("antigravity", "google", "gemini-3.8-flash-high")
CLA = Candidate("anthropic", "anthropic", "sonnet", "high")
DS = Candidate("deepseek", "deepseek", "deepseek-v4-flash")






def test_gemini_effort_rides_in_the_model_id_not_a_flag():
    """CAO's _build_agy_command passes --model but never --effort."""
    p = build_profile(3, "worker", False, GEM, max_depth=3)
    assert p["model"] == "gemini-3.8-flash-high"
    assert "claudeConfig" not in p


def test_claude_effort_goes_to_claudeconfig():
    """CAO maps claudeConfig.effort -> --effort (claude_code.py:332)."""
    p = build_profile(2, "supervisor", True, CLA, max_depth=3)
    assert p["claudeConfig"]["effort"] == "high"
    assert p["provider"] == "claude_code"


def test_deepseek_routes_to_the_opencode_provider():
    p = build_profile(3, "worker", False, DS, max_depth=3)
    assert p["provider"] == "opencode_cli"
    assert p["model"] == "deepseek-v4-flash"


def test_profile_states_its_own_depth_in_the_prompt():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "2 of 3" in p["system_prompt"]


def test_non_orchestrator_profiles_forbid_replanning():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "do not re-plan" in p["system_prompt"].lower()


def test_profiles_tell_workers_how_to_push_back_with_evidence():
    p = build_profile(3, "worker", False, GEM, max_depth=3)
    assert "needs_revision" in p["system_prompt"]


def test_leaf_prompt_says_it_is_a_leaf():
    p = build_profile(3, "worker", False, GEM, max_depth=3)
    assert "leaf" in p["system_prompt"].lower()


def test_memory_tools_are_never_granted():
    """Omnigraph is the canonical memory layer; two memory systems confuse agents."""
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "memory_store" not in p["allowedTools"]
    assert "memory_recall" not in p["allowedTools"]


def test_generate_writes_one_profile_per_non_orchestrator_level(tmp_path):
    written = generate(FakeCfg(3), tmp_path)
    assert len(written) == 2  # level 1 is the Claude session, not a CAO profile


def test_generated_profiles_are_valid_yaml_with_stable_names(tmp_path):
    written = generate(FakeCfg(3), tmp_path)
    names = [read_profile(p)["name"] for p in written]
    assert names == ["cao_demo_L2_supervisor", "cao_demo_L3_worker"]


def test_generated_profile_warns_against_hand_editing(tmp_path):
    written = generate(FakeCfg(3), tmp_path)
    prof = read_profile(written[0])
    assert "not hand-edit" in prof["description"].lower()


def test_every_pool_maps_to_a_real_cao_provider_enum_value():
    """Regression: "antigravity" is not a ProviderType member.

    The hand-written agy_*.yaml profiles used it, and CAO silently fell back to
    kiro_cli — launching a completely different agent with no error. An invalid
    provider must fail here, not at 2am in a live session.
    """
    for pool, provider in PROVIDER_BY_POOL.items():
        assert provider in VALID_PROVIDERS, f"{pool} -> {provider!r} is not a ProviderType"


def test_antigravity_uses_the_suffixed_enum_value():
    assert PROVIDER_BY_POOL["antigravity"] == "antigravity_cli"


def test_generated_profiles_declare_valid_providers(tmp_path):
    written = generate(FakeCfg(3), tmp_path)
    for path in written:
        prof = read_profile(path)
        assert prof["provider"] in VALID_PROVIDERS


def test_generate_writes_markdown_not_yaml(tmp_path):
    """CAO's store resolves <name>.md; a .yaml in ~/.cao/profiles is read by nothing."""
    written = generate(FakeCfg(3), tmp_path)
    assert all(p.suffix == ".md" for p in written), [p.name for p in written]


def test_rendered_profile_has_frontmatter_then_the_prompt_as_body():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    text = render_markdown(p)
    assert text.splitlines()[0] == "---"
    assert text.count("---") >= 2
    assert "# SUPERVISOR" in text.split("---", 2)[2]


def test_system_prompt_is_the_body_not_a_frontmatter_key():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    front = yaml.safe_load(render_markdown(p).split("---", 2)[1])
    assert "system_prompt" not in front
    assert front["name"].endswith("_L2_supervisor")






def test_spawning_profile_declares_the_cao_mcp_server():
    sup = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "cao-mcp-server" in sup["mcpServers"]






def test_leaf_on_a_soft_enforcement_provider_is_flagged():
    """Measured: a Gemini leaf told not to spawn can still spawn (D12)."""
    from cao.profiles import leaf_enforcement_warnings

    class Cfg:
        max_depth = 3
        levels = [
            {"level": 1, "role": "orchestrator", "canSpawn": True},
            {"level": 2, "role": "supervisor", "canSpawn": True},
            {"level": 3, "role": "worker", "canSpawn": False, "pool": "antigravity"},
        ]

    warnings = leaf_enforcement_warnings(Cfg())
    assert len(warnings) == 1
    assert "advisory" in warnings[0]


def test_leaf_on_a_hard_enforcement_provider_is_silent():
    from cao.profiles import leaf_enforcement_warnings

    class Cfg:
        max_depth = 3
        levels = [
            {"level": 1, "role": "orchestrator", "canSpawn": True},
            {"level": 2, "role": "supervisor", "canSpawn": True},
            {"level": 3, "role": "worker", "canSpawn": False, "pool": "deepseek"},
        ]

    assert leaf_enforcement_warnings(Cfg()) == []


def test_hard_enforcement_set_excludes_antigravity():
    from cao.profiles import HARD_ENFORCEMENT_PROVIDERS

    assert "antigravity_cli" not in HARD_ENFORCEMENT_PROVIDERS
    assert "opencode_cli" in HARD_ENFORCEMENT_PROVIDERS


# --------------------------------------------------------------------------
# Depth is NOT enforced by the tool surface. Measured: MCP is granted per
# server, so a leaf cannot be given send_message while being denied assign;
# and on antigravity_cli restrictions are advisory anyway. Every level keeps
# @cao-mcp-server; cao/monitor.py does the policing.
# --------------------------------------------------------------------------




def test_leaf_prompt_tells_it_to_report_via_send_message():
    leaf = build_profile(3, "worker", False, GEM, max_depth=3)
    prompt = leaf["system_prompt"]
    assert "send_message" in prompt
    assert "RESULT" in prompt


def test_leaf_prompt_warns_that_a_spawned_child_gets_killed():
    """The honest deterrent: policing is reactive, so say what it costs."""
    leaf = build_profile(3, "worker", False, GEM, max_depth=3)
    assert "terminates" in leaf["system_prompt"]


def test_supervisor_prompt_forbids_blocking_handoff():
    """Measured: a 10-minute worker under handoff took the MCP session down."""
    sup = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "Do NOT use `handoff`" in sup["system_prompt"]
    assert "assign" in sup["system_prompt"]




def test_level_without_a_pool_uses_the_run_default():
    from cao.profiles import candidate_for

    assert candidate_for({"level": 3, "role": "worker"}, GEM) is GEM


def test_level_pool_overrides_the_default():
    from cao.profiles import candidate_for

    got = candidate_for(
        {"level": 2, "role": "supervisor", "pool": "anthropic",
         "family": "anthropic", "model": "sonnet", "effort": "high"},
        GEM,
    )
    assert (got.pool, got.model, got.effort) == ("anthropic", "sonnet", "high")


def test_mixed_hierarchy_generates_different_providers_per_level(tmp_path):
    """Claude supervising DeepSeek is cross-family with no extra dispatch."""

    class Mixed:
        max_depth = 3
        routing = {}
        repo_root = "/home/u/code/mixed"
        levels = [
            {"level": 1, "role": "orchestrator", "canSpawn": True},
            {"level": 2, "role": "supervisor", "canSpawn": True, "pool": "anthropic",
             "family": "anthropic", "model": "sonnet", "effort": "high"},
            {"level": 3, "role": "worker", "canSpawn": False, "pool": "deepseek",
             "family": "deepseek", "model": "deepseek/deepseek-v4-flash"},
        ]

    written = generate(Mixed(), tmp_path)
    sup, worker = (read_profile(p) for p in written)
    assert sup["provider"] == "claude_code"
    assert sup["claudeConfig"]["effort"] == "high"
    assert worker["provider"] == "opencode_cli"


def test_claude_profiles_carry_a_generous_init_timeout():
    """Measured: CAO's 60s default kills a Claude Code terminal during startup."""
    from cao.profiles import PROVIDER_INIT_TIMEOUT

    p = build_profile(2, "supervisor", True, CLA, max_depth=3)
    assert p["provider_init_timeout"] == PROVIDER_INIT_TIMEOUT["claude_code"]
    assert p["provider_init_timeout"] >= 120


def test_every_tui_provider_gets_an_init_timeout():
    from cao.profiles import PROVIDER_BY_POOL, PROVIDER_INIT_TIMEOUT

    for provider in PROVIDER_BY_POOL.values():
        assert provider in PROVIDER_INIT_TIMEOUT, provider


def test_init_timeout_is_written_into_the_generated_profile(tmp_path):
    written = generate(FakeCfg(3), tmp_path)
    assert read_profile(written[0])["provider_init_timeout"] >= 120


# --------------------------------------------------------------------------
# D19: allowedTools names are PROVIDER-SPECIFIC. Measured 2026-09-10 — an
# opencode worker handed the antigravity names ("write_file",
# "run_shell_command") received NO filesystem tools at all and reported back
# that it could only reach cao-mcp-server, skill and todowrite.
# --------------------------------------------------------------------------








# --------------------------------------------------------------------------
# D20: naming tools grants NOTHING, on every provider tried. A claude_code
# supervisor given ("Read","Glob","Grep") reported "no Read tool is available
# in this supervisor session". So every level gets "*" and the delegation rule
# lives in the prompt, where it is at least visible.
# --------------------------------------------------------------------------




def test_supervisor_prompt_still_carries_the_delegation_rule():
    """The rule is prompt-level now, so it had better be in the prompt."""
    sup = build_profile(2, "supervisor", True, CLA, max_depth=3)
    assert "Never write code yourself" in sup["system_prompt"]


# --------------------------------------------------------------------------
# CAO has a UNIVERSAL tool vocabulary that it translates per provider. Using
# provider-NATIVE names maps to nothing, so get_disallowed_tools blocks every
# native tool and the agent is left with MCP + skill + todowrite. That looked
# like "restrictions do not work" twice before the mapping was read.
# --------------------------------------------------------------------------


def test_profiles_speak_caos_vocabulary_not_native_names():
    for prof in (build_profile(2, "supervisor", True, CLA, 3),
                 build_profile(3, "worker", False, DS, 3)):
        for entry in prof["allowedTools"]:
            assert entry.startswith("@") or entry in CAO_VOCAB, entry


def test_no_provider_native_name_leaks_into_a_profile():
    """Read/Bash/write_file/run_shell_command are native, and map to nothing."""
    native = {"Read", "Bash", "Write", "Edit", "Glob", "Grep",
              "read_file", "write_file", "run_shell_command", "replace"}
    for cand in (GEM, CLA, DS):
        for can_spawn in (True, False):
            tools = set(build_profile(2, "r", can_spawn, cand, 3)["allowedTools"])
            assert not (tools & native), tools & native


def test_spawner_can_read_and_search_but_not_write_or_execute():
    """'Delegate, never implement' as an enforced property, not a request."""
    tools = set(build_profile(2, "supervisor", True, CLA, 3)["allowedTools"])
    assert tools == set(SPAWNER_TOOLS)
    assert "fs_read" in tools and "fs_list" in tools
    assert "fs_write" not in tools and "execute_bash" not in tools and "fs_*" not in tools


def test_leaf_can_execute_and_write_because_it_does_the_work():
    tools = set(build_profile(3, "worker", False, DS, 3)["allowedTools"])
    assert tools == set(LEAF_TOOLS)
    assert "execute_bash" in tools and "fs_*" in tools


def test_every_level_keeps_the_mcp_server_for_send_message():
    for can_spawn in (True, False):
        assert MCP_CAO in build_profile(3, "w", can_spawn, GEM, 3)["allowedTools"]


def test_wildcard_is_never_used_now_that_the_vocabulary_is_known():
    """"*" disables all restrictions (get_disallowed_tools returns [])."""
    for can_spawn in (True, False):
        assert ALL_TOOLS not in build_profile(2, "r", can_spawn, CLA, 3)["allowedTools"]


@pytest.mark.parametrize("depth", [2, 3, 4, 5])
def test_generated_leaf_can_work_and_report(tmp_path, depth):
    leaf = read_profile(generate(FakeCfg(depth), tmp_path)[-1])
    assert set(leaf["allowedTools"]) == set(LEAF_TOOLS)


@pytest.mark.parametrize("depth", [3, 4, 5])
def test_generated_spawners_are_read_only(tmp_path, depth):
    for path in generate(FakeCfg(depth), tmp_path)[:-1]:
        assert set(read_profile(path)["allowedTools"]) == set(SPAWNER_TOOLS)


def test_a_soft_enforcement_spawner_is_warned_about():
    from cao.profiles import spawner_restriction_warnings

    class Cfg:
        max_depth = 3
        levels = [{"level": 1, "role": "orchestrator", "canSpawn": True},
                  {"level": 2, "role": "supervisor", "canSpawn": True, "pool": "antigravity"}]

    warns = spawner_restriction_warnings(Cfg())
    assert len(warns) == 1 and "prompt text, not a block" in warns[0]


def test_a_hard_enforcement_spawner_is_silent():
    from cao.profiles import spawner_restriction_warnings

    class Cfg:
        max_depth = 3
        levels = [{"level": 1, "role": "orchestrator", "canSpawn": True},
                  {"level": 2, "role": "supervisor", "canSpawn": True, "pool": "anthropic"}]

    assert spawner_restriction_warnings(Cfg()) == []


def test_supervisor_is_told_to_reverify_not_relay():
    """D21: a worker reported an od -c dump that did not match the real file.

    The work was right; the evidence was confidently wrong, and the supervisor
    relayed it. A supervisor with fs_read has no excuse for relaying a file
    claim it could have checked.
    """
    sup = build_profile(2, "supervisor", True, CLA, max_depth=3)
    prompt = sup["system_prompt"]
    assert "RE-VERIFY" in prompt
    assert "Relaying is not verifying" in prompt


def test_the_supervisor_actually_has_the_tools_to_reverify():
    """Telling it to verify is empty unless fs_read is granted."""
    sup = build_profile(2, "supervisor", True, CLA, max_depth=3)
    assert "fs_read" in sup["allowedTools"]


# --------------------------------------------------------------------------
# Orchestrators are never cheap. A level that spawns decides what work exists,
# who does it, and whether the evidence holds. A weak model there decomposes
# badly and accepts weak evidence, and every layer beneath inherits it.
# --------------------------------------------------------------------------


def _cfg(levels):
    return type("Cfg", (), {"max_depth": 3, "routing": {}, "levels": levels})()


def test_a_spawner_on_a_cheap_model_is_flagged():
    from cao.profiles import orchestrator_model_warnings

    warns = orchestrator_model_warnings(_cfg([
        {"level": 1, "role": "orchestrator", "canSpawn": True},
        {"level": 2, "role": "supervisor", "canSpawn": True, "model": "sonnet"},
    ]))
    assert len(warns) == 1
    assert "not an orchestrator model" in warns[0]


def test_a_spawner_on_opus_is_accepted():
    from cao.profiles import orchestrator_model_warnings

    assert orchestrator_model_warnings(_cfg([
        {"level": 1, "role": "orchestrator", "canSpawn": True},
        {"level": 2, "role": "supervisor", "canSpawn": True, "model": "opus"},
    ])) == []


def test_a_leaf_may_use_any_model():
    """Cheapness belongs where the task is specified and the guard is written."""
    from cao.profiles import orchestrator_model_warnings

    for model in ("sonnet", "gemini-3.8-flash-low", "deepseek-direct/deepseek-flash"):
        assert orchestrator_model_warnings(_cfg([
            {"level": 1, "role": "orchestrator", "canSpawn": True},
            {"level": 2, "role": "supervisor", "canSpawn": True, "model": "opus"},
            {"level": 3, "role": "worker", "canSpawn": False, "model": model},
        ])) == [], model


def test_fable_is_level_one_only():
    """Fable may drive the top session; it is never spawned as a subagent."""
    from cao.profiles import orchestrator_model_warnings

    warns = orchestrator_model_warnings(_cfg([
        {"level": 1, "role": "orchestrator", "canSpawn": True},
        {"level": 2, "role": "supervisor", "canSpawn": True, "model": "claude-fable-5-1"},
    ]))
    assert any("Level-1-only" in w for w in warns)


def test_gemini_pro_counts_as_an_orchestrator_model():
    """The rule is about reasoning strength, not about being Anthropic."""
    from cao.profiles import ORCHESTRATOR_MODELS

    assert "gemini-3.1-pro-high" in ORCHESTRATOR_MODELS
    assert "gemini-3.8-flash-low" not in ORCHESTRATOR_MODELS


def test_the_shipped_config_obeys_the_tier_policy(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from cao.config import load_config
    from cao.profiles import orchestrator_model_warnings

    skill = Path(__file__).resolve().parents[2]
    shutil.copy(skill / "handoff.config.example.json", tmp_path / "handoff.config.json")
    assert orchestrator_model_warnings(load_config(tmp_path)) == []


# --------------------------------------------------------------------------
# Project scoping. CAO's profile store is GLOBAL (one per CAO_HOME_DIR), so an
# unscoped cao_L2_supervisor from one repo silently overwrites another's and
# the next launch runs the wrong prompt and model.
# --------------------------------------------------------------------------


def test_profile_names_carry_the_project():
    from cao.profiles import profile_name

    assert profile_name("agent-skills", 2, "supervisor") == "cao_agent-skills_L2_supervisor"


def test_two_projects_do_not_collide():
    from cao.profiles import profile_name

    assert profile_name("alpha", 2, "supervisor") != profile_name("beta", 2, "supervisor")


def test_a_project_name_is_made_store_safe():
    """CAO restricts profile names to [A-Za-z0-9_-] with a 64-char cap."""
    import re

    from cao.profiles import profile_name, project_slug

    assert project_slug("My Project! v2") == "My-Project-v2"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", profile_name("a/b c:d", 2, "supervisor"))
    assert len(project_slug("x" * 200)) <= 32


def test_an_unusable_project_name_falls_back_rather_than_producing_an_empty_one():
    from cao.profiles import project_slug

    assert project_slug("...") == "project"
    assert project_slug("") == "project"


def test_generated_profiles_are_scoped_to_the_repo(tmp_path):
    class Cfg:
        max_depth = 3
        routing = {}
        repo_root = "/home/u/code/my-repo"
        levels = [
            {"level": 1, "role": "orchestrator", "canSpawn": True},
            {"level": 2, "role": "supervisor", "canSpawn": True},
            {"level": 3, "role": "worker", "canSpawn": False},
        ]

    names = [read_profile(p)["name"] for p in generate(Cfg(), tmp_path)]
    assert names == ["cao_my-repo_L2_supervisor", "cao_my-repo_L3_worker"]


def test_the_leaf_prompt_bans_destructive_operations():
    """A leaf holds an unrestricted shell; the prompt is the only brake on intent."""
    leaf = build_profile(3, "worker", False, GEM, max_depth=3)["system_prompt"]
    for forbidden in ("force-push", "rm -rf", "rewrite", "main checkout"):
        assert forbidden in leaf, forbidden


def test_the_leaf_prompt_forbids_weakening_the_guard():
    leaf = build_profile(3, "worker", False, GEM, max_depth=3)["system_prompt"]
    assert "weaken the acceptance guard" in leaf
    assert "Layer 1 runs" in leaf


# ---------------------------------------------------------------------------
# What an agent is TAUGHT to emit and what Layer 1 PARSES must be one format.
# `cao verify --terminal` reads the RESULT envelope to catch a false
# "status=ok"; an envelope it cannot parse counts as no claim at all, so a
# drifted example here would silently disable the lie detector rather than
# fail anything. The example is therefore rendered by `wire.render_result`.
# ---------------------------------------------------------------------------

from cao import wire


def _worker_prompt():
    return build_profile(3, "worker", False, CLA, max_depth=3)["system_prompt"]


def test_the_taught_envelope_parses():
    body = _worker_prompt()
    example = body.split("```")[1].strip()
    parsed = wire.parse_result(example)
    assert parsed["task_id"] == "p3"
    assert parsed["status"] == "ok"


def test_a_supervisor_is_taught_the_same_format():
    body = build_profile(2, "supervisor", True, CLA, max_depth=3)["system_prompt"]
    assert wire.parse_result(body.split("```")[1].strip())["status"] == "ok"


def test_the_prompt_lists_the_statuses_the_parser_accepts():
    body = _worker_prompt()
    for status in wire.STATUSES:
        assert status in body, status


def test_the_prompt_says_ok_means_the_guard_passed():
    """"ok" must not be readable as "I believe I am finished"."""
    body = _worker_prompt()
    assert "guard PASSED" in body
    assert "contradiction" in body
