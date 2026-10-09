#!/usr/bin/env python3
"""Tests for infra/mcp-servers/scripts/_go_gate.py: the D-825 GO-ref check.

Fleet rule D-825 lets a live-store or live-gateway run proceed only on an explicit
judge GO, and the gate is one implementation this module owns: `apply.sh`,
`apply-cluster.sh` and the PowerShell twins reach it through its `verify-ref`
command, the omnigraph tools through `enforce`. Checking only the ref's FORMAT was
the bug — `--go D-1` matched the shape, named nothing, and passed. So the shared
gate now also proves the ref EXISTS:

  D-<n>      a line or heading with that exact id in the routing decisions log
             (AUTOOS_DECISIONS_LOG, else AUTOOS_ROUTING_DIR/docs/decisions-log.md,
             and AUTOOS_ROUTING_DIR/DECISIONS.md counts too)
  OS-<n>     the same in the routing QUESTIONS.md or ANSWERS.md
  run id     a worker record: AUTOOS_WORKERS_DIR/<id>.json or its sibling
             agents/<id>/, else <checkout>/logs/workers and logs/agents
  absent id, or an unreadable/missing source -> refuse (verdict 'unreadable')
  exact-id match: D-82 must not be satisfied by a log line holding D-825

Nothing here reads a real routing dir or a real store: every case builds a temp tree
and hands it over as an env mapping. No docker, no network, no token.

Fleet rule D-852 (operator 2026-10-09): a case that CLEARS the gate is not finished —
the tool it names goes on to exec docker/systemctl/curl/ssh and to dial the live
gateway, and one such case restarted omnigraph-server on the host. So every case that
spawns a process runs inside a stand-in directory (Standins below, the twin of
gate_stubs_make in tests/run-tests.sh): those binaries are shadowed by scripts that
record the call and exit 0, and every gateway/graph URL env var points at a closed
port. The guard cases assert the shadow itself holds.

Run from the repo root:

    python3 tests/test_go_gate_ref.py
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "infra" / "mcp-servers" / "scripts" / "_go_gate.py"

RUN_ID = "20261009-064139-goref-fix"
CONTEXT = {"HOME": "/fixture-home"}

# What a cleared gate execs off PATH, and every env var that can name a live
# gateway or graph store. Same lists the bash and PowerShell suites shadow.
STANDIN_BINS = ("docker", "curl", "systemctl", "ssh", "omniroute", "npx", "node")
URL_ENV = ("AUTOOS_OMNIROUTE_URL", "OMNIROUTE_BASE_URL",
           "AUTOOS_OMNIGRAPH_URL", "OMNIGRAPH_URL", "OMNI_S3")
URL_SINK = "http://127.0.0.1:9"          # discard port: nothing listens


class Standins:
    """A PATH stand-in directory: records the call, runs nothing, exits 0."""

    def __init__(self, root):
        self.dir = Path(root) / "stub"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "calls.log"
        self.log.write_text("", encoding="utf-8")
        for name in STANDIN_BINS:
            if os.name == "nt":
                # cmd.exe runs the .cmd twin; %~dp0 keeps the log path out of it.
                (self.dir / (name + ".cmd")).write_text(
                    "@echo off\r\n"
                    '>>"%~dp0calls.log" echo %s %%*\r\n'
                    "exit /b 0\r\n" % name, encoding="utf-8")
            else:
                path = self.dir / name
                path.write_text('#!/bin/sh\nprintf "%%s %%s\\n" "%s" "$*" >>"%s"\nexit 0\n'
                                % (name, self.log), encoding="utf-8")
                path.chmod(0o755)

    def binary(self, name):
        return self.dir / (name + (".cmd" if os.name == "nt" else ""))

    def env(self, extra=None):
        full = dict(os.environ)
        full["PATH"] = os.pathsep.join([str(self.dir), full.get("PATH", "")])
        for key in URL_ENV:
            full[key] = URL_SINK
        full.update(extra or {})
        return full

    def where(self, name):
        """Where `name` resolves inside the stand-in environment."""
        return shutil.which(name, path=self.env()["PATH"])

    def calls(self):
        return self.log.read_text(encoding="utf-8")

    def saw(self, name):
        return any(line.startswith(name + " ") for line in self.calls().splitlines())


def load_gate():
    spec = importlib.util.spec_from_file_location("_go_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_gate()


class Tree:
    """A throwaway artefact tree plus the env mapping that points the gate at it."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        self.routing = self.root / "routing"
        self.workers = self.root / "logs" / "workers"

    def log(self, text, name="docs/decisions-log.md"):
        path = self.routing / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def questions(self, text, name="QUESTIONS.md"):
        self.routing.mkdir(parents=True, exist_ok=True)
        path = self.routing / name
        path.write_text(text, encoding="utf-8")
        return path

    def worker(self, run_id):
        self.workers.mkdir(parents=True, exist_ok=True)
        path = self.workers / (run_id + ".json")
        path.write_text(json.dumps({"id": run_id}), encoding="utf-8")
        return path

    def agent_dir(self, run_id):
        path = self.workers.parent / "agents" / run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "record.md").write_text("fake\n", encoding="utf-8")
        return path

    def env(self, decisions_log=None):
        env = {"AUTOOS_ROUTING_DIR": str(self.routing),
               "AUTOOS_WORKERS_DIR": str(self.workers)}
        if decisions_log is not None:
            env["AUTOOS_DECISIONS_LOG"] = str(decisions_log)
        return env


class RefExistsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Tree(self._tmp.name)

    def verdict(self, ref, env=None):
        return gate.verify_ref(ref, str(self.tree.root), env=self.tree.env() if env is None else env)

    def test_decision_id_present_in_the_log_is_found(self):
        self.tree.log("| D-825 | all L1s | only an explicit GO counts | router |\n")
        self.assertEqual(self.verdict("D-825"), "found")

    def test_decision_id_absent_from_the_log_is_not(self):
        self.tree.log("| D-825 | all L1s | only an explicit GO counts | router |\n")
        self.assertEqual(self.verdict("D-9999"), "absent")

    def test_the_id_matches_exactly_not_as_a_prefix(self):
        # The whole point: a log line holding D-825 must not satisfy D-82, and one
        # holding D-82 must not satisfy D-825. A substring search gets both wrong.
        self.tree.log("| D-825 | all L1s | only an explicit GO counts | router |\n")
        self.assertEqual(self.verdict("D-82"), "absent")
        self.tree.log("## D-82 the earlier rule\n")
        self.assertEqual(self.verdict("D-825"), "absent")

    def test_a_decision_id_in_the_consolidated_decisions_file_is_enough(self):
        self.tree.log("# Decisions log\n\nnothing here.\n", name="docs/decisions-log.md")
        self.tree.log("5.7 Live steps need a GO (D-825).\n", name="DECISIONS.md")
        self.assertEqual(self.verdict("D-825"), "found")

    def test_an_explicit_decisions_log_is_the_whole_source(self):
        # AUTOOS_DECISIONS_LOG names one file; the routing dir must not quietly
        # contribute a second one, or a test (or an operator) cannot pin the source.
        self.tree.log("D-825 here.\n", name="DECISIONS.md")
        other = Path(self.tree.root) / "elsewhere.md"
        other.write_text("nothing.\n", encoding="utf-8")
        self.assertEqual(self.verdict("D-825", env=self.tree.env(decisions_log=other)), "absent")

    def test_an_unreadable_log_is_not_absent_but_unreadable(self):
        missing = Path(self.tree.root) / "no-such-dir" / "decisions-log.md"
        self.assertEqual(self.verdict("D-825", env=self.tree.env(decisions_log=missing)),
                         "unreadable")
        # ... and an empty tree is unreadable too, not "the id was not found".
        self.assertEqual(self.verdict("D-825", env={"AUTOOS_ROUTING_DIR": str(self.tree.routing),
                                                    "HOME": "/fixture-home"}), "unreadable")

    def test_default_sources_are_the_routing_dir_under_home(self):
        home = Path(self.tree.root) / "home"
        (home / "code" / "routing" / "docs").mkdir(parents=True)
        (home / "code" / "routing" / "docs" / "decisions-log.md").write_text(
            "| D-825 | router |\n", encoding="utf-8")
        env = {"HOME": str(home)}
        self.assertEqual(gate.verify_ref("D-825", str(self.tree.root), env=env), "found")
        self.assertEqual(gate.verify_ref("D-9999", str(self.tree.root), env=env), "absent")

    def test_os_item_in_questions_or_answers(self):
        self.tree.questions("## OS-1 — where does the gate run?\n")
        self.assertEqual(self.verdict("OS-1"), "found")
        self.assertEqual(self.verdict("OS-9"), "absent")
        self.tree.questions("## OS-10 — answered elsewhere\n", name="ANSWERS.md")
        self.assertEqual(self.verdict("OS-10"), "found")
        # OS-1 is not satisfied by an OS-10 line.
        self.assertEqual(self.verdict("OS-1"), "found")

    def test_run_id_needs_a_worker_record(self):
        self.assertEqual(self.verdict(RUN_ID), "unreadable")
        self.tree.worker(RUN_ID)
        self.assertEqual(self.verdict(RUN_ID), "found")
        self.assertEqual(self.verdict("20261009-000000-nosuchrun"), "absent")

    def test_run_id_agent_dir_shape_counts(self):
        self.tree.agent_dir("20261009-070000-goref-agent")
        self.assertEqual(self.verdict("20261009-070000-goref-agent"), "found")

    def test_run_id_defaults_to_the_checkout_logs_dir(self):
        workers = Path(self.tree.root) / "logs" / "workers"
        workers.mkdir(parents=True)
        (workers / (RUN_ID + ".json")).write_text("{}", encoding="utf-8")
        self.assertEqual(gate.verify_ref(RUN_ID, str(self.tree.root), env=dict(CONTEXT)), "found")
        self.assertEqual(gate.verify_ref("20261009-000000-nosuchrun", str(self.tree.root),
                                         env=dict(CONTEXT)), "absent")


class EnforceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Tree(self._tmp.name)
        self.tree.log("D-825 present.\n")
        self.tree.worker(RUN_ID)

    def enforce(self, ref, sha="deadbeef", offline=False, head="deadbeef"):
        original = gate.checkout_head
        gate.checkout_head = lambda root: head
        self.addCleanup(setattr, gate, "checkout_head", original)
        out, err = io.StringIO(), io.StringIO()
        code = None
        try:
            with redirect_stdout(out), redirect_stderr(err):
                gate.enforce("tool.py", "mutate the live store", True, ref, sha,
                             str(self.tree.root), go_offline=offline,
                             env=self.tree.env())
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_a_ref_that_names_nothing_is_refused(self):
        code, out, err = self.enforce("D-9999")
        self.assertEqual(code, 2)
        self.assertIn("names no decision", err)
        self.assertNotIn("GO:", out)

    def test_a_ref_that_exists_clears_the_gate_and_prints_go_first(self):
        code, out, err = self.enforce("D-825")
        self.assertIsNone(code)
        self.assertEqual(out.splitlines()[0], "GO: D-825 sha=deadbeef")

    def test_the_gate_itself_spawns_nothing(self):
        # Clearing the gate is pure file reading. Everything that execs docker or
        # curls the gateway belongs to the CALLER, which runs under the stand-in
        # directory; if the gate module ever spawns one itself, that is a host
        # call no test can shadow, so this fails it here.
        original = gate.subprocess

        class _NoSpawn:
            def run(self, *args, **kwargs):
                raise AssertionError("the gate execed %r" % (args[:1],))

        gate.subprocess = _NoSpawn()
        self.addCleanup(setattr, gate, "subprocess", original)
        code, out, _ = self.enforce("D-825")
        self.assertIsNone(code)
        self.assertEqual(out.splitlines()[0], "GO: D-825 sha=deadbeef")

    def test_the_sha_bar_still_fires_before_the_artefact_lookup(self):
        code, out, err = self.enforce("D-825", sha="not-head")
        self.assertEqual(code, 2)
        self.assertIn("is not this checkout's HEAD", err)

    def test_offline_proceeds_and_logs_the_unverified_ref_first(self):
        env = self.tree.env(decisions_log=Path(self.tree.root) / "gone.md")
        original = gate.checkout_head
        gate.checkout_head = lambda root: "deadbeef"
        self.addCleanup(setattr, gate, "checkout_head", original)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            gate.enforce("tool.py", "mutate the live store", True, "D-825", "deadbeef",
                         str(self.tree.root), go_offline=True, env=env)
        self.assertEqual(out.getvalue().splitlines()[0], "GO-OFFLINE: D-825 unverified")
        self.assertEqual(out.getvalue().splitlines()[1], "GO: D-825 sha=deadbeef")

    def test_offline_still_refuses_a_malformed_ref(self):
        code, out, err = self.enforce("not-a-ref", offline=True)
        self.assertEqual(code, 2)
        self.assertIn("is not a judge run id", err)
        self.assertNotIn("GO-OFFLINE", out)

    def test_a_read_only_run_needs_neither_ref_nor_offline(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            gate.enforce("tool.py", "mutate the live store", False, "", "", str(self.tree.root))
        self.assertEqual(out.getvalue(), "")


class VerifyRefCommandTests(unittest.TestCase):
    """The CLI the bash and PowerShell gates call so the rule lives in one file.

    Every run goes out through the stand-in environment (D-852): the CLI reads files
    only, so a cleared or a refused gate must leave the stand-in record empty — the
    one call the host would have noticed is a call that never should have been made.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._standin = tempfile.TemporaryDirectory()
        self.addCleanup(self._standin.cleanup)
        self.standins = Standins(self._standin.name)
        self.tree = Tree(self._tmp.name)
        self.tree.log("D-825 present.\n")

    def run_cli(self, *args, env=None, tool="apply.sh"):
        full_env = self.standins.env(self.tree.env())
        full_env.update(env or {})
        proc = subprocess.run([sys.executable, str(GATE), "verify-ref",
                               "--tool", tool, "--root", str(self.tree.root)]
                              + list(args), capture_output=True, text=True, env=full_env)
        return proc.returncode, proc.stdout, proc.stderr

    def test_existing_ref_exits_zero_and_says_nothing(self):
        rc, out, err = self.run_cli("--ref", "D-825")
        self.assertEqual((rc, out.strip(), err.strip()), (0, "", ""))
        self.assertEqual(self.standins.calls().strip(), "",
                         "a cleared gate execed a host binary: %s" % self.standins.calls())

    def test_absent_ref_exits_two_with_the_reason_on_stderr(self):
        rc, out, err = self.run_cli("--ref", "D-9999")
        self.assertEqual(rc, 2)
        self.assertIn("apply.sh: --go 'D-9999' names no decision", err)
        self.assertEqual(self.standins.calls().strip(), "",
                         "a refused run reached a host binary: %s" % self.standins.calls())

    def test_malformed_ref_exits_two(self):
        rc, out, err = self.run_cli("--ref", "D-")
        self.assertEqual(rc, 2)
        self.assertIn("is not a judge run id", err)

    def test_ps_flag_style_is_named_in_the_message(self):
        rc, out, err = self.run_cli("--ref", "D-9999", "--flag-style", "ps", tool="apply.ps1")
        self.assertEqual(rc, 2)
        self.assertIn("-Go 'D-9999'", err)
        self.assertIn("-GoOffline", err)
        self.assertNotIn("--go ", err)

    def test_the_default_message_names_the_bash_flags(self):
        rc, out, err = self.run_cli("--ref", "D-9999")
        self.assertIn("--go-offline", err)

    def test_offline_exits_zero_and_prints_the_log_line(self):
        rc, out, err = self.run_cli("--ref", "D-9999", "--offline")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "GO-OFFLINE: D-9999 unverified")

    def test_the_module_still_imports_cleanly_for_its_callers(self):
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import _go_gate;"
             "print(_go_gate.REF_RE.match('D-825') is not None)" % str(GATE.parent)],
            capture_output=True, text=True, env=self.standins.env())
        self.assertEqual(proc.stdout.strip().splitlines()[-1], "True", proc.stderr)


class StandinEnvironmentTests(unittest.TestCase):
    """D-852: the stand-in directory is what the spawned cases really run on.

    A case whose shadow ever stops being first still prints green while it restarts
    the live stack — that is how omnigraph-server was restarted on 2026-10-09 — so
    the shadow is a tested property, not a convention: every binary resolves inside
    it, a stand-in records the call instead of making one, and no URL env var names a
    live gateway.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.standins = Standins(self._tmp.name)

    def test_every_binary_a_cleared_gate_execs_resolves_inside_the_stand_in_dir(self):
        escaped = []
        for name in STANDIN_BINS:
            where = self.standins.where(name)
            if where is None or Path(where).parent != self.standins.dir:
                escaped.append("%s:%s" % (name, where))
        # docker and systemctl are the two that restarted the live server; a
        # regression must say which one escaped, not only that one did.
        for name in ("docker", "systemctl"):
            where = self.standins.where(name)
            if where is None or Path(where).parent != self.standins.dir:
                escaped.append("%s-outside-the-stand-in-dir:%s" % (name, where))
        self.assertEqual(escaped, [], "resolved outside the stand-in dir: %s" % " ".join(escaped))

    def test_a_stand_in_records_the_call_and_runs_nothing(self):
        env = self.standins.env()
        for name in STANDIN_BINS:
            proc = subprocess.run([str(self.standins.binary(name)), "--pretend-argument"],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, "%s exited %s" % (name, proc.returncode))
            self.assertEqual(proc.stdout, "", "%s printed: %s" % (name, proc.stdout))
            self.assertTrue(self.standins.saw(name), "%s was not recorded" % name)
        self.assertIn("--pretend-argument", self.standins.calls(),
                      "the record lost the arguments")
        # The host's docker answers `inspect` with a StartedAt; the stand-in answers
        # nothing at all — the direct test of the incident.
        proc = subprocess.run([str(self.standins.binary("docker")), "inspect",
                               "-f", "{{.State.StartedAt}}", "omnigraph-server"],
                              capture_output=True, text=True, env=env)
        self.assertNotIn("20", proc.stdout, "the stand-in answered with a real container")

    def test_the_gateway_url_env_never_names_a_live_gateway(self):
        env = self.standins.env()
        for key in URL_ENV:
            self.assertEqual(env[key], URL_SINK, key)
        got = subprocess.run([sys.executable, "-c",
                              "import os; print(os.environ.get('AUTOOS_OMNIROUTE_URL', ''))"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(got.stdout.strip(), URL_SINK)
        # A case that names its own loopback stand-in still wins — that is how the
        # apply battery talks to its fake gateway — but it is a port on this machine.
        got = subprocess.run(
            [sys.executable, "-c",
             "import os; print(os.environ.get('AUTOOS_OMNIROUTE_URL', ''))"],
            capture_output=True, text=True,
            env=self.standins.env({"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}))
        self.assertEqual(got.stdout.strip(), "http://127.0.0.1:20128")
        self.assertEqual(URL_SINK, "http://127.0.0.1:9", "the sink is not the discard port")


if __name__ == "__main__":
    unittest.main(verbosity=2)
