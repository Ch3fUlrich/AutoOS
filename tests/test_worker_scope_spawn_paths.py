#!/usr/bin/env python3
"""MEMGUARD ADDENDUM item 7: every spawn path puts a run into an
autoos-worker-*.scope, and an unscopable host is never silent about it.

Three claims, one file:

1. *No quiet spawn site.* ``tools/autoos-agent.py`` and
   ``tools/autoos_agent_mcp.py`` are AST-scanned for ``subprocess.Popen`` /
   ``subprocess.call``; every site must sit inside a function whitelisted
   below. A spawn path added outside that list fails here until it is scoped -
   the hole item 7 closes is an *unscopable* launch that nothing reports.

2. *The launch paths really scope (or really warn).* ``run_client`` is driven
   through its own launch code with the OS and process seams faked (nothing is
   started): a ``scoped`` decision wraps the client in
   ``systemd-run --user --scope --unit autoos-worker-...``, and an
   ``unscoped`` decision with ``SCOPE_REASON_UNSUPPORTED`` prints
   ``SCOPE_WARNING`` on stderr. ``run_job`` (the MCP path) must gate its
   ``subprocess.call`` on ``scope_supported()`` and wrap it with
   ``worker_scope_launch`` in source - static, because driving it really spawns
   a worker.

3. *memguard can stop what these paths create.* The unit names the spawner
   derives are matched against ``WORKER_SCOPE_RE`` read out of
   ``lib/linux/memguard.sh`` - the guard's own character-whitelist gate. A
   scope the guard would refuse to stop is a scope nothing can stop under
   memory pressure, which is the one thing MEMGUARD exists to do.

Run from the repo root:

    python3 tests/test_worker_scope_spawn_paths.py
"""
import ast
import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
MCP = TOOLS / "autoos_agent_mcp.py"
MEMGUARD = ROOT / "lib" / "linux" / "memguard.sh"

# The ONLY functions either file may start a subprocess in, and why each one
# is allowed to exist without a scope around every one of its children:
SPAWN_WHITELIST = {
    "tools/autoos-agent.py": {
        # the client launch: wraps in a scope when scope_decision says
        # "scoped", prints SCOPE_WARNING when it says "unscoped"
        "run_client",
        # availability probe - `systemd-run ... python -c pass` and nothing
        # else; it never starts an agent run (and its answer is the warning)
        "_probe_scope",
        # teardown: `taskkill /T /F` of a client this process already launched
        "_terminate_group",
    },
    "tools/autoos_agent_mcp.py": {
        # the detached `--run-job` runner; run_job inside it is what builds the
        # worker scope, and the runner gets the user bus back to do it
        "spawn",
        # the worker launch: worker_scope_launch when scope_supported()
        "run_job",
    },
}

# Names that must be CALLED inside a function, on top of the whitelist - a
# spawn site whose scope mechanism was quietly deleted fails instead of
# silently moving to an unscoped launch.
REQUIRED_CALLS = {
    "tools/autoos-agent.py": {"_probe_scope": ("worker_scope_argv",)},
    "tools/autoos_agent_mcp.py": {"run_job": ("scope_supported",
                                              "worker_scope_launch"),
                                  "spawn": ("spawner_child_env",)},
}

RUN_ID = "20261002-181312-memguard-abcdef"


def load_agent(name="autoos_agent_worker_scope_spawn_tests"):
    spec = importlib.util.spec_from_file_location(name, str(AGENT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_spawn_call(node):
    """A `subprocess.Popen(...)` / `subprocess.call(...)` in the source."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("Popen", "call")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess")


def spawn_sites(tree):
    """[(enclosing-function-name, Call node)] for every spawn site in `tree`.

    The function name is None for a module-level site - which no whitelist
    entry can cover, so it fails on its own.
    """
    found = []

    def walk(node, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        if _is_spawn_call(node):
            found.append((fn, node))
        for child in ast.iter_child_nodes(node):
            walk(child, fn)

    walk(tree, None)
    return found


def function_tree(tree, name):
    """The FunctionDef `name`, failing loudly rather than feeding None onward."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("no function %r in the parsed source" % name)


def called_names(fn_node):
    """Every function NAME called anywhere inside `fn_node`.

    ``worker_scope_argv(...)`` is a bare name and ``agent.spawn()`` an
    attribute; a scanner that only sees one of the two reads a function that
    merely stopped using method syntax as if it had dropped its scope gate.
    """
    names = []
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
        elif isinstance(node.func, ast.Name):
            names.append(node.func.id)
    return names


def memguard_worker_scope_re():
    """The regex `lib/linux/memguard.sh` re-validates unit names against."""
    text = MEMGUARD.read_text(encoding="utf-8")
    match = re.search(r"WORKER_SCOPE_RE='([^']+)'", text)
    if not match:
        raise AssertionError("WORKER_SCOPE_RE not found in %s - memguard's "
                             "stop gate moved; this test must follow it"
                             % MEMGUARD)
    return match.group(1)


class _PosixOsProxy(object):
    """The real `os` module, with `name` forced to "posix".

    `run_client` picks its launch path off `os.name`; pinning POSIX keeps these
    assertions the same on the Windows hosts that also run the suite, without
    mutating the process-wide `os` module the rest of the runtime is using.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, item):
        if item == "name":
            return "posix"
        return getattr(self._real, item)


class _SubprocessProxy(object):
    """`subprocess` with one attribute replaced (the recording Popen)."""

    def __init__(self, real, popen):
        self._real = real
        self._popen = popen

    def __getattr__(self, item):
        if item == "Popen":
            return self._popen
        return getattr(self._real, item)


class _NothingStarted(object):
    """A Popen result that starts nothing: no process, no signal, no group."""

    pid = 424242
    stdout = None
    stderr = None

    def wait(self):
        return 0


class SpawnSiteWhitelistTests(unittest.TestCase):
    """Every Popen/call site is inside a whitelisted, scope-aware function."""

    def _sites(self, relpath):
        path = ROOT / relpath
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return spawn_sites(tree), tree

    def test_every_spawn_site_sits_inside_a_whitelisted_function(self):
        for relpath, allowed in sorted(SPAWN_WHITELIST.items()):
            sites, _ = self._sites(relpath)
            offenders = sorted(set(fn for fn, _ in sites if fn not in allowed))
            self.assertEqual(
                offenders, [],
                "%s spawns a subprocess outside the audited list: %s (a new "
                "spawn path must land the run in an autoos-worker-*.scope <fn>"
                % (relpath, offenders))
            self.assertTrue(sites, "%s spawned nothing - the scan is broken, "
                                   "or these sites moved to another spelling "
                                   "this test does not see" % relpath)

    def test_the_whitelist_names_every_function_that_still_spawns(self):
        """Equality, not containment: a whitelisted site that disappears (say,
        run_client's launch) must be noticed, not pass as "covered"."""
        for relpath, allowed in sorted(SPAWN_WHITELIST.items()):
            sites, _ = self._sites(relpath)
            found = set(fn for fn, _ in sites)
            self.assertEqual(
                found, allowed,
                ("%s: audited %s but found %s - update the whitelist AND the "
                 "scope behaviour together" % (relpath, sorted(allowed),
                                               sorted(found))))

    def test_each_audited_site_still_calls_its_scope_mechanism(self):
        for relpath, required in sorted(REQUIRED_CALLS.items()):
            _, tree = self._sites(relpath)
            for fn, names in sorted(required.items()):
                present = called_names(function_tree(tree, fn))
                for name in names:
                    self.assertIn(name, present,
                                  "%s.%s no longer calls %s - the scope gate "
                                  "it guards may be gone" % (relpath, fn, name))


class McpSpawnScopeContractTests(unittest.TestCase):
    """The MCP side, statically: the runner gets the bus, the worker gets a scope."""

    @staticmethod
    def _tree():
        return ast.parse(MCP.read_text(encoding="utf-8"), filename=str(MCP))

    def test_the_detached_runner_is_started_detached_and_gets_the_user_bus(self):
        tree = self._tree()
        sites = [node for fn, node in spawn_sites(tree) if fn == "spawn"]
        self.assertEqual(len(sites), 1, sites)
        call = sites[0]
        kw = {k.arg: k.value for k in call.keywords}
        self.assertIn("env", kw, "spawn builds no child env")
        # start_new_session: the runner leads its own session, so it survives
        # the spawner; scope_bus: it alone gets the bus address back, because
        # launching the worker scope is what it needs it for.
        self.assertTrue(_constant(kw.get("start_new_session")),
                        "the detached runner lost start_new_session=True")
        env_call = kw["env"]
        self.assertIsInstance(env_call, ast.Call)
        env_names = [(k.arg, k.value) for k in env_call.keywords]
        self.assertTrue(any(name == "scope_bus" and _constant(value)
                            for name, value in env_names),
                        "spawn no longer hands spawn's child the user bus "
                        "(scope_bus=True) - run_job could not build the scope")

    def test_run_job_gates_its_call_on_scope_supported_and_wraps_it(self):
        tree = self._tree()
        fn = function_tree(tree, "run_job")
        gate_index = call_index = None
        for index, stmt in enumerate(fn.body):
            names = set(called_names(stmt))
            if gate_index is None and "scope_supported" in names:
                gate_index = index
            if call_index is None and any(_is_spawn_call(n)
                                          for n in ast.walk(stmt)):
                call_index = index
        self.assertIsNotNone(gate_index,
                             "run_job never asks whether a scope is possible")
        self.assertIsNotNone(call_index, "run_job launches nothing?")
        self.assertLess(gate_index, call_index,
                        "run_job launches the worker BEFORE it asks whether a "
                        "scope is possible - a run could land outside "
                        "autoos-worker-*.scope with nothing to say so")
        names = set(called_names(fn))
        self.assertIn("worker_scope_launch", names,
                      "run_job does not wrap the worker in its scope")


class RunClientScopeLaunchTests(unittest.TestCase):
    """drive run_client's real launch branch with the seams faked."""

    def setUp(self):
        self.agent = load_agent()

    def _launch(self, scope):
        """One un-captured client launch; returns (rc, recorded argv/kw, stderr).

        Nothing is spawned: Popen is a recorder, os says posix, and the scope
        decision is handed in so both branches can be driven identically on any
        host the suite runs on.
        """
        records = {}

        def record_popen(argv, **kw):
            records["argv"] = list(argv)
            records["kw"] = kw
            return _NothingStarted()

        err = io.StringIO()
        env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
        with mock.patch.object(self.agent, "os", _PosixOsProxy(os)), \
                mock.patch.object(self.agent, "subprocess",
                                  _SubprocessProxy(subprocess, record_popen)), \
                mock.patch.object(self.agent, "resolve_client_executable",
                                  lambda cmd: list(cmd)), \
                mock.patch.object(self.agent, "scope_decision",
                                  lambda run_id=None, attempt=None: dict(scope)):
            with contextlib.redirect_stderr(err):
                rc = self.agent.run_client([sys.executable, "-c", "pass"],
                                           str(ROOT), env, reap=False,
                                           capture=False, run_id=RUN_ID,
                                           attempt=1)
        return rc, records, err.getvalue()

    def test_a_scoped_launch_wraps_the_client_in_the_worker_scope(self):
        unit = self.agent.cli_scope_unit(RUN_ID, 1)
        scope = {"path": "scoped", "unit": unit, "reason": ""}
        rc, records, err = self._launch(scope)
        argv = records["argv"]
        self.assertEqual(int(rc), 0, rc)
        self.assertEqual(argv[:4], ["systemd-run", "--user", "--scope", "--unit"],
                         argv)
        self.assertEqual(argv[4], unit[:-len(".scope")], argv)
        self.assertIn("--", argv)
        inner = argv[argv.index("--") + 1:]
        # SCOPEBUS: the wrapper strips the bus address again INSIDE the scope
        # (env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS), then runs the
        # client unchanged - the client is the last thing on the line.
        self.assertEqual(inner[0], shutil.which("env") or "/usr/bin/env", inner)
        self.assertEqual(len(inner), 1 + 2 * len(self.agent.SCOPE_BUS_ENV) + 3,
                         inner)
        self.assertEqual(inner[-3:], [sys.executable, "-c", "pass"], inner)
        self.assertTrue(records["kw"].get("start_new_session"),
                        "even a scoped client lost its own session")
        # the scope the guard would stop, and the command that states it
        self.assertIn(unit, err, err)
        self.assertIn("systemctl --user stop", err, err)
        self.assertNotIn("WARNING", err, err)
        self.assertEqual(rc.scope, scope, rc.scope)

    def test_an_unscopable_host_warns_with_the_unsupported_reason(self):
        """MEMGUARD item 7: SCOPE_REASON_UNSUPPORTED is a warning, not silence.

        The fallback launch is legal; running it without a word is what left an
        operator unable to say whether `cancel` follows a setsid() child (a
        scope) or only the process group.
        """
        scope = {"path": "unscoped", "unit": None,
                 "reason": self.agent.SCOPE_REASON_UNSUPPORTED}
        rc, records, err = self._launch(scope)
        argv = records["argv"]
        self.assertEqual(int(rc), 0, rc)
        self.assertEqual(argv, [sys.executable, "-c", "pass"], argv)
        self.assertEqual(err.splitlines(),
                         [self.agent.SCOPE_WARNING
                          % self.agent.SCOPE_REASON_UNSUPPORTED], err)
        self.assertIn("UNSCOPED", err, err)
        self.assertIn(self.agent.SCOPE_REASON_UNSUPPORTED, err, err)
        self.assertEqual(rc.scope, scope, rc.scope)

    def test_the_unscopable_reason_also_reaches_the_reader_of_a_run_line(self):
        """`scope:` is what `ps` and the run log show; an empty reason there
        is the same silence the warning exists to end."""
        line = self.agent.scope_line({"path": "unscoped", "unit": None,
                                      "reason": self.agent.SCOPE_REASON_UNSUPPORTED})
        self.assertIn(self.agent.SCOPE_REASON_UNSUPPORTED, line, line)
        self.assertIn("scope: unscoped", line, line)
        # a record that never computed one still says WHY it is unscoped
        bare = self.agent.scope_line({"path": "unscoped", "unit": None,
                                      "reason": ""})
        self.assertIn(self.agent.SCOPE_REASON_UNSUPPORTED, bare, bare)

    def test_scope_decision_never_reports_a_unit_memguard_could_not_name(self):
        """The reason/units the decision hands back are the ones the guard can
        stop: `scope_unit_name` sanitises an id systemd would refuse, and the
        result must still pass memguard's re-validation gate."""
        regex = re.compile(memguard_worker_scope_re())
        units = [
            self.agent.scope_unit_name(RUN_ID),
            self.agent.cli_scope_unit(RUN_ID, 2),
            self.agent.worker_scope_unit(RUN_ID),
            # an id nobody chose: derived names are sanitised, never raw
            self.agent.scope_unit_name("../../etc/passwd; reboot"),
        ]
        for unit in units:
            self.assertRegex(unit, regex,
                             "%s is a scope memguard would refuse to stop: %s"
                             % (unit, memguard_worker_scope_re()))
            self.assertTrue(unit.startswith(self.agent.WORKER_SCOPE_PREFIX), unit)
            self.assertTrue(unit.endswith(".scope"), unit)


def _constant(node):
    """True for `True` / a truthy literal, False for anything else."""
    if node is None:
        return False
    if isinstance(node, ast.Name) and node.id == "True":
        return True
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return False


if __name__ == "__main__":
    unittest.main(verbosity=2)
