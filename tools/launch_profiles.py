#!/usr/bin/env python3
"""Render the per-role Claude Code launch profiles (ORCH-A1 phase 1).

Tracked templates ``configuration/launch-profiles/<role>.settings.example.
json`` are pure render output of this module - do not edit them by hand.
Regenerate after any harness or grant change::

    python3 tools/launch_profiles.py render --out configuration/launch-profiles

and gate drift in CI::

    python3 tools/launch_profiles.py render --check

A new small module rather than a ``lib/agent_harness.py`` subcommand, on
single-responsibility grounds: that generator renders opencode/OpenHands
configs consumed by the *installers*, while these profiles are Claude Code
``--settings`` inputs consumed (phase 2) by the *spawner* - a different
output surface, a different consumer, and a HIGH control-plane review
cadence. The render/``--check`` style follows ``tools/registry.py`` and
``lib/agent_harness.py`` ``vendor --check`` (exit 1 naming each differing
file). The harness file is read, never written (phase 1 reads
``catalog/agent-harness.json`` fences; out of scope).

Settings shape (review round 1 - the shape the CLI actually reads): Claude
Code CLI 2.1.283 reads permission rules ONLY under a top-level
``permissions`` object - ``{"permissions": {"allow": [...], "deny": [...]}}``.
Top-level ``allow``/``deny`` keys are ignored silently, and
``permissionMode`` / ``permissionPrompts`` are NOT settings keys, so the
render emits neither. No ``defaultMode`` either: no accepted value
(``default``, ``acceptEdits``, ``plan`` - never ``bypassPermissions``)
expresses the fail-closed prompting behaviour the spec wanted, so that
behaviour is dropped from this file and stated here instead: fail-closed
for non-interactive sessions comes from the launch flags (a Phase 2
concern), not from this file. The remaining top-level fields (``role``,
``harnessRole``, ``mcpConfig``) are AutoOS metadata the CLI ignores - the
spec's launch identity and MCP-document reference, kept because the spec
requires them.

Deny composition, in committed order (deny entries are order-independent -
Claude Code evaluates deny before allow - so this order is for reviewers):

1. the push-to-``main`` fence set (spec 3.3): leading-``*`` ``git push``
   globs, so a ``sudo``/``env``/``xargs`` prefix does not escape the fence,
   with a space before ``main`` so a lane merely containing "main"
   (``main2``) is not fenced, while every spelling of the real ref is
   (``:main``, ``HEAD:main``, ``+main``, ``refs/heads/main``,
   ``--force``/``-f``, ``--tags``); plus the H2 forms without the word
   "main" as a ref (``--all``/``--mirror`` in both positions, any ``HEAD``
   push, a bare ``git push`` with no refspec - exact for the bare command,
   ``* ``-prefixed for the bare command under a prefix); plus the H3
   wrapper/option forms (``git -C ... push``, ``git --git-dir=... push``,
   ``git -c ... push`` - each denies every push through that argv shape,
   lane refs included, because a wrapper push is never pre-granted);
   plus the round-6 class fences (spec 3.3): any git push containing
   a tab, CR or LF is denied on every role (bash splits on them; the
   fences match spaces only), and any git push whose text contains a
   double quote, a single quote, a dollar sign or a backtick is denied
   on every role (the fences see literal refs only - quoted, variable
   and substituted refs deny outright, fail closed).
2. the ``gh workflow run --ref main`` fence (spec Q4): the L1
   ``gh workflow run`` grant stays, an explicit ``--ref main`` (or
   ``--ref=main``) dispatch does not - and the round-6 class fence: a
   dispatch whose text contains a quote, a dollar sign or a backtick
   denies on every role (the fences see literal refs only).
3. the secret/credential entries of ``fences.bash_deny_all``, selected by
   ``bash_secret_patterns``: an entry is secret-relevant when its
   ``*``-stripped core contains a ``*``-stripped core of some
   ``fences.read_deny_all`` entry (zero restatement - computed), or when
   named in ``BASH_SECRET_EXTRA`` (shell-only disclosure vectors with no
   read counterpart: ``env``/``printenv``, the opencode config dirs,
   ``set``, ``/proc``). Operational fences (``git merge``/``rebase``,
   ``gh *``, ``rm -rf``, ``sudo``, ``docker`` ...) are deliberately NOT
   rendered: under deny-precedence a rendered ``gh *`` would swallow the
   L1 ``gh workflow run`` grant the spec pre-approves. They stay
   fenced by the harness overlay for non-claude clients.
4. every ``fences.read_deny_all`` pattern as a ``Read(...)`` deny.
5. profile-specific secret extras the harness does not list
   (``*.vault.yml``, ``*.pem`` - spec 3.3 names them).
6. ``~/.claude.json`` writes (``Edit``, ``Write`` and the shell
   ``tee``/redirect/``sed -i`` forms - spec 3.3).
7. the l2-orchestrator-only single-ref push fences (round 4, F1/F2;
   tightened round 5, by shape not tokens): ``*git push * *:*`` (any
   refspec colon), ``*git push *refs/*`` (any refs/ path),
   ``*git push *--delete*`` / ``* -d`` (delete flags),
   ``git push origin L2-* *`` / ``git push -u origin L2-* *`` (a space
   after the L2 token means a second argument of any kind - a second
   refspec or a trailing option), ``git push --* origin L2-*`` (any long
   option before the ref) plus ``git push -f/-d origin L2-*`` (the known
   short force/delete flags; ``--prune`` and friends are long-only) - so
   only exactly ``git push origin L2-<name>`` / ``git push -u origin
   L2-<name>`` (one ref, nothing after it, destination = source) allows.
   A lone ``git push -* origin L2-*`` shape would also match the ``-u``
   spelling, and deny beats allow regardless of order, so no ordering
   keeps ``-u`` allowed - hence the ``--*`` + short-flag split, stated
   here so a reviewer knows why the short flags stay enumerated.
   Rendered ONLY into the l2-orchestrator profile; the L1 roles keep
   their existing grants unchanged, so these fences cannot shadow
   anything the L1 roles need.

Two deny-precedence consequences a reviewer must know (both pinned by
``tests/test_launch_profiles.py``):

- The harness ``read_allow_all`` hole (``*.env.example``,
  ``*configuration/api-keys.example.yml``) is inexpressible here: the
  opencode overlay means it as "a later allow", and a later allow loses
  to deny under Claude Code. The render emits no dead allow entries that
  would imply a hole that does not exist, so example templates under a
  denied glob stay denied (fail closed).
- Leaf profiles (``l3-*``) additionally render ``fences.bash_deny_leaf``
  verbatim: a leaf inherits the leaf fences unchanged (spec 2 - a profile
  may make a leaf grant smaller, never larger), so a lane push denies on
  leaves while L1 pre-grants it.

Role table (spec 2 launch identities; the Q2 default - the ``role`` field
carries the launch identity, the MCP document rides as ``mcpConfig``):

- ``l1-coordinator`` and ``l1-routing`` are the only roles with the full
  pre-grants (review round 1, spec 3.2): the lane-branch push and
  ``gh workflow run`` grants, with ``l1-routing`` alone extending them
  with the gateway ``apply.sh`` run grant (A1-D4, the MISTRALFIX precedent
  - the model may run the script but stays denied reading its key, which
  the script reads in its own process).
- ``l2-orchestrator`` carries a NARROWER push grant (review round 3,
  D-138, tightened round 4, shaped round 5): exactly one ``L2-*`` ref,
  same-name, no options except ``-u`` - ``Bash(git push origin L2-*)``
  plus the ``-u`` spelling - and ``gh workflow run`` only with ``--ref``
  on that same prefix - never a bare run; per role, not per session: any
  l2-orchestrator session may push/dispatch on any ``L2-*`` lane branch,
  not only the one it created - per-session scoping is not expressible
  in a per-role profile. No stated L2 branch-naming convention exists in
  the skill or docs text; the live branches are ``L2-<name>/...``
  (``L2-general/*``), so the grant keys on the ``L2-`` prefix. The
  always-deny fence set renders into the L2 profile unchanged, and deny
  still beats the new allows (an ``L2-x:main`` refspec matches the allow
  glob and still denies); round 4 adds L2-only fences so the allow glob
  cannot span a refspec colon, a ``refs/`` path or a delete flag, and
  round 5 denies by shape anything after the branch token (a second
  argument of any kind) and any option before the ref but ``-u`` - only
  the same-name single-ref push (destination = source) allows.
- ``l0-router`` maps to the orchestrator harness role but carries no
  pre-grant: unlisted stays fail-closed under the launch flags.
- ``l3-worker``/``l3-reviewer`` map to the leaf harness roles with an
  empty pre-grant set (spec Q9 default: no pre-grants beyond read-only).
- ``l3-worker``/``l3-reviewer`` map to the leaf harness roles with an
  empty pre-grant set (spec Q9 default: no pre-grants beyond read-only).
- ``mcpConfig`` names the restart-spec section 8 documents
  (``configuration/mcp/<stem>.json``); those files land in a later lane,
  the reference is by name only.

Stdlib only. Path-independent: everything is anchored on the repository
root derived from this file's own location.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HARNESS_PATH = ROOT / "catalog" / "agent-harness.json"
DEFAULT_OUT_DIR = ROOT / "configuration" / "launch-profiles"

# Launch identity -> harness role, MCP document stem, grant set.
ROLES = {
    "l0-router": {"harness": "orchestrator", "mcp": "l2-orchestrator", "grants": "none"},
    "l1-coordinator": {"harness": "orchestrator", "mcp": "l2-orchestrator", "grants": "coordinator"},
    "l1-routing": {"harness": "orchestrator", "mcp": "l2-orchestrator", "grants": "routing"},
    "l2-orchestrator": {"harness": "suborchestrator", "mcp": "l2-orchestrator", "grants": "l2"},
    "l3-worker": {"harness": "leaf-implementer", "mcp": "l3-worker", "grants": "leaf"},
    "l3-reviewer": {"harness": "leaf-reviewer", "mcp": "l3-reviewer", "grants": "leaf"},
}

# The shared grant sets, rendered per role so they cannot drift: only the L1
# roles share the full lane-push / workflow pre-grants, l1-routing extends
# them with exactly the apply.sh grant, l2-orchestrator carries exactly the
# narrower L2-*-only set (plus its L2-only same-name-push deny fences, not
# part of the allow set), every other role carries no pre-grant (spec 3.2
# as fixed in review round 1 and narrowed in round 3, A1-D4, Q9).
COORDINATOR_ALLOW = (
    "Bash(git push:*)",
    "Bash(gh workflow run:*)",
)
APPLY_ALLOW = "Bash(bash configuration/omniroute/apply.sh:*)"
# L2 lane-branch prefix (round 3, D-138; tightened round 4, shaped round
# 5): narrower than the L1 grant. Exactly one `L2-*` ref, same-name, no
# options except `-u` - the plain and -u spellings, the exact Claude Code
# rule syntax the L1 grant already uses - and workflow dispatch only with
# --ref on that prefix (both --ref spellings, mirroring the GH_REF_MAIN
# fence below), never a bare `gh workflow run`. The grant is per role,
# not per session: any l2-orchestrator session may push/dispatch on any
# `L2-*` lane branch - per-session scoping is not expressible in a
# per-role profile.
# Non-origin remotes, other prefixes and wrapper push shapes stay
# unlisted; main in every spelling stays denied by the unchanged fence
# set (deny wins); refspec/refs/delete shapes (round 4) and any trailing
# arg or leading option but `-u` (round 5, by shape) stay denied by the
# L2-only L2_PUSH_DENY fences (deny wins).
L2_PUSH_ALLOW = (
    "Bash(git push origin L2-*)",
    "Bash(git push -u origin L2-*)",
)
L2_WORKFLOW_ALLOW = (
    "Bash(gh workflow run * --ref L2-*)",
    "Bash(gh workflow run * --ref=L2-*)",
)
L2_ALLOW = L2_PUSH_ALLOW + L2_WORKFLOW_ALLOW

# L2 same-name-push fences (round 4, F1/F2) plus the single-ref shape
# fences (round 5): the L2 allow entries above are whole-line globs, so
# `*` spans a refspec `:` and the destination - without the round-4
# entries, `git push origin L2-x:<anything>` allowed an overwrite of any
# non-main branch, and `--delete`/`-d` allowed branch deletion. Round 5
# closes the same hole by shape: the allow glob's trailing `*` also spans
# the space after the ref, so `git push origin L2-x L1-foo` (a second
# refspec - git pushes local L1-foo onto origin L1-foo) and `git push
# origin L2-x --prune` (deletes remote branches) allowed too. Anything
# following the branch token now denies (`git push origin L2-* *` /
# `git push -u origin L2-* *` - a space after the L2 token means a second
# argument of any kind), and any option before the ref denies but `-u`.
# A lone `git push -* origin L2-*` would also match the `-u` spelling,
# and deny beats allow regardless of order, so no ordering keeps `-u`
# allowed: long options deny via one `git push --* origin L2-*` shape
# while the known short force/delete flags stay enumerated (`-f`, `-d`;
# prune has no short spelling - `--prune` is covered by `--*`). Only
# `git push [-u] origin L2-<name>` (one ref, nothing after it,
# destination = source) allows. The round-4 colon/refs/delete entries
# stay as defence in depth. Rendered ONLY into the l2-orchestrator
# profile - only that profile carries the L2 allow glob, and
# l1-coordinator/l1-routing keep the full lane-push grant, so these
# fences cannot shadow anything the L1 roles need. The ` -d` forms carry
# a leading space so a branch merely ending in `-d` (`L2-x-d`) stays
# outside the fence.
L2_PUSH_DENY = (
    "*git push * *:*",
    "*git push *refs/*",
    "*git push *--delete*",
    "*git push * -d *",
    "*git push * -d",
    "git push origin L2-* *",
    "git push -u origin L2-* *",
    "git push --* origin L2-*",
    "git push -f origin L2-*",
    "git push -d origin L2-*",
)

GRANT_SETS = {
    "coordinator": tuple(COORDINATOR_ALLOW),
    "routing": tuple(COORDINATOR_ALLOW) + (APPLY_ALLOW,),
    "l2": tuple(L2_ALLOW),
    "none": (),
    "leaf": (),
}

# Push-to-main fence set (spec 3.3, H2, H3): leading-`*` globs so a
# sudo/env/xargs prefix does not escape the fence. The space before `main`
# keeps a lane merely containing "main" (`main2`) outside the fence.
# Round 6: any git push containing a tab, CR or LF is denied on every
# role (bash splits on them; the fences match spaces only).
MAIN_FENCE = (
    "Bash(*git push * main)",
    "Bash(*git push * main *)",
    "Bash(*git push * main:*)",
    "Bash(*git push *:main*)",
    "Bash(*git push *HEAD:*main*)",
    "Bash(*git push *+*main*)",
    "Bash(*git push *refs/heads/main*)",
    "Bash(*git push *--force*)",
    "Bash(*git push *-f *)",
    "Bash(*git push *-f)",
    "Bash(*git push *--tags*)",
    # H2: push forms without the word `main` as a ref - denied on every
    # role. A bare `git push` pushes the checked-out branch, which may be
    # main; --all/--mirror push every ref, which includes main.
    "Bash(*git push --all*)",
    "Bash(*git push * --all*)",
    "Bash(*git push --mirror*)",
    "Bash(*git push * --mirror*)",
    "Bash(*git push * HEAD*)",
    "Bash(git push)",
    "Bash(* git push)",
    # H3: wrapper/option argv shapes - the push hides behind another argv
    # form, so the anchored fence cannot see it. Each denies every push
    # through that shape, lane refs included.
    "Bash(git -C * push*)",
    "Bash(git --git-dir* push*)",
    "Bash(git -c * push*)",
    # Round 6, whitespace class: every fence above matches a literal ASCII
    # space as the argument separator, but the allow globs' `*` spans any
    # character and bash word-splits on TAB, CR and LF too - so
    # `git push origin L2-x<TAB>main` allowed before this round. A push
    # never legitimately needs a non-space separator, so any `git push`
    # containing a tab, CR or LF is denied on every role (bash splits on
    # them; the fences match spaces only) - deny the class, fail closed,
    # without enumerating positions. Written with Python `\t`/`\r`/`\n`
    # escapes so the rendered JSON carries `\t` `\r` `\n`.
    "Bash(*git push*\t*)",
    "Bash(*git push*\r*)",
    "Bash(*git push*\n*)",
    # Round 6, quoting/expansion class: the fences match command text
    # literally, so `git push origin "main"` (or `'main'`, `$REF`,
    # `$(...)`, backticks) allowed under `Bash(git push:*)` - no fence
    # entry carries a literal ` main`. A push never needs quoting or
    # expansion here; the ref must be literal text so the fences can see
    # it, so any `git push` whose text contains a double quote, a single
    # quote, a `$` or a backtick is denied on every role (fail closed).
    # Do NOT normalise quoting in the decide() model - it must keep
    # mirroring the CLI's literal-text matching.
    'Bash(*git push*"*)',
    "Bash(*git push*'*)",
    "Bash(*git push*$*)",
    "Bash(*git push*`*)",
)

# gh workflow run naming main stays denied under the L1 grant. Round 6,
# quoting/expansion class (same rationale as the push class above): the
# fences see literal `--ref <name>` text only, so a quoted, variable or
# substituted ref (`--ref "main"`, `--ref $R`, `--ref $(...)`) must deny
# outright on every role - the ref must be literal text so the fences can
# see it (fail closed).
GH_REF_MAIN = (
    "Bash(gh workflow run *--ref *main*)",
    "Bash(gh workflow run *--ref=*main*)",
    'Bash(*gh workflow run*"*)',
    "Bash(*gh workflow run*'*)",
    "Bash(*gh workflow run*$*)",
    "Bash(*gh workflow run*`*)",
)

# Shell-only secret-disclosure vectors with no read_deny_all counterpart,
# hence not derivable and named here (the one hand-maintained secret-
# adjacent list; the profiles carry these only via the render, and
# tests/test_launch_profiles.py fails if one leaves the harness).
BASH_SECRET_EXTRA = (
    "env",
    "env *",
    "printenv*",
    "*.config/opencode*",
    "*.local/share/opencode*",
    "set",
    "cat /proc/*",
)

# Secret extras the harness fences do not list (spec 3.3 names them).
SECRET_EXTRA_BASH = ("*.vault.yml", "*.pem")
SECRET_EXTRA_READ = ("*.vault.yml", "*.pem")

# ~/.claude.json writes: Edit, Write and the shell forms (spec 3.3).
# Note: the CLI does not consult Write path rules - the Read/Edit fences
# carry that case; the Write entry stays rendered anyway (harmless).
CLAUDE_JSON_WRITES = (
    "Edit(*.claude.json*)",
    "Write(*.claude.json*)",
    "Bash(*>*.claude.json*)",
    "Bash(*tee *.claude.json*)",
    "Bash(*sed*-i*.claude.json*)",
)


def bash_secret_patterns(bash_deny_all, read_deny_all):
    """The secret/credential entries of ``bash_deny_all``, in harness order.

    An entry is selected when its ``*``-stripped core contains a
    ``*``-stripped core of some ``read_deny_all`` entry (computed, never
    restated), or when named in ``BASH_SECRET_EXTRA``. Pure: no I/O.
    """
    read_cores = [p.replace("*", "") for p in read_deny_all]
    read_cores = [c for c in read_cores if c]
    selected = []
    for pattern in bash_deny_all:
        core = pattern.replace("*", "")
        if any(read_core in core for read_core in read_cores):
            selected.append(pattern)
        elif pattern in BASH_SECRET_EXTRA:
            selected.append(pattern)
    return selected


def render_role(role, harness):
    """Render one profile document (pure: no I/O).

    The permission rules live under ``permissions`` - the only shape the
    CLI reads. ``role``/``harnessRole``/``mcpConfig`` are AutoOS metadata
    the CLI ignores. Fail-closed for non-interactive sessions comes from
    the launch flags (Phase 2), not from this file.
    """
    fences = harness["fences"]
    spec = ROLES[role]
    deny = list(MAIN_FENCE)
    deny.extend(GH_REF_MAIN)
    if role == "l2-orchestrator":
        # Round 4 (F1/F2) plus round 5 (single-ref shape): the L2-only
        # fences - only this profile carries the L2 allow glob, so only
        # it needs the fence; every other profile's deny list is
        # byte-identical to before.
        deny.extend("Bash(%s)" % pattern for pattern in L2_PUSH_DENY)
    deny.extend("Bash(%s)" % p for p in bash_secret_patterns(
        fences["bash_deny_all"], fences["read_deny_all"]))
    deny.extend("Read(%s)" % p for p in fences["read_deny_all"])
    deny.extend("Bash(%s)" % p for p in SECRET_EXTRA_BASH)
    deny.extend("Read(%s)" % p for p in SECRET_EXTRA_READ)
    deny.extend(CLAUDE_JSON_WRITES)
    if spec["grants"] == "leaf":
        deny.extend("Bash(%s)" % p for p in fences["bash_deny_leaf"])
    return {
        "//": "Generated from catalog/agent-harness.json by "
              "tools/launch_profiles.py render - do not edit; site-free "
              "example, the runtime file is generated and git-ignored",
        "role": role,
        "harnessRole": spec["harness"],
        "mcpConfig": "configuration/mcp/%s.json" % spec["mcp"],
        "permissions": {
            "allow": list(GRANT_SETS[spec["grants"]]),
            "deny": deny,
        },
    }


def render_text(role, harness):
    """Serialize one profile the way the committed template stores it."""
    return json.dumps(render_role(role, harness), indent=2, ensure_ascii=False) + "\n"


def load_harness(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def cmd_render(args):
    harness = load_harness(args.harness)
    rendered = {role: render_text(role, harness) for role in ROLES}
    if args.check:
        differing = []
        for role, text in rendered.items():
            path = Path(args.out) / (role + ".settings.example.json")
            try:
                current = path.read_bytes()
            except OSError:
                current = None
            if current != text.encode("utf-8"):
                differing.append(str(path))
        if differing:
            for path in differing:
                print("launch-profiles render: differs %s" % path)
            return 1
        print("launch-profiles render: ok")
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for role, text in rendered.items():
        path = out / (role + ".settings.example.json")
        path.write_text(text, encoding="utf-8", newline="\n")
        print("launch-profiles render: wrote %s" % path)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="launch-profiles")
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render")
    render.add_argument("--harness", default=str(DEFAULT_HARNESS_PATH))
    render.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    render.add_argument("--check", action="store_true")
    render.set_defaults(func=cmd_render)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
