"""_oc_l1_fakes.py - shared fake HTTP server and fake binary helpers for oc_l1 tests."""

import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PW_ENV = "AUTOOS_OCL1_TEST_PW"
PW_VALUE = "sk-TEST-SRV-PW-001"
RECORD_ENV = "OC_L1_FAKE_RECORD"
FAKE_SESSION_ID = "ses_fake42"
HINT_EXPECTED = (
    "MCP tools are available only through the built-in execute tool "
    "(code mode): call execute with a short script that invokes the "
    "tool; if a call fails, re-read the tool list from execute instead "
    "of guessing names."
)

FAKE_PY = """import json, os, sys
rec = os.environ["%s"]
names = [k for k in ("OPENCODE_CONFIG", "OPENCODE_SERVER_PASSWORD",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "XDG_CACHE_HOME") if k in os.environ]
with open(rec, "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "env_names": names, "pid": os.getpid()}, f)
import time
time.sleep(300)
""" % RECORD_ENV


def make_fake_bin(td, fake_py, python_exe):
    """opencode_bin stand-in: platform wrapper that execs the fake script."""
    d = td / "fakebin"
    d.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        p = d / "fake_opencode.bat"
        p.write_text(
            '@echo off\r\n"%s" "%s" %%*\r\n' % (python_exe, fake_py),
            encoding="ascii",
        )
    else:
        p = d / "fake_opencode.sh"
        p.write_text(
            '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (python_exe, fake_py),
            encoding="utf-8",
        )
        os.chmod(p, 0o755)
    return str(p)


class FakeServer:
    """A thread-bound fake of the opencode v2 API on 127.0.0.1:<port>."""

    def __init__(self, password, active_503=False, canary_mode="denied"):
        self.password = password
        self.active_503 = active_503
        self.canary_mode = canary_mode
        self.canary_session_id = "ses_canary99"
        self.canary_items = None
        self.session_outcome = "succeeded"
        self.items = []
        self.requests = []  # {method, path, auth_ok, body}
        self._lock = threading.Lock()
        self._stopped = False
        self._httpd = None
        self._thread = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                expected = "Basic " + base64.b64encode(
                    ("opencode:%s" % outer.password).encode("utf-8")
                ).decode("ascii")
                return self.headers.get("Authorization") == expected

            def _handle(self, method):
                path = self.path.split("?", 1)[0]
                ok = self._auth_ok()
                body = None
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(n) if n else b""
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        body = None
                with outer._lock:
                    outer.requests.append(
                        {"method": method, "path": path,
                         "auth_ok": ok, "body": body}
                    )
                if not ok:
                    self._send(401, {"error": "unauthorized"})
                    return
                if method == "GET" and path == "/api/session/active":
                    if outer.active_503:
                        self._send(503, {"error": "not ready"})
                    else:
                        self._send(200, {"data": {}})
                elif method == "POST" and path == "/api/session":
                    if body and isinstance(body, dict) and body.get("title", "").endswith("-canary"):
                        self._send(200, {"data": {"id": outer.canary_session_id}})
                    else:
                        self._send(200, {"data": {"id": FAKE_SESSION_ID}})
                elif method == "POST" and path.endswith("/prompt"):
                    self._send(200, {"data": {"id": "inbox_fake"}})
                elif method == "GET" and path.endswith("/message"):
                    if outer.canary_session_id in path:
                        if outer.canary_items is not None:
                            self._send(200, {"data": outer.canary_items})
                        elif outer.canary_mode == "denied":
                            self._send(200, {"data": [
                                {
                                    "type": "assistant",
                                    "content": [
                                        {
                                            "type": "tool",
                                            "tool": "shell",
                                            "state": {
                                                "status": "error",
                                                "error": "bash-guard: DENIED: unquoted heredoc command substitution not permitted",
                                            },
                                        }
                                    ],
                                }
                            ]})
                        elif outer.canary_mode == "allowed":
                            self._send(200, {"data": [
                                {
                                    "type": "assistant",
                                    "content": [
                                        {
                                            "type": "tool",
                                            "tool": "shell",
                                            "state": {
                                                "status": "completed",
                                                "output": "canary Sun Oct 04 2026",
                                            },
                                        }
                                    ],
                                }
                            ]})
                        elif outer.canary_mode == "no_tool":
                            self._send(200, {"data": [
                                {
                                    "type": "assistant",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Refusing to run arbitrary commands.",
                                        }
                                    ],
                                }
                            ]})
                        else:
                            self._send(200, {"data": outer.items})
                    else:
                        self._send(200, {"data": outer.items})
                elif method == "GET" and path.startswith("/api/session/"):
                    sid = path.rsplit("/", 1)[-1]
                    now = int(time.time())
                    if sid == outer.canary_session_id:
                        if outer.canary_mode == "timeout":
                            self._send(200, {"data": {
                                "id": sid,
                                "time": {"created": now, "updated": now},
                            }})
                        else:
                            self._send(200, {"data": {
                                "id": sid,
                                "outcome": "succeeded",
                                "time": {"created": now, "updated": now},
                            }})
                    else:
                        self._send(200, {"data": {
                            "id": sid,
                            "outcome": outer.session_outcome,
                            "time": {"created": now, "updated": now},
                        }})
                else:
                    self._send(404, {"error": "not found"})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        # poll_interval=0.05: shutdown() must not wait up to 0.5 s for the
        # serve_forever loop to notice (test-suite time budget).
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.05),
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._httpd.shutdown()
        self._httpd.server_close()


def make_lane(td, port):
    proj = td / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    handoff = proj / "handoff.md"
    handoff.write_text(
        "\n".join("line-%02d" % i for i in range(1, 46)) + "\n",
        encoding="utf-8",
    )
    return {
        "name": "l1test",
        "cwd": str(proj),
        "handoff": str(handoff),
        "opencode_bin": make_fake_bin(td, td / "fake_opencode.py",
                                      sys.executable),
        "password_env": PW_ENV,
        "model": {
            "provider": "omniroute",
            "key": "placeholder-direct-model",
            "modelID": "placeholderprovider/PlaceholderModel",
        },
        "mcp": ["autoos-agent"],
        "serve_port": port,  # the fake server's port (deliberate, tests only)
        "instructions": ["AGENTS.md", str(handoff)],
        "plugins": [],
        "scratch_dir": str(td / "scratch"),
        "state_file": str(td / "state" / "l1test.state.json"),
        "heartbeat_file": str(td / "scratch" / "heartbeat.json"),
        "canary_timeout_s": 2,
        "health_timeout_s": 2,
        "silent_minutes": 10,
    }


def write_cfg(td, lane):
    (td / "fake_opencode.py").write_text(FAKE_PY, encoding="utf-8")
    p = td / "config.json"
    p.write_text(json.dumps({"l1test": lane}), encoding="utf-8")
    return p


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return ',"%d",' % pid in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # kill(0) is also true for a ZOMBIE (killed, not yet reaped by its parent: our own un-reaped child):
    # a zombie is dead for these tests. Reap it when it is our child, else read /proc where it exists.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return True


def wait_file(path, seconds=5):
    deadline = time.time() + seconds
    while not Path(path).is_file() and time.time() < deadline:
        time.sleep(0.1)
