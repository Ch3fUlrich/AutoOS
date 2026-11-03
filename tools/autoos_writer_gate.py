"""Ready writer attribution, independent counted seats and routing-bound waivers."""
import re
from types import SimpleNamespace

from autoos_resolver import author_family, family_key


def writers(commits, writer_re):
    """Yield every attributed writer in non-merge last paragraphs, not body prose."""
    for commit in commits:
        if (commit.get("parents") or 0) > 1:
            continue
        paragraph = re.split(r"\n[ \t]*\n", (commit.get("message") or "").rstrip())[-1]
        for line in paragraph.split("\n"):
            match = writer_re.match(line)
            if match:
                yield match.group("model"), False
            elif re.match(r"^Hand-Edit:[ \t]+\S.*$", line):
                yield "Hand-Edit", True


def refusal(commits, registry, report, guards):
    """Apply attribution before checking independence against COUNTED seats only."""
    shape = guards.writer_trailer_refusal(commits)
    if shape:
        return "writer-trailer: " + shape
    seats = {family_key(s.get("family")) for s in report["cross_family"].get("seats", [])}
    seats.discard(None)
    seats.discard("")
    for model, hand in writers(commits, guards._WRITER_RE):
        family = "anthropic" if hand else author_family(model, registry)[0]
        if not family:
            return "writer-family: writer %s has no known family" % model
        if not seats - {family}:
            return ("writer-family: writer %s (%s) has no counted seat of another family"
                    % (model, family))
    return ""


def bound_row(agent, ref, sha, env=None):
    """Shared cap/waiver binding, preserving the cap reader's refusal wording."""
    agent = SimpleNamespace(**agent) if isinstance(agent, dict) else agent
    try:
        gate = agent.go_gate()
    except Exception as exc:
        return False, "cannot load the routing gate: %s" % exc, None, None
    if (not isinstance(ref, str) or not ref.startswith("D-")
            or not gate.REF_RE.match(ref)):
        return False, "%r is not a decision id of the shape D-<n>" % (ref,), None, None
    if not isinstance(sha, str) or not agent._FULL_SHA_RE.fullmatch(sha):
        return False, "%r is not a full 40-hex commit id (--sha)" % (sha,), None, None
    ok, reason, commit, all_rows = agent._routing_committed_log(env)
    if not ok:
        return False, reason, None, None
    rows = [row for row in all_rows if row[0] == ref]
    if not rows:
        return False, ("%s names no row in %s on origin/main -- a prose mention, and a "
                       "table line whose first cell is not exactly that id, are not rows"
                       % (ref, agent.CAP_EXCEPTION_LOG)), None, None
    if len(rows) > 1:
        return False, ("%s names %d rows; one row binds one sha" % (ref, len(rows))), None, None
    row = rows[0]
    if len(row) < 4 or row[3] != agent.CAP_EXCEPTION_BY:
        return False, ("row's by cell is %r, not %r"
                       % (row[3] if len(row) > 3 else "", agent.CAP_EXCEPTION_BY)), None, None
    if not gate._exact_id(sha).search(" | ".join(row)):
        return False, ("row %s does not name the full --sha %s; one row binds one sha"
                       % (ref, sha)), None, None
    return True, "", commit, row


def waiver(agent, args):
    """Return (ok, durable field); validate before any other ready output."""
    ref = getattr(args, "writer_waiver", None)
    if ref is None:
        return True, ""
    ok, reason, commit, _ = bound_row(agent, ref, args.sha)
    if not ok:
        print("ready: not appended -- writer-waiver %s: %s" % (ref, reason))
        return False, ""
    print("writer-waiver %s accepted (routing %s): waived writer-trailer and writer-family"
          % (ref, commit))
    return True, " writer-waiver=%s routing=%s" % (ref, commit)
