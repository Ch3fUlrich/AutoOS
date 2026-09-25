#!/usr/bin/env python3
"""A fake OpenHands app API for the profile-push tests (no docker, no network).

Implements just what tools/sync-openhands-profiles.py --push-url talks to:
openapi.json (a StrictLLM schema), /api/v1/settings, the profile list
/api/v1/settings/profiles and /api/v1/settings/profiles/{name} (GET, POST,
DELETE), with the real app's behaviour measured on 2026-09-24: extra LLM
keys are a 422, settings start as 404, at most CAP profiles (409 beyond).
The list shape {"profiles": [{name, model, base_url, api_key_set}],
"active_profile": ...} was read off a live app on 2026-09-25; the DELETE
reply {"name", "message"} follows that app's openapi.json
(ProfileMutationResponse) - no live profile was deleted to measure it.
Every request is appended to the log file as "METHOD PATH" so a test can
assert what was (not) sent.

    python3 fake_openhands_app.py <portfile> <logfile> [<seed.json>]

seed.json (optional) pre-loads the app: {"cap": 3, "settings": {...},
"profiles": {"<name>": {<llm>}}, "active": "<name>"}. Without it the app
starts empty with CAP 3.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAP = 3
ALLOWED = {"model", "api_key", "base_url", "max_input_tokens", "max_output_tokens"}
STATE = {"settings": None, "profiles": {}, "active": None}
LOG = sys.argv[2]
LIST = "/api/v1/settings/profiles"
ONE = LIST + "/"

if len(sys.argv) > 3:
    with open(sys.argv[3], encoding="utf-8") as _fh:
        _seed = json.load(_fh)
    CAP = _seed.get("cap", CAP)
    STATE["settings"] = _seed.get("settings")
    STATE["profiles"] = dict(_seed.get("profiles") or {})
    STATE["active"] = _seed.get("active")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _log(self):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (self.command, self.path))

    def do_GET(self):
        self._log()
        if self.path == "/openapi.json":
            return self._send(200, {"components": {"schemas": {"StrictLLM": {
                "properties": {k: {} for k in ALLOWED}}}}})
        if self.path == "/api/v1/settings":
            if STATE["settings"] is None:
                return self._send(404, {"error": "Settings not found"})
            return self._send(200, STATE["settings"])
        if self.path == LIST:
            return self._send(200, {
                "profiles": [{"name": n, "model": p.get("model"), "base_url": p.get("base_url"),
                              "api_key_set": bool(p.get("api_key"))}
                             for n, p in STATE["profiles"].items()],
                "active_profile": STATE["active"]})
        if self.path.startswith(ONE):
            name = self.path.rsplit("/", 1)[1]
            prof = STATE["profiles"].get(name)
            if prof is None:
                return self._send(404, {"detail": "not found"})
            cfg = dict(prof)
            key_set = bool(cfg.get("api_key"))
            cfg["api_key"] = None
            return self._send(200, {"name": name, "config": cfg, "api_key_set": key_set})
        return self._send(404, {})

    def do_POST(self):
        self._log()
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/v1/settings":
            if "agent_settings_diff" not in body:
                return self._send(422, {"error": "Use *_diff nested settings payloads"})
            STATE["settings"] = body
            return self._send(200, {"message": "Settings stored"})
        if self.path.startswith(ONE):
            if STATE["settings"] is None:
                return self._send(404, {"detail": "Settings not found"})
            name = self.path.rsplit("/", 1)[1]
            llm = body.get("llm") or {}
            extra = sorted(set(llm) - ALLOWED)
            if extra:
                return self._send(422, {"detail": "extra_forbidden %s" % extra})
            if name not in STATE["profiles"] and len(STATE["profiles"]) >= CAP:
                return self._send(409, {"detail": "cap"})
            STATE["profiles"][name] = llm
            return self._send(201, {"name": name})
        return self._send(404, {})

    def do_DELETE(self):
        self._log()
        if self.path.startswith(ONE):
            name = self.path.rsplit("/", 1)[1]
            if STATE["profiles"].pop(name, None) is None:
                return self._send(404, {"detail": "not found"})
            if STATE["active"] == name:
                STATE["active"] = None
            return self._send(200, {"name": name, "message": "Profile deleted"})
        return self._send(404, {})


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(str(httpd.server_address[1]))
httpd.serve_forever()
