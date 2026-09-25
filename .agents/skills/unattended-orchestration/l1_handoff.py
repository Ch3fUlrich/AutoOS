"""Hand an L1 orchestrator's work to a fresh (or /clear-ed) session.

    python .agents/skills/unattended-orchestration/l1_handoff.py [--state FILE] [--out FILE]

Reads the hand-written state file (goal, decisions, open tasks; default
logs/handoff-sessions/<today>/L1-STATE.md), adds a read-only snapshot of the
machine (git, lane worktrees, background Claude sessions, Serena/Graphify
helpers, DONE notes) and writes L1-HANDOFF.md next to it, inside the
repository's git-ignored logs/ folder. Prints the prompt for the new session.

It never stops or kills anything: stale items are listed with the command that
would clean them up, so the new session decides after checking.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess

HELPERS = ("serena", "graphify")


def sh(args, cwd=None) -> str:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def git_state(repo: pathlib.Path) -> dict:
    g = lambda *a: sh(["git", "-C", str(repo), *a])  # noqa: E731
    return {
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "head": g("log", "--oneline", "-1"),
        "main": g("rev-parse", "--short", "main"),
        "origin_main": g("rev-parse", "--short", "origin/main"),
        "dirty": len([l for l in g("status", "--porcelain").splitlines() if l.strip()]),
    }


def lanes(repo: pathlib.Path) -> list[dict]:
    root = repo.parent / (repo.name + "-lanes")
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not (d / ".git").exists():
            continue
        branch = sh(["git", "-C", str(d), "rev-parse", "--abbrev-ref", "HEAD"])
        merged = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", branch, "main"],
                                capture_output=True).returncode == 0 if branch else False
        dirty = bool(sh(["git", "-C", str(d), "status", "--porcelain"]))
        out.append({"path": str(d), "branch": branch, "merged": merged, "dirty": dirty})
    return out


def sessions(repo: pathlib.Path) -> list[dict]:
    if not shutil.which("claude"):
        return []
    try:
        data = json.loads(sh(["claude", "agents", "--json"]) or "[]")
    except ValueError:
        return []
    items = data if isinstance(data, list) else data.get("sessions", [])
    mine = (str(repo), str(repo) + "-lanes")
    return [{"id": s.get("id", ""), "name": s.get("name", ""), "cwd": s.get("cwd", ""),
             "status": s.get("status") or s.get("state", "")}
            for s in items if str(s.get("cwd", "")).startswith(mine)]


def helpers() -> list[dict]:
    """Serena/Graphify processes with their cwd (Linux /proc only; empty elsewhere)."""
    out = []
    proc = pathlib.Path("/proc")
    for p in proc.iterdir() if proc.is_dir() else []:
        if not p.name.isdigit():
            continue
        try:
            args = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            cwd = os.readlink(p / "cwd")
        except OSError:
            continue
        if any(h in args.lower() for h in HELPERS) and "l1_handoff" not in args:
            out.append({"pid": int(p.name), "cwd": cwd, "args": args[:160]})
    return out


def done_notes(repo: pathlib.Path) -> list[str]:
    base = repo / "logs" / "handoff-sessions"
    return sorted(str(p.relative_to(repo)) for p in base.glob("*/done/*.md")) if base.is_dir() else []


def stale(snap: dict) -> list[str]:
    """Cleanup candidates, each with the command that would clean it up."""
    notes = []
    lane_by_path = {l["path"]: l for l in snap["lanes"]}
    for s in snap["sessions"]:
        lane = lane_by_path.get(s["cwd"])
        if s["cwd"] != snap["repo"] and (lane is None or lane["merged"]):
            why = "worktree gone" if lane is None else "branch merged into main"
            notes.append("session %s (%s) - %s: claude stop %s" % (s["name"], s["status"], why, s["id"] or s["name"]))
    live = {s["cwd"] for s in snap["sessions"]}
    for h in snap["helpers"]:
        if "-lanes" in h["cwd"] or "/sandboxes/" in h["cwd"]:
            if not any(h["cwd"].startswith(c) for c in live):
                notes.append("helper pid %d in %s has no live session: kill %d" % (h["pid"], h["cwd"], h["pid"]))
    for l in snap["lanes"]:
        if l["merged"] and not l["dirty"]:
            notes.append("lane %s merged and clean: git worktree remove %s" % (l["branch"], l["path"]))
    return notes


def render(snap: dict, state_text: str) -> str:
    g = snap["git"]
    lines = ["# L1 handoff - %s" % snap["when"], "",
             "Resume as the L1 orchestrator. Verify every live-state line before acting on it.", "",
             "## Live state (snapshot)", "",
             "- repo `%s` on `%s` (%s), %d uncommitted" % (snap["repo"], g["branch"], g["head"], g["dirty"]),
             "- main `%s`, origin/main `%s`" % (g["main"], g["origin_main"])]
    lines += ["- lane `%s`: %s%s" % (l["branch"], "merged" if l["merged"] else "open",
                                     ", dirty" if l["dirty"] else "") for l in snap["lanes"]]
    lines += ["- session `%s` %s in `%s`" % (s["name"], s["status"], s["cwd"]) for s in snap["sessions"]]
    lines += ["- helper pid %d in `%s`" % (h["pid"], h["cwd"]) for h in snap["helpers"]]
    lines += ["", "## Cleanup candidates (check, then run)", ""]
    lines += ["- " + n for n in stale(snap)] or ["- none"]
    lines += ["", "## DONE notes", ""] + (["- " + d for d in snap["done"]] or ["- none"])
    lines += ["", "## Operator state (hand-written)", "", state_text.strip() or "(no state file)", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=sh(["git", "rev-parse", "--show-toplevel"]) or ".")
    ap.add_argument("--state")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    repo = pathlib.Path(a.repo).resolve()
    day = repo / "logs" / "handoff-sessions" / datetime.date.today().strftime("%Y%m%d")
    state = pathlib.Path(a.state) if a.state else day / "L1-STATE.md"
    out = pathlib.Path(a.out) if a.out else day / "L1-HANDOFF.md"
    snap = {"when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "repo": str(repo),
            "git": git_state(repo), "lanes": lanes(repo), "sessions": sessions(repo),
            "helpers": helpers(), "done": done_notes(repo)}
    text = render(snap, state.read_text(encoding="utf-8") if state.is_file() else "")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    rel = out.relative_to(repo) if out.is_relative_to(repo) else out
    print("wrote %s\n\nPrompt for the new session (after /clear or in a fresh one):\n\n"
          "Read %s and continue as the L1 orchestrator (skill: unattended-orchestration). "
          "Verify the live state first, then take the next task." % (out, rel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
