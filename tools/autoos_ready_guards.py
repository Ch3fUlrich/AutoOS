"""Pure `ready` guards (AO-WRITER-GUARDS P4a, writer-ops.md §4-5): the brief's FILES
scope, the diff's file scope, the report's CHECK evidence, tool preflight. Every
input arrives as an argument (diff paths, injected `which`); HERMETIC (D-852): no
command, no filesystem, nothing installed."""
import re
import shutil

# Windows device stems, mirrored from tools/autoos_brief.py (private there).
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}
                      | {"%s%d" % (p, i) for p in ("COM", "LPT") for i in range(1, 10)})
# ops.md's FILES line: HEAD claims to be one, RE is the rendered template's exact wording.
_FILES_HEAD = re.compile(r"^ {0,3}FILES\b")
_FILES_RE = re.compile(r"^ {0,3}FILES \(only these; <= 3 files, <= 200 lines changed\):"
                       r"[ \t]*(?P<paths>.*?)\.[ \t]Do not touch other lines/files\.[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_QUOTE_RE = re.compile(r"^ {0,3}>")
_CHECK_RE = re.compile(r"^ {0,3}CHECK[ \t]+(\d+)[ \t]*:[ \t]*(?P<rest>.*)$")
_VERDICT_RE = re.compile(r"^(PASS|FAIL|INPUT_REQUIRED)(?:[ \t].*)?$")
REQUIRED_TOOLS = ("yamllint", "ansible-playbook", "ansible-lint", "gitleaks", "pre-commit")
# Documented install step, quoted for the operator; nothing runs it here.
INSTALL_HELP = ("pipx install yamllint ansible-core ansible-lint pre-commit\n"
                "gitleaks: the vendor release binary on PATH "
                "(https://github.com/gitleaks/gitleaks/releases)")


class GuardError(ValueError):
    """The brief, the report shape, or a caller argument is not usable."""


def norm_path(path):
    """Comparable path form, or None when it is not a plain relative path.
    Restates tools/autoos_brief.py's unexported `_check_single_path`; no Unicode
    folding on purpose, so a look-alike is a *different* name and every caller
    that reads None refuses it (fail closed)."""
    if (type(path) is not str or not path or not path.isascii() or path != path.strip()
            or any(ord(c) < 0x20 or ord(c) == 0x7f for c in path)):
        return None
    parts = path.split("/")
    if any(c in path for c in "*?[]:\\") or path.endswith(("/", ".")):
        return None
    if "" in parts or ".." in parts:
        return None
    parts = [p for p in parts if p != "."]
    if any(c != c.strip() or c.endswith(".") or c.split(".")[0].upper() in _RESERVED
           for c in parts):
        return None
    return "/".join(parts).lower() or None


def brief_files(brief_text):
    """Normalised paths the brief allows, from its one canonical FILES line."""
    heads = [ln for ln in brief_text.splitlines() if _FILES_HEAD.match(ln)]
    if len(heads) != 1:
        raise GuardError("brief has %d FILES lines, expected exactly 1" % len(heads))
    match = _FILES_RE.match(heads[0])
    if not match:
        raise GuardError("FILES line is not the canonical ops-brief form")
    raw = match.group("paths")
    out = [norm_path(p.strip()) for p in raw.split(",")]
    if None in out or len(out) != len(set(out)) or len(out) > 3:
        raise GuardError("FILES paths unusable (unparsable, duplicate, >3): %r" % raw[:120])
    return out


def scope_fence(diff_paths, allowed):
    """Paths of `diff_paths` that are not an exact member of `allowed`. Renames,
    deletes and mode changes all arrive as paths and the caller passes both rename
    halves; an empty allowed fails closed; membership only, never a prefix test —
    a path under an allowed directory is a violation."""
    ok = {norm_path(a) for a in allowed}
    ok.discard(None)
    return [p for p in diff_paths if norm_path(p) not in ok]


def report_checks(report_text, required=(1, 2, 3, 4, 5, 6)):
    """{ok, missing, failed, input_required} over the report's CHECK lines.
    Fenced (``` or ~~~) and '>'-quoted lines are quoted output and never count.
    No line for n is missing; a FAIL, an unparsable verdict, or conflicting
    duplicates (the last line does not silently win) is failed."""
    req = list(required)
    seen, fenced = {}, False
    for line in report_text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced or _QUOTE_RE.match(line):
            continue
        m = _CHECK_RE.match(line)
        if not m or int(m.group(1)) not in req:
            continue
        v = _VERDICT_RE.match(m.group("rest"))
        seen.setdefault(int(m.group(1)), set()).add(v.group(1) if v else "FAIL")
    missing = sorted(set(req) - set(seen))
    failed = sorted(n for n, vs in seen.items() if len(vs) > 1 or "FAIL" in vs)
    need = sorted(n for n in seen if seen[n] == {"INPUT_REQUIRED"})
    return {"ok": not (missing or failed or need), "missing": missing,
            "failed": failed, "input_required": need}


def tool_preflight(required_tools=REQUIRED_TOOLS, which=shutil.which):
    """`required_tools` `which` cannot find, in order; installs nothing. The
    lookup is injected to keep this hermetic; a bad tool name is refused and a
    lookup that raises counts as missing, never skipped."""
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
        if not found and tool not in missing:
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
