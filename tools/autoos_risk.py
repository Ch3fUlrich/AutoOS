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

The four rule types:

  path_glob        a changed path matching `pattern` (`glob_match` below)
  diff_deletion    any deleted file in the diff
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
    """`[{"path", "deleted"}]` for everything the diff touches.

    Renames are read as the NEW path and are not a deletion — moving a file does
    not delete content. `deleted` is the flag `diff_deletion` reads.
    """
    mb = merge_base(repo, base, sha, runner)
    code, out = runner(["diff", "--name-status", mb, sha], repo)
    if code:
        raise RiskError("git diff --name-status %s %s failed: %s"
                        % (mb, sha, out.strip()))
    files = []
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        status, paths = fields[0], fields[1:]
        letter = status[0]
        if letter in ("R", "C"):        # rename / copy: <old>\t<new>
            path, deleted = paths[-1], False
        else:
            path, deleted = paths[0], letter == "D"
        files.append({"path": path, "deleted": deleted})
    return files


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
            target = raw[4:].strip()
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

def classify(files: list, added_lines: dict, registry: dict,
             registry_policy_changed: bool = False) -> dict:
    """`{"risk": "normal"|"high", "reasons": ["<reason>: <path or detail>", ...]}`.

    Every rule in `policy.risk_rules` is applied, in registry order; a reason
    line is one per matching path (per rule), so a reviewer sees which file
    raised the class and why. An unknown rule type raises — a rule no code
    reads is a rule that does not exist.
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
            for entry in files:
                if glob_match(pattern, entry["path"]):
                    add(reason, entry["path"])
        elif type_ == "diff_deletion":
            for entry in files:
                if entry["deleted"]:
                    add(reason, entry["path"])
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
    not possible (the answer is the commit). 12 hex digits is well past the
    point where the low two decimal digits are uniform, and git's own short
    shas are at least 7, so a short sha is still a real bucket.
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

    `{"risk", "reasons", "audit", "audit_percent", "files"}`. `runner` is the
    git seam (`runner(args, cwd) -> (code, stdout)`); the default shells out to
    git. Any git failure raises `RiskError` — the caller exits 2, it does not
    get to treat an unreadable diff as low risk.
    """
    runner = runner or _git
    changed = changed_files(repo, base, sha, runner)
    added = added_lines(repo, base, sha, runner)
    out = classify(changed, added, registry,
                   registry_policy_changed=registry_policy_changed(
                       repo, base, sha, runner))
    percent = audit_percent(registry)
    out["audit"] = audit(sha, percent)
    out["audit_percent"] = percent
    out["files"] = changed
    return out
