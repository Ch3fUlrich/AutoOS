"""Report-only post-run diff guard: heuristic drift rules R0..R5.

Library half of tools/diff-guard.py. Compares two git revisions with
`git diff` (name-status and -U0) plus `git show`, applies small heuristic
rules to the changed files and returns report findings. The guard never
edits anything. The rules can produce false positives - the reviewer
judges the report; see docs/ai/diff-guard.md for the rule contract.
"""

from __future__ import annotations

import ast
import functools
import re
import subprocess

MAX_TEXT = 160

# The writer rule: ONE reviewed text, held as data so no human ever retypes
# it and the wording cannot drift between briefs. Task authors paste it into
# every writer task text (last paragraph); the CLI prints it verbatim with
# `python3 tools/diff-guard.py --writer-rule`. Docs: docs/ai/writer-contract.md
# (asserted verbatim against this constant by tests/test_diff_guard.py).
WRITER_RULE = (
    "Never change production behaviour to satisfy a test and never edit tests outside the write scope. If a test fails, report it. Do not add equality or hash overrides to subclasses of builtin types, do not detect that a test is running (no checks for the test runner, its environment variables or loaded modules), do not special-case test inputs or expected values, and do not weaken, skip or delete a test or a guard. Report the failure instead."
)

# Builtin-basis classes whose custom __eq__/__ne__/__hash__ silently breaks
# the implicit __hash__ contract (the 2026-03 js-files tuple incident).
BUILTIN_BASES = frozenset({
    "tuple", "list", "dict", "str", "int", "float", "set", "frozenset", "bytes",
})
_DUNDERS = ("__eq__", "__ne__", "__hash__")

# R4a: markers that opt test lines out of running (substrings, case-sensitive).
SKIP_MARKERS = (
    "skip", "xfail", "skipTest", "skipIf", "skipif",
    "expectedFailure", "importorskip",
)

# R4b: a line that, after its indent, asserts something.
ASSERT_PREFIXES = (
    "assert", "self.assert", "self.fail",
    "pytest.raises", "with self.assertRaises", "with pytest.raises",
)

# R5: test definitions a File may drop.
TEST_DEF_PREFIXES = ("def test_", 'it "', 'if it "', "Test-Case '", 'Test-Case "')

_HUNK_RE = re.compile(r"^@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@")
_NUM_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_QUOTED_RE = re.compile(r"""['"]([^'"]*)['"]""")


class GitError(RuntimeError):
    """A git invocation failed; the CLI reports it as exit code 2."""


def run_git(repo: str, *args: str) -> str:
    """Run git against `repo` and return stdout; raise GitError on failure."""
    cmd = ["git", "-C", str(repo)] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:  # git not executable at all
        raise GitError("git could not be started: %s" % exc)
    stdout = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").splitlines()
        detail = stderr[0] if stderr else "no error output"
        raise GitError(
            "%s failed (%d): %s" % (" ".join(cmd), proc.returncode, detail)
        )
    return stdout


# -- paths -----------------------------------------------------------------

def _norm(path: str) -> str:
    path = str(path).replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    return path


def is_test_path(path) -> bool:
    """R1's test-file patterns: tests/**, test_*.py, *_test.py, *.Tests.ps1,
    tests/linux/*.sh (subsumed by tests/**)."""
    if not path:
        return False
    norm = _norm(path)
    base = norm.rsplit("/", 1)[-1]
    if "tests/" in norm + "/":
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base.endswith("_test.py"):
        return True
    if base.endswith(".Tests.ps1"):
        return True
    return False


@functools.lru_cache(maxsize=256)
def _glob_rx(pattern: str):
    """Compile a scope glob: `**` any depth, `*`/`?` within one segment."""
    parts = pattern.split("/")
    out = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
        else:
            piece = re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
            out.append(piece if last else piece + "/")
    return re.compile("^" + "".join(out) + "$")


def in_scope(path: str, scopes) -> bool:
    """True when `path` matches at least one --scope glob. A pattern without
    a `/` matches the basename; otherwise the whole (repo-relative) path."""
    if not path:
        return False
    norm = _norm(path)
    base = norm.rsplit("/", 1)[-1]
    for pattern in scopes:
        rx = _glob_rx(pattern)
        subject = base if "/" not in pattern else norm
        if rx.match(subject):
            return True
    return False


# -- diff parsing ----------------------------------------------------------

def parse_name_status(text: str):
    """`git diff --name-status` rows -> [{code, old_path, new_path}]."""
    entries = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        code = status[0].upper()
        old_path = new_path = None
        if len(parts) >= 3:
            old_path, new_path = parts[1], parts[2]
        elif len(parts) == 2:
            if code == "D":
                old_path = parts[1]
            else:
                new_path = parts[1]
        entries.append({"code": code, "old_path": old_path, "new_path": new_path})
    return entries


def _diff_path(rest: str):
    path = rest.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def parse_hunks(text: str):
    """`git diff -U0` -> {path: [{removed: [(old_no, text)],
    added: [(new_no, text)]}]}. Hunk rows are keyed by the `+++` path,
    falling back to `---` (deletions)."""
    hunks = {}
    old_path = new_path = None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old_path = _diff_path(line[4:])
            i += 1
        elif line.startswith("+++ "):
            new_path = _diff_path(line[4:])
            i += 1
        elif line.startswith("@@"):
            match = _HUNK_RE.match(line)
            i += 1
            if not match:
                continue
            old_no, new_no = int(match.group(1)), int(match.group(3))
            path = new_path or old_path
            if path is None:
                continue
            hunk = {"removed": [], "added": []}
            hunks.setdefault(path, []).append(hunk)
            while i < len(lines):
                body = lines[i]
                if (body.startswith("@@") or body.startswith("--- ")
                        or body.startswith("diff --git ") or body == ""):
                    break
                if body.startswith("+"):
                    hunk["added"].append((new_no, body[1:]))
                    new_no += 1
                elif body.startswith("-"):
                    hunk["removed"].append((old_no, body[1:]))
                    old_no += 1
                elif body.startswith("\\"):
                    pass  # "\ No newline at end of file"
                elif body.startswith(" "):
                    old_no += 1
                    new_no += 1
                i += 1
            continue
        else:
            i += 1
    return hunks


# -- rule helpers ----------------------------------------------------------

def _finding(rule: str, path: str, line, text: str) -> dict:
    cleaned = str(text).replace("\r", " ").replace("\n", " ").strip()[:MAX_TEXT]
    return {"rule": rule, "path": _norm(path), "line": int(line), "text": cleaned}


def _base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _base_name(node.value)
        return parent + "." + node.attr if parent else None
    return None


def _is_assert(line: str) -> bool:
    return line.strip().startswith(ASSERT_PREFIXES)


def _is_test_def(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(TEST_DEF_PREFIXES)


def _test_ref(line: str) -> bool:
    """R3: a non-test source line reaching into the test frameworks."""
    if "importorskip" in line or "PYTEST_CURRENT_TEST" in line:
        return True
    if "pytest" in line or "unittest" in line:
        return True
    if "sys.modules" in line and "test" in line.lower():
        return True
    if "os.environ" in line:
        for literal in _QUOTED_RE.findall(line):
            if "TEST" in literal:
                return True
    return False


def _source_findings(path: str, content: str, added):
    """R0 (unparsable), R2 (builtin-basis class with dunder equality in the
    added lines), R3 (test references) for one non-test tip file."""
    found = []
    added_nos = {no for no, _ in added}
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, TypeError):
        tree = None
        found.append(_finding("R0", path, 1, "unparsable"))
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.lineno not in added_nos:
                continue
            bases = [name for name in (_base_name(b) for b in node.bases) if name]
            if not any(base in BUILTIN_BASES for base in bases):
                continue
            methods = {
                child.name for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            dunders = [name for name in _DUNDERS if name in methods]
            if not dunders:
                continue
            found.append(_finding(
                "R2", path, node.lineno,
                "class %s(%s) defines %s"
                % (node.name, ", ".join(bases), ", ".join(dunders)),
            ))
    for no, raw in added:
        if _test_ref(raw):
            found.append(_finding("R3", path, no, raw.strip()))
            break  # one R3 report per file keeps the report quiet
    return found


def _test_findings(path: str, hunks):
    """R4/R5 for one test-file endpoint.
    R4a skip marker on an added line; R4b more assert lines removed than
    added; R4c an added assert whose numeric literal grew vs the removed
    twin in the same hunk; R5 a removed test def never re-added."""
    found = []
    removed = [(no, text) for hunk in hunks for no, text in hunk["removed"]]
    added = [(no, text) for hunk in hunks for no, text in hunk["added"]]

    for no, raw in added:
        if any(marker in raw for marker in SKIP_MARKERS):
            found.append(_finding("R4", path, no, raw.strip()))
            break

    removed_asserts = [(no, text.strip()) for no, text in removed if _is_assert(text)]
    added_asserts = [(no, text.strip()) for no, text in added if _is_assert(text)]
    if removed_asserts and len(removed_asserts) > len(added_asserts):
        found.append(_finding(
            "R4", path, removed_asserts[0][0],
            "assert lines removed %d > added %d"
            % (len(removed_asserts), len(added_asserts)),
        ))

    done_grown = False
    for hunk in hunks:
        if done_grown:
            break
        by_skeleton = {}
        for _, text in hunk["removed"]:
            if _is_assert(text):
                by_skeleton.setdefault(_NUM_RE.sub("#", text.strip()), []).append(
                    text.strip()
                )
        if not by_skeleton:
            continue
        for no, raw in hunk["added"]:
            if not _is_assert(raw):
                continue
            stripped = raw.strip()
            candidates = by_skeleton.get(_NUM_RE.sub("#", stripped))
            if not candidates:
                continue
            old_nums = _NUM_RE.findall(candidates[0])
            new_nums = _NUM_RE.findall(stripped)
            if len(old_nums) != len(new_nums) or not old_nums:
                continue
            grown = [
                (old, new) for old, new in zip(old_nums, new_nums)
                if float(new) > float(old)
            ]
            if grown:
                found.append(_finding(
                    "R4", path, no,
                    "assert numeric literal grew: "
                    + "; ".join("%s -> %s" % pair for pair in grown),
                ))
                done_grown = True
                break

    added_texts = {text.strip() for _, text in added}
    for no, raw in removed:
        stripped = raw.strip()
        if _is_test_def(stripped) and stripped not in added_texts:
            found.append(_finding("R5", path, no, stripped))
    return found


# -- entry point -----------------------------------------------------------

def analyze(base: str, tip: str, scopes, repo="."):
    """Run every rule over `base..tip`; return findings sorted by
    (path, line, rule, text). Raises GitError on a git failure."""
    repo = str(repo)
    name_status = run_git(
        repo, "-c", "core.quotepath=false",
        "diff", "--no-color", "--find-renames", "--name-status", base, tip,
    )
    diff_text = run_git(
        repo, "-c", "core.quotepath=false",
        "diff", "--no-color", "--find-renames", "-U0", base, tip,
    )
    entries = parse_name_status(name_status)
    hunks = parse_hunks(diff_text)
    findings = []
    for entry in entries:
        _check_entry(entry, hunks, scopes, repo, tip, findings)
    findings.sort(key=lambda f: (f["path"], f["line"], f["rule"], f["text"]))
    return findings


def _check_entry(entry, hunks, scopes, repo, tip, findings):
    code = entry["code"]
    old_path, new_path = entry["old_path"], entry["new_path"]

    # R1: every test-file endpoint must sit inside some --scope glob.
    for path in (old_path, new_path):
        if not path or not is_test_path(path):
            continue
        if not in_scope(path, scopes):
            findings.append(_finding(
                "R1", path, 1, "test file changed outside scope (%s)" % code,
            ))

    path = new_path or old_path
    entry_hunks = hunks.get(new_path or "", hunks.get(old_path or "", []))

    # R0-R3: source files as they exist at the tip, only when lines were added.
    added = [
        (no, text) for hunk in entry_hunks for no, text in hunk["added"]
    ]
    if (code != "D" and path and added and not is_test_path(path)
            and path.endswith(".py")):
        content = run_git(repo, "show", "%s:%s" % (tip, path))
        findings.extend(_source_findings(path, content, added))

    # R4/R5: test endpoints, once per entry (new path preferred).
    if any(p and is_test_path(p) for p in (old_path, new_path)):
        key = new_path if new_path and new_path in hunks else old_path
        in_file = hunks.get(key, []) if key else []
        if in_file:
            findings.extend(_test_findings(key, in_file))
