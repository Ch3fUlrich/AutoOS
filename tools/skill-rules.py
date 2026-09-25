#!/usr/bin/env python3
"""Utility to check and list skill rule definitions.

Usage:
  skill-rules.py check [FILE...]
  skill-rules.py list [--topic TOPIC] [FILE...]

A rule is one line: `R-<topic>-<nn>: <imperative>. (why: <=12 words; source: <test or
measurement>)` (spec 2026-09-25-routing-v2 section 8.1, D20). check exits 1 on a duplicate
id, a missing or empty source, a why over 12 words, a line over 200 characters, a
near-duplicate imperative (difflib ratio >= 0.85), a missing file or a file without rules.
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
        for idx, line in enumerate(lines, start=1):
            parsed = parse_rule(line)
            if not parsed:
                bad = MALFORMED_REGEX.match(line)
                if bad:
                    problems.append((f, idx, bad.group(1), "malformed id (want R-<topic>-<nn>)"))
                continue
            rule_id, imp, why, source = parsed
            file_rule_count += 1
            total_rules += 1
            rule_entries.append((f, idx, rule_id, imp))
            # duplicate id detection
            if rule_id in id_locations:
                problems.append((f, idx, rule_id, "duplicate id"))
            else:
                id_locations[rule_id] = (f, idx)
            # missing tail
            if why is None or source is None:
                problems.append((f, idx, rule_id, "missing tail"))
                continue
            if not imp.strip():
                problems.append((f, idx, rule_id, "empty imperative"))
            # empty source
            if source.strip() == "":
                problems.append((f, idx, rule_id, "empty source"))
            # why too long (>12 words)
            if len(why.split()) > 12:
                problems.append((f, idx, rule_id, "why too long"))
            # line too long (>200 chars without leading '- ')
            stripped = line.lstrip('- ').strip('\n')
            if len(stripped) > 200:
                problems.append((f, idx, rule_id, "line too long"))
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
        for line in lines:
            parsed = parse_rule(line)
            if not parsed:
                continue
            rule_id, imp, why, source = parsed
            # filter by topic if needed
            if topic:
                m = re.match(r"R-([a-z]+)-\d{2}", rule_id)
                if not m or m.group(1) != topic:
                    continue
            out_lines.append(line)
    return out_lines

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
