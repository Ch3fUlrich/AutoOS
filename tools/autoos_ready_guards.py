"""Pure `ready` guards (AO-WRITER-GUARDS P4a, writer-ops.md §4-5): the brief's FILES
scope, the diff's file scope, the report's CHECK evidence, tool preflight, and the
decision helper for whether the guards are required at all. Every input arrives as an
argument (diff paths, injected `which`); HERMETIC (D-852): no command, nothing
installed, no writes — the only disk touch is the default `shutil.which` a caller may
inject away, and the `isdir` read on whatever that lookup returned."""
import os
import re
import shutil

from autoos_writer_rule import R_LEVELS, required_r_level

# Windows device stems, mirrored from tools/autoos_brief.py (private there).
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}
                      | {"%s%d" % (p, i) for p in ("COM", "LPT") for i in range(1, 10)})
# ops.md's FILES line: the canonical prefix is matched at column 0 EXACTLY, RE is the
# rendered template's exact wording. Anything FILES-shaped but not this is a violation.
_CANON_PREFIX = "FILES (only these;"
_FILES_LOOK = re.compile(r"^[ \t]*FILES\b")
_FILES_RE = re.compile(r"^FILES \(only these; <= 3 files, <= 200 lines changed\):"
                       r"[ \t]*(?P<paths>.*?)\.[ \t]Do not touch other lines/files\.[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_QUOTE_ONE = re.compile(r"^ {0,3}> ?")
# Column 0, ASCII digits only (no 'CHECK 03', no '١'), no leading list marker.
_CHECK_RE = re.compile(r"^CHECK[ \t]+(?P<n>[1-9][0-9]*)[ \t]*:[ \t]*(?P<rest>.*)$")
# A verdict needs a non-empty tail after it: a bare 'PASS' proves nothing.
_VERDICT_RE = re.compile(r"^(PASS|FAIL|INPUT_REQUIRED)[ \t]+\S.*$")
REQUIRED_TOOLS = ("yamllint", "ansible-playbook", "ansible-lint", "gitleaks", "pre-commit")
# Documented install step, quoted for the operator; nothing runs it here.
INSTALL_HELP = ("pipx install yamllint ansible-core ansible-lint pre-commit\n"
                "gitleaks: the vendor release binary on PATH "
                "(https://github.com/gitleaks/gitleaks/releases)")


class GuardError(ValueError):
    """The brief, the report shape, or a caller argument is not usable."""


def norm_path(path):
    """Case-FOLDING comparator, used only to detect duplicate FILES entries in
    brief_files (there a case variant IS the same file). Not the scope fence:
    see exact_path, which keeps the case. Restates tools/autoos_brief.py's
    unexported `_check_single_path`; returns None for anything that is not a
    plain relative path, so every caller that reads None refuses it
    (fail closed)."""
    folded = exact_path(path)
    return folded.lower() if folded is not None else None


def exact_path(path):
    """The scope fence's comparable form: the path verbatim except one leading
    './' — nothing else is collapsed. Case, NFKC form and look-alikes are KEPT,
    because on Linux 'Tools/A.PY' and 'tools/a.py' are different files, so they
    compare unequal and become violations. Returns None when the input is not a
    plain relative path (fail closed)."""
    if (type(path) is not str or not path or not path.isascii() or path != path.strip()
            or any(ord(c) < 0x20 or ord(c) == 0x7f for c in path)):
        return None
    if path.startswith("./"):
        path = path[2:]
    if any(c in path for c in "*?[]:\\") or path.endswith(("/", ".")):
        return None
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if any(c != c.strip() or c.endswith(".") or c.split(".")[0].upper() in _RESERVED
           for c in parts):
        return None
    return path or None


def _unquote(line):
    """Drop '>' blockquote markers (with their optional space), outermost first."""
    while True:
        stripped = _QUOTE_ONE.sub("", line, count=1)
        if stripped == line:
            return line
        line = stripped


def _files_looks(line):
    """FILES-shaped at the start of the line, quoted or not."""
    return bool(_FILES_LOOK.match(line) or _FILES_LOOK.match(_unquote(line)))


def brief_files(brief_text):
    """Normalised paths the brief allows, from its ONE canonical FILES line.

    The line must start at column 0 with `_CANON_PREFIX` in the canonical
    wording. A FILES-looking line that is indented, CR-terminated, quoted, or
    sitting inside a ``` / ~~~ fence is never used silently — it raises, as does
    a second canonical line anywhere (fences are tracked by their own character:
    a ``` block closes only on ```, a ~~~ block only on ~~~). Duplicate
    DETECTION stays case-insensitive (same file), but the returned paths keep
    their case for the fence."""
    if type(brief_text) is not str:
        raise GuardError("brief_text must be a str, got %s" % type(brief_text).__name__)
    fence = None
    canonical = []
    for line in brief_text.split("\n"):
        marker = _FENCE_RE.match(line)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1]:
                fence = None
            elif _files_looks(line):
                raise GuardError("FILES line inside a code fence is never used: %r"
                                 % line[:120])
            continue
        if marker:
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        if not _files_looks(line):
            continue
        if "\r" not in line and line.startswith(_CANON_PREFIX) and _FILES_RE.match(line):
            canonical.append(line)
            continue
        raise GuardError("non-canonical FILES line: %r" % line[:120])
    if len(canonical) != 1:
        raise GuardError("brief has %d canonical FILES lines, expected exactly 1"
                         % len(canonical))
    raw = _FILES_RE.match(canonical[0]).group("paths")
    items = [p.strip() for p in raw.split(",")]
    out = [exact_path(p) for p in items]
    folded = [norm_path(p) for p in items]
    if None in out or len(out) > 3 or len(folded) != len(set(folded)):
        raise GuardError("FILES paths unusable (unparsable, duplicate, >3): %r" % raw[:120])
    return out


def scope_fence(diff_paths, allowed):
    """Paths of `diff_paths` that are not an EXACT member of `allowed` (only one
    leading './' is normalised away; case and Unicode form are part of the name).
    Renames, deletes and mode changes all arrive as paths and the caller passes
    both rename halves; an empty allowed fails closed; membership only, never a
    prefix test — a path under an allowed directory is a violation. A non-str in
    `allowed` is a GuardError (the caller passed a broken allow-list); a non-str
    diff path is a violation named by its repr, never a crash."""
    ok = set()
    for a in allowed:
        if type(a) is not str:
            raise GuardError("allowed entry must be a str, got %r" % (a,))
        folded = exact_path(a)
        if folded is not None:
            ok.add(folded)
    violations = []
    for p in diff_paths:
        if type(p) is not str:
            violations.append(repr(p))
            continue
        folded = exact_path(p)
        if folded is None or folded not in ok:
            violations.append(p)
    return violations


def report_checks(report_text, required=(1, 2, 3, 4, 5, 6)):
    """{ok, missing, failed, input_required} over the report's CHECK lines.
    A CHECK line counts only at column 0: indented, '>'-quoted or list-marked
    lines are quoted output and never count, and neither does a zero-padded or
    non-ASCII digit ('CHECK 03', 'check 3', 'CHECK ١'). Fenced (``` or ~~~)
    output never counts. No line for n is missing; a FAIL, a verdict with no
    evidence tail, an unparsable verdict, or conflicting duplicates (the last
    line does not silently win) is failed."""
    req = list(required)
    seen, fenced = {}, False
    for line in report_text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _CHECK_RE.match(line)
        if not m or int(m.group("n")) not in req:
            continue
        v = _VERDICT_RE.match(m.group("rest"))
        seen.setdefault(int(m.group("n")), set()).add(v.group(1) if v else "FAIL")
    missing = sorted(set(req) - set(seen))
    failed = sorted(n for n, vs in seen.items() if len(vs) > 1 or "FAIL" in vs)
    need = sorted(n for n in seen if seen[n] == {"INPUT_REQUIRED"})
    return {"ok": not (missing or failed or need), "missing": missing,
            "failed": failed, "input_required": need}


def _found(found):
    """A `which` hit only when it is a usable non-empty str: not a directory,
    no embedded NUL/newline. Anything else reads as missing."""
    return (type(found) is str and found != ""
            and "\x00" not in found and "\n" not in found
            and not os.path.isdir(found))


def tool_preflight(required_tools=REQUIRED_TOOLS, which=shutil.which):
    """`required_tools` `which` cannot find, in order; installs nothing. The
    lookup is injected to keep this hermetic; a bad tool name is refused and a
    lookup that raises counts as missing, never skipped — and so does a result
    that is not a non-empty str, or is a directory, or carries a NUL/newline."""
    if not callable(which):
        raise GuardError("which must be callable")
    missing = []
    for tool in required_tools:
        if type(tool) is not str or not tool.strip():
            raise GuardError("bad tool name: %r" % (tool,))
        try:
            found = which(tool)
        except Exception:  # noqa: BLE001 - a broken lookup fails closed
            found = None
        if not _found(found) and tool not in missing:
            missing.append(tool)
    return missing


def preflight_report(missing):
    """{'state', 'missing', 'message'} for tool_preflight's output."""
    ms = list(missing)
    msg = ("missing tool(s): %s — STOP, report input_required, never skip a check.\n"
           "Documented install step:\n%s" % (", ".join(ms), INSTALL_HELP)) if ms else (
           "all required tools present (%s); nothing installed here"
           % ", ".join(REQUIRED_TOOLS))
    return {"state": "input_required" if ms else "ok", "missing": ms, "message": msg}


def requires_ops_guards(task_type, r_level=None, paths=(), diff_text=""):
    """Whether the ops ready guards must run for this card.

    Delegates the level to tools/autoos_writer_rule.required_r_level (one source
    of the R-scale rules, never restated here) and raises the same ValueError on
    a bad task_type. True for an `ops` card or any card whose required level —
    the declared `r_level` and the computed floor, whichever is higher — reaches
    R2, so a mislabelled docs/code card that touches ops or auth paths still
    gets the guards instead of slipping through on its label."""
    level = required_r_level(task_type, paths, diff_text)
    if r_level is not None:
        if r_level not in R_LEVELS:
            raise ValueError("unknown r_level %r" % (r_level,))
        if R_LEVELS.index(r_level) > R_LEVELS.index(level):
            level = r_level
    return task_type == "ops" or R_LEVELS.index(level) >= R_LEVELS.index("R2")
