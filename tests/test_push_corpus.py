#!/usr/bin/env python3
"""The push-spelling corpus (ORCH-A1 phase 1, round 13 / routing-00 D-159).

Round 13 removes every pre-granted push and workflow dispatch from the role
launch profiles (``COORDINATOR_ALLOW``, ``L2_PUSH_ALLOW``,
``L2_WORKFLOW_ALLOW``) together with the deny entries that existed only to
shape those grants (the L1 shape / refspec / lane-prefix denies, the L2
single-ref fences, the workflow-run shape denies and the round 6-8 character
classes). What survives is the fence that names ``main``: a push that does not
name a main-resolving ref is no longer pre-granted either, so it reaches the
classifier, and the classifier — not a glob over command text — is the guard
until the hooks lane (HOOKS H2) reads the real argv.

``tests/fixtures/push-corpus.json`` is the one home of every push and dispatch
spelling harvested from the earlier review rounds (1-12; round 2 added no push
spelling) of ``tests/test_launch_profiles.py``. Each row is
``{cmd, expect, why, round}``:

- ``deny`` — the command denies on EVERY profile, because a main-resolving ref
  or a push-wide flag (``--force``/``-f``/``--all``/``--mirror``/``--tags``)
  is visible in its literal text;
- ``allow-lane`` — a push or dispatch that names only lane refs: the main fence
  must not reach it (the no-over-deny boundary a reviewer checks when the fence
  set changes). It is no longer *pre-granted* — round 13 removed the grants —
  so its decision is the classifier's, like every other push;
- ``prompt`` — neither: no fence matches, so the decision is ``none`` and the
  launch flags or the classifier settle it.

``why`` records the round-13 disposition of each row, including the rows whose
fence is gone and whose hazard is therefore an OPEN residual owned by HOOKS H2
(quoted / substituted / glob / rewritten refs, ``;`` and ``#`` suffixes,
matching and empty-``--ref`` forms, ``git config`` in argv).

The ``deny`` rows are also the witnesses of the fence set (KEYDENY, 2704196: a
deny is a decision on a real command): every Bash deny rule any profile renders
that names a push or a dispatch must match at least one of them. That is what
caught the three ``*:main:*``-shaped rules round 13 deleted — a refspec carries
one colon, so they matched no argv git accepts.

The matcher model, the corpus validator and the real-git premise tests are in
``tests/helpers/launch_profile_model.py`` (one home, principle 1);
``tests/test_launch_profiles.py`` imports the same objects. The premise tests
prove with real git that the spellings the fence set names really do advance
``refs/heads/main`` (``heads/main`` in every position, the ``refs/heads/*``
glob refspec) or really do create a stray ``refs/heads/HEAD`` — the premises
round 11 and round 12 fenced on, and the premises MED-4 (the glob refspec, now
unfenced and classifier-only) rests on.

Run directly (``python3 tests/test_push_corpus.py``) or from the Linux suite
(``tests/linux/33-documentation.sh``). Stdlib only; no network, no key file is
read, no command runs outside a temporary directory (AGENTS.md section 5).
"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from launch_profile_model import (  # noqa: E402
    ALL_ROLES,
    CORPUS_EXPECTS,
    NON_LEAF_ROLES,
    RealGitPremiseTests,
    bash_matches,
    corpus_rows,
    decide,
    decide_deny,
    load_profile,
    split_rule,
)

# Commands no role may pre-grant any more (round 13, D-159). A push behind a
# wrapper is matched as a substring so the wrapper spellings are covered too.
GRANT_FREE_PATTERNS = ("git push", "gh workflow run")


class CorpusShapeTests(unittest.TestCase):
    """The fixture is well-formed, site-free and complete enough to review."""

    def setUp(self):
        self.rows = corpus_rows()

    def test_every_row_has_the_four_fields(self):
        for row in self.rows:
            with self.subTest(cmd=str(row.get("cmd"))):
                self.assertEqual(sorted(row), ["cmd", "expect", "round", "why"])
                self.assertIsInstance(row["cmd"], str)
                self.assertTrue(row["cmd"].strip(), "blank cmd")
                self.assertEqual(row["cmd"], row["cmd"].strip())
                self.assertIn(row["expect"], CORPUS_EXPECTS)
                self.assertIsInstance(row["round"], int)
                self.assertGreater(row["round"], 0)
                self.assertTrue(row["why"].strip(), "empty why")

    def test_no_command_is_listed_twice(self):
        seen = {}
        for row in self.rows:
            with self.subTest(cmd=row["cmd"]):
                self.assertNotIn(row["cmd"], seen, "duplicate cmd, second expect %s"
                                 % row["expect"])
                seen[row["cmd"]] = row["expect"]

    def test_the_corpus_covers_every_round_it_claims(self):
        rounds = {row["round"] for row in self.rows}
        self.assertLessEqual(rounds, set(range(1, 14)))
        # The labels are the phase-1 review rounds that added a push spelling;
        # round 2 added none, so it is the one gap in 1..13.
        self.assertEqual(set(range(1, 14)) - rounds, {2},
                         "corpus rounds: %s" % sorted(rounds))
        self.assertGreaterEqual(len(self.rows), 100)

    def test_the_fixture_is_site_free(self):
        # AGENTS.md rule 1: a tracked fixture carries placeholder paths only.
        text = (Path(__file__).resolve().parent / "fixtures" / "push-corpus.json").read_text(
            encoding="utf-8"
        )
        for banned in ("/home/s/", "localhost", ".local/share/io.github", "omniroute/"):
            with self.subTest(token=banned):
                self.assertNotIn(banned, text)


class CorpusDecisionTests(unittest.TestCase):
    """Each row decides as the fixture says, on the rendered profiles."""

    def setUp(self):
        self.rows = corpus_rows()
        self.profiles = {role: load_profile(role) for role in ALL_ROLES}

    def test_deny_rows_deny_on_every_profile(self):
        for row in [r for r in self.rows if r["expect"] == "deny"]:
            for role in ALL_ROLES:
                with self.subTest(role=role, cmd=row["cmd"]):
                    self.assertTrue(
                        decide_deny(self.profiles[role], "Bash", row["cmd"]),
                        "%s is not denied by %s (%s)" % (row["cmd"], role, row["why"]),
                    )

    def test_allow_lane_rows_are_never_fence_denied(self):
        # The fence set must not reach a push that names only lane refs. Leaf
        # profiles deny EVERY push (the harness leaf fence, spec 2), so the
        # boundary is asserted on the roles that could legitimately push, and
        # the leaf fence is asserted to still catch the push rows. A dispatch is
        # not leaf-fenced (the harness leaf list names push, commit, tag and the
        # other branch writers), so it is only checked on the non-leaf roles.
        for row in [r for r in self.rows if r["expect"] == "allow-lane"]:
            for role in NON_LEAF_ROLES:
                with self.subTest(role=role, cmd=row["cmd"]):
                    self.assertFalse(
                        decide_deny(self.profiles[role], "Bash", row["cmd"]),
                        "%s is denied by %s (%s)" % (row["cmd"], role, row["why"]),
                    )
            if "git push" not in row["cmd"]:
                continue
            for role in ("l3-worker", "l3-reviewer"):
                with self.subTest(role=role, cmd=row["cmd"]):
                    self.assertEqual(
                        decide(self.profiles[role], "Bash", row["cmd"]),
                        "deny",
                        "%s is not denied by the leaf push fence" % row["cmd"],
                    )

    def test_prompt_rows_are_unlisted(self):
        for row in [r for r in self.rows if r["expect"] == "prompt"]:
            for role in NON_LEAF_ROLES:
                with self.subTest(role=role, cmd=row["cmd"]):
                    self.assertEqual(
                        decide(self.profiles[role], "Bash", row["cmd"]),
                        "none",
                        "%s is not unlisted on %s (%s)" % (row["cmd"], role, row["why"]),
                    )

    def test_no_role_pre_grants_a_push_or_a_dispatch(self):
        # Round 13 (D-159): the profiles carry no push and no dispatch grant,
        # so every push above is either fenced on its text or unlisted.
        for role in ALL_ROLES:
            allow = (load_profile(role).get("permissions") or {})["allow"]
            for rule in allow:
                with self.subTest(role=role, rule=rule):
                    for token in GRANT_FREE_PATTERNS:
                        self.assertNotIn(token, rule)


def push_deny_rules():
    """Every Bash deny rule any profile renders that names a push or a dispatch."""
    rules = set()
    for role in ALL_ROLES:
        for rule in (load_profile(role).get("permissions") or {}).get("deny", []):
            name, matcher = split_rule(rule)
            if name == "Bash" and ("push" in matcher or "gh workflow run" in matcher):
                rules.add(rule)
    return rules


class FenceWitnessTests(unittest.TestCase):
    """Each rendered push fence decides a command that really exists.

    KEYDENY (2704196): a deny is a decision on a real command, never a shape
    someone expected. With no pre-granted push left to contradict (round 13),
    the corpus rows are the witnesses: a rule no ``deny`` row matches either
    fences an argv git does not accept (the ``:main:*`` refspec-with-a-tail
    forms round 13 deleted) or means a real fence went missing.
    """

    def setUp(self):
        self.rules = push_deny_rules()
        self.denied = [row["cmd"] for row in corpus_rows() if row["expect"] == "deny"]

    def test_the_fence_set_is_the_size_round_13_left_it(self):
        # Asserted separately so an emptied deny list fails here rather than
        # leaving the witness loop comparing against nothing.
        self.assertGreaterEqual(len(self.rules), 40)

    def test_every_push_rule_has_a_deny_witness(self):
        for rule in sorted(self.rules):
            _, matcher = split_rule(rule)
            with self.subTest(rule=rule):
                self.assertTrue(
                    any(bash_matches(matcher, cmd) for cmd in self.denied),
                    "no corpus deny row matches %s" % rule,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
