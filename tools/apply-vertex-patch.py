#!/usr/bin/env python3
"""
apply-vertex-patch.py — Apply the trailing model turn strip to compiled chunks.

Patches the mergeConsecutiveSameRoleContents call sites in the compiled
webpack/turbopack chunks so a translated Vertex/Gemini request never ends on a
role:"model" turn and never ends on an empty contents array either.

Every site is decided in three states, and only those three (the why is in
configuration/omniroute/vertex-trailing-turn-README.md):

    the v2 guard is present            -> SKIP    (idempotent)
    pristine, or the v1 guard shipped  -> PATCH   (install, or upgrade in place)
    anything else                      -> ERROR   (loud; the build gate stops)

The ERROR branch never guesses: a chunk whose text moved is a chunk this patch
must not half-apply, because the gateway would then ship a request Vertex 400s.

AutoOS lane F1-vertex    |  2026-09-30  (v1: one conditional pop)
AutoOS lane VERTEX-GUARD |  2026-10-09  (v2: pop every trailing model turn, then
                                                  refill emptied contents)
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


def _v1_expr(var):
    """The inserted expression v1 shipped at an expression site."""
    return (var + '.contents.length>1&&"model"===' + var + '.contents[' + var
            + '.contents.length-1].role&&' + var + '.contents.pop()')


def _v1_stmt(var):
    """The statement v1 shipped at a statement site."""
    return ('if(' + var + '.contents.length>1&&"model"===' + var + '.contents[' + var
            + '.contents.length-1].role)' + var + '.contents.pop();')


def _site(chunk, var, kind, decl):
    """One call site: the bytes that make it v2, and the bytes it may arrive as."""
    if kind == "expr":
        # Compiled shape: `return ...,x.contents=merge(x.contents),x}` inside a
        # register( ..., null) call, so the guard has to be one expression.
        head = f"mergeConsecutiveSameRoleContents)({var}.contents),"
        tail = f"{var}}},null)"
        sep = ","
        guard = "(()=>{" + pop_model_turns_then_refill(var) + "})()"
        v1 = _v1_expr(var)
    else:
        head = f"mergeConsecutiveSameRoleContents)({var}.contents??[]);"
        tail = decl
        sep = ""
        guard = pop_model_turns_then_refill(var) + ";"
        v1 = _v1_stmt(var)
    new = head + guard + sep + tail
    return {
        "chunk": chunk,
        "label": f"{chunk} {var}",
        "new": new,
        "forms": [
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
            print(f"SKIP  {label}: v2 guard already present")
            skipped += 1
            continue

        # First form that matches exactly once wins (v1 before pristine); a form
        # that matches twice is a moved chunk, not something to patch blindly.
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
        print(f"PATCH {label}: v2 guard installed over {state} text "
              f"(backup: {os.path.basename(bak)})")
        applied += 1

    print(f"\nDone: {applied} patched, {skipped} skipped, {errors} errors")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
