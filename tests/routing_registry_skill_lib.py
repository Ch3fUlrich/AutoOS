#!/usr/bin/env python3
"""Shared constants and helpers for the routing-registry skill's test guards.

Holds what tests/test_skill_routing_registry.py and
tests/test_skill_routing_registry_commands.py both read: ROOT and the skill's
own file locations, every regex the guards match with (links, script paths,
repo paths, the wrap gap, apply-flag tokens, `render ... --out` claims, the
tool's "never the real ..." refusal), and the helpers that pull links, script
paths, wrapped repo paths, apply invocations and `render <target> --out`
claims out of skill pages. The assertions live in the two test files, not
here; this module is imported, never run:

    from routing_registry_skill_lib import ...
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".agents" / "skills" / "routing-registry"
INDEX = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"

INDEX_MAX_LINES = 150
REFERENCE_MAX_LINES = 500

# The removed skill's name, spelled at runtime on purpose: this checker must
# not carry the literal it forbids, or the prose scan would flag the very test
# file that performs it.
NEEDLE = "combo" + "-" + "create"

# Shape of a date as it appears in a dated history document's filename
# (2026-10-01 or 20261001).
DATE_IN_NAME_RE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{8})(?!\d)")

# [text](target) — the target may carry a #fragment, which is stripped before
# resolution. External links (a scheme) are not repo paths and are skipped.
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# Every top-level tracked directory a repo path can start with — one tuple,
# two users: the wrap guard's shape of a repo path, and SCRIPT_RE below (a
# script path is a repo path with a script suffix).
REPO_DIRS = ("tools", "configuration", "catalog", "tests", "infra", "logs",
             "docs", ".agents", "lib", ".github")

# A repo-relative script path as the skill may name one: a REPO_DIRS path
# ending in a script suffix. Data paths are not asserted here — several name
# git-ignored runtime state (logs/routing/…), which legitimately does not
# exist in a checkout.
SCRIPT_RE = re.compile(
    r"(?<![\w./-])(?:%s)/[A-Za-z0-9._/-]*\.(?:py|sh|ps1|psm1|mjs)\b"
    % "|".join(re.escape(d) for d in REPO_DIRS))

MD_DIRS_SKIP = {".git", "node_modules", "third_party", "__pycache__"}

# MED 3: the steps each path must carry. A path can lose a step while every
# file, link and script path above still resolves — that is exactly the drift
# this list pins. Each entry is the step's name and the patterns that must all
# match somewhere in the page: its heading AND its operative keyword. A bare
# keyword is not enough — a historical note about a dropped step keeps the
# word alive after the step itself is gutted, so the legs are pinned as they
# were restored: the command in the gate block and the lint requirement line.
REQUIRED_STEPS = {
    "add-modify.md": [
        ("registry edit", [r"(?m)^## 1\. Edit the registry\b",
                           r"registry edit"]),
        ("render --check", [r"(?m)^## 2\. Render and check\b",
                            r"render[^\n]*--check"]),
        ("combo-contract", [r"(?m)^## 3\. Contract gate\b",
                            r"combo-contract"]),
        ("apply --dry-run", [r"apply(?:\.sh)? --dry-run"]),
        ("GO", [r"(?m)^## GO\b"]),
        ("apply", [r"then apply"]),
        ("pytest/shellcheck/PSScriptAnalyzer step", [
            r"(?m)^## 5\. Offline checks\b",
            r"(?m)^\s*pytest\s+tests/test_registry\.py\b",
            r"shellcheck over every touched",
            r"PSScriptAnalyzer over every touched"]),
    ],
    "remove.md": [
        ("retired list", [r"(?m)^## 1\. Retire a combo\b",
                          r"OMNIROUTE_RETIRED_IDS"]),
        ("unavailable_legs", [r"(?m)^## 2\. Remove a leg\b",
                              r"unavailable_legs"]),
        ("re-render", [r"[Rr]e-render"]),
    ],
}

# MED 4: the applier scripts' own argument handling is the truth about their
# flags. apply.sh refuses unknown arguments with exit 2 by design, so a flag
# its case arms do not carry is a command that fails when it is run.
APPLY_SH = ROOT / "configuration" / "omniroute" / "apply.sh"
APPLY_PS1 = ROOT / "configuration" / "omniroute" / "apply.ps1"

# A flag claim inside an invocation: a whitespace token starting with a dash
# and a letter. Matched as a whole token on purpose — `--go-shaX` must not be
# read as `--go-sha` plus a letter and pass silently.
APPLY_FLAG_TOKEN_RE = re.compile(r"-{1,2}[A-Za-z][\w-]*(?:=[^\s]*)?")
SH_TOKEN_RE = re.compile(r"(?<![\w.-])apply\.sh\b")
PS_TOKEN_RE = re.compile(r"(?<![\w.-])apply\.ps1\b")
FENCE_RE = re.compile(r"^\s*```")
SPAN_RE = re.compile(r"`([^`]*)`")

# The wrap guard's shape of a repo path: a REPO_DIRS prefix, then the rest.
REPO_PATH_RE = re.compile(
    r"(?:%s)/[A-Za-z0-9._/-]+" % "|".join(re.escape(d) for d in REPO_DIRS))

# F1: a `render <target> --out <path>` claim as the skill may name one —
# fenced or inline; wrapped commands are re-joined first. The path must look
# like a path ("--out to a scratch file" is prose, not a file claim).
RENDER_OUT_RE = re.compile(r"render\s+([A-Za-z][\w-]*)\s+--out\s+([^\s`\"']+)")

# The tool's own refusal, read from its --help. Help text wraps mid-phrase
# ("never the real\n combos.json)"), so the text is whitespace-normalized
# before this is searched.
NEVER_THE_REAL_RE = re.compile(r"never the real\s+([A-Za-z0-9._/-]+)")

# A wrapped line as it appears in prose: the break, the indent, and the list
# or quote marker a reader's eye skips but a copied path must not carry.
WRAP_GAP_RE = re.compile(r"\r?\n[ \t]*(?:- |> )?")


def _skill_files():
    """SKILL.md plus every reference page."""
    return ([INDEX] if INDEX.is_file() else []) + sorted(REFERENCES.glob("*.md"))


def _links(path):
    """Every relative markdown link target in `path` (fragment stripped)."""
    text = path.read_text(encoding="utf-8")
    return [t.split("#", 1)[0] for t in LINK_RE.findall(text)
            if not SCHEME_RE.match(t) and t.split("#", 1)[0]]


def _script_paths(path):
    """Every repo-relative script path named in `path`."""
    return sorted(set(SCRIPT_RE.findall(path.read_text(encoding="utf-8"))))


def _render_out_mentions(text):
    """Every (target, path) claim of `render <target> --out <path>` in `text`:
    fenced or inline, a wrapped command re-joined before matching."""
    hits = []
    for m in RENDER_OUT_RE.finditer(WRAP_GAP_RE.sub("", text)):
        path = m.group(2).rstrip(".,;:)")
        if "/" in path or "." in path:
            hits.append((m.group(1), path))
    return hits


# One `render <target> --help` per target, read once (subprocess, no network).
_RENDER_HELP = {}


def _never_the_real(target):
    """The file `render <target> --help` calls "never the real ...", or None.

    That phrase is the tool's own refusal — the one the skill's `--out` claims
    are checked against — so it is read from the help at run time, not from a
    table here that would drift from the tool."""
    if target not in _RENDER_HELP:
        run = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "registry.py"),
             "render", target, "--help"],
            cwd=str(ROOT), capture_output=True, encoding="utf-8",
            errors="replace")
        _RENDER_HELP[target] = run.stdout + " " + run.stderr
    m = NEVER_THE_REAL_RE.search(re.sub(r"\s+", " ", _RENDER_HELP[target]))
    return m.group(1) if m else None


def _out_violations(mentions):
    """(target, path, named) per mention whose path IS the file the target's
    help refuses: equal to the name, or carrying it as a tail (help text
    abbreviates — `combos.json` is `configuration/omniroute/combos.json`)."""
    return [(t, p, named) for t, p in mentions
            for named in [_never_the_real(t)]
            if named is not None and (p == named or p.endswith("/" + named)
                                      or named.endswith("/" + p))]


def _repo_markdown():
    for p in ROOT.rglob("*.md"):
        if not MD_DIRS_SKIP.intersection(p.parts):
            yield p


def _tracked_files():
    """Every git-tracked file, repo-relative — untracked scratch is out of scope.

    The index is the only thing that separates tracked history from scratch
    lying around the checkout, and the scan must not read the latter. A
    Windows-created worktree's `.git` pointer holds a `C:/...` gitdir that
    Linux git joins against the worktree and fails on, so when the pointer is
    a Windows path and its `/mnt/<drive>/` form exists, that form is passed
    explicitly as `--git-dir`.
    """
    git_dir = None
    dot = ROOT / ".git"
    if dot.is_file() and "gitdir:" in dot.read_text(
            encoding="utf-8", errors="replace"):
        git_dir = dot.read_text(encoding="utf-8", errors="replace").split(
            "gitdir:", 1)[1].strip()
        if re.match(r"[A-Za-z]:[\\/]", git_dir):
            posix = "/mnt/%s/%s" % (git_dir[0].lower(),
                                    git_dir[3:].replace("\\", "/"))
            if Path(posix).is_dir():
                git_dir = posix
    cmd = ["git"] + (["--git-dir", git_dir] if git_dir else []) \
        + ["ls-files", "-z"]
    out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, check=True)
    return [Path(p) for p in out.stdout.decode("utf-8", "replace").split("\0")
            if p]


def _strip_comment(line):
    """A trailing `# ...` in a command example is prose, not flags. A line
    that is itself a comment keeps its text: the skill spells the Windows
    twin command as a comment and it is still an invocation."""
    cut = re.search(r"\s+#", line)
    return line[:cut.start()] if cut and line[:cut.start()].strip() else line


def _invocation_units(path):
    """Every place `path` shows running the appliers: a fenced command line or
    an inline code span (which may wrap lines) that names apply.sh/apply.ps1.
    Prose flags named outside an invocation are covered by the manual audit,
    not here — attribution without the script name is guesswork."""
    text = path.read_text(encoding="utf-8")
    units, prose, infence = [], [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            infence = not infence
            continue
        if infence:
            line = _strip_comment(line)
            if SH_TOKEN_RE.search(line) or PS_TOKEN_RE.search(line):
                units.append(line)
        else:
            prose.append(line)
    units += [s for s in SPAN_RE.findall("\n".join(prose))
              if SH_TOKEN_RE.search(s) or PS_TOKEN_RE.search(s)]
    return units


def _unit_flags(unit):
    """(script, flag) for every flag claim in one invocation, claims read as
    whitespace tokens so `--go-shaX` is one claim and not `--go-sha` + "X".
    A unit naming one script attributes every flag to it — so a
    PowerShell-style flag in a bash twin's invocation is the bash script's to
    handle, and it refuses it. Naming both twins splits by style: --kebab is
    the bash twin's, -CamelCase the PowerShell twin's."""
    sh, ps = bool(SH_TOKEN_RE.search(unit)), bool(PS_TOKEN_RE.search(unit))
    pairs = []
    for token in unit.split():
        m = APPLY_FLAG_TOKEN_RE.fullmatch(token)
        if not m:
            continue
        flag = token.split("=", 1)[0]
        if sh and ps:
            script = APPLY_SH if flag.startswith("--") else APPLY_PS1
        else:
            script = APPLY_SH if sh else APPLY_PS1
        pairs.append((script, flag))
    return pairs


def _apply_sh_flags():
    """The flags apply.sh's argument loop accepts (its `case "$1"` arms)."""
    text = APPLY_SH.read_text(encoding="utf-8")
    loop = re.search(r"(?ms)^while \[\[ \$# -gt 0 \]\]; do\n(.*?)^done$", text)
    if loop is None:
        return set()  # reshaped; the vacuity guard fails loudly
    return set(re.findall(r"(?m)^\s*(--[a-z][a-z0-9-]*)(?:=\*)?\)",
                          loop.group(1)))


def _apply_ps1_flags():
    """The flags apply.ps1's top-level param() block accepts."""
    text = APPLY_PS1.read_text(encoding="utf-8")
    try:
        start = text.index("param(", text.index("[CmdletBinding()]")) \
            + len("param(")
    except ValueError:
        return set()  # reshaped; the vacuity guard fails loudly
    depth, i = 1, start
    while depth:
        depth += (text[i] == "(") - (text[i] == ")")
        i += 1
    return {"-" + n for n in re.findall(r"\]\$(\w+)", text[start:i - 1])}


def _wrapped_repo_paths(text):
    """Every backticked repo path in `text` that spans a line break.

    A wrap hides inside one span or between two spans whose join is one
    path. Fenced blocks are stripped before the single backticks are paired:
    the fences' own backticks would shift the pairing and hide the wrap."""
    prose, infence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            infence = not infence
        elif not infence:
            prose.append(line)
    prose = "\n".join(prose)
    found, spans = [], list(SPAN_RE.finditer(prose))
    for i, span in enumerate(spans):
        body = span.group(1)
        if "\n" in body and REPO_PATH_RE.fullmatch(
                WRAP_GAP_RE.sub("", body)):
            found.append(body)
        if (i + 1 < len(spans) and body.endswith("/")
                and WRAP_GAP_RE.fullmatch(
                    prose[span.end():spans[i + 1].start()])
                and REPO_PATH_RE.fullmatch(body + spans[i + 1].group(1))):
            found.append(prose[span.start():spans[i + 1].end()])
    return found
