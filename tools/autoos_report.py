#!/usr/bin/env python3
"""Parse and format BRIEF/REPORT protocol messages (routing v2 spec §8.2).

The protocol is fixed-field, terse:

    BRIEF  id · goal · paths · spec · done-when · skills: <names> · effort · budget
    REPORT id · status (completed|failed|input_required) · files · tests (cmd → result) · blockers · lessons

Bulky output goes to files; messages carry paths. §5.7 gate: "the report's claims
match the diff" — check_report() implements that comparison.

Workers print prose around the block, so the parser finds the LAST "BRIEF <id>"
or "REPORT <id>" block in free text. Two forms are accepted:

1. "·"-joined single-line: all fields on one line, separated by "·".
   Each segment may start with the field name followed by ': ' or ' ' (e.g.
   "status completed" or "status: completed") — the field name is stripped
   automatically. Bare values (no field name) are also accepted (legacy form).

2. Multi-line "field: value": each field on its own line, case-insensitive.

Lists are joined with "; " in the single-line form. Tests entries are split on
"->" or "→" (unicode arrow). The sentinel values "none" and "-" for list
fields (files, blockers, lessons) produce an empty list.

Exit codes:
    0  — parsed successfully, or check found no problems
    1  — no BRIEF/REPORT block found (parse), or problems found (check)
    2  — usage error, or unreadable/missing file

Usage:
    python3 tools/autoos_report.py parse <file|->
    python3 tools/autoos_report.py check <report-file|-> --changed <file...>
"""
import json
import re
import sys
from typing import Optional


VALID_STATUSES = {"completed", "failed", "input_required"}

REPORT_FIELDS = ["id", "status", "files", "tests", "blockers", "lessons"]
BRIEF_FIELDS = ["id", "goal", "paths", "spec", "done_when", "skills", "effort", "budget"]

# Normalised set (hyphen → underscore) for field-name prefix detection
_REPORT_FIELDS_NORM = {f.lower().replace("-", "_") for f in REPORT_FIELDS}
_BRIEF_FIELDS_NORM = {f.lower().replace("-", "_") for f in BRIEF_FIELDS}


def _split_list(text: str) -> list:
    """Split a semicolon-separated list, stripping whitespace.

    "none" and "-" sentinels produce an empty list.
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if text.lower() in ("none", "-"):
        return []
    return [item.strip() for item in text.split(";") if item.strip()]


def _strip_field_prefix(segment: str, known_fields_norm: set) -> str:
    """Strip an optional ``field:`` or ``field `` prefix from *segment*.

    Accepts ``field: value``, ``field value``, or bare ``value`` (no prefix).
    The field name is matched case-insensitively against *known_fields_norm*
    (which should already have hyphens replaced by underscores).
    Returns the value portion only (with leading/trailing whitespace stripped).
    """
    s = segment.strip()
    if not s:
        return s

    # Colon form: "field: value"
    if ":" in s:
        key, _, val = s.partition(":")
        key_norm = key.strip().lower().replace("-", "_")
        if key_norm in known_fields_norm:
            return val.strip()

    # Space form: "field value"
    idx = s.find(" ")
    if idx > 0:
        first_word = s[:idx]
        first_norm = first_word.lower().replace("-", "_")
        if first_norm in known_fields_norm:
            return s[idx:].strip()

    # No recognised field-name prefix — return the whole segment
    return s


def _parse_test_entry(text: str) -> dict:
    """Split a test entry on '->' or '→' into {cmd, result}."""
    for sep in ("->", "→"):
        if sep in text:
            parts = text.split(sep, 1)
            return {"cmd": parts[0].strip(), "result": parts[1].strip()}
    return {"cmd": text.strip(), "result": ""}


def _format_list(items: list) -> str:
    """Join a list with '; '."""
    return "; ".join(str(item) for item in items)


def _format_test(test: dict) -> str:
    """Format a test entry as 'cmd -> result'."""
    return f"{test['cmd']} -> {test['result']}"


def _find_block(text: str, prefix: str) -> Optional[str]:
    """Find the LAST block starting with '<prefix> <id>' in free text.

    A block starts with a line matching '^<prefix>\\s+\\S+' and continues until
    a blank line or another block header. Returns the block text or None.
    """
    if not text:
        return None

    lines = text.split("\n")
    blocks = []
    current_block = []
    in_block = False

    pattern = re.compile(rf"^{re.escape(prefix)}\s+\S+")

    for line in lines:
        if pattern.match(line):
            if current_block:
                blocks.append("\n".join(current_block))
            current_block = [line]
            in_block = True
        elif in_block:
            if not line.strip():
                blocks.append("\n".join(current_block))
                current_block = []
                in_block = False
            else:
                current_block.append(line)

    if current_block:
        blocks.append("\n".join(current_block))

    return blocks[-1] if blocks else None


def _parse_single_line_report(line: str) -> dict:
    """Parse a single-line REPORT in '·'-joined form."""
    # A one-part line falls through: every field is missing and gets its
    # default, so the dict has the same keys as a full one.
    parts = [p.strip() for p in line.split("·")]
    first = parts[0]
    first = re.sub(r"^REPORT\s+", "", first, flags=re.IGNORECASE)
    result = {"id": first, "missing": []}

    field_map = [
        ("status", 1),
        ("files", 2),
        ("tests", 3),
        ("blockers", 4),
        ("lessons", 5),
    ]

    for field, idx in field_map:
        if idx < len(parts):
            value = _strip_field_prefix(parts[idx], _REPORT_FIELDS_NORM)
            _set_report_field(result, field, value)
        else:
            result["missing"].append(field)
            _set_report_field_default(result, field)

    return result


def _set_report_field(result: dict, field: str, value: str) -> None:
    """Assign *value* to *field* in *result* (REPORT)."""
    if field == "status":
        result["status"] = value
        if value not in VALID_STATUSES:
            result["missing"].append("status")
    elif field == "files":
        result["files"] = _split_list(value)
    elif field == "tests":
        result["tests"] = [_parse_test_entry(t) for t in _split_list(value) if t.strip()]
    elif field == "blockers":
        result["blockers"] = _split_list(value)
    elif field == "lessons":
        result["lessons"] = _split_list(value)


def _set_report_field_default(result: dict, field: str) -> None:
    """Set a default (empty) value for *field* in *result*."""
    if field == "status":
        result["status"] = ""
    elif field == "files":
        result["files"] = []
    elif field == "tests":
        result["tests"] = []
    elif field == "blockers":
        result["blockers"] = []
    elif field == "lessons":
        result["lessons"] = []


def _parse_multiline_report(block: str) -> dict:
    """Parse a multi-line REPORT in 'field: value' form."""
    lines = block.split("\n")
    header = lines[0]
    match = re.match(r"^REPORT\s+(\S+)", header)
    result = {"id": match.group(1) if match else "", "missing": []}

    field_values = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            field_values[key.strip().lower()] = value.strip()

    for field in REPORT_FIELDS[1:]:
        if field in field_values:
            value = field_values[field]
            _set_report_field(result, field, value)
        else:
            result["missing"].append(field)
            _set_report_field_default(result, field)

    return result


def parse_report(text) -> Optional[dict]:
    """Parse the LAST REPORT block in free text. Returns dict or None."""
    if not text or not isinstance(text, str):
        return None

    block = _find_block(text, "REPORT")
    if not block:
        return None

    lines = block.split("\n")
    if len(lines) == 1 or all("·" in line for line in lines):
        return _parse_single_line_report(lines[0])
    else:
        return _parse_multiline_report(block)


def _parse_single_line_brief(line: str) -> dict:
    """Parse a single-line BRIEF in '·'-joined form."""
    # A one-part line falls through: every field is missing and gets its
    # default, so the dict has the same keys as a full one.
    parts = [p.strip() for p in line.split("·")]
    first = parts[0]
    first = re.sub(r"^BRIEF\s+", "", first, flags=re.IGNORECASE)
    result = {"id": first, "missing": []}

    for idx, field in enumerate(BRIEF_FIELDS[1:], start=1):
        if idx < len(parts):
            value = _strip_field_prefix(parts[idx], _BRIEF_FIELDS_NORM)
            _set_brief_field(result, field, value)
        else:
            result["missing"].append(field)
            _set_brief_field_default(result, field)

    return result


def _set_brief_field(result: dict, field: str, value: str) -> None:
    """Assign *value* to *field* in *result* (BRIEF)."""
    if field == "skills":
        result["skills"] = _split_list(value)
    elif field == "paths":
        result["paths"] = _split_list(value)
    elif field == "done_when":
        result["done_when"] = value
    else:
        result[field] = value


def _set_brief_field_default(result: dict, field: str) -> None:
    """Set a default (empty) value for *field* in *result*."""
    if field == "paths":
        result["paths"] = []
    elif field == "skills":
        result["skills"] = []
    else:
        result[field] = ""


def _parse_multiline_brief(block: str) -> dict:
    """Parse a multi-line BRIEF in 'field: value' form."""
    lines = block.split("\n")
    header = lines[0]
    match = re.match(r"^BRIEF\s+(\S+)", header)
    result = {"id": match.group(1) if match else "", "missing": []}

    field_values = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            field_values[key.strip().lower().replace("-", "_")] = value.strip()

    for field in BRIEF_FIELDS[1:]:
        if field in field_values:
            value = field_values[field]
            _set_brief_field(result, field, value)
        else:
            result["missing"].append(field)
            _set_brief_field_default(result, field)

    return result


def parse_brief(text) -> Optional[dict]:
    """Parse the LAST BRIEF block in free text. Returns dict or None."""
    if not text or not isinstance(text, str):
        return None

    block = _find_block(text, "BRIEF")
    if not block:
        return None

    lines = block.split("\n")
    if len(lines) == 1 or all("·" in line for line in lines):
        return _parse_single_line_brief(lines[0])
    else:
        return _parse_multiline_brief(block)


def format_report(data: dict) -> str:
    """Format a REPORT dict as a single-line '·'-joined string."""
    parts = [
        f"REPORT {data.get('id', '')}",
        data.get("status", ""),
        _format_list(data.get("files", [])),
        _format_list([_format_test(t) for t in data.get("tests", [])]),
        _format_list(data.get("blockers", [])),
        _format_list(data.get("lessons", [])),
    ]
    return " · ".join(parts)


def format_brief(data: dict) -> str:
    """Format a BRIEF dict as a single-line '·'-joined string."""
    skills_str = f"skills: {_format_list(data.get('skills', []))}"
    parts = [
        f"BRIEF {data.get('id', '')}",
        data.get("goal", ""),
        _format_list(data.get("paths", [])),
        data.get("spec", ""),
        data.get("done_when", ""),
        skills_str,
        data.get("effort", ""),
        data.get("budget", ""),
    ]
    return " · ".join(parts)


def check_report(report: dict, changed_files: list) -> list:
    """Check a report's claims against the actual diff. Returns list of problems.

    §5.7 gate: "the report's claims match the diff".
    """
    problems = []

    claimed_files = set(report.get("files", []))
    actual_files = set(changed_files)

    extra = claimed_files - actual_files
    for f in sorted(extra):
        problems.append(f"file claimed but not in diff: {f}")

    missing = actual_files - claimed_files
    for f in sorted(missing):
        problems.append(f"file in diff but not claimed: {f}")

    if report.get("status") == "completed" and not report.get("tests"):
        problems.append("status completed but no tests reported")

    return problems


def _read_file_or_stdin(source: str) -> str:
    """Read *source* (``-`` == stdin) and return the text.

    Exits with code 2 on FileNotFoundError.
    """
    if source == "-":
        return sys.stdin.read()
    try:
        with open(source, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"error: file not found: {source}", file=sys.stderr)
        sys.exit(2)


def main():
    """CLI: parse <file|-> | check <report-file|-> --changed <file...>"""
    if len(sys.argv) < 2:
        print("Usage:", file=sys.stderr)
        print("  autoos_report.py parse <file|->", file=sys.stderr)
        print("  autoos_report.py check <report-file|-> --changed <file...>", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]

    if cmd == "parse":
        if len(sys.argv) < 3:
            print("parse requires <file|->", file=sys.stderr)
            sys.exit(2)

        text = _read_file_or_stdin(sys.argv[2])

        result = parse_report(text)
        if result is None:
            result = parse_brief(text)

        if result is None:
            print("no BRIEF or REPORT block found", file=sys.stderr)
            sys.exit(1)

        print(json.dumps(result, indent=2))
        sys.exit(0)

    elif cmd == "check":
        # `--changed` with nothing after it is an empty diff, not a usage error.
        if len(sys.argv) < 4 or sys.argv[3] != "--changed":
            print("check requires <report-file|-> --changed <changed-file...>", file=sys.stderr)
            sys.exit(2)

        report_text = _read_file_or_stdin(sys.argv[2])
        # --changed values are file NAMES, not files to open
        changed_files = sys.argv[4:]

        report = parse_report(report_text)
        if report is None:
            print("no REPORT block found", file=sys.stderr)
            sys.exit(1)

        problems = check_report(report, changed_files)
        if problems:
            for p in problems:
                print(p)
            sys.exit(1)
        else:
            print("ok")
            sys.exit(0)

    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()