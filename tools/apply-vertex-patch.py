#!/usr/bin/env python3
"""
apply-vertex-patch.py — Apply the Vertex/Gemini turn-shape guard to compiled chunks.

Patches the mergeConsecutiveSameRoleContents call sites in the compiled
webpack/turbopack chunks so a translated Vertex/Gemini request never ends on a
role:"model" turn, never ends on an empty contents array, and never carries a
user turn that MIXES a functionResponse part with a text part (Vertex 400s the
signed tool path on such a turn even when the body ends on user — D-908).

The guard is one transform, in three shipped generations:

    v3  split each mixed user turn into user[functionResponse parts],
        model[text "Noted."], user[other parts]; then pop every trailing model
        turn and refill an emptied contents with one user turn  <- THIS BUILD
    v2  pop every trailing model turn, then refill an emptied contents (autoos4)
    v1  pop one trailing model turn when contents.length > 1 (autoos3)

Every site is decided in three states, and only those three (the why is in
configuration/omniroute/vertex-trailing-turn-README.md):

    the v3 guard is present            -> SKIP    (idempotent)
    v2, v1, or pristine text           -> PATCH   (install, or upgrade in place)
    anything else                      -> ERROR   (loud; the build gate stops)

The ERROR branch never guesses: a chunk whose text moved is a chunk this patch
must not half-apply, because the gateway would then ship a request Vertex 400s.

AutoOS lane F1-vertex      |  2026-09-30  (v1: one conditional pop)
AutoOS lane VERTEX-GUARD   |  2026-10-09  (v2: pop every trailing model turn,
                                                   then refill emptied contents)
AutoOS lane VERTEX-LIVE    |  2026-10-09  (v3: split mixed tool/text user turns,
                                                   D-908)
"""
import os
import shutil
import sys
from datetime import datetime

BASE = os.path.join(
    os.environ.get("APPDATA", ""),
    "npm", "node_modules", "omniroute",
    "dist", ".build", "next", "server", "chunks"
)
# Fallback for non-Windows or custom paths
if not os.path.isdir(BASE):
    BASE = sys.argv[1] if len(sys.argv) > 1 else BASE

# The turn appended when popping the model tail emptied contents. Vertex rejects
# an empty contents array as flatly as one ending on a model turn, and refusing
# here would keep today's failure: the unattended lane dies on its first turn.
CONTINUE_TURN = '{role:"user",parts:[{text:"Continue."}]}'
# The synthetic model turn that separates the functionResponse user turn from the
# remaining text user turn when a mixed turn is split (v3, D-908).
NOTED_MODEL_TURN = '{role:"model",parts:[{text:"Noted."}]}'

# (chunk, variable of the expression site, variable of the statement site,
#  the declaration that follows the statement site in the compiled text)
CHUNKS = [
    ("_08_y1bx._.js", "f", "o", "let _=t.tools"),
    ("_18ct13i._.js", "f", "o", "let _=t.tools"),
    ("_1j_edf1._.js", "f", "o", "let _=t.tools"),
    ("_1luyz1c._.js", "f", "o", "let _=t.tools"),
    ("_15ose6x._.js", "m", "s", "let A=t.tools"),
    ("_1xkpq2s._.js", "m", "s", "let A=t.tools"),
]


def split_mixed_tool_turns(var):
    """Split each user turn that mixes functionResponse and non-response parts.

    Vertex rejects a user turn carrying BOTH a functionResponse and text on the
    signed tool path (D-908) even though the body ends on user. A mixed turn is
    replaced, in place, by: user[functionResponse parts, original order],
    model["Noted."], user[the other parts, original order]. Text-only and
    functionResponse-only turns are untouched. Pure statements, no side effects
    beyond reassigning `var.contents`.
    """
    return (
        f'{var}.contents={var}.contents.reduce((a,c)=>{{'
        f'const q=c.parts||[];'
        f'if("user"!==c.role){{a.push(c);return a}}'
        f'const r=q.filter(p=>p.functionResponse),x=q.filter(p=>!p.functionResponse);'
        f'if(r.length&&x.length){{a.push({{role:"user",parts:r}});'
        f'a.push({NOTED_MODEL_TURN});'
        f'a.push({{role:"user",parts:x}})}}else{{a.push(c)}}'
        f'return a}},[])'
    )


def pop_model_turns_then_refill(var):
    """Pop every trailing model turn; if that emptied contents, refill one user turn.

    v1 tested `contents.length>1`, so a body of one lone model turn — the shape
    that reproduces the 400 — was never popped; `>=1` alone empties the array,
    which Vertex rejects too. The popped model text is dropped, not re-appended:
    the model is meant to continue from systemInstruction and the kept history.
    """
    return (
        f'for(;{var}.contents.length&&"model"==={var}.contents[{var}.contents.length-1].role;)'
        f'{var}.contents.pop();'
        f'{var}.contents.length||{var}.contents.push({CONTINUE_TURN})'
    )


def _v3_inner(var):
    """The v3 guard body: split mixed turns, then pop/refill."""
    return split_mixed_tool_turns(var) + ";" + pop_model_turns_then_refill(var)


def v3_guard_expr(var):
    """The v3 guard as one expression (the expression site is a `return a,b,c`)."""
    return "(()=>{" + _v3_inner(var) + "})()"


def v3_guard_stmt(var):
    """The v3 guard as statements (the statement site continues with `let ...`)."""
    return _v3_inner(var) + ";"


def v2_guard_expr(var):
    """The v2 guard as one expression; frozen as the v2 -> v3 upgrade anchor."""
    return "(()=>{" + pop_model_turns_then_refill(var) + "})()"


def v2_guard_stmt(var):
    """The v2 guard as statements; frozen as the v2 -> v3 upgrade anchor."""
    return pop_model_turns_then_refill(var) + ";"


def _v1_expr(var):
    """The inserted expression v1 shipped at an expression site."""
    return (var + '.contents.length>1&&"model"===' + var + '.contents[' + var
            + '.contents.length-1].role&&' + var + '.contents.pop()')


def _v1_stmt(var):
    """The statement v1 shipped at a statement site."""
    return ('if(' + var + '.contents.length>1&&"model"===' + var + '.contents[' + var
            + '.contents.length-1].role)' + var + '.contents.pop();')


def _site(chunk, var, kind, decl):
    """One call site: the v3 bytes it should end as, and the bytes it may arrive as."""
    if kind == "expr":
        # Compiled shape: `return ...,x.contents=merge(x.contents),x}` inside a
        # register( ..., null) call, so the guard has to be one expression.
        head = f"mergeConsecutiveSameRoleContents)({var}.contents),"
        tail = f"{var}}},null)"
        sep = ","
        guard = v3_guard_expr(var)
        v2 = v2_guard_expr(var)
        v1 = _v1_expr(var)
    else:
        head = f"mergeConsecutiveSameRoleContents)({var}.contents??[]);"
        tail = decl
        sep = ""
        guard = v3_guard_stmt(var)
        v2 = v2_guard_stmt(var)
        v1 = _v1_stmt(var)
    new = head + guard + sep + tail
    return {
        "chunk": chunk,
        "label": f"{chunk} {var}",
        "new": new,
        # Upgrade anchors, newest first: a v2 site must not be re-anchored on the
        # shorter pristine text it also contains. Order matters only for the .ts
        # site (its pristine line begins every block); the chunks match head+tail.
        "forms": [
            (head + v2 + sep + tail, new, "v2"),
            (head + v1 + sep + tail, new, "v1"),
            (head + tail, new, "pristine"),
        ],
    }


SITES = []
for chunk, expr_var, stmt_var, decl in CHUNKS:
    SITES.append(_site(chunk, expr_var, "expr", decl))
    SITES.append(_site(chunk, stmt_var, "stmt", decl))


def main():
    applied = 0
    skipped = 0
    errors = 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backed_up = set()

    for site in SITES:
        fname, label = site["chunk"], site["label"]
        fpath = os.path.join(BASE, fname)
        if not os.path.isfile(fpath):
            print(f"ERROR {label}: file not found")
            errors += 1
            continue

        content = open(fpath, encoding="utf-8").read()

        if site["new"] in content:
            print(f"SKIP  {label}: v3 guard already present")
            skipped += 1
            continue

        # First form that matches exactly once wins (v2 before v1 before
        # pristine); a form that matches twice is a moved chunk, not something to
        # patch blindly.
        chosen = None
        ambiguous = None
        for old, new, state in site["forms"]:
            count = content.count(old)
            if count > 1:
                ambiguous = (state, count)
                break
            if count == 1 and chosen is None:
                chosen = (old, new, state)
        if ambiguous is not None:
            print(f"ERROR {label}: {ambiguous[1]} matches of the {ambiguous[0]} "
                  f"anchor, expected 1")
            errors += 1
            continue
        if chosen is None:
            # ASCII only: this line goes to stdout, and the console code page on
            # Windows is not guaranteed to be UTF-8.
            print(f"ERROR {label}: no anchor form matched, chunk text changed, "
                  f"refusing to guess")
            errors += 1
            continue
        old, new, state = chosen

        bak = f"{fpath}.autoos-backup-{stamp}"
        if fpath not in backed_up:
            shutil.copy2(fpath, bak)
            backed_up.add(fpath)
        content = content.replace(old, new, 1)
        open(fpath, "w", encoding="utf-8", newline="").write(content)
        print(f"PATCH {label}: v3 guard installed over {state} text "
              f"(backup: {os.path.basename(bak)})")
        applied += 1

    print(f"\nDone: {applied} patched, {skipped} skipped, {errors} errors")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
