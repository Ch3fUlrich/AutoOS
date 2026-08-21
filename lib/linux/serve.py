#!/usr/bin/env python3
"""AutoOS browser UI for headless machines.

Serves web/index.html and drives the real installer by shelling out to
./setup.sh, so the browser path and the terminal path cannot drift apart.

Security: binds 127.0.0.1 by default and always requires a per-run token. A
wider bind is opt-in and warned about, because this endpoint installs software.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
BIND = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
# Starting the server in dry-run LOCKS the whole session to preview-only: a safe
# way to hand someone the URL without handing them the ability to change the box.
FORCE_DRY = (sys.argv[4] if len(sys.argv) > 4 else "0") == "1"
TOKEN = secrets.token_urlsafe(24)

LOCK = threading.Lock()
LOG: list[dict] = []
RUN = {"running": False, "done": 0, "total": 0, "summary": ""}


def sh(*args: str) -> str:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout


def build_state() -> dict:
    """System info + catalog, produced by the same shell code the CLI uses."""
    probe = r"""
set -euo pipefail
cd "$AUTOOS_ROOT"
. lib/linux/ui.sh; . lib/linux/detect.sh; . lib/linux/catalog.sh
AUTOOS_NO_COLOR=1 ui_init
detect_system
catalog_load catalog/linux.json "$SYS_ARCH" "$SYS_IS_HEADLESS"
python3 - "$SYS_DISTRO_NAME" "$SYS_ARCH" "$SYS_MODEL" "$SYS_CPU_NAME" "$SYS_CPU_CORES" \
          "$SYS_RAM_GB" "$SYS_FREE_DISK_GB" "$SYS_USER" "$SYS_IS_HEADLESS" \
          "$(suggested_profile)" "$(hostname)" <<'PY'
import json, sys
k = sys.argv[1:]
print(json.dumps({
  "system": {
    "host": k[10], "distribution": k[0], "architecture": k[1], "model": k[2],
    "cpu": k[3], "cores": k[4], "memory": k[5] + " GB", "free disk": k[6] + " GB",
    "user": k[7], "display": "headless" if k[8] == "1" else "graphical",
  },
  "suggested": k[9],
}))
PY
"""
    env = dict(os.environ, AUTOOS_ROOT=str(ROOT))
    out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True,
                         env=env, cwd=ROOT, check=False)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-2000:] or "detection failed")
    info = json.loads(out.stdout.strip().splitlines()[-1])

    catalog = json.loads((ROOT / "catalog" / "linux.json").read_text(encoding="utf-8"))
    arch = info["system"]["architecture"]
    headless = info["system"]["display"] == "headless"
    components = []
    for grp in catalog.get("categories", []):
        if grp.get("requiresDisplay") and headless:
            continue
        for c in grp.get("components", []):
            if c.get("arch") and arch not in c["arch"]:
                continue
            components.append({
                "id": c["id"], "name": c["name"], "description": c["description"],
                "provider": c["provider"], "package": c["package"],
                "profiles": c.get("profiles", []), "prompt": c.get("prompt"),
                "category": grp["name"],
                "requires": c.get("requires", []), "homepage": c.get("homepage"),
                "verify": c.get("verify"), "notes": c.get("notes"),
            })
    return {
        "platform": "Linux",
        "system": info["system"],
        "suggested": info["suggested"],
        "profiles": catalog.get("profiles", {}),
        "prompts": catalog.get("prompts", {}),
        "components": components,
    }


def classify(line: str) -> str:
    t = line.strip()
    if t.startswith("+ "):
        return "ok"
    if t.startswith("! "):
        return "warn"
    if t.startswith("x "):
        return "err"
    if t.startswith("> "):
        return "step"
    if t.startswith(("run:", "would run:", "would ")):
        return "muted"
    return ""


def run_install(ids: list[str], answers: dict, dry: bool) -> None:
    env = dict(os.environ, AUTOOS_NO_COLOR="1")
    for key, val in (answers or {}).items():
        if val:
            env["AUTOOS_ANSWER_" + key.upper().replace("-", "_")] = str(val)

    cmd = ["bash", "setup.sh", "--only", ",".join(ids), "--yes", "--no-color"]
    if dry:
        cmd.append("--dry-run")

    with LOCK:
        RUN.update(running=True, done=0, total=len(ids), summary="")
        LOG.append({"level": "step", "text": "$ " + " ".join(cmd)})

    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        with LOCK:
            LOG.append({"level": classify(line), "text": line})
            if line.strip().startswith("> ["):
                RUN["done"] += 1
    proc.wait()

    with LOCK:
        RUN["running"] = False
        RUN["summary"] = "finished (exit %d)" % proc.returncode
        LOG.append({
            "level": "ok" if proc.returncode == 0 else "err",
            "text": "--- exit code %d ---" % proc.returncode,
        })


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoOS"

    def log_message(self, *_args):  # keep the terminal clean
        pass

    def _authed(self, qs) -> bool:
        return secrets.compare_digest((qs.get("token") or [""])[0], TOKEN)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            page = (ROOT / "web" / "index.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")

        if not self._authed(qs):
            return self._json(403, {"error": "bad or missing token"})

        if u.path == "/api/state":
            try:
                return self._json(200, build_state())
            except Exception as exc:  # surface the real reason to the page
                return self._json(500, {"error": str(exc)})

        if u.path == "/api/log":
            offset = int((qs.get("offset") or ["0"])[0])
            with LOCK:
                lines = LOG[offset:]
                return self._json(200, {
                    "lines": lines, "offset": offset + len(lines),
                    "running": RUN["running"], "done": RUN["done"],
                    "total": RUN["total"], "summary": RUN["summary"],
                })

        return self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._json(403, {"error": "bad or missing token"})
        if u.path != "/api/install":
            return self._json(404, {"error": "not found"})
        if RUN["running"]:
            return self._json(409, {"error": "a run is already in progress"})

        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        ids = [str(i) for i in body.get("ids", []) if i]
        if not ids:
            return self._json(400, {"error": "no components selected"})

        with LOCK:
            LOG.clear()
        dry = bool(body.get("dryRun", True)) or FORCE_DRY
        threading.Thread(
            target=run_install,
            args=(ids, body.get("answers", {}), dry),
            daemon=True,
        ).start()
        return self._json(202, {"started": True})


def main() -> int:
    # Line-buffer stdout: when this is redirected to a file or a pipe (which is
    # exactly how a wrapper reads back the URL) Python block-buffers by default
    # and the connection details never appear until the process exits.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:  # pragma: no cover - Python < 3.7
        pass

    url = f"http://{'localhost' if BIND == '127.0.0.1' else BIND}:{PORT}/?token={TOKEN}"
    print()
    print("  AutoOS browser UI")
    print("  " + "-" * 58)
    print(f"  {url}")
    print("  " + "-" * 58)
    if BIND != "127.0.0.1":
        print("  WARNING: bound to a non-loopback address. Anyone who can reach this")
        print("           port AND has the token above can install software here.")
    if FORCE_DRY:
        print("  Session is LOCKED to dry run - the browser cannot install anything.")
    print("  The token changes every run. Ctrl-C to stop.")
    print()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
