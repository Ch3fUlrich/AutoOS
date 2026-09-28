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
of the ids, word-boundary matched, across all three suites.

What it reads
    tests/linux/*.sh      `if it "name"; then` blocks
    tests/run-tests.ps1   `Test-Case 'name' {` blocks
    tests/test_*.py       `test_*` functions, module level or in a class pytest
                          collects (a `Test*` name or a TestCase subclass),
                          located with `ast`
A shell or Pester block runs from its header to the next header or the next
`describe` / `Describe-Group`, whichever comes first, so the text is credited to
one case only and the header's own words count as a mention.

Outputs
    --format filter       one comma-separated string for run-tests.sh --filter /
                          run-tests.ps1 -Filter and nothing else, so it is safe in
                          $( ). Both runners split their filter on commas and match
                          each term as a substring of a *test name*, so a term is
                          always a comma-free, space-free substring of a name that
                          is affected. Over-inclusion is intended: a term that also
                          names a few extra cases cannot miss one.
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

# A filter term: whitespace-free and comma-free by construction, and a literal
# substring of the test name it came from. ':' and '+' are out of the charset -
# they end a token in the suite's names without adding anything a filter needs.
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@-]*")


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


def regions(text: str, header: re.Pattern, group: re.Pattern):
    """Yield (name, line, body) for every test block in one runner's source text.

    A block starts where its own comment block starts - this suite writes the
    explaining comment above the `if it`, so that text belongs to the case below
    it, not to the case before - and ends at the next block's start, the next
    `describe` / `Describe-Group`, or the end of the file, whichever comes first.
    """
    found = list(header.finditer(text))
    if not found:
        return
    lines = text.split("\n")
    starts = _line_starts(text)
    line_of = lambda offset: bisect.bisect_right(starts, offset) - 1
    heads = [line_of(m.start()) for m in found]
    groups = sorted(line_of(m.start()) for m in group.finditer(text))
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
            for name, line, body in regions(text, header, group)]


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
    """Word-boundary match for an id, tolerant of one that starts or ends on punctuation."""
    word = lambda ch: ch.isalnum() or ch == "_"
    left = r"\b" if word(an_id[:1]) else ""
    right = r"\b" if word(an_id[-1:]) else ""
    return re.compile(left + re.escape(an_id) + right, re.IGNORECASE)


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
    """(terms, {test: term}) - a filter term per affected case, and the tight list.

    A term must be a substring of an affected *name* - that is all either runner
    can match - so it comes out of the name itself: a token carrying one of the
    ids when the name has one, else the token that pulls in the fewest other
    cases, tie-broken toward the one naming the most affected cases and then the
    longest. A term contained inside another is dropped: the shorter one already
    selects everything the longer one does. Collateral is therefore small and the
    list is exact enough to run, but its words come from test names, not from the
    registry - the table format shows which term selects which case.
    """
    def collateral(term):
        return sum(1 for name in corpus if name_matches(runner, term, name))

    def covers(term):
        return sum(1 for test in hits if name_matches(runner, term, test.name))

    picks = []
    for test in hits:
        pool = TOKEN.findall(test.name)
        carrying = [t for t in pool if any(an_id.lower() in t.lower() for an_id in test.ids)]
        candidates = carrying or [t for t in pool if len(t) >= MIN_TERM_LEN] or pool
        if candidates:
            picks.append(min(candidates, key=lambda t: (collateral(t), -covers(t), -len(t), t)))
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
    return sorted(set(terms), key=lambda t: (t.lower(), len(t), t)), assignment


def selection(hits_by_runner, corpora):
    """The filter string, and which term selects which shell / Pester case."""
    terms, assigned = [], {}
    for runner in ("sh", "ps1"):
        hits = hits_by_runner.get(runner, [])
        if hits:
            chosen, assignment = choose_terms(hits, corpora.get(runner, []), runner)
            terms += chosen
            assigned.update(assignment)
    joined = ",".join(sorted(set(terms), key=lambda t: (t.lower(), len(t), t)))
    return joined, assigned


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
    terms, assigned = selection(by_runner, corpora)
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
