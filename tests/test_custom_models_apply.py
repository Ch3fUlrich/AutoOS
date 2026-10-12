#!/usr/bin/env python3
"""AO-MIMO-CUSTOM-APPLY: the declared custom models are applied by both apply paths.

WHY. OmniRoute 3.8.50 ships provider `xiaomi-mimo` with a static, image-baked
list (mimo-v2.5-pro, mimo-v2.5), no live discovery, and the synced catalog is
authoritative — so mimo-v2.6-* answered 400 "not available in the active live
catalog". The fix is a declared override: configuration/omniroute/
custom-models.json, registered as manual models by apply-custom-models.sh. This
test pins the three things that make that fix hold on a real machine:

  (1) the file's SHAPE — a row the applier cannot build a body from is a row that
      silently never gets declared, so every row must carry provider/id/name and
      positive integer limits, with no duplicate id under one provider;
  (2) the applier's BEHAVIOUR against a gateway — it POSTs only the ids the
      gateway does not hold, a second run changes nothing, --dry-run writes
      nothing, a live run without a fleet-rule D-825 GO refuses, and the manage
      key never reaches stdout;
  (3) the WIRING — apply.sh (Linux) and apply.ps1 (Windows) both run the step,
      after the catalog refresh and BEFORE the live catalog is read for combo
      validation (the same ordering rule that fixed the fresh-machine drop at
      L0 2026-10-01T19:07Z: read-then-register leaves the first run blind), and
      apply.sh's read-only --drift path exits before the step.

HERMETIC (AGENTS.md section 5): no test here opens the real gateway. Every run
points AUTOOS_OMNIROUTE_URL at a fake http.server this test owns, on loopback,
on a port the kernel picked; the ordering checks read the two apply scripts as
text and never execute them.

Stdlib-only, path-independent (ROOT from __file__), run directly — like every
other suite in this repo:
    python3 tests/test_custom_models_apply.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
OMNIROUTE = ROOT / "configuration" / "omniroute"
CUSTOM_FILE = OMNIROUTE / "custom-models.json"
APPLIER = OMNIROUTE / "apply-custom-models.sh"
APPLY_SH = OMNIROUTE / "apply.sh"
APPLY_PS1 = OMNIROUTE / "apply.ps1"

# The applier is a bash script that needs curl; the suite runs on Windows too,
# where neither is guaranteed. The text checks on both apply paths still run.
BASH = shutil.which("bash")
CURL = shutil.which("curl")

# A decision id of the right shape; --go-offline waives the artefact lookup, so
# the run never reads the routing decisions log of whatever host runs the test.
GO_REF = "D-1184"
FAKE_KEY = "fake-manage-key-not-a-real-secret"


def _rows():
    return json.loads(CUSTOM_FILE.read_text(encoding="utf-8"))["models"]


def _git_head():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


class FakeGateway:
    """The /api/provider-models surface, backed by a mutable set of ids.

    GET answers from what it "holds", POST records the body and adds the id —
    exactly the contract apply-custom-models.sh depends on for its no-op
    re-run. Bound to 127.0.0.1 on port 0, so nothing can reach the real one.
    """

    def __init__(self, held=None):
        self.held = {provider: list(ids) for provider, ids in (held or {}).items()}
        self.posts = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, doc):
                body = json.dumps(doc).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 - http.server's name
                provider = ""
                if "provider=" in self.path:
                    provider = self.path.split("provider=", 1)[1].split("&", 1)[0]
                self._send(200, {"models": [{"id": mid} for mid in gateway.held.get(provider, [])]})

            def do_POST(self):  # noqa: N802 - http.server's name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    record = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    self._send(400, {"error": "not json"})
                    return
                gateway.posts.append(record)
                gateway.held.setdefault(record.get("provider", ""), []).append(record.get("modelId"))
                self._send(200, {"ok": True})

            def log_message(self, *args):  # keep the suite's output clean
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)


def run_applier(args, url, key=FAKE_KEY, extra_env=None):
    """One apply-custom-models.sh run against `url`, with the environment copied."""
    env = dict(os.environ)
    env["AUTOOS_OMNIROUTE_URL"] = url
    env["OMNIROUTE_API_KEY"] = key
    # The repo's own declaration, whatever the developer's shell points at.
    env["AUTOOS_CUSTOM_MODELS_FILE"] = str(CUSTOM_FILE)
    env.update(extra_env or {})
    proc = subprocess.run([BASH, str(APPLIER)] + list(args),
                          cwd=str(ROOT), env=env,
                          capture_output=True, text=True)
    # `output` is what an operator sees: both streams, the only place a secret
    # or a report line can turn up.
    return type("Run", (), {"returncode": proc.returncode, "stdout": proc.stdout,
                            "stderr": proc.stderr, "output": proc.stdout + proc.stderr})()


HEAD = _git_head()
LIVE_FLAGS = ["--go", GO_REF, "--go-sha", HEAD, "--go-offline"]


class CustomModelsFileShapeTests(unittest.TestCase):
    def test_file_exists_and_declares_models(self):
        self.assertTrue(CUSTOM_FILE.is_file(), "%s is missing" % CUSTOM_FILE)
        self.assertGreater(len(_rows()), 0, "custom-models.json declares no model")

    def test_every_row_names_provider_id_and_name(self):
        for row in _rows():
            for field in ("provider", "id", "name"):
                self.assertIsInstance(row.get(field), str, "%r: %s is not a string" % (row, field))
                self.assertTrue(row[field].strip(), "%r: %s is empty" % (row, field))

    def test_every_row_carries_positive_integer_limits(self):
        for row in _rows():
            for field in ("max_input_tokens", "max_output_tokens"):
                value = row.get(field)
                self.assertIsInstance(value, int, "%s: %s is not an int" % (row["id"], field))
                self.assertNotIsInstance(value, bool, "%s: %s is a bool, not a limit" % (row["id"], field))
                self.assertGreater(value, 0, "%s: %s must be positive" % (row["id"], field))

    def test_ids_are_unique_within_a_provider(self):
        seen = []
        for row in _rows():
            key = (row["provider"], row["id"])
            self.assertNotIn(key, seen, "duplicate declaration for %s/%s" % key)
            seen.append(key)


@unittest.skipIf(BASH is None or CURL is None, "bash/curl unavailable: the applier cannot run")
class ApplierAgainstFakeGatewayTests(unittest.TestCase):
    @unittest.skipUnless(HEAD, "no git HEAD to name in --go-sha")
    def test_first_run_posts_only_the_missing_ids(self):
        rows = _rows()
        held_id = rows[0]["id"]
        with FakeGateway({rows[0]["provider"]: [held_id]}) as gw:
            proc = run_applier(LIVE_FLAGS, gw.url)
            self.assertEqual(proc.returncode, 0, proc.output)
            expected = [row["id"] for row in rows if row["id"] != held_id]
            self.assertEqual([post["modelId"] for post in gw.posts], expected,
                             "posted the wrong set of ids: %s" % proc.output)
            for post in gw.posts:
                self.assertEqual(post["source"], "manual", "%s not declared manual" % post["modelId"])
                row = next(r for r in rows if r["id"] == post["modelId"])
                self.assertEqual(post["provider"], row["provider"])
                self.assertEqual(post["modelName"], row["name"])
                self.assertEqual(post["max_input_tokens"], row["max_input_tokens"])
                self.assertEqual(post["max_output_tokens"], row["max_output_tokens"])
                self.assertEqual(post["supportsVision"], row["supportsVision"])
            self.assertIn("  = %s/%s already present" % (rows[0]["provider"], held_id), proc.output)
            for mid in expected:
                self.assertIn("  + %s/%s added" % (rows[0]["provider"], mid), proc.output)

    @unittest.skipUnless(HEAD, "no git HEAD to name in --go-sha")
    def test_second_run_changes_nothing(self):
        rows = _rows()
        with FakeGateway() as gw:
            self.assertEqual(run_applier(LIVE_FLAGS, gw.url).returncode, 0)
            first_posts = len(gw.posts)
            self.assertGreater(first_posts, 0, "the first run posted nothing — nothing was proved")
            second = run_applier(LIVE_FLAGS, gw.url)
            self.assertEqual(second.returncode, 0, second.output)
            self.assertEqual(len(gw.posts), first_posts, "a re-run POSTed again")
            for row in rows:
                self.assertIn("  = %s/%s already present" % (row["provider"], row["id"]), second.output)

    def test_dry_run_posts_nothing(self):
        rows = _rows()
        with FakeGateway() as gw:
            proc = run_applier(["--dry-run"], gw.url)
            self.assertEqual(proc.returncode, 0, proc.output)
            self.assertEqual(gw.posts, [], "--dry-run wrote to the gateway")
            for row in rows:
                self.assertIn("  - would add %s/%s" % (row["provider"], row["id"]), proc.output)

    def test_live_run_without_a_go_refuses(self):
        with FakeGateway() as gw:
            proc = run_applier([], gw.url)
            self.assertEqual(proc.returncode, 2, proc.output)
            self.assertEqual(gw.posts, [], "a run with no GO mutated the gateway")
            self.assertIn("D-825", proc.output)

    @unittest.skipUnless(HEAD, "no git HEAD to name in --go-sha")
    def test_manage_key_is_never_printed(self):
        with FakeGateway() as gw:
            for args in (["--dry-run"], LIVE_FLAGS):
                proc = run_applier(args, gw.url)
                self.assertNotIn(FAKE_KEY, proc.output,
                                 "the manage key leaked into the run's output")


class ApplyPathWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sh = APPLY_SH.read_text(encoding="utf-8")
        cls.ps1 = APPLY_PS1.read_text(encoding="utf-8")

    def test_apply_sh_runs_the_step_after_the_catalog_refresh(self):
        for marker in ("echo \"Catalog:\"", "echo \"Custom models:\"", 'live_ids=""'):
            self.assertIn(marker, self.sh, "apply.sh no longer contains %r" % marker)
        self.assertLess(self.sh.index('echo "Catalog:"'), self.sh.index('echo "Custom models:"'),
                        "the custom step runs before the catalog refresh")
        self.assertLess(self.sh.index('echo "Custom models:"'), self.sh.index('live_ids=""'),
                        "the custom step must run BEFORE the combos read the live catalog")

    def test_apply_sh_forwards_the_gate_flags_and_never_exports_the_key(self):
        self.assertIn('bash "$HERE/apply-custom-models.sh"', self.sh)
        self.assertIn('_custom_args=(--dry-run)', self.sh)
        self.assertIn('_custom_args=(--go "$GO_REF" --go-sha "$GO_SHA")', self.sh)
        self.assertIn('_custom_args+=(--go-offline)', self.sh)
        step = self.sh[self.sh.index('echo "Custom models:"'):
                       self.sh.index("# TORDER 2026-10-01: combo-contract gate")]
        # The key is resolved by the applier itself (inherited env var, else the
        # ai-stack manage.key). An assignment prefix onto `bash` would export it
        # to the applier's whole process tree — curl included, which takes it
        # from a private 0600 config file instead. apply.sh keeps the manage key
        # out of every environment but the CLI's; tests/linux/34-ai-services.sh
        # proves it with a stand-in curl that reports any tool that could see it.
        self.assertNotIn('export OMNIROUTE_API_KEY', step)
        self.assertNotRegex(step, r'OMNIROUTE_API_KEY=.*bash "\$HERE/apply-custom-models\.sh"')
        # And its failure is announced, not fatal — like every other step.
        self.assertRegex(step, r'if ! bash "\$HERE/apply-custom-models\.sh" "\$\{_custom_args\[@\]\}"; then')

    def test_apply_sh_drift_exits_before_the_step(self):
        self.assertLess(self.sh.index('if [[ $DRIFT -eq 1 ]]; then'),
                        self.sh.index('echo "Custom models:"'),
                        "--drift is read-only: it must not reach the custom step")

    def test_apply_ps1_has_the_block_before_the_combo_catalog_read(self):
        self.assertIn("Write-Host 'Catalog:'", self.ps1)
        self.assertIn("Write-Host 'Custom models:'", self.ps1)
        self.assertIn("# --- Live catalog for validation ---", self.ps1)
        self.assertLess(self.ps1.index("Write-Host 'Catalog:'"),
                        self.ps1.index("Write-Host 'Custom models:'"))
        self.assertLess(self.ps1.index("Write-Host 'Custom models:'"),
                        self.ps1.index("# --- Live catalog for validation ---"),
                        "apply.ps1 must declare custom ids before it reads the live catalog")

    def test_apply_ps1_posts_the_same_body_shape_as_the_shell(self):
        block = self.ps1[self.ps1.index("Write-Host 'Custom models:'"):
                         self.ps1.index("# --- Live catalog for validation ---")]
        self.assertIn('-Path "/api/provider-models?provider=$customProvider"', block)
        self.assertIn("-Method POST -Path '/api/provider-models'", block)
        self.assertIn("source = 'manual'", block)
        for field in ("modelId", "modelName", "max_input_tokens", "max_output_tokens", "supportsVision"):
            self.assertIn(field, block, "apply.ps1 dropped %s from the body" % field)
        # The same three report lines the .sh prints, so one operator sees one
        # shape of answer on either platform.
        for line in ("already present", "added", "would add"):
            self.assertIn(line, block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
