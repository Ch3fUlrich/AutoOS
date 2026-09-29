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

1. the push-to-``main`` fence set (spec 3.3, round 13 / routing-00 D-159):
   globs anchored on ``*git*push``, so neither a ``sudo``/``env``/``xargs``
   prefix nor a ``git -C``/``git --git-dir``/``git -c`` wrapper hides a
   ``main`` ref from the fence — round 13 dropped the three wrapper blanket
   denies, and the glob between the two tokens is what keeps ``git -C x push
   origin main`` denied while ``git -C x push origin L1-x`` reaches the
   classifier; both rows live in ``tests/fixtures/push-corpus.json``, which is
   also where the ``docker push`` look-alike is pinned as *not* fenced. A space
   before ``main`` keeps a lane
   merely containing "main" (``main2``, ``L1-main-fix``) outside the fence,
   while every spelling of the real ref is inside it as a WHOLE ref name —
   ``main``, ``heads/main``, ``refs/heads/main`` in bare, source and
   destination position, each followed only by end-of-text, a space or a
   refspec colon (git's ref-name rules give exactly those three spellings
   that resolve to ``refs/heads/main``, proven with real git by
   ``tests/test_push_corpus.py``), plus ``:main``, ``HEAD:main``, ``+main``;
   plus the push-wide flags that reach main whatever the ref
   (``--force``/``-f``, ``--all``/``--mirror`` in both positions, any ``HEAD``
   push, ``--tags``/``--follow-tags``/``refs/tags``/``tag``), plus ``:HEAD``
   (``<src>:HEAD`` creates a remote branch literally named ``HEAD``, proven
   with real git; main is unchanged but every tool that resolves HEAD breaks).
   What round 13 REMOVED from this set, with the reason: the bare
   ``git push`` / ``git push origin`` no-ref forms, the ``:``/``+:`` matching
   pushes and ``@``, and the round 6-8 character classes (tab/CR/LF, quotes,
   ``$``, backtick, backslash, ``{``, ``&``, ``>``, ``<``, glob characters,
   ``;``, ``#``). Those fences existed to stop a *pre-granted* glob from
   letting an unreviewed ref through; with no push grant left, every one of
   those commands is simply unlisted and goes to the classifier, which sees
   the real argv. Each removed hazard is a row in
   ``tests/fixtures/push-corpus.json`` (expect ``prompt``) and an OPEN
   residual owned by the hooks lane (spec 3.3, HOOKS H2).
2. the ``gh workflow run --ref main`` fence (spec Q4): an explicit
   ``--ref main`` / ``--ref=main`` dispatch stays denied on every role. The
   round 6-9 shape and class fences (bare run, ``-R``/``--repo``, a second
   ``--ref``, ``-r``, empty ``--ref``, quotes, metacharacters, whitespace)
   are removed with the dispatch grant — an unlisted dispatch now reaches the
   classifier, which reads the real argv; the hazards are corpus ``prompt``
   rows and HOOKS H2 residuals.
3. the secret/credential entries of ``fences.bash_deny_all``, selected by
   ``bash_secret_patterns``: an entry is secret-relevant when its
   ``*``-stripped core contains a ``*``-stripped core of some
   ``fences.read_deny_all`` entry (zero restatement - computed), or when
   named in ``BASH_SECRET_EXTRA`` (shell-only disclosure vectors with no
   read counterpart: ``env``/``printenv``, the opencode config dirs,
   ``set``, ``/proc``). Operational fences (``git merge``/``rebase``,
   ``gh *``, ``rm -rf``, ``sudo``, ``docker`` ...) are deliberately NOT
   rendered: a rendered ``gh *`` denies the whole verb class, so the
   classifier never gets to judge the one dispatch the role actually needs —
   a deny is not a narrower prompt, it is a refusal. They stay fenced by the
   harness overlay for non-claude clients.
4. every ``fences.read_deny_all`` pattern as a ``Read(...)`` deny.
5. profile-specific secret extras the harness does not list
   (``*.vault.yml``, ``*.pem`` - spec 3.3 names them).
6. ``~/.claude.json`` writes (``Edit`` and ``Write`` - spec 3.3; round
   9: the shell ``tee``/redirect/``sed -i`` Bash forms were dead
   duplicates of the harness-rendered ``Bash(*.claude.json*)`` and
   are dropped, the generic entry carries those cases).

Two deny-precedence consequences a reviewer must know (pinned by
``tests/test_launch_profiles.py`` and ``tests/test_push_corpus.py``):

- The harness ``read_allow_all`` hole (``*.env.example``,
  ``*configuration/api-keys.example.yml``) is inexpressible here: the
  opencode overlay means it as "a later allow", and a later allow loses
  to deny under Claude Code. The render emits no dead allow entries that
  would imply a hole that does not exist, so example templates under a
  denied glob stay denied (fail closed).
- Leaf profiles (``l3-*``) additionally render ``fences.bash_deny_leaf``
  verbatim: a leaf inherits the leaf fences unchanged (spec 2 - a profile
  may make a leaf grant smaller, never larger), so a push denies on leaves
  and on every other role there is simply no push grant either.

Role table (spec 2 launch identities; the Q2 default - the ``role`` field
carries the launch identity, the MCP document rides as ``mcpConfig``):

- No role pre-grants a push or a workflow dispatch any more (round 13,
  routing-00 D-159). Twelve review rounds narrowed a grant that had to exist
  only because the classifier refused the *shape* of an orchestrator
  relaunching sessions; with the grant gone, a lane push is an unlisted
  command and the classifier judges the real argv, which is what the fence
  set was always a stand-in for. The push fence keeps exactly what no
  classifier judgement should need: ``main`` in every spelling.
- ``l1-routing`` is the only role with any allow entry at all - the gateway
  ``apply.sh`` run grant (A1-D4, the MISTRALFIX precedent: the model may run
  the script but stays denied reading its key, which the script reads in its
  own process). ``l1-coordinator`` is that role minus the apply grant,
  ``l2-orchestrator``/``l0-router`` carry none, and
  ``l3-worker``/``l3-reviewer`` map to the leaf harness roles with an empty
  pre-grant set (spec Q9 default: no pre-grants beyond read-only).
- ``l0-router`` maps to the orchestrator harness role but carries no
  pre-grant: unlisted stays fail-closed under the launch flags.
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

# The shared grant sets, rendered per role so they cannot drift. Round 13
# (routing-00 D-159) removes every push and workflow-dispatch pre-grant, so
# only the gateway ``apply.sh`` run grant survives, and only on ``l1-routing``
# (A1-D4, the MISTRALFIX precedent: the model may run the reviewed script but
# stays denied reading its key, which the script reads in its own process).
# A lane push is now an unlisted command: it reaches the permission prompt,
# which under the non-interactive launch flags is the classifier judging the
# real argv - the thing the twelve rounds of grant-shaping denies were a
# literal-text stand-in for. Spec 3.3 hands the residual hazards to HOOKS H2.
APPLY_ALLOW = "Bash(bash configuration/omniroute/apply.sh:*)"

GRANT_SETS = {
    "coordinator": (),
    "routing": (APPLY_ALLOW,),
    "l2": (),
    "none": (),
    "leaf": (),
}

# Push-to-main fence set (spec 3.3; round 13, routing-00 D-159).
#
# The anchor is ``*git*push`` - a glob between the two tokens, not the
# contiguous literal ``git push``. That is what lets round 13 drop the three
# wrapper blanket denies (``git -C ... push``, ``git --git-dir=... push``,
# ``git -c ... push``) without opening a main hole: ``git -C <path> push origin
# main`` is denied by the same entry that denies the plain form, which is the row
# ``tests/fixtures/push-corpus.json`` pins for exactly that reason. The leading
# ``*`` keeps a ``sudo``/``env``/``xargs`` prefix inside the fence as before. The
# ``git`` token stays in the anchor because the fence must not swallow an
# unrelated push: ``docker push registry/app:main`` is an image tag, not a
# branch, and a deny - unlike the prompt a lane push now gets - has no way to
# explain itself.
#
# ``main`` is fenced as a WHOLE ref name, never as a substring. Git's ref-name
# rules give exactly three spellings that resolve to ``refs/heads/main``
# (``main``, ``heads/main``, ``refs/heads/main``) - proven with real git by
# ``tests/test_push_corpus.py`` - and each is fenced in every position a push
# argv can put a ref in: after a space (a bare ref or a remote-prefixed one),
# after a colon (a refspec destination, or a delete) and after a ``+`` (the
# forced-push prefix, on either side of the colon); each of those ends the ref
# at end-of-text, at a space (another argument follows) or at a colon (the ref
# is a refspec source). So ``L1-x:heads/main-fix``, ``L1-x:L1-main-fix`` and
# ``L1-routing/maintenance`` stay outside the fence while ``git push origin
# L1-x:refs/heads/main`` is inside it - the previous ``*refs/heads/main*`` entry
# denied both.
#
# The push-wide flags reach ``main`` whatever the ref says, so they stay fenced
# with it: ``--force``/``-f``, ``--all``/``--mirror`` in either position, any
# ``HEAD`` push (``<src>:HEAD`` is main-unaffected but creates a remote branch
# literally named ``HEAD`` - verified with real git - and breaks every tool that
# resolves HEAD), and the tag forms (``--tags``, ``--follow-tags``,
# ``refs/tags``, ``tag <name>``).
#
# Round 13 REMOVED the rest of the old set: the bare ``git push`` / ``git push
# origin`` no-ref forms, the ``:``/``+:`` matching pushes and ``@``, the wrapper
# shapes, and the round 6-8 character classes (tab/CR/LF, quotes, ``$``,
# backtick, backslash, ``{``, ``&``, ``>``, ``<``, glob characters, ``;``,
# ``#``). Each of those existed to close a gap a *pre-granted* glob could not
# see; with no glob there is no gap to close, and the command is simply
# unlisted - the classifier reads the real argv. The hazards they covered are
# OPEN residuals owned by the hooks lane (spec 3.3, HOOKS H2) and are pinned as
# ``prompt`` rows in the corpus.
_MAIN_ANCHOR = "*git*push"

# The three spellings that resolve to ``refs/heads/main`` (git ref-name rules),
# proven against a real git server in ``tests/test_push_corpus.py``.
_MAIN_REFS = ("main", "heads/main", "refs/heads/main")

# The (separator, suffix) pairs that can surround a main-resolving ref in one
# push argv: a space glues it to the text before it (``origin main``), a colon
# makes it a refspec destination (``L1-x:main``, ``:main``) and a ``+`` is the
# force prefix (``+main``, ``src:+main`` - a ``*`` between covers the colon a
# ``+`` can sit behind). What can follow the ref: end of text, a space (more
# arguments follow) or a colon (the ref is a refspec *source*).
#
# A refspec carries exactly one colon, so the colon separator and the colon
# suffix cannot both apply: ``git push origin X:main:Y`` is not a refspec git
# accepts, and round 13 drops those three shapes rather than render a deny that
# matches no command. Round 13 also dropped every suffix outside these three
# (``main;``, ``main&``, ``main"``): that is not a ref git resolves to main in
# one word, so the command is left to the classifier.
_MAIN_SHAPES = tuple(
    (sep, suffix)
    for suffix in ("", " *", ":*")
    for sep in ((" ", ":", "+") if suffix != ":*" else (" ", "+"))
)

MAIN_REF_FENCE = tuple(
    "Bash(%s*%s%s%s)" % (_MAIN_ANCHOR, sep, ref, suffix)
    for sep, suffix in _MAIN_SHAPES
    for ref in _MAIN_REFS
)

# Push-wide flags: denied because they carry every ref, main included, or
# resolve a symbolic ref the main fences cannot see.
MAIN_FLAG_FENCE = tuple(
    "Bash(%s%s)" % (_MAIN_ANCHOR, tail)
    for tail in (
        " *--force*", " *-f *", " *-f", " *--all*", " *--mirror*",
        "* HEAD*", " *:HEAD", " *:HEAD *", " *--tags*", " *--follow-tags*",
        " *refs/tags*", " *tag *",
    )
)

MAIN_FENCE = MAIN_REF_FENCE + MAIN_FLAG_FENCE

# A workflow dispatch names a branch, so ``main`` is fenced as a whole ref name
# after ``--ref``/``--ref=`` (both spellings), at end of text or followed by
# another argument. The leading ``*`` keeps a wrapper prefix inside the fence,
# as push's anchor does. ``--ref L1-main-fix`` is NOT denied: a deny beats the
# prompt, and a lane name merely containing "main" is a lane, not main.
# Round 13 removed the rest of the dispatch fences with the dispatch grant -
# a bare run (default branch = main), ``-R``/``--repo`` (another repo), a second
# ``--ref`` and ``-r`` (last-flag-wins), an empty/valueless ``--ref``, and the
# quoting, metacharacter and whitespace classes. Each is an unlisted command
# the classifier now judges from the argv; each hazard is an OPEN residual owned
# by HOOKS H2 (spec 3.3) and a ``prompt`` row in the corpus.
GH_REF_MAIN = tuple(
    "Bash(*gh workflow run*--ref%s%s%s)" % (sep, ref, suffix)
    for sep in (" ", "=")
    for ref in _MAIN_REFS
    for suffix in ("", " *")
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

# ~/.claude.json writes: Edit and Write (spec 3.3). Round 9: the shell
# tee/redirect/sed-i Bash forms were dead duplicates of the
# harness-rendered `Bash(*.claude.json*)` (decide() rows prove the
# generic entry matches the same commands), so they are dropped here -
# the generic entry carries those cases. Note: the CLI does not consult
# Write path rules - the Read/Edit fences carry that case; the Write
# entry stays rendered anyway (harmless).
CLAUDE_JSON_WRITES = (
    "Edit(*.claude.json*)",
    "Write(*.claude.json*)",
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
