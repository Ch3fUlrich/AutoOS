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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
# the incident shape is the canary's contract; the fake transcript quotes it
# rather than copying it, so the two cannot drift
from oc_l1_canary import INCIDENT_COMMAND as CANARY_COMMAND  # noqa: E402
import oc_l1_serve  # noqa: E402

PW_ENV = "AUTOOS_OCL1_TEST_PW"
PW_VALUE = "sk-TEST-SRV-PW-001"
RECORD_ENV = "OC_L1_FAKE_RECORD"
FAKE_SESSION_ID = "ses_fake42"
# the fake lane is a guarded lane: `start` refuses an empty plugins list
# (D-665), so the default must carry the bash-guard plugin directory.
GUARD_PLUGIN = "/fake/plugins/bash-guard"
# the fake binary writes this to ITS stderr, the way the real opencode child
# writes the plugin's fail-open notes there (D-665 fix 4: nothing may swallow it)
FAKE_STDERR_NOTE = "fake-opencode: child stderr note"
HINT_EXPECTED = (
    "MCP tools are available only through the built-in execute tool "
    "(code mode): call execute with a short script that invokes the "
    "tool; if a call fails, re-read the tool list from execute instead "
    "of guessing names."
)

FAKE_PY = """import json, os, sys
rec = os.environ["%s"]
# every NAME the child was given, sorted: the launcher hands a lane an
# allowlist, and a test can only pin that if the fake reports the whole set
# (values never travel - a token in a test artifact is the leak being tested)
names = sorted(os.environ)
with open(rec, "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "env_names": names, "pid": os.getpid(),
               "guard_role": os.environ.get("AUTOOS_GUARD_ROLE"),
               "l1_inbox": os.environ.get("AUTOOS_L1_INBOX"),
               "agent_layer": os.environ.get("AUTOOS_AGENT_LAYER")}, f)
sys.stderr.write("%s\\n")
sys.stderr.flush()
import time
time.sleep(300)
""" % (RECORD_ENV, FAKE_STDERR_NOTE)


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

    def __init__(self, password, active_503=False, canary_mode="denied", port=0):
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
                            # the denial carries the command it was raised for:
                            # the canary only counts a denial OF ITS OWN probe
                            self._send(200, {"data": [
                                {
                                    "type": "assistant",
                                    "content": [
                                        {
                                            "type": "tool",
                                            "tool": "shell",
                                            "state": {
                                                "status": "error",
                                                "input": {"command": CANARY_COMMAND},
                                                "error": "bash-guard: DENIED - unquoted heredoc <<CANARY_EOF with a backtick",
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
                                                "input": {"command": CANARY_COMMAND},
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

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._httpd.server_address[1]
        self._real_probe = None
        # poll_interval=0.05: shutdown() must not wait up to 0.5 s for the
        # serve_forever loop to notice (test-suite time budget).
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.05),
            daemon=True,
        )

    def start(self):
        # The fake answers on the lane's OWN port, which is exactly what the
        # launcher's pre-spawn bind probe (fix 6) would read as a foreign holder.
        # So the fake reports its own port bindable and leaves every other port
        # to the real probe - a collision test can hold a second port with a live
        # listener and the launcher really does move off it.
        mine = self.port
        real = oc_l1_serve.port_is_free
        self._real_probe = real
        oc_l1_serve.port_is_free = lambda port, host=oc_l1_serve.HOST: (
            True if port == mine else real(port, host))
        self._thread.start()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._real_probe is not None:
            oc_l1_serve.port_is_free = self._real_probe
            self._real_probe = None


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
        # A real lane carries the bash-guard plugin; `start` refuses one that
        # does not (D-665), so the fake must look like the guarded default.
        "plugins": [GUARD_PLUGIN],
        # D-665 (AO-L2-LAUNCH fix 2): the lane child env is an allowlist, so a
        # fake binary that reads its record path out of the environment has to
        # declare that name as the lane's own extra - exactly what a real lane
        # config does for a variable the launcher's allowlist does not know.
        "child_env": [RECORD_ENV],
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
