"""The risk class of a change, decided by code from the diff (RISKTIER-a).

Operator Q-013 / D-060, 2026-09-28: *the writer does not grade its own work.* A
card's ``risk`` field was whatever the spawning session felt like typing; this
module reads the diff instead. High risk is security, secrets, an installer or
root/sudo, a policy or skill rule, a public API/contract, a data migration, or a
plan/spec — every one of those shapes is declared as data in
`policy.risk_rules` (catalog/ai-registry.json, spec
docs/plans/2026-09-25-routing-v2-spec.md §5.7) and applied here. What the class
then costs (cross-family reviews, the Sonnet final, the sampled audit) is
`policy.review_counts` / `policy.risk_audit_percent`.

Nothing in here is a decision the registry could make itself: `classify` walks
`policy.risk_rules` in order and raises on a rule type it does not implement, so
a rule that silently does nothing is impossible. `tools/registry.py validate` is
the gate that keeps the registry's rules inside the implemented set.

The rule types:

  path_glob        a changed path matching `pattern`. A rename or copy is tested
                   against BOTH names — the one the change moved away from and the
                   one it moved to — because `git mv AGENTS.md docs/AGENTS.md`
                   otherwise walks a policy file out of the class that guards it.
                   An optional `exclude` glob cancels a match, which is what keeps
                   a tracked `.env.example` template out of a `.env` rule.
  diff_deletion    any deleted file in the diff, and — when the rule declares
                   `min_deleted_lines` — a diff that removes more than that many
                   lines. A change that deletes 240 lines from a file that
                   survives is a large deletion; only a whole file disappearing
                   used to read as one.
  added_regex      an ADDED line matching `pattern`, optionally restricted to
                   files matching `paths`. Case-sensitive and textual: a test
                   that only mentions `sudo` inside a string raises the class.
                   That is the deal — the cheap reviews read it, the final decides.
  registry_policy  the diff changes the registry's `policy` object (compared as
                   JSON at the merge base and at the sha; a models-only edit is
                   not a policy edit)

Every git read goes through one injectable runner, `runner(args, cwd) ->
(returncode, stdout)`, where `args` is git's argv without the program name. A
git failure raises `RiskError` — never a silent `normal`, because an
unclassified diff that reads as low risk is how a secrets change gets one cheap
review. Callers map `RiskError` to exit 2.

`assess` resolves the rev it was handed (`HEAD`, a branch, a short sha) to one
full commit hex with `git rev-parse --verify <rev>^{commit}` and uses that
everywhere — including the audit draw, so one commit has one answer however the
caller spelled it.

`audit` is the sampling side of the same decision: `normal` work gets two cheap
cross-family reviews deterministically, and 20% of them (by the sha, so the same
commit is always answered the same way and a re-run cannot shop for a bucket)
additionally get the final check.

Pure functions; stdlib only; no network, no clock.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess

# The rule types implemented by `classify`. `tools/registry.py validate` reports
# anything outside this set, so the registry can never hold a rule that is data
# without a reader.
RULE_TYPES = ("path_glob", "diff_deletion", "added_regex", "registry_policy")

# policy.risk_audit_percent when the registry does not declare one.
DEFAULT_AUDIT_PERCENT = 20

# The registry file whose `policy` object is the routing policy.
REGISTRY_REL_PATH = "catalog/ai-registry.json"

# How many leading hex digits of a sha decide the audit bucket.
_AUDIT_DIGITS = 12
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
# One resolved commit, as `git rev-parse` writes it.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# A `--name-status -z` status field: the letter, plus the similarity score git
# appends to R and C. Anything else at a status position is a desync of the
# field walk, not a path.
_STATUS_RE = re.compile(r"^[A-Za-z]\d*$")
# The numeric head of a `--numstat -z` record: `added<TAB>deleted<TAB>`, with
# `-` where a binary file has no counts.
_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t")

# The escapes git writes inside a C-quoted path (`"tab\\tname.md"`), and the two
# punctuation ones that quote a quote and a backslash.
_C_ESCAPES = {"a": 0x07, "b": 0x08, "t": 0x09, "n": 0x0A, "v": 0x0B, "f": 0x0C,
              "r": 0x0D, '"': 0x22, "\\": 0x5C}


class RiskError(Exception):
    """The diff could not be read, or a rule could not be applied — never
    reported as `normal`."""


def _git(args, cwd):
    """The default runner: git with `args` in `cwd`, output captured.

    The `-c` overrides are the point, not decoration: this function exists to
    hand the parsers a byte stream whose shape does not depend on the machine it
    ran on. A user's `diff.noprefix=true` removes the `b/` the `+++` header is
    read through, and every added line then vanishes — a `sudo` change that
    classifies as `normal`, the one failure this module may not have.
    `color.diff=always` writes escape sequences into the same bytes, and
    `diff.external` replaces the diff with some script's stdout, so `diff` runs
    with `--no-ext-diff` and no colour. `core.quotepath=false` keeps a
    non-ASCII path as itself instead of `"\303\251.md"`, which no glob here
    would match.
    """
    argv = ["git",
            "-c", "core.quotepath=false",
            "-c", "diff.noprefix=false",
            "-c", "color.diff=never"]
    args = list(args)
    if args and args[0] == "diff":
        args.insert(1, "--no-ext-diff")
    proc = subprocess.run(argv + args, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout


# ---------------------------------------------------------------------------
# the glob matcher
# ---------------------------------------------------------------------------

def glob_match(pattern: str, path: str) -> bool:
    """Whether `path` matches the registry's `pattern`.

    fnmatch-style, one segment at a time: `*` matches within a segment and never
    crosses a `/`, and `**` matches any number of segments INCLUDING zero, so
    `a/**/b` matches both `a/b` and `a/x/y/b`, and `**/*.key` matches a key at
    the repo root. `fnmatch` itself cannot express that (its `*` eats `/`), which
    is why this file owns a matcher instead of calling fnmatch directly — and
    why `tests/test_autoos_risk.py` carries the whole table.
    """
    pats = [s for s in pattern.split("/") if s]
    segs = [s for s in path.split("/") if s]
    # reachable[i][j]: pats[i:] matches segs[j:]. Rows are (len(pats)+1) x
    # (len(segs)+1) so the trailing-** case can look one column past the end.
    reachable = [[False] * (len(segs) + 1) for _ in range(len(pats) + 1)]
    reachable[len(pats)][len(segs)] = True
    for i in range(len(pats) - 1, -1, -1):
        for j in range(len(segs), -1, -1):
            if pats[i] == "**":
                # zero segments (the rest of the pattern at the same depth) or
                # one more path segment consumed.
                reachable[i][j] = (reachable[i + 1][j]
                                   or (j < len(segs) and reachable[i][j + 1]))
            elif j < len(segs) and fnmatch.fnmatchcase(segs[j], pats[i]):
                reachable[i][j] = reachable[i + 1][j + 1]
    return reachable[0][0]


# ---------------------------------------------------------------------------
# reading the diff
# ---------------------------------------------------------------------------

def resolve_sha(repo: str, rev: str, runner) -> str:
    """The full commit hex that `rev` names — `HEAD`, a branch, a tag, a short sha.

    Two reasons, both about the audit draw. `audit()` buckets the first 12 hex
    digits, so `28ada0a` and the 40 digits of the same commit can land in
    different buckets: one commit, two answers, and a caller who learns the
    friendlier spelling. And `HEAD` — what a lane actually has when it asks about
    the commit it just made — is not hex at all, so a perfectly classifiable diff
    used to exit 2 on the shape of the argument.

    `^{commit}` is what makes this a commit and not any object: a tree or a blob
    has no diff to classify, and answering with a resolved sha nobody can diff is
    the silent failure this module is built not to have.
    """
    code, out = runner(["rev-parse", "--verify", "%s^{commit}" % rev], repo)
    text = out.strip()
    if code or not text:
        raise RiskError("git rev-parse --verify %s^{commit} failed: %s"
                        % (rev, text.splitlines()[0] if text else "no such rev"))
    first = text.splitlines()[0].strip()
    if not _FULL_SHA_RE.match(first):
        raise RiskError("git rev-parse answered %r for %s, not a commit sha"
                        % (first, rev))
    return first


def merge_base(repo: str, base: str, sha: str, runner) -> str:
    """The merge base of `base` and `sha` — the diff's other end.

    Comparing sha against the merge base rather than `base` directly is what
    keeps a lane's own commits out of the classification: `base` moves when the
    lane merges, and the risk of a change is not "everything since main's tip at
    the moment I asked".
    """
    code, out = runner(["merge-base", base, sha], repo)
    text = out.strip()
    if code or not text:
        raise RiskError("git merge-base %s %s failed: %s"
                        % (base, sha, text or "no common ancestor"))
    return text.splitlines()[0]


def changed_files(repo: str, base: str, sha: str, runner) -> list:
    """`[{"path", "old_path", "deleted"}]` for everything the diff touches.

    `-z`, with the fields split on NUL. The default `--name-status` C-quotes a
    path containing a tab, a newline or a quote — and the tab it escapes with is
    the very character the line is split on, so `M\\t"docs/tab\\tname.md"` arrives
    as three fields and the path keeps its quotes. A name like that matches no
    glob in the registry, which is a `normal` bought with a filename.

    A rename or copy keeps BOTH names: `old_path` is the name the change moved
    away from, `path` the one it moved to. `git mv AGENTS.md docs/AGENTS.md`
    reports only the new one, and a rule on `AGENTS.md` that reads only the new
    one watches a policy file walk out of the class that guards it.
    A rename is not a deletion — moving a file deletes no content — so
    `deleted` stays the flag `diff_deletion` reads.
    """
    mb = merge_base(repo, base, sha, runner)
    code, out = runner(["diff", "--name-status", "-z", mb, sha], repo)
    if code:
        raise RiskError("git diff --name-status -z %s %s failed: %s"
                        % (mb, sha, out.strip()))
    fields = [f for f in out.split("\0") if f != ""]
    files = []
    index = 0
    while index < len(fields):
        status = fields[index]
        if not _STATUS_RE.match(status):
            # The walk is by the arity each status declares, so this is only
            # reachable when git wrote something this reader does not know.
            # Guessing one field forward would drop a file off the diff.
            raise RiskError("git diff --name-status -z: unexpected status %r "
                            "at field %d of the %s..%s diff"
                            % (status, index, mb, sha))
        letter = status[0]
        arity = 3 if letter in ("R", "C") else 2
        if len(fields) < index + arity:
            raise RiskError("git diff --name-status -z: status %s is missing a "
                            "path in the %s..%s diff" % (status, mb, sha))
        if letter == "R" or letter == "C":
            files.append({"path": fields[index + 2], "old_path": fields[index + 1],
                          "deleted": False})
        else:
            files.append({"path": fields[index + 1], "old_path": None,
                          "deleted": letter == "D"})
        index += arity
    return files


def deleted_lines(repo: str, base: str, sha: str, runner) -> int:
    """How many lines the diff removes, summed over every file it touches.

    `diff_deletion` used to see only a whole file disappearing; how much a change
    takes *out* of the files that survive was invisible, and a 240-line deletion
    from a file that stays is a large deletion by any reading of the rule.

    `-z` for the same reason as `--name-status`: the counts and the name are
    separated by tabs, so only an unquoted path keeps a filename from being read
    as numbers. Each record's own numeric head is what is summed, which also means
    the reader never walks a rename's pair of paths and never double-counts. A
    binary file writes `-` — not a line count, so not a contribution.
    """
    mb = merge_base(repo, base, sha, runner)
    code, out = runner(["diff", "--numstat", "-z", mb, sha], repo)
    if code:
        raise RiskError("git diff --numstat -z %s %s failed: %s"
                        % (mb, sha, out.strip()))
    total = 0
    for token in out.split("\0"):
        head = _NUMSTAT_RE.match(token)
        if head and head.group(2) != "-":
            total += int(head.group(2))
    return total


def _unquote_path(text: str) -> str:
    """A C-quoted git path (`"tab\\tname.md"`) back to the path itself.

    `core.quotepath=false` only stops git quoting *non-ASCII*; a name with a tab,
    a newline or a quote is still written escaped and inside double quotes, in the
    `+++` header as in `--name-status`. Read as a raw string the header matches
    neither `b/` nor `/dev/null`, `current` stays None, and every added line of
    that file — `sudo` included — is dropped before a rule ever sees it.

    The escapes are the C ones plus octal for a byte git has no letter for, so the
    body is reassembled as bytes and decoded: a `\303\251` pair is one é, not two
    characters.
    """
    if not (len(text) >= 2 and text.startswith('"') and text.endswith('"')):
        return text
    body = text[1:-1]
    raw = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            nxt = body[index + 1]
            if nxt in _C_ESCAPES:
                raw.append(_C_ESCAPES[nxt])
                index += 2
                continue
            if nxt.isdigit() and nxt < "8":
                digits = ""
                while len(digits) < 3 and index + 1 + len(digits) < len(body) \
                        and body[index + 1 + len(digits)].isdigit() \
                        and body[index + 1 + len(digits)] < "8":
                    digits += body[index + 1 + len(digits)]
                raw.append(int(digits, 8))
                index += 2 + len(digits)
                continue
        raw += char.encode("utf-8")
        index += 1
    return bytes(raw).decode("utf-8", "replace")


def added_lines(repo: str, base: str, sha: str, runner) -> dict:
    """`{path: [added line text]}` from `git diff -U0`.

    Only the plus side: `added_regex` asks what the change *introduced*. A
    removed `sudo` is not a new risk, and a context line was already reviewed
    when it landed. `-U0` keeps the payload to the changed lines.

    A `+` line is a header only before the file's first `@@` hunk. Git writes the
    one-character marker and the line's own text with nothing between them, so an
    added line that begins with a plus (`++++ x` in a test that embeds a diff)
    looks exactly like a header — and a header seen as content, or content seen
    as a header, is a `sudo` line nobody ever classified.

    The `+++` target goes through `_unquote_path`: a file whose name git quotes
    would otherwise leave `current` as None for its whole body, dropping every
    added line it contains.
    """
    mb = merge_base(repo, base, sha, runner)
    code, out = runner(["diff", "-U0", mb, sha], repo)
    if code:
        raise RiskError("git diff -U0 %s %s failed: %s" % (mb, sha, out.strip()))
    added = {}
    current = None
    in_hunk = False
    for raw in out.splitlines():
        if raw.startswith("diff --git "):
            current, in_hunk = None, False
        elif not in_hunk and raw.startswith("@@"):
            in_hunk = True
        elif not in_hunk and raw.startswith("+++ "):
            target = _unquote_path(raw[4:].strip())
            if target.startswith("b/"):
                current = target[2:]
                added.setdefault(current, [])
            else:
                current = None        # /dev/null: the file was deleted
        elif in_hunk and raw.startswith("+"):
            if current is not None:
                added[current].append(raw[1:].rstrip("\r"))
    return added


def _show(repo: str, rev: str, path: str, runner):
    """The file as of `rev`, or None when it is not there.

    A non-zero exit means "no such path at this rev" here and only here: the
    revision itself was already proven by `merge_base`, and `changed_files` has
    run against the same pair, so a repo the caller cannot read fails before
    this function is reached.
    """
    code, out = runner(["show", "%s:%s" % (rev, path)], repo)
    return out if code == 0 else None


def registry_policy_changed(repo: str, base: str, sha: str, runner) -> bool:
    """Whether the diff changes the registry's `policy` object.

    `git show` at both ends, parsed, and only `["policy"]` compared — the
    resolver's knobs are the thing being guarded, and a model-price or
    catalog-only edit is not a routing-policy edit. Unparseable JSON is a
    `RiskError`, not a shrug: a registry nobody can read is worse than a policy
    change.
    """
    mb = merge_base(repo, base, sha, runner)
    return (_policy(_show(repo, mb, REGISTRY_REL_PATH, runner), mb)
            != _policy(_show(repo, sha, REGISTRY_REL_PATH, runner), sha))


def _policy(text, rev):
    """The `policy` half of a registry blob (None when the file is absent)."""
    if text is None:
        return None
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise RiskError("registry at %s is not valid JSON (%s)" % (rev, exc))
    if not isinstance(doc, dict):
        raise RiskError("registry at %s is not an object" % rev)
    return doc.get("policy")


# ---------------------------------------------------------------------------
# classify / audit / assess
# ---------------------------------------------------------------------------

def _path_hit(pattern, exclude, entry):
    """The path a `path_glob` rule matched, or None.

    A rename or copy is tested against both of its names, and the reported one is
    whichever matched — the old name with an arrow to where it went, so a reviewer
    reading `policy: AGENTS.md -> docs/AGENTS.md` can see the move rather than
    hunt for a file that is no longer there.
    """
    new = entry.get("path")
    old = entry.get("old_path")
    if (new is not None and glob_match(pattern, new)
            and not (exclude and glob_match(exclude, new))):
        return new
    if (old and glob_match(pattern, old)
            and not (exclude and glob_match(exclude, old))):
        return "%s -> %s" % (old, new)
    return None


def classify(files: list, added_lines: dict, registry: dict,
             registry_policy_changed: bool = False,
             deleted_lines: int = 0) -> dict:
    """`{"risk": "normal"|"high", "reasons": ["<reason>: <path or detail>", ...]}`.

    Every rule in `policy.risk_rules` is applied, in registry order; a reason
    line is one per matching path (per rule), so a reviewer sees which file
    raised the class and why. An unknown rule type raises — a rule no code
    reads is a rule that does not exist.

    `deleted_lines` is the diff's own count from `deleted_lines()` above; a
    `diff_deletion` rule that declares `min_deleted_lines` reads it, and a rule
    that does not is unaffected by a caller who never measured it.
    """
    rules = ((registry or {}).get("policy") or {}).get("risk_rules") or []
    reasons = []
    seen = set()

    def add(reason, detail):
        line = "%s: %s" % (reason, detail)
        if line not in seen:
            seen.add(line)
            reasons.append(line)

    for rule in rules:
        if not isinstance(rule, dict):
            raise RiskError("risk rule is not an object: %r" % (rule,))
        type_, reason = rule.get("type"), rule.get("reason") or rule.get("type")
        if type_ == "path_glob":
            pattern = rule.get("pattern")
            if not pattern:
                continue
            exclude = rule.get("exclude")
            for entry in files:
                hit = _path_hit(pattern, exclude, entry)
                if hit:
                    add(reason, hit)
        elif type_ == "diff_deletion":
            for entry in files:
                if entry["deleted"]:
                    add(reason, entry["path"])
            threshold = rule.get("min_deleted_lines")
            if threshold is not None:
                if isinstance(threshold, bool) or not isinstance(threshold, int):
                    raise RiskError("min_deleted_lines must be an int, got %r"
                                    % (threshold,))
                if deleted_lines > threshold:
                    add(reason, "%d lines deleted" % deleted_lines)
        elif type_ == "added_regex":
            pattern = rule.get("pattern")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise RiskError("added_regex pattern %r does not compile (%s)"
                                % (pattern, exc))
            paths_glob = rule.get("paths")
            for entry in files:
                path = entry["path"]
                if paths_glob and not glob_match(paths_glob, path):
                    continue
                if any(compiled.search(line)
                       for line in added_lines.get(path, [])):
                    add(reason, path)
        elif type_ == "registry_policy":
            if registry_policy_changed:
                add(reason, REGISTRY_REL_PATH)
        else:
            raise RiskError("unknown risk rule type %r (this build applies: %s)"
                            % (type_, ", ".join(RULE_TYPES)))
    return {"risk": "high" if reasons else "normal", "reasons": reasons}


def audit(sha: str, percent: int) -> bool:
    """Whether this sha is drawn into the audit sample.

    `int(sha[:12], 16) % 100 < percent`, which is deterministic in the commit
    itself: the same sha is always in or always out, no process state and no
    clock can re-roll it, and re-running the classifier after an unlucky draw is
    not possible (the answer is the commit). 12 hex digits is well past the point
    where the low two decimal digits are uniform.

    Pass the resolved commit — `assess()` does, through `resolve_sha()` — because
    a rev spelled shorter buckets shorter, and two answers for one commit is
    exactly the shopping this function exists to remove.
    """
    if isinstance(percent, bool) or not isinstance(percent, int):
        raise RiskError("audit percent must be an int, got %r" % (percent,))
    if not 0 <= percent <= 100:
        raise RiskError("audit percent must be 0-100, got %d" % percent)
    digits = (sha or "")[:_AUDIT_DIGITS]
    if not digits or not _HEX_RE.match(digits):
        raise RiskError("not a git sha: %r" % (sha,))
    return int(digits, 16) % 100 < percent


def audit_percent(registry: dict) -> int:
    """`policy.risk_audit_percent`, or `DEFAULT_AUDIT_PERCENT` when undeclared.

    The registry spells it `{"value": n, "source": ...}` (every numeric policy
    value carries its provenance, spec D20); a bare int is read too, because
    that is the shape a test fixture writes.
    """
    value = ((registry or {}).get("policy") or {}).get("risk_audit_percent")
    if value is None:
        return DEFAULT_AUDIT_PERCENT
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, int):
        raise RiskError("policy.risk_audit_percent must be an int, got %r"
                        % (value,))
    return value


def assess(repo: str, base: str, sha: str, registry: dict, runner=None) -> dict:
    """Classify `repo`'s diff (merge base of `base` and `sha`, up to `sha`).

    `{"risk", "reasons", "sha", "audit", "audit_percent", "files"}`, where `sha`
    is the resolved commit — what the answer is actually about, so a caller that
    asked about `HEAD` can record which commit it turned out to be. Every git read
    after the resolution uses that hex, so the diff, the reasons and the audit draw
    are all decisions about one commit.

    `runner` is the git seam (`runner(args, cwd) -> (code, stdout)`); the default
    shells out to git. Any git failure raises `RiskError` — the caller exits 2, it
    does not get to treat an unreadable diff as low risk.
    """
    runner = runner or _git
    commit = resolve_sha(repo, sha, runner)
    changed = changed_files(repo, base, commit, runner)
    added = added_lines(repo, base, commit, runner)
    out = classify(changed, added, registry,
                   registry_policy_changed=registry_policy_changed(
                       repo, base, commit, runner),
                   deleted_lines=deleted_lines(repo, base, commit, runner))
    percent = audit_percent(registry)
    out["audit"] = audit(commit, percent)
    out["audit_percent"] = percent
    out["sha"] = commit
    out["files"] = changed
    return out
