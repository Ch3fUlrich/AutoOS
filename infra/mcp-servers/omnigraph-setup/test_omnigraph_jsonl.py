#!/usr/bin/env python3
"""Safety regression tests for omnigraph_jsonl.py.

These guard the invariant whose absence cost central its graphs on 2026-07-17:

    AN EMPTY BODY IS NOT A CLEAN GRAPH.

A failed fetch — dead server, bad token, wrong graph, curl error — returns zero
records. When `verify` called that "clean (no duplicates)" and exited 0, a wiped
stack reported perfect health for every graph, and the operator (an agent) believed
it. Everything here exists to keep no-data failing loudly.

If you are tempted to make `verify` pass on empty input again: don't. Pass
`--allow-empty` at the call site where emptiness is genuinely expected.

Run:  python3 test_omnigraph_jsonl.py     (no dependencies; non-zero exit on failure)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JQ = os.path.join(HERE, "omnigraph_jsonl.py")

CLEAN = ('{"type":"Rule","data":{"slug":"r1","statement":"s"}}\n'
         '{"edge":"ConstrainsProject","from":"r1","to":"p"}\n')
DUPE = '{"edge":"X","from":"a","to":"b"}\n{"edge":"X","from":"a","to":"b"}\n'

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def run(stdin, *args):
    p = subprocess.run([sys.executable, JQ, *args], input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def run_bytes(stdin, *args, encoding="cp1252"):
    """Run the CLI with RAW BYTES and a legacy stdio encoding in the child.

    `text=True` above cannot catch encoding bugs: it encodes stdin with the parent's
    locale and decodes stdout the same way, so a mismatch cancels out. The sync feeds
    UTF-8 bytes from curl into a python whose stdin is cp1252 on a German/US Windows
    box — this reproduces exactly that.
    """
    env = dict(os.environ, PYTHONIOENCODING=encoding)
    p = subprocess.run([sys.executable, JQ, *args], input=stdin, capture_output=True, env=env)
    return p.returncode, p.stdout, p.stderr


@case("empty input must NOT verify clean (the 2026-07-17 data-loss trap)")
def t_empty():
    rc, _, err = run("", "verify")
    assert rc != 0, f"empty input exited {rc} — a failed fetch would read as success"
    assert "NO DATA" in err, f"expected a NO DATA refusal, got: {err!r}"


@case("whitespace-only input must NOT verify clean")
def t_blank():
    rc, _, err = run("\n\n", "verify")
    assert rc != 0, f"blank input exited {rc} — must not pass"


@case("a genuinely empty graph passes only when explicitly allowed")
def t_allow_empty():
    rc, _, err = run("", "verify", "--allow-empty")
    assert rc == 0, f"--allow-empty should pass, got {rc}: {err!r}"


@case("a real graph still verifies clean")
def t_clean():
    rc, _, err = run(CLEAN, "verify")
    assert rc == 0, f"clean data must pass, got {rc}: {err!r}"
    assert "clean (no duplicates)" in err, err


@case("duplicates are still detected")
def t_dupes():
    rc, _, err = run(DUPE, "verify")
    assert rc == 1, f"duplicates must exit 1, got {rc}"
    assert "DUPLICATES PRESENT" in err, err


@case("dedup still collapses duplicates (arg parsing intact)")
def t_dedup():
    rc, out, _ = run(DUPE, "dedup")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert rc == 0 and len(lines) == 1, f"dedup broken: rc={rc} out={out!r}"


# A node whose text carries the punctuation this repo's prose is full of: em dash,
# arrow, non-breaking space, umlaut. In UTF-8 these are multi-byte.
UNICODE_NODE = ('{"type":"Rule","data":{"slug":"r-uni",'
                '"statement":"prefer MCP → fewer tokens — always; café naïve"}}\n')
ASCII_NODE = '{"type":"Rule","data":{"slug":"r-ascii","statement":"plain"}}\n'


@case("pushset: identical non-ASCII data is NOT a change (the write-amplification bug)")
def t_pushset_unicode_identical():
    import tempfile
    both = (UNICODE_NODE + ASCII_NODE).encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", suffix=".jsonl", delete=False) as fh:
        fh.write(both)                       # central: the SAME bytes as local
        central = fh.name
    try:
        rc, out, err = run_bytes(both, "pushset", central)
        emitted = [ln for ln in out.decode("utf-8").splitlines() if ln.strip()]
        assert rc == 0, f"pushset exited {rc}: {err.decode('utf-8', 'replace')}"
        assert emitted == [], (
            f"identical data reported {len(emitted)} change(s) — stdin was decoded with a "
            f"different codec than the central file, so every non-ASCII node looks changed "
            f"and is re-pushed forever. Emitted: {emitted}"
        )
    finally:
        os.unlink(central)


@case("pushset: a REAL change to a non-ASCII node is still detected")
def t_pushset_unicode_real_change():
    import tempfile
    with tempfile.NamedTemporaryFile("wb", suffix=".jsonl", delete=False) as fh:
        fh.write(ASCII_NODE.encode("utf-8"))          # central lacks r-uni entirely
        central = fh.name
    try:
        rc, out, err = run_bytes((UNICODE_NODE + ASCII_NODE).encode("utf-8"), "pushset", central)
        emitted = [ln for ln in out.decode("utf-8").splitlines() if ln.strip()]
        assert rc == 0, f"pushset exited {rc}: {err.decode('utf-8', 'replace')}"
        assert len(emitted) == 1, f"a genuinely new node must be pushed, got {emitted}"
        assert "→" in emitted[0] and "café" in emitted[0], (
            f"the pushed line must carry its text intact, not mojibake: {emitted[0]!r}")
    finally:
        os.unlink(central)


@case("dedup: non-ASCII text survives the round-trip byte-for-byte")
def t_dedup_unicode_roundtrip():
    src = UNICODE_NODE.encode("utf-8")
    rc, out, err = run_bytes(src + src, "dedup")       # same node twice
    assert rc == 0, f"dedup exited {rc}: {err.decode('utf-8', 'replace')}"
    lines = [ln for ln in out.split(b"\n") if ln.strip()]
    assert len(lines) == 1, f"dedup must collapse to 1, got {len(lines)}"
    assert lines[0] == src.strip(), (
        f"text was mangled in transit:\n  in  {src.strip()!r}\n  out {lines[0]!r}")


def main():
    # The runner prints failure messages that quote the data under test — which is
    # non-ASCII by design here. On a cp1252 console that print raises UnicodeEncodeError
    # and the *reporting* crashes on top of the real failure, hiding it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}\n        {exc}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
