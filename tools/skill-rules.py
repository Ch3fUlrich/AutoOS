#!/usr/bin/env python3
"""Utility to check and list skill rule definitions.

Usage:
  skill-rules.py check [FILE...]
  skill-rules.py list [--topic TOPIC] [FILE...]

A rule starts on one line: `R-<topic>-<nn>: <imperative>. (why: <=12 words; source: <test
or measurement>)` (spec 2026-09-25-routing-v2 section 8.1, D20); a rule that grew past 200
characters wraps onto continuation lines indented two spaces (REDCLEAR 2026-09-30) and the
checks run on the joined rule. check exits 1 on a duplicate id, a missing or empty source, a
why over 12 words, a physical line over 200 characters, a near-duplicate imperative
(difflib ratio >= 0.85), a missing file or a file without rules.
"""
import argparse
import sys
import re
import difflib
from pathlib import Path

RULE_REGEX = re.compile(r"^(?:-\s+)?(R-[a-z]+-\d{2}):\s+(.+)$")
# Greedy why, source without ";": the split is at the LAST "; source:".
TAIL_REGEX = re.compile(r"\s*\(why:\s*(.+)\s*;\s*source:\s*([^;]*?)\s*\)\s*$")
MALFORMED_REGEX = re.compile(r"^(?:-\s+)?(R-\S*?):")

# A long rule wraps: continuation lines are indented two spaces and belong to the
# rule above (REDCLEAR 2026-09-30 - the measured rules grew past one 200-char
# line). The semantic checks run on the joined rule; the 200-char cap stays and
# applies to every physical line of it.
CONTINUATION_INDENT = "  "


def iter_rule_blocks(lines):
    """Yield (first line number, physical lines) for every rule in `lines`."""
    block = None
    for lineno, line in enumerate(lines, start=1):
        if block and line.startswith(CONTINUATION_INDENT) and line.strip():
            block[1].append(line)
            continue
        if block:
            yield block
            block = None
        if RULE_REGEX.match(line):
            block = (lineno, [line])
    if block:
        yield block


def rule_text(lines):
    """The logical rule: its physical lines joined into one line."""
    return " ".join(part.strip() for part in lines)


def parse_rule(line):
    m = RULE_REGEX.match(line)
    if not m:
        return None
    rule_id, rest = m.group(1), m.group(2)
    # split tail
    tail_match = TAIL_REGEX.search(rest)
    if not tail_match:
        return (rule_id, rest, None, None)
    why, source = tail_match.group(1), tail_match.group(2)
    # imperative part is before the tail
    imp = rest[: tail_match.start()].rstrip()
    return (rule_id, imp, why, source)

def check_files(files):
    total_rules = 0
    problems = []
    id_locations = {}
    rule_entries = []  # (file, lineno, id, imp)

    for f in files:
        path = Path(f)
        if not path.is_file():
            problems.append((f, 0, "", "file not found"))
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        file_rule_count = 0
        consumed = set()
        for lineno, parts in iter_rule_blocks(lines):
            consumed.update(range(lineno, lineno + len(parts)))
            rule_id, imp, why, source = parse_rule(rule_text(parts))
            file_rule_count += 1
            total_rules += 1
            rule_entries.append((f, lineno, rule_id, imp))
            # duplicate id detection
            if rule_id in id_locations:
                problems.append((f, lineno, rule_id, "duplicate id"))
            else:
                id_locations[rule_id] = (f, lineno)
            # missing tail
            if why is None or source is None:
                problems.append((f, lineno, rule_id, "missing tail"))
                continue
            if not imp.strip():
                problems.append((f, lineno, rule_id, "empty imperative"))
            # empty source
            if source.strip() == "":
                problems.append((f, lineno, rule_id, "empty source"))
            # why too long (>12 words)
            if len(why.split()) > 12:
                problems.append((f, lineno, rule_id, "why too long"))
            # line too long (>200 chars without leading '- '): every physical
            # line of the rule, wrapped continuations included
            for offset, part in enumerate(parts):
                stripped = part.lstrip('- ').strip('\n')
                if len(stripped) > 200:
                    problems.append((f, lineno + offset, rule_id, "line too long"))
        for lineno, line in enumerate(lines, start=1):
            if lineno in consumed or parse_rule(line):
                continue
            bad = MALFORMED_REGEX.match(line)
            if bad:
                problems.append((f, lineno, bad.group(1), "malformed id (want R-<topic>-<nn>)"))
        if file_rule_count == 0:
            problems.append((f, 0, "", "no rules found"))
    # near-duplicate detection
    for i in range(len(rule_entries)):
        f1, l1, id1, imp1 = rule_entries[i]
        for j in range(i + 1, len(rule_entries)):
            f2, l2, id2, imp2 = rule_entries[j]
            seq = difflib.SequenceMatcher(None, imp1.lower(), imp2.lower())
            if seq.ratio() >= 0.85:
                problems.append((f1, l1, id1, f"near-duplicate with {id2}"))
    return total_rules, problems

def list_rules(files, topic=None):
    out_lines = []
    for f in files:
        path = Path(f)
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for _lineno, parts in iter_rule_blocks(lines):
            text = rule_text(parts)
            parsed = parse_rule(text)
            if not parsed:
                continue
            rule_id, imp, why, source = parsed
            # filter by topic if needed
            if topic:
                m = re.match(r"R-([a-z]+)-\d{2}", rule_id)
                if not m or m.group(1) != topic:
                    continue
            out_lines.append(text)
    return out_lines

# R-worker-11 (D-146, SB-C item 2; tightened by SB-C2 item 2): who is asking
# for a transcript summary. The harness that compacts a session asks in plain
# prose and carries no origin; a genuine cross-session message arrives wrapped
# in a leading `<cross-session-message ... from=...>` tag. So `from=` counts
# ONLY as an attribute of that leading wrapper tag: a `from=` in quoted text,
# a code span or the body is text the summarise-ask talks ABOUT, not an
# origin, and a classifier that scans the whole string lets a compaction
# request be dressed up as a peer by embedding the marker in its body.
# Documented residual (SB-C2, stated not fixed): a real peer message that
# arrives WITHOUT the wrapper (a bare `from=` inbox line) classifies as
# harness-compaction — the wrapper is the unforgeable-by-quote half, so the
# safe miss is the comply side, which is why this direction is acceptable.
PEER_ORIGIN = re.compile(r"^\s*<cross-session-message\b[^>]*\bfrom\s*=",
                         re.IGNORECASE)

HARNESS_COMPACTION = "harness-compaction"
PEER = "peer"


def classify_origin(text: str) -> str:
    """`peer` when `text` STARTS with a `<cross-session-message ... from=...>`
    wrapper tag, else `harness-compaction`. Only a `from=` attribute on that
    leading tag decides: a summary request whose body merely quotes `from=`
    is your own harness compacting you (R-worker-11), and a peer message
    without the wrapper is the documented residual — it reads as compaction."""
    return PEER if PEER_ORIGIN.search(text or "") else HARNESS_COMPACTION


def main():
    parser = argparse.ArgumentParser(prog="skill-rules")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    check_parser = subparsers.add_parser("check", help="Validate rule files")
    check_parser.add_argument("files", nargs="*", default=[".agents/skills/unattended-orchestration/SKILL.md"]) 

    list_parser = subparsers.add_parser("list", help="List rule lines")
    list_parser.add_argument("--topic", help="Filter by rule topic")
    list_parser.add_argument("files", nargs="*", default=[".agents/skills/unattended-orchestration/SKILL.md"]) 

    args = parser.parse_args()
    if args.cmd == "check":
        total, problems = check_files(args.files)
        if problems:
            for f, lineno, rid, prob in problems:
                location = f
                if lineno:
                    location += f":{lineno}"
                print(f"{location}: {rid}: {prob}")
            sys.exit(1)
        else:
            print(f"ok: {total} rules")
            sys.exit(0)
    else:  # list
        lines = list_rules(args.files, args.topic)
        for l in lines:
            print(l)

if __name__ == "__main__":
    main()
