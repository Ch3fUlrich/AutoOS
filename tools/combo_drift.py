"""
combo_drift: detect and guard drift between gateway live combos and git config.
Pure functions for parsing, diffing, snapshots, and guard checks.
CLI: check, export, guard.
"""
import json
import os
import sys
import hashlib
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

UNIT_SEP = "\x1f"

def parse_live(text: str) -> dict[str, dict[str, Any]]:
    """Parse live text lines into dict name -> {strategy, models, dropped}."""
    result: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(UNIT_SEP)
        if len(parts) < 3:
            continue
        name = parts[0]
        strategy = parts[1] if len(parts) > 1 else ""
        models_str = parts[2] if len(parts) > 2 else ""
        dropped = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        models = [m for m in models_str.split(",") if m] if models_str else []
        result[name] = {"strategy": strategy, "models": models, "dropped": dropped}
    return result

def load_known_gaps(path: Path) -> tuple[dict[str, str], set[str]]:
    """Load known gaps file. Returns (ref->reason dict, unmanaged set). Missing file = empty."""
    if not path.exists():
        return {}, set()
    with path.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    gaps = {}
    for entry in doc.get("gaps", []):
        if not isinstance(entry, dict):
            raise ValueError(f"known gap needs a reason: {entry}")
        ref = entry.get("ref", "")
        reason = entry.get("reason", "")
        if not reason:
            raise ValueError(f"known gap needs a reason: {ref}")
        gaps[ref] = reason
    unmanaged = set(doc.get("unmanaged", []))
    return gaps, unmanaged

def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _combos_json_bytes(combos: list[dict[str, Any]]) -> bytes:
    return json.dumps(combos, sort_keys=True, separators=(",", ":")).encode("utf-8")

def diff(file_doc: dict[str, Any], live: dict[str, dict[str, Any]],
         gaps: dict[str, str], unmanaged: set[str]) -> list[tuple[str, str, str]]:
    """Return sorted list of (kind, name, detail)."""
    retired = set(file_doc.get("retired", []))
    omitted = set(file_doc.get("omitted", []))
    file_combos = {c["name"]: c for c in file_doc.get("combos", [])}

    ignored = retired | unmanaged

    diffs: list[tuple[str, str, str]] = []

    # Process file combos
    for name, fcombo in file_combos.items():
        if name in ignored:
            continue
        fmodels = fcombo.get("models", [])
        fstrategy = fcombo.get("strategy", "")

        # Remove gap refs from file models
        filtered_fmodels = [m for m in fmodels if m not in gaps]

        if name not in live:
            # Check if all legs are gaps
            if all(m in gaps for m in fmodels):
                diffs.append(("known-gap", name, "no live combo"))
            else:
                diffs.append(("missing", name, ""))
            continue

        lcombo = live[name]
        if lcombo["dropped"] > 0:
            diffs.append(("drift", name,
                          f"live={', '.join(lcombo['models'])} file={', '.join(fmodels)} unreadable steps={lcombo['dropped']}"))
            continue

        lmodels = lcombo["models"]
        if filtered_fmodels != lmodels:
            diffs.append(("drift", name,
                          f"live={', '.join(lmodels)} file={', '.join(filtered_fmodels)}"))
        else:
            # Check if any gap legs were removed to make it match
            removed_gaps = [m for m in fmodels if m in gaps]
            for rg in removed_gaps:
                diffs.append(("known-gap", name, rg))

    # Process live combos not in file
    for name, lcombo in live.items():
        if name in ignored:
            continue
        if name not in file_combos:
            detail = "omitted: no servable leg" if name in omitted else ""
            diffs.append(("extra", name, detail))

    # Sort by kind order then name
    kind_order = {"missing": 0, "drift": 1, "extra": 2, "known-gap": 3}
    diffs.sort(key=lambda x: (kind_order.get(x[0], 99), x[1]))
    return diffs

def snapshot_build(live: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build snapshot dict from live combos (excludes dropped>0, retired, unmanaged handled by caller)."""
    combos = []
    for name in sorted(live.keys()):
        lc = live[name]
        if lc["dropped"] > 0:
            continue
        combos.append({
            "name": name,
            "strategy": lc["strategy"],
            "models": lc["models"]
        })
    combo_bytes = _combos_json_bytes(combos)
    return {
        "count": len(combos),
        "sha256": _compute_sha256(combo_bytes),
        "combos": combos
    }

def snapshot_verify(doc: Any) -> bool:
    """Verify snapshot doc structure and sha256."""
    if not isinstance(doc, dict):
        return False
    if "count" not in doc or "sha256" not in doc or "combos" not in doc:
        return False
    combos = doc["combos"]
    if not isinstance(combos, list):
        return False
    if doc["count"] != len(combos):
        return False
    combo_bytes = _combos_json_bytes(combos)
    return doc["sha256"] == _compute_sha256(combo_bytes)

CLEAN_FORBIDDEN_PREFIXES = ("bzl/", "bazaarlink/", "groq/", "openrouter/", "vertex/")

def clean_violations(file_doc: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    for combo in file_doc.get("combos", []):
        name = combo.get("name", "")
        if not name.endswith("-clean"):
            continue
        for ref in combo.get("models", []):
            if ref.endswith(":free") or any(ref.startswith(p) for p in CLEAN_FORBIDDEN_PREFIXES):
                violations.append(f"forbidden leg in clean combo: {name}: {ref}")
    return sorted(violations)

def guard(live: dict[str, dict[str, Any]], snapshot_doc: Any,
          file_doc: dict[str, Any], unmanaged: set[str],
          unreadable_reason: str | None = None) -> tuple[bool, list[str]]:
    """Guard check. Returns (ok, reasons)."""
    reasons: list[str] = []

    if unreadable_reason:
        reasons.append(f"live store unreadable: {unreadable_reason}")
        return False, reasons

    clean_viols = clean_violations(file_doc)

    # An unusable snapshot proves nothing, but a live store that already equals the file has nothing to lose.
    snap_ok = snapshot_verify(snapshot_doc)

    retired = set(file_doc.get("retired", []))
    ignored = retired | unmanaged

    # Build snapshot lookup
    snap_combos = {c["name"]: c for c in snapshot_doc.get("combos", [])} if snap_ok else {}
    file_combos = {c["name"]: c for c in file_doc.get("combos", [])}

    for name, lcombo in live.items():
        if name in ignored:
            continue
        if lcombo["dropped"] > 0:
            reasons.append(f"unexported live change: {name}")
            continue

        lmodels = lcombo["models"]
        lstrat = lcombo["strategy"]

        # Check against snapshot
        snap_match = False
        if name in snap_combos:
            sc = snap_combos[name]
            if sc["models"] == lmodels and (not lstrat or not sc["strategy"] or sc["strategy"] == lstrat):
                snap_match = True

        # Check against file
        file_match = False
        if name in file_combos:
            fc = file_combos[name]
            if fc.get("models", []) == lmodels:
                file_match = True

        if not (snap_match or file_match):
            reasons.append(f"unexported live change: {name}")

    if reasons and not snap_ok:
        reasons.insert(0, "snapshot missing or does not verify")
    reasons.extend(clean_viols)
    return len(reasons) == 0, reasons

def _read_live_text(live_lines_path: str | None, repo_root: Path) -> tuple[str | None, str | None]:
    """Read live text from file or command. Returns (text, unreadable_reason)."""
    if live_lines_path:
        if live_lines_path == "-":
            return sys.stdin.read(), None
        try:
            return Path(live_lines_path).read_text(encoding="utf-8"), None
        except Exception as e:
            return None, f"live read failed: {e}"

    # Run command
    cmd_str = os.environ.get("AUTOOS_COMBO_LIVE_CMD")
    if cmd_str:
        argv = shlex.split(cmd_str)
    else:
        apply_sh = repo_root / "configuration" / "omniroute" / "apply.sh"
        argv = ["bash", str(apply_sh), "--live-lines"]

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except Exception as e:
        return None, f"live read failed: {e}"

    if proc.returncode == 0:
        return proc.stdout, None
    elif proc.returncode == 3:
        reason = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "unknown"
        return None, reason
    else:
        return None, f"live read failed (rc={proc.returncode})"

def _default_paths(repo_root: Path) -> tuple[Path, Path, Path]:
    base = repo_root / "configuration" / "omniroute"
    return (base / "combos.json", base / "combos.live.json", base / "drift-known-gaps.json")

def _report_path() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "state"
    return base / "autoos" / "combo-drift.json"

def _write_atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tf:
        tf.write(data)
        tmp_name = tf.name
    os.replace(tmp_name, path)

def _append_alert(message: str) -> None:
    inbox = os.environ.get("AUTOOS_ALERT_INBOX")
    if inbox:
        try:
            with open(inbox, "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except Exception:
            pass
    else:
        sys.stderr.write(message + "\n")

def cmd_check(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="combo_drift check", add_help=False)
    parser.add_argument("--alert", action="store_true")
    parser.add_argument("--report", type=str)
    parser.add_argument("--combos", type=str)
    parser.add_argument("--snapshot", type=str)
    parser.add_argument("--known-gaps", type=str)
    parser.add_argument("--live-lines", type=str)
    ns, _ = parser.parse_known_args(args)

    repo_root = Path(__file__).resolve().parents[1]
    combos_path = Path(ns.combos) if ns.combos else _default_paths(repo_root)[0]
    snapshot_path = Path(ns.snapshot) if ns.snapshot else _default_paths(repo_root)[1]
    gaps_path = Path(ns.known_gaps) if ns.known_gaps else _default_paths(repo_root)[2]

    live_text, unreadable = _read_live_text(ns.live_lines, repo_root)
    live_sha256 = _compute_sha256(live_text.encode("utf-8")) if live_text is not None else ""

    if unreadable:
        print(f"drift: live store unreadable - {unreadable}")
        if ns.alert:
            _append_alert(f"combo-drift: live store unreadable ({unreadable})")
        # Write report if requested
        if ns.report:
            report = {
                "checked_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "result": "unreadable",
                "counts": {"missing": 0, "drift": 0, "extra": 0, "known-gap": 0},
                "names": {"missing": [], "drift": [], "extra": [], "known-gap": []},
                "live_sha256": live_sha256
            }
            _write_atomic(Path(ns.report), json.dumps(report, separators=(",", ":")) + "\n")
        return 3

    live = parse_live(live_text)
    file_doc = json.loads(combos_path.read_text(encoding="utf-8"))
    gaps, unmanaged = load_known_gaps(gaps_path)

    diffs = diff(file_doc, live, gaps, unmanaged)

    counts = {"missing": 0, "drift": 0, "extra": 0, "known-gap": 0}
    names = {"missing": [], "drift": [], "extra": [], "known-gap": []}
    for kind, name, detail in diffs:
        counts[kind] = counts.get(kind, 0) + 1
        names[kind].append(name)
        if detail:
            print(f"{kind} {name}: {detail}")
        else:
            print(f"{kind} {name}")

    has_problems = counts["missing"] > 0 or counts["drift"] > 0 or counts["extra"] > 0

    if ns.report:
        report = {
            "checked_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "result": "drift" if has_problems else "sync",
            "counts": counts,
            "names": names,
            "live_sha256": live_sha256
        }
        _write_atomic(Path(ns.report), json.dumps(report, separators=(",", ":")) + "\n")

    if has_problems:
        if ns.alert:
            first5 = ", ".join((names["missing"] + names["drift"] + names["extra"])[:5])
            _append_alert(f"combo-drift: {counts['missing']+counts['drift']+counts['extra']} differences ({first5})")
            return 0
        return 1
    else:
        print(f"in sync: {len(live)} combos ({counts['known-gap']} known gaps)")
        return 0

def cmd_export(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="combo_drift export", add_help=False)
    parser.add_argument("--out", type=str)
    parser.add_argument("--combos", type=str)
    parser.add_argument("--snapshot", type=str)
    parser.add_argument("--known-gaps", type=str)
    parser.add_argument("--live-lines", type=str)
    ns, _ = parser.parse_known_args(args)

    repo_root = Path(__file__).resolve().parents[1]
    combos_path = Path(ns.combos) if ns.combos else _default_paths(repo_root)[0]
    snapshot_path = Path(ns.out) if ns.out else _default_paths(repo_root)[1]
    gaps_path = Path(ns.known_gaps) if ns.known_gaps else _default_paths(repo_root)[2]

    live_text, unreadable = _read_live_text(ns.live_lines, repo_root)
    if unreadable:
        print(f"export: live store unreadable - {unreadable}")
        return 3

    live = parse_live(live_text)
    file_doc = json.loads(combos_path.read_text(encoding="utf-8"))
    gaps, unmanaged = load_known_gaps(gaps_path)

    # Check for dropped > 0 in non-ignored combos
    retired = set(file_doc.get("retired", []))
    ignored = retired | unmanaged
    for name, lc in live.items():
        if name in ignored:
            continue
        if lc["dropped"] > 0:
            print(f"export: combo {name} has unreadable steps")
            return 3

    # Filter live for snapshot (exclude retired/unmanaged)
    snap_live = {n: lc for n, lc in live.items() if n not in ignored and lc["dropped"] == 0}
    snap = snapshot_build(snap_live)
    _write_atomic(snapshot_path, json.dumps(snap, indent=2) + "\n")
    print(f"exported {snap['count']} combos sha256={snap['sha256'][:12]}")
    return 0

def cmd_guard(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="combo_drift guard", add_help=False)
    parser.add_argument("--unreadable", type=str)
    parser.add_argument("--combos", type=str)
    parser.add_argument("--snapshot", type=str)
    parser.add_argument("--known-gaps", type=str)
    parser.add_argument("--live-lines", type=str)
    ns, _ = parser.parse_known_args(args)

    repo_root = Path(__file__).resolve().parents[1]
    combos_path = Path(ns.combos) if ns.combos else _default_paths(repo_root)[0]
    snapshot_path = Path(ns.snapshot) if ns.snapshot else _default_paths(repo_root)[1]
    gaps_path = Path(ns.known_gaps) if ns.known_gaps else _default_paths(repo_root)[2]

    file_doc = json.loads(combos_path.read_text(encoding="utf-8"))
    gaps, unmanaged = load_known_gaps(gaps_path)

    snapshot_doc = None
    if snapshot_path.exists():
        try:
            snapshot_doc = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot_doc = None

    if ns.unreadable:
        ok, reasons = guard({}, snapshot_doc, file_doc, unmanaged, unreadable_reason=ns.unreadable)
    else:
        live_text, unreadable = _read_live_text(ns.live_lines, repo_root)
        if unreadable:
            ok, reasons = guard({}, snapshot_doc, file_doc, unmanaged, unreadable_reason=unreadable)
        else:
            live = parse_live(live_text)
            ok, reasons = guard(live, snapshot_doc, file_doc, unmanaged)

    for r in reasons:
        print(f"guard: {r}")
    return 0 if ok else 4

def main() -> None:
    if len(sys.argv) < 2:
        print("usage: combo_drift.py <check|export|guard> [options]", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "check":
        sys.exit(cmd_check(args))
    elif cmd == "export":
        sys.exit(cmd_export(args))
    elif cmd == "guard":
        sys.exit(cmd_guard(args))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
