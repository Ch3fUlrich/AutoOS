#!/usr/bin/env python3
"""check-file-size.py - the 400-line file-size gate (AO-400LOC, D-653).

Counts the lines of every TRACKED file (``git ls-files -z``, split on NUL:
plain ``ls-files`` mangles non-ASCII names through ``core.quotePath``) in the
checkout the cwd is in and refuses a file that is too big. Hand-written
source, test, script, playbook and doc files stay <= 400 lines; over 400 ->
split by responsibility before adding more (coding-principles, Principle 3).

WHAT IS COUNTED
---------------
Everything tracked, minus:

  * ``*.lock``, data ``*.json``, ``*.svg``, ``*.min.*``;
  * anything under ``vendor/``, ``third_party/`` (this repo's own vendored
    dir) or ``node_modules/``;
  * binary content (a NUL byte in the first 8 KiB): the gate counts text,
    and a line count over an image is not a source-size signal;
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
The baseline-vs-reference check runs on the PROPOSED counts before any
write: a refusal writes nothing, so the flag cannot launder a baseline a
hand-edit grew.

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
#: Binary sniff: a NUL byte in this window marks binary content (git's own
#: test). The gate counts lines of text, and a line count over an image or a
#: packed blob is not a source-size signal.
BINARY_SNIFF_BYTES = 8 * 1024


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


def is_binary(path):
    """A NUL byte in the first 8 KiB: binary content the gate does not count."""
    with open(path, "rb") as handle:
        return b"\0" in handle.read(BINARY_SNIFF_BYTES)


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
    """``({path: lines}, binaries)`` for every tracked file the gate counts.

    ``-z`` and a NUL split: a name may hold any byte, and plain ``ls-files``
    mangles non-ASCII ones through ``core.quotePath`` into strings that name
    no file at all - such a file would silently never be measured.
    """
    run = git(root, "ls-files", "-z")
    if run.returncode != 0:
        raise SystemExit("check-file-size: git ls-files failed: %s"
                         % run.stderr.strip())
    measured, binaries = {}, 0
    for rel in run.stdout.split("\0"):
        if not rel or is_excluded(rel) or rel in allowed:
            continue
        full = full_path(root, rel)
        if not os.path.isfile(full):
            continue
        if is_binary(full):
            binaries += 1
            continue
        measured[rel] = count_lines(full)
    return measured, binaries


def limit_violations(measured, counts):
    """The refusal lines for files over their cap (the limit, or their entry)."""
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
    return problems


def ratchet_violations(counts, ref_counts, ref_origin):
    """The refusal lines for a baseline document that grew: no entry added,
    none raised, wherever the reference can be read."""
    problems = []
    if ref_counts is None:
        return problems
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


def proposed_baseline(counts, measured, exists):
    """``(counts, lowered, created)`` - what ``--update-baseline`` would write.

    Computed before anything is written so the caller can run the ratchet on
    the proposal first: a baseline written before the check is a baseline
    that has already grown by the time the refusal arrives.
    """
    if not exists:
        return ({name: lines for name, lines in measured.items()
                 if lines > LIMIT}, 0, True)
    lowered = 0
    for name in list(counts):
        if name in measured and measured[name] < counts[name]:
            counts[name] = measured[name]
            lowered += 1
    return counts, lowered, False


def write_baseline(root, counts):
    with open(full_path(root, BASELINE_NAME), "w", encoding="utf-8",
              newline="\n") as handle:
        for name in sorted(counts):
            handle.write("%s %d\n" % (name, counts[name]))


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
    measured, binaries = measure(root, allowed)
    ref_counts, ref_errors, ref_origin = reference_baseline(root)
    if args.update_baseline:
        counts, lowered, created = proposed_baseline(
            counts, measured, os.path.exists(full_path(root, BASELINE_NAME)))
        # The baseline-vs-reference check runs on the PROPOSED counts BEFORE
        # any write: --update-baseline lowers counts, it must never launder
        # an entry a hand-edit added or raised. A refusal writes nothing.
        problems = ratchet_violations(counts, ref_counts, ref_origin)
        problems += limit_violations(measured, counts)
    else:
        problems = limit_violations(measured, counts)
        problems += ratchet_violations(counts, ref_counts, ref_origin)
    errors = allow_errors + base_errors + ref_errors
    for line in problems + errors:
        print(line)
    if problems or errors:
        if args.update_baseline:
            print("check-file-size: %s left unchanged" % BASELINE_NAME)
        print("check-file-size: %d violation(s), %d config error(s) - "
              "%d tracked file(s) measured, %d binary file(s) skipped, "
              "%d baseline entr(y/ies)"
              % (len(problems), len(errors), len(measured), binaries,
                 len(counts)))
        return 1
    if args.update_baseline:
        write_baseline(root, counts)
        if created:
            print("check-file-size: wrote %s - %d entr(y/ies) over %d lines"
                  % (BASELINE_NAME, len(counts), LIMIT))
        else:
            print("check-file-size: lowered %d count(s); %d entr(y/ies) kept"
                  % (lowered, len(counts)))
    print("check-file-size: ok - %d tracked file(s) measured, %d binary "
          "file(s) skipped, %d baseline entr(y/ies)"
          % (len(measured), binaries, len(counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
