#!/usr/bin/env python3
"""Generate agent-harness config from catalog/agent-harness.json.

One generator, used by both installers. Pure functions plus a small CLI:

    check            validate the harness file
    pin SERVER       print the pinned package for one MCP server
    serena-excluded  print Serena's excluded tools, one per line
    opencode         merge the harness into an OpenCode config
    vendor           render the vendored OpenHands agent profiles
    openhands        merge the harness into an installed ~/.openhands tree

Only the standard library is used, and nothing here touches the network.
"""
import argparse
import copy
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_SCHEMA = "https://opencode.ai/config.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HARNESS = REPO_ROOT / "catalog" / "agent-harness.json"

TOP_LEVEL_KEYS = ("version", "rules", "fences", "mcp_servers", "roles")
MANAGED_AGENT_KEYS = ("description", "mode", "model", "permission", "tools")


SPAWNER = "autoos-agent"  # tools/autoos_agent_mcp.py: only spawning roles may list it


def load_harness(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------- check


def validate(harness):
    """Return a list of human-readable problems; empty means valid."""
    if not isinstance(harness, dict):
        return ["harness is not a JSON object"]
    problems = []
    for key in TOP_LEVEL_KEYS:
        if key not in harness:
            problems.append("missing key: %s" % key)

    fences = harness.get("fences")
    fences = fences if isinstance(fences, dict) else {}
    for key in ("bash_deny_all", "bash_deny_leaf", "bash_allow_all"):
        if key not in fences:
            problems.append("missing key: fences.%s" % key)
    # Push is a leaf fence, not a global one (operator 2026-09-22): leaves
    # must never push, but the interactive top-level session and spawning
    # roles may. It must live in exactly one fence list.
    _push_all = "git push*" in (fences.get("bash_deny_all") or [])
    _push_leaf = "git push*" in (fences.get("bash_deny_leaf") or [])
    if _push_all == _push_leaf:
        problems.append("fences: 'git push*' must be in exactly one of bash_deny_all / bash_deny_leaf")
    if "git commit*" not in (fences.get("bash_deny_leaf") or []):
        problems.append("fences.bash_deny_leaf must contain 'git commit*'")

    servers = harness.get("mcp_servers")
    servers = servers if isinstance(servers, dict) else {}
    for name, server in servers.items():
        if not isinstance(server, dict) or not isinstance(server.get("package"), str):
            problems.append("mcp_servers.%s.package must be a string" % name)
    serena = servers.get("serena")
    serena = serena if isinstance(serena, dict) else {}
    memory_tools = serena.get("memory_tools")
    # Exact set, not just non-empty: the opencode repo config and both
    # installer writers derive their serena_* tools block from this field,
    # so a typo here silently disables the wrong tools everywhere.
    if set(memory_tools or []) != {
        "write_memory",
        "read_memory",
        "list_memories",
        "edit_memory",
        "delete_memory",
        "rename_memory",
    } or not isinstance(memory_tools, list):
        problems.append(
            "mcp_servers.serena.memory_tools must be exactly the 6 memory tools"
        )

    roles = harness.get("roles")
    roles = roles if isinstance(roles, dict) else {}
    profiles = {}
    any_spawn = False
    for name, role in roles.items():
        if not isinstance(role, dict):
            problems.append("roles.%s must be an object" % name)
            continue
        if "description" not in role:
            problems.append("roles.%s.description is missing" % name)
        for field in ("spawn", "leaf"):
            if not isinstance(role.get(field), bool):
                problems.append("roles.%s.%s must be a boolean" % (name, field))
        opencode = role.get("opencode")
        if not isinstance(opencode, dict):
            problems.append("roles.%s.opencode must be an object" % name)
        else:
            for field in ("mode", "model"):
                if field not in opencode:
                    problems.append("roles.%s.opencode.%s is missing" % (name, field))
        openhands = role.get("openhands")
        if not isinstance(openhands, dict):
            problems.append("roles.%s.openhands must be an object" % name)
        else:
            for field in ("profile", "llm_profile_ref"):
                if field not in openhands:
                    problems.append("roles.%s.openhands.%s is missing" % (name, field))
            profile = openhands.get("profile")
            if isinstance(profile, str):
                if profile in profiles:
                    problems.append(
                        "openhands.profile %s used by both %s and %s"
                        % (profile, profiles[profile], name)
                    )
                else:
                    profiles[profile] = name
        mcp = role.get("mcp")
        if not isinstance(mcp, list):
            problems.append("roles.%s.mcp must be a list" % name)
        else:
            for server in mcp:
                if server not in servers:
                    problems.append("roles.%s.mcp references unknown server %s" % (name, server))
            if role.get("spawn") is not True and SPAWNER in mcp:
                problems.append("roles.%s cannot spawn but lists the %s MCP server" % (name, SPAWNER))
        if role.get("leaf") is True and role.get("spawn") is not False:
            problems.append("roles.%s is a leaf and must have spawn: false" % name)
        if role.get("spawn") is True:
            any_spawn = True
    if not any_spawn:
        problems.append("no role has spawn: true")
    return problems


def cmd_check(args):
    try:
        harness = load_harness(args.harness)
    except (OSError, ValueError) as exc:
        print("agent-harness: cannot read %s: %s" % (args.harness, exc))
        return 1
    problems = validate(harness)
    if problems:
        for problem in problems:
            print("agent-harness: %s" % problem)
        return 1
    print("agent-harness: ok")
    return 0


# --------------------------------------------------------------------------- pin


def cmd_pin(args):
    harness = load_harness(args.harness)
    servers = harness.get("mcp_servers") or {}
    server = servers.get(args.server)
    if not isinstance(server, dict) or not isinstance(server.get("package"), str):
        print("agent-harness: unknown server: %s" % args.server, file=sys.stderr)
        return 1
    print(server["package"])
    return 0


def cmd_serena_excluded(args):
    harness = load_harness(args.harness)
    serena = (harness.get("mcp_servers") or {}).get("serena") or {}
    for tool in serena.get("excluded_tools") or []:
        print(tool)
    return 0


# --------------------------------------------------------------------------- opencode


def _unexpected_type(user):
    """Return a reason string if the user document has a shape we cannot merge."""
    if not isinstance(user, dict):
        return "top-level value is not an object"
    permission = user.get("permission")
    if permission is not None and not isinstance(permission, dict):
        return "permission is not an object"
    if isinstance(permission, dict):
        for key in ("bash", "read"):
            if key in permission and not isinstance(permission[key], (str, dict)):
                return "permission.%s is neither a string nor an object" % key
    if "instructions" in user and not isinstance(user["instructions"], list):
        return "instructions is not a list"
    agent = user.get("agent")
    if agent is not None and not isinstance(agent, dict):
        return "agent is not an object"
    if isinstance(agent, dict):
        for name, block in agent.items():
            if not isinstance(block, dict):
                return "agent.%s is not an object" % name
    return None


def _pattern_map(value):
    """A permission.bash/read string v counts as {"*": v}."""
    if isinstance(value, str):
        return {"*": value}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _rebuild_patterns(user_value, deny_all, deny_leaf, allow_all):
    """Rebuild a bash/read pattern map: "*" first, user's own patterns next, fences last.

    The harness owns every pattern in its fence lists, so a user value for one of
    those is dropped here and re-emitted with the fence's verdict. That is what
    makes the fence win over a user's `allow`.
    """
    user_map = _pattern_map(user_value)
    owned = set(deny_all) | set(deny_leaf) | set(allow_all)
    result = {"*": user_map.get("*", "allow")}
    for pattern, value in user_map.items():
        if pattern == "*" or pattern in owned:
            continue
        result[pattern] = value
    for pattern in deny_all:
        result[pattern] = "deny"
    for pattern in deny_leaf:
        result[pattern] = "deny"
    for pattern in allow_all:
        result[pattern] = "allow"
    return result


def _drop_stale_leaf_denies(user_value, retired):
    """Remove a previous harness version's leaf-deny footprint.

    deny_leaf patterns are no longer fenced at top level (they live on the
    leaf agent blocks). A top-level leaf pattern with verdict "deny" can only
    be this generator's own stale output, so drop it; any other user verdict
    (ask/allow) survives untouched.
    """
    user_map = _pattern_map(user_value)
    for pattern in retired:
        if user_map.get(pattern) == "deny":
            del user_map[pattern]
    return user_map


def _rebuild_read(user_value, deny_all, allow_all):
    return _rebuild_patterns(user_value, deny_all, [], allow_all)


def _join(base, *parts):
    """Join path parts with '/', never a backslash, on every platform."""
    pieces = [str(base).rstrip("/")]
    for part in parts:
        pieces.append(str(part).strip("/"))
    return "/".join(piece for piece in pieces if piece)


def _role_bash(role, fences):
    bash = {"*": "allow"}
    for pattern in fences["bash_deny_all"]:
        bash[pattern] = "deny"
    leaf = bool(role.get("leaf"))
    for pattern in fences["bash_deny_leaf"]:
        bash[pattern] = "deny" if leaf else "allow"
    for pattern in fences.get("bash_allow_all") or []:
        bash[pattern] = "allow"
    return bash


def desired_opencode(user, harness, repo_root, skills_source):
    """Return the merged OpenCode config; `user` is the parsed existing config."""
    doc = copy.deepcopy(user)
    fences = harness["fences"]
    deny_all = list(fences["bash_deny_all"])
    deny_leaf = list(fences["bash_deny_leaf"])
    allow_all = list(fences.get("bash_allow_all") or [])

    if "$schema" not in doc:
        doc["$schema"] = DEFAULT_SCHEMA

    permission = doc.setdefault("permission", {})
    if "skill" not in permission:
        permission["skill"] = "allow"
    # Top level gets deny_all ONLY. The leaf fences (commit/checkout/stash/…)
    # are per-agent (see _role_bash): the top-level session is the interactive
    # user, and the V2 tier agents inherit top-level bash since they define no
    # bash rules of their own — denying commit/checkout there breaks everyday
    # git inside any agent run (measured 2026-09-22). Leaves still deny via
    # their own blocks; push/merge/destructive stays denied for everyone.
    permission["bash"] = _rebuild_patterns(
        _drop_stale_leaf_denies(permission.get("bash"), deny_leaf),
        deny_all,
        [],
        allow_all,
    )
    permission["read"] = _rebuild_read(
        permission.get("read"),
        list(fences["read_deny_all"]),
        list(fences["read_allow_all"]),
    )

    instructions = list(doc.get("instructions") or [])
    for skill in harness["rules"]["skills"]:
        entry = _join(skills_source, skill, "SKILL.md")
        if entry not in instructions:
            instructions.append(entry)
    leaf_contract = _join(repo_root, harness["rules"]["leaf_contract"])
    if leaf_contract not in instructions:
        instructions.append(leaf_contract)
    doc["instructions"] = instructions

    agent = doc.setdefault("agent", {})
    servers = list((harness.get("mcp_servers") or {}).keys())
    for name, role in harness["roles"].items():
        existing = agent.get(name)
        existing = existing if isinstance(existing, dict) else {}
        block = {k: v for k, v in existing.items() if k not in MANAGED_AGENT_KEYS}
        block["description"] = role["description"]
        block["mode"] = role["opencode"]["mode"]
        block["model"] = role["opencode"]["model"]
        block["permission"] = {
            "task": "allow" if role.get("spawn") else "deny",
            "bash": _role_bash(role, fences),
        }
        role_mcp = set(role.get("mcp") or [])
        tools = {"%s*" % server: False for server in servers if server not in role_mcp}
        if tools:
            block["tools"] = tools
        agent[name] = block
    return doc


def _is_junction(path):
    if os.name != "nt":
        return False
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            return False
        # Reparse-point flag, not a realpath-vs-abspath comparison: the
        # latter misfires when TEMP carries 8.3 short names (RUNNER~1 on
        # CI runners), where realpath returns the long form and every
        # plain directory looks like a junction.
        import stat

        return bool(
            os.lstat(path).st_file_attributes
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    except OSError:
        return False


def _link_target(path):
    try:
        target = os.readlink(path)
    except OSError:
        return os.path.realpath(path)
    # Junction readlink targets carry a device prefix (\\?\ or \??\);
    # strip it so target comparisons see a plain absolute path.
    for prefix in ('\\\\?\\', '\\??\\'):
        if target.startswith(prefix):
            return target[len(prefix):]
    return target


def _same_target(target, link_path, source):
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link_path), target)
    # Fully resolve both sides: TEMP may use 8.3 short names, so a raw
    # normcase comparison sees long-vs-short as different targets.
    return os.path.normcase(os.path.realpath(target)) == os.path.normcase(
        os.path.realpath(source)
    )


def _remove_link(path):
    if _is_junction(path):
        os.rmdir(path)
    else:
        os.unlink(path)


def _create_link(source, link_path):
    os.makedirs(os.path.dirname(os.path.abspath(link_path)), exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(source, link_path)
    else:
        os.symlink(source, link_path)


def _link_plan(link_path, source):
    """Return (needs_change, note) for the skills link."""
    if not os.path.exists(source):
        return False, "skills: source missing"
    if os.path.islink(link_path) or _is_junction(link_path):
        if _same_target(_link_target(link_path), link_path, source):
            return False, None
        return True, None
    if os.path.lexists(link_path):
        return False, "skills: left alone (not a link)"
    return True, None


def _print_notes(notes):
    for note in notes:
        print("  note: %s" % note)


def _backup_and_write(path, text):
    """Write `text` to `path`, copying an existing file aside first (hard rule 5)."""
    if os.path.exists(path):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(path, "%s.autoos-backup-%s" % (path, stamp))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def cmd_opencode(args):
    harness = load_harness(args.harness)
    config_path = args.config
    existed = os.path.exists(config_path)
    if existed:
        # utf-8-sig: a BOM written by an editor must not make the file "invalid".
        with open(config_path, encoding="utf-8-sig") as handle:
            raw = handle.read()
        try:
            user = json.loads(raw)
        except ValueError:
            print("agent-harness opencode: left alone, not valid JSON")
            return 0
        reason = _unexpected_type(user)
        if reason:
            print("agent-harness opencode: left alone, %s" % reason)
            return 0
    else:
        user = {}

    desired = desired_opencode(user, harness, args.repo_root, args.skills_source)
    link_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), "skills")
    link_needs_change, note = _link_plan(link_path, args.skills_source)
    notes = [note] if note else []

    # Compare serialised, not as dicts: OpenCode applies the last matching
    # permission rule, so a reordered rule list is a real change.
    changed = json.dumps(desired) != json.dumps(user) or link_needs_change
    if not changed:
        print("agent-harness opencode: skipped %s" % config_path)
        _print_notes(notes)
        return 0

    if args.dry_run:
        print(
            "agent-harness opencode: would %s %s"
            % ("update" if existed else "install", config_path)
        )
        _print_notes(notes)
        return 0

    _backup_and_write(config_path, json.dumps(desired, indent=2, ensure_ascii=False) + "\n")
    if link_needs_change:
        if os.path.islink(link_path) or _is_junction(link_path):
            _remove_link(link_path)
        _create_link(args.skills_source, link_path)
    print(
        "agent-harness opencode: %s %s"
        % ("updated" if existed else "installed", config_path)
    )
    _print_notes(notes)
    return 0


# --------------------------------------------------------------------------- openhands


def vendored_base(repo_root, profile):
    """The vendored profile for `profile`; worker's shape when there is no file yet.

    `implementer` is new, so its base is `worker.json` with a deterministic id and
    revision 0. uuid5 keeps that id stable across runs and machines.
    """
    directory = os.path.join(repo_root, "openhands", "agent-profiles")
    path = os.path.join(directory, profile + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    with open(os.path.join(directory, "worker.json"), encoding="utf-8") as handle:
        base = json.load(handle)
    base["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "autoos-agent-profile:" + profile))
    base["revision"] = 0
    return base


LEAF_SUFFIX = (
    "Follow the AGENTS.md of the repository you work in and the coding-principles skill."
)


def render_profile(role_name, role, base, contract_ref):
    """Return a copy of `base` carrying this role's OpenHands settings.

    Managed keys are overwritten in place so the base's key order is kept; a key
    the base does not have is appended. Everything the role does not own survives.
    """
    profile = copy.deepcopy(base)
    openhands = role["openhands"]
    profile["name"] = openhands["profile"]
    profile["llm_profile_ref"] = openhands["llm_profile_ref"]
    profile["enable_sub_agents"] = role["spawn"]
    profile["mcp_server_refs"] = list(role["mcp"])
    suffix = LEAF_SUFFIX
    if role.get("leaf"):
        suffix += " You are a leaf: follow the leaf contract at " + contract_ref + "."
    if role.get("spawn"):
        suffix += (
            " You may start sub-agents: brief each one as a leaf under the leaf contract at "
            + contract_ref
            + ", then judge and commit its edits yourself."
        )
    profile["system_message_suffix"] = suffix
    return profile


def _serialize_profile(profile):
    return json.dumps(profile, indent=2, ensure_ascii=False) + "\n"


def _apply_file(path, text, dry_run):
    """Write `text` idempotently and return the status word for its line.

    Equal means equal JSON in the same key order, not equal bytes: OpenHands and
    the installer's own writer format these files differently, and a formatting
    difference alone must not cost a write and a backup on every run.
    """
    existed = os.path.exists(path)
    if existed:
        try:
            with open(path, encoding="utf-8-sig") as handle:
                current = json.load(handle)
            if json.dumps(current) == json.dumps(json.loads(text)):
                return "skipped"
        except (OSError, ValueError):
            pass
    if dry_run:
        return "would update" if existed else "would install"
    _backup_and_write(path, text)
    return "updated" if existed else "installed"


def cmd_vendor(args):
    harness = load_harness(args.harness)
    roles = harness.get("roles") or {}
    contract_ref = harness["rules"]["leaf_contract"]
    directory = os.path.join(args.repo_root, "openhands", "agent-profiles")
    # Render everything before writing: the new implementer base is worker.json, so
    # worker.json must still hold its pre-vendor bytes when implementer reads it.
    rendered = {}
    for role_name, role in roles.items():
        profile = role["openhands"]["profile"]
        base = vendored_base(args.repo_root, profile)
        rendered[profile] = _serialize_profile(
            render_profile(role_name, role, base, contract_ref)
        )
    if args.check:
        differing = []
        for profile, text in rendered.items():
            path = os.path.join(directory, profile + ".json")
            try:
                with open(path, "rb") as handle:
                    current = handle.read()
            except OSError:
                current = None
            if current != text.encode("utf-8"):
                differing.append(path)
        if differing:
            for path in differing:
                print("agent-harness vendor: differs %s" % path)
            return 1
        print("agent-harness vendor: ok")
        return 0
    for profile, text in rendered.items():
        path = os.path.join(directory, profile + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return 0


def _installed_base(path):
    """The parsed installed profile, or None when absent/unreadable/not an object."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _settings_status(settings_path, enable, dry_run):
    """The status word for settings.json, or None when it must be left alone."""
    if not os.path.exists(settings_path):
        desired = {"agent_settings": {"enable_sub_agents": enable}}
        return _apply_file(settings_path, _serialize_profile(desired), dry_run)
    try:
        with open(settings_path, encoding="utf-8-sig") as handle:
            settings = json.load(handle)
    except ValueError:
        return None
    if not isinstance(settings, dict):
        return None
    if "agent_settings" not in settings:
        settings["agent_settings"] = {}
    agent_settings = settings["agent_settings"]
    if not isinstance(agent_settings, dict):
        return None
    agent_settings["enable_sub_agents"] = enable
    return _apply_file(settings_path, _serialize_profile(settings), dry_run)


def cmd_openhands(args):
    harness = load_harness(args.harness)
    roles = harness.get("roles") or {}
    contract_ref = _join(args.repo_root, harness["rules"]["leaf_contract"])
    enable = bool((roles.get("orchestrator") or {}).get("spawn"))

    settings_path = os.path.join(args.openhands_dir, "settings.json")
    status = _settings_status(settings_path, enable, args.dry_run)
    if status is None:
        print("agent-harness openhands: left alone %s" % settings_path)
    else:
        print("agent-harness openhands: %s %s" % (status, settings_path))

    for role_name, role in roles.items():
        profile = role["openhands"]["profile"]
        path = os.path.join(args.openhands_dir, "agent-profiles", profile + ".json")
        base = _installed_base(path)
        if base is None:
            base = vendored_base(args.repo_root, profile)
        rendered = render_profile(role_name, role, base, contract_ref)
        status = _apply_file(path, _serialize_profile(rendered), args.dry_run)
        print("agent-harness openhands: %s %s" % (status, path))
    return 0


# --------------------------------------------------------------------------- cli


def build_parser():
    parser = argparse.ArgumentParser(prog="agent-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def with_harness(sub):
        sub.add_argument("--harness", default=str(DEFAULT_HARNESS))
        return sub

    with_harness(subparsers.add_parser("check")).set_defaults(func=cmd_check)

    pin = with_harness(subparsers.add_parser("pin"))
    pin.add_argument("server")
    pin.set_defaults(func=cmd_pin)

    with_harness(subparsers.add_parser("serena-excluded")).set_defaults(
        func=cmd_serena_excluded
    )

    opencode = with_harness(subparsers.add_parser("opencode"))
    opencode.add_argument("--config", required=True)
    opencode.add_argument("--repo-root", required=True)
    opencode.add_argument("--skills-source", required=True)
    opencode.add_argument("--dry-run", action="store_true")
    opencode.set_defaults(func=cmd_opencode)

    vendor = with_harness(subparsers.add_parser("vendor"))
    vendor.add_argument("--repo-root", required=True)
    vendor.add_argument("--check", action="store_true")
    vendor.set_defaults(func=cmd_vendor)

    openhands = with_harness(subparsers.add_parser("openhands"))
    openhands.add_argument("--openhands-dir", required=True)
    openhands.add_argument("--repo-root", required=True)
    openhands.add_argument("--dry-run", action="store_true")
    openhands.set_defaults(func=cmd_openhands)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
