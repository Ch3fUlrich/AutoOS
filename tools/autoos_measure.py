"""Feature measurement for routing v2 (spec docs/plans/2026-09-25-routing-v2-spec.md 5.1).

This is the I/O half of the resolver: it reads git, the filesystem and each
client's sign-in probe and returns the plain `features` dict the pure planner
consumes. Planning itself never lives here.

`measure` fails closed on an empty `paths`: a card that names nothing cannot be
measured, and guessing a scope would route the task on invented facts. Every
feature reports *how* it was measured in the returned ``sources`` map, so an
`--explain` line can say "declared" instead of "measured".

`client_state` never reads or prints a credential - it asks the client's own
sign-in probe (`autoos_clients.signin_state`) and reports whether its binary is
on PATH; the reason line is the probe's own error text, never a key.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# The six features the planner reads, in the order a report lists them.
FEATURES = ("files", "modules", "fanout", "lines", "tests", "need_tokens")


def estimate_tokens(text: str) -> int:
    """ceil(len(text) / 4): the spec's cheap planning estimate, not a tokenizer."""
    return (len(text) + 3) // 4


def _run_git(repo, args):
    """Run one git command against `repo`; the caller decides what exit 1 means."""
    return subprocess.run(["git", "-C", repo, *args], capture_output=True)


def tracked_files(repo, paths=None):
    """Repo-relative tracked files, optionally scoped to `paths` (dirs or files).

    NUL-separated output so a path with a newline or quote survives intact.
    """
    args = ["ls-files", "-z"]
    if paths:
        args += ["--", *paths]
    proc = _run_git(repo, args)
    if proc.returncode != 0:
        raise ValueError("git ls-files failed in %s: %s"
                         % (repo, proc.stderr.decode("utf-8", "replace").strip()))
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def grep_fanout(repo, symbol):
    """Whole-word reference count of `symbol` across tracked files (git grep).

    `git grep -c` prints `path:count` per matching file; the counts are summed.
    No match is exit 1, which means zero references, not an error. `-e` keeps a
    symbol that starts with `-` from being read as an option.
    """
    proc = _run_git(repo, ["grep", "-w", "-c", "-F", "-e", symbol])
    if proc.returncode != 0:
        return 0
    total = 0
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        _, sep, count = line.rpartition(":")
        if not sep:
            continue
        try:
            total += int(count)
        except ValueError:
            continue
    return total


def _is_test_file(path):
    """Whether `path` is a test file by location or by name (spec 5.1 `tests`)."""
    if path.startswith("tests/") or "/tests/" in path:
        return True
    base = path.rsplit("/", 1)[-1]
    return (base.startswith("test_") and base.endswith(".py")) or \
        base.endswith(".Tests.ps1")


def _module_of(path):
    """Top-level component of a repo-relative path; a root file is module "."."""
    if "/" not in path:
        return "."
    return path.split("/", 1)[0]


def _covered(repo, touched, tracked):
    """The first tracked test file that covers a touched file, else None.

    A test file covers a touched path when its own name contains the touched
    file's stem, or its text contains the touched file's repo path (a suite
    reference). Text is read with errors="replace" so one odd byte cannot hide a
    reference.
    """
    stems = {Path(p).stem for p in touched}
    for candidate in tracked:
        if not _is_test_file(candidate):
            continue
        name = candidate.rsplit("/", 1)[-1]
        if any(stem and stem in name for stem in stems):
            return candidate
        try:
            text = (Path(repo) / candidate).read_text(encoding="utf-8",
                                                      errors="replace")
        except OSError:
            continue
        if any(p in text for p in touched):
            return candidate
    return None


def _read_for_tokens(path):
    """The file's text, or None when it is missing, unreadable or binary.

    A NUL byte is the cheap binary test: tokenising a compiled blob would make
    ``need_tokens`` meaningless, so the file is skipped rather than counted.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    return data.decode("utf-8", "replace")


def measure(card, repo, brief, symbols=(), schema_tokens=0, fanout_backends=None):
    """Measured features for one task card (spec 5.1), plus a ``sources`` map.

    `card` is a normalized v2 card: ``paths`` (repo-relative files/dirs) and the
    optional orchestrator declarations ``files`` and ``lines``. Declarations win
    over measurement - they are the operator's knowledge, and a test pins both
    branches. An empty ``paths`` raises ValueError (fail closed).
    """
    if not isinstance(card, dict):
        raise ValueError("card must be a dict, got %s" % type(card).__name__)
    paths = card.get("paths") or []
    if not paths:
        raise ValueError("card paths is empty: refusing to measure (fail closed)")
    paths = [str(p) for p in paths]

    sources = {}
    declared_files = card.get("files")
    if declared_files is not None:
        files = [str(p) for p in declared_files]
        sources["files"] = "declared"
    else:
        files = tracked_files(repo, paths)
        sources["files"] = "git ls-files"
    n_files = len(files)

    sources["modules"] = "derived"
    modules = len({_module_of(p) for p in files})

    declared_lines = card.get("lines")
    if declared_lines is not None:
        lines = int(declared_lines)
        sources["lines"] = "declared"
    else:
        lines = 20 * n_files
        sources["lines"] = "20*files"

    if not symbols:
        fanout = 0
        sources["fanout"] = "none"
    else:
        backends = [grep_fanout] if fanout_backends is None else list(fanout_backends)
        best = None
        best_backend = "unmeasured"
        for symbol in symbols:
            for backend in backends:
                value = backend(repo, symbol)
                if value is not None:
                    if best is None or value > best:
                        best = value
                        best_backend = getattr(backend, "__name__", repr(backend))
                    break
        fanout = 0 if best is None else best
        sources["fanout"] = best_backend

    covered = _covered(repo, files, tracked_files(repo))
    sources["tests"] = covered if covered is not None else "none"

    need_tokens = estimate_tokens(brief)
    for name in files:
        text = _read_for_tokens(Path(repo) / name)
        if text is not None:
            need_tokens += estimate_tokens(text)
    need_tokens += schema_tokens
    sources["need_tokens"] = "estimate"

    return {"files": n_files, "modules": modules, "fanout": fanout,
            "lines": lines, "tests": covered is not None,
            "need_tokens": need_tokens, "sources": sources}


def client_state(clients_module, env=None):
    """Per-client ``{installed, signed_in, reason}`` from a clients module.

    `clients_module` exposes CLIENTS (name -> client with a ``binary``) and
    ``signin_state``, exactly like tools/autoos_clients.py. A client whose binary
    is not on PATH is not installed and reports no sign-in; an installed one is
    asked its own probe, whose reason is a human line, never a credential.
    """
    env = os.environ if env is None else env
    path = env.get("PATH", os.defpath)
    out = {}
    for name, client in clients_module.CLIENTS.items():
        if shutil.which(client.binary, path=path) is None:
            out[name] = {"installed": False, "signed_in": None,
                         "reason": "not installed"}
            continue
        signed_in, reason = clients_module.signin_state(client, env)
        out[name] = {"installed": True, "signed_in": signed_in, "reason": reason}
    return out
