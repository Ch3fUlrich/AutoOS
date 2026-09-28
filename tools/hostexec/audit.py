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
import sys
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
# SPAWNREDACT item 1: the pattern set itself lives in tools/autoos_redact.py,
# shared with the spawner's worker-output streams (one home per fact). The
# mask stays "***" here, which is what the audit log's own tests pin.

_TOOLS_DIR = str(Path(__file__).resolve().parent.parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import autoos_redact as _redact  # noqa: E402

sanitize_text = _redact.sanitize_text
redact_argv = _redact.redact_argv


def hash_argv(argv: Sequence[str]) -> str:
    """sha256 of the REDACTED argv (item 5), joined with a byte that cannot
    appear in a single redacted element (unit separator -- redaction strips
    it), so distinct argv arrays never collide via naive concatenation. The
    raw form is deliberately NOT hashed: a digest of a raw secret is
    offline-guessable, so a secret's value must not influence the digest."""
    canonical = "\x1f".join(redact_argv(argv)).encode("utf-8", "surrogateescape")
    return hashlib.sha256(canonical).hexdigest()


# ─── the log ────────────────────────────────────────────────────────────

def _path_for(state_dir: str, date: datetime.date) -> Path:
    return Path(state_dir) / f"audit-{date.isoformat()}.jsonl"


def _last_seq(path: Path) -> int:
    """Last seq in the file, reading backwards until a complete JSON line
    parses (any length -- Qoder-6). A legal record can be 64x4096 chars, far
    longer than any fixed window, so a fixed 8KB tail would hold only a
    fragment and restart seq at 1. Returns 0 for missing/unreadable/empty."""
    if not path.exists():
        return 0
    try:
        with open(path, "rb") as fh:
            try:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
            except OSError:
                return 0
            chunk = 8192
            offset = size
            buf = b""
            while True:
                if offset <= 0 and buf:
                    # Whole file already in buf; fall through to parse below.
                    pass
                if offset > 0:
                    read_len = min(chunk, offset)
                    offset -= read_len
                    try:
                        fh.seek(offset)
                        data = fh.read(read_len)
                    except OSError:
                        return 0
                    buf = data + buf
                # Candidates: ignore trailing incomplete (no trailing \n)
                # and leading fragment (when offset>0).
                lines = buf.split(b"\n")
                # Last element: b'' if ends with \n (complete), else incomplete.
                cands = lines[:-1]
                if offset > 0 and cands:
                    cands = cands[1:]
                for raw in reversed(cands):
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    seq = record.get("seq")
                    if isinstance(seq, int):
                        return seq
                if offset <= 0:
                    return 0
                chunk = min(chunk * 2, 65536)
                if not buf and offset <= 0:
                    return 0
    except OSError:
        return 0
    return 0


def _default_journald(line: str) -> None:
    logger_bin = shutil.which("logger")
    if not logger_bin:
        return
    try:
        subprocess.run([logger_bin, "-t", "autoos-exec"], input=line.encode("utf-8"),
                        timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _iso(dt: datetime.datetime) -> str:
    dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


_FIELD_CAP = 4096


def _cap_field(value: str | None) -> tuple[str | None, bool]:
    """Cap reason/session/cwd to 4096 chars (Qoder-13); returns (capped,
    was_truncated). Sanitizes first so control chars never reach disk."""
    if value is None:
        return None, False
    clean = sanitize_text(value)
    if len(clean) > _FIELD_CAP:
        return clean[:_FIELD_CAP], True
    return clean, False


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
                data = line.encode("utf-8")
                total = 0
                while total < len(data):
                    try:
                        n = os.write(self._fd, data[total:])
                    except OSError as exc:
                        raise AuditWriteError(
                            f"cannot write audit log under {self.state_dir}: {exc}") from exc
                    if n <= 0:
                        raise AuditWriteError(
                            f"short audit write under {self.state_dir}: "
                            f"wrote {total}/{len(data)} bytes")
                    total += n
            finally:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except AuditWriteError:
            raise
        except OSError as exc:
            raise AuditWriteError(f"cannot write audit log under {self.state_dir}: {exc}") from exc
        self._journald(line.rstrip("\n"))
        return seq

    def journald_advisory(self, text: str) -> None:
        """Best-effort journald copy when the file write failed (r2 L-12):
        an executed-but-unlogged call must still leave an advisory trace
        even when stderr is unwatched. Never raises -- the caller is already
        handling a failure."""
        try:
            self._journald(text)
        except Exception:
            pass

    def write(self, *, actor: str, session: str | None, via: str, host: str,
              argv: Sequence[str], cwd: str, run_as: str, decision: str, rule: str | None,
              reason: str | None, exit_code: int | None, duration_ms: int | None,
              out_bytes: int | None, truncated: bool) -> int:
        """Write one call's audit line. Raises AuditWriteError on failure --
        the caller must treat that as fail-closed (spec section 5)."""
        self._ensure_open()
        capped_session, sess_trunc = _cap_field(session)
        capped_cwd, cwd_trunc = _cap_field(cwd)
        capped_reason, reason_trunc = _cap_field(reason)
        truncated = bool(truncated or sess_trunc or cwd_trunc or reason_trunc)
        return self._write_locked(lambda seq: {
            "ts": _iso(self._now()),
            "seq": seq,
            "actor": actor,
            "session": capped_session,
            "via": via,
            "host": host,
            "argv": redact_argv(argv),
            "argv_sha256": hash_argv(argv),
            "cwd": capped_cwd,
            "run_as": run_as,
            "decision": decision,
            "rule": rule,
            "reason": capped_reason,
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
