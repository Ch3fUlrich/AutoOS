import json
import os
import tempfile
import time
import re
import contextlib
from pathlib import Path

# Constants
CLAIM_ATTEMPTS = 8
CLAIM_BACKOFF_MS = (25, 400)
POOL_MAX_N = 6

def n_clamp(requested, log=None):
    """
    Clamp the requested number of workers to the pool maximum.
    *requested* must be an int >= 1 (bool is rejected).
    If clamped, optionally call *log* with a message.
    """
    if not isinstance(requested, int) or isinstance(requested, bool):
        raise ValueError("requested must be an int")
    if requested < 1:
        raise ValueError("requested must be >= 1")
    used = requested if requested <= POOL_MAX_N else POOL_MAX_N
    if used != requested and log is not None:
        log(f"clamp requested={requested} used={used}")
    return used

def dead_slot(pid_alive, filetime_matches, heartbeat_age_s, T=120):
    """
    Return True when a slot is considered dead:
    - either the PID is gone or the filetime mismatches,
    AND the heartbeat age exceeds the threshold T (seconds).
    """
    return (not pid_alive or not filetime_matches) and heartbeat_age_s > T

def strip_env(env):
    """
    Return a shallow copy of *env* without the key
    'AUTOOS_ADMISSION_OFF' in any letter case (the NT environment is
    case-insensitive). Original dict is unchanged.
    """
    return {k: v for k, v in env.items()
            if not (isinstance(k, str) and k.upper() == 'AUTOOS_ADMISSION_OFF')}

def may_claim(free_mb, floor_mb):
    """
    Determine whether a claim can be made based on free memory.
    None for free_mb means insufficient information → False.
    """
    if free_mb is None:
        return False
    return free_mb >= floor_mb

def mem_available_mb(os_name=os.name, meminfo_path='/proc/meminfo', nt_reader=None):
    """
    Platform‑specific memory availability:
    - On Windows (os_name == 'nt') use *nt_reader* if supplied,
      otherwise query via ctypes GlobalMemoryStatusEx.
    - On POSIX read *meminfo_path* and parse the MemAvailable line.
    Return the available megabytes as int, or None on failure.
    """
    try:
        if os_name == 'nt':
            # Windows path
            if nt_reader is not None:
                return int(nt_reader())
            # Default reader using ctypes
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullExtendedVirtual', ctypes.c_ulonglong),
                ]

            mem_status = MEMORYSTATUSEX()
            mem_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_status)):
                return None
            return int(mem_status.ullAvailPhys // (1024 * 1024))
        else:
            # POSIX / Linux path
            path = Path(meminfo_path)
            if not path.is_file():
                return None
            with path.open('r', encoding='utf-8') as f:
                for line in f:
                    m = re.match(r'^MemAvailable:\s+(\d+)\s+kB', line)
                    if m:
                        kb = int(m.group(1))
                        return kb // 1024
            return None
    except Exception:
        return None

def _is_sharing(e):
    """Return True if the OSError represents a sharing violation."""
    return isinstance(e, PermissionError) or getattr(e, "winerror", None) == 32

def _retry(op, attempts, sleep):
    """Run *op* up to *attempts* times, retrying on sharing violations.

    Returns True on success, False if the final attempt fails with a sharing
    violation.  Propagates any other exception.  Sleeps between attempts using
    exponential back‑off defined by CLAIM_BACKOFF_MS.
    """
    for i in range(attempts):
        try:
            op()
            return True
        except OSError as e:
            # Propagate non‑sharing errors immediately.
            if isinstance(e, FileNotFoundError):
                raise
            if _is_sharing(e):
                # Last attempt – give up.
                if i == attempts - 1:
                    return False
                backoff = min(CLAIM_BACKOFF_MS[0] * (2 ** i), CLAIM_BACKOFF_MS[1]) / 1000.0
                sleep(backoff)
                continue
            raise
    return False  # Should never reach here.

def claim(queue_dir, slot_dir, rename=os.rename, sleep=time.sleep):
    """Atomically claim the oldest *.json file from *queue_dir* into *slot_dir*.

    Returns the destination Path on success, or None if the claim fails.
    """
    # Gather candidates sorted by mtime then name, ignoring files that disappear.
    queue_dir, slot_dir = Path(queue_dir), Path(slot_dir)
    candidates = []
    for p in queue_dir.glob("*.json"):
        try:
            st = p.stat()
            candidates.append((st.st_mtime, p.name, p))
        except FileNotFoundError:
            continue
    candidates.sort(key=lambda x: (x[0], x[1]))

    for _, _, src in candidates:
        dst = slot_dir / src.name

        def op():
            rename(src, dst)

        try:
            succeeded = _retry(op, CLAIM_ATTEMPTS, sleep)
        except FileNotFoundError:
            # Source vanished between glob and rename – try next candidate.
            continue

        if succeeded:
            return dst
        # Sharing violation persisted → give up without trying other candidates.
        return None
    return None

def write_json_atomic(path, obj, replace=os.replace, sleep=time.sleep):
    """Write *obj* as JSON to *path* atomically.

    Returns True on success, False if the final replace fails due to a sharing
    violation after all retries.  Other exceptions propagate.
    """
    path = Path(path)
    dir_path = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(dir_path))
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(obj, f)

        def op():
            replace(tmp_path, path)

        if _retry(op, CLAIM_ATTEMPTS, sleep):
            return True
        # Replacement failed after retries – clean up temporary file.
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return False
    except Exception:
        # Ensure temp file is removed on any unexpected error.
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise

def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to a temporary file then atomically replace the target."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

def _read_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return 0

@contextlib.contextmanager
def locked(path: Path):
    """
    Context manager that opens ``path`` for appending binary and acquires an
    exclusive lock (fcntl on POSIX, msvcrt on Windows). The lock is released
    and the file closed on exit.
    """
    # Lazy imports to avoid platform‑specific errors on import time
    if os.name == "posix":
        import fcntl
    else:
        import msvcrt

    # Ensure the lock file exists
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+b")
    try:
        if os.name == "posix":
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        else:  # nt
            f.seek(0)
            # lock the whole file (1 byte is enough for our purpose)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield f
    finally:
        try:
            if os.name == "posix":
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            f.close()

def sample_decision(pool_dir, submitter, rc, risk, diff_empty,
                    tests_skipped, sample_every=4):
    """
    Decide whether a submission should be sampled for deep analysis.
    Returns dict with keys ``deep`` (bool) and ``reasons`` (list of strings).

    Reasons are added in the following order:
        sample, rc, risk, first3, empty-diff, tests-skipped
    """
    # Validate submitter name before touching the filesystem
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", submitter):
        raise ValueError(f"Invalid submitter name: {submitter!r}")

    pool_path = Path(pool_dir)
    pool_path.mkdir(parents=True, exist_ok=True)

    submitters_dir = pool_path / "submitters"
    submitters_dir.mkdir(parents=True, exist_ok=True)

    lock_path = pool_path / "counters.lock"

    # Update counters atomically under the lock
    with locked(lock_path):
        global_counter_path = pool_path / "sample.count"
        submit_counter_path = submitters_dir / f"{submitter}.count"

        global_cnt = _read_int(global_counter_path) + 1
        submit_cnt = _read_int(submit_counter_path) + 1

        _atomic_write_text(global_counter_path, str(global_cnt))
        _atomic_write_text(submit_counter_path, str(submit_cnt))

    reasons = []

    # sample reason (global counter based)
    if sample_every > 0 and global_cnt % sample_every == 0:
        reasons.append("sample")

    # rc reason
    if rc != 0:
        reasons.append("rc")

    # risk reason
    if risk in {"R2", "R3", "R4"}:
        reasons.append("risk")

    # first3 reason (per‑submitter)
    if submit_cnt <= 3:
        reasons.append("first3")

    # empty diff reason
    if diff_empty:
        reasons.append("empty-diff")

    # tests‑skipped reason
    if tests_skipped:
        reasons.append("tests-skipped")

    return {"deep": bool(reasons), "reasons": reasons}

def requeue_dead(job_path, queue_dir, failed_dir):
    """
    Move ``job_path`` to ``queue_dir`` on the first attempt, creating a
    ``<job>.requeued`` marker. On a subsequent call (marker already present),
    move the job to ``failed_dir`` and return ``'failed'``.
    """
    job_path = Path(job_path)
    queue_dir = Path(queue_dir)
    failed_dir = Path(failed_dir)

    marker = queue_dir / f"{job_path.name}.requeued"

    if marker.is_file():
        # Already requeued once → move to failed
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / job_path.name
        os.replace(job_path, dest)
        return "failed"
    else:
        # First requeue
        queue_dir.mkdir(parents=True, exist_ok=True)
        dest = queue_dir / job_path.name
        os.replace(job_path, dest)
        marker.write_text("requeued", encoding="utf-8")
        return "requeued"
