"""JSONL audit log for hostexec (spec status/L1-backlog.spec-host-shell.md
section 5): one line per decision, append-only, fail-closed -- if the line
can't be written, the caller must refuse the call and run nothing.

Layout: <state_dir>/audit-YYYY-MM-DD.jsonl, dir 0700, files 0600, one
os.write() per line on an O_APPEND fd under an flock so concurrent writers
never interleave. `seq` is a per-file monotonic counter that resumes from
the file's last line across a restart (a gap in `seq` means tampering or
loss -- it is never silently renumbered). Also emitted to journald via
`logger -t autoos-exec` when available -- ADVISORY only (review F5: the
journald copy is forgeable by anyone who can run `logger` with that tag;
the 0700/0600 O_APPEND file is the authoritative record).
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator, Sequence


class AuditWriteError(RuntimeError):
    """The audit line could not be written. The caller MUST refuse the call
    and run nothing -- see module docstring (FAIL CLOSED)."""


def default_state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "autoos-exec")


# ─── redaction (spec section 5; review F12 broadens the patterns, F13 strips
# control characters so a log line can never forge terminal output) ────────

_SECRET_NAME_RE = re.compile(r"(?i)(token|secret|password|passwd|bearer|api[_-]?key|credential)")
_SECRET_VALUE_FLAGS = ("--token", "--password", "--passwd", "--secret", "--key",
                        "--api-key", "--bearer", "-u")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")


def sanitize_text(s: str) -> str:
    """Strip C0/C1 control characters (tab kept) so a stored/rendered field
    can never inject a terminal escape or a fake extra log line."""
    return _CONTROL_RE.sub("", s)


# note: 0x0a (LF) and 0x0d (CR) are C0 controls too and MUST be stripped --
# only 0x09 (tab) is kept.


def redact_argv(argv: Sequence[str]) -> list[str]:
    """Best-effort credential redaction for the STORED/rendered argv:
    `KEY=value`-shaped names that look secret, `Bearer <token>`, common
    `--token`/`--password`/... flags with a separate value, and mysql-style
    `-pSECRET`. argv_sha256 (below) hashes the RAW, unredacted form
    separately, so two identical raw calls can still be correlated without
    the secret ever being stored in clear."""
    out: list[str] = []
    mask_next = False
    for tok in argv:
        tok = sanitize_text(tok)
        if mask_next:
            out.append("***")
            mask_next = False
            continue
        if tok == "Bearer":
            out.append(tok)
            mask_next = True
            continue
        if "=" in tok:
            name, _, _value = tok.partition("=")
            bare = name.lstrip("-")
            if _SECRET_NAME_RE.search(bare):
                out.append(f"{name}=***")
                continue
            out.append(tok)
            continue
        if tok.startswith("-p") and len(tok) > 2 and not tok.startswith("--"):
            out.append("-p***")
            continue
        if tok in _SECRET_VALUE_FLAGS:
            out.append(tok)
            mask_next = True
            continue
        out.append(tok)
    return out


def hash_argv(argv: Sequence[str]) -> str:
    """sha256 of the RAW (unredacted) argv, joined with a byte that cannot
    appear in a single argv element (unit separator), so distinct argv
    arrays never collide via naive concatenation."""
    canonical = "\x1f".join(argv).encode("utf-8", "surrogateescape")
    return hashlib.sha256(canonical).hexdigest()


# ─── the log ────────────────────────────────────────────────────────────

def _path_for(state_dir: str, date: datetime.date) -> Path:
    return Path(state_dir) / f"audit-{date.isoformat()}.jsonl"


def _last_seq(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path, "rb") as fh:
            try:
                fh.seek(-8192, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail_bytes = fh.read()
    except OSError:
        return 0
    for raw in reversed([ln for ln in tail_bytes.split(b"\n") if ln.strip()]):
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        seq = record.get("seq")
        if isinstance(seq, int):
            return seq
    return 0


def _default_journald(line: str) -> None:
    logger_bin = shutil.which("logger")
    if not logger_bin:
        return
    try:
        subprocess.run([logger_bin, "-t", "autoos-exec"], input=line.encode("utf-8"),
                        timeout=2, check=False)
    except OSError:
        pass


def _iso(dt: datetime.datetime) -> str:
    dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class AuditLog:
    """One process's handle on the audit log."""

    def __init__(self, state_dir: str | os.PathLike, *,
                 now: Callable[[], datetime.datetime] | None = None,
                 journald: Callable[[str], None] | None = None,
                 policy_sha256: str | None = None, version: str = "0"):
        self.state_dir = Path(state_dir)
        self._now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self._journald = journald if journald is not None else _default_journald
        self._policy_sha256 = policy_sha256
        self._version = version
        self._fd: int | None = None
        self._current_date: datetime.date | None = None

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def preflight(self) -> None:
        """Raise AuditWriteError now if a write would fail. Call this
        BEFORE running an allowed command: fail closed means refusing to
        run rather than running unlogged (spec section 5)."""
        self._ensure_open()

    def _ensure_open(self) -> None:
        today = self._now().date()
        if self._fd is not None and self._current_date == today:
            return
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.state_dir, 0o700)
            path = _path_for(str(self.state_dir), today)
            is_new = not path.exists()
            fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise AuditWriteError(f"cannot open audit log under {self.state_dir}: {exc}") from exc
        self._fd = fd
        self._current_date = today
        self._write_locked(lambda seq: {
            "ts": _iso(self._now()),
            "seq": seq,
            "event": "start",
            "pid": os.getpid(),
            "version": self._version,
            "policy_sha256": self._policy_sha256,
            "new_file": is_new,
        })

    def _write_locked(self, build: Callable[[int], dict]) -> int:
        """Assign the next seq and write one line, all under one flock, so
        two writers (two threads, two processes, or a restarted instance)
        can never both compute the same seq -- the flock is the ONLY source
        of truth for "what is the next seq", never in-memory state, since
        this class must be safe when more than one process holds the log
        open at once (review F20: refuse/serialize a second instance)."""
        assert self._fd is not None
        path = _path_for(str(self.state_dir), self._current_date)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
            try:
                seq = _last_seq(path) + 1
                record = build(seq)
                line = json.dumps(record, sort_keys=True) + "\n"
                os.write(self._fd, line.encode("utf-8"))
            finally:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError as exc:
            raise AuditWriteError(f"cannot write audit log under {self.state_dir}: {exc}") from exc
        self._journald(line.rstrip("\n"))
        return seq

    def write(self, *, actor: str, session: str | None, via: str, host: str,
              argv: Sequence[str], cwd: str, run_as: str, decision: str, rule: str | None,
              reason: str | None, exit_code: int | None, duration_ms: int | None,
              out_bytes: int | None, truncated: bool) -> int:
        """Write one call's audit line. Raises AuditWriteError on failure --
        the caller must treat that as fail-closed (spec section 5)."""
        self._ensure_open()
        return self._write_locked(lambda seq: {
            "ts": _iso(self._now()),
            "seq": seq,
            "actor": actor,
            "session": sanitize_text(session) if session else session,
            "via": via,
            "host": host,
            "argv": redact_argv(argv),
            "argv_sha256": hash_argv(argv),
            "cwd": cwd,
            "run_as": run_as,
            "decision": decision,
            "rule": rule,
            "reason": sanitize_text(reason) if reason else reason,
            "exit": exit_code,
            "duration_ms": duration_ms,
            "out_bytes": out_bytes,
            "truncated": truncated,
        })


# ─── reading (hostexec.py log) ─────────────────────────────────────────────

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(spec: str) -> int:
    m = _SINCE_RE.match(spec.strip())
    if not m:
        raise ValueError(f"bad --since value: {spec!r} (expected e.g. 1h, 30m, 2d)")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


def _iter_log_files(state_dir: str) -> list[Path]:
    d = Path(state_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("audit-*.jsonl") if p.is_file())


def _age_seconds(ts: str | None, now: float) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return now - dt.timestamp()


def _render(record: dict) -> str:
    return (f"{record.get('ts')} seq={record.get('seq')} actor={record.get('actor')} "
            f"decision={record.get('decision')} rule={record.get('rule')} "
            f"host={record.get('host')} via={record.get('via')} exit={record.get('exit')} "
            f"argv={record.get('argv')} reason={record.get('reason')}")


def tail(state_dir: str, *, since_seconds: int | None = None, actor: str | None = None,
         decision: str | None = None) -> Iterator[str]:
    """Yield human-readable lines (oldest first), filtered. Escapes on
    render too (belt and suspenders alongside write-time sanitizing --
    review F13), and never prints raw JSON straight to a terminal."""
    now = time.time()
    for path in _iter_log_files(state_dir):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if record.get("event") == "start":
                        if actor or decision:
                            continue
                        yield sanitize_text(
                            f"[start] {record.get('ts')} pid={record.get('pid')} "
                            f"version={record.get('version')} policy_sha256={record.get('policy_sha256')}")
                        continue
                    if since_seconds is not None:
                        age = _age_seconds(record.get("ts"), now)
                        if age is not None and age > since_seconds:
                            continue
                    if actor and record.get("actor") != actor:
                        continue
                    if decision and record.get("decision") != decision:
                        continue
                    yield sanitize_text(_render(record))
        except OSError:
            continue


def prune(state_dir: str, *, days: int = 90, today: datetime.date | None = None) -> list[str]:
    """Delete audit-YYYY-MM-DD.jsonl files older than `days`, by NAME date
    only -- never by mtime, so a touched or copied file cannot dodge
    retention (spec section 5: "monthly file roll by name, not logrotate")."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    cutoff = today - datetime.timedelta(days=days)
    deleted = []
    for path in _iter_log_files(state_dir):
        m = re.match(r"^audit-(\d{4}-\d{2}-\d{2})\.jsonl$", path.name)
        if not m:
            continue
        try:
            file_date = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError:
                pass
    return deleted
