"""Ready writer attribution, independent counted seats and routing-bound waivers."""
import re

from autoos_resolver import author_family, family_key


def writers(commits, guards):
    """Yield every attributed writer in non-merge last paragraphs, not body prose."""
    for commit in commits:
        if (commit.get("parents") or 0) > 1:
            continue
        paragraph = guards.last_paragraph(commit.get("message"))
        for line in paragraph.split("\n"):
            match = guards.WRITER_RE.match(line)
            if match:
                yield match.group("model"), False
            elif guards.HAND_EDIT_RE.match(line):
                yield "Hand-Edit", True


def refusal(commits, registry, report, guards):
    """Apply attribution and independence against COUNTED seats only.

    Independence is defence-in-depth guarding a relaxed cross-family seat floor;
    today's ready entry point already requires two distinct counted families.
    """
    shape = guards.writer_trailer_refusal(commits)
    if shape:
        return "writer-trailer: " + shape
    seats = {family_key(s.get("family")) for s in report["cross_family"].get("seats", [])}
    seats.discard(None)
    seats.discard("")
    for model, hand in writers(commits, guards):
        family = "anthropic" if hand else author_family(model, registry)[0]
        if not family:
            return "writer-family: writer %s has no known family" % model
        if not seats - {family}:
            return ("writer-family: writer %s (%s) has no counted seat of another family"
                    % (model, family))
    return ""


def bound_row(go_gate, full_sha_re, routing_log, log, by, ref, sha, env=None):
    """Shared cap/waiver binding, preserving the cap reader's refusal wording."""
    try:
        gate = go_gate()
    except Exception as exc:
        return False, "cannot load the routing gate: %s" % exc, None, None
    if (not isinstance(ref, str) or not ref.startswith("D-")
            or not gate.REF_RE.match(ref)):
        return False, "%r is not a decision id of the shape D-<n>" % (ref,), None, None
    if not isinstance(sha, str) or not full_sha_re.fullmatch(sha):
        return False, "%r is not a full 40-hex commit id (--sha)" % (sha,), None, None
    ok, reason, commit, all_rows = routing_log(env)
    if not ok:
        return False, reason, None, None
    rows = [row for row in all_rows if row[0] == ref]
    if not rows:
        return False, ("%s names no row in %s on origin/main -- a prose mention, and a "
                       "table line whose first cell is not exactly that id, are not rows"
                       % (ref, log)), None, None
    if len(rows) > 1:
        return False, ("%s names %d rows; one row binds one sha" % (ref, len(rows))), None, None
    row = rows[0]
    if len(row) < 4 or row[3] != by:
        return False, ("row's by cell is %r, not %r"
                       % (row[3] if len(row) > 3 else "", by)), None, None
    if not gate._exact_id(sha).search(" | ".join(row)):
        return False, ("row %s does not name the full --sha %s; one row binds one sha"
                       % (ref, sha)), None, None
    return True, "", commit, row


def waiver(args, go_gate, full_sha_re, routing_log, log, by):
    """Return (ok, durable field); validate before any other ready output."""
    ref = getattr(args, "writer_waiver", None)
    if ref is None:
        return True, ""
    ok, reason, commit, row = bound_row(go_gate, full_sha_re, routing_log, log, by, ref, args.sha)
    if ok and not re.search(r"(?<![\w-])writer-waiver(?![\w-])", row[2]):
        ok, reason = False, "row %s does not name writer-waiver in its decision cell" % ref
    if not ok:
        print("ready: not appended -- writer-waiver %s: %s" % (ref, reason))
        return False, ""
    print("note: writer-waiver %s bound (routing %s); waives "
          "writer-trailer and writer-family if the lane reaches them"
          % (ref, commit))
    return True, " writer-waiver=%s routing=%s" % (ref, commit)
