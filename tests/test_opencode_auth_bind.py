#!/usr/bin/env python3
"""opencode serve exposure + auth contract (incident D-2542, scope D-2543/D-2549).

opencode V2 answers its static UI to anyone and guards only /api/* with HTTP
Basic auth (user ``opencode``), so what keeps the LAN out of the agent's
sessions is the *bind*, not the password. What this file pins:

* compose publishes opencode on ``${AUTOOS_OPENCODE_PUBLISH_IP:-127.0.0.1}`` -
  loopback by default, an explicit allowlisted address from ``stack.env``,
  never ``0.0.0.0`` (``AUTOOS_STACK_BIND`` keeps owning 20128/3000);
* the container healthcheck proves the guarded API answers (it authenticates,
  or the auth layer itself answers 401) instead of curling the open root;
* every host spawner binds loopback by default;
* ``OPENCODE_PASSWORD`` and ``OPENCODE_SERVER_PASSWORD`` come from the one
  ``configuration/api-keys.yml`` -> ``opencode_password`` value, so a build
  reading either name gets the same password (measured: with both set,
  ``OPENCODE_PASSWORD`` wins; either name alone works).

The last class records upstream bug anomalyco/opencode#45856 (a configured
``OPENCODE_SERVER_USERNAME`` is ignored - the server always authenticates the
user ``opencode``) as an expected failure: it fails until upstream honours the
username, and an *unexpected success* is the signal to drop the xfail.

Fake values only, never a real credential; no password value is ever printed.

Run directly:

    python3 tests/test_opencode_auth_bind.py
"""
import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "configuration" / "docker" / "ai-stack" / "compose.yml"
AISTACK = ROOT / "configuration" / "docker" / "ai-stack" / "ai-stack.sh"
STACK_ENV_EXAMPLE = ROOT / "configuration" / "docker" / "ai-stack" / "stack.env.example"
WRAPPER = ROOT / "configuration" / "autostart" / "run-opencode-serve.sh"
WIN_SPAWNERS = (
    ROOT / "configuration" / "start-stack.ps1",
    ROOT / "configuration" / "autostart" / "Start-AutoOSStack.ps1",
)

# Fake values only. They are asserted on, so they must never be a real secret.
FAKE_USER = "autoos-test-user"
FAKE_PW = "fake-serve-password-0001"


def _read(path):
    # .ps1 keeps a UTF-8 BOM and CRLF in the working copy (AGENTS.md 6).
    return Path(path).read_text(encoding="utf-8-sig", errors="replace")


def _service_body(name):
    """The compose service block, parsed by the same helper the suite uses."""
    sys.path.insert(0, str(ROOT / "tests" / "helpers"))
    import check_compose  # noqa: PLC0415 (the repo has no package layout)
    body = check_compose.services(_read(COMPOSE)).get(name)
    if body is None:
        raise AssertionError("no service %s in compose.yml" % name)
    return body


def _healthcheck(body):
    m = re.search(r"^      test: .*$", body, re.M)
    if not m:
        raise AssertionError("the service has no healthcheck test line")
    return m.group(0)


class ComposeContractTests(unittest.TestCase):
    """compose.yml: the opencode publish address and its healthcheck."""

    def test_opencode_publishes_on_the_loopback_allowlist_only(self):
        """The published port is the control (D-2542): loopback by default,
        an explicit AUTOOS_OPENCODE_PUBLISH_IP from stack.env for a deliberate
        LAN/tail publish - never AUTOOS_STACK_BIND, never a bare 0.0.0.0."""
        body = _service_body("opencode")
        ports = re.findall(r'^      - "([^"]+)"$', body, re.M)
        self.assertEqual(
            ports, ["${AUTOOS_OPENCODE_PUBLISH_IP:-127.0.0.1}:4096:4096"],
            "opencode must publish on ${AUTOOS_OPENCODE_PUBLISH_IP:-127.0.0.1}:4096:4096, "
            "got %r" % ports)

    def test_the_other_services_keep_their_stack_bind(self):
        """Scope boundary: the change is opencode's exposure only - omniroute
        and openhands keep publishing through AUTOOS_STACK_BIND."""
        for name, port in (("omniroute", "20128"), ("openhands", "3000")):
            body = _service_body(name)
            self.assertIn(
                '"${AUTOOS_STACK_BIND:-0.0.0.0}:%s:%s"' % (port, port), body,
                "%s must keep the AUTOOS_STACK_BIND publish address" % name)

    def test_the_healthcheck_never_probes_the_open_root(self):
        """`curl http://127.0.0.1:4096/` is satisfied by any process that
        serves the static UI, authenticated or not. The probe must reach the
        guarded /api/session: credentialed when a password is configured, and
        otherwise the auth layer itself answering 401 (no password can be
        invented by the healthcheck - opencode generates one at start)."""
        line = _healthcheck(_service_body("opencode"))
        self.assertIn('"CMD-SHELL"', line, "the probe must be a shell test: %s" % line)
        self.assertIn("/api/session", line, "the probe must reach the guarded API: %s" % line)
        self.assertNotIn('4096/"', line, "the open root must not be the probe: %s" % line)
        self.assertRegex(line, r"OPENCODE_(SERVER_)?PASSWORD",
                         "the probe must authenticate with the configured password: %s" % line)
        self.assertIn("401", line, "without a password the auth layer's 401 must count as alive: %s" % line)


class StackEnvExampleTests(unittest.TestCase):
    def test_the_publish_ip_is_documented_with_a_loopback_default(self):
        text = _read(STACK_ENV_EXAMPLE)
        self.assertIn("AUTOOS_OPENCODE_PUBLISH_IP=127.0.0.1", text,
                      "stack.env.example must carry the opencode publish IP, loopback by default")
        self.assertNotIn("AUTOOS_OPENCODE_PUBLISH_IP=0.0.0.0", text,
                         "the documented default must never be a wildcard bind")


class SpawnerContractTests(unittest.TestCase):
    """Every way `opencode serve` is started by hand or at logon."""

    def _dry_run(self, extra=None):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available (the .sh suite covers this)")
        home = tempfile.mkdtemp(prefix="autoos-ocbind-")
        env = {k: v for k, v in os.environ.items() if k != "AUTOOS_OPENCODE_HOST"}
        env.update({
            "HOME": home,
            "XDG_CONFIG_HOME": home + "/.config",
            "AUTOOS_LITELLM_DIR": home + "/none",
            "AUTOOS_KEYS_FILE": home + "/none.yml",
            "AUTOOS_OPENCODE_PASSWORD_FILE": home + "/pw",
        })
        env.update(extra or {})
        try:
            return subprocess.run(
                [bash, str(WRAPPER), "--dry-run"], env=env, cwd=str(ROOT),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_the_wrapper_binds_loopback_by_default(self):
        """D-2549: the native wrapper's default bind is 127.0.0.1. A phone or
        proxy reaches it through a named address (AUTOOS_OPENCODE_HOST), never
        by way of a wildcard default."""
        proc = self._dry_run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("opencode serve --hostname 127.0.0.1 --port 4096", proc.stdout,
                      "the wrapper's dry-run plan must show a loopback bind: %s" % proc.stdout)

    def test_the_wrapper_refuses_a_wildcard_bind(self):
        """0.0.0.0 is never an answer: the root UI needs no password, so a
        wildcard bind hands every interface the agent's web UI."""
        proc = self._dry_run({"AUTOOS_OPENCODE_HOST": "0.0.0.0"})
        self.assertNotEqual(proc.returncode, 0,
                            "AUTOOS_OPENCODE_HOST=0.0.0.0 must be refused: %s" % proc.stdout)
        self.assertIn("0.0.0.0", proc.stdout + proc.stderr,
                      "the refusal must name the refused value")

    def test_the_wrapper_honours_an_explicit_allowlisted_address(self):
        """Loopback or an explicitly named address - the allowlist is the
        operator naming the address, in this shell or the unit's environment."""
        proc = self._dry_run({"AUTOOS_OPENCODE_HOST": "192.0.2.10"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("--hostname 192.0.2.10 --port 4096", proc.stdout,
                      "an explicit address must be used as given: %s" % proc.stdout)

    def test_both_windows_spawners_bind_loopback(self):
        """start-stack.ps1 -App opencode-serve and the logon resume helper."""
        for path in WIN_SPAWNERS:
            text = _read(path)
            with self.subTest(spawner=path.name):
                # assertTrue, not assertIn: a whole-file haystack in the
                # failure output buries the point of the assertion.
                self.assertTrue("'--hostname', '127.0.0.1'" in text,
                                "%s must spawn opencode on loopback" % path.name)
                self.assertFalse("'--hostname', '0.0.0.0'" in text,
                                 "%s must not spawn opencode on a wildcard" % path.name)


# A fake `opencode` that reports *which* password variables it received, as
# names and argv only - never a value (AGENTS.md: no password value is ever
# printed, fake or not, on a line a log collector could keep).
FAKE_OPENCODE_PW_REPORT = (
    "#!/bin/sh\n"
    'a="${OPENCODE_PASSWORD:-}"\n'
    'b="${OPENCODE_SERVER_PASSWORD:-}"\n'
    'if [ -n "$a" ] && [ "$a" = "$b" ]; then echo "pw-both-same $*";\n'
    'elif [ -n "$a" ]; then echo "pw-one-name $*";\n'
    'elif [ -n "$b" ]; then echo "pw-other-name $*";\n'
    'else echo "pw-none $*"; fi\n'
)

# The PATH shim that points a fake tool at tests/helpers/aistack_fake.sh.
# Like the fake above it is inert text until a test that already requires
# bash (they skipTest otherwise) writes it out - so it lives at module
# scope, not inside a test function.
SHIM_TEMPLATE = '#!/bin/sh\nexec bash "%s" "%s" %s "$@"\n'


class DualPasswordExportTests(unittest.TestCase):
    """Both password names, one source: `configuration/api-keys.yml` ->
    ``opencode_password``.

    Measured (D-2542): either name alone authenticates, and with both set
    ``OPENCODE_PASSWORD`` wins - so every surface that hands a password to
    opencode must hand it under *both* names, or a build reading only the
    other name would answer 401 to the value the operator was given.
    """

    def _run_wrapper(self, fake_body):
        """Run the native wrapper with a fake `opencode` in front of it."""
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available (the .sh suite covers this)")
        d = Path(tempfile.mkdtemp(prefix="autoos-ocpw-"))
        try:
            (d / "bin").mkdir()
            fake = d / "bin" / "opencode"
            fake.write_text(fake_body, encoding="utf-8")
            fake.chmod(0o755)
            keys = d / "keys.yml"
            keys.write_text("omniroute: sk-test-key\nopencode_password: '%s'\n"
                            % FAKE_PW, encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENCODE_PASSWORD", "OPENCODE_SERVER_PASSWORD",
                                "AUTOOS_KEYS_FILE", "AUTOOS_OPENCODE_PASSWORD_FILE",
                                "AUTOOS_OPENCODE_HOST", "AUTOOS_OPENCODE_PORT")}
            env.update({
                "HOME": str(d),
                "XDG_CONFIG_HOME": str(d / ".config"),
                "PATH": str(d / "bin") + os.pathsep + os.environ.get("PATH", ""),
                "AUTOOS_LITELLM_DIR": str(d / "lit"),
                "AUTOOS_KEYS_FILE": str(keys),
                "AUTOOS_OPENCODE_PASSWORD_FILE": str(d / "pw"),
            })
            return subprocess.run(
                [bash, str(WRAPPER)], env=env, cwd=str(ROOT),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_native_wrapper_exports_both_names_from_the_one_value(self):
        """run-opencode-serve.sh must export OPENCODE_PASSWORD *and*
        OPENCODE_SERVER_PASSWORD with the same api-keys.yml value, so a build
        reading either name logs in with the operator's password."""
        proc = self._run_wrapper(FAKE_OPENCODE_PW_REPORT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("pw-both-same serve --hostname 127.0.0.1 --port 4096",
                      proc.stdout,
                      "the wrapper must hand the one password under both names "
                      "and still bind loopback: %s" % proc.stdout)
        self.assertNotIn(FAKE_PW, proc.stdout + proc.stderr,
                         "a password value was printed")

    def test_the_container_env_carries_both_names_from_the_one_value(self):
        """ai-stack.sh init writes the pair into the opencode.env the compose
        service reads (env_file), so the container's own environment answers
        under either name - one source, no second secret to drift."""
        bash = shutil.which("bash")
        fake_sh = ROOT / "tests" / "helpers" / "aistack_fake.sh"
        if bash is None or not fake_sh.is_file():
            self.skipTest("bash + tests/helpers/aistack_fake.sh are needed "
                          "(the .sh suite covers this)")
        d = Path(tempfile.mkdtemp(prefix="autoos-ocinit-"))
        try:
            for sub in ("bin", "home/.config/opencode", "repo", "code"):
                (d / sub).mkdir(parents=True, exist_ok=True)
            for name in ("docker", "systemctl", "ss", "curl", "sleep",
                         "omniroute", "register", "start-stack", "start-litellm"):
                shim = d / "bin" / name
                shim.write_text(SHIM_TEMPLATE % (fake_sh, d, name),
                                encoding="utf-8")
                shim.chmod(0o755)
            for name, as_name in (("systemctl", "fake-systemctl"),
                                  ("register", "fake-register"),
                                  ("start-stack", "fake-start-stack")):
                (d / "bin" / name).rename(d / "bin" / as_name)
            keys = d / "repo" / "api-keys.yml"
            keys.write_text("omniroute: sk-test-client-key\nopencode_password: '%s'\n"
                            % FAKE_PW, encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("AUTOOS_OMNIROUTE_KEY", "OMNIGRAPH_TOKEN",
                                "AUTOOS_STACK_BIND", "OPENCODE_PASSWORD",
                                "OPENCODE_SERVER_PASSWORD", "AUTOOS_KEYS_FILE")}
            env.update({
                "HOME": str(d / "home"),
                "PATH": str(d / "bin") + os.pathsep + os.environ.get("PATH", ""),
                "AUTOOS_DOCKER": str(d / "bin" / "docker"),
                "AUTOOS_SYSTEMCTL": str(d / "bin" / "fake-systemctl"),
                "AUTOOS_AI_STACK_CONFIG": str(d / "cfg"),
                "AUTOOS_AI_STACK_DATA": str(d / "data"),
                "AUTOOS_CODE_DIR": str(d / "code"),
                "AUTOOS_KEYS_FILE": str(keys),
                "AUTOOS_LITELLM_DIR": str(d / "repo"),
                "AUTOOS_OMNIROUTE_HOME": str(d / "home" / ".omniroute"),
                "AUTOOS_OPENHANDS_DIR": str(d / "home" / ".openhands"),
                "XDG_CONFIG_HOME": str(d / "home" / ".config"),
                "AUTOOS_REGISTER_AUTOSTART": str(d / "bin" / "fake-register"),
                "AUTOOS_START_STACK": str(d / "bin" / "fake-start-stack"),
                "AUTOOS_START_LITELLM": str(d / "bin" / "start-litellm"),
            })
            proc = subprocess.run(
                [bash, str(AISTACK), "init"], env=env, cwd=str(ROOT),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            env_file = d / "cfg" / "opencode.env"
            self.assertTrue(env_file.is_file(),
                            "init wrote no opencode.env: %s" % proc.stdout)
            body = env_file.read_text(encoding="utf-8-sig", errors="replace")
            both = dict(re.findall(r"^(OPENCODE_(?:SERVER_)?PASSWORD)=(.*)$",
                                   body, re.M))
            self.assertIn("OPENCODE_PASSWORD", both,
                          "opencode.env has no OPENCODE_PASSWORD: %s" % sorted(both))
            self.assertIn("OPENCODE_SERVER_PASSWORD", both,
                          "opencode.env must carry OPENCODE_SERVER_PASSWORD too "
                          "(one source, both names): %s" % sorted(both))
            self.assertEqual(both["OPENCODE_PASSWORD"],
                             both["OPENCODE_SERVER_PASSWORD"],
                          "the two names must hold the one source value")
            self.assertNotIn(FAKE_PW, proc.stdout + proc.stderr,
                             "init printed a password value")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_windows_spawner_exports_both_names_from_the_one_value(self):
        """start-stack.ps1 -App opencode-serve reads `opencode_password` once
        and must publish it under both names, from that one value."""
        text = _read(ROOT / "configuration" / "start-stack.ps1")
        self.assertTrue("$env:OPENCODE_PASSWORD = $pw" in text,
                        "start-stack.ps1 must export OPENCODE_PASSWORD")
        self.assertTrue("$env:OPENCODE_SERVER_PASSWORD = $pw" in text,
                        "start-stack.ps1 must export OPENCODE_SERVER_PASSWORD "
                        "from the same $pw (one source, both names)")


def _opencode_exec():
    """The opencode server binary a probe may spawn - never a running session's.

    On POSIX the npm shim `exec`s the server, so the spawned pid *is* the
    server and terminating it frees the port. On Windows only the shim that
    resolves to a real .exe qualifies: killing the .cmd wrapper would leave
    its child holding the port.
    """
    exe = shutil.which("opencode")
    if not exe:
        return None
    if os.name != "nt":
        return exe
    for cand in (os.path.splitext(exe)[0], exe):
        try:
            text = Path(cand).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.startswith("#!"):
            m = re.search(r'exec "\$basedir/([^"]+)"', text)
            if m and m.group(1).lower().endswith(".exe"):
                return str(Path(exe).parent / m.group(1))
    return None


def _probe(port, path, user=None, pw=None):
    """The HTTP status of a probe, or None when the server did not answer."""
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    if pw is not None:
        token = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


@unittest.skipUnless(_opencode_exec(), "opencode is not installed on this PATH")
class UpstreamUsernameAnomalyTests(unittest.TestCase):
    """anomalyco/opencode#45856: a configured server username is ignored.

    Measured (D-2542) on 2.0.19 in-container and host builds 2.0.32/2.1.1:
    with ``OPENCODE_SERVER_USERNAME`` set, Basic auth as that user answers
    401 while the hard-coded user ``opencode`` answers 200 for the *same*
    password. So today the configured-username assertion below fails, and
    ``unittest.expectedFailure`` records it as an expected failure; when
    upstream honours the variable the assertion turns green and the run ends
    FAILED on an *unexpected success* - the signal to drop the xfail.

    Fake values only; no password value is ever printed. The probe spawns its
    own ``opencode serve`` on a free loopback port with an isolated HOME, and
    stops exactly that pid.
    """

    def _serve(self):
        exe = _opencode_exec()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        home = tempfile.mkdtemp(prefix="autoos-ocauth-")
        env = {k: v for k, v in os.environ.items()
               if k not in ("OPENCODE_PASSWORD", "OPENCODE_SERVER_PASSWORD",
                            "OPENCODE_SERVER_USERNAME")}
        env.update({
            "HOME": home,
            "XDG_CONFIG_HOME": home + "/.config",
            "XDG_DATA_HOME": home + "/.local/share",
            "XDG_STATE_HOME": home + "/.local/state",
            "OPENCODE_SERVER_USERNAME": FAKE_USER,
            "OPENCODE_PASSWORD": FAKE_PW,
        })
        kwargs = {"cwd": home, "env": env,
                  "stdout": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [exe, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            **kwargs)
        return proc, port, home

    @staticmethod
    def _stop(proc):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)

    # Fails today (upstream bug) - an unexpected SUCCESS ends the run FAILED:
    # that is the signal to drop this xfail when #45856 is fixed.
    @unittest.expectedFailure
    def test_a_configured_username_is_accepted(self):
        proc, port, home = self._serve()
        try:
            ready = False
            for _ in range(90):
                time.sleep(0.5)
                if _probe(port, "/") == 200:
                    ready = True
                    break
                if proc.poll() is not None:
                    break
            if not ready:
                self.skipTest("opencode serve did not become ready (rc=%s)"
                              % proc.poll())
            self.assertEqual(_probe(port, "/api/session"), 401,
                             "the unauthenticated probe must be refused, or "
                             "the username anomaly cannot be measured")
            got = _probe(port, "/api/session", FAKE_USER, FAKE_PW)
            self.assertEqual(
                got, 200,
                "Basic auth as OPENCODE_SERVER_USERNAME must be accepted "
                "(anomalyco/opencode#45856: the configured username is "
                "ignored; got HTTP %s)" % got)
        finally:
            self._stop(proc)
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
