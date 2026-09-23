"""Thin CAO REST client — only what this lane needs.

Terminals take three hops, and shortcutting them is how a sweep becomes a silent
no-op. Measured 2026-09-10 against CAO 2.5.0:

    GET /sessions                       -> session summaries. NO terminals array.
    GET /sessions/{name}/terminals      -> ids, provider, profile. NO caller_id,
                                           NO status.
    GET /terminals/{id}                 -> the full record, including caller_id
                                           (the ancestry link) and status (what
                                           the watchdog needs).

An earlier version of this module read ``session["terminals"]``, a key that never
exists. It reported zero terminals forever and printed "0 violation(s)" — a
reassuring result from a check that was looking at nothing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class CaoClient:
    def __init__(self, base_url: str = "http://localhost:9889", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str):
        with urllib.request.urlopen(
            f"{self.base_url}{path}", timeout=self.timeout
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def health(self) -> dict:
        return self._get("/health")

    def sessions(self) -> list:
        data = self._get("/sessions")
        return data if isinstance(data, list) else data.get("sessions", [])

    def session_terminals(self, session_name: str) -> list:
        quoted = urllib.parse.quote(str(session_name), safe="")
        data = self._get(f"/sessions/{quoted}/terminals")
        return data if isinstance(data, list) else data.get("terminals", [])

    def terminal(self, terminal_id: str) -> dict:
        return self._get(f"/terminals/{urllib.parse.quote(str(terminal_id), safe='')}")

    def all_terminals(self, detail: bool = True) -> list:
        """Every terminal across every session.

        ``detail`` fetches each terminal individually so ``caller_id`` and
        ``status`` are present. Without it the depth monitor has no ancestry to
        walk and the watchdog has no status to check, so both silently pass.

        A terminal that disappears between the two calls is skipped rather than
        raising: sessions are torn down constantly, and a sweep that crashes on a
        normal race is worse than one that misses a dying terminal for 30 s.
        """
        out = []
        for session in self.sessions():
            name = session.get("name") or session.get("id")
            if not name:
                continue
            for term in self.session_terminals(name):
                if not detail:
                    out.append(term)
                    continue
                tid = term.get("id")
                if not tid:
                    continue
                try:
                    out.append({**term, **self.terminal(tid)})
                except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                    continue
        return out

    def live_task_ids(self) -> set:
        """Task ids CAO reports as alive.

        The resume predicate trusts THIS over the lease ledger. Errors propagate
        rather than returning an empty set: empty reads as "nobody is working",
        which would license restarting live work.
        """
        out = set()
        for term in self.all_terminals():
            task_id = (term.get("metadata") or {}).get("task_id")
            if task_id:
                out.add(task_id)
        return out

    def terminal_output(self, terminal_id: str, lines: int = 60) -> str:
        """Recent output from one terminal — the text a blocked agent is showing.

        The watchdog needs the PROMPT, not just the status: "waiting_user_answer"
        says a decision is pending, and only the text says whether it is a
        workspace-trust dialog or a request to spend money.
        """
        quoted = urllib.parse.quote(str(terminal_id), safe="")
        data = self._get(f"/terminals/{quoted}/output?lines={int(lines)}")
        if isinstance(data, str):
            return data
        return str(data.get("output") or data.get("content") or data)

    def answer(self, terminal_id: str, text: str) -> dict:
        """Send an answer to a terminal that is waiting on one.

        A blocked agent is a held slot doing nothing and it stalls everything
        above it, so answering is an obligation of the orchestrating layer —
        see cao/watchdog.py for what may be answered automatically and what
        must go to a human.
        """
        quoted = urllib.parse.quote(str(terminal_id), safe="")
        payload = json.dumps({"input": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/terminals/{quoted}/input",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}

    def available_providers(self) -> dict:
        """Provider -> bool from /health components.

        "ok" here means the binary exists, NOT that it is authenticated or in
        quota — use cao.probe for that.
        """
        components = self.health().get("components", {})
        return {k: v == "ok" for k, v in components.items()}
