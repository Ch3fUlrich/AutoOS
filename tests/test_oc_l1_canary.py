"""tests/test_oc_l1_canary.py - step B1 tests for the L1 canary probe.

unittest, stdlib only, NO real opencode: the shared FakeServer (v2 API on
127.0.0.1, Basic auth) is pointed at via the lane's serve_port, and the
lane's opencode_bin is the recording fake script that sleeps. The fake's
canary_mode selects the shape of the canary session's turn: "denied"
(shell tool entry, state.status error, bash-guard text), "allowed"
(status completed), "no_tool" (assistant text only) or "timeout" (the
canary session never gets an outcome).

Covered:
  1. denied  -> start exits 0, state/heartbeat carry canary.denied true,
     the canary session is created exactly once, its prompt carries the
     unquoted-heredoc incident command
  2. allowed -> exit 5, UNATTENDED-REFUSED printed, server stays up
  3. no tool call at all -> exit 5 (inconclusive = not denied)
  4. timeout (canary_timeout_s = 2) -> exit 5 within a few seconds
  5. idempotent second start: no second canary session, stored line
  6. relaunch (dead server, stale state): NEW canary session, heartbeat
     ts newer
  7. heartbeat merge keeps the pilot's `turn`, resets state and
     current_step
  8. the password value appears in no argv, state file, heartbeat.json,
     stdout or the child's stderr log
  9. F1: a denial counts only on a name the bash-guard hook inspects. The
     canary's accepted set is pinned equal to the plugin's (`shell`, `bash`);
     `execute` is code-mode's executor, unguarded, so its "denial" must NOT
     pass. A session with no guarded call reports the tool names and assistant
     text it did see
  11. F2: an item with no tool name is never a guarded call, whatever its state
  12. F3: a denial is an error that STARTS with the marker, on a guarded tool
      call in error state — echoed marker text never passes
  10. D-665 fix 5: the prompt demands one tool call and names the shell tool,
      and a prose-only reply reads as "inconclusive: text-only answer" —
      never as the guard allowing the command (same exit code, other detail)
"""

import contextlib
import ctypes
import io
import json
import os
import re
import sys
import tempfile
import shutil
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1_serve  # noqa: E402
import oc_l1_canary  # noqa: E402
import oc_l1_http  # noqa: E402
from oc_l1_canary import (  # noqa: E402
    DENIED_MARKER,
    GUARDED_TOOL_NAMES,
    INCONCLUSIVE_TEXT_ONLY,
)
from _oc_l1_fakes import (  # noqa: E402
    GUARD_PLUGIN,
    PW_ENV,
    PW_VALUE,
    RECORD_ENV,
    FakeServer,
    make_lane,
    wait_file,
    write_cfg,
)

CANARY_SESSION_ID = "ses_canary99"
PLUGIN_SRC = (ROOT / "configuration" / "opencode" / "plugins"
              / "bash-guard" / "index.mjs")
# the incident shape: an UNQUOTED heredoc whose body contains a
# backticked (harmless) command.
INCIDENT = "cat <<CANARY_EOF\ncanary `date`\nCANARY_EOF"


def plugin_guarded_tool_names():
    """The tool names the bash-guard hook actually inspects."""
    src = PLUGIN_SRC.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'\be\.tool\s*!==\s*"([A-Za-z_]+)"', src)))


def denied_items(tool_name, command=INCIDENT):
    """A canary transcript: one shell-tool item denied by the guard.

    The denied call carries its OWN command (opencode records the tool
    arguments under `state.input`, the same shape the plugin is handed:
    `{"tool": "shell", "input": {"command": ...}}`), because a denial is only
    evidence about the command it was raised for.
    """
    state = {"status": "error",
             "error": "bash-guard: DENIED - unquoted heredoc <<CANARY_EOF with a backtick"}
    if command is not None:
        state["input"] = {"command": command}
    return [{
        "type": "assistant",
        "content": [
            {"type": "tool", "tool": tool_name, "state": state}
        ],
    }]


def _terminate_pid(pid):
    """Best-effort fast kill on Windows (ctypes, no taskkill round-trip)."""
    if os.name != "nt":
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass
        return
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


def _pid_alive(pid):
    """Fast liveness check (tasklist takes ~2 s on this Windows host)."""
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0400, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _canary_posts(srv):
    """POST /api/session requests that created a canary session."""
    return [r for r in srv.requests
            if r["method"] == "POST" and r["path"] == "/api/session"
            and isinstance(r["body"], dict)
            and r["body"].get("title", "").endswith("-canary")]


def _ts_is_utc_iso(ts):
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return False
    return True


def _run_start(lane, cfg):
    args = SimpleNamespace(name=lane["name"], config=str(cfg))
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = oc_l1_serve.cmd_start(lane, args)
    return rc, out.getvalue()


class _Base(unittest.TestCase):
    """One fake server + one lane per test; class attr selects the mode."""

    mode = "denied"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_canary_test_")
        self.td = Path(self._td.name)
        self.srv = FakeServer(PW_VALUE, canary_mode=self.mode)
        self.srv.start()
        self.lane = make_lane(self.td, self.srv.port)
        self.cfg = write_cfg(self.td, self.lane)
        self.rec = self.td / "bin_record.json"
        self._pids = []
        self._envs = {}
        for k in (PW_ENV, RECORD_ENV):
            self._envs[k] = os.environ.get(k)
            os.environ[k] = PW_VALUE if k == PW_ENV else str(self.rec)

    def tearDown(self):
        pids = list(self._pids)
        if self.rec.is_file():
            try:
                pids.append(json.loads(
                    self.rec.read_text(encoding="utf-8")).get("pid"))
            except ValueError:
                pass
        for pid in pids:
            if pid:
                _terminate_pid(pid)
        self.srv.stop()
        for k, v in self._envs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # on Windows the just-terminated fake binary may still hold its working directory for a moment
        for _ in range(20):
            try:
                self._td.cleanup()
                break
            except OSError:
                time.sleep(0.25)
        else:
            shutil.rmtree(self.td, ignore_errors=True)

    def _start(self):
        """cmd_start with captured output; track children that stay up."""
        rc, out = _run_start(self.lane, self.cfg)
        if rc in (0, 5):  # refused keeps the server running on purpose
            st = oc_l1_serve._read_state(self.lane["state_file"])
            if isinstance(st, dict) and st.get("pid"):
                self._pids.append(st["pid"])
        return rc, out

    def _state(self):
        return json.loads(
            Path(self.lane["state_file"]).read_text(encoding="utf-8"))

    def _heartbeat(self):
        p = Path(self.lane["heartbeat_file"])
        self.assertTrue(p.is_file(), "heartbeat.json missing")
        return json.loads(p.read_text(encoding="utf-8"))


class TestDenied(_Base):
    # (1)
    def test_denied_start_ok_state_heartbeat_prompt(self):
        rc, out = self._start()
        self.assertEqual(rc, 0)
        self.assertIn("canary denied=yes", out)
        self.assertNotIn("UNATTENDED-REFUSED", out)
        st = self._state()
        self.assertTrue(st["canary"]["denied"])
        self.assertEqual(st["canary"]["session_id"], CANARY_SESSION_ID)
        posts = _canary_posts(self.srv)
        self.assertEqual(len(posts), 1, "canary session created once")
        body = posts[0]["body"]
        self.assertEqual(body["title"], "l1test-canary")
        self.assertEqual(body["location"], {"directory": self.lane["cwd"]})
        self.assertEqual(body["model"], {
            "providerID": "omniroute",
            "id": "placeholderprovider/PlaceholderModel",
        })
        prompts = [r for r in self.srv.requests
                   if r["method"] == "POST"
                   and CANARY_SESSION_ID in r["path"]
                   and r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 1)
        self.assertIn(INCIDENT, prompts[0]["body"]["text"])
        hb = self._heartbeat()
        self.assertTrue(hb["canary"]["denied"])
        self.assertEqual(
            hb["canary"]["plugin_path"],
            GUARD_PLUGIN)
        self.assertTrue(_ts_is_utc_iso(hb["canary"]["ts"]),
                        "ts %r is not UTC ISO" % hb["canary"]["ts"])
        self.assertEqual(hb["state"], "started")
        self.assertEqual(hb["current_step"], "canary")
        self.assertEqual(hb["turn"], 0)


class TestAllowed(_Base):
    mode = "allowed"

    # (2)
    def test_allowed_refused_exit5_server_stays_up(self):
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("UNATTENDED-REFUSED", out)
        self.assertIn("canary denied=no", out)
        self.assertNotIn("Traceback", out)
        st = self._state()
        self.assertFalse(st["canary"]["denied"])
        hb = self._heartbeat()
        self.assertFalse(hb["canary"]["denied"])
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertTrue(_pid_alive(rec["pid"]),
                        "the server child must stay up when refused")


class TestNoTool(_Base):
    mode = "no_tool"

    # (3)
    def test_no_tool_call_refused_exit5(self):
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("UNATTENDED-REFUSED", out)
        self.assertFalse(self._state()["canary"]["denied"])

    # (3b) D-665 fix 5: a model that only talked is NOT a guard that allowed.
    #      Same exit code, different detail, so the next report can tell them
    #      apart without re-running anything.
    def test_text_only_answer_detail_is_inconclusive_not_allowed(self):
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        detail = self._state()["canary"]["detail"]
        self.assertTrue(detail.startswith(INCONCLUSIVE_TEXT_ONLY),
                        "detail %r must start %r" % (detail, INCONCLUSIVE_TEXT_ONLY))
        self.assertNotIn("tool completed without denial", detail)

    # (3c) the watcher records only the launcher's last stdout line, so the
    #      reason must travel on the printed line as well as in the files.
    def test_refusal_reason_is_on_the_printed_line(self):
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn(INCONCLUSIVE_TEXT_ONLY, out)

    # (3d) an empty session is a different finding from a prose-only one
    def test_empty_session_detail_is_not_the_text_only_one(self):
        self.srv.canary_items = []
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        detail = self._state()["canary"]["detail"]
        self.assertFalse(detail.startswith(INCONCLUSIVE_TEXT_ONLY), detail)
        self.assertIn("no shell tool call in canary session", detail)
        self.assertIn("tools seen: none", detail)


class TestCanaryPrompt(_Base):
    """D-665 fix 5: the prompt must ask for a tool call by name, not prose."""

    def _prompt_text(self):
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        prompts = [r for r in self.srv.requests
                   if r["method"] == "POST"
                   and CANARY_SESSION_ID in r["path"]
                   and r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 1)
        return prompts[0]["body"]["text"]

    def test_prompt_names_the_shell_tool_and_one_call(self):
        text = self._prompt_text()
        self.assertIn(INCIDENT, text)
        self.assertIn("shell tool", text,
                      "the prompt must name the tool, not just 'a command'")
        self.assertRegex(text.lower(), r"\b(exactly one|only one)\b")

    def test_prompt_tool_name_is_a_name_the_canary_accepts(self):
        # the prompt and the transcript parser must not drift apart: if the
        # prompt asks for a tool the canary would not recognise, every run of
        # that model reads as "no shell tool call".
        text = self._prompt_text().lower()
        self.assertTrue(any(name in text for name in GUARDED_TOOL_NAMES),
                        "prompt asks for no accepted tool name: %r" % text)


class TestToolNames(_Base):
    """F1: only the names the guard hooks are accepted, and a miss says why."""

    def test_tool_named_bash_denied(self):
        self.srv.canary_items = denied_items("bash")
        rc, out = self._start()
        self.assertEqual(rc, 0)
        self.assertIn("canary denied=yes", out)
        self.assertTrue(self._state()["canary"]["denied"])

    # (F1) `execute` is code-mode's executor, NOT a name the bash-guard hook
    #      inspects: a denial carried by it would be a model echoing the marker,
    #      so accepting it lets an unguarded tool false-pass the canary.
    def test_tool_named_execute_is_not_guarded(self):
        self.srv.canary_items = denied_items("execute")
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("canary denied=no", out)
        self.assertFalse(self._state()["canary"]["denied"])
        self.assertIn("execute", self._state()["canary"]["detail"],
                      "the missed name must be reported")

    def test_no_shell_call_detail_names_tools_and_text(self):
        self.srv.canary_items = [{
            "type": "assistant",
            "content": [
                {"type": "text",
                 "text": "I am not going to run that, it looks like a probe."},
                {"type": "tool", "tool": "read",
                 "state": {"status": "completed", "output": "AGENTS.md"}},
            ],
        }]
        rc, out = self._start()
        self.assertEqual(rc, 5)
        detail = self._state()["canary"]["detail"]
        self.assertIn("read", detail, "detail must name the tools seen")
        self.assertIn("not going to run that", detail,
                      "detail must quote the assistant text")
        hb = self._heartbeat()
        self.assertIn("read", hb["canary"]["detail"])


class TestGuardedToolNamesPin(unittest.TestCase):
    """F1: the canary's accepted set and the plugin's guarded set are one list.

    The plugin is JS and the canary is python, so neither imports the other —
    this test is the enforcement: the two sides may never drift, in either
    direction.
    """

    def test_canary_accepts_exactly_the_names_the_guard_guards(self):
        self.assertEqual(sorted(GUARDED_TOOL_NAMES),
                         plugin_guarded_tool_names(),
                         "canary tool set != bash-guard hook set")

    def test_execute_is_not_in_the_guarded_set(self):
        self.assertNotIn("execute", GUARDED_TOOL_NAMES)

    def test_the_plugin_pattern_extracts_the_hook_names(self):
        # the pin must not read an empty set and pass vacuously
        self.assertEqual(plugin_guarded_tool_names(), ["bash", "shell"])


class TestOneToolNameList(unittest.TestCase):
    """F1: the canary holds exactly one accepted-tool-name list."""

    def test_the_parser_uses_the_shared_list_and_no_aliases(self):
        src = (ROOT / "tools" / "oc_l1_canary.py").read_text(encoding="utf-8")
        self.assertNotIn("SHELL_TOOL_NAMES", src)
        self.assertEqual(src.count("GUARDED_TOOL_NAMES ="), 1)
        self.assertIn("tool_name not in GUARDED_TOOL_NAMES", src)


class TestUnnamedGuardedCall(_Base):
    """F2: a guarded call is an item that NAMES its tool.

    `"status" in state` alone read any state-bearing item as a shell call, so a
    transcript with no tool name at all could reach either terminal verdict.
    """

    def _only(self, item):
        return [{"type": "assistant", "content": [item]}]

    def test_unnamed_error_item_is_not_a_shell_call(self):
        self.srv.canary_items = self._only({
            "type": "tool",
            "state": {"status": "error",
                      "error": "bash-guard: DENIED - unquoted heredoc"},
        })
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("canary denied=no", out)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_unnamed_completed_item_is_not_read_as_allowed(self):
        self.srv.canary_items = self._only({
            "type": "tool",
            "state": {"status": "completed", "output": "canary"},
        })
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        detail = self._state()["canary"]["detail"]
        self.assertIn("no shell tool call in canary session", detail)
        self.assertNotIn("tool completed without denial", detail)

    def test_message_level_tool_item_without_a_name_is_not_a_shell_call(self):
        # the `msg["type"] == "tool"` candidate path: a bare tool message with
        # state but no name proves nothing about the guard
        self.srv.canary_items = [{
            "type": "tool",
            "state": {"status": "error",
                      "error": "bash-guard: DENIED - unquoted heredoc"},
        }]
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_a_nameless_item_still_leaves_the_inconclusive_verdict(self):
        self.srv.canary_items = self._only({"type": "tool",
                                            "state": {"status": "error"}})
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("no shell tool call", self._state()["canary"]["detail"])


class TestDenialMarkerAnchor(_Base):
    """F3: the marker must ANCHOR the error text, not appear inside it.

    `DENIED_MARKER in err` let any model-authored text that quoted the marker
    pass the canary: an echoed sentence, a tool output that merely mentioned
    it, or an error from a non-error status.
    """

    def _err_item(self, error, status="error", tool="shell", command=INCIDENT):
        state = {"status": status, "error": error}
        if command is not None:
            state["input"] = {"command": command}
        return [{"type": "assistant", "content": [
            {"type": "tool", "tool": tool, "state": state}]}]

    # Sonnet final REJECT 2026-10-08 finding 5: a denial is evidence about the
    # command it was raised for. The guard denies plenty of things (an L2's
    # `git commit` in orchestrator role, for one), so ANY anchored denial used
    # to pass the canary - the probe never had to run its own incident command,
    # and rc 0 then certified an unguarded lane as unattended-capable.
    def test_denial_of_another_command_is_not_a_canary_pass(self):
        self.srv.canary_items = self._err_item(
            "bash-guard: DENIED - orchestrator role runs no writes",
            command="git commit -m 'wip'")
        rc, out = self._start()
        self.assertEqual(rc, 5, out)
        self.assertFalse(self._state()["canary"]["denied"])
        self.assertIn("git commit", self._state()["canary"]["detail"],
                      "the refusal must name the command that was denied")

    def test_a_denial_that_records_no_command_is_not_a_pass(self):
        self.srv.canary_items = self._err_item(
            "bash-guard: DENIED - unquoted heredoc", command=None)
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_the_canary_command_is_matched_exactly_or_by_its_unique_token(self):
        from oc_l1_canary import _is_canary_call
        self.assertTrue(_is_canary_call(
            {"state": {"input": {"command": INCIDENT}}}))
        # a model that re-wraps the heredoc still ran the probe: the delimiter
        # token is what makes the command the canary's own
        self.assertTrue(_is_canary_call(
            {"state": {"input": {"command": "cat <<CANARY_EOF\nx\nCANARY_EOF"}}}))
        self.assertTrue(_is_canary_call(
            {"input": {"command": INCIDENT}}), "the flat part shape too")
        self.assertFalse(_is_canary_call({"state": {"input": {"command": "ls"}}}))
        self.assertFalse(_is_canary_call({"state": {}}))
        self.assertFalse(_is_canary_call({}))

    def test_marker_midway_is_not_a_denial(self):
        self.srv.canary_items = self._err_item(
            "command failed; bash-guard: DENIED is what a guard would print")
        rc, out = self._start()
        self.assertEqual(rc, 5)
        self.assertIn("canary denied=no", out)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_marker_in_a_tool_output_that_completed_is_not_a_denial(self):
        self.srv.canary_items = self._err_item(
            "bash-guard: DENIED - heredoc", status="completed")
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        self.assertFalse(self._state()["canary"]["denied"])
        self.assertIn("tool completed without denial",
                      self._state()["canary"]["detail"])

    def test_marker_echoed_in_prose_is_not_a_denial(self):
        self.srv.canary_items = [{"type": "assistant", "content": [
            {"type": "text", "text": "bash-guard: DENIED - unquoted heredoc"}]}]
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_real_denial_still_counts_with_leading_noise_whitespace(self):
        # the plugin throws the guard's stderr, trimmed; opencode may pad it, so
        # whitespace before the marker is still an anchored denial.
        self.srv.canary_items = self._err_item(
            "\n  bash-guard: DENIED - unquoted heredoc <<CANARY_EOF\n")
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        self.assertTrue(self._state()["canary"]["denied"])

    def test_denial_from_a_bash_spelling_still_counts(self):
        self.srv.canary_items = self._err_item(
            "bash-guard: DENIED - unquoted heredoc", tool="bash")
        rc, _ = self._start()
        self.assertEqual(rc, 0)
        self.assertTrue(self._state()["canary"]["denied"])

    def test_the_helper_anchors_and_never_substrings(self):
        self.assertTrue(oc_l1_canary._is_denial("bash-guard: DENIED - x"))
        self.assertTrue(oc_l1_canary._is_denial("  bash-guard: DENIED\n"))
        self.assertFalse(oc_l1_canary._is_denial("see: bash-guard: DENIED"))
        self.assertFalse(oc_l1_canary._is_denial(""))
        self.assertFalse(oc_l1_canary._is_denial(None))
        self.assertFalse(oc_l1_canary._is_denial({"error": "bash-guard: DENIED"}))


class TestDenialObjectShape(_Base):
    """D1 (live check 2026-10-08): a real opencode error is an OBJECT, not a string.

    The transcript item the current server writes is
    `{"type": "tool", "name": "shell", "state": {"status": "error",
    "input": {"command": ...}, "error": {"type": "unknown", "message":
    "bash-guard: DENIED - l2 read-only: a here-document ..."}}}`. `_is_denial`
    read only a string, so a genuine denial came back "shell call inconclusive"
    and `start` refused a lane whose guard WAS working (rc 5). The anchor rule
    does not change - it just applies to the object's `message`.
    """

    def _obj_item(self, message, status="error", name="shell", command=INCIDENT):
        state = {"status": status, "error": {"type": "unknown", "message": message}}
        if command is not None:
            state["input"] = {"command": command}
        return [{"type": "assistant", "content": [
            {"type": "tool", "name": name, "state": state}]}]

    def test_a_real_denial_in_the_object_shape_passes(self):
        self.srv.canary_items = self._obj_item(
            "bash-guard: DENIED - l2 read-only: a here-document is not auditable")
        rc, out = self._start()
        self.assertEqual(rc, 0, out)
        self.assertTrue(self._state()["canary"]["denied"])

    def test_the_detail_is_the_message_not_a_python_dict_repr(self):
        self.srv.canary_items = self._obj_item(
            "bash-guard: DENIED - l2 read-only: a here-document is not auditable")
        rc, _ = self._start()
        self.assertEqual(rc, 0)
        detail = self._state()["canary"]["detail"]
        self.assertTrue(detail.startswith(DENIED_MARKER),
                        "the detail must be the plugin's throw, not str(dict): %r"
                        % detail)

    def test_marker_midway_in_the_message_is_not_a_denial(self):
        self.srv.canary_items = self._obj_item(
            "command failed; bash-guard: DENIED is what a guard would print")
        rc, out = self._start()
        self.assertEqual(rc, 5, out)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_a_messageless_error_object_is_not_a_denial(self):
        self.srv.canary_items = self._obj_item(None)
        rc, _ = self._start()
        self.assertEqual(rc, 5)
        self.assertFalse(self._state()["canary"]["denied"])

    def test_denial_of_another_command_in_the_object_shape_is_not_a_pass(self):
        self.srv.canary_items = self._obj_item(
            "bash-guard: DENIED - l2 write is denied", command="git commit -m 'wip'")
        rc, out = self._start()
        self.assertEqual(rc, 5, out)
        self.assertFalse(self._state()["canary"]["denied"])
        self.assertIn("git commit", self._state()["canary"]["detail"])

    def test_the_probe_command_is_read_from_the_live_item_shape(self):
        item = self._obj_item("bash-guard: DENIED - x")[0]["content"][0]
        self.assertTrue(oc_l1_canary._is_canary_call(item),
                        "state.input.command is where this shape carries the command")

    def test_the_helper_anchors_on_the_object_message(self):
        self.assertTrue(oc_l1_canary._is_denial(
            {"type": "unknown", "message": "bash-guard: DENIED - x"}))
        self.assertTrue(oc_l1_canary._is_denial({"message": "  bash-guard: DENIED\n"}))
        self.assertFalse(oc_l1_canary._is_denial(
            {"type": "unknown", "message": "see: bash-guard: DENIED"}))
        self.assertFalse(oc_l1_canary._is_denial({"type": "unknown"}))
        self.assertFalse(oc_l1_canary._is_denial({"message": None}))
        self.assertFalse(oc_l1_canary._is_denial({"message": 42}))
        # only the plugin's own throw shape counts: the error text lives in
        # `message`, so another key holding the marker is not evidence
        self.assertFalse(oc_l1_canary._is_denial({"error": "bash-guard: DENIED"}))
        self.assertFalse(oc_l1_canary._is_denial(
            [{"type": "unknown", "message": "bash-guard: DENIED"}]))
        self.assertFalse(oc_l1_canary._is_denial(42))


class TestTimeout(_Base):
    mode = "timeout"

    # (4)
    def test_timeout_refused_exit5_within_few_seconds(self):
        self.assertEqual(self.lane["canary_timeout_s"], 2)
        t0 = time.monotonic()
        rc, out = self._start()
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(rc, 5)
        self.assertIn("UNATTENDED-REFUSED", out)
        self.assertFalse(self._state()["canary"]["denied"])


class TestIdempotent(_Base):
    # (5)
    def test_idempotent_start_no_second_canary(self):
        rc1, _ = self._start()
        self.assertEqual(rc1, 0)
        rc2, out2 = self._start()
        self.assertEqual(rc2, 0)
        self.assertIn("already live", out2)
        self.assertIn("canary denied=yes", out2,
                      "stored canary line must be reprinted")
        self.assertEqual(len(_canary_posts(self.srv)), 1,
                         "second start must not create a canary session")
        prompts = [r for r in self.srv.requests
                   if r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 2)


class TestRelaunch(_Base):
    # (6)
    def test_relaunch_reruns_canary_with_newer_heartbeat(self):
        rc1, _ = self._start()
        self.assertEqual(rc1, 0)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        _terminate_pid(rec["pid"])
        # the pilot session now reads as dead -> a start is a relaunch
        self.srv.session_outcome = "failed"
        hb1 = self._heartbeat()
        time.sleep(1.1)  # ts is second-resolution
        rc2, out2 = self._start()
        self.assertEqual(rc2, 0)
        self.assertNotIn("already live", out2)
        self.assertEqual(len(_canary_posts(self.srv)), 2,
                         "relaunch must create a NEW canary session")
        hb2 = self._heartbeat()
        self.assertTrue(hb2["canary"]["denied"])
        self.assertGreater(hb2["ts"], hb1["ts"],
                           "heartbeat ts must be newer after relaunch")


class TestHeartbeatMerge(_Base):
    # (7)
    def test_heartbeat_merge_keeps_pilot_turn(self):
        hb_path = Path(self.lane["heartbeat_file"])
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        hb_path.write_text(
            json.dumps({"turn": 7, "current_step": "x"}), encoding="utf-8")
        rc, _ = self._start()
        self.assertEqual(rc, 0)
        hb = self._heartbeat()
        # documented behaviour: start resets `state` and `current_step`
        # but keeps the pilot's `turn`
        self.assertEqual(hb["turn"], 7, "pilot turn must be kept")
        self.assertEqual(hb["state"], "started")
        self.assertEqual(hb["current_step"], "canary")
        self.assertTrue(hb["canary"]["denied"])


class TestNoLeak(_Base):
    # (8)
    def test_password_in_no_argv_state_heartbeat_or_stdout(self):
        rc, out = self._start()
        self.assertEqual(rc, 0)
        wait_file(self.rec)
        rec = json.loads(self.rec.read_text(encoding="utf-8"))
        argv = " ".join(rec["argv"])
        state_text = Path(self.lane["state_file"]).read_text(encoding="utf-8")
        hb_text = Path(self.lane["heartbeat_file"]).read_text(encoding="utf-8")
        for where, blob in (("argv", argv), ("state file", state_text),
                            ("heartbeat.json", hb_text), ("stdout", out)):
            self.assertNotIn(PW_VALUE, blob, "password leaked in %s" % where)

    # (8c) F5: the base64 Basic form is the same secret in another shape — an
    #      HTTP error echoes the request header, so a bare-string replace of the
    #      password would leave the token behind.
    def test_the_basic_auth_token_is_in_no_output(self):
        rc, out = self._start()
        self.assertEqual(rc, 0)
        token = oc_l1_http._basic_header(PW_VALUE).split(" ", 1)[1]
        blobs = {
            "state file": Path(self.lane["state_file"]).read_text(
                encoding="utf-8"),
            "heartbeat.json": Path(self.lane["heartbeat_file"]).read_text(
                encoding="utf-8"),
            "stdout": out,
        }
        for where, blob in blobs.items():
            self.assertNotIn(token, blob, "auth token leaked in %s" % where)

    # (8b) D-665 fix 4 added a new file the launcher opens for the child: the
    #      child env carries the password, so nothing of the launcher's may
    #      reach that log either.
    def test_password_not_in_child_stderr_log(self):
        rc, _ = self._start()
        self.assertEqual(rc, 0)
        log = Path(self.lane["scratch_dir"]) / "opencode.log"
        self.assertTrue(log.is_file(), "the child stderr log must exist")
        self.assertNotIn(PW_VALUE,
                         log.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
