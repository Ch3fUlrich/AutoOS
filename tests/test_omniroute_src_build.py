#!/usr/bin/env python3
"""tests/test_omniroute_src_build.py - the gateway is built from pinned upstream source.

Hermetic: reads the recipe, the patch and the Dockerfile, and runs the script's
--dry-run against a fake `docker`. No clone, no build, no network.

The published OmniRoute image ships compiled chunks, which cannot take a new
executor, so configuration/docker/ai-stack/build-omniroute-src.sh builds the base
from the upstream tag with configuration/omniroute/acp-executor-<tag>.patch applied
(the generic ACP executor and the qoder-acp provider). These tests pin that the
recipe, the Dockerfile FROM, the compose tag and the patch name agree, that the
patch only touches upstream source (no private text), and that the script is
idempotent (a matching label means "skipped").
"""

import hashlib
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "configuration" / "docker" / "ai-stack"
SCRIPT = STACK / "build-omniroute-src.sh"
DOCKERFILE = STACK / "omniroute.Dockerfile"
COMPOSE = STACK / "compose.yml"
PATCH_DIR = ROOT / "configuration" / "omniroute"


def recipe():
    text = SCRIPT.read_text()
    return {k: re.search(r'^%s="([^"]*)"' % k, text, re.M).group(1) for k in ("TAG", "COMMIT", "REV")}


class SourceBuildRecipe(unittest.TestCase):
    def test_pin_is_a_tag_and_a_full_commit(self):
        r = recipe()
        self.assertRegex(r["TAG"], r"^v\d+\.\d+\.\d+$")
        self.assertRegex(r["COMMIT"], r"^[0-9a-f]{40}$")
        self.assertRegex(r["REV"], r"^acp\d+$")
        text = SCRIPT.read_text()
        self.assertIn('[[ "$got" == "$COMMIT" ]]', text, "the cloned commit must be verified")
        self.assertIn("--depth 1 --branch", text)
        self.assertIn("docker", text)
        self.assertNotIn("--no-verify", text)

    def test_dockerfile_from_matches_the_recipe(self):
        r = recipe()
        want = "FROM autoos/omniroute-src:%s-%s AS base" % (r["TAG"][1:], r["REV"])
        self.assertIn(want, DOCKERFILE.read_text().splitlines())

    def test_compose_tag_names_the_same_upstream_version(self):
        r = recipe()
        self.assertRegex(
            COMPOSE.read_text(),
            r"(?m)^    image: autoos/omniroute:%s-autoos\d+$" % re.escape(r["TAG"][1:]),
        )

    def test_qodercli_is_the_acp_capable_version(self):
        m = re.search(r"@qoder-ai/qodercli@(\d+)\.(\d+)\.(\d+)", DOCKERFILE.read_text())
        self.assertIsNotNone(m)
        self.assertGreaterEqual(tuple(int(x) for x in m.groups()), (1, 1, 69))

    def test_patch_exists_for_the_tag_and_touches_only_upstream_source(self):
        r = recipe()
        patch = PATCH_DIR / ("acp-executor-%s.patch" % r["TAG"])
        self.assertTrue(patch.is_file(), "patch for the pinned tag is missing")
        text = patch.read_text()
        files = re.findall(r"^diff --git a/(\S+) b/", text, re.M)
        self.assertIn("open-sse/executors/acp.ts", files)
        self.assertIn("open-sse/executors/acpProtocol.ts", files)
        self.assertIn("open-sse/executors/acpAgents.ts", files)
        for f in files:
            self.assertRegex(f, r"^(open-sse|src)/", "patch touches %s" % f)
        self.assertNotRegex(text, r"(?i)autoos|fleet|/home/|\bD-\d{3,}\b")

    def test_no_other_patch_for_a_stale_tag(self):
        r = recipe()
        stale = [p.name for p in PATCH_DIR.glob("acp-executor-*.patch") if p.name != "acp-executor-%s.patch" % r["TAG"]]
        self.assertEqual(stale, [])


@unittest.skipIf(os.name == "nt", "runs a POSIX shell script against a fake docker")
class SourceBuildScript(unittest.TestCase):
    def _run(self, label_matches):
        r = recipe()
        image = "autoos/omniroute-src:%s-%s" % (r["TAG"][1:], r["REV"])
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "docker"
            # `image inspect <img>` succeeds; with -f it prints the label (the wanted hash or stale)
            fake.write_text(
                "#!/bin/sh\n"
                'if [ "$1 $2" = "image inspect" ]; then\n'
                '  case "$3" in -f) echo "$LABEL_OUT" ;; esac\n'
                "  exit 0\n"
                "fi\n"
                'echo "unexpected docker $*" >&2; exit 9\n'
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            patch = PATCH_DIR / ("acp-executor-%s.patch" % r["TAG"])
            h = hashlib.sha256()
            h.update(("tag=%s\ncommit=%s\ntarget=runner-base\nrev=%s\n" % (r["TAG"], r["COMMIT"], r["REV"])).encode())
            h.update(patch.read_bytes())
            env = dict(os.environ, AUTOOS_DOCKER=str(fake), LABEL_OUT=h.hexdigest() if label_matches else "stale")
            return subprocess.run([str(SCRIPT), "--dry-run"], env=env, capture_output=True, text=True), image

    def test_up_to_date_image_is_skipped(self):
        res, image = self._run(True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("skipped", res.stdout)
        self.assertIn(image, res.stdout)

    def test_stale_label_would_rebuild(self):
        res, image = self._run(False)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("would rebuild", res.stdout)

    def test_unknown_argument_is_refused(self):
        res = subprocess.run([str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
