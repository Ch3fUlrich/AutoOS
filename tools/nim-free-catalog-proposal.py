#!/usr/bin/env python3
"""Check the keyless NIM catalog and propose registry review, never edit it.

Test-only --check-tool, --gh and --test-timeout require --test-mode, announced
on stderr. Explicit .py gh hooks use sys.executable on every platform; production
uses plain gh. No environment-based executable overrides or
credential lookups: children inherit the environment, including the inventory's
two AUTOOS_NIM_* URL overrides (which still require --snapshot).
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

CHECK_TOOL = Path(__file__).resolve().with_name("nim-free-catalog.py")
ID_PATTERN = re.compile(r"[A-Za-z0-9._/-]{1,200}")
DELTA_PATTERN = re.compile(rf"(added|removed): (\(none\)|{ID_PATTERN.pattern}"
                           rf"(?:, {ID_PATTERN.pattern})*)")
LABEL = "nim-free-catalog"


class ProposalError(Exception):
    """Fail closed without exposing child output or environment values."""


def run(command, operation, timeout):
    try:
        # Text mode would turn an embedded CR into LF before delta validation.
        result = subprocess.run(command, capture_output=True, timeout=timeout)
        result.stdout = result.stdout.decode("utf-8")
        result.stderr = result.stderr.decode("utf-8")
        return result
    except subprocess.TimeoutExpired:
        raise ProposalError(f"{operation}: timed out after {timeout:g} seconds; "
                            "no success reported") from None
    except FileNotFoundError:
        raise ProposalError(f"{operation}: executable not found") from None
    except (OSError, UnicodeError):
        raise ProposalError(f"{operation}: could not launch or decode child output") from None


def delta_ids(output):
    groups = {"added": set(), "removed": set()}
    for line in output.split("\n"):
        line = line.removesuffix("\r")
        if not line.startswith(("added:", "removed:")):
            continue
        match = DELTA_PATTERN.fullmatch(line)
        if match is None:
            raise ProposalError("invalid delta ID or line; no proposal opened")
        kind, value = match.groups()
        if value == "(none)":
            continue
        for resource_id in value.split(", "):
            groups[kind].add(resource_id)
    if not any(groups.values()):
        raise ProposalError("check exited 1 with no added/removed IDs; no proposal opened")
    return groups


def proposal_text(groups):
    today = datetime.now(timezone.utc).date().isoformat()
    title = (f"NIM free catalog changed: +{len(groups['added'])} / "
             f"-{len(groups['removed'])} ({today})")
    rows = [f"- {kind}: `{resource_id}`" for kind, ids in groups.items()
            for resource_id in sorted(ids)]
    body = ["The live public NIM free catalog has changed. Registry proposal only.", ""]
    footer = ["", "Regenerate the snapshot for review:", "", "```sh",
              "python3 tools/nim-free-catalog.py", "```", "",
              "The registry is edited only by a reviewed pull request."]
    row_budget = 200 - len(body) - len(footer)
    truncated = len(rows) > row_budget
    shown = row_budget - int(truncated)
    body += rows[:shown]
    if truncated:
        body.append(f"... and {len(rows) - shown} more")
    body += footer
    return title, "\n".join(body) + "\n"


def gh_call(args, *arguments):
    executable = args.gh or "gh"
    launcher = ([sys.executable, executable] if args.gh and
                Path(executable).suffix == ".py" else [executable])
    operation = "gh " + " ".join(arguments[:2])
    result = run(launcher + list(arguments), operation, args.test_timeout or 60)
    if result.returncode != 0:
        raise ProposalError(f"{operation} failed (exit {result.returncode}); no success reported")
    return result.stdout


def open_proposal(args):
    output = gh_call(args, "issue", "list", "--state", "open", "--label", LABEL,
                     "--json", "number", "--limit", "1")
    try:
        issues = json.loads(output)
    except ValueError:
        raise ProposalError("gh issue list returned invalid JSON") from None
    if not isinstance(issues, list):
        raise ProposalError("gh issue list JSON is not a list")
    if not issues:
        return None
    issue = issues[0]
    number = issue.get("number") if isinstance(issue, dict) else None
    if type(number) is not int or number <= 0:
        raise ProposalError("gh issue list returned an invalid issue number")
    return number


def create_proposal(args, title, body):
    gh_call(args, "label", "create", LABEL, "--force", "--description",
            "NIM free catalog registry proposals requiring reviewed pull requests")
    temporary = None
    try:
        # Close before gh opens it: Windows does not allow reopening a named temp file.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix="nim-free-catalog-", suffix=".md",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(body)
        gh_call(args, "issue", "create", "--label", LABEL, "--title", title,
                "--body-file", str(temporary))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def propose(args):
    command = [sys.executable, str(args.check_tool or CHECK_TOOL), "--check"]
    if args.snapshot is not None:
        command += ["--snapshot", str(args.snapshot)]
    check = run(command, "check failed", args.test_timeout or 300)
    if check.returncode == 0:
        print("no change")
        return 0
    if check.returncode != 1:
        status = (f"signal {-check.returncode}" if check.returncode < 0
                  else f"exit {check.returncode}")
        raise ProposalError(f"check failed ({status}); expected 0 or 1; no proposal opened")
    title, body = proposal_text(delta_ids(check.stdout))
    if not args.no_gh:
        number = open_proposal(args)
        if number is not None:
            print(f"proposal already open: #{number}")
            return 0
    if args.dry_run:
        print(title + "\n\n" + body, end="")
    else:
        create_proposal(args, title, body)
        print("proposal created")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gh", action="store_true", help="local --dry-run without gh")
    parser.add_argument("--test-mode", action="store_true", help="announce explicit test hooks")
    parser.add_argument("--check-tool", type=Path, help="test-only inventory script override")
    parser.add_argument("--gh", help="test-only gh executable or Python script override")
    parser.add_argument("--test-timeout", type=float,
                        help="test-only child timeout in seconds (0 < seconds <= 60)")
    args = parser.parse_args(argv)
    if (args.check_tool is not None or args.gh is not None or
            args.test_timeout is not None) and not args.test_mode:
        parser.error("--check-tool, --gh and --test-timeout require --test-mode")
    if args.test_timeout is not None and not (0 < args.test_timeout <= 60):
        parser.error("--test-timeout must be greater than 0 and at most 60 seconds")
    if args.no_gh and not args.dry_run:
        parser.error("--no-gh requires --dry-run")
    if args.test_mode:
        print("nim-free-catalog-proposal: TEST MODE: explicit test hooks enabled", file=sys.stderr)
    try:
        return propose(args)
    except (ProposalError, OSError, UnicodeError) as exc:
        reason = str(exc) if isinstance(exc, ProposalError) else "local I/O failed"
        print(f"nim-free-catalog-proposal: {reason}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
