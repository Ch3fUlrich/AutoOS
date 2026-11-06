"""Synthetic file-key regression, called by the spawner suite."""
from pathlib import Path
import os
import tempfile
from unittest import mock


def fake_key_environment(case):
    """Fence in-process fake key lookups and restore the caller's environment."""
    patch = mock.patch.dict(os.environ, {
        "AUTOOS_OMNIROUTE_URL": "https://gateway.example.invalid",
        "AUTOOS_HOST_NAME": "test-only",
    })
    patch.start()
    case.addCleanup(patch.stop)
    os.environ.pop("AUTOOS_NO_KEY_FILES", None)


def keyless_plan_ignores_fake_file(case, agent):
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp, "configuration")
        config.mkdir()
        (config / "api-keys.yml").write_text(
            "omniroute_server: test-only-fake\nomniroute: test-only-fake\n",
            encoding="utf-8")
        key_files = agent.key_files
        with mock.patch.dict(os.environ, {"AUTOOS_NO_KEY_FILES": "1"}):
            case.assertEqual(agent.key_files(tmp), [])
        with mock.patch.dict(os.environ):
            os.environ.pop("AUTOOS_NO_KEY_FILES", None)
            case.assertEqual(agent.key_files(tmp), [str(config / "api-keys.yml")])
        with mock.patch.object(agent, "key_files", lambda root: key_files(tmp)):
            r = case._dry(key=False)
        case.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        case.assertIn("--model %s" % agent.DEFAULT_FREE_MODEL, r.stdout)
        case.assertNotIn("AUTOOS_OMNIROUTE_KEY", case._env_line(r))
