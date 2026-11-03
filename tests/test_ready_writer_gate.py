"""Writer attribution and committed waivers: hermetic CLI regression tests."""
import os
import json
import unittest
from unittest import mock
import test_autoos_spawner as fixtures


class WriterGateTests(fixtures.ReadyCommandTests):
    def setUp(self):
        super().setUp()
        self.registry["models"]["claude-sonnet-5-5"] = {"family": "anthropic"}
        with open(self.registry_path, "w", encoding="utf-8") as stream:
            json.dump(self.registry, stream)

    def drive(self, message, families=("openai",), parents=1):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        self.stub_lane_commits([self.commit(message=message, lines=900, parents=parents)])
        original = self.agent.review_status

        def report(*args, **kwargs):
            result = original(*args, **kwargs)
            result["cross_family"]["seats"] = [
                {"family": family, "ran": "tests", "reviewer": "fixture", "verdict": "PASS"}
                for family in families]
            return result

        with mock.patch.object(self.agent, "review_status", report):
            rc, out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox)
        return rc, out + err, self.read_inbox(inbox)

    def test_claude_independent_seat(self):
        for family in ("openai", "qwen"):
            rc, out, line = self.drive("x\n\nWriter: claude-sonnet-5-5 run-1", (family,))
            self.assertEqual(rc, 0, out)
            self.assertIn("ready: appended", out)
            self.assertTrue(line)

    def test_same_family(self):
        rc, out, line = self.drive("x\n\nWriter: claude-sonnet-5-5 run-1", ("anthropic",))
        self.assertEqual(rc, 1, out)
        self.assertIn("writer-family: writer claude-sonnet-5-5 (anthropic) has no counted seat of another family", out)
        self.assertEqual(line, "")

    def test_hand_edit(self):
        rc, out, _ = self.drive("x\n\nHand-Edit: fold a line")
        self.assertEqual(rc, 0, out)
        rc, out, line = self.drive("x\n\nHand-Edit: fold a line", ("anthropic",))
        self.assertEqual(rc, 1, out)
        self.assertIn("(anthropic) has no counted seat", out)
        self.assertEqual(line, "")

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


class WriterWaiverTests(fixtures.ReadyCommandTests):
    git = fixtures.ReadyCapExceptionTests.git
    routing_repo = fixtures.ReadyCapExceptionTests.routing_repo
    TABLE = fixtures.ReadyCapExceptionTests.TABLE
    row = fixtures.ReadyCapExceptionTests.row
    REF = "D-7"

    def test_bound_and_dry_run(self):
        for dry in (False, True):
            repo, sha = self.make_repo()
            routing = self.routing_repo(self.TABLE + self.row(sha))
            commit = self.git(routing, "rev-parse", "origin/main")
            inbox = self.make_inbox("")
            self.stub_lane_commits([self.commit(message="no attribution")])
            extra = ["--writer-waiver", self.REF] + (["--dry-run"] if dry else [])
            with mock.patch.dict(os.environ, {"AUTOOS_ROUTING_DIR": routing}):
                rc, out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox, extra)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("writer-waiver D-7 accepted (routing %s): waived" % commit, out)
            self.assertIn(" writer-waiver=D-7 routing=" + commit, out)
            self.assertEqual(bool(self.read_inbox(inbox)), not dry)

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


# Inherit helper methods, not a second copy of the original suite's tests.
for cls in (WriterGateTests, WriterWaiverTests):
    for name in dir(fixtures.ReadyCommandTests):
        if name.startswith("test_") and name not in cls.__dict__:
            setattr(cls, name, None)
del cls

if __name__ == "__main__":
    unittest.main()
