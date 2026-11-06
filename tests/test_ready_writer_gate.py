"""Writer attribution and committed waivers: hermetic CLI regression tests."""
import os
import json
import unittest
from unittest import mock
import test_autoos_spawner as fixtures


class ReadyFixtures(unittest.TestCase):
    locals().update({name: value for name, value in vars(fixtures.ReadyCommandTests).items()
                     if not name.startswith(("test_", "_class"))
                     and not (name.startswith("__") and name.endswith("__"))})


class WriterGateTests(ReadyFixtures):
    def setUp(self):
        super().setUp()
        self.registry["models"]["claude-sonnet-5-5"] = {"family": "anthropic"}
        with open(self.registry_path, "w", encoding="utf-8") as stream:
            json.dump(self.registry, stream)

    def drive(self, message, parents=1):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        self.stub_lane_commits([self.commit(message=message, lines=900, parents=parents)])
        rc, out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox)
        return rc, out + err, self.read_inbox(inbox)

    def test_claude_independent_seat(self):
        rc, out, line = self.drive("x\n\nWriter: claude-sonnet-5-5 run-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("ready: appended", out)
        self.assertTrue(line)

    def test_same_family(self):
        # Rule 3 is defence-in-depth: through `ready` the review floor already demands
        # two distinct counted families, so it is pinned here at `refusal()` itself.
        hand = [self.commit(message="x\n\nHand-Edit: fold")]
        for families, refused in ((("anthropic",), True), (("openai",), False)):
            report = {"cross_family": {"seats": [{"family": f} for f in families]}}
            self.assertEqual(bool(self.agent.writer_gate.refusal(
                hand, self.registry, report, self.agent.ready_guards)), refused, families)
        commits = [self.commit(message="x\n\nWriter: claude-sonnet-5-5 run-1")]
        for families in (("anthropic",), ("openai",), (), (None,)):
            report = {"cross_family": {"seats": [{"family": f} for f in families]}}
            result = self.agent.writer_gate.refusal(commits, self.registry, report,
                                                    self.agent.ready_guards)
            self.assertEqual(bool(result), families != ("openai",), result)
        self.assertIn("no counted seat", self.agent.writer_gate.refusal(
            commits, self.registry, {"cross_family": {}}, self.agent.ready_guards))

    def test_hand_edit(self):
        rc, out, _ = self.drive("x\n\nHand-Edit: fold")
        self.assertEqual(rc, 0, out)

    def test_unknown(self):
        rc, out, line = self.drive("x\n\nWriter: imaginary-model run-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("writer-family: writer imaginary-model has no known family", out)
        self.assertEqual(line, "")

    def test_shape_and_merge(self):
        for message in ("x", "x\n\nHand-Edit:   ", "Hand-Edit: why\n\nbody"):
            refusal = self.agent.ready_guards.writer_trailer_refusal([self.commit(message=message)])
            self.assertIn("Writer: <model> <run id>", refusal)
            self.assertIn("Hand-Edit: <reason>", refusal)
            self.assertIn("D-2148", refusal)
        rc, out, _ = self.drive("merge", parents=2)
        self.assertEqual(rc, 0, out)


class WriterWaiverTests(ReadyFixtures):
    git = fixtures.ReadyCapExceptionTests.git
    routing_repo = fixtures.ReadyCapExceptionTests.routing_repo
    TABLE = fixtures.ReadyCapExceptionTests.TABLE
    def row(self, sha, **kwargs):
        return fixtures.ReadyCapExceptionTests.row(self, sha, extra="writer-waiver", **kwargs)
    REF = "D-7"

    def test_bound_and_dry_run(self):
        for message, dry in (("no attribution", False), ("no attribution", True),
                             ("x\n\nWriter: imaginary-model run-1", False)):
            repo, sha = self.make_repo()
            routing = self.routing_repo(self.TABLE + self.row(sha))
            commit = self.git(routing, "rev-parse", "origin/main")
            inbox = self.make_inbox("")
            self.stub_lane_commits([self.commit(message=message)])
            extra = ["--writer-waiver", self.REF] + (["--dry-run"] if dry else [])
            with mock.patch.dict(os.environ, {"AUTOOS_ROUTING_DIR": routing}):
                rc, out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox, extra)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("note: writer-waiver D-7 bound (routing %s); waives "
                          "writer-trailer and writer-family if the lane reaches them" % commit, out)
            self.assertIn(" writer-waiver=D-7 routing=" + commit, out)
            self.assertEqual(bool(self.read_inbox(inbox)), not dry)

    def test_decision_token_required(self):
        repo, sha = self.make_repo()
        routing = self.routing_repo(self.TABLE + fixtures.ReadyCapExceptionTests.row(self, sha))
        inbox = self.make_inbox("")
        with mock.patch.dict(os.environ, {"AUTOOS_ROUTING_DIR": routing}):
            rc, out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox,
                                      ["--writer-waiver", self.REF])
        self.assertEqual(rc, 1, out + err)
        self.assertIn("row D-7 does not name writer-waiver in its decision cell", out)
        self.assertEqual(self.read_inbox(inbox), "")

    def test_unbound(self):
        repo, sha = self.make_repo()
        for ref, row in (("D-8", self.row(sha)), ("bad", self.row(sha)),
                         (self.REF, self.row("a" * 40)), (self.REF, self.row(sha[:12])),
                         (self.REF, self.row(sha, by="routing-01")),
                         (self.REF, self.row(sha) * 2)):
            with self.subTest(ref=ref, row=row):
                routing = self.routing_repo(self.TABLE + row)
                inbox = self.make_inbox("")
                with mock.patch.dict(os.environ, {"AUTOOS_ROUTING_DIR": routing}):
                    rc, out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox,
                                              ["--writer-waiver", ref])
                self.assertEqual(rc, 1, out + err)
                self.assertEqual(len(out.splitlines()), 1, out)
                self.assertEqual(self.read_inbox(inbox), "")


class CommittedLogLocationTests(WriterWaiverTests):
    def test_log_locations(self):
        sha = "a" * 40
        routing = self.routing_repo(self.TABLE + self.row(sha))
        env = {"AUTOOS_ROUTING_DIR": routing}
        self.assertTrue(self.agent.cap_exception_ok(self.REF, sha, env)[0])
        self.git(routing, "mv", "router/docs", "docs")
        self.git(routing, "-c", "user.name=autoos-test", "-c", "user.email=" + self.agent.WORKER_EMAIL, "commit", "-qm", "old location")
        self.git(routing, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertFalse(self.agent.cap_exception_ok(self.REF, sha, env)[0])
        os.makedirs(os.path.join(routing, "router", "docs"))
        with open(os.path.join(routing, "router", "docs", "decisions-log.md"), "w") as stream:
            stream.write(self.TABLE + self.row(sha))
        self.assertFalse(self.agent.cap_exception_ok(self.REF, sha, env)[0])
    def test_default_directories(self):
        with mock.patch.dict(os.environ, {"HOME": os.path.dirname(self.registry_path), "USERPROFILE": os.path.dirname(self.registry_path)}):
            os.environ.pop("AUTOOS_ROUTING_DIR", None)
            self.assertEqual(self.agent._routing_log_dir(os.environ), os.path.join(os.path.dirname(self.registry_path), "fleet", "plangraph-data"))
            self.assertEqual(self.agent.go_gate().routing_dir(os.environ), os.path.join(os.path.dirname(self.registry_path), "code", "routing"))


class RealRegistryWriterFamilyTests(unittest.TestCase):
    """The shipped catalog places every writer spelling the fleet signs (D-3465, D-3666; luna stays
    unregistered under the newest-only Codex policy, D-2144):
    a fixture-only family would let the gate pass here and refuse the real lane."""

    def test_fleet_writer_spellings_resolve_in_the_real_catalog(self):
        from autoos_resolver import author_family
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog",
                            "ai-registry.json")
        with open(path, encoding="utf-8") as stream:
            registry = json.load(stream)
        for model, family in (("claude-sonnet-5-5", "anthropic"),
                              ("console-anthropic/claude-sonnet-5-5", "anthropic"),
                              ("omniroute/cx-gpt-6.1-sol", "openai")):
            with self.subTest(model=model):
                self.assertEqual(author_family(model, registry)[0], family)


if __name__ == "__main__":
    unittest.main()
