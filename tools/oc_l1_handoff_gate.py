"""Optional fail-closed handoff-card gate; no credentials in checker argv."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unicodedata

CHECK_TIMEOUT = 15


def _clean(text, password):
    # Scrub before truncation so a credential cannot leak a prefix at the boundary.
    secrets = [password] + [v for k, v in os.environ.items() if v and
                           any(s in k.upper() for s in ("PASSWORD", "SECRET", "TOKEN", "KEY", "_PW"))]
    for value in secrets:
        if value:
            text = text.replace(value, "[redacted]")
    text = "".join(c for c in text if c == "\n" or not unicodedata.category(c).startswith("C"))
    return "\n".join(line[:400] for line in text.splitlines()[:19])[:7600]


def reason(lane, password):
    """Return (card, failure); reject symlinks even when the checker would pass."""
    card = Path(lane["handoff"]).with_suffix(".card.md")
    try:
        if card.is_symlink() or not card.is_file():
            return card, "card missing, not regular, or symlinked"
        before = card.stat()
        # A disk spool bounds memory even for a noisy checker; only the preview is read.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([lane["handoff_check"], str(card), "--log"],
                                    shell=False, timeout=CHECK_TIMEOUT, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=output)
            output.seek(0)
            text = _clean(output.read(65536).decode("utf-8", "replace"), password)
        after = card.stat()
        if card.is_symlink() or (after.st_ino, after.st_size, after.st_mtime_ns) != (
                before.st_ino, before.st_size, before.st_mtime_ns):
            return card, "card changed during check"
        if result.returncode == 0:
            return card, ""
        return card, text or "checker exited %d" % result.returncode
    except subprocess.TimeoutExpired:
        return card, "checker timed out"
    except Exception as exc:
        # Exception strings may include command arguments or credentials.
        return card, "checker unavailable or failed (%s)" % type(exc).__name__


def blocked(lane, state, sid, port, password, http):
    """Persist before posting: at most one attempt per distinct failure per session."""
    card, failure = reason(lane, password)
    if not failure:
        return ""
    detail = _clean("%s\n%s" % (card, failure), password)
    fingerprint = hashlib.sha256((sid + detail).encode()).hexdigest()
    seen = list(state.get("handoff_check_seen") or [])
    fresh = fingerprint not in seen
    if fresh:
        seen.append(fingerprint)
    state.update(action="handoff_check_failed", handoff_check_failed=detail,
                 handoff_check_seen=seen)
    http._write_state(lane["state_file"], state)
    hb = lane.get("heartbeat_file") or str(Path(lane["scratch_dir"]) / "heartbeat.json")
    heartbeat = http._read_state(hb)
    heartbeat = heartbeat if isinstance(heartbeat, dict) else {}
    heartbeat.update(action="handoff_check_failed", handoff_check_failed=detail, session_id=sid)
    http._write_state(hb, heartbeat)
    if fresh:
        http._request(port, "POST", "/api/session/%s/prompt" % sid,
                      body={"text": "HANDOFF CHECK FAILED: repair this card; session retained.\n" + detail},
                      password=password)
    return detail
