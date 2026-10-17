#!/usr/bin/env python3
"""Fleet rule D-825 GO gate, shared by the live-omnigraph-store tools in this dir.

A run that MUTATES the live omnigraph store (dedup-graph.py, populate-embeddings.py,
split-project-graph.py --apply, and — over the network — apply-cluster.sh) proceeds only
on an explicit judge GO. It refuses (exit 2) unless `--go <ref>` names the approving
artefact AND `--go-sha <sha>` equals `git rev-parse HEAD` of the checkout the tool runs
from, so the GO covers the exact code being applied. Naming the *shape* of a reference
was not enough: 2026-10-09 review found that `--go D-1` passed the old gate because the
only check was a regex, and no `D-1` decision exists anywhere. So `enforce()` now also
proves the ref EXISTS, in the source its kind reads from:

  D-<n>       a line or heading holding that exact id in the routing decisions log
              (AUTOOS_DECISIONS_LOG, else AUTOOS_ROUTING_DIR/docs/decisions-log.md;
              AUTOOS_ROUTING_DIR/DECISIONS.md counts as a second source)
  OS-<n>      the same id in the routing QUESTIONS.md or ANSWERS.md
  run id      a worker record for the judge run: AUTOOS_WORKERS_DIR/<id>.json, or the
              sibling agents/<id>/ dir — else, under the checkout the tool runs from,
              logs/workers/<id>.json or logs/agents/<id>/

The match is on the exact id, never a substring: a log line with `D-825` does not
satisfy `--go D-82`. A source that is missing or unreadable refuses too — an
unverifiable GO is not a GO — unless `--go-offline` (PowerShell `-GoOffline`) is
passed, which waives the lookup only and logs `GO-OFFLINE: <ref> unverified` as the
first line of the run; the sha bar stays.

The gate prints `GO: <ref> sha=<sha>` as its first line and runs before any
docker/token access, so a refused run touches nothing. Read-only runs (a dry run /
--no-load / no --apply) need no GO and are unchanged; a `--go` there is echoed only.
This mirrors apply.sh, apply.ps1, apply-capability-overrides.ps1 and apply-cluster.sh —
see configuration/omniroute/apply.sh for the rule. Those bash/PowerShell gates reach
THE SAME implementation for both ref bars — shape and existence — through this file's
`verify-ref` command rather than keeping a second copy of either; what stays theirs is
the --go/--go-sha presence check and the sha-versus-HEAD comparison, because the refusal
wording is theirs, and the sha is verified first so a GO naming the wrong code never
reaches a lookup.

Run as a command (what the shell gates do):

    python3 _go_gate.py verify-ref --tool apply.sh --ref D-825 [--root <checkout>]
                                   [--flag-style ps] [--offline]

exits 0 when the ref names something that exists (and prints nothing), 2 with the
reason on stderr when it does not, and prints `GO-OFFLINE: <ref> unverified` on stdout
under `--offline`.
"""
import argparse
import os
import re
import subprocess
import sys

# judge run id | decision id | OS-<n> item
REF_RE = re.compile(r"^(?:\d{8}-\d{6}-\S+|D-\d+|OS-\d+)$")

# where each kind of reference is looked up (env override, then the host default)
ROUTING_DIR_ENV = "AUTOOS_ROUTING_DIR"
DECISIONS_LOG_ENV = "AUTOOS_DECISIONS_LOG"
WORKERS_DIR_ENV = "AUTOOS_WORKERS_DIR"

GO_REF_HELP = (
    "a judge GO naming this change: a judge run id YYYYMMDD-HHMMSS-…, "
    "a decision id D-<n>, or an OS-<n> item (fleet rule D-825). Required on a live run."
)
GO_SHA_HELP = (
    "'git rev-parse HEAD' of this checkout. Must equal the checkout's HEAD so the GO "
    "names the exact code being applied (fleet rule D-825). Required with --go on a live run."
)
GO_OFFLINE_HELP = (
    "proceed although the GO's approving artefact cannot be read from this machine "
    "(a missing or unreadable routing dir, no worker record on disk). The run logs "
    "'GO-OFFLINE: <ref> unverified' as its first line; --go-sha is still verified "
    "(fleet rule D-825)."
)


def add_go_args(parser):
    """Register the --go / --go-sha / --go-offline options every gated tool shares."""
    parser.add_argument("--go", default="", help=GO_REF_HELP)
    parser.add_argument("--go-sha", dest="go_sha", default="", help=GO_SHA_HELP)
    parser.add_argument("--go-offline", dest="go_offline", action="store_true",
                        help=GO_OFFLINE_HELP)


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


# ─── the artefact sources a GO reference is looked up in ──────────────────────
def _env(env, key):
    """One env value, '' when unset — an empty override means 'not set', not 'nothing'."""
    env = os.environ if env is None else env
    return (env.get(key) or "").strip()


def _home(env):
    env = os.environ if env is None else env
    return env.get("HOME") or os.path.expanduser("~")


def routing_dir(env=None):
    """The routing repo's docs tree: AUTOOS_ROUTING_DIR, else $HOME/code/routing."""
    return _env(env, ROUTING_DIR_ENV) or os.path.join(_home(env), "code", "routing")


def decisions_logs(env=None):
    """Where a D-<n> id is looked for.

    An explicit AUTOOS_DECISIONS_LOG is the WHOLE source: naming one file must pin the
    lookup, not silently also read the host's own routing dir (that is how a test
    passes on one machine and fails on another). Unset, both the decisions log and the
    consolidated DECISIONS.md count.
    """
    explicit = _env(env, DECISIONS_LOG_ENV)
    if explicit:
        return [explicit]
    routing = routing_dir(env)
    return [os.path.join(routing, "docs", "decisions-log.md"),
            os.path.join(routing, "DECISIONS.md")]


def question_files(env=None):
    """Where an OS-<n> id is looked for: the open questions, and the answered ones."""
    routing = routing_dir(env)
    return [os.path.join(routing, "QUESTIONS.md"), os.path.join(routing, "ANSWERS.md")]


def worker_dirs(root, env=None):
    """Where a judge run id is looked for: worker records, then the agent session dirs.

    AUTOOS_WORKERS_DIR names the worker-record dir; its sibling `agents/` is the agent
    dir shape, so one override moves both together (the checkout's own logs/ tree has
    exactly that shape).
    """
    override = _env(env, WORKERS_DIR_ENV)
    if override:
        trimmed = override.rstrip(os.sep) or override
        return [trimmed, os.path.join(os.path.dirname(trimmed), "agents")]
    base = os.path.join(root or ".", "logs")
    return [os.path.join(base, "workers"), os.path.join(base, "agents")]


def _exact_id(ref):
    """A word-bounded search for the id: `D-82` must not be satisfied by `D-825`.

    `\\b` alone is not enough in either direction — the '-' inside the id is a
    non-word character, so a boundary exists between it and the digits; the guard is
    that the id's own edge touches no word character at all.
    """
    return re.compile(r"(?<!\w)" + re.escape(ref) + r"(?!\w)")


def _found_in_text(ref, paths):
    """(found, readable) for an id searched through text files."""
    pattern = _exact_id(ref)
    readable = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        readable.append(path)
        if pattern.search(text):
            return True, readable
    return False, readable


def _found_in_dirs(ref, dirs):
    """(found, readable) for a run id looked up as <dir>/<id>.json or <dir>/<id>/."""
    readable = []
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        readable.append(directory)
        if os.path.isfile(os.path.join(directory, ref + ".json")):
            return True, readable
        if os.path.isdir(os.path.join(directory, ref)):
            return True, readable
    return False, readable


def verify_ref(ref, root, env=None):
    """Does `ref` name an artefact that EXISTS? 'found' | 'absent' | 'unreadable'.

    'unreadable' is the verdict when every source the kind reads is missing or cannot
    be read — the ref may well be real, but nothing on this machine proves it, which is
    a refusal in its own right (see the module docstring).
    """
    if not REF_RE.match(ref or ""):
        return "absent"
    if ref.startswith("D-"):
        found, readable = _found_in_text(ref, decisions_logs(env))
    elif ref.startswith("OS-"):
        found, readable = _found_in_text(ref, question_files(env))
    else:
        found, readable = _found_in_dirs(ref, worker_dirs(root, env))
    if found:
        return "found"
    return "unreadable" if not readable else "absent"


# ─── the wording every gate shares ────────────────────────────────────────────
_KINDS = {
    "D": ("decision in the routing decisions log", "read the routing decisions log"),
    "OS": ("OS item in the routing QUESTIONS.md or ANSWERS.md",
           "read the routing QUESTIONS.md or ANSWERS.md"),
    "run": ("worker record for the judge run (logs/workers/<id>.json or logs/agents/<id>/)",
            "read this checkout's worker records"),
}


def _kind_of(ref):
    if ref.startswith("D-"):
        return "D"
    if ref.startswith("OS-"):
        return "OS"
    return "run"


def offline_line(ref):
    """The one line an offline GO logs — kept here so no gate rewords it."""
    return "GO-OFFLINE: %s unverified" % ref


def _sources(ref, root, env=None):
    if ref.startswith("D-"):
        return decisions_logs(env)
    if ref.startswith("OS-"):
        return question_files(env)
    return worker_dirs(root, env)


def ref_refusal_lines(tool, ref, verdict, root, env=None, flag="--go",
                      offline_flag="--go-offline"):
    """The refusal a gate prints when the ref does not clear the existence bar."""
    names, reads = _KINDS[_kind_of(ref)]
    sources = _sources(ref, root, env)
    searched = "  searched: %s" % ", ".join(sources)
    hint = ("  A GO must name an approving artefact that exists (fleet rule D-825). If this"
            " machine cannot read it, pass %s: the run proceeds and logs '%s'"
            % (offline_flag, offline_line(ref)))
    if verdict == "unreadable":
        head = ("%s: cannot %s to verify %s '%s' (fleet rule D-825)."
                % (tool, reads, flag, ref))
    else:
        head = ("%s: %s '%s' names no %s (fleet rule D-825)."
                % (tool, flag, ref, names))
    return [head, searched, hint]


def format_refusal_lines(tool, ref, flag="--go"):
    """The refusal for a reference that is not even the shape of a judge GO."""
    return ["%s: %s '%s' is not a judge run id (YYYYMMDD-HHMMSS-...), a decision id"
            " D-<n> or an OS-<n> item." % (tool, flag, ref)]


def verify_go_ref(ref, root, offline=False, tool="", env=None, flag="--go",
                  offline_flag="--go-offline"):
    """(exit_code, stdout_lines, stderr_lines) — the whole ref bar, in one place.

    0 with the GO-OFFLINE line when offline waives the lookup, 0 and nothing when the
    artefact is there, 2 with the reason when it is not or cannot be read.
    """
    if not REF_RE.match(ref or ""):
        return 2, [], format_refusal_lines(tool, ref, flag)
    if offline:
        return 0, [offline_line(ref)], []
    verdict = verify_ref(ref, root, env=env)
    if verdict == "found":
        return 0, [], []
    return 2, [], ref_refusal_lines(tool, ref, verdict, root, env=env, flag=flag,
                                    offline_flag=offline_flag)


def enforce(tool, action, mutating, go_ref, go_sha, root, go_offline=False, env=None):
    """Apply fleet rule D-825 for one tool invocation.

    tool        the script name, printed in every message (e.g. 'dedup-graph.py')
    action      the live thing a mutating run does ('dedup the live omnigraph store')
    mutating    True when this run changes live store state
    go_ref      the value of --go
    go_sha      the value of --go-sha
    root        the repo checkout the code is applied from
    go_offline  True when --go-offline waives the artefact lookup (not the sha bar)
    env         the environment the sources are read from (os.environ when None)
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
    # The ref clears the sha bar, so ask what it refers to. After the sha checks and
    # before the first printed line: a refusal has to print nothing, and an offline GO
    # logs its own line here so it stays the first thing the run says.
    code, out_lines, err_lines = verify_go_ref(go_ref, root, offline=go_offline,
                                               tool=tool, env=env)
    if code:
        _die(tool, err_lines)
    for line in out_lines:
        print(line)
    print("GO: %s sha=%s" % (go_ref, head))
    return head


def _die(tool, lines):
    for line in lines:
        print("%s" % line, file=sys.stderr)
    raise SystemExit(2)


# ─── the command the bash / PowerShell gates call ─────────────────────────────
_DEFAULT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "..", "..", ".."))


def verify_ref_main(argv=None):
    """`_go_gate.py verify-ref --tool T --ref R` — the ref bar as an exit code.

    Prints nothing on a cleared gate; the refusal goes to stderr so a shell gate can
    let it reach the user and exit with this code. An offline GO's log line goes to
    stdout, which the caller prints before its own `GO: <ref> sha=<sha>` line.
    """
    ap = argparse.ArgumentParser(
        prog="_go_gate.py verify-ref",
        description="fleet rule D-825: prove a GO reference names an artefact that exists")
    ap.add_argument("command", choices=["verify-ref"])
    ap.add_argument("--tool", default="go-gate",
                    help="the script name to print in the refusal (apply.sh, apply.ps1, ...)")
    ap.add_argument("--ref", required=True, help="the --go / -Go value")
    ap.add_argument("--root", default=_DEFAULT_ROOT,
                    help="the checkout whose logs/ tree holds a judge run record")
    ap.add_argument("--flag-style", dest="flag_style", choices=["long", "ps"], default="long",
                    help="'long' names --go / --go-offline in the message, 'ps' names -Go / -GoOffline")
    ap.add_argument("--offline", action="store_true",
                    help="the caller's --go-offline / -GoOffline: waive the lookup, log it")
    a = ap.parse_args(argv)
    flag, offline_flag = ("--go", "--go-offline") if a.flag_style == "long" else ("-Go", "-GoOffline")
    code, out_lines, err_lines = verify_go_ref(a.ref, a.root, offline=a.offline,
                                               tool=a.tool, flag=flag,
                                               offline_flag=offline_flag)
    for line in out_lines:
        print(line)
    for line in err_lines:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(verify_ref_main())
