"""Strict brief scope validation; search tracked content in the git index only.

Definition-shaped lines in documentation or fixtures count: shape, not language.
"""
import os
import importlib.util
import re
import subprocess
from pathlib import Path


def load_sibling(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _definitions(root, name):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "-c", "grep.lineNumber=false",
             "-c", "grep.column=false", "-c", "grep.fullName=true",
             "grep", "--cached", "--no-color", "-z",
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
            k not in {"root", "findings", "rulings", "size_cap", "lines_cap"} for k in scope):
        raise ValueError("scope must be a dict with known keys")
    if "root" in scope and (type(scope["root"]) is not str
                            or not Path(scope["root"]).is_dir()):
        raise ValueError("scope root must be a repository directory string")
    if any(k in scope for k in ("findings", "rulings", "size_cap")) and "root" not in scope:
        raise ValueError("scope root required for findings, rulings or size_cap")
    for key in ("size_cap", "lines_cap"):
        if key in scope and (type(scope[key]) is not int or (
                key == "lines_cap" and not 1 <= scope[key] <= 200)):
            raise ValueError("%s must be an integer%s" % (key, " 1..200" if key == "lines_cap" else ""))
    if "root" in scope:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        try:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=scope["root"],
                                 env=env, capture_output=True, text=True, check=True)
            if Path(top.stdout.strip()).resolve() != Path(scope["root"]).resolve():
                raise ValueError("scope root must be repository top level")
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ValueError("scope root must be repository top level") from exc
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
    if "size_cap" in scope:
        root, cap = Path(scope["root"]), scope["size_cap"]
        try:
            counts, errors = load_sibling("check-file-size").parse_baseline(
                (root / ".filesize-baseline").read_text(encoding="utf-8"), ".filesize-baseline")
            if errors:
                raise ValueError("; ".join(errors))
            for path in expanded:
                baseline = counts.get(path)
                if baseline is None:
                    file = root / path
                    baseline = len(file.read_bytes().decode("utf-8", errors="replace").splitlines()) if file.exists() else 0
                if baseline > cap:
                    raise ValueError("%s baseline/current lines %d exceeds size cap %d" % (path, baseline, cap))
        except (OSError, UnicodeError) as exc:
            raise ValueError("cannot read size baseline or in-scope file") from exc
    return expanded
