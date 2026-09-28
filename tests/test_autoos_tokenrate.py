#!/usr/bin/env python3
"""Tests for tools/autoos_tokenrate.py and the `token-rate` verb (RESTART spec §5 Metric).

Fixtures are shaped exactly like a real Claude Code transcript record. Measured
2026-09-28 on one assistant record in the L1-routing lane's transcript dir
(`~/.claude/projects/<the cwd's slug>/*.jsonl`, one file per session): the
top-level keys are

    ['advisorModel', 'apiBlockIndex', 'cwd', 'effort', 'entrypoint', 'gitBranch',
     'isSidechain', 'message', 'parentUuid', 'perTurnEffort', 'requestId',
     'serverClassifierRequest', 'sessionId', 'sessionKind', 'session_id',
     'timestamp', 'type', 'userType', 'uuid', 'version']

and `message.usage` carries

    ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
     'output_tokens', 'output_tokens_details', 'server_tool_use', 'service_tier',
     'cache_creation', 'inference_geo', 'iterations', 'speed']

with `timestamp` like `2026-09-26T19:16:44.016Z` and `cwd` like a lane
directory under the operator's code tree. `usage.iterations` repeats the four
token fields per iteration, so summing it too would double count — the fixtures
carry it and the tests assert it is ignored.

The git denominator is exercised against a throwaway repo in a TemporaryDirectory,
the transcripts against a throwaway projects dir, so nothing reads the live
machine. Run it directly, never through unittest discover:

    python3 tests/test_autoos_tokenrate.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
TOKENRATE = TOOLS / "autoos_tokenrate.py"
sys.path.insert(0, str(TOOLS))

import autoos_tokenrate as tr  # noqa: E402

# The orchestrator prefix used by most fixtures, and a sibling that must not match.
PREFIX = "/home/user/code/AutoOS-lanes/L1-routing"
OTHER = "/home/user/code/AutoOS-lanes/L1-backlog"


def usage_line(input_tokens=0, output_tokens=0, cache_creation=0, cache_read=0,
               ts="2026-09-27T12:00:00.000Z", cwd=PREFIX, session="sess-1",
               with_iterations=True, sidechain=False):
    """One assistant record, key-for-key as the real transcript writes it.

    `sidechain=True` is the in-session subagent turn (router D-045): the same
    shape with `isSidechain` true, plus the `agentId` key only the subagent
    files carry (measured 2026-09-28: top-level records have `isSidechain:
    false` and no `agentId`; the 8,888 records under
    `<projects>/<session>/subagents/*.jsonl` have `isSidechain: true`,
    `agentId`, `sessionKind: "bg"`, and the parent's `cwd`).
    """
    usage = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "output_tokens_details": {"thinking_tokens": 0},
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "cache_creation": {"ephemeral_1h_input_tokens": cache_creation,
                           "ephemeral_5m_input_tokens": 0},
        "inference_geo": "not_available",
        "speed": "standard",
    }
    if with_iterations:
        # The real client mirrors the turn into `iterations`; a sum over both
        # fields and the list would double every record.
        usage["iterations"] = [{
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
            "type": "message",
        }]
    record = {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": ts,
        "sessionId": session,
        "session_id": session,
        "isSidechain": sidechain,
        "gitBranch": "L1-routing/R5ARATE",
        "uuid": "uuid-%s" % input_tokens,
        "message": {
            "model": "claude-opus-4-6[1m]",
            "role": "assistant",
            "usage": usage,
        },
    }
    if sidechain:
        record["agentId"] = "agent-%s" % session
    return json.dumps(record)


def slug(path):
    return str(path).replace("/", "-").replace(".", "-")


def write_transcript(projects_dir, cwd, name, lines, session_paths=False):
    d = projects_dir / slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d / name


def git(repo, *args, env_extra=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class WeightTests(unittest.TestCase):
    """The 0.1 cache-read weight and the four summed fields (spec §5)."""

    def test_cache_read_weight_is_one_tenth(self):
        self.assertEqual(tr.CACHE_READ_WEIGHT, 0.1)

    def test_all_four_fields_summed(self):
        # 100 + 200 + 300 + 0.1 * 4000 = 1000.
        rec = tr.UsageRecord(input_tokens=100, output_tokens=200,
                             cache_creation_input_tokens=300,
                             cache_read_input_tokens=4000)
        self.assertEqual(rec.weighted(), 1000.0)
        self.assertEqual(rec.naive(), 4600)

    def test_iterations_are_not_double_counted(self):
        lines = [usage_line(input_tokens=10, output_tokens=20,
                            cache_creation=30, cache_read=400)]
        total = tr.sum_records(tr.iter_usage_records(lines))
        self.assertEqual(total.records, 1)
        self.assertEqual(total.naive, 460)
        self.assertAlmostEqual(total.weighted, 100.0)  # 10+20+30+0.1*400

    def test_malformed_and_non_usage_lines_are_skipped(self):
        lines = ["", "not json", "{", json.dumps({"type": "user", "cwd": PREFIX}),
                 json.dumps({"type": "assistant", "message": {"no": 1}}),
                 json.dumps({"type": "assistant", "message": {"usage": "no"}}),
                 usage_line(input_tokens=1)]
        total = tr.sum_records(tr.iter_usage_records(lines))
        self.assertEqual(total.records, 1)
        self.assertEqual(total.naive, 1)


class AllRecordsTests(unittest.TestCase):
    """The regression this tool exists for: every record, not the last one."""

    def test_every_record_counts_not_only_the_last(self):
        lines = [usage_line(input_tokens=1000), usage_line(input_tokens=2000),
                 usage_line(input_tokens=3000)]
        total = tr.sum_records(tr.iter_usage_records(lines))
        self.assertEqual(total.records, 3)
        self.assertEqual(total.naive, 6000)
        # fill_from_transcript keeps the last record only: 3000. Guard that the
        # new iterator does not inherit that behaviour.
        import autoos_context as ctx
        self.assertEqual(ctx.fill_from_transcript(lines)["tokens"], 3000)
        self.assertGreater(total.naive, ctx.fill_from_transcript(lines)["tokens"])

    def test_sessions_count_distinct_ids_across_files(self):
        lines_a = [usage_line(session="a"), usage_line(session="a")]
        lines_b = [usage_line(session="b")]
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "a.jsonl", lines_a)
            write_transcript(projects, PREFIX, "b.jsonl", lines_b)
            res = tr.measure(projects, [PREFIX], "2026-09-27T00:00:00Z",
                             "2026-09-28T00:00:00Z")
        self.assertEqual(res.sessions, 2)
        self.assertEqual(res.records, 3)


class SidechainTests(unittest.TestCase):
    """Router D-045: in-session subagent turns stay in the numerator, split out.

    The decision is that an `isSidechain` record is orchestrator cost — the
    parent session paid for it — so nothing is filtered out; what was missing
    was the visibility of how big that part is.
    """

    def mixed(self):
        """One parent turn (weighted 400) and one subagent turn (weighted 100)."""
        return [
            usage_line(input_tokens=100, output_tokens=100, cache_creation=100,
                       cache_read=1000),
            usage_line(input_tokens=10, output_tokens=20, cache_creation=30,
                       cache_read=400, sidechain=True),
        ]

    def test_parse_record_carries_the_sidechain_flag(self):
        parent = tr.parse_record(usage_line(input_tokens=1))
        child = tr.parse_record(usage_line(input_tokens=1, sidechain=True))
        self.assertFalse(parent.sidechain)
        self.assertTrue(child.sidechain)

    def test_sidechain_record_is_not_excluded_from_the_numerator(self):
        total = tr.sum_records(tr.iter_usage_records(self.mixed()))
        self.assertEqual(total.records, 2)
        self.assertAlmostEqual(total.weighted, 500.0)
        self.assertEqual(total.naive, 1760)

    def test_sidechain_split_of_a_mixed_fixture(self):
        total = tr.sum_records(tr.iter_usage_records(self.mixed()))
        self.assertEqual(total.subagent_records, 1)
        self.assertAlmostEqual(total.subagent_weighted, 100.0)
        self.assertEqual(total.subagent_naive, 460)

    def test_all_parent_fixture_reports_a_zero_subagent_split(self):
        total = tr.sum_records(tr.iter_usage_records(
            [usage_line(input_tokens=100, output_tokens=100, cache_creation=100,
                        cache_read=1000)] * 2))
        self.assertEqual(total.subagent_records, 0)
        self.assertAlmostEqual(total.subagent_weighted, 0.0)
        self.assertEqual(total.subagent_naive, 0)
        self.assertAlmostEqual(total.summary().subagent_share_pct(), 0.0)

    def test_measure_splits_sidechain_over_files(self):
        lines = self.mixed() + [usage_line(input_tokens=6, output_tokens=0,
                                           cache_creation=0, cache_read=0,
                                           session="sess-2", sidechain=True)]
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "a.jsonl", lines)
            res = tr.measure(projects, [PREFIX], "2026-09-26T00:00:00Z",
                             "2026-09-29T00:00:00Z")
        self.assertEqual(res.records, 3)
        self.assertAlmostEqual(res.weighted, 506.0)  # 400 + 100 + 6
        self.assertEqual(res.naive, 1766)
        self.assertEqual(res.subagent_records, 2)
        self.assertAlmostEqual(res.subagent_weighted, 106.0)
        self.assertEqual(res.subagent_naive, 466)
        self.assertAlmostEqual(res.subagent_share_pct(),
                               round(100.0 * 106.0 / 506.0, 1))

    def test_share_is_of_weighted_and_survives_rounding(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "a.jsonl", self.mixed())
            res = tr.report(projects_dir=projects, cwd_prefixes=[PREFIX], repo=None,
                            since="2026-09-26T00:00:00Z",
                            until="2026-09-29T00:00:00Z")
        self.assertEqual(res["subagent_records"], 1)
        self.assertEqual(res["subagent_weighted"], 100.0)
        self.assertEqual(res["subagent_naive"], 460)
        self.assertEqual(res["subagent_share_pct"], 20.0)  # 100 of 500 weighted


class WindowTests(unittest.TestCase):
    """Half-open window: since inclusive, until exclusive, by record timestamp."""

    def test_records_outside_the_window_are_dropped(self):
        lines = [
            usage_line(ts="2026-09-26T23:59:59.999Z", input_tokens=1),
            usage_line(ts="2026-09-27T00:00:00.000Z", input_tokens=2),
            usage_line(ts="2026-09-27T12:00:00.000Z", input_tokens=4),
            usage_line(ts="2026-09-28T00:00:00.000Z", input_tokens=8),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "s.jsonl", lines)
            res = tr.measure(projects, [PREFIX], "2026-09-27T00:00:00Z",
                             "2026-09-28T00:00:00Z")
        self.assertEqual(res.records, 2)
        self.assertEqual(res.naive, 6)

    def test_relative_since_resolves_against_until(self):
        start, end = tr.resolve_window("48h", "2026-09-28T00:00:00Z", now=datetime(
            2026, 9, 28, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2026, 9, 26, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 28, tzinfo=timezone.utc))

    def test_missing_timestamp_record_is_skipped(self):
        rec = json.loads(usage_line(input_tokens=5))
        del rec["timestamp"]
        total = tr.sum_records(tr.iter_usage_records([json.dumps(rec)]))
        self.assertEqual(total.records, 0)


class CwdScopingTests(unittest.TestCase):
    """Only sessions whose cwd belongs to the named orchestrator count."""

    def test_records_outside_the_prefix_are_excluded(self):
        lines = [usage_line(input_tokens=1, cwd=PREFIX),
                 usage_line(input_tokens=100, cwd=OTHER),
                 usage_line(input_tokens=10000, cwd="/home/user/code/Invest")]
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "a.jsonl", [lines[0]])
            write_transcript(projects, OTHER, "b.jsonl", [lines[1]])
            write_transcript(projects, "/home/user/code/Invest", "c.jsonl", [lines[2]])
            res = tr.measure(projects, [PREFIX], "2026-09-26T00:00:00Z",
                             "2026-09-29T00:00:00Z")
        self.assertEqual(res.records, 1)
        self.assertEqual(res.naive, 1)

    def test_lane_sandboxes_under_the_prefix_still_match(self):
        deep = PREFIX + "/logs/sandboxes/L1-routing-R5ARATE-20260928-071419-run"
        lines = [usage_line(input_tokens=7, cwd=deep)]
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, deep, "a.jsonl", lines)
            res = tr.measure(projects, [PREFIX], "2026-09-26T00:00:00Z",
                             "2026-09-29T00:00:00Z")
        self.assertEqual(res.records, 1)
        self.assertEqual(res.naive, 7)

    def test_sibling_prefix_is_not_matched(self):
        # /home/user/code/AutoOS is a prefix of the string but the lane dir is a
        # different orchestrator: matching is per path segment, not per char.
        lines = [usage_line(input_tokens=9, cwd="/home/user/code/AutoOS-lanes/L2-general")]
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, "/home/user/code/AutoOS-lanes/L2-general",
                             "a.jsonl", lines)
            res = tr.measure(projects, ["/home/user/code/AutoOS"],
                             "2026-09-26T00:00:00Z", "2026-09-29T00:00:00Z")
        self.assertEqual(res.records, 0)


class MergeTests(unittest.TestCase):
    """The denominator: first-parent merges into main, named by branch prefix."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name)
        git(cls.repo, "init", "-b", "main")
        # main base, then two lane merges and one unrelated merge.
        (cls.repo / "README").write_text("base\n", encoding="utf-8")
        git(cls.repo, "add", "README")
        git(cls.repo, "commit", "-m", "chore: base")
        for i, subject in enumerate([
            "Merge L1-routing/R6STOP 0732cf0: gateway stop parsed",
            "Merge l1/routing T2FREE ad23e99: free leg",
            "Merge l1/backlog 5575b86: omnigraph-client joins profile",
        ]):
            branch = "lane%d" % i
            git(cls.repo, "checkout", "-b", branch, "main")
            (cls.repo / ("%s.txt" % branch)).write_text("x\n", encoding="utf-8")
            git(cls.repo, "add", "%s.txt" % branch)
            git(cls.repo, "commit", "-m", "feat: %s" % branch)
            git(cls.repo, "checkout", "main")
            env = {"GIT_COMMITTER_DATE": "2026-09-27T%d:00:00 +0000" % (10 + i)}
            git(cls.repo, "merge", "--no-ff", "-m", subject, branch, env_extra=env)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_merges_attributed_by_prefix(self):
        n = tr.count_merges(self.repo, "main", ["L1-routing/", "l1/routing"],
                            "2026-09-26T00:00:00Z", "2026-09-28T00:00:00Z")
        self.assertEqual(n, 2)

    def test_prefix_is_case_insensitive_substring(self):
        # The lane writes `Merge L1-routing/…`; the orchestrator names either
        # casing, so matching must not depend on it.
        self.assertEqual(tr.count_merges(self.repo, "main", ["l1-routing/"],
                                         "2026-09-26T00:00:00Z",
                                         "2026-09-28T00:00:00Z"), 1)
        self.assertEqual(tr.count_merges(self.repo, "main", ["L1-ROUTING/"],
                                         "2026-09-26T00:00:00Z",
                                         "2026-09-28T00:00:00Z"), 1)
        self.assertEqual(tr.count_merges(self.repo, "main", ["L1/Routing"],
                                         "2026-09-26T00:00:00Z",
                                         "2026-09-28T00:00:00Z"), 1)

    def test_no_prefix_counts_every_merge(self):
        self.assertEqual(tr.count_merges(self.repo, "main", [],
                                         "2026-09-26T00:00:00Z",
                                         "2026-09-28T00:00:00Z"), 3)

    def test_window_is_committer_date(self):
        n = tr.count_merges(self.repo, "main", [], "2026-09-27T11:00:00Z",
                            "2026-09-27T13:00:00Z")
        self.assertEqual(n, 2)

    def test_non_merge_commits_are_never_counted(self):
        self.assertEqual(tr.count_merges(self.repo, "main", ["chore: base"],
                                         "2026-09-20T00:00:00Z",
                                         "2026-09-30T00:00:00Z"), 0)


class ReportTests(unittest.TestCase):
    """Output shape: the §5 fields, the bias line, and n/a on an empty denominator."""

    def build(self, tmp, merges_repo=None):
        projects = Path(tmp) / "projects"
        write_transcript(projects, PREFIX, "a.jsonl", [
            usage_line(input_tokens=100, output_tokens=100, cache_creation=100,
                       cache_read=1000),
            usage_line(input_tokens=100, output_tokens=100, cache_creation=100,
                       cache_read=1000),
        ])
        return projects

    def test_zero_merges_prints_na_and_never_divides(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init", "-b", "main")
            (repo / "f").write_text("1\n", encoding="utf-8")
            git(repo, "add", "f")
            git(repo, "commit", "-m", "chore: only a plain commit")
            projects = self.build(tmp)
            res = tr.report(projects_dir=projects, cwd_prefixes=[PREFIX],
                            repo=repo, branch="main", branch_prefixes=["L1-routing/"],
                            since="2026-09-26T00:00:00Z",
                            until="2026-09-28T00:00:00Z")
            text = tr.format_text(res)
        self.assertEqual(res["merges"], 0)
        self.assertEqual(res["weighted_per_merge"], None)
        self.assertIn("n/a", text)
        self.assertNotIn("nan", text)
        self.assertIn("weighted-per-merge: n/a", text)

    def test_per_merge_division_and_bias_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init", "-b", "main")
            (repo / "f").write_text("1\n", encoding="utf-8")
            git(repo, "add", "f")
            git(repo, "commit", "-m", "chore: base")
            git(repo, "checkout", "-b", lane := "lane")
            (repo / "g").write_text("2\n", encoding="utf-8")
            git(repo, "add", "g")
            git(repo, "commit", "-m", "feat: g")
            git(repo, "checkout", "main")
            git(repo, "merge", "--no-ff", "-m", "Merge L1-routing/R5 1a2b3c: x", lane,
                env_extra={"GIT_COMMITTER_DATE": "2026-09-27T12:00:00 +0000"})
            projects = self.build(tmp)
            res = tr.report(projects_dir=projects, cwd_prefixes=[PREFIX],
                            repo=repo, branch="main",
                            branch_prefixes=["L1-routing/"],
                            since="2026-09-26T00:00:00Z",
                            until="2026-09-28T00:00:00Z")
            text = tr.format_text(res)
        # 2 records: weighted 2*(100+100+100+0.1*1000) = 800, naive 2*1300 = 2600.
        self.assertEqual(res["weighted"], 800.0)
        self.assertEqual(res["naive"], 2600)
        self.assertEqual(res["merges"], 1)
        self.assertEqual(res["weighted_per_merge"], 800.0)
        self.assertEqual(res["naive_per_merge"], 2600.0)
        self.assertIn("sessions: 1", text)
        self.assertIn("bias:", text)
        self.assertIn("rewards shorter sessions", text)
        self.assertIn("prices the orchestrator only", text)

    def test_subagent_columns_are_in_the_text_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "a.jsonl", [
                usage_line(input_tokens=100, output_tokens=100,
                           cache_creation=100, cache_read=1000),
                usage_line(input_tokens=10, output_tokens=20, cache_creation=30,
                           cache_read=400, sidechain=True),
            ])
            res = tr.report(projects_dir=projects, cwd_prefixes=[PREFIX], repo=None,
                            since="2026-09-26T00:00:00Z",
                            until="2026-09-29T00:00:00Z")
            text = tr.format_text(res)
        self.assertIn("subagent-records: 1", text)
        self.assertIn("subagent-weighted: 100", text)
        self.assertIn("subagent-naive: 460", text)
        self.assertIn("subagent-share: 20.0%", text)
        # the numerator is unchanged by the split: D-045 keeps sidechain in it.
        self.assertIn("weighted: 500", text)

    def test_empty_numerator_share_is_na_not_a_division(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            write_transcript(projects, PREFIX, "a.jsonl",
                             [usage_line(input_tokens=1, cwd=OTHER)])
            res = tr.report(projects_dir=projects, cwd_prefixes=[PREFIX], repo=None,
                            since="2026-09-26T00:00:00Z",
                            until="2026-09-29T00:00:00Z")
            text = tr.format_text(res)
        self.assertEqual(res["weighted"], 0.0)
        self.assertIsNone(res["subagent_share_pct"])
        self.assertIn("subagent-share: n/a", text)
        self.assertNotIn("nan", text)
        json.dumps(res)

    def test_json_output_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = self.build(tmp)
            res = tr.report(projects_dir=projects, cwd_prefixes=[PREFIX], repo=None,
                            branch="main", branch_prefixes=["L1-routing/"],
                            since="2026-09-26T00:00:00Z", until="2026-09-28T00:00:00Z")
        for key in ("window", "sessions", "records", "weighted", "naive",
                    "merges", "weighted_per_merge", "naive_per_merge", "bias",
                    "subagent_records", "subagent_weighted", "subagent_naive",
                    "subagent_share_pct"):
            self.assertIn(key, res)
        json.dumps(res)


class CliTests(unittest.TestCase):
    """The CLI and the `token-rate` verb read a fixture projects dir."""

    env = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name) / "projects"
        write_transcript(self.projects, PREFIX, "a.jsonl", [
            usage_line(input_tokens=1, output_tokens=2, cache_creation=3,
                       cache_read=40),
        ])
        self.env = dict(os.environ, HOME=Path(self.tmp.name) / "no-home")

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(TOKENRATE)] + list(args),
                              capture_output=True, text=True, env=self.env,
                              cwd=str(ROOT), stdin=subprocess.DEVNULL)

    def test_cli_prints_all_fields(self):
        proc = self.run_tool("--projects-dir", str(self.projects),
                             "--cwd-prefix", PREFIX, "--no-git",
                             "--since", "2026-09-26T00:00:00Z",
                             "--until", "2026-09-28T00:00:00Z")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for label in ("window:", "sessions:", "records:", "weighted:", "naive:",
                      "subagent-records:", "subagent-weighted:",
                      "subagent-naive:", "subagent-share:",
                      "merges:", "weighted-per-merge:", "naive-per-merge:",
                      "bias:"):
            self.assertIn(label, proc.stdout)

    def test_cli_json_reports_the_subagent_split(self):
        lines = [usage_line(input_tokens=1, output_tokens=2, cache_creation=3,
                            cache_read=40),
                 usage_line(input_tokens=10, output_tokens=0, cache_creation=0,
                            cache_read=100, sidechain=True)]
        write_transcript(self.projects, PREFIX, "b.jsonl", lines)
        proc = self.run_tool("--projects-dir", str(self.projects),
                             "--cwd-prefix", PREFIX, "--no-git",
                             "--since", "2026-09-26T00:00:00Z",
                             "--until", "2026-09-28T00:00:00Z", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        # setUp's parent (weighted 10) + this parent (10) + subagent (20) = 40,
        # and half of the numerator is the subagent turn.
        self.assertEqual(data["records"], 3)
        self.assertEqual(data["weighted"], 40.0)
        self.assertEqual(data["subagent_records"], 1)
        self.assertEqual(data["subagent_weighted"], 20.0)
        self.assertEqual(data["subagent_naive"], 110)
        self.assertEqual(data["subagent_share_pct"], 50.0)

    def test_cli_json(self):
        proc = self.run_tool("--projects-dir", str(self.projects),
                             "--cwd-prefix", PREFIX, "--no-git",
                             "--since", "2026-09-26T00:00:00Z",
                             "--until", "2026-09-28T00:00:00Z", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["records"], 1)
        self.assertEqual(data["naive"], 46)
        self.assertEqual(data["weighted"], 10.0)
        self.assertEqual(data["merges"], 0)
        self.assertIsNone(data["weighted_per_merge"])

    def test_repeatable_branch_prefix(self):
        proc = self.run_tool("--projects-dir", str(self.projects), "--no-git",
                             "--cwd-prefix", "/x",
                             "--branch-prefix", "L1-routing/",
                             "--branch-prefix", "l1/routing",
                             "--since", "48h", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["branch_prefixes"], ["L1-routing/", "l1/routing"])

    def test_relative_since_accepted(self):
        proc = self.run_tool("--projects-dir", str(self.projects), "--no-git",
                             "--cwd-prefix", PREFIX, "--since", "48h", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("2026", json.loads(proc.stdout)["window"]["since"])

    def test_unknown_since_unit_exits_2(self):
        proc = self.run_tool("--projects-dir", str(self.projects), "--no-git",
                             "--cwd-prefix", PREFIX, "--since", "tomorrow")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--since", proc.stderr)

    def test_missing_branch_exits_2_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init", "-b", "main")
            proc = self.run_tool("--projects-dir", str(self.projects),
                                 "--cwd-prefix", PREFIX, "--repo", str(repo),
                                 "--branch", "no-such-branch",
                                 "--since", "2026-09-26T00:00:00Z",
                                 "--until", "2026-09-28T00:00:00Z")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("token-rate:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_agent_verb_dispatches(self):
        proc = subprocess.run(
            [sys.executable, str(AGENT), "token-rate",
             "--projects-dir", str(self.projects), "--no-git",
             "--cwd-prefix", PREFIX, "--since", "2026-09-26T00:00:00Z",
             "--until", "2026-09-28T00:00:00Z", "--json"],
            capture_output=True, text=True, env=self.env, cwd=str(ROOT),
            stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["records"], 1)


if __name__ == "__main__":
    unittest.main()
