#!/usr/bin/env python3
"""Tests for tools/smart-ci/selector.py (CI-SMART-SELECT).

The selector turns a diff plus a per-repo map into the jobs and tests to run. The
properties that make it safe to trust:

  * a diff that only touches mapped paths selects exactly the mapped jobs/tests;
  * a workflow, dependency or CI file change, an unknown path, an empty diff, a
    broken map, an unreadable diff or an explicit --full all fall back to FULL;
  * it never raises: the CLI always exits 0 with a JSON answer.

Run directly:

    python3 tests/test_smart_ci.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "smart-ci" / "selector.py"
_spec = importlib.util.spec_from_file_location("smart_ci_selector", str(SCRIPT))
sel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sel)

MAP = {
    "version": 1,
    "jobs": ["lint", "pytest", "pg", "rust"],
    "always_jobs": ["lint"],
    "ignore": ["docs/**", "**/*.md"],
    "rules": [
        {"paths": ["src/api/**"], "tests": ["tests/test_api.py"], "jobs": ["pytest"]},
        {"paths": ["src/db/**"], "tests": ["tests/test_db.py"], "jobs": ["pytest", "pg"]},
        {"paths": ["tests/test_*.py"], "tests": ["{path}"], "jobs": ["pytest"]},
        {"paths": ["crates/**"], "jobs": ["rust"]},
    ],
}


def run(files, **kw):
    return sel.select(files, sel.validate_map(MAP), **kw)


class SelectionTests(unittest.TestCase):
    def test_mapped_path_selects_its_tests_and_jobs_plus_always_jobs(self):
        r = run(["src/api/routes.py"])
        self.assertEqual(r["mode"], "selected")
        self.assertEqual(r["tests"], ["tests/test_api.py"])
        self.assertEqual(r["jobs"], {"lint": True, "pytest": True, "pg": False, "rust": False})

    def test_heavy_job_runs_only_when_its_own_paths_changed(self):
        self.assertFalse(run(["src/api/a.py"])["jobs"]["pg"])
        self.assertTrue(run(["src/db/a.py"])["jobs"]["pg"])
        self.assertFalse(run(["src/db/a.py"])["jobs"]["rust"])
        self.assertTrue(run(["crates/core/lib.rs"])["jobs"]["rust"])

    def test_union_over_several_files_without_duplicates(self):
        r = run(["src/api/a.py", "src/db/b.py", "src/db/c.py"])
        self.assertEqual(r["tests"], ["tests/test_api.py", "tests/test_db.py"])

    def test_a_changed_test_file_selects_itself(self):
        self.assertEqual(run(["tests/test_new.py"])["tests"], ["tests/test_new.py"])

    def test_docs_only_selects_only_always_jobs(self):
        r = run(["docs/a/b.md", "README.md"])
        self.assertEqual(r["mode"], "none")
        self.assertEqual(r["jobs"], {"lint": True, "pytest": False, "pg": False, "rust": False})
        self.assertEqual(r["tests"], [])

    def test_docs_plus_code_ignores_the_docs(self):
        r = run(["README.md", "src/api/a.py"])
        self.assertEqual(r["mode"], "selected")
        self.assertEqual(r["tests"], ["tests/test_api.py"])

    def test_a_selected_test_file_missing_at_head_is_full(self):
        r = run(["tests/test_gone.py"], exists=lambda p: False)
        self.assertEqual(r["mode"], "full")
        self.assertIn("missing at HEAD", r["reason"])
        self.assertEqual(run(["tests/test_here.py"], exists=lambda p: True)["tests"], ["tests/test_here.py"])
        # explicit (non-{path}) tests are the map author's names and are not probed
        self.assertEqual(run(["src/api/a.py"], exists=lambda p: False)["mode"], "selected")

    def test_backslash_paths_are_normalised(self):
        self.assertEqual(run(["src\\api\\a.py"])["mode"], "selected")


class FullFallbackTests(unittest.TestCase):
    def assertFull(self, r, why):
        self.assertEqual(r["mode"], "full")
        self.assertIn(why, r["reason"])
        self.assertTrue(all(r["jobs"].values()))
        self.assertEqual(sorted(r["jobs"]), sorted(MAP["jobs"]))
        self.assertEqual(r["tests"], [])

    def test_workflow_change_is_full(self):
        self.assertFull(run([".github/workflows/ci.yml", "docs/x.md"]), "workflow/dependency/CI")

    def test_dependency_files_are_full(self):
        for f in ["requirements.txt", "svc/requirements-dev.txt", "pyproject.toml", "uv.lock",
                  "Cargo.toml", "crates/a/Cargo.lock", "package-lock.json", "go.mod",
                  "deploy/Dockerfile", "deploy/Dockerfile.runner"]:
            self.assertFull(run(["src/api/a.py", f]), "workflow/dependency/CI")

    def test_the_map_and_the_selector_themselves_are_full(self):
        self.assertFull(run([".github/smart-ci.json"]), "workflow/dependency/CI")
        self.assertFull(run(["tools/smart-ci/selector.py"]), "workflow/dependency/CI")

    def test_shard_plan_inputs_and_validator_are_full(self):
        for path in ("tests/ci-shards.py", "tests/ci-shards.txt", "tests/test_ci_shards.py"):
            with self.subTest(path=path):
                self.assertFull(run([path]), "workflow/dependency/CI")

    def test_unknown_path_is_full(self):
        self.assertFull(run(["src/api/a.py", "scripts/new-thing.sh"]), "unknown path")

    def test_empty_diff_is_full(self):
        self.assertFull(run([]), "empty diff")

    def test_explicit_full_label_or_dispatch(self):
        self.assertFull(run(["docs/a.md"], force_full=True, force_reason="label merge-ready"), "label merge-ready")

    def test_no_map_is_full(self):
        r = sel.select(["a.py"], None)
        self.assertEqual((r["mode"], r["tests"]), ("full", []))

    def test_map_can_add_full_triggers(self):
        m = dict(MAP, full_if=["src/core/**"])
        r = sel.select(["src/core/x.py"], sel.validate_map(m))
        self.assertEqual(r["mode"], "full")


class MapValidationTests(unittest.TestCase):
    def bad(self, m):
        with self.assertRaises(sel.MapError):
            sel.validate_map(m)

    def test_rejects_unknown_keys_jobs_and_empty_paths(self):
        self.bad({"surprise": 1})
        self.bad({"jobs": ["a"], "rules": [{"paths": ["x"], "jobs": ["b"]}]})
        self.bad({"jobs": ["a"], "always_jobs": ["b"]})
        self.bad({"rules": [{"paths": []}]})
        self.bad([])

    def test_rejects_non_string_rule_paths(self):
        self.bad({"rules": [{"paths": [1]}]})


class GlobTests(unittest.TestCase):
    def test_glob_semantics(self):
        m = sel.matches
        self.assertTrue(m("README.md", ["**/*.md"]))
        self.assertTrue(m("a/b/c.md", ["**/*.md"]))
        self.assertTrue(m("src/a/b.py", ["src/**"]))
        self.assertTrue(m("src/a.py", ["src/*.py"]))
        self.assertFalse(m("src/a/b.py", ["src/*.py"]))
        self.assertFalse(m("xsrc/a.py", ["src/**"]))
        self.assertTrue(m("tests/test_a.py", ["tests/test_*.py"]))
        self.assertFalse(m("a.pyc", ["*.py"]))


class CliTests(unittest.TestCase):
    def cli(self, *args, env=None):
        e = dict(os.environ)
        e.pop("GITHUB_OUTPUT", None)
        e.update(env or {})
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, env=e)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.map = os.path.join(self.tmp.name, "map.json")
        Path(self.map).write_text(json.dumps(MAP), encoding="utf-8")

    def test_files_mode(self):
        r = self.cli("--map", self.map, "--files", "src/db/x.py")
        self.assertEqual((r["mode"], r["jobs"]["pg"]), ("selected", True))

    def test_broken_map_falls_back_to_full_with_exit_zero(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        Path(bad).write_text("{not json", encoding="utf-8")
        r = self.cli("--map", bad, "--files", "src/db/x.py")
        self.assertEqual(r["mode"], "full")
        self.assertIn("map unusable", r["reason"])

    def test_missing_map_falls_back_to_full(self):
        r = self.cli("--map", os.path.join(self.tmp.name, "nope.json"), "--files", "a.py")
        self.assertEqual(r["mode"], "full")

    def test_full_flag_wins(self):
        r = self.cli("--map", self.map, "--files", "docs/a.md", "--full", "--full-reason", "dispatch full=true")
        self.assertEqual((r["mode"], r["reason"]), ("full", "dispatch full=true"))

    def test_unreadable_diff_is_full(self):
        r = self.cli("--map", self.map, "--base", "0" * 40, "--head", "HEAD")
        self.assertEqual(r["mode"], "full")
        self.assertIn("selection failed", r["reason"])

    def test_no_diff_source_is_full(self):
        self.assertEqual(self.cli("--map", self.map)["mode"], "full")

    def test_real_git_diff(self):
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, "src", "api"))
        g = lambda *a: subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                      capture_output=True, text=True, check=True).stdout.strip()
        g("init", "-q")
        Path(repo, "src", "api", "a.py").write_text("1\n")
        g("add", "-A")
        g("commit", "-q", "-m", "one")
        base = g("rev-parse", "HEAD")
        Path(repo, "src", "api", "a.py").write_text("2\n")
        g("commit", "-qam", "two")
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--map", self.map, "--base", base, "--head", "HEAD"],
                           capture_output=True, text=True, cwd=repo)
        r = json.loads(p.stdout)
        self.assertEqual((r["mode"], r["tests"]), ("selected", ["tests/test_api.py"]))

    def test_github_output_lines(self):
        out = os.path.join(self.tmp.name, "gh_out")
        self.cli("--map", self.map, "--files", "src/db/x.py", "--github-output", env={"GITHUB_OUTPUT": out})
        text = Path(out).read_text(encoding="utf-8").splitlines()
        self.assertIn("mode=selected", text)
        self.assertIn("full=false", text)
        self.assertIn("tests=tests/test_db.py", text)
        self.assertIn("job_pg=true", text)
        self.assertIn("job_rust=false", text)
        jobs = [l for l in text if l.startswith("jobs=")][0][5:]
        self.assertEqual(json.loads(jobs)["pytest"], True)


def run_block(workflow, header):
    """The shell text of the `run: |` that follows `header` in a workflow file (no YAML parser: stdlib only)."""
    lines = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if header in l)
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    out = []
    for l in lines[run + 1:]:
        if l.strip() and len(l) - len(l.lstrip()) < indent:
            break
        out.append(l[indent:])
    return chr(10).join(out) + chr(10)


@unittest.skipUnless(os.name == "posix", "workflow shell blocks are bash")
class WorkflowShellTests(unittest.TestCase):
    """The two shell blocks that guard correctness run for real, with a stub `gh`."""

    def setUp(self):
        if os.name == "nt":
            self.skipTest("POSIX-only: stub executables and bash")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.tmp.name, "gh-calls")
        # the gate posts the status with curl (the runner image has no gh)
        curl = os.path.join(self.bin, "curl")
        Path(curl).write_text(chr(10).join(["#!/bin/sh", 'echo "$@" >> "%s"' % self.calls,
                                              'exit "${CURL_EXIT_CODE:-0}"', ""]))
        os.chmod(curl, 0o755)

    def gate(self, event, full, results, windows_enabled="", curl_exit_code=0):
        """results: 'name=result ...' as the workflow builds it."""
        script = run_block("ci.yml", "merge-gate:")
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], GH_TOKEN="x", REPO="o/r", SHA="abc",
                   EVENT=event, FULL=full, REASON="r", RESULTS=results, WINDOWS_ENABLED=windows_enabled,
                   CURL_EXIT_CODE=str(curl_exit_code))
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", script], capture_output=True, text=True, env=env)
        calls = Path(self.calls).read_text() if os.path.exists(self.calls) else ""
        state = re.findall(r'"state":"([a-z]+)"', calls)
        return p.returncode, (state[-1] if state else None)

    OK = "smart=success linux=success ansible=success secrets=success scrub=success"

    def test_status_post_failure_fails_full_and_selected_only_runs(self):
        for full, state in (("true", "success"), ("false", "pending")):
            with self.subTest(full=full):
                results = self.OK + " windows=skipped"
                code, posted = self.gate("pull_request", full, results, curl_exit_code=22)
                self.assertEqual(posted, state)
                self.assertNotEqual(code, 0, "a failed status POST must fail the gate")
                self.assertEqual(self.gate("pull_request", full, results), (0, state))

    def test_selected_only_pr_run_is_green_with_a_pending_status(self):
        r = "smart=success linux=success ansible=skipped secrets=success scrub=success windows=skipped"
        self.assertEqual(self.gate("pull_request", "false", r), (0, "pending"))

    def test_full_run_sets_success_while_windows_is_switched_off(self):
        self.assertEqual(self.gate("pull_request", "true", self.OK + " windows=skipped"), (0, "success"))
        self.assertEqual(self.gate("push", "true", self.OK + " windows=skipped"), (0, "success"))

    def test_full_run_requires_windows_once_it_is_enabled(self):
        self.assertEqual(self.gate("push", "true", self.OK + " windows=skipped", "True"), (1, "failure"))
        self.assertEqual(self.gate("push", "true", self.OK + " windows=skipped", "true"), (1, "failure"))
        self.assertEqual(self.gate("push", "true", self.OK + " windows=failure", "true"), (1, "failure"))
        self.assertEqual(self.gate("push", "true", self.OK + " windows=success", "true"), (0, "success"))

    def test_a_failed_windows_job_is_red_even_while_switched_off(self):
        self.assertEqual(self.gate("push", "true", self.OK + " windows=failure"), (1, "failure"))

    def test_a_failed_or_cancelled_job_is_red_with_a_failure_status(self):
        self.assertEqual(self.gate("pull_request", "false", "smart=success linux=failure windows=skipped"), (1, "failure"))
        self.assertEqual(self.gate("pull_request", "true", "smart=success linux=cancelled windows=skipped"), (1, "failure"))

    def test_any_other_skipped_job_in_a_full_run_is_red(self):
        self.assertEqual(self.gate("pull_request", "true", "smart=success linux=skipped windows=skipped"), (1, "failure"))
        self.assertEqual(self.gate("pull_request", "true", "smart=success linux=success ansible=skipped windows=skipped"),
                         (1, "failure"))

    def test_a_non_pr_run_that_is_not_full_never_reports_success(self):
        self.assertEqual(self.gate("push", "", self.OK + " windows=skipped"), (1, "failure"))
        self.assertEqual(self.gate("workflow_dispatch", "false", self.OK + " windows=skipped"), (1, "failure"))

    def test_the_gate_results_cover_exactly_its_needs_list(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        gate = text[text.index("  merge-gate:"):]
        needs = re.search(r"needs: \[([^\]]*)\]", gate).group(1).replace(" ", "").split(",")
        reported = re.findall(r"needs\.([A-Za-z0-9_-]+)\.result", re.search(r"RESULTS: .*", gate).group(0))
        self.assertEqual(sorted(needs), sorted(reported))

    def test_runs_on_defaults_to_hosted_so_self_hosted_is_opt_in(self):
        text = (ROOT / ".github" / "workflows" / "smart-ci.yml").read_text(encoding="utf-8")
        block = text[text.index("      runs_on:"):]
        default = re.search(r"default: (.*)", block).group(1)
        self.assertNotIn("self-hosted", default)
        self.assertIn("ubuntu-latest", default)

    def test_the_token_is_not_put_on_the_command_line(self):
        gate = run_block("ci.yml", "merge-gate:")
        self.assertIn("-H @-", gate)
        self.assertNotIn("Bearer $GH_TOKEN\"", gate)

    def test_selector_that_cannot_run_falls_back_to_full(self):
        script = run_block("smart-ci.yml", "name: Select")
        out = os.path.join(self.tmp.name, "gh_out")
        env = dict(os.environ, EVENT="pull_request", LABELS="[]", MERGE_LABEL="merge-ready", FORCE_FULL="false",
                   BASE_SHA="a", HEAD_SHA="b", MAP="m.json", TOOL_REPO="", GITHUB_OUTPUT=out,
                   GITHUB_STEP_SUMMARY=os.path.join(self.tmp.name, "sum"), RUNNER_TEMP=self.tmp.name)
        # cwd has no tools/smart-ci/selector.py, so python3 itself fails
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", script], capture_output=True, text=True, env=env,
                           cwd=self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = Path(out).read_text().splitlines()
        self.assertIn("mode=full", lines)
        self.assertIn("full=true", lines)

    def test_merge_ready_label_forces_full(self):
        script = run_block("smart-ci.yml", "name: Select")
        out = os.path.join(self.tmp.name, "gh_out")
        tool = os.path.join(self.tmp.name, "tools", "smart-ci")
        os.makedirs(tool)
        Path(tool, "selector.py").write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        Path(self.tmp.name, "m.json").write_text(json.dumps(MAP))
        env = dict(os.environ, EVENT="pull_request", LABELS='["x","merge-ready"]', MERGE_LABEL="merge-ready",
                   FORCE_FULL="false", BASE_SHA="a", HEAD_SHA="b", MAP="m.json", TOOL_REPO="", GITHUB_OUTPUT=out,
                   GITHUB_STEP_SUMMARY=os.path.join(self.tmp.name, "sum"), RUNNER_TEMP=self.tmp.name)
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", script], capture_output=True, text=True, env=env,
                           cwd=self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = Path(out).read_text().splitlines()
        self.assertIn("mode=full", lines)
        self.assertTrue(any("merge-ready" in l for l in lines if l.startswith("reason=")))


class RepoMapTests(unittest.TestCase):
    """The map this repository ships must itself be valid and place real paths."""

    def setUp(self):
        self.m = sel.load_map(str(ROOT / ".github" / "smart-ci.json"))

    def test_smart_runner_uses_the_same_fork_guard_as_self_hosted_jobs(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        smart = text.split("  smart:", 1)[1].split("\n  linux-plan:", 1)[0]
        value = re.search(r"^      runs_on: \$\{\{ (.*) \}\}$", smart, re.MULTILINE).group(1)
        self.assertTrue(value.startswith("toJSON(") and value.endswith(")"))
        expression = value[len("toJSON("):-1]
        runners = re.findall(r"^    runs-on: \$\{\{ (.*) \}\}$", text, re.MULTILINE)
        linux_runners = [runner for runner in runners if "self-hosted" in runner and "ubuntu-latest" in runner]
        self.assertTrue(linux_runners)
        for runner in linux_runners:
            with self.subTest(runner=runner):
                self.assertEqual(expression, runner)
                self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", runner)
                self.assertIn("ubuntu-latest", runner)

    def test_job_conditions_put_full_fallback_before_jobs_json(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        conditions = [line for line in text.splitlines()
                      if line.lstrip().startswith("if:") and "fromJSON(needs.smart.outputs.jobs)." in line]
        self.assertTrue(conditions)
        for line in conditions:
            with self.subTest(condition=line):
                prefix = line.split("fromJSON(needs.smart.outputs.jobs).", 1)[0]
                self.assertIn("needs.smart.outputs.full == 'true' ||", prefix)

    def test_workflow_job_conditions_match_map_exactly(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        workflow_jobs = set(re.findall(r"fromJSON\(needs\.smart\.outputs\.jobs\)\.([A-Za-z0-9_-]+)", text))
        mapped_jobs = set(self.m["jobs"])
        self.assertEqual(workflow_jobs, mapped_jobs,
                         "job mismatch: only in ci.yml=%s; only in map=%s" %
                         (sorted(workflow_jobs - mapped_jobs), sorted(mapped_jobs - workflow_jobs)))
        self.assertTrue(set(self.m["always_jobs"]) <= mapped_jobs,
                        "always_jobs missing from jobs: %s" % sorted(set(self.m["always_jobs"]) - mapped_jobs))

    def test_a_single_linux_part_selects_only_its_shard_work(self):
        r = sel.select(["tests/linux/13-end-to-end-dry-run-only.sh"], self.m)
        self.assertEqual(r["mode"], "selected")
        self.assertEqual(r["tests"], ["tests/linux/13-end-to-end-dry-run-only.sh"])
        self.assertTrue(r["jobs"]["shards"])
        self.assertFalse(r["jobs"]["ansible"])

    def test_ansible_change_runs_ansible_not_the_suite(self):
        r = sel.select(["Windows/ansible/roles/x/tasks/main.yml"], self.m)
        self.assertTrue(r["jobs"]["ansible"])
        self.assertFalse(r["jobs"]["shards"])

    def test_library_change_is_full_by_unknown_path(self):
        self.assertEqual(sel.select(["lib/linux/ui.sh"], self.m)["mode"], "full")

    def test_unmapped_library_plus_mapped_test_part_is_full(self):
        r = sel.select(["lib/linux/ui.sh", "tests/linux/13-x.sh"], self.m)
        self.assertEqual(r["mode"], "full")
        self.assertEqual(r["reason"], "unknown path (no rule): lib/linux/ui.sh")
        self.assertTrue(all(r["jobs"].values()))
        self.assertEqual(r["tests"], [])

    def test_the_workflow_is_full(self):
        self.assertEqual(sel.select([".github/workflows/ci.yml"], self.m)["mode"], "full")


if __name__ == "__main__":
    unittest.main()
