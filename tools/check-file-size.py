#!/usr/bin/env python3
"""check-file-size.py - the 400-line file-size gate (AO-400LOC, D-653).

Counts the lines of every TRACKED file (``git ls-files``) in the checkout the
cwd is in and refuses a file that is too big. Hand-written source, test,
script, playbook and doc files stay <= 400 lines; over 400 -> split by
responsibility before adding more (coding-principles, Principle 3).

WHAT IS COUNTED
---------------
Everything tracked, minus:

  * ``*.lock``, data ``*.json``, ``*.svg``, ``*.min.*``;
  * anything under ``vendor/``, ``third_party/`` (this repo's own vendored
    dir) or ``node_modules/``;
  * paths listed in ``.filesize-allow`` - one ``path reason`` per line. The
    reason is mandatory: an exemption is a promise, not a shrug.

THE BASELINE (``.filesize-baseline``)
------------------------------------
``path count`` per line: the line count a file already had when the gate
landed. A listed file may shrink below its count, never grow above it; a new
file gets no amnesty at all. The document itself is a ratchet - it may
shrink, never grow - measured against the baseline at ``origin/main`` (the
base the pre-push gate already requires fetched) or, failing that, HEAD.

``--update-baseline`` only LOWERS counts: it never raises one and never adds
an entry. With no baseline present it writes the first one from the tree it
sees (the one-time generation); from then on the file only ratchets down.

Lines are counted ``splitlines()``-style, so a final line without a trailing
newline still counts - a ``wc -l`` count would drop exactly the file whose
author forgot the newline.

Exit: 0 clean (or updated), 1 violations or malformed config, 2 usage.
"""
import argparse
import os
import subprocess
import sys

LIMIT = 400
BASELINE_NAME = ".filesize-baseline"
ALLOW_NAME = ".filesize-allow"
#: Vendored / generated trees. `third_party/` is AutoOS's own vendored dir
#: (AGENTS.md); kept beside `vendor/` so the gate covers both spellings.
EXCLUDED_DIRS = frozenset(("vendor", "third_party", "node_modules"))
EXCLUDED_SUFFIXES = (".lock", ".json", ".svg")
EXCLUDED_NAME_PARTS = (".min.",)
#: Where the "may shrink, never grow" ratchet reads its reference from.
REFERENCE_REFS = ("origin/main", "HEAD")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args),
                          capture_output=True, text=True, errors="replace")


def git_toplevel():
    run = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, errors="replace")
    if run.returncode != 0:
        raise SystemExit("check-file-size: the cwd is not in a git checkout "
                         "(run inside one; the gate measures the toplevel)")
    return os.path.abspath(run.stdout.strip())


def is_excluded(path):
    parts = path.split("/")
    name = parts[-1]
    return (any(part in EXCLUDED_DIRS for part in parts)
            or name.endswith(EXCLUDED_SUFFIXES)
            or any(bit in name for bit in EXCLUDED_NAME_PARTS))


def count_lines(path):
    """The editor's count: splitlines(), not the newline count of wc -l."""
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8", errors="replace")
    return len(text.splitlines())


def full_path(root, rel):
    return os.path.join(root, *rel.split("/"))


def read_allow(path):
    """``(allowed, errors)`` - `path reason` per line; a reason is mandatory."""
    allowed, errors = set(), []
    if not os.path.isfile(path):
        return allowed, errors
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(None, 1)
        if len(fields) < 2 or not fields[1].strip():
            errors.append("%s:%d: an allow entry needs a reason: %s"
                          % (ALLOW_NAME, number, line))
        elif fields[0] in allowed:
            errors.append("%s:%d: duplicate entry %s"
                          % (ALLOW_NAME, number, fields[0]))
        else:
            allowed.add(fields[0])
    return allowed, errors


def parse_baseline(text, origin):
    """``(counts, errors)`` from `path count` lines."""
    counts, errors = {}, []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            errors.append("%s:%d: want `path count`, got: %s"
                          % (origin, number, line))
            continue
        name, count = fields
        try:
            count = int(count)
        except ValueError:
            errors.append("%s:%d: count is not a number: %s"
                          % (origin, number, count))
            continue
        if name in counts:
            errors.append("%s:%d: duplicate entry %s" % (origin, number, name))
            continue
        counts[name] = count
    return counts, errors


def read_baseline(path):
    if not os.path.isfile(path):
        return {}, []
    with open(path, encoding="utf-8", errors="replace") as handle:
        return parse_baseline(handle.read(), BASELINE_NAME)


def reference_baseline(root):
    """``(counts, errors, origin)`` the ratchet compares against.

    ``origin/main`` first - a lane is not allowed to grow the baseline relative
    to the base it will merge into - and HEAD when no fetched base carries one
    (a fresh checkout, the first commit of this very file). No reference at
    all means nothing to ratchet against; the per-file limits still hold.
    """
    for ref in REFERENCE_REFS:
        run = git(root, "show", "%s:%s" % (ref, BASELINE_NAME))
        if run.returncode == 0:
            counts, errors = parse_baseline(run.stdout,
                                            "%s:%s" % (ref, BASELINE_NAME))
            return counts, errors, ref
    return None, [], None


def measure(root, allowed):
    """``{path: lines}`` for every tracked file the gate counts."""
    run = git(root, "ls-files")
    if run.returncode != 0:
        raise SystemExit("check-file-size: git ls-files failed: %s"
                         % run.stderr.strip())
    measured = {}
    for rel in run.stdout.splitlines():
        if not rel or is_excluded(rel) or rel in allowed:
            continue
        full = full_path(root, rel)
        if os.path.isfile(full):
            measured[rel] = count_lines(full)
    return measured


def violations(measured, counts, ref_counts, ref_origin):
    """The refusal lines for a measured tree."""
    problems = []
    for path in sorted(measured):
        lines = measured[path]
        if lines <= max(LIMIT, counts.get(path, 0)):
            continue
        if path in counts:
            problems.append(
                "FAIL %s: %d lines > baseline %d - a baselined file may "
                "shrink, never grow (split it, or lower the entry with "
                "--update-baseline after splitting)" % (path, lines, counts[path]))
        else:
            problems.append(
                "FAIL %s: %d lines > %d - split it by responsibility, or add "
                "a reasoned %s entry" % (path, lines, LIMIT, ALLOW_NAME))
    if ref_counts is not None:
        for path in sorted(counts):
            if path not in ref_counts:
                problems.append(
                    "FAIL %s: entry %s added - %s has no such entry; the "
                    "baseline may shrink, never grow"
                    % (BASELINE_NAME, path, ref_origin))
            elif counts[path] > ref_counts[path]:
                problems.append(
                    "FAIL %s: entry %s raised %d -> %d - the baseline may "
                    "shrink, never grow; --update-baseline only lowers counts"
                    % (BASELINE_NAME, path, ref_counts[path], counts[path]))
    return problems


def update_baseline(root, counts, measured):
    """Rewrite the baseline: create it once, afterwards only lower counts."""
    path = full_path(root, BASELINE_NAME)
    if not os.path.exists(path):
        counts = {name: lines for name, lines in measured.items()
                  if lines > LIMIT}
        print("check-file-size: wrote %s - %d entr(y/ies) over %d lines"
              % (BASELINE_NAME, len(counts), LIMIT))
    else:
        lowered = 0
        for name in list(counts):
            if name in measured and measured[name] < counts[name]:
                counts[name] = measured[name]
                lowered += 1
        print("check-file-size: lowered %d count(s); %d entr(y/ies) kept"
              % (lowered, len(counts)))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for name in sorted(counts):
            handle.write("%s %d\n" % (name, counts[name]))
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="check-file-size",
        description="refuse a tracked file over 400 lines unless the baseline "
                    "grandfathers it (and then only while it keeps shrinking)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="rewrite %s: create it if missing, otherwise only "
                         "lower counts - never raise, never add"
                         % BASELINE_NAME)
    args = ap.parse_args(argv)

    root = git_toplevel()
    allowed, allow_errors = read_allow(full_path(root, ALLOW_NAME))
    counts, base_errors = read_baseline(full_path(root, BASELINE_NAME))
    measured = measure(root, allowed)
    if args.update_baseline:
        counts = update_baseline(root, counts, measured)
    ref_counts, ref_errors, ref_origin = reference_baseline(root)

    problems = violations(measured, counts, ref_counts, ref_origin)
    errors = allow_errors + base_errors + ref_errors
    for line in problems + errors:
        print(line)
    if problems or errors:
        print("check-file-size: %d violation(s), %d config error(s) - "
              "%d tracked file(s) measured, %d baseline entr(y/ies)"
              % (len(problems), len(errors), len(measured), len(counts)))
        return 1
    print("check-file-size: ok - %d tracked file(s) measured, %d baseline "
          "entr(y/ies)" % (len(measured), len(counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
