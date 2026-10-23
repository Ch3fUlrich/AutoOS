#!/usr/bin/env python3
"""Smart CI selector: base..head diff + per-repo map -> the jobs and tests to run.

    python3 selector.py --map .github/smart-ci.json --base <sha> --head <sha> [--full] [--github-output]
    python3 selector.py --map .github/smart-ci.json --files a.py b/c.rs      (no git; fixtures)

Prints one JSON object (also appended to $GITHUB_OUTPUT with --github-output):

    mode      "full" | "selected" | "none"   (none = only ignorable paths changed)
    reason    why (the first full trigger, or a short summary)
    jobs      {"<job>": true|false, ...}     every job named in the map
    tests     ["tests/test_x.py", ...]       empty with mode full = run everything
    changed   the changed paths seen

The selector never guesses. Anything it cannot place - a path no rule names, a
workflow / dependency / CI file, a missing or broken map, an unreadable diff, an
explicit --full - turns into mode "full". It always exits 0 with a usable
answer, so a failed selection can only cost minutes, never skip a test.

Map format: docs/ci-smart-select.md. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# A change to any of these invalidates every selection: the CI definition itself,
# dependency manifests and lockfiles, container build inputs, and this selector.
BUILTIN_FULL = [
    ".github/workflows/**",
    ".github/actions/**",
    ".github/smart-ci.json",
    "tools/smart-ci/**",
    "tests/ci-shards.py",
    "tests/ci-shards.txt",
    "tests/test_ci_shards.py",
    "**/requirements*.txt",
    "**/constraints*.txt",
    "**/pyproject.toml",
    "**/setup.py",
    "**/setup.cfg",
    "**/uv.lock",
    "**/poetry.lock",
    "**/Pipfile",
    "**/Pipfile.lock",
    "**/package.json",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/Cargo.toml",
    "**/Cargo.lock",
    "**/rust-toolchain*",
    "**/go.mod",
    "**/go.sum",
    "**/Gemfile",
    "**/Gemfile.lock",
    "**/Dockerfile*",
    "**/*.csproj",
    "**/Directory.Packages.props",
]


def glob_to_regex(pattern: str) -> re.Pattern:
    """`**` crosses directories, `*` and `?` do not; the match is anchored at the root."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, patterns) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


class MapError(ValueError):
    pass


def load_map(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise MapError("cannot read map %s: %s" % (path, exc))
    return validate_map(data)


def validate_map(data) -> dict:
    if not isinstance(data, dict):
        raise MapError("map is not an object")
    allowed = {"version", "comment", "jobs", "always_jobs", "ignore", "full_if", "rules", "planner"}
    extra = set(data) - allowed
    if extra:
        raise MapError("unknown map keys: %s" % ", ".join(sorted(extra)))
    if "planner" in data:
        planner = data["planner"]
        if (not isinstance(planner, dict) or set(planner) != {"argv"}
                or not isinstance(planner["argv"], list) or not planner["argv"]
                or not all(isinstance(arg, str) for arg in planner["argv"])):
            raise MapError("planner needs a non-empty argv list of strings")
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list) or not all(isinstance(j, str) and j for j in jobs):
        raise MapError("jobs must be a list of names")
    for key in ("always_jobs", "ignore", "full_if"):
        val = data.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            raise MapError("%s must be a list of strings" % key)
    for j in data.get("always_jobs", []):
        if j not in jobs:
            raise MapError("always_jobs names unknown job %r" % j)
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise MapError("rules must be a list")
    for n, rule in enumerate(rules):
        if not isinstance(rule, dict) or not isinstance(rule.get("paths"), list) or not rule["paths"]:
            raise MapError("rule %d needs a non-empty paths list" % n)
        if not all(isinstance(path, str) for path in rule["paths"]):
            raise MapError("rule %d paths must be strings" % n)
        if set(rule) - {"paths", "tests", "jobs", "comment"}:
            raise MapError("rule %d has unknown keys" % n)
        for j in rule.get("jobs", []):
            if j not in jobs:
                raise MapError("rule %d names unknown job %r" % (n, j))
        if not all(isinstance(t, str) for t in rule.get("tests", [])):
            raise MapError("rule %d tests must be strings" % n)
    return data


def full_result(files, reason, job_names):
    return {
        "mode": "full",
        "reason": reason,
        "jobs": {j: True for j in job_names},
        "tests": [],
        "changed": files,
    }


def select(files, cmap, force_full=False, force_reason="forced full run", exists=None, plan=None):
    """files: changed paths (posix, repo-relative). cmap: a validated map or None.

    exists: optional callable(path) -> bool. A test that came from a `{path}` token (the
    changed file itself) but is gone at HEAD (the PR deleted or renamed it) cannot be
    run, so the run goes full rather than hand the runner a path that does not exist."""
    files = sorted({f.strip().replace("\\", "/") for f in files if f.strip()})
    job_names = list((cmap or {}).get("jobs", []))
    if force_full:
        return full_result(files, force_reason, job_names)
    if cmap is None:
        return full_result(files, "no usable map", job_names)
    if not files:
        return full_result(files, "empty diff (cannot tell what changed)", job_names)

    full_patterns = BUILTIN_FULL + cmap.get("full_if", [])
    for f in files:
        if matches(f, full_patterns):
            return full_result(files, "workflow/dependency/CI file changed: %s" % f, job_names)

    jobs = {j: False for j in job_names}
    tests = []
    unplanned = []
    relevant = False
    for f in files:
        if matches(f, cmap.get("ignore", [])):
            continue
        relevant = True
        hit = False
        for rule in cmap.get("rules", []):
            if matches(f, rule["paths"]):
                hit = True
                for j in rule.get("jobs", []):
                    jobs[j] = True
                for t in rule.get("tests", []):
                    if t == "{path}":
                        t = f
                        if exists is not None and not exists(f):
                            return full_result(files, "selected test missing at HEAD: %s" % f, job_names)
                    if t not in tests:
                        tests.append(t)
        if not hit:
            if "planner" in cmap:
                unplanned.append(f)
            else:
                return full_result(files, "unknown path (no rule): %s" % f, job_names)

    if unplanned:
        try:
            if plan is None:
                raise ValueError("planner unavailable (requires --base)")
            planned = plan()
            if not isinstance(planned, dict) or not {"parts", "terms", "pytest"} <= set(planned):
                raise ValueError("expected plan object with parts, terms and pytest")
            if (not isinstance(planned["parts"], list)
                    or not all(isinstance(p, str) and re.fullmatch(r"[0-9]{2}", p)
                               for p in planned["parts"])
                    or not isinstance(planned["terms"], str)
                    or not isinstance(planned["pytest"], list)):
                raise ValueError("invalid plan parts, terms or pytest")
        except Exception as exc:
            return full_result(files, "planner failed: %s" % type(exc).__name__, job_names)
        if planned["terms"]:
            return full_result(files, "planner selected cases by filter terms, not runnable as parts", job_names)
        if not planned["parts"]:
            return full_result(files, "planner selected no CI part (pytest-only plan)", job_names)
        if planned["parts"]:
            if "shards" not in jobs:
                return full_result(files, "planner failed: shards job not declared", job_names)
            jobs["shards"] = True
            for part in planned["parts"]:
                if part not in tests:
                    tests.append(part)

    if not relevant:
        return {"mode": "none", "reason": "only ignorable paths changed", "jobs": jobs, "tests": [], "changed": files}
    for j in cmap.get("always_jobs", []):
        jobs[j] = True
    return {
        "mode": "selected",
        "reason": "%d file(s) mapped to %d job(s), %d test(s)" % (len(files), sum(jobs.values()), len(tests)),
        "jobs": jobs,
        "tests": tests,
        "changed": files,
    }


def git_changed(base: str, head: str):
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "%s...%s" % (base, head)],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def github_output(result: dict, path: str) -> None:
    lines = [
        "mode=%s" % result["mode"],
        "full=%s" % ("true" if result["mode"] == "full" else "false"),
        "reason=%s" % result["reason"].replace("\n", " "),
        "jobs=%s" % json.dumps(result["jobs"], separators=(",", ":")),
        "tests=%s" % " ".join(result["tests"]),
        "tests_json=%s" % json.dumps(result["tests"], separators=(",", ":")),
    ]
    for name, on in result["jobs"].items():
        lines.append("job_%s=%s" % (re.sub(r"[^A-Za-z0-9_]", "_", name), "true" if on else "false"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", required=True)
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--files", nargs="*", help="changed paths instead of a git diff")
    ap.add_argument("--full", action="store_true", help="force the full suite (label / dispatch / non-PR event)")
    ap.add_argument("--full-reason", default="full suite requested")
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)

    cmap = None
    try:
        cmap = load_map(args.map)
    except MapError as exc:
        err = str(exc)
    files = []
    plan = None
    if cmap is not None and "planner" in cmap and args.base:
        def plan():
            command = [arg.replace("{base}", args.base) for arg in cmap["planner"]["argv"]]
            output = subprocess.run(
                command, capture_output=True, text=True, check=True, timeout=120,
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ).stdout
            return json.loads(output)
    if args.full:
        result = select([], cmap, True, args.full_reason)
    else:
        try:
            if args.files is not None:
                files = args.files
            elif args.base:
                files = git_changed(args.base, args.head)
            else:
                raise ValueError("no --base and no --files")
            result = select(files, cmap, exists=os.path.exists, plan=plan)
            if cmap is None:
                result["reason"] = "map unusable: %s" % err
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            result = select([], cmap, True, "selection failed: %s" % str(exc).splitlines()[0][:200])
    print(json.dumps(result, indent=2))
    out = os.environ.get("GITHUB_OUTPUT")
    if args.github_output and out:
        github_output(result, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
