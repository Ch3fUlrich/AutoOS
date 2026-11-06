"""Strict brief scope validation; search tracked content in the git index only.

Definition-shaped lines in documentation or fixtures count: shape, not language.
"""
import os
import re
import subprocess
from pathlib import Path


def _definitions(root, name):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "grep", "--cached", "--no-color", "-z",
             "-F", "-e", name, "--", "."], cwd=root, env=env,
            capture_output=True)
    except (OSError, UnicodeError) as exc:
        raise ValueError("definition search failed for %s" % name) from exc
    if result.returncode not in (0, 1):
        raise ValueError("definition search failed for %s" % name)
    pattern = re.compile(
        r"^\s*(?:(?:async\s+)?(?:def|class)\s+" + re.escape(name)
        + r'\b|(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+|async\s+|const\s+|extern\s+(?:"[^"]*"\s+)?)*'
        + r"(?:fn|struct|enum|trait|type)\s+" + re.escape(name)
        + r"\b|(?:function\s+" + re.escape(name) + r"(?:\s*\(\s*\))?|"
        + re.escape(name) + r"\s*\(\s*\))\s*\{)")
    bare = re.compile(r"^\s*(?:function\s+" + re.escape(name)
                      + r"(?:\s*\(\s*\))?|" + re.escape(name) + r"\s*\(\s*\))\s*$")
    found = []
    for record in result.stdout.split(b"\n"):
        raw_path, sep, raw_line = record.partition(b"\0")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("undecodable definition path for %s" % name) from exc
        line = raw_line.decode("utf-8", errors="replace")
        if sep and (pattern.match(line) or (
                Path(path).suffix in {".sh", ".bash"} and bare.fullmatch(line))) and path not in found:
            found.append(path)
    if not found:
        raise ValueError("no definition in tracked content for %s" % name)
    return found


def expand_scope(scope, paths, validate_paths):
    """Return finding-expanded paths; rulings must stay in the original paths."""
    if type(scope) is not dict or any(
            k not in {"root", "findings", "rulings"} for k in scope):
        raise ValueError("scope must be a dict with known keys")
    if "root" in scope and (type(scope["root"]) is not str
                            or not Path(scope["root"]).is_dir()):
        raise ValueError("scope root must be a repository directory string")
    if any(k in scope for k in ("findings", "rulings")) and "root" not in scope:
        raise ValueError("scope root required for findings or rulings")
    groups = {}
    for kind in ("findings", "rulings"):
        entries = scope.get(kind, [])
        if type(entries) is not list:
            raise ValueError("%s must be a list" % kind)
        groups[kind] = entries
        for entry in entries:
            allowed = {"names", "probes"} if kind == "findings" else {"names"}
            if type(entry) is not dict or set(entry) - allowed or "names" not in entry:
                raise ValueError("invalid %s entry" % kind)
            if type(entry["names"]) is not list or not entry["names"] or any(
                    type(n) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", n)
                    for n in entry["names"]):
                raise ValueError("names must be a list of plain identifiers: %r" % entry["names"])
            if "probes" in entry and (type(entry["probes"]) is not list or any(
                    type(p) is not str for p in entry["probes"])):
                raise ValueError("probes must be a list of strings")
    expanded = list(paths)
    original = set(paths)
    for kind, entries in groups.items():
        for entry in entries:
            for name in entry["names"]:
                for path in _definitions(scope["root"], name):
                    if "," in path:
                        raise ValueError("definition path contains comma: %s" % path)
                    validate_paths("definition", [path])
                    if kind == "rulings" and path not in original:
                        raise ValueError("ruling file %s for %s outside paths" % (path, name))
                    if kind == "findings" and path not in expanded:
                        for listed in expanded:
                            if listed.casefold() == path.casefold():
                                raise ValueError("definition paths differ only in case: %s, %s"
                                                 % (listed, path))
                        if path != "CHANGELOG.md" and sum(p != "CHANGELOG.md" for p in expanded) >= 3:
                            raise ValueError("finding file %s for %s exceeds max 3" % (path, name))
                        expanded.append(path)
    return expanded
