#!/usr/bin/env python3
"""
apply-vertex-patch.py — Apply the trailing model turn strip to compiled chunks.

Patches the mergeConsecutiveSameRoleContents call sites in the compiled
webpack/turbopack chunks to also strip trailing role:"model" contents.

AutoOS lane F1-vertex  |  2026-09-30
"""
import os
import sys

BASE = os.path.join(
    os.environ.get("APPDATA", ""),
    "npm", "node_modules", "omniroute",
    "dist", ".build", "next", "server", "chunks"
)
# Fallback for non-Windows or custom paths
if not os.path.isdir(BASE):
    BASE = sys.argv[1] if len(sys.argv) > 1 else BASE

# Idempotency marker: the strip code we insert
STRIP_MARKER = '"model"==='

patches = [
    # Group 1: variable f/o (Claude->Gemini uses f, OpenAI->Gemini uses o)
    ("_08_y1bx._.js",
     "mergeConsecutiveSameRoleContents)(f.contents),f},null)",
     'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
    ("_08_y1bx._.js",
     "mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools",
     'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),

    ("_18ct13i._.js",
     "mergeConsecutiveSameRoleContents)(f.contents),f},null)",
     'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
    ("_18ct13i._.js",
     "mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools",
     'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),

    ("_1j_edf1._.js",
     "mergeConsecutiveSameRoleContents)(f.contents),f},null)",
     'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
    ("_1j_edf1._.js",
     "mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools",
     'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),

    ("_1luyz1c._.js",
     "mergeConsecutiveSameRoleContents)(f.contents),f},null)",
     'mergeConsecutiveSameRoleContents)(f.contents),f.contents.length>1&&"model"===f.contents[f.contents.length-1].role&&f.contents.pop(),f},null)'),
    ("_1luyz1c._.js",
     "mergeConsecutiveSameRoleContents)(o.contents??[]);let _=t.tools",
     'mergeConsecutiveSameRoleContents)(o.contents??[]);if(o.contents.length>1&&"model"===o.contents[o.contents.length-1].role)o.contents.pop();let _=t.tools'),

    # Group 2: variable m/s (Claude->Gemini uses m, OpenAI->Gemini uses s)
    ("_15ose6x._.js",
     "mergeConsecutiveSameRoleContents)(m.contents),m},null)",
     'mergeConsecutiveSameRoleContents)(m.contents),m.contents.length>1&&"model"===m.contents[m.contents.length-1].role&&m.contents.pop(),m},null)'),
    ("_15ose6x._.js",
     "mergeConsecutiveSameRoleContents)(s.contents??[]);let A=t.tools",
     'mergeConsecutiveSameRoleContents)(s.contents??[]);if(s.contents.length>1&&"model"===s.contents[s.contents.length-1].role)s.contents.pop();let A=t.tools'),

    ("_1xkpq2s._.js",
     "mergeConsecutiveSameRoleContents)(m.contents),m},null)",
     'mergeConsecutiveSameRoleContents)(m.contents),m.contents.length>1&&"model"===m.contents[m.contents.length-1].role&&m.contents.pop(),m},null)'),
    ("_1xkpq2s._.js",
     "mergeConsecutiveSameRoleContents)(s.contents??[]);let A=t.tools",
     'mergeConsecutiveSameRoleContents)(s.contents??[]);if(s.contents.length>1&&"model"===s.contents[s.contents.length-1].role)s.contents.pop();let A=t.tools'),
]


def main():
    applied = 0
    skipped = 0
    errors = 0

    for fname, old, new in patches:
        fpath = os.path.join(BASE, fname)
        if not os.path.exists(fpath):
            print(f"SKIP  {fname}: file not found")
            skipped += 1
            continue

        content = open(fpath, encoding="utf-8").read()

        # Idempotency check: if the new pattern is already present, skip
        if new in content:
            print(f"SKIP  {fname}: already patched (strip present)")
            skipped += 1
            continue

        count = content.count(old)
        if count == 0:
            # Check if the old pattern was partially modified
            if STRIP_MARKER in content and "mergeConsecutiveSameRoleContents" in content:
                print(f"SKIP  {fname}: merge call modified, strip may already be applied differently")
                skipped += 1
            else:
                print(f"ERROR {fname}: pattern not found")
                errors += 1
        elif count == 1:
            content = content.replace(old, new, 1)
            open(fpath, "w", encoding="utf-8", newline="").write(content)
            print(f"PATCH {fname}: 1 replacement applied")
            applied += 1
        else:
            print(f"ERROR {fname}: {count} matches (expected 1)")
            errors += 1

    print(f"\nDone: {applied} patched, {skipped} skipped, {errors} errors")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
