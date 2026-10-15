#!/usr/bin/env python3
"""
vertex-patch-gate.py — judge a compiled-chunk tree by counting ALL its Vertex sites.

The image build stage used to assert fixed numbers over 6 hard-coded chunk names,
which fails open: a gateway bump that adds a 13th mergeConsecutiveSameRoleContents
call site in a new chunk, or renames one of the 6, ships an unpatched gateway and
the build stays green, because the count it checks never looked at that file.

This gate takes the chunk directory and derives everything from the text of every
compiled chunk it finds:

    total    = call sites of the merge function, in any chunk
    patched  = v3 guards present (split mixed turns, then pop/refill the tail)
    refill   = synthetic Continue-turn pushes present
    split    = synthetic "Noted." model turns the mixed-turn split inserts

and requires patched == total AND refill == total AND split == total AND
total >= --min-sites. The patcher's own two run summaries are optional extra
evidence (--run1 / --run2): the sites it reports have to add up to the sites that
really exist, run 1 must be error-free and run 2 must have patched nothing
(idempotence).

Any mismatch exits 1 with every count printed, so a build that stops here names the
chunk a human then has to add to tools/apply-vertex-patch.py's table.

Report lines are ASCII: they go to a build log and, on the Windows companion, to a
console whose code page is not guaranteed to be UTF-8.

AutoOS lane VERTEX-GUARD (gate 06c19a) | 2026-10-09
AutoOS lane VERTEX-LIVE  (v3, D-908)  | 2026-10-09
"""
import argparse
import importlib.util
import os
import re
import sys

MERGE_NAME = "mergeConsecutiveSameRoleContents"
SENTINEL = "__GATEVAR__"
VAR = r"[A-Za-z_$][A-Za-z0-9_$]*"

# A call site is the name dereferenced and applied: `(0,u.fn)(args)`, or a direct
# `fn(args)`. A `function fn(...)` declaration and a `fn:()=>...` re-export are not
# calls, so they do not inflate the count. A compiled look-alike that does count as
# a site and is not a site fails the build loudly, which is the cheap direction.
SITE_RE = re.compile(r"(?<!function )" + re.escape(MERGE_NAME) + r"\)?\s*\(")
# The refill turn is appended once per guarded site.
REFILL_RE = None
# The synthetic model turn the v3 split inserts once per guarded site.
SPLIT_RE = None
# The v1 guard this patch replaces; its bytes must not survive anywhere.
V1_RES = []
GUARD_RE = None
SUMMARY_RE = re.compile(r"Done:\s*(\d+)\s+patched,\s*(\d+)\s+skipped,\s*(\d+)\s+errors")


def load_patcher():
    """The guard bytes come from the patcher, so one text describes them twice."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "apply-vertex-patch.py")
    spec = importlib.util.spec_from_file_location("apply_vertex_patch_gate_source", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the patcher table: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_patterns(patcher):
    """Var-agnostic versions of the patcher's own v3 guard, split, refill, v1 texts."""
    global REFILL_RE, SPLIT_RE, GUARD_RE, V1_RES
    # The v3 guard is split + pop/refill; the two site wrappers (IIFE vs plain
    # statements) both contain that inner sequence verbatim, so one pattern covers
    # both shapes without re-describing the wrapper.
    guard = patcher.split_mixed_tool_turns(SENTINEL) + ";" + patcher.pop_model_turns_then_refill(SENTINEL)
    GUARD_RE = re.compile(re.escape(guard).replace(re.escape(SENTINEL), VAR))
    SPLIT_RE = re.compile(re.escape(patcher.NOTED_MODEL_TURN))
    REFILL_RE = re.compile(re.escape(patcher.CONTINUE_TURN))
    V1_RES = [re.compile(re.escape(f(SENTINEL)).replace(re.escape(SENTINEL), VAR))
              for f in (patcher._v1_expr, patcher._v1_stmt)]


def chunk_files(root):
    """Every compiled chunk under root: name-keyed tables are what this gate replaced."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "node_modules")
        for name in sorted(filenames):
            if not name.endswith(".js") or ".autoos-backup-" in name:
                continue
            found.append(os.path.join(dirpath, name))
    return found


def scan(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return {
        "sites": len(SITE_RE.findall(text)),
        "guarded": len(GUARD_RE.findall(text)),
        "refill": len(REFILL_RE.findall(text)),
        "split": len(SPLIT_RE.findall(text)),
        "v1": sum(len(r.findall(text)) for r in V1_RES),
    }


def read_summary(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    matches = SUMMARY_RE.findall(text)
    if not matches:
        return None, "%s carries no 'Done: N patched, N skipped, N errors' line" % path
    patched, skipped, errors = (int(v) for v in matches[-1])
    return {"patched": patched, "skipped": skipped, "errors": errors}, None


def check_summaries(label, parsed, total, problems, expect_idle):
    if parsed is None:
        return
    if parsed["errors"]:
        problems.append("%s: the patcher reported %d error(s)"
                        % (label, parsed["errors"]))
    counted = parsed["patched"] + parsed["skipped"]
    if counted != total:
        problems.append("%s: the patcher decided %d site(s) but the tree has %d call "
                        "site(s) - its table does not cover every chunk"
                        % (label, counted, total))
    if expect_idle and parsed["patched"]:
        problems.append("%s: a second run patched %d site(s); the guard is not "
                        "idempotent" % (label, parsed["patched"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("chunks_dir", help="directory of compiled chunks to judge")
    parser.add_argument("--min-sites", type=int, default=12,
                        help="the smallest site count this gateway is known to have (%(default)s)")
    parser.add_argument("--run1", help="summary of the patcher's first run")
    parser.add_argument("--run2", help="summary of the patcher's second (idempotence) run")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.chunks_dir):
        print("VERTEX-GATE: VERDICT=FAIL")
        print("GATE-FAIL: not a directory: %s" % args.chunks_dir)
        return 1
    try:
        build_patterns(load_patcher())
    except Exception as exc:
        print("VERTEX-GATE: VERDICT=FAIL")
        print("GATE-FAIL: cannot load the guard bytes from the patcher: %s" % exc)
        return 1

    rows = []
    unreadable = []
    for path in chunk_files(args.chunks_dir):
        try:
            counts = scan(path)
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append("%s: %s" % (path, exc))
            continue
        rows.append((path, counts))

    total = sum(c["sites"] for _, c in rows)
    patched = sum(c["guarded"] for _, c in rows)
    refill = sum(c["refill"] for _, c in rows)
    split = sum(c["split"] for _, c in rows)
    v1 = sum(c["v1"] for _, c in rows)

    problems = []
    for path, counts in rows:
        if counts["sites"]:
            print("site    %-44s call=%d guard=%d refill=%d split=%d"
                  % (os.path.relpath(path, args.chunks_dir), counts["sites"],
                     counts["guarded"], counts["refill"], counts["split"]))
        if counts["guarded"] > counts["sites"]:
            problems.append("%s: %d guard(s) at %d call site(s)"
                            % (path, counts["guarded"], counts["sites"]))
        if counts["sites"] > counts["guarded"]:
            problems.append("%s: %d call site(s) are not guarded - add them to "
                            "tools/apply-vertex-patch.py"
                            % (path, counts["sites"] - counts["guarded"]))
        if counts["guarded"] > counts["refill"]:
            problems.append("%s: %d guard(s) but only %d refill(s)"
                            % (path, counts["guarded"], counts["refill"]))
        if counts["guarded"] != counts["split"]:
            problems.append("%s: %d guard(s) but %d mixed-turn split(s) - a v2 "
                            "guard is not a v3 guard" % (path, counts["guarded"],
                                                         counts["split"]))
    for line in unreadable:
        problems.append("chunk unreadable: %s" % line)
    if not rows:
        problems.append("no compiled chunk files under %s" % args.chunks_dir)
    if v1:
        problems.append("%d v1 guard(s) (contents.length>1) left in the tree - "
                        "the broken guard would still 400 a lone model turn" % v1)
    if patched != total:
        problems.append("patched=%d != total=%d" % (patched, total))
    if refill != total:
        problems.append("refill=%d != total=%d" % (refill, total))
    if split != total:
        problems.append("split=%d != total=%d" % (split, total))
    if total < args.min_sites:
        problems.append("total=%d is below the minimum %d - the gateway lost call "
                        "sites, or the wrong directory was judged" % (total, args.min_sites))

    for label, path, idle in (("run1", args.run1, False), ("run2", args.run2, True)):
        if path is None:
            continue
        parsed, error = read_summary(path)
        if error:
            problems.append("%s: %s" % (label, error))
        check_summaries(label, parsed, total, problems, idle)

    print("VERTEX-GATE: total=%d patched=%d refill=%d split=%d v1=%d files=%d "
          "minimum=%d verdict=%s" % (total, patched, refill, split, v1, len(rows),
                                     args.min_sites, "FAIL" if problems else "PASS"))
    for problem in problems:
        print("GATE-FAIL: %s" % problem)
    print("VERTEX-GATE: VERDICT=%s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
