#!/usr/bin/env python3
"""Fleet rule D-825 GO gate, shared by the live-omnigraph-store tools in this dir.

A run that MUTATES the live omnigraph store (dedup-graph.py, populate-embeddings.py,
split-project-graph.py --apply, and — over the network — apply-cluster.sh) proceeds only
on an explicit judge GO. It refuses (exit 2) unless `--go <ref>` names the approving
artefact (a judge run id `YYYYMMDD-HHMMSS-…`, a decision id `D-<n>`, or an `OS-<n>` item)
AND `--go-sha <sha>` equals `git rev-parse HEAD` of the checkout the tool runs from, so
the GO covers the exact code being applied. The gate prints `GO: <ref> sha=<sha>` as its
first line and runs before any docker/token access, so a refused run touches nothing.
Read-only runs (a dry run / --no-load / no --apply) need no GO and are unchanged; a `--go`
there is echoed only. This mirrors apply.sh, apply.ps1, apply-capability-overrides.ps1 and
apply-cluster.sh — see configuration/omniroute/apply.sh for the rule.
"""
import re
import subprocess
import sys

# judge run id | decision id | OS-<n> item
REF_RE = re.compile(r"^(?:\d{8}-\d{6}-\S+|D-\d+|OS-\d+)$")

GO_REF_HELP = (
    "a judge GO naming this change: a judge run id YYYYMMDD-HHMMSS-…, "
    "a decision id D-<n>, or an OS-<n> item (fleet rule D-825). Required on a live run."
)
GO_SHA_HELP = (
    "'git rev-parse HEAD' of this checkout. Must equal the checkout's HEAD so the GO "
    "names the exact code being applied (fleet rule D-825). Required with --go on a live run."
)


def add_go_args(parser):
    """Register the --go / --go-sha options every gated tool shares."""
    parser.add_argument("--go", default="", help=GO_REF_HELP)
    parser.add_argument("--go-sha", dest="go_sha", default="", help=GO_SHA_HELP)


def checkout_head(root):
    """The checkout's HEAD sha, or '' if git cannot read it."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def enforce(tool, action, mutating, go_ref, go_sha, root):
    """Apply fleet rule D-825 for one tool invocation.

    tool      the script name, printed in every message (e.g. 'dedup-graph.py')
    action    the live thing a mutating run does ('dedup the live omnigraph store')
    mutating  True when this run changes live store state
    go_ref    the value of --go
    go_sha    the value of --go-sha
    root      the repo checkout the code is applied from
    Returns the checkout HEAD on a cleared gate; SystemExit(2) on any refusal.
    """
    if not mutating:
        if go_ref:
            print("GO: %s sha=%s (read-only run — no gate applies)" % (go_ref, go_sha or "none"))
        return ""

    if not go_ref:
        _die(tool, [
            "%s: refusing to %s without --go <ref> (fleet rule D-825)." % (tool, action),
            "  Pass --go <ref> --go-sha <sha>, where <ref> is a judge run id"
            " (YYYYMMDD-HHMMSS-…), a decision id D-<n> or an OS-<n> item, and <sha> is"
            " 'git rev-parse HEAD' of this checkout. To inspect without changing anything,"
            " use this tool's read-only mode (--dry-run / --no-load, or omit --apply).",
        ])
    if not REF_RE.match(go_ref):
        _die(tool, [
            "%s: --go '%s' is not a judge run id (YYYYMMDD-HHMMSS-…), a decision id D-<n>"
            " or an OS-<n> item." % (tool, go_ref),
        ])
    if not go_sha:
        _die(tool, [
            "%s: refusing to %s without --go-sha <sha> (fleet rule D-825)." % (tool, action),
            "  <sha> must equal 'git rev-parse HEAD' of this checkout, so the GO names the"
            " exact code applied.",
        ])
    head = checkout_head(root)
    if not head:
        _die(tool, [
            "%s: cannot read this checkout's HEAD ('git -C %s rev-parse HEAD' failed)." % (tool, root),
            "  Refusing to mutate the live store against an unverifiable sha.",
        ])
    if go_sha != head:
        _die(tool, [
            "%s: --go-sha '%s' is not this checkout's HEAD '%s'." % (tool, go_sha, head),
            "  The GO must name the exact sha of the code being applied (fleet rule D-825).",
        ])
    print("GO: %s sha=%s" % (go_ref, head))
    return head


def _die(tool, lines):
    for line in lines:
        print("%s" % line, file=sys.stderr)
    raise SystemExit(2)
