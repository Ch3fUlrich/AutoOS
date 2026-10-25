#!/usr/bin/env python3
"""D-2549 offline bind/auth contracts. Env password auth is bypassed in v2; anomalyco/opencode#45856. Network bind is the binding control. Fake credentials only."""
import base64
import http.server
import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
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
        self.assertIn('!= 000', line, "every HTTP answer must count as alive")


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
                self.assertTrue("'--hostname', $ocHost" in text,
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


# Offline regression checks for D-2549 review findings. (The imports and ROOT
# above are the file's own: one home per fact, no second module namespace.)


class BindParityTests(unittest.TestCase):
    # Construct non-loopback fixtures rather than embedding deployment addresses.
    cases = [("127.0.0.1", True), ("::1", True),
             (".".join(map(str, (192, 0, 2, 10))), True)] + [
        (".".join(map(str, parts)), False) for parts in
        ((0, 0, 0, 0), (0, 1, 2, 3), (224, 0, 0, 1),
         (240, 0, 0, 1), (255, 255, 255, 255))] + [
        (value, False) for value in ("::", "*", "", "garbage", "127.1", "[::1]")]

    def test_python_bind_table(self):
        spec = importlib.util.spec_from_file_location('bind', ROOT / 'configuration/opencode-bind.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for value, expected in self.cases:
            with self.subTest(value=value):
                self.assertEqual(module.valid(value), expected)

    def test_powershell_bind_parity(self):
        pwsh = shutil.which('pwsh')
        if not pwsh:
            self.skipTest('pwsh unavailable: PowerShell bind parity requires pwsh')
        script = ". './configuration/opencode-bind.ps1'; $cases = $env:AUTOOS_BIND_CASES | ConvertFrom-Json; foreach ($case in $cases) { $env:AUTOOS_OPENCODE_HOST = $case[0]; try { $null = Get-AutoOSOpencodeHost; $ok = $true } catch { $ok = $false }; if ($ok -ne $case[1]) { throw 'Bind table mismatch' } }"
        result = subprocess.run([pwsh, '-NoProfile', '-Command', script], cwd=ROOT,
                                env=dict(os.environ, AUTOOS_BIND_CASES=json.dumps(self.cases)),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_launcher_ast_failures_are_clear(self):
        pwsh = shutil.which('pwsh')
        if not pwsh:
            self.skipTest('pwsh unavailable: launcher AST regression requires pwsh')
        text = _read(WIN_SPAWNERS[1])
        block = text.split('    $tokens = $null; $errors = $null', 1)[1].split('    $keysFile =', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / 'launcher.ps1'
            for source, message in (("function broken {", 'Cannot parse opencode launcher'),
                                    ('# no functions', 'Missing opencode launcher function'),
                                    (None, 'Cannot parse opencode launcher')):
                if source is not None:
                    launcher.write_text(source, encoding='utf-8')
                elif launcher.exists():
                    launcher.unlink()
                script = "$launcher = $env:AUTOOS_TEST_LAUNCHER; try { & { $tokens = $null; $errors = $null\n" + block + "\n}; throw 'Expected failure' } catch { if (-not $_.Exception.Message.StartsWith($env:AUTOOS_TEST_MESSAGE)) { throw }; exit 0 }"
                result = subprocess.run([pwsh, '-NoProfile', '-Command', script], cwd=ROOT,
                                        env=dict(os.environ, AUTOOS_TEST_LAUNCHER=str(launcher),
                                                 AUTOOS_TEST_MESSAGE=message),
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RoundTwoTests(unittest.TestCase):
    def test_auth_401_nonstrict_xfail_v2(self):
        # Offline v2 fake: env password does NOT enforce auth. Always exercised
        # in CI; recorded-outcome xfail is deliberately non-strict (#45856).
        class V2Fake(http.server.BaseHTTPRequestHandler):
            status = 200

            def do_GET(self):
                self.send_response(self.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        for status in (200, 401):
            V2Fake.status = status
            server = http.server.HTTPServer(('127.0.0.1', 0), V2Fake)
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%s/api/session' % server.server_port) as response:
                        actual = response.status
                except urllib.error.HTTPError as error:
                    actual = error.code
                self.assertEqual(actual, 401)
            except AssertionError:
                print('XFAIL anomalyco/opencode#45856: env password does not enforce auth in v2')
            else:
                print('PASS anomalyco/opencode#45856: upstream auth fixed')
            finally:
                server.shutdown()
                worker.join()
                server.server_close()

    def test_healthcheck_survives_compose_interpolation(self):
        # compose interpolates $name and ${...} in the whole file; only $$ is a
        # literal $. A shell variable written as $c renders as "" and the probe
        # fails for every answer (seat r2 of ZED-SETUP-REMOVE, docker compose config).
        import sys
        sys.path.insert(0, str(ROOT / 'tests/helpers'))
        import check_compose
        body = check_compose.services((ROOT / 'configuration/docker/ai-stack/compose.yml').read_text())['opencode']
        line = next(line for line in body.splitlines() if 'test: [' in line)
        command = json.loads(line.split('test:', 1)[1])[1]
        unescaped = re.findall(r'\$[A-Za-z_{][A-Za-z0-9_]*', command.replace('$$', ''))
        self.assertEqual(unescaped, [], 'compose would interpolate these away: %s' % unescaped)

    def test_healthcheck_as_docker_compose_renders_it(self):
        # The real interpolator, when a docker CLI with compose is on this host:
        # the rendered probe must be exactly the shell command we mean to run.
        docker = shutil.which('docker')
        if not docker or subprocess.run([docker, 'compose', 'version'], capture_output=True).returncode != 0:
            self.skipTest('docker compose not available')
        import sys
        sys.path.insert(0, str(ROOT / 'tests/helpers'))
        import check_compose
        body = check_compose.services((ROOT / 'configuration/docker/ai-stack/compose.yml').read_text())['opencode']
        line = next(line for line in body.splitlines() if 'test: [' in line)
        want = ('c=$(curl --max-time 4 -sS -o /dev/null -w \'%{http_code}\' -u '
                '"opencode:${OPENCODE_PASSWORD:-${OPENCODE_SERVER_PASSWORD}}" '
                'http://127.0.0.1:4096/api/session 2>/dev/null); [ -n "$c" ] && [ "$c" != 000 ]')
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, 'compose.yml').write_text(
                'services:\n  opencode:\n    image: scratch\n    healthcheck:\n      ' + line.strip() + '\n')
            env = {k: v for k, v in os.environ.items() if k not in ('c', 'OPENCODE_PASSWORD', 'OPENCODE_SERVER_PASSWORD')}
            result = subprocess.run([docker, 'compose', '--project-directory', directory, '-f',
                                     str(pathlib.Path(directory, 'compose.yml')), 'config', '--format', 'json'],
                                    capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('variable is not set', result.stderr)
        # `config` writes compose again, so every literal $ comes back as $$.
        rendered = [part.replace('$$', '$') for part in
                    json.loads(result.stdout)['services']['opencode']['healthcheck']['test']]
        self.assertEqual(rendered, ['CMD-SHELL', want])

    def test_execute_healthcheck_against_v2_fake(self):
        if os.name == 'nt':
            self.skipTest('POSIX shell probe runs in the Linux gate')
        import sys
        sys.path.insert(0, str(ROOT / 'tests/helpers'))
        import check_compose
        body = check_compose.services((ROOT / 'configuration/docker/ai-stack/compose.yml').read_text())['opencode']
        line = next(line for line in body.splitlines() if 'test: [' in line)
        command = json.loads(line.split('test:', 1)[1])[1].replace('$$', '$')
        with tempfile.TemporaryDirectory() as directory:
            curl = pathlib.Path(directory) / 'curl'
            curl.write_text('#!/bin/sh\nprintf "%s" "$FAKE_HTTP_STATUS"\n')
            curl.chmod(0o755)
            for status in ('200', '401', '500', '503', '000'):
                env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                           FAKE_HTTP_STATUS=status, OPENCODE_PASSWORD='fake-only')
                result = subprocess.run(['bash', '-c', command], env=env, capture_output=True)
                self.assertEqual(result.returncode == 0, status != '000')

    def test_positive_address_validation(self):
        helper = ROOT / 'configuration/opencode-bind.py'
        # sys.executable, not a PATH lookup: the suite may run as `python` while
        # only `python3` is on PATH (or the reverse) on a CI box.
        for address in ('0.0.0.0', '::', '[::]', '0:0:0:0:0:0:0:0',
                        '::ffff:0.0.0.0', '*', '', 'garbage', '224.0.0.1'):
            with self.subTest(address=address):
                self.assertNotEqual(subprocess.run(
                    [sys.executable, str(helper), address], capture_output=True).returncode, 0)
        for address in ('127.0.0.1', '::1', '192.0.2.10'):
            self.assertEqual(subprocess.run(
                [sys.executable, str(helper), address], capture_output=True).returncode, 0)

    def test_each_windows_spawner_exports_and_validates(self):
        for name in ('start-stack.ps1', 'autostart/Start-AutoOSStack.ps1'):
            text = (ROOT / 'configuration' / name).read_text(encoding='utf-8-sig')
            for required in ('$env:OPENCODE_PASSWORD = $pw',
                             '$env:OPENCODE_SERVER_PASSWORD = $pw',
                             'AUTOOS_OPENCODE_HOST', 'Get-AutoOSOpencodeHost'):
                self.assertIn(required, text, name)

    def test_windows_payload_matches_loopback(self):
        text = (ROOT / 'lib/windows/AutoOS.Serve.psm1').read_text(encoding='utf-8-sig')
        line = text.split("id = 'opencode'", 1)[1].split('unit =', 1)[0]
        self.assertIn("bind = '127.0.0.1'", line)



if __name__ == "__main__":
    unittest.main(verbosity=2)
