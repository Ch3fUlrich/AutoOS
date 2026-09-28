#!/usr/bin/env python3
"""Map changed registry ids to the tests that name them.

    python3 tools/affected-tests.py t1-orchestrator muse-spark
    python3 tools/affected-tests.py --from-diff HEAD~1
    bash tests/run-tests.sh  --filter "$(python3 tools/affected-tests.py --from-diff HEAD~1 --format filter)"
    pwsh tests/run-tests.ps1 -Filter (python3 tools/affected-tests.py --from-diff HEAD~1 --format filter)

Why this exists (lessons PROVPIN, MUSEPIN 2026-09-27): after a route or provider
flip a worker picks its own --filter terms, the shell and Pester cases naming the
changed id never run, and CI goes red twice for want of a list nobody derived. The
list is derivable, so it is derived here: every test block whose text mentions one
of the ids, matched only where the id ends, across all three suites. Over-inclusion
is allowed and a miss is not, so the match is strict against a *longer* id
(`t1-orchestrator` does not name `t1-orchestrator-clean`) and loose against a name
this id generated (`omniroute-t1-orchestrator` is).

What it reads
    tests/linux/*.sh      `if it "name"; then` blocks
    tests/run-tests.ps1   `Test-Case 'name' {` blocks
    tests/test_*.py       `test_*` functions, module level or in a class pytest
                          collects (a `Test*` name or a TestCase subclass),
                          located with `ast`
A shell or Pester block runs from its header to the next header or the next
`describe` / `Describe-Group`, whichever comes first, so the text is credited to
one case only and the header's own words count as a mention. A heredoc
(`<<EOS` ... `EOS`) or PowerShell here-string (`@'` ... `'@`) body is data, not
code: the suites write whole fake scripts into scratch files that way, and a
header-shaped line in one neither starts a case nor ends the case holding it. The
heredoc may open inside a double-quoted command substitution
(`out="$(python3 - <<'PY'` ... `PY` ... `)"`) - the suites' dominant shape - which
is why the quote and substitution state is carried across lines rather than reset
by each one: bash reads a substitution's contents as code even while an outer `"`
is still open. A `<<` inside `(( … ))` or `$(( … ))`, or one immediately followed
by `=` (`x<<=1`), is bash's shift operator and opens nothing, so arithmetic
contexts are carried across lines the same way.

If a file's scan ends with state still open - a heredoc delimiter that never
arrives, a quote or substitution left unterminated - the masking for that one file
is discarded and its headers matched plain, with a single line on stderr naming it.
An unfinished scan means this parser and bash disagree about where the data is, and
the disagreement that hurts reads the rest of the file as one heredoc body, so
every real case below the bad construct goes missing. Plain matching can only add
phantom cases, and over-inclusion costs one extra test run: it is the safe side to
fail on (review AFFFIX3).

Known limits, accepted rather than fixed
    * A `case` pattern's `)` inside a `$( … )` substitution sits at paren depth 1,
      so it closes the substitution early and everything after it is read outside.
      A test pins what that costs today, so a grammar fix flips it deliberately.
    * `<<$var` names its delimiter at run time and is left untracked, so its body
      is read as code.
    * The deprecated `$[expr]` arithmetic form is not a recognised context, so a
      shift written there still opens a heredoc - and, because the delimiter it
      invents is never found, the fallback above catches it as an unbalanced file.

Outputs
    --format filter       one comma-separated string for run-tests.sh --filter /
                          run-tests.ps1 -Filter and nothing else, so it is safe in
                          $( ). Both runners split their filter on commas and match
                          each term as a substring of a *test name*, so a term is
                          always a comma-free, space-free substring of a name that
                          is affected - one exception below. Over-inclusion is
                          intended: a term that also names a few extra cases cannot
                          miss one. A name with no word character in it at all has
                          no such token to offer, so its whole name becomes the
                          term; when even that is impossible - a comma in the name -
                          it is reported on stderr as unfilterable, because silence
                          here is how a case goes unrun. Exit status stays 0 either
                          way.
    --format pytest       space-separated node ids (tests/test_x.py::Class::method)
    --format human        the table: which ids hit, in which runner, and where

--from-diff <rev> takes the ids instead of the command line: the keys of
catalog/ai-registry.json's routes / providers / models sections whose value
differs between <rev> and the worktree (an added or removed key counts as
changed). Other sections are not ids - `clients` and `policy` hold words like
"qoder" that would drag in half the suite as if they were registry entries.

Exit status is 0 whenever the scan ran, including when nothing is affected: an
empty filter says "these ids touch no test", which is an answer, not a failure.
run-tests.sh refuses an empty filter on its own - host OOM guard, R-host-08.
"""
import argparse
import ast
import bisect
import json
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "catalog/ai-registry.json"
# Registry sections whose keys are ids a test may name. `clients` is out on purpose.
ID_SECTIONS = ("routes", "providers", "models")
# Shortest name token accepted as a filter term when no id appears in the name.
MIN_TERM_LEN = 4
RUNNERS = ("sh", "ps1", "pytest")

# A test header and the group heading that also closes a block's region. Bash
# quotes a name with `"`, PowerShell with either, and a name may hold the other
# kind of apostrophe, so the closing quote is a backreference.
SH_HEADER = re.compile(r"""^[ \t]*(?:if[ \t]+)?it[ \t]+(?P<q>["'])(?P<name>[^\n]*?)(?P=q)""", re.M)
SH_GROUP = re.compile(r"^[ \t]*describe[ \t]", re.M)
PS_HEADER = re.compile(r"""^[ \t]*Test-Case[ \t]+(?P<q>["'])(?P<name>[^\n]*?)(?P=q)""", re.M)
PS_GROUP = re.compile(r"^[ \t]*Describe-Group[ \t]", re.M)

# Neither header pattern sees whether its line is code. The suites write whole
# fake scripts into a scratch file (`cat <<EOS` ... `EOS`) and PowerShell carries
# them in a here-string (`@'` ... `'@`), and every one of those body lines is
# data: a header-shaped line in it used to end the real case above, hiding that
# case's own mentions (review F1). `(?<!<)<<(?!<)` is a heredoc opener and never
# the one-line `<<<` here-string; `(?!=)` excludes the `<<=` left-shift
# assignment, whose `=` the delimiter charset would otherwise swallow as the
# whole delimiter name (review AFFFIX3). PowerShell's opener must end its line.
SHELL_OPENER = re.compile(r"(?<!<)<<(?!<)(?!=)")
HEREDOC_NAME = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")
PS_OPENER = re.compile(r"""(?:^|[\s=(,{$])@(['"])$""")

# A filter term: whitespace-free and comma-free by construction, and a literal
# substring of the test name it came from. ':' and '+' are out of the charset -
# they end a token in the suite's names without adding anything a filter needs.
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@-]*")

# Characters that continue an id to the right. Registry ids are kebab-case model
# names, so `-` and `_` belong inside one; `.` does not - it is the sentence's own
# punctuation or a file extension, and a mention followed by either is still a
# mention. `\b` reads `-` as a word break, which is what let `t1-orchestrator`
# match inside `t1-orchestrator-clean` (review F2).
ID_CHARS = r"[A-Za-z0-9_\-]"


class Test(NamedTuple):
    """One case: its runner, name, location, and the text that may name an id."""
    runner: str
    name: str
    path: Path           # relative to the scanned root
    line: int
    body: str
    ids: tuple = ()      # filled in by affected()

    @property
    def location(self) -> str:
        return "%s:%d" % (self.path.as_posix(), self.line)


def _line_starts(text: str):
    """Offset of every line, for turning a character offset into a line number."""
    starts, pos = [0], 0
    for line in text.split("\n"):
        pos += len(line) + 1
        starts.append(pos)
    return starts


def _code_view(line: str, frames=()):
    """The line with quoted content and the trailing comment blanked out.

    Same length, same offsets, so a match found in it is still the right place in
    the original: `echo "<<"` and `# see <<EOF` must not read as heredocs, and
    neither must the `<<` of an arithmetic shift.

    `frames` is the lexical state carried in from the previous line - a list of
    open contexts, innermost last, each one `("q", quote_char)`,
    `("sub", delimiter, paren_depth)` or `("arith", paren_depth)`. It is returned
    updated, because a quote really does span lines: the suites' dominant idiom is

        out="$(python3 - 2>&1 <<'PY'
        ...
        PY
        )"

    and bash parses a command substitution's contents as code even while the outer
    `"` is still open. Blanking from that `"` to the end of the line - what a
    line-by-line reset does - hides the `<<'PY'`, so the body is read as code and a
    header-shaped line in it starts a case that does not exist (review AFFFIX2).
    """
    stack = [tuple(f) for f in frames]
    out, index, size = list(line), 0, len(line)
    while index < size:
        ch = line[index]
        frame = stack[-1] if stack else None
        if frame and frame[0] == "q":
            quote = frame[1]
            out[index] = " "
            if ch == quote:
                stack.pop()
            elif quote == '"':
                if ch == "\\" and index + 1 < size:
                    out[index + 1] = " "
                    index += 1
                elif (ch == "$" and index + 2 < size and line[index + 1] == "("
                      and line[index + 2] == "("):
                    # `$(( ))` inside a string is arithmetic, not a substitution.
                    out[index], out[index + 1] = "$", "("
                    stack.append(("arith", 2))
                    index += 2
                elif ch == "$" and index + 1 < size and line[index + 1] == "(":
                    # The substitution is code, not string data.
                    out[index], out[index + 1] = "$", "("
                    stack.append(("sub", "(", 1))
                    index += 1
                elif ch == "`":
                    out[index] = "`"
                    stack.append(("sub", "`", 0))
            index += 1
            continue
        if ch == "#" and (not index or line[index - 1] in " \t"):
            return "".join(out[:index]), stack       # the rest is a comment
        if frame and frame[0] == "arith" and ch == "<":
            # Inside an arithmetic expression `<<` is the shift operator, and bash
            # reads no heredoc there at all (review AFFFIX3).
            out[index] = " "
            index += 1
            continue
        if ch in "'\"":
            out[index] = " "
            stack.append(("q", ch))
        elif ch == "\\" and index + 1 < size:
            out[index + 1] = " "
            index += 1
        elif ch == "`":
            # A backtick closes the substitution it opened; anywhere else it starts
            # one, whose contents are code too.
            if frame and frame[0] == "sub" and frame[1] == "`":
                stack.pop()
            else:
                stack.append(("sub", "`", 0))
        elif (ch == "$" and index + 2 < size and line[index + 1] == "("
              and line[index + 2] == "("):
            stack.append(("arith", 2))
            index += 2
        elif ch == "$" and index + 1 < size and line[index + 1] == "(":
            stack.append(("sub", "(", 1))
            index += 1
        elif ch == "(" and index + 1 < size and line[index + 1] == "(":
            # `(( ))` is an arithmetic expression, and its `<<` is a shift.
            stack.append(("arith", 2))
            index += 1
        elif ch == "(" and frame and frame[0] == "arith":
            stack[-1] = ("arith", frame[1] + 1)
        elif ch == ")" and frame and frame[0] == "arith":
            if frame[1] > 1:
                stack[-1] = ("arith", frame[1] - 1)
            else:
                stack.pop()
        elif ch == "(" and frame and frame[0] == "sub" and frame[1] == "(":
            stack[-1] = ("sub", "(", frame[2] + 1)
        elif ch == ")" and frame and frame[0] == "sub" and frame[1] == "(":
            # `( ... )` grouping inside the substitution is balanced against itself;
            # only the depth-1 `)` closes the `$( `.
            if frame[2] > 1:
                stack[-1] = ("sub", "(", frame[2] - 1)
            else:
                stack.pop()
        index += 1
    return "".join(out), stack


def _heredoc_opener(line: str, at: int):
    """Parse `<<DELIM`, `<<-DELIM`, `<<'DELIM'`, `<<"DELIM"` or `<<\\DELIM` at `at`.

    `at` is just past the `<<`. Returns (delimiter, tab-strip) or None when the
    line does not open a heredoc there - `<<$var` names its delimiter at run time
    and is left untracked rather than guessed at.
    """
    tab_strip = at < len(line) and line[at] == "-"
    if tab_strip:
        at += 1
    if at >= len(line):
        return None
    if line[at] in "'\"":
        quote, at = line[at], at + 1
        end = line.find(quote, at)
        name = line[at:end] if end > at else ""
    else:
        if line[at] == "\\":
            at += 1
        matched = HEREDOC_NAME.match(line, at)
        name = matched.group() if matched else ""
    return (name, tab_strip) if name else None


def _ps_opener(line: str) -> str:
    """The quote char of a here-string opened at the end of this line, else "".

    PowerShell puts its opening delimiter at the end of a line and nothing else
    there, and the parity check rejects a line that merely ends inside a string -
    `Write-Host 'text @'` closes one, it does not start a here-string.
    """
    match = PS_OPENER.search(line.rstrip())
    if not match:
        return ""
    quote = match.group(1)
    if line[:match.start() + 1].count(quote) % 2:
        return ""
    return quote


def masked_lines(text: str, syntax: str):
    """The masked lines of one file's text, and whether the scan ended balanced.

    sh: each heredoc body, from the line after the opener through the line
    carrying its delimiter; a line may open two heredocs, so openers queue and
    each delimiter closes only the body it ends. The lexical state - which quotes
    and command substitutions are still open - is carried across lines, and frozen
    while a body is being read, since body text is not shell.
    ps1: each here-string body, closed by the delimiter at column 0 - PowerShell
    lets nothing precede it.

    The second value is False when the scan reaches the end of the file with a
    heredoc delimiter, a here-string quote, a quote or a substitution still open:
    one construct left unfinished means the parser no longer knows where bash
    reads data, so the caller must not trust the masking it just computed
    (regions() re-reads the file plainly).
    """
    lines = text.split("\n")
    masked = set()
    if syntax == "ps1":
        open_quote = ""
        for index, raw in enumerate(lines):
            line = raw.rstrip("\r")
            if open_quote:
                masked.add(index)
                if line.startswith(open_quote + "@"):
                    open_quote = ""
                continue
            open_quote = _ps_opener(line)
        return masked, not open_quote
    pending, frames = [], []
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r")
        if pending:
            masked.add(index)
            head = line.lstrip("\t") if pending[0][1] else line
            if head.rstrip() == pending[0][0]:
                del pending[0]
            continue
        view, frames = _code_view(line, frames)
        for match in SHELL_OPENER.finditer(view):
            opener = _heredoc_opener(line, match.end())
            if opener:
                pending.append(opener)
    return masked, not pending and not frames


def regions(text: str, header: re.Pattern, group: re.Pattern, syntax: str,
            source: str = ""):
    """Yield (name, line, body) for every test block in one runner's source text.

    A block starts where its own comment block starts - this suite writes the
    explaining comment above the `if it`, so that text belongs to the case below
    it, not to the case before - and ends at the next block's start, the next
    `describe` / `Describe-Group`, or the end of the file, whichever comes first.
    A header or group heading inside a heredoc / here-string body is data, so it
    neither opens a block nor closes one; its text still counts toward the block
    that contains it.

    `source` names the file on stderr. When this file's scan ends unbalanced
    (masked_lines), its masking is thrown away and the headers are matched plain:
    the far more dangerous failure of a mis-tracked construct is the reader sitting
    inside a phantom heredoc to the end of the file, which hides *every* case below
    it - one real bug report in the suites is worth more phantoms than silence
    (review AFFFIX3). `source` empty means the caller cannot name a file, so the
    fallback happens without a word.
    """
    lines = text.split("\n")
    starts = _line_starts(text)
    line_of = lambda offset: bisect.bisect_right(starts, offset) - 1
    masked, balanced = masked_lines(text, syntax)
    if not balanced:
        if source:
            sys.stderr.write("affected-tests: %s: unbalanced scan at EOF, "
                             "masking disabled for this file\n" % source)
        masked = set()
    found = [m for m in header.finditer(text) if line_of(m.start()) not in masked]
    if not found:
        return
    heads = [line_of(m.start()) for m in found]
    groups = sorted(line_of(m.start()) for m in group.finditer(text)
                    if line_of(m.start()) not in masked)
    begins = []
    for head in heads:
        begin = head
        while begin > 0 and (not lines[begin - 1].strip()
                             or lines[begin - 1].lstrip().startswith("#")):
            begin -= 1
        begins.append(begin)
    for index, head in enumerate(heads):
        begin = begins[index]
        limit = begins[index + 1] if index + 1 < len(begins) else len(lines)
        end = min([limit, len(lines)] + [g for g in groups if begin < g < limit])
        yield found[index].group("name"), head + 1, "\n".join(lines[begin:end])


def parse_runner_text(rel: Path, text: str, runner: str):
    """The Test list one file contributes to the shell or the Pester runner."""
    header, group = (SH_HEADER, SH_GROUP) if runner == "sh" else (PS_HEADER, PS_GROUP)
    return [Test(runner=runner, name=name, path=rel, line=line, body=body)
            for name, line, body in regions(text, header, group, runner,
                                            rel.as_posix())]


def parse_pytest_file(rel: Path, text: str):
    """The Test list a pytest file contributes: node id plus the function's source."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    tests = []

    def add(class_name: Optional[str], node):
        body = ast.get_source_segment(text, node) or ""
        name = ("%s::%s::%s" % (rel.as_posix(), class_name, node.name) if class_name
                else "%s::%s" % (rel.as_posix(), node.name))
        tests.append(Test(runner="pytest", name=name, path=rel, line=node.lineno, body=body))

    def is_test_case(node):
        return any((isinstance(b, ast.Name) and b.id.endswith("TestCase")) or
                   (isinstance(b, ast.Attribute) and b.attr.endswith("TestCase"))
                   for b in node.bases)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            add(None, node)
        elif isinstance(node, ast.ClassDef):
            if not (node.name.startswith("Test") or is_test_case(node)):
                continue
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name.startswith("test_"):
                    add(node.name, member)
    return tests


def discover(root: Path = ROOT):
    """Every case of all three suites, in file order, as `Test` records."""
    root = Path(root)
    tests = []
    linux = root / "tests" / "linux"
    for path in sorted(linux.glob("*.sh")) if linux.is_dir() else []:
        tests += parse_runner_text(path.relative_to(root), _read(path), "sh")
    ps1 = root / "tests" / "run-tests.ps1"
    if ps1.is_file():
        tests += parse_runner_text(ps1.relative_to(root), _read(ps1), "ps1")
    unit = root / "tests"
    for path in sorted(unit.glob("test_*.py")) if unit.is_dir() else []:
        tests += parse_pytest_file(path.relative_to(root), _read(path))
    return tests


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def id_pattern(an_id: str) -> re.Pattern:
    """Match an id where that id ends: no id character may follow it.

    Registry ids are kebab-case, so `-` and `_` continue one: `t1-orchestrator-clean`
    and `auto-added` are not mentions of `t1-orchestrator` and `auto` - each is its
    own registry entry or plain English - and `\\b` let both through because it
    reads `-` as a word break (review F2). A `.` is not an id character, so
    `t1-orchestrator.json` and a sentence-ending `t1-orchestrator.` still count:
    dropping those would be a *miss*, which - unlike over-inclusion - this tool may
    not emit. The left side keeps the plain word boundary for the same reason:
    `omniroute-t1-orchestrator` is the profile file this route generates, and a
    strict left edge hides 24 blocks here - among them every service case naming
    that profile and every cap case naming `claude-opus-4-6`, the model the route
    serves. What that looseness lets back in (`chip-auto`, `AUTOOS_WIPE_TARGET`)
    only ever runs one more case.
    """
    if not an_id:
        return re.compile(r"(?!)")
    word = lambda ch: ch.isalnum() or ch == "_"
    left = r"\b" if word(an_id[:1]) else ""
    return re.compile(left + re.escape(an_id) + r"(?!" + ID_CHARS + r")", re.IGNORECASE)


def affected(tests, ids):
    """The tests whose text mentions one of the ids, each tagged with the ids it mentions."""
    patterns = [(an_id, id_pattern(an_id)) for an_id in ids]
    hits = []
    for test in tests:
        found = tuple(an_id for an_id, pattern in patterns if pattern.search(test.body))
        if found:
            hits.append(test._replace(ids=found))
    return hits


def name_matches(runner: str, term: str, name: str) -> bool:
    """How that runner would decide: bash `==` is case-sensitive, `-like` is not."""
    return term in name if runner == "sh" else term.lower() in name.lower()


def choose_terms(hits, corpus, runner: str):
    """(terms, {test: term}, [name, ...]) - a filter term per affected case, and the tight list.

    A term must be a substring of an affected *name* - that is all either runner
    can match - so it comes out of the name itself: a token carrying one of the
    ids when the name has one, else the token that pulls in the fewest other
    cases, tie-broken toward the one naming the most affected cases and then the
    longest. A term contained inside another is dropped: the shorter one already
    selects everything the longer one does. Collateral is therefore small and the
    list is exact enough to run, but its words come from test names, not from the
    registry - the table format shows which term selects which case. A name with
    no token in it at all (`✓ ✗ ✗`) has nothing to choose from, so the whole name
    is the term, and when even that is impossible - a comma in it would split the
    term in --filter - its name is returned as unfilterable for stderr instead of
    being dropped in silence.
    """
    def collateral(term):
        return sum(1 for name in corpus if name_matches(runner, term, name))

    def covers(term):
        return sum(1 for test in hits if name_matches(runner, term, test.name))

    picks, unfilterable = [], []
    for test in hits:
        pool = TOKEN.findall(test.name)
        carrying = [t for t in pool if any(an_id.lower() in t.lower() for an_id in test.ids)]
        candidates = carrying or [t for t in pool if len(t) >= MIN_TERM_LEN] or pool
        if candidates:
            picks.append(min(candidates, key=lambda t: (collateral(t), -covers(t), -len(t), t)))
        elif test.name.strip() and "," not in test.name:
            picks.append(test.name.strip())
        else:
            unfilterable.append(test.name)
    terms = [t for t in picks if not any(o != t and name_matches(runner, o, t) for o in picks)]
    assignment = {}
    for test in hits:
        hit = [t for t in terms if name_matches(runner, t, test.name)]
        if not hit:
            # No term can be missing a case it was chosen for; if one somehow is,
            # the case's longest name token selects it and says so in the table.
            pool = TOKEN.findall(test.name)
            if pool:
                rescue = max(pool, key=len)
                terms.append(rescue)
                hit = [rescue]
        if hit:
            assignment[test] = hit[0]
    return (sorted(set(terms), key=lambda t: (t.lower(), len(t), t)), assignment,
            unfilterable)


def selection(hits_by_runner, corpora):
    """The filter string, which term selects which shell / Pester case, and what cannot."""
    terms, assigned, unfilterable = [], {}, []
    for runner in ("sh", "ps1"):
        hits = hits_by_runner.get(runner, [])
        if hits:
            chosen, assignment, wordless = choose_terms(hits, corpora.get(runner, []), runner)
            terms += chosen
            assigned.update(assignment)
            unfilterable += wordless
    joined = ",".join(sorted(set(terms), key=lambda t: (t.lower(), len(t), t)))
    return joined, assigned, unfilterable


def git_show(root: Path, rev: str, path: Path) -> Optional[str]:
    """`git show <rev>:<path>`, or None when the file did not exist at that rev."""
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        rel = str(path)
    proc = subprocess.run(["git", "-C", str(root), "show", "%s:%s" % (rev, rel)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        if "does not have" in proc.stderr or "exists on disk" in proc.stderr:
            return None
        sys.exit("affected-tests: git show %s:%s failed: %s"
                 % (rev, rel, proc.stderr.strip()))
    return proc.stdout


_MISSING = object()


def changed_registry_ids(root: Path, rev: str, registry: Path):
    """Keys of the id sections whose value differs between <rev> and the worktree."""
    try:
        old_doc = json.loads(git_show(root, rev, registry) or "{}")
    except json.JSONDecodeError as exc:
        sys.exit("affected-tests: %s at %s is not JSON: %s" % (registry, rev, exc))
    if not Path(registry).is_file():
        sys.exit("affected-tests: no registry at %s" % registry)
    try:
        new_doc = json.loads(_read(Path(registry)))
    except json.JSONDecodeError as exc:
        sys.exit("affected-tests: %s is not JSON: %s" % (registry, exc))
    ids = []
    for section in ID_SECTIONS:
        old, new = old_doc.get(section) or {}, new_doc.get(section) or {}
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        for key in sorted(set(old) | set(new)):
            if old.get(key, _MISSING) != new.get(key, _MISSING):
                ids.append(key)
    return ids


LABELS = {"sh": "sh        (tests/run-tests.sh --filter)",
          "ps1": "ps1       (tests/run-tests.ps1 -Filter)",
          "pytest": "pytest  (python3 -m pytest)"}


def human_table(ids, hits, terms, nodes, assigned) -> str:
    """The readable answer: the ids, what hit, which term selects it, and where."""
    out = ["ids: %s" % (", ".join(ids) if ids else "(none)")]
    if not hits:
        out.append("no test in tests/linux/*.sh, tests/run-tests.ps1 or "
                   "tests/test_*.py mentions any of them")
        return "\n".join(out)
    for runner in RUNNERS:
        rows = [t for t in hits if t.runner == runner]
        if not rows:
            continue
        out.append("\n%s: %d affected" % (LABELS[runner], len(rows)))
        for test in sorted(rows, key=lambda t: (t.location, t.name)):
            term = assigned.get(test, test.name if runner == "pytest" else "-")
            out.append("  %-52s %-18s %-24s %s" %
                       (test.name[:52], term[:18], test.location, ",".join(test.ids)))
    if terms:
        out.append("\nfilter terms (comma-separated, for both runners):")
        out.append("  " + terms)
    if nodes:
        out.append("\npytest node ids:")
        out.append("  " + nodes)
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="affected-tests", description=__doc__.splitlines()[0],
        epilog="see the module docstring for the shapes it reads and what each format emits")
    parser.add_argument("ids", nargs="*", metavar="ID",
                        help="route, provider or model ids (or any string a test may name)")
    parser.add_argument("--from-diff", metavar="REV",
                        help="take the ids from %s entries changed since REV" % REGISTRY)
    parser.add_argument("--registry", metavar="PATH",
                        help="registry to diff (default: <root>/%s)" % REGISTRY)
    parser.add_argument("--root", metavar="DIR", default=str(ROOT),
                        help="repository to scan (default: this checkout)")
    parser.add_argument("--format", choices=("human", "filter", "pytest"), default="human",
                        help="output shape (default: human table)")
    args = parser.parse_args(argv)

    if not args.ids and not args.from_diff:
        parser.print_usage(sys.stderr)
        sys.stderr.write("affected-tests: give at least one ID or --from-diff REV\n")
        return 2

    root = Path(args.root)
    registry = Path(args.registry) if args.registry else root / REGISTRY
    ids = list(args.ids)
    if args.from_diff:
        ids += [i for i in changed_registry_ids(root, args.from_diff, registry)
                if i not in ids]

    tests = discover(root)
    hits = affected(tests, ids)
    by_runner, corpora = {}, {}
    for test in tests:
        corpora.setdefault(test.runner, []).append(test.name)
    for hit in hits:
        by_runner.setdefault(hit.runner, []).append(hit)
    terms, assigned, unfilterable = selection(by_runner, corpora)
    for name in unfilterable:
        sys.stderr.write("affected-tests: unfilterable test name - no word character "
                         "to make a term from and a comma in it, so --filter cannot "
                         "select it: %s\n" % name)
    nodes = " ".join(sorted({t.name for t in by_runner.get("pytest", [])}))

    if args.format == "filter":
        sys.stdout.write(terms + "\n" if terms else "")
    elif args.format == "pytest":
        sys.stdout.write(nodes + "\n" if nodes else "")
    else:
        print(human_table(ids, hits, terms, nodes, assigned))
    return 0


if __name__ == "__main__":
    # A table truncated by `| head` must not leave a traceback behind: die of
    # SIGPIPE the way the shell expects instead of raising inside print().
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())
