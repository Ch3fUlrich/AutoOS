"""tests/test_oc_l1_recycle_order.py - AO-OC-RECYCLE-FOLLOWUPS: a hard crossing that is both
inside the restart cooldown and mid-merge answers `deferred` (exit 3), not `cooldown`
(exit 0): the merge is checked first. Cooldown alone stays `cooldown`, exit 0."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_oc_l1_recycle as base  # noqa: E402


class CooldownVersusMergeTest(base.RecycleBase):
    def in_cooldown(self):
        """A session restarted a moment ago whose new head is already over the hard limit."""
        self.srv.next_session_ids = [base.NEW_SESSION_ID, "ses_recycled9"]
        self.fill(310000)
        self.assertEqual(self.check()[1]["action"], "restarted")
        self.srv.session_items[base.NEW_SESSION_ID] = [base.assistant(320000)]

    def merge_head(self):
        (self.git_dir() / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")

    def test_cooldown_only_is_cooldown_exit_0(self):
        self.in_cooldown()
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "cooldown"))

    def test_cooldown_and_merge_is_deferred_exit_3(self):
        self.in_cooldown()
        self.merge_head()
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (3, "deferred"))
        self.assertIn("deferred: merge in progress", res["detail"])
        self.assertEqual(len(base.creates(self.srv)), 1)  # nothing restarted

    def test_the_cooldown_answers_again_once_the_merge_clears(self):
        self.in_cooldown()
        self.merge_head()
        self.assertEqual(self.check()[1]["action"], "deferred")
        (self.git_dir() / "MERGE_HEAD").unlink()
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "cooldown"))


if __name__ == "__main__":
    unittest.main()
