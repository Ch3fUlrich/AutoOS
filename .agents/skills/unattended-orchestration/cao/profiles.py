"""Generate CAO agent profiles from config, so depth is real rather than asked for.

CAO has **no spawn-depth limit**. Its only ``depth`` parameter clamps the prefix
used by ``list_siblings``; nothing bounds nesting. Depth has to be built here.

Depth cannot be enforced through the tool surface, and this module says so rather
than pretending otherwise. Two measurements on 2026-09-10 closed that door:

* ``cao profile validate`` warns that ``assign`` is "not in CAO's recognized
  vocabulary" — MCP access is granted per SERVER (``@cao-mcp-server``), never per
  tool, so a leaf cannot be handed ``send_message`` while being denied ``assign``.
* ``antigravity_cli`` sits in CAO's ``SOFT_ENFORCEMENT_PROVIDERS``
  (``terminal_service.py:211``), whose restrictions are "prompt-level text only
  ... advisory, not enforced".

So every generated profile keeps ``@cao-mcp-server`` (a leaf needs
``send_message`` to report), the prompt states the level's position, and actual
policing happens out of band in :mod:`cao.monitor`, which walks the server-side
``caller_id`` chain and terminates anything deeper than ``maxDepth``.

Tool RESTRICTIONS, as opposed to depth, are enforceable — through CAO's universal
vocabulary rather than provider-native names. See :data:`CAO_VOCAB`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from cao import wire

#: CAO's UNIVERSAL tool vocabulary (utils/tool_mapping.py). CAO translates these
#: to each provider's native names; provider-native names are NOT recognised.
#:
#: This cost two wrong diagnoses. Listing native names ("write_file",
#: "Read") maps to nothing, so `get_disallowed_tools` computes "block every
#: native tool" and the agent ends up with only MCP + skill + todowrite. It looks
#: exactly like "restrictions do not work", and it is really "you spoke the wrong
#: language". The escape hatch is literal "*", which returns an empty block list.
CAO_VOCAB = ("execute_bash", "fs_read", "fs_write", "fs_list", "fs_*", "web_fetch")

MCP_CAO = "@cao-mcp-server"
ALL_TOOLS = "*"

#: A SPAWNER may read and search, never write or execute. On a hard-enforcement
#: provider (claude_code, opencode_cli, grok_cli, kiro_cli, copilot_cli) this
#: makes "delegate, never implement" an enforced property rather than a request.
SPAWNER_TOOLS = (MCP_CAO, "fs_read", "fs_list")

#: A LEAF does the work, so it needs execution and writes.
LEAF_TOOLS = (MCP_CAO, "execute_bash", "fs_*", "web_fetch")

#: pool -> CAO ProviderType VALUE (models/provider.py). These strings must match
#: the enum exactly. Measured 2026-09-10: the hand-written agy_*.yaml profiles
#: declared "antigravity", which is not a member — CAO silently fell back to
#: kiro_cli and launched the wrong agent with no error anywhere.
PROVIDER_BY_POOL = {
    "anthropic": "claude_code",
    "antigravity": "antigravity_cli",
    "deepseek": "opencode_cli",
    "muse": "mcode",
    "openrouter": "opencode_cli",
}

#: The full enum, duplicated here so the test can catch drift without importing
#: CAO (which is not installed on the machine that runs these unit tests).
VALID_PROVIDERS = frozenset({
    "kiro_cli", "claude_code", "codex", "kimi_cli", "copilot_cli", "opencode_cli",
    "hermes", "cursor_cli", "antigravity_cli", "omp", "grok_cli", "mcode", "mock_cli",
})

#: Providers whose tool restrictions CAO can actually enforce. The complement is
#: SOFT_ENFORCEMENT_PROVIDERS (terminal_service.py:211), documented upstream as
#: "prompt-level text only (no native blocking mechanism)".
HARD_ENFORCEMENT_PROVIDERS = frozenset(
    {"claude_code", "opencode_cli", "grok_cli", "kiro_cli", "copilot_cli"}
)

#: Seconds CAO waits for a provider's TUI to reach idle. Measured: Claude Code
#: needs more than CAO's 60s default in WSL — it loads MCP servers at startup —
#: and the failure deletes the terminal.
PROVIDER_INIT_TIMEOUT = {
    "claude_code": 300,
    "antigravity_cli": 300,
    "opencode_cli": 180,
    "mcode": 180,
}

#: Models fit to ORCHESTRATE. A level that spawns is deciding what work exists,
#: who does it, and whether the evidence holds — the judgement is the job. Put a
#: cheap model there and it decomposes badly, accepts weak evidence, and every
#: layer beneath it inherits the mistake. Cheapness belongs at the leaves, where
#: the task is already specified and the guard is already written.
#:
#: Level 1 (this Claude Code session) may be Fable 5.1 or Opus 5. Fable is a
#: LEVEL-1-ONLY model: never spawn one as a subagent.
ORCHESTRATOR_MODELS = frozenset(
    {"opus", "claude-opus-5", "claude-opus-4-6-thinking", "gemini-3.1-pro-high"}
)

#: Level 1 only. Never below.
LEVEL_1_ONLY_MODELS = frozenset({"claude-fable-5-1", "fable"})

#: Anything may run at a leaf: the work is bounded and checked by a guard.
LEAF_MODELS_ANY = True


def orchestrator_model_warnings(cfg) -> list:
    """Flag a spawning level on a model too weak to orchestrate with.

    Deliberately a hard-sounding warning rather than a silent default: a
    supervisor on a cheap model still *runs*, so nothing fails — it just
    delegates worse, and the cost surfaces three layers down as rework.
    """
    out = []
    for level_spec in cfg.levels:
        level = level_spec["level"]
        model = level_spec.get("model")
        if not model:
            continue
        if level > 1 and model in LEVEL_1_ONLY_MODELS:
            out.append(
                f"level {level} uses {model!r}, which is a Level-1-only model — "
                "never spawn it as a subagent"
            )
        if level_spec.get("canSpawn") and level > 1 and model not in ORCHESTRATOR_MODELS:
            out.append(
                f"level {level} SPAWNS on {model!r}, which is not an orchestrator "
                f"model. A level that delegates must reason well: use one of "
                f"{sorted(ORCHESTRATOR_MODELS)}. Cheap models belong at the leaves."
            )
    return out


_ROLE_DUTY = {
    "supervisor": (
        "Decompose your phase into concrete tasks and delegate every one of them. "
        "Never write code yourself. "
        "RE-VERIFY every worker claim you are able to check yourself. You have "
        "fs_read and fs_list: if a worker says a file exists or has certain "
        "contents, Read it before relaying. Measured 2026-09-10: a worker "
        "correctly created a file and then reported a byte-level `od -c` dump "
        "that did not match it (it claimed a trailing newline the file did not "
        "have), and the supervisor relayed that verbatim. Confident, specific, "
        "wrong evidence is the failure mode you exist to catch. Relaying is not "
        "verifying."
    ),
    "worker": (
        "Execute exactly the TASK you were given and report evidence for it. "
        "You are in a DISPOSABLE worktree. Stay in it: never touch the main "
        "checkout, never `git push`, never force-push, never delete a branch, "
        "never `rm -rf` outside your own working directory, and never rewrite "
        "history. If the task seems to require any of those, it is not your "
        "call — emit RESULT status=needs_revision and stop. "
        "Never edit or weaken the acceptance guard to make it pass. Layer 1 runs "
        "that guard itself afterwards, so a claim that disagrees with an exit "
        "code loses, and the phase comes back to you as rework."
    ),
    "reviewer": (
        "Review the code you are given, run its guard, and return APPROVED or "
        "NEEDS_REVISION with specific issues."
    ),
}


#: CAO's profile store restricts names to [A-Za-z0-9_-] with a 64-char cap.
_NAME_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def project_slug(name) -> str:
    """A project name safe to embed in a CAO profile or session name."""
    slug = _NAME_SAFE.sub("-", str(name)).strip("-")
    return slug[:32] or "project"


def profile_name(project, level: int, role: str) -> str:
    """``cao_<project>_L<n>_<role>``.

    The project segment is not decoration. CAO's profile store is GLOBAL — one
    agent store per CAO_HOME_DIR, shared by every repository — so an unscoped
    ``cao_L2_supervisor`` from one project silently overwrites another's, and
    the next launch runs the wrong prompt and the wrong model. It also makes a
    busy `cao session list` readable when several projects run at once.
    """
    return f"cao_{project_slug(project)}_L{level}_{role}"


def _prompt(level: int, role: str, can_spawn: bool, max_depth: int) -> str:
    lines = [
        f"# {role.upper()} — Layer {level} of {max_depth}",
        "",
        f"You are at depth {level} of {max_depth} in a CAO orchestration hierarchy.",
        "",
        "## The plan is final",
        "A PLAN.lock.json contract was agreed with the user before you were spawned.",
        "Do not re-plan and do not re-open scope. If you find evidence that the plan",
        "is wrong, emit `RESULT <id> status=needs_revision` with the contradicting",
        "evidence and stop — the Layer 1 orchestrator decides, not you.",
        "",
        "## Your duty",
        _ROLE_DUTY.get(role, _ROLE_DUTY["worker"]),
        "",
        "## Messages",
        "Reply only with a RESULT envelope. Never re-quote the plan — reference",
        "plan_ref. Never paste more than 20 lines of a file — reference path:line.",
        "",
        "Exactly this shape — Layer 1 PARSES it, and an envelope it cannot read",
        "counts as no report at all:",
        "",
        "```",
        # Rendered by the same function that defines the format, so what an agent
        # is taught and what `wire.parse_result` accepts cannot drift apart. A
        # hand-written example here would drift on the first format change, and
        # the failure is silent: an unparsable envelope makes `compare_claim`
        # return "no-claim", so a false "status=ok" would never be caught.
        wire.render_result(
            task_id="p3",
            status="ok",
            evidence="pytest -q -> 41 passed (exit 0)",
            files_changed=["src/loader.py", "tests/test_loader.py"],
            notes="fixture path was absolute; made it relative to the worktree",
            cost={"tokens": 18400},
        ).rstrip(),
        "```",
        "",
        "`status` is one of: " + ", ".join(wire.STATUSES) + ". Use `ok` only when",
        "the acceptance guard PASSED when you ran it. Layer 1 runs it again, and a",
        "claim that disagrees with an exit code is recorded as a contradiction",
        "against every phase you touched — not just this one.",
        "",
        "## Memory",
        "Use the omnigraph MCP tools. Do NOT use memory_store / memory_recall.",
    ]
    if can_spawn:
        lines += [
            "",
            "## Delegation",
            "Spawn workers with `assign` (async). Do NOT use `handoff`: it blocks",
            "your MCP call for the worker's whole run, and measured on 2026-09-10 a",
            "10-minute worker timed out the connection and took the whole session's",
            "MCP tools down with it.",
            "After `assign`, wait for each worker's `send_message` callback.",
            "The TASK's `use_pool:` and `use_model:` lines are not advice. Pass",
            "`use_model` VERBATIM as the model id - it is already the exact id the",
            "provider expects, and prefixing it with the pool makes it a model that",
            "does not exist. `use_pool` names the provider, never the model.",
            "Layer 1 chose both from the task class and the pools that are open,",
            "and it knows which are cooling; substituting a model you prefer",
            "spends quota Layer 1 was deliberately saving.",
            f"Your children are at depth {level + 1} of {max_depth}.",
        ]
    else:
        lines += [
            "",
            "## You are a leaf",
            f"You are at the configured maximum depth ({max_depth}). Do NOT call",
            "`assign` or `handoff` — do all of your own work.",
            "A monitor terminates any terminal deeper than the maximum, so a child",
            "you spawn will be killed mid-work and its tokens wasted.",
            "Report by calling `send_message` back to your supervisor with your",
            "RESULT envelope as the body, then STOP.",
        ]
    return "\n".join(lines)


def build_profile(
    level: int, role: str, can_spawn: bool, candidate, max_depth: int, project=None
) -> dict:
    # A spawner is read-only and delegates writes; a leaf does the actual work.
    # Giving a supervisor write tools is how "never write code yourself" quietly
    # stops being true.
    # Every level keeps @cao-mcp-server. A leaf needs `send_message` to report
    # asynchronously, and CAO grants MCP per SERVER, not per tool — there is no
    # way to hand it send_message while withholding assign. Depth is therefore
    # policed by cao/monitor.py, not by this tool surface; see that module for
    # what that costs.
    provider = PROVIDER_BY_POOL[candidate.pool]
    tools = list(SPAWNER_TOOLS if can_spawn else LEAF_TOOLS)

    profile = {
        "name": profile_name(project or "project", level, role),
        "description": (
            f"Generated by cao/profiles.py — layer {level} of {max_depth}, "
            f"{'can spawn' if can_spawn else 'leaf'}. Do not hand-edit; "
            "regenerate from handoff.config.json instead."
        ),
        "role": role,
        "provider": provider,
        "model": candidate.model,
        "allowedTools": tools,
        "system_prompt": _prompt(level, role, can_spawn, max_depth),
    }
    provider = profile["provider"]
    if provider in PROVIDER_INIT_TIMEOUT:
        profile["provider_init_timeout"] = PROVIDER_INIT_TIMEOUT[provider]
    # THE ENFORCEMENT POINT. A leaf gets no cao-mcp-server, so `assign` and
    # `handoff` do not exist in its terminal at all.
    #
    # allowedTools alone is NOT enough, measured 2026-09-10: antigravity_cli is
    # in CAO's SOFT_ENFORCEMENT_PROVIDERS (terminal_service.py:211), so its tool
    # restrictions are "prompt-level text only ... advisory, not enforced". A
    # Gemini leaf told not to spawn could still spawn. Withholding the MCP
    # server is provider-independent: the tool is not wired, so there is nothing
    # to ignore.
    #
    # The cost is that a leaf also loses send_message, so leaves must be invoked
    # with `handoff` (blocking), which returns their output to the supervisor
    # directly. The shipped agy_reviewer profile already works this way.
    profile["mcpServers"] = {
        "cao-mcp-server": {"type": "stdio", "command": "cao-mcp-server", "args": []}
    }
    # Effort: Claude Code takes a flag (claudeConfig.effort -> --effort). `agy`
    # does not, so Gemini effort must already be encoded in the model id
    # (…-high / -medium / -low) — see cao/routing.py.
    if candidate.effort and candidate.pool == "anthropic":
        profile["claudeConfig"] = {"effort": candidate.effort}
    return profile


def render_markdown(profile: dict) -> str:
    """CAO's on-disk profile format: YAML frontmatter + the prompt as the body.

    Measured 2026-09-10: CAO's profile store resolves ``<name>.md`` under
    ``LOCAL_AGENT_STORE_DIR`` (~/.cao/agent-store). A ``.yaml`` written to
    ``~/.cao/profiles/`` is read by nothing — the setup script had been placing
    copies there, and `cao profile list` never showed them.
    """
    front = {k: v for k, v in profile.items() if k != "system_prompt"}
    body = profile.get("system_prompt", "")
    frontmatter = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


def candidate_for(level_spec, default):
    """Per-level model, falling back to the run default.

    Levels carry their own pool/model so a hierarchy can be genuinely mixed —
    judgement at the top on one family, cheap execution below on another. That is
    also what makes cross-family review free: a Claude supervisor reviewing a
    DeepSeek worker is already cross-family, with no extra dispatch.
    """
    from cao.routing import Candidate

    pool = level_spec.get("pool")
    if not pool:
        return default
    return Candidate(
        pool,
        level_spec.get("family", pool),
        level_spec.get("model", default.model),
        level_spec.get("effort"),
    )


def generate(cfg, out_dir, candidate=None, project=None) -> list:
    """Write one profile per level below the orchestrator, as CAO ``.md`` files.

    Level 1 is the human-facing orchestrator (this Claude session); it holds the
    plan contract and is never a CAO profile.
    """
    from cao.routing import Candidate

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    default = candidate or Candidate("antigravity", "google", "gemini-3.8-flash-high")

    written = []
    for level_spec in cfg.levels:
        if level_spec["level"] == 1:
            continue
        profile = build_profile(
            level_spec["level"],
            level_spec["role"],
            bool(level_spec.get("canSpawn")),
            candidate_for(level_spec, default),
            cfg.max_depth,
            project=project or Path(str(cfg.repo_root)).name,
        )
        path = out / f"{profile['name']}.md"
        path.write_text(render_markdown(profile), encoding="utf-8")
        written.append(path)
    return written


def read_profile(path) -> dict:
    """Parse a generated ``.md`` profile back into a dict (frontmatter only)."""
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path}: not a CAO profile (no frontmatter)")
    _, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter)


#: Providers whose tool restrictions CAO can actually enforce. The complement is
#: ``SOFT_ENFORCEMENT_PROVIDERS`` (terminal_service.py:211), documented upstream as
#: "prompt-level text only (no native blocking mechanism) — a restricted policy on
#: these is advisory, not enforced".


def leaf_enforcement_warnings(cfg) -> list:
    """Warn when a leaf level would run on a provider that cannot enforce limits.

    Measured 2026-09-10: on ``antigravity_cli`` a leaf told not to spawn can still
    spawn, because its restrictions are advisory. Depth is only a rule on a
    hard-enforcement provider; anywhere else it needs external policing (poll
    /sessions and terminate terminals deeper than maxDepth).
    """
    out = []
    for level_spec in cfg.levels:
        if level_spec.get("canSpawn") or level_spec["level"] == 1:
            continue
        pool = level_spec.get("pool")
        if pool is None:
            continue
        provider = PROVIDER_BY_POOL.get(pool)
        if provider and provider not in HARD_ENFORCEMENT_PROVIDERS:
            out.append(
                f"level {level_spec['level']} is a leaf on pool {pool!r} "
                f"({provider}), which CAO cannot enforce tool restrictions on — "
                "the depth limit there is advisory, not a rule"
            )
    return out


def spawner_restriction_warnings(cfg) -> list:
    """Warn only where a spawner's read-only restriction is NOT enforced.

    On a hard-enforcement provider the restriction is real. On a soft one
    (antigravity_cli and friends) CAO injects it as prompt text, so it is a
    request — worth saying, because a control you believe you have is worse than
    one you know you lack.
    """
    out = []
    for level_spec in cfg.levels:
        if level_spec["level"] == 1 or not level_spec.get("canSpawn"):
            continue
        provider = PROVIDER_BY_POOL.get(level_spec.get("pool"))
        if provider and provider not in HARD_ENFORCEMENT_PROVIDERS:
            out.append(
                f"level {level_spec['level']} spawns on {provider!r}, which CAO "
                "cannot enforce tool restrictions on — its read-only limit is "
                "prompt text, not a block; watch it in the dashboard."
            )
    return out
