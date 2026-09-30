#!/usr/bin/env python3
"""Tests for the gateway selection in tools/autoos-agent.py (gwloopback, 2026-09-30).

`GATEWAY` used to be the hard-coded `http://127.0.0.1:20128` - right on a host,
refused inside the stack's containers, where the gateway is a sibling container
and the compose network reaches it as `omniroute`
(configuration/docker/ai-stack/compose.yml spells the in-network address
"http://omniroute:20128"). The selection is now: an explicit
`AUTOOS_OMNIROUTE_URL` (the same override tools/autoos_usage.py already reads)
tried alone, else the docker DNS name with loopback as the fallback, and
`gateway_up()` is the pre-check that walks those candidates and rebinds
`GATEWAY` to the one that answers.

GWLOOPBACK-2 builds the spawned worker's side on top of that: `gateway_base_url()`
spells the resolved address the way the repo's provider block spells it (its API
root is `/v1`), for the `OPENCODE_CONFIG_CONTENT` overlay a worker's opencode
merges last - the sandbox clone's own `opencode.jsonc` pins loopback, which is
refused in-container, so without the stamp every gateway-path worker dies on its
first model call.

No test here contacts a gateway: candidates are pure strings, every probe is
injected, and the import test below asserts `urlopen` is not called while the
module loads - the suite's rule is that the gateway is never contacted.

Run directly, never through unittest discover:

    python3 tests/test_gateway_selection.py
"""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))


def load_agent(name="autoos_agent_gateway_tests"):
    """A fresh import of the tool, as every caller that spawns it gets one."""
    spec = importlib.util.spec_from_file_location(name, str(AGENT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent = load_agent()


class GatewayCandidateTests(unittest.TestCase):
    def test_an_override_is_the_only_candidate(self):
        """AUTOOS_OMNIROUTE_URL is the operator's answer: it is tried alone, so
        a deliberately dead endpoint (the shell suite points at
        http://127.0.0.1:1) can never be rescued by a live docker DNS
        candidate and turn an expected refusal into a run."""
        self.assertEqual(
            agent.gateway_candidates({"AUTOOS_OMNIROUTE_URL": "http://gw.override:20128/"}),
            ["http://gw.override:20128"])

    def test_without_an_override_the_candidates_are_docker_dns_then_loopback(self):
        self.assertEqual(agent.gateway_candidates({}),
                         [agent.GATEWAY_DOCKER, agent.GATEWAY_FALLBACK])

    def test_without_an_override_the_environment_is_read_when_none_is_passed(self):
        with mock.patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://gw.env:20128"}):
            self.assertEqual(agent.gateway_candidates(), ["http://gw.env:20128"])


class GatewayConstTests(unittest.TestCase):
    """The line-225 const: resolved, and resolved without I/O."""

    def test_the_const_is_the_override_when_one_is_set(self):
        self.assertEqual(agent.resolve_gateway({"AUTOOS_OMNIROUTE_URL": "http://gw:1/"}),
                         "http://gw:1")

    def test_the_const_is_the_docker_dns_name_without_one(self):
        self.assertEqual(agent.resolve_gateway({}), agent.GATEWAY_DOCKER)

    def test_the_names_of_the_three_addresses(self):
        """Pinned so a rename of the compose service or the env override is a
        deliberate edit here, not a silent reversion to loopback."""
        self.assertEqual(agent.GATEWAY_ENV_VAR, "AUTOOS_OMNIROUTE_URL")
        self.assertEqual(agent.GATEWAY_DOCKER, "http://omniroute:20128")
        self.assertEqual(agent.GATEWAY_FALLBACK, "http://127.0.0.1:20128")

    def test_importing_the_tool_never_touches_a_gateway(self):
        """Import must stay I/O-free: the suite imports this module in
        processes that must not contact a gateway, and a health GET per import
        would make every one of them depend on the host's stack."""
        env = {k: v for k, v in os.environ.items() if k != agent.GATEWAY_ENV_VAR}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("import contacted a gateway")):
                mod = load_agent("autoos_agent_gateway_no_io")
        self.assertEqual(mod.GATEWAY, mod.GATEWAY_DOCKER)

    def test_a_fresh_interpreter_resolves_the_const_the_same_way(self):
        """The production entry point: a caller that just imports the tool and
        reads the const, with and without an override in its environment."""
        code = ("import importlib.util, sys\n"
                "spec = importlib.util.spec_from_file_location('agent', sys.argv[1])\n"
                "mod = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(mod)\n"
                "print(mod.GATEWAY)\n")
        base = {k: v for k, v in os.environ.items() if k != agent.GATEWAY_ENV_VAR}

        def const_with(env):
            out = subprocess.run([sys.executable, "-c", code, str(AGENT)],
                                 env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.strip().splitlines()[-1]

        self.assertEqual(const_with(dict(base)), agent.GATEWAY_DOCKER)
        override = dict(base, AUTOOS_OMNIROUTE_URL="http://gw.override:20128/")
        self.assertEqual(const_with(override), "http://gw.override:20128")


class GatewayBaseUrlTests(unittest.TestCase):
    """GWLOOPBACK-2: the URL a worker's provider block is handed - the address
    this process resolved to (`GATEWAY` after the pre-check rebound it),
    spelled with the API root the repo's provider spells."""

    def test_the_container_answer_carries_the_repo_api_root(self):
        self.assertEqual(agent.gateway_base_url(agent.GATEWAY_DOCKER),
                         "http://omniroute:20128/v1")

    def test_the_host_fallback_keeps_loopback(self):
        # On a host the stamp spells what the file already says, so a host
        # worker's provider block is exactly what it was.
        self.assertEqual(agent.gateway_base_url(agent.GATEWAY_FALLBACK),
                         "http://127.0.0.1:20128/v1")

    def test_the_default_reads_the_rebound_gateway_at_call_time(self):
        # gateway_up() rebinds GATEWAY after the import-time guess; a caller
        # that passes nothing must get the post-probe address, not a copy of
        # the guess.
        with mock.patch.object(agent, "GATEWAY", "http://rebound:20128"):
            self.assertEqual(agent.gateway_base_url(), "http://rebound:20128/v1")

    def test_a_trailing_slash_never_doubles_the_api_root(self):
        self.assertEqual(agent.gateway_base_url("http://gw.override:20128/"),
                         "http://gw.override:20128/v1")

    def test_an_override_with_its_own_path_keeps_it(self):
        self.assertEqual(agent.gateway_base_url("http://gw.override:20128/openai/v1"),
                         "http://gw.override:20128/openai/v1")


class GatewayAnswersTests(unittest.TestCase):
    """The health GET itself: 200 is up, anything else - or a transport error -
    is down, and neither raises."""

    @staticmethod
    def _response(status):
        resp = mock.MagicMock()
        resp.__enter__.return_value.status = status
        return resp

    def test_200_is_up_and_the_health_path_is_what_is_asked(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=self._response(200)) as urlopen:
            self.assertTrue(agent.gateway_answers("http://gw:20128"))
        urlopen.assert_called_once_with("http://gw:20128/api/health", timeout=3)

    def test_a_non_200_is_down(self):
        with mock.patch("urllib.request.urlopen", return_value=self._response(503)):
            self.assertFalse(agent.gateway_answers("http://gw:20128"))

    def test_a_transport_error_is_down_and_never_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertFalse(agent.gateway_answers("http://127.0.0.1:20128"))


class GatewayUpPreCheckTests(unittest.TestCase):
    """`gateway_up()`: the pre-check that walks the candidates and corrects the
    import-time guess."""

    def setUp(self):
        self._gateway = agent.GATEWAY

    def tearDown(self):
        agent.GATEWAY = self._gateway

    def test_a_healthy_docker_dns_answer_is_kept(self):
        """The container case the fix exists for: docker DNS answers, loopback
        is never reached."""
        probed = []
        with mock.patch.object(agent, "gateway_answers",
                               lambda url, timeout=3:
                               probed.append(url) or url == agent.GATEWAY_DOCKER):
            self.assertTrue(agent.gateway_up())
        self.assertEqual(probed, [agent.GATEWAY_DOCKER])
        self.assertEqual(agent.GATEWAY, agent.GATEWAY_DOCKER)

    def test_a_host_where_loopback_answers_falls_back_from_docker_dns(self):
        """The host case: `omniroute` does not resolve outside the compose
        network, the second candidate answers, and GATEWAY is rebound to it so
        every later consumer in this process talks to the address that works."""
        probed = []
        with mock.patch.object(agent, "gateway_answers",
                               lambda url, timeout=3:
                               probed.append(url) or url == agent.GATEWAY_FALLBACK):
            self.assertTrue(agent.gateway_up())
        self.assertEqual(probed, [agent.GATEWAY_DOCKER, agent.GATEWAY_FALLBACK])
        self.assertEqual(agent.GATEWAY, agent.GATEWAY_FALLBACK)

    def test_nothing_answering_is_false_and_the_guess_is_left_alone(self):
        with mock.patch.object(agent, "gateway_answers", return_value=False) as probe:
            self.assertFalse(agent.gateway_up())
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(agent.GATEWAY, self._gateway)

    def test_an_override_is_never_second_guessed(self):
        """A dead override stays dead: cmd_run must still refuse with rc 3 the
        way the shell suite expects when AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1
        is set, even though a live gateway sits behind the docker DNS name."""
        probed = []
        with mock.patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1"}):
            with mock.patch.object(agent, "gateway_answers",
                                   lambda url, timeout=3:
                                   probed.append(url) or url == agent.GATEWAY_DOCKER):
                self.assertFalse(agent.gateway_up())
        self.assertEqual(probed, ["http://127.0.0.1:1"])
        self.assertEqual(agent.GATEWAY, self._gateway)

    def test_an_explicit_url_is_probed_alone_and_never_rebinds(self):
        with mock.patch.object(agent, "gateway_answers", return_value=True) as probe:
            self.assertTrue(agent.gateway_up("http://one-off:20128"))
        probe.assert_called_once_with("http://one-off:20128", 3)
        self.assertEqual(agent.GATEWAY, self._gateway)


if __name__ == "__main__":
    unittest.main()
