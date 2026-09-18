#!/usr/bin/env python3
"""Generate agent-harness config from catalog/agent-harness.json.

One generator, used by both installers. Pure functions plus a small CLI:

    check            validate the harness file
    pin SERVER       print the pinned package for one MCP server
    serena-excluded  print Serena's excluded tools, one per line
    opencode         merge the harness into an OpenCode config

Only the standard library is used, and nothing here touches the network.
"""
import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_SCHEMA = "https://opencode.ai/config.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HARNESS = REPO_ROOT / "catalog" / "agent-harness.json"

TOP_LEVEL_KEYS = ("version", "rules", "fences", "mcp_servers", "roles")
MANAGED_AGENT_KEYS = ("description", "mode", "model", "permission", "tools")


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
    if "git push*" not in (fences.get("bash_deny_all") or []):
        problems.append("fences.bash_deny_all must contain 'git push*'")
    if "git commit*" not in (fences.get("bash_deny_leaf") or []):
        problems.append("fences.bash_deny_leaf must contain 'git commit*'")

    servers = harness.get("mcp_servers")
    servers = servers if isinstance(servers, dict) else {}
    for name, server in servers.items():
        if not isinstance(server, dict) or not isinstance(server.get("package"), str):
            problems.append("mcp_servers.%s.package must be a string" % name)

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
    permission["bash"] = _rebuild_patterns(
        permission.get("bash"), deny_all, deny_leaf, allow_all
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
        return os.path.normcase(os.path.realpath(path)) != os.path.normcase(
            os.path.abspath(path)
        )
    except OSError:
        return False


def _link_target(path):
    try:
        return os.readlink(path)
    except OSError:
        return os.path.realpath(path)


def _same_target(target, link_path, source):
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link_path), target)
    return os.path.normcase(os.path.normpath(target)) == os.path.normcase(
        os.path.normpath(source)
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

    if existed:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(config_path, "%s.autoos-backup-%s" % (config_path, stamp))
    os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
    with open(config_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(desired, indent=2, ensure_ascii=False) + "\n")
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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
