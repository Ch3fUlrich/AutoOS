#!/usr/bin/env python3
"""apply-gateway-quota-patch.py — Apply gateway quota null-safety patch to compiled chunks.

Loud-fail atomic substitution patch so a quota window without a valid total is treated
as UNKNOWN (remainingPercentage = null), and ignored in the exhaustion decision. A connection
is exhausted iff at least one window has a valid total and every window with a valid total is
exhausted (remaining <= 0 / used >= total). Total-less windows (such as Vertex AI 'spend' tracked
usage) stay not-exhausted, while genuine 0% exhaustion windows stay exhausted.

Fixes closed in this patch:
1. normalizeQuotas computation: total <= 0 evaluates remainingPercentage to null instead of 0.
2. setQuotaCache loop computation: total <= 0 evaluates remainingPercentage to null instead of 0.
3. isExhausted decision: connection is exhausted iff at least one window has a valid total and every
   window with a valid total is exhausted.
4. Snapshot persistence: per-window windowExhausted flag checks null != rem && rem <= 0 so a
   total-less window is persisted as not exhausted (is_exhausted: 0) with null remaining.
5. Snapshot reload: hydrateQuotaCacheFromSnapshots keeps null remainingPercentage as null instead
   of coercing to 0 via '?? 0'.
6. getQuotaWindowStatus: total-less window keeps remainingPercentage as null (unknown) and
   evaluates reachedThreshold to false.

AutoOS lane U13-2  |  2026-10-03
"""

import argparse
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Variable and anchor descriptors for the 12 compiled chunks defining quotaCache logic
CHUNK_DEFS = {
    "[root-of-the-server]__0ius5xo._.js": {
        "v1": "n", "v2": "t", "exh_v": "o", "rem_v": "r", "reload_clamp": "x",
        "w": "_", "clamp": "x", "rem": "u", "used": "o", "exp": "i", "thresh": "r",
    },
    "[root-of-the-server]__0uraaag._.js": {
        "v1": "s", "v2": "n", "exh_v": "u", "rem_v": "t", "reload_clamp": "m",
        "w": "o", "clamp": "m", "rem": "i", "used": "u", "exp": "l", "thresh": "t",
    },
    "[root-of-the-server]__10_1pn4._.js": {
        "v1": "n", "v2": "_", "exh_v": "u", "rem_v": "r", "reload_clamp": "x",
        "w": "t", "clamp": "x", "rem": "c", "used": "u", "exp": "h", "thresh": "r",
    },
    "[root-of-the-server]__1lqj039._.js": {
        "v1": "s", "v2": "n", "exh_v": "u", "rem_v": "t", "reload_clamp": "m",
        "w": "o", "clamp": "m", "rem": "i", "used": "u", "exp": "l", "thresh": "t",
    },
    "[root-of-the-server]__1mvj910._.js": {
        "v1": "n", "v2": "o", "exh_v": "u", "rem_v": "r", "reload_clamp": "A",
        "w": "i", "clamp": "A", "rem": "s", "used": "u", "exp": "l", "thresh": "r",
    },
    "[root-of-the-server]__1qfdzi_._.js": {
        "v1": "n", "v2": "_", "exh_v": "u", "rem_v": "r", "reload_clamp": "x",
        "w": "t", "clamp": "x", "rem": "c", "used": "u", "exp": "h", "thresh": "r",
    },
    "[root-of-the-server]__1xh83py._.js": {
        "v1": "s", "v2": "n", "exh_v": "u", "rem_v": "t", "reload_clamp": "m",
        "w": "o", "clamp": "m", "rem": "i", "used": "u", "exp": "l", "thresh": "t",
    },
    "_0brmz9y._.js": {
        "v1": "i", "v2": "r", "exh_v": "l", "rem_v": "n", "reload_clamp": "x",
        "w": "o", "clamp": "x", "rem": "a", "used": "l", "exp": "s", "thresh": "n",
    },
    "_0w44psh._.js": {
        "v1": "i", "v2": "r", "exh_v": "a", "rem_v": "n", "reload_clamp": "y",
        "w": "o", "clamp": "y", "rem": "l", "used": "a", "exp": "c", "thresh": "n",
    },
    "_15ha7rl._.js": {
        "v1": "r", "v2": "o", "exh_v": "s", "rem_v": "n", "reload_clamp": "x",
        "w": "i", "clamp": "x", "rem": "l", "used": "s", "exp": "u", "thresh": "n",
    },
    "_1sb9s37._.js": {
        "v1": "r", "v2": "i", "exh_v": "u", "rem_v": "n", "reload_clamp": "w",
        "w": "a", "clamp": "w", "rem": "o", "used": "u", "exp": "c", "thresh": "n",
    },
    "_1u7fapo._.js": {
        "v1": "r", "v2": "i", "exh_v": "l", "rem_v": "n", "reload_clamp": "y",
        "w": "o", "clamp": "y", "rem": "a", "used": "l", "exp": "c", "thresh": "n",
    },
}

MODULE_SIGNATURE = b"__omnirouteQuotaCacheState"

ORIG_EXHAUSTION_ANCHOR = ".every(e=>!1!==e.fractionReported&&e.remainingPercentage<=0)"
OLD_EXHAUSTION_PATCH = ".every(e=>!1!==e.fractionReported&&(e.remainingPercentage??100)<=0)"
NEW_EXHAUSTION_DECISION = ".every((e,_,a)=>a.some(w=>null!=w.remainingPercentage)&&(null==e.remainingPercentage||(!1!==e.fractionReported&&e.remainingPercentage<=0)))"


def get_chunk_replacements(fname: str, d: dict):
    """Generate (unpatched_replacements, old_patch_upgrade_replacement) for a chunk."""
    r1 = (
        f"{d['v1']}.total>0?Math.round(({d['v1']}.total-({d['v1']}.used||0))/{d['v1']}.total*100):0",
        f"{d['v1']}.total>0?Math.round(({d['v1']}.total-({d['v1']}.used||0))/{d['v1']}.total*100):null"
    )
    r2 = (
        f"{d['v2']}.total>0?Math.round(({d['v2']}.total-({d['v2']}.used||0))/{d['v2']}.total*100):0",
        f"{d['v2']}.total>0?Math.round(({d['v2']}.total-({d['v2']}.used||0))/{d['v2']}.total*100):null"
    )
    r3 = (
        ORIG_EXHAUSTION_ANCHOR,
        NEW_EXHAUSTION_DECISION
    )
    r3_old_patch = (
        OLD_EXHAUSTION_PATCH,
        NEW_EXHAUSTION_DECISION
    )
    r4 = (
        f"let {d['exh_v']}={d['rem_v']}<=0;",
        f"let {d['exh_v']}=null!={d['rem_v']}&&{d['rem_v']}<=0;"
    )
    r5 = (
        f"remainingPercentage:{d['reload_clamp']}(Number(e.remainingPercentage??e.remaining_percentage??0))",
        f"remainingPercentage:null==(e.remainingPercentage??e.remaining_percentage)?null:{d['reload_clamp']}(Number(e.remainingPercentage??e.remaining_percentage))"
    )
    r6 = (
        f"let {d['rem']}={d['clamp']}({d['w']}.remainingPercentage),{d['used']}={d['clamp']}(100-{d['rem']})",
        f"let {d['rem']}=null=={d['w']}.remainingPercentage?null:{d['clamp']}({d['w']}.remainingPercentage),{d['used']}=null=={d['rem']}?null:{d['clamp']}(100-{d['rem']})"
    )
    r7 = (
        f"reachedThreshold:!{d['exp']}&&!1!=={d['w']}.fractionReported&&({d['rem']}<=0||{d['used']}>={d['thresh']})",
        f"reachedThreshold:!{d['exp']}&&!1!=={d['w']}.fractionReported&&null!={d['rem']}&&({d['rem']}<=0||{d['used']}>={d['thresh']})"
    )
    return [r1, r2, r3, r4, r5, r6, r7], r3_old_patch


def resolve_base_dir(root_arg: str) -> Path:
    """Resolve chunks directory from package root or explicit chunks dir."""
    cand = Path(root_arg).resolve()
    nested_chunks = cand / "dist" / ".build" / "next" / "server" / "chunks"
    if nested_chunks.is_dir():
        return nested_chunks
    if cand.is_dir():
        return cand
    return cand


def scan_signature_chunks(base_dir: Path) -> list:
    """Scan base_dir for files matching MODULE_SIGNATURE."""
    def check_file(entry):
        if not entry.name.endswith(".js"):
            return None
        try:
            with open(entry.path, "rb") as f:
                if MODULE_SIGNATURE in f.read():
                    return entry.name
        except Exception:
            pass
        return None

    matches = []
    with os.scandir(base_dir) as it:
        entries = list(it)

    with ThreadPoolExecutor(max_workers=32) as ex:
        for name in ex.map(check_file, entries):
            if name:
                matches.append(name)
    return sorted(matches)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Apply gateway quota null-safety patch to compiled chunks."
    )
    parser.add_argument(
        "root_pos",
        nargs="?",
        default=None,
        help="Optional positional root directory",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Root directory (omniroute package root or chunks directory directly)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry run: verify patchability without writing files or creating backups",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Alias for --check",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create backup files before modifying (default: True)",
    )
    parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Do not create backup files",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Patch only a specific chunk filename",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    is_check = args.check or args.dry_run

    root_input = args.root if args.root is not None else args.root_pos
    if not root_input or not str(root_input).strip():
        print("ERROR: --root is required and must not be empty.", file=sys.stderr)
        return 1

    base_dir = resolve_base_dir(root_input)
    if not base_dir.is_dir():
        print(f"ERROR: chunks directory not found: {base_dir}", file=sys.stderr)
        return 1

    # Chunk discovery by signature
    signature_chunks = scan_signature_chunks(base_dir)
    unknown_chunks = [c for c in signature_chunks if c not in CHUNK_DEFS]
    if unknown_chunks:
        print(
            f"ERROR: unknown chunk(s) containing quotaCache signature found: {unknown_chunks}",
            file=sys.stderr
        )
        return 1

    if args.file:
        if args.file not in CHUNK_DEFS:
            print(f"ERROR: no patches registered for file: {args.file}", file=sys.stderr)
            return 1
        target_fnames = [args.file]
    else:
        # Check all expected chunks exist
        missing = [fn for fn in CHUNK_DEFS if not (base_dir / fn).is_file()]
        if missing:
            print(f"ERROR: missing expected chunk file(s): {missing}", file=sys.stderr)
            return 1
        target_fnames = sorted(CHUNK_DEFS.keys())

    # Atomic plan computation
    planned_actions = {}
    errors = []

    for fname in target_fnames:
        fpath = base_dir / fname
        if not fpath.is_file():
            errors.append(f"ERROR {fname}: file not found")
            continue

        try:
            with open(fpath, "r", encoding="utf-8", newline="") as f:
                content = f.read()
        except Exception as e:
            errors.append(f"ERROR {fname}: unreadable: {e}")
            continue

        d = CHUNK_DEFS[fname]
        replacements, r3_old_patch = get_chunk_replacements(fname, d)

        # Check if already fully patched with new patch
        is_fully_patched = (
            all(content.count(new_s) == 1 and (old_s == new_s or content.count(old_s) == 0) for old_s, new_s in replacements) and
            content.count(r3_old_patch[0]) == 0
        )
        if is_fully_patched:
            planned_actions[fname] = ("SKIPPED", content, [])
            continue

        # Check if cleanly unpatched (original)
        is_clean_original = (
            all(content.count(old_s) == 1 and (old_s == new_s or content.count(new_s) == 0) for old_s, new_s in replacements) and
            content.count(r3_old_patch[0]) == 0
        )
        if is_clean_original:
            planned_actions[fname] = ("PATCH", content, replacements)
            continue

        # Check if upgrading from old patch version
        # Old patch has new r1 and r2, old r3 marker, and unpatched r4..r7
        r1, r2, r3, r4, r5, r6, r7 = replacements
        is_old_patched = (
            content.count(r1[1]) == 1 and content.count(r1[0]) == 0 and
            content.count(r2[1]) == 1 and content.count(r2[0]) == 0 and
            content.count(r3_old_patch[0]) == 1 and content.count(r3[0]) == 0 and content.count(r3[1]) == 0 and
            content.count(r4[0]) == 1 and content.count(r4[1]) == 0 and
            content.count(r5[0]) == 1 and content.count(r5[1]) == 0 and
            content.count(r6[0]) == 1 and content.count(r6[1]) == 0 and
            content.count(r7[0]) == 1 and content.count(r7[1]) == 0
        )
        if is_old_patched:
            upgrade_replacements = [r3_old_patch, r4, r5, r6, r7]
            planned_actions[fname] = ("UPGRADE", content, upgrade_replacements)
            continue

        # Inconsistent / partial state: fail loudly
        counts = {old[:30]: content.count(old) for old, _ in replacements}
        errors.append(f"ERROR {fname}: inconsistent state / missing anchors: {counts}")

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        print("ABORTED: no files were modified due to validation errors.", file=sys.stderr)
        return 1

    # Atomic execution
    patched_count = 0
    skipped_count = 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if is_check:
        for fname, (action, content, repls) in planned_actions.items():
            if action == "SKIPPED":
                print(f"SKIP  {fname}: already patched")
                skipped_count += 1
            else:
                action_label = "UPGRADE" if action == "UPGRADE" else "PATCH"
                print(f"CHECK {fname}: {len(repls)} replacement(s) ({action_label}) would be applied")
                patched_count += 1
        print(f"\nDone: {patched_count} would be patched, {skipped_count} skipped, 0 errors")
        return 0

    # Two-phase write:
    # Phase 1: Write backups and write new content to temporary files
    tmp_files = []
    write_failed = False

    for fname, (action, content, repls) in planned_actions.items():
        if action == "SKIPPED":
            continue

        fpath = base_dir / fname
        if args.backup:
            bak_path = Path(f"{fpath}.autoos-backup-{stamp}")
            try:
                shutil.copy2(fpath, bak_path)
            except Exception as e:
                print(f"ERROR {fname}: backup failed: {e}", file=sys.stderr)
                write_failed = True
                break

        new_content = content
        for old, new in repls:
            new_content = new_content.replace(old, new, 1)

        tmp_path = base_dir / f"{fname}.autoos-tmp-{stamp}"
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                f.write(new_content)
            tmp_files.append((tmp_path, fpath, fname, action, len(repls)))
        except Exception as e:
            print(f"ERROR {fname}: write failed: {e}", file=sys.stderr)
            write_failed = True
            break

    if write_failed:
        for tmp_path, _, _, _, _ in tmp_files:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        print("ABORTED: staging failed; original files were left unchanged.", file=sys.stderr)
        return 1

    # Phase 2: Atomic rename of all staged files
    for fname, (action, content, repls) in planned_actions.items():
        if action == "SKIPPED":
            print(f"SKIP  {fname}: already patched")
            skipped_count += 1

    for tmp_path, fpath, fname, action, num_repls in tmp_files:
        action_label = "UPGRADE" if action == "UPGRADE" else "PATCH"
        bak_note = f" (backup: {fname}.autoos-backup-{stamp})" if args.backup else ""
        try:
            os.replace(tmp_path, fpath)
            print(f"{action_label} {fname}: {num_repls} replacement(s) applied{bak_note}")
            patched_count += 1
        except Exception as e:
            print(f"ERROR {fname}: rename failed: {e}", file=sys.stderr)
            return 1

    print(f"\nDone: {patched_count} patched, {skipped_count} skipped, 0 errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
