"""Task ownership, so auto-resume never duplicates live work.

The constraint this exists to satisfy: resume work that stopped, but **only if
no other agent already continued it**.

Four conditions must all hold before a task may be restarted (spec 6.3). The
load-bearing one is ``live_task_ids``, which comes from CAO's ``/sessions``: a
ledger can be stale after a crash, but the server knows which terminals are
actually alive. Trusting the file alone is how you get two agents editing the
same worktree.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Only these states describe work that stopped without finishing.
RESUMABLE = ("running", "quota_stalled")


def _lock_path(dir_, task_id) -> Path:
    return Path(dir_) / f"{task_id}.lease"


def claim(dir_, task_id: str, owner: str, lease_minutes: int = 30, attempt: int = 1) -> bool:
    """Atomically take ownership. Returns False if someone already holds it.

    ``O_CREAT | O_EXCL`` is atomic on POSIX and on NTFS, so two agents racing
    for the same task cannot both win — which a read-then-write check would allow.

    The record written here is the one :func:`may_resume` reads. An earlier
    version wrote only ``task_id``/``owner``/``claimed_at`` while ``may_resume``
    looked for ``state``, ``lease_expires`` and ``attempt`` — so every lease read
    as unresumable and the resume path could never have worked. Nothing surfaced
    it because nothing called ``may_resume``.
    """
    path = _lock_path(dir_, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    now = datetime.now(timezone.utc)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "task_id": task_id,
                "owner": owner,
                "state": "running",
                "claimed_at": now.isoformat(),
                "lease_expires": (now + timedelta(minutes=lease_minutes)).isoformat(),
                "continued_by": None,
                "attempt": int(attempt),
            },
            fh,
        )
    return True


def all_leases(dir_) -> list:
    """Every lease record in ``dir_``. A corrupt file is skipped, not fatal."""
    root = Path(dir_)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.lease")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def release(dir_, task_id: str) -> None:
    _lock_path(dir_, task_id).unlink(missing_ok=True)


def state(dir_, task_id: str):
    path = _lock_path(dir_, task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def may_resume(rec: dict, live_task_ids, max_attempts: int, now=None) -> bool:
    if rec.get("state") not in RESUMABLE:
        return False
    if rec.get("continued_by"):
        return False
    if rec.get("task_id") in set(live_task_ids or ()):
        return False  # CAO reports someone alive on it — believe the server
    if int(rec.get("attempt", 1)) >= max_attempts:
        return False  # a loop that never converges is worse than a stall
    expires = datetime.fromisoformat(rec["lease_expires"])
    return (now or datetime.now(timezone.utc)) >= expires
