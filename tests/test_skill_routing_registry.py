#!/usr/bin/env python3
"""Guard: the routing-registry skill stays loadable, resolvable and true.

A skill is documentation with a contract: an agent follows its links, runs the
commands it names, and trusts the paths it points at. Each of those rots
silently — a renamed script leaves a command that fails only when somebody
executes it, a moved reference breaks a link nobody clicks, and a folded-in
skill leaves two half-homes for one rule. This asserts the six ways the
routing-registry skill (which folded in and retired its predecessor) can lie:

    * the index budget (SKILL.md <= 150 lines — it must stay an index) and the
      reference budget (each references/*.md <= 500 lines);
    * every relative markdown link resolves to a file that exists;
    * the folded-in predecessor is gone — no skill directory, no relative link
      anywhere in the repo points at it, and no prose mention names it outside
      the allowed history (the exception list in the test below);
    * every script path the skill names exists in the repo;
    * the add/modify and remove paths lose a required step while every file and
      link still resolves — each step's heading/keyword must be present;
    * an apply.sh / apply.ps1 invocation names a flag the script's own
      argument handling refuses (apply.sh exits 2 on unknown arguments, so
      such a command fails when it is run).

Run directly:

    python3 tests/test_skill_routing_registry.py
"""
import re
import subprocess
import unittest
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
            r"(?m)^\s*pytest\s*$",
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


class RoutingRegistrySkillTests(unittest.TestCase):
    def test_index_is_at_most_150_lines(self):
        self.assertTrue(INDEX.is_file(), "missing %s" % INDEX)
        lines = len(INDEX.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            lines, INDEX_MAX_LINES,
            "SKILL.md is %d lines (max %d): the index must stay an index, with "
            "the substance in references/" % (lines, INDEX_MAX_LINES))

    def test_every_reference_is_at_most_500_lines(self):
        refs = sorted(REFERENCES.glob("*.md"))
        self.assertTrue(refs, "no references/*.md under %s" % REFERENCES)
        over = ["%s:%d" % (p.name, len(p.read_text(encoding="utf-8").splitlines()))
                for p in refs
                if len(p.read_text(encoding="utf-8").splitlines())
                > REFERENCE_MAX_LINES]
        self.assertEqual(
            over, [],
            "reference page(s) over %d lines: %s" % (REFERENCE_MAX_LINES,
                                                     ", ".join(over)))

    def test_every_relative_link_resolves(self):
        broken = ["%s -> %s" % (p.relative_to(ROOT), t)
                  for p in _skill_files()
                  for t in _links(p)
                  if not (p.parent / t).exists()]
        self.assertEqual(
            broken, [],
            "dangling relative link(s) in the routing-registry skill: %s"
            % "; ".join(broken))

    def test_combo_create_is_gone(self):
        self.assertFalse(
            (ROOT / ".agents" / "skills" / NEEDLE).exists(),
            ".agents/skills/%s still exists; routing-registry "
            "replaced it and must be its only home" % NEEDLE)
        stale = ["%s -> %s" % (p.relative_to(ROOT), t)
                 for p in _repo_markdown()
                 for t in _links(p)
                 if NEEDLE in t]
        self.assertEqual(
            stale, [],
            "relative link(s) still point at the removed %s skill: %s"
            % (NEEDLE, "; ".join(stale)))
        # A prose mention is a pointer too. The allowed exceptions, listed
        # explicitly and valid nowhere else: CHANGELOG.md (the entry that
        # removed the skill may name it); a dated history doc — a file whose
        # name carries a date (2026-10-01 or 20261001); anything under logs/
        # (run logs, handoff session records).
        mentions = [
            str(rel) for rel in _tracked_files()
            if (ROOT / rel).is_file()
            and NEEDLE.encode("ascii") in (ROOT / rel).read_bytes()
            and rel.name != "CHANGELOG.md"
            and not DATE_IN_NAME_RE.search(rel.name)
            and rel.parts[:1] != ("logs",)]
        self.assertEqual(
            mentions, [],
            "prose mention(s) of the removed %s skill outside the allowed "
            "history (CHANGELOG.md / dated history docs / logs/): %s"
            % (NEEDLE, ", ".join(mentions)))

    def test_backticked_repo_paths_do_not_wrap(self):
        """Wrapped paths evade SCRIPT_RE and are not copyable repo paths.

        A wrap hides inside one backtick span or between two spans whose
        join is one path, the break may carry a list or quote marker, and
        fenced blocks must not shift span pairing. One fixture string per
        form is pinned first — a guard that cannot flag these asserts
        nothing — and the real skill must stay clean."""
        fixtures = [
            ("one span", "run `configuration/\nomniroute/apply.ps1`"),
            ("one span, list marker",
             "run `configuration/\n- omniroute/apply.ps1`"),
            ("one span, quote marker",
             "run `configuration/\n> omniroute/apply.ps1`"),
            ("one span, docs/", "see `docs/\nagents/leaf-contract.md`"),
            ("one span, .agents/",
             "see `.agents/\nskills/routing-registry/SKILL.md`"),
            ("one span, lib/", "see `lib/\nwindows/AutoOS.Ui.psm1`"),
            ("one span, .github/", "see `.github/\nworkflows/ci.yml`"),
            ("two spans", "run `configuration/`\n`omniroute/apply.ps1`"),
            ("two spans, indented", "run `logs/routing/`\n  `availability.json`"),
            ("two spans, list marker",
             "run `logs/routing/`\n- `availability.json`"),
            ("two spans, quote marker",
             "run `logs/routing/`\n> `availability.json`"),
            ("fence must not shift pairing",
             "```\necho `a` b `c\n```\nrun `configuration/"
             "\nomniroute/apply.ps1`"),
        ]
        for name, text in fixtures:
            self.assertTrue(
                _wrapped_repo_paths(text),
                "guard missed the %s wrap fixture: %r" % (name, text))
        wrapped = ["%s: %r" % (path.relative_to(ROOT), s)
                   for path in _skill_files()
                   for s in _wrapped_repo_paths(
                       path.read_text(encoding="utf-8"))]
        self.assertEqual(wrapped, [], "wrapped repo path(s): %s" % wrapped)

    def test_every_script_path_named_exists(self):
        missing = ["%s: %s" % (p.relative_to(ROOT), s)
                   for p in _skill_files()
                   for s in _script_paths(p)
                   if not (ROOT / s).is_file()]
        self.assertEqual(
            missing, [],
            "script path(s) named by the skill do not exist in the repo: %s"
            % "; ".join(missing))

    def test_add_modify_and_remove_carry_every_required_step(self):
        """The paths are procedures, not pages: a step can vanish while every
        file and link still resolves. Each required step's heading or command
        keyword must be present in the page that owns the path."""
        for name, steps in REQUIRED_STEPS.items():
            path = REFERENCES / name
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            missing = [step for step, patterns in steps
                       if not all(re.search(p, text) for p in patterns)]
            self.assertEqual(
                missing, [],
                "%s lost required step(s) — heading/keyword gone: %s"
                % (name, ", ".join(missing)))
            # The checker must be able to fail: against no text every step is
            # reported — a pattern that cannot miss asserts nothing.
            self.assertEqual(
                [step for step, patterns in steps
                 if not all(re.search(p, "") for p in patterns)],
                [step for step, _ in steps],
                "step pattern(s) match the empty string: %s" % name)

    def test_every_apply_invocation_flag_is_handled_by_the_script(self):
        """Every flag the skill names in an apply.sh / apply.ps1 invocation
        must appear in that script's argument handling."""
        handled = {APPLY_SH: _apply_sh_flags(), APPLY_PS1: _apply_ps1_flags()}
        problems = [
            "%s: %s names %s" % (p.relative_to(ROOT), flag, script.name)
            for p in _skill_files()
            for unit in _invocation_units(p)
            for script, flag in _unit_flags(unit)
            if flag not in handled[script]]
        self.assertEqual(
            problems, [],
            "flag(s) named in the skill's apply invocations that the script's "
            "argument handling does not accept: %s" % "; ".join(problems))

    def test_the_apply_flag_check_is_not_vacuous(self):
        """If a parser stops matching the script's argument handling, every
        flag above would pass vacuously — pin the parse and the failure shape."""
        self.assertTrue(
            {"--dry-run", "--probe", "--drift", "--drift-against",
             "--allow-drift", "--go", "--go-sha", "--go-offline"}
            <= _apply_sh_flags(),
            "apply.sh argument loop not parsed (empty or partial)")
        self.assertTrue(
            {"-DryRun", "-Probe", "-Go", "-GoSha", "-GoOffline"}
            <= _apply_ps1_flags(),
            "apply.ps1 param() block not parsed (empty or partial)")
        self.assertEqual(
            _unit_flags("bash configuration/omniroute/apply.sh --dry-runn"),
            [(APPLY_SH, "--dry-runn")])
        self.assertNotIn("--dry-runn", _apply_sh_flags())
        self.assertEqual(
            _unit_flags("bash configuration/omniroute/apply.sh --go-shaX"),
            [(APPLY_SH, "--go-shaX")],
            "a malformed flag must be read as one claim, not as --go-sha")
        self.assertNotIn("--go-shaX", _apply_sh_flags())
        self.assertEqual(
            _unit_flags("configuration/omniroute/apply.ps1 -DryRunX"),
            [(APPLY_PS1, "-DryRunX")])
        self.assertNotIn("-DryRunX", _apply_ps1_flags())
        self.assertEqual(
            _unit_flags("apply.sh -DryRun"), [(APPLY_SH, "-DryRun")],
            "a PowerShell-style flag in a bash twin's invocation belongs to "
            "the bash twin, which refuses it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
