# ORCH-A1 — Role launch profiles with pre-granted permissions (spec)

- Date: 2026-09-28.
- Path: `docs/plans/2026-09-28-orch-a1-role-launch-profiles-spec.md`.
- Task: ORCH-A1 (`docs/tasks.md`).
- Status: **spec, not implemented.** The code, the settings files, the enforcement and the tests
  are a LATER lane (§8); nothing here is built by this change.
- Owner: `autoos-L1-backlog`.
- Related: ORCH-C1 (REVIVE), ORCH-A2 (`policy/*.yml`), ORCH-D1 (`enforced`/`advisory` rule tags),
  `.agents/skills/unattended-orchestration/references/runner-setup.md`, the restart spec
  (`docs/plans/2026-09-28-restart-spec.md` §8, `configuration/mcp/<role>.json`), KEYDENY
  (`2704196`), RISKTIER-a (operator Q-013 / D-060), `tools/autoos_risk.py`,
  `catalog/ai-registry.json` `policy.risk_rules` / `review_counts`.

**This repo is public. This spec is site-free on purpose:** every host, account, path, branch
prefix and token is a placeholder (`<owner>`, `<registry>`, `<omnigraph-url>`, `<lan-cidr>`,
`<worktree>`, `<user>`). Real values live in git-ignored files, never here (AGENTS.md rule 1).

## Why

- On **2026-09-28** a Claude Code auto-mode session refused a *read-only* planning call for the
  **session-relaunch-watchdog** task with the reason **`[Create Unsafe Agents]`**. The call only
  read files and wrote a plan; what tripped the classifier was the *shape* of the task — a session
  arranging to launch and relaunch other sessions — not anything the call actually did.
- The same refusal class is already recorded in the repo, measured, for a different launcher:
  `.agents/skills/unattended-orchestration/references/l3-routing.md:148-149` — *"The classifier
  refuses raw `opencode run` ('Create Unsafe Agents', measured)."* So this is a stable property of
  the refusal, not a one-off.
- The repo's answer is **not** to argue with the classifier and **not** to widen a grant until the
  refusal goes away. It is to make each launch come from a **per-role, human-pre-reviewed launch
  profile**: a narrow, written-down set of operations the role may do without a prompt, and a
  set it may never do at all. A pre-reviewed profile is the difference between "an agent doing
  something undeclared to get around a guard" (refused — correctly) and "an agent doing exactly
  what a reviewer already signed off for this role".
- ORCH-A1's DONE-criterion is: *spec OK by routing-00, then every orchestrator/worker launched
  from a per-role settings file (tests)*. The second half is the later lane; this document is the
  first.

## Decisions

Local to this spec; operator decisions keep the global `D-nn` form.

| # | Decision |
|---|---|
| A1-D1 | A launch profile is a per-role **pre-reviewed grant bundle**: a tracked `.example` template plus a git-ignored runtime file, the same shape as `configuration/mcp/<role>.json` in the restart spec §8. |
| A1-D2 | Path `configuration/launch-profiles/<role>.settings.json`. The `.settings.json` suffix makes the existing `**/*settings*.json` rule in `policy.risk_rules` (operator Q-013) cover a profile automatically, with no new risk rule. |
| A1-D3 | The profile's **deny set is rendered from `catalog/agent-harness.json` `fences`** — one home. The profile restates no secret pattern and no `.claude.json` pattern of its own. |
| A1-D4 | The **pre-granted allow set is one entry**: the gateway `apply.sh` run grant, in the `l1-routing` profile only. Round 13 (routing-00 D-159) removed every push and workflow-dispatch grant — the lane-prefix push (`git push origin P*` with its `-u`/`-q`/`FETCH_HEAD:refs/heads/P*`/`:P*` spellings for each prefix `L1-`, `L2-`, `WS-`, `worktree-`), the lane dispatch (`gh workflow run --ref P*`, both spellings) and the `l2-orchestrator` own-prefix set (round 3, D-138) — together with every deny entry that existed only to shape them (the L1 no-ref / matching / `@` / refspec denies, the L2 single-ref shape fences, the workflow-run shape denies, the round 6-8 character classes, and `LANE_PREFIXES` itself). A push or dispatch that names no `main` ref is now **unlisted**, so the classifier reads its real argv (§3.2). Profiles still follow the session's tier, not its host: an L1 coordinator session (including a workstation L1) launches with `l1-coordinator`; only L2-tier sessions use `l2-orchestrator`. |
| A1-D5 | `git push` to `main`, in every spelling that names it, and every secret read/write are **always-deny, no override**; a later allow entry can never win (tested, §3.3). |
| A1-D6 | No profile sets `--dangerously-skip-permissions`, disables the classifier, or otherwise weakens Claude Code's own permission system (§6). |
| A1-D7 | `autoos-agent run` and the MCP `spawn` load the profile through Claude Code's own `--settings` (claude/qoder only), keyed by the launch role; an unknown launching role is an error naming the role, never a silent no-profile run. |
| A1-D8 | REVIVE (ORCH-C1) re-issues the same profile for a restored session and adds no new grant semantics; this spec describes the interface only. |
| A1-D9 | Profile authoring and every change are **HIGH** by construction (a settings JSON), paying 2 cross-family reviews + the Sonnet final (`policy.review_counts.high`). |
| A1-D10 | Build-out — templates, generator, enforcement, tests — is a **LATER lane**; this lane writes this spec and nothing else. |

## 1. What a launch profile is

- A profile answers one question per role: *what may this session do with no human in the loop,
  and what may it never do whatever it says?*
- It bundles three things, and only these:
  1. the **pre-granted allow** list — named operations promoted from "prompt" to "allowed",
     each one reviewed before it was written down;
  2. the **always-deny** list — named operations that stay denied no matter what else the profile
     or session says;
  3. the **MCP document** the role loads (the restart spec §8 `configuration/mcp/<role>.json`),
     by reference, so the profile and the MCP set are one decision, not two.
- It is **not** a new permission system. It is data that feeds Claude Code's existing one
  (§4, §6).
- Profiles are **tracked as `.example` templates with placeholders only**, and the runtime file is
  generated from the template and git-ignored, exactly like `configuration/run.json` and
  `configuration/mcp/<role>.json` (restart spec §8). A tracked profile never carries a real path,
  branch or account (AGENTS.md rule 1).

## 2. Fleet roles

The fleet has four axes today and they do not share one vocabulary; A1 has to name one.

| Axis | Values | Source |
|---|---|---|
| depth level | `L0` router, `L1` coordinator, `L2` orchestrator, `L3` worker / reviewer | `SKILL.md` "Levels (L0-L3)" |
| harness role | `orchestrator`, `suborchestrator`, `leaf-implementer`, `leaf-reviewer` | `catalog/agent-harness.json` `roles` |
| card role / kind | `orchestrate`, `implement`, `review`, `research` (v2 `kind`) | `tools/autoos-agent.py` card parsing |
| MCP document stem | `l2-orchestrator`, `l3-worker`, `l3-reviewer` | restart spec §8, `configuration/mcp/` |

- A1 keeps the **launch identity** as the profile key. That is the level + the one session name the
  launcher starts, not the card role of the task it will run:
  `l0-router`, `l1-coordinator`, `l1-routing`, `l2-orchestrator`, `l3-worker`, `l3-reviewer`.
  - `l1-routing` is separate from `l1-coordinator` **only because of A1-D4**: it is the one role
    whose profile carries the gateway-apply pre-grant (the MISTRALFIX precedent — "gateway apply by
    L1-routing"). Every other grant is identical, and that is the whole difference.
  - `l1-backlog` and `l1-main` are the same role as `l1-routing` minus the apply grant; the profile
    set does not need a file per session name, only per grant set.
- Each launch identity maps to a harness role and an MCP document (§3.1). A worker/reviewer
  identity that maps to a `leaf` harness role inherits the leaf fences unchanged — a profile may
  make a leaf's grant *smaller*, never larger.
- Ambiguity to resolve before build: the restart spec §8's run.json `role` field has only the
  three MCP stems, while a profile needs the six launch identities above. Either the field's domain
  grows or the profile is derived from (level, session name). Open question **Q2**; the default is
  proposed there.

## 3. Profile shape and paths

### 3.1 Shape

- Path: `configuration/launch-profiles/<role>.settings.json` (A1-D2), for `<role>` in §2's set.
- The tracked template is `<role>.settings.example.json`; the runtime `<role>.settings.json` is
  git-ignored and generated from it, as with `configuration/mcp/`.
- Shape (the real rendered shape - excerpt of one real profile; every exact
  matcher spelling is pinned by `tests/fixtures/push-corpus.json`, decided
  against the rendered templates):

```jsonc
{
  "//": "site-free example; the runtime file is generated and git-ignored",
  "role": "l1-routing",
  "harnessRole": "orchestrator",
  "mcpConfig": "configuration/mcp/l2-orchestrator.json",
  "permissions": {
    "allow": [
      "Bash(bash configuration/omniroute/apply.sh:*)"  // the only grant left (round 13)
    ],
    "deny": [
      "Bash(*git*push* main)",
      "Bash(*git*push*+refs/heads/main)",
      "Bash(*git*push*--force*)",
      "Bash(*gh workflow run*--ref main)"
      // + every pattern rendered from catalog/agent-harness.json fences (A1-D3)
    ]
  }
}
```

  Claude Code CLI 2.1.283 reads permission rules ONLY under
  `permissions.allow` / `permissions.deny` - top-level `allow`/`deny`
  keys are ignored silently, and `permissionMode` / `permissionPrompts`
  are NOT settings keys, so the render emits neither.

- `allow` is the **pre-granted** set: each entry is a real, named, reviewed operation. Nothing
  is granted "for convenience" — an entry that is not needed by the role is a defect.
- `deny` is the **always-deny** set: rendered from `catalog/agent-harness.json` `fences`
  (`read_deny_all` + the secret/credential entries of `bash_deny_all`) **plus** the profile-specific
  entries below. One home (A1-D3): the profile generator reads the harness file; the profile does
  not carry a second copy of `*api-keys*`, `*client.key*`, `*.claude.json*` and so on.
- `permissionPrompts: "none"` is deliberate: under `--print`, "nobody: anything that would prompt
  is denied automatically" (`claude --help`). An unlisted action therefore **fails closed** instead
  of blocking on a dialog no one answers.

### 3.2 PRE-GRANTED allow (narrow, role-scoped)

**Round 13 (routing-00 D-159) emptied this table of push and dispatch.** One entry survives:

| Grant | Roles | Why pre-granted | Guard |
|---|---|---|---|
| `Bash(bash configuration/omniroute/apply.sh:*)` | **`l1-routing` only** | the gateway apply is a reviewed, idempotent, site-free script (APPLYIDEM `36be9c9`) | the model may *run* apply.sh but may not *read* its key (§3.3); apply.sh reads `manage.key` in its own process |

- The gateway-apply entry is the sharpest illustration of "narrower pre-reviewed grant": the
  session is allowed to **run** the script but stays **denied** reading
  `configuration/api-keys.yml`, `~/.config/autoos/ai-stack/client.key` and `manage.key`. The
  secret never enters the model's context; the reviewed tool reads it in its own process.
- **Removed in round 13 (D-159):** the lane-prefix push grant (`git push origin P*` with its `-u`
  / `-q` / `FETCH_HEAD:refs/heads/P*` / `:P*` spellings for each lane prefix `L1-`, `L2-`, `WS-`,
  `worktree-`), the lane dispatch grant (`gh workflow run * --ref P*`, both spellings), the
  `l2-orchestrator` own-prefix grant (round 3, D-138) — and with them every deny entry that
  existed only to *shape* a grant: the L1 no-ref / matching (`:` / `+:`) / `@` and refspec denies,
  the L2 single-ref shape fences (rounds 4-5), the workflow-run shape denies (rounds 8-9) and the
  round 6-8 character classes (tab/CR/LF, quotes, `$`, backtick, backslash, braces, `&`, `>`,
  `<`, glob characters, `;`, `#`). `LANE_PREFIXES` is gone from the generator with them.
- **Why removal is the safer direction.** A grant is a glob over command *text*. Each of those
  classes was a hole in such a glob: the grant read "any ref after `origin ` is a lane", so every
  spelling the text fence could not see had to be denied by name — an enumeration that grows one
  review round at a time and cannot finish. With no grant, a lane push is simply **unlisted**: it
  reaches the permission prompt, which under the non-interactive launch flags is the classifier
  reading the real argv. The failure mode changes from "an unreviewed ref slipped through a glob"
  to "the classifier was asked", which is the guard this spec was built to feed, not to replace.
- The asymmetry now bounds the deny set: an unlisted command can explain itself (the classifier
  says why), a deny cannot. So a fence that over-reaches silently removes a capability —
  `docker push registry/app:main` is an image tag, not a branch, and `L1-x:refs/heads/main-fix` is
  a lane, not main. What survives names `main` and the push-wide flags and nothing else (§3.3),
  and every surviving rule is witnessed by a real command in
  `tests/fixtures/push-corpus.json` (the KEYDENY property, `2704196`).
- A coordinator that needs its lane push *unprompted* gets it from the hooks lane — HOOKS H2's
  PreToolUse `git-push-to-main` guard, which reads the argv rather than its spelling — not from a
  wider glob here (Q6).
- Every grant is scoped by role. The set is deliberately tiny; adding an entry is a HIGH-risk
  change (§5) and needs a test that the new entry is reachable and the deny set still wins.
- Profiles follow the session's tier, not its host: an L1 coordinator session (including a
  workstation L1) launches with `l1-coordinator`; only L2-tier sessions use `l2-orchestrator`.

### 3.3 PRE-DENIED always-deny (no override)

- **Push to `main`, in every spelling that names it.** Every profile denies it and (since round
  13) no profile grants it. The deny is a *fence set*, not one string, anchored on `*git*push` —
  a glob **between** the two tokens, so neither a `sudo`/`env`/`xargs` prefix nor a `git -C` /
  `git --git-dir=` / `git -c` wrapper hides a `main` ref from it. The `git` token stays in the
  anchor so the fence cannot swallow an unrelated push: `docker push registry/app:main` is an
  image tag, not a branch.
  - `main` as a **whole ref name**, never a substring (round 11): git's ref-name rules give
    exactly three spellings that resolve to `refs/heads/main` (`main`, `heads/main`,
    `refs/heads/main`), each fenced in every position a push argv can put a ref in — after a
    space (bare or remote-prefixed), after a colon (refspec destination, or a delete) and after a
    `+` (the force prefix, either side of the colon) — and each closed at end-of-text, at a space
    (more arguments follow) or at a colon (the ref is a refspec source). So `L1-x:heads/main-fix`,
    `L1-main-fix` and `L1-routing/maintenance` stay outside the fence while `git push origin
    L1-x:refs/heads/main` is inside it. A refspec carries exactly one colon, so the
    `:main:*`-shaped entries round 12 rendered match no command git accepts and round 13 deleted
    them.
  - the push-wide flags that reach `main` whatever the ref: `--force` / `-f`, `--all`, `--mirror`
    (in both positions), any `HEAD` push, `:HEAD` (round 11: `<src>:HEAD` creates a remote branch
    literally named `HEAD` — `refs/heads/HEAD`; `main` is unchanged, verified with real git, but
    every tool that resolves HEAD breaks), and the tag forms (`--tags`, `--follow-tags`,
    `refs/tags`, `tag <name>`, round 12).
  - Real git proves the premises: `RealGitPremiseTests`
    (`tests/helpers/launch_profile_model.py`, run by `tests/test_push_corpus.py`) advances
    `refs/heads/main` in a scratch bare repo for `heads/main` in every position and for the glob
    refspec, and shows the stray `refs/heads/HEAD` branch. The KEYDENY lesson (`2704196`) — *a
    fence is a decision on a real command, not a list of names* — is enforced mechanically: every
    push and dispatch deny rule the profiles render must match a `deny` row of
    `tests/fixtures/push-corpus.json`.
- **Dispatch naming `main`.** `--ref main` and `--ref=main` (and their `heads/main`,
  `refs/heads/main` spellings) deny on every role, with or without a following argument. Round 13
  removed the rest of the dispatch fences with the dispatch grant: a bare run (default branch =
  `main`), `-R`/`--repo` (another repo), a second `--ref`, `-r` (last-flag-wins), an
  empty/valueless `--ref`, and the quoting, metacharacter and whitespace classes. Each is an
  unlisted command the classifier judges from the argv.
- **What round 13 stopped fencing.** The removed classes were holes in a *grant*; with no grant
  they are simply commands the classifier reads. That closes nothing by itself, so each hazard is
  an **OPEN residual with a named owner** — the hooks lane, because a PreToolUse guard sees argv
  where this file sees a string:

  | Id | Residual | The command that a text fence cannot see | Owner |
  |---|---|---|---|
  | MED-3 | git config | `git config remote.origin.push refs/heads/*:refs/heads/*`, then any refspec-less push | HOOKS H2 (`git-push-to-main`) |
  | MED-4 | glob refs | `git push origin 'refs/heads/*:refs/heads/*'` — proven by `RealGitPremiseTests` to advance `main`; `*` and `?` are rule-syntax characters, so no fence can name them | HOOKS H2 |
  | MED-5 | rewritten and substituted refs | `"main"`, `$REF`, `$(…)`, backticks, tab/CR/LF separators, `;` and `#` tails, the matching and valueless `--ref` forms | HOOKS H2 |

  Until H2 lands the guard on those is the classifier plus the surviving `main` fence — and
  because nothing is pre-granted, none of them reaches a remote without a decision. Each is pinned
  as a `prompt` row in the corpus, so a later change that re-fences (or re-grants) one shows up
  there.
- **Any secret access.** Reading, writing, copying or echoing `configuration/api-keys.yml`,
  `*.env` / `*.env.*`, `*client.key*`, `*manage.key*`, `*auth.json*`, `**/*.vault.yml`, `**/*.pem`
  and the token/config files named in `catalog/agent-harness.json` `fences`. The pattern list is
  the harness file's; the profile renders it (A1-D3). The `*.example` templates stay readable
  (`read_allow_all`), because a placeholder is not a secret.
- **Any write to `~/.claude.json`.** `Edit`, `Write` and the shell forms (`tee`, `>>`, `sed -i`)
  against `*.claude.json*`, plus the same pattern already in `fences.read_deny_all` /
  `fences.bash_deny_all`. A profile may not edit the file that holds another session's trust and
  permission state.
+ **No override.** Claude Code evaluates its deny rules before allow rules, and the profile
  additionally renders the always-deny set through the session's disallow list so it does not
  depend on a later allow entry losing a precedence race. This is asserted, not assumed: the
  phase-1 test adds a contradictory `allow` for each always-deny entry and asserts the decision is
  still `deny` (A1-D5) — for a push or dispatch fence, the target of that contradictory allow is
  the fence's own corpus witness, so the same test re-checks the KEYDENY property. The exact CLI
  precedence is verified against `claude --help` in phase 0, the way restart spec §8 verified its
  argv.
- **Text fencing is blind; the profiles are not the last line.** A command-text fence cannot see
  git config, a shell alias, a function, or which branch is checked out, so a bare `git push` on
  `main` carries no `main` token to match. Round 13 makes the consequence explicit rather than
  papering over it: that push is now *unlisted*, so it is the classifier's decision, and the
  server-side check is the one thing that does not depend on either. AutoOS `main` has no branch
  protection and no rulesets (measured: GitHub API reports the branch unprotected and the rulesets
  list empty), and this lane does not change that — enabling it is an operator action, deferred.
  (The round-13 brief cites the deferral as **D-150**; the only D-150 in this checkout's history
  is the fleet-console phase approval at `67f875d`, so confirm the id before quoting it elsewhere.
  The open question itself stands on the measurement, not the id.) How `main` advances without a
  profile grant stays **Q6**.

## 4. Consumption

### 4.1 `autoos-agent run` and the MCP `spawn`

- Today `tools/autoos-agent.py` builds a client command with `tools/autoos_clients.py` and, for a
  client in `MCP_STRICT_CLIENTS = ("claude", "qoder")`, inserts `--strict-mcp-config`
  (`tools/autoos-agent.py` ~L1927). The MCP `spawn` (`tools/autoos_agent_mcp.py`) calls the same
  `build_argv` / `run` path.
- The profile adds, for claude/qoder only:
  - `--settings configuration/launch-profiles/<role>.settings.json` (`claude --help`: "Path to a
    settings JSON file"), which carries `allow` / `deny` / `permissionPrompts`;
  - the MCP document from the profile's `mcpConfig` via the restart spec §8 pair
    (`--mcp-config <file> --strict-mcp-config`), so one decision selects both.
- **Role selection.** The request must name its launch role (`role=` on the MCP card / `--role` on
  `run`), or the launcher derives it from the level + session name (§2). A **launching** role with
  no profile file is an **error naming the role** — never a silent run with no profile, the same
  failure-mode-pinning rule restart spec §8 states for a missing MCP document.
- Non-claude clients (opencode, qwen, gemini, codex) do not take `--settings`; their fences are the
  existing `catalog/agent-harness.json` overlay (`OPENCODE_CONFIG_CONTENT`). A1 does not change
  them. A follow-up may render the same allow/deny model into those overlays; that is **out of
  scope** here (§9).

### 4.2 Future REVIVE launcher (ORCH-C1) — interface only

- REVIVE restores background orchestrators and the router on user-manager start and on crash
  (ORCH-C1, `docs/tasks.md`; D-117/D-119/D-121). It is **not implemented here**.
- The interface A1 fixes so REVIVE does not invent a second grant model:
  - REVIVE reads the restored session's **launch role** from the same place `spawn`/`run` wrote it
    (the restart spec §4 `run.json` `role` field, or its successor — **Q2**).
  - REVIVE re-issues the **same** profile file and the same MCP document. A restored session is
    identical to a freshly launched one; REVIVE adds **no** grant, **no** "restore mode", and
    **no** widened allow set.
  - If the profile file is missing at restore time, REVIVE follows §4.1: it errors naming the role
    and does not start a session without a profile.
- This is the point of the whole exercise: the watchdog/relaunch loop is exactly the shape the
  2026-09-28 classifier refused, and it becomes legible and reviewable because each relaunch names
  a profile a human already approved.

## 5. Authoring, review and change

- **A profile is HIGH by construction.** `configuration/launch-profiles/<role>.settings.json`
  matches `policy.risk_rules` `**/*settings*.json` (reason: security settings; operator Q-013), so
  the classifier in `tools/autoos_risk.py` — which reads the class **off the diff**, not off the
  author's claim — marks it HIGH.
- **The cost of HIGH** is fixed in `catalog/ai-registry.json` `policy.review_counts.high`:
  **2 cross-family reviewers plus the Sonnet final** (operator Q-013 / D-060, RISKTIER-a2). Normal
  work pays the same two cross-family reviews and the final only on the `policy.risk_audit_percent`
  (20 %) audit draw.
- **Every change is HIGH too**, not only the first authoring: adding an allow entry, removing a
  deny entry, changing `permissionPrompts`, or repointing `mcpConfig` all touch the same settings
  JSON. Changing the deny *source* (`catalog/agent-harness.json` `fences`) is HIGH as well, and so
  is a skill edit that cites a profile (`.agents/skills/**`, operator Q-013).
- **This spec is HIGH** under the same policy: `docs/**/*spec*.md` and `docs/plans/**` are both
  HIGH rules. The spec is not exempt from the review it prescribes.
- **A reviewer's checklist for a profile diff:**
  1. each `allow` entry is needed by *this* role and no other profile could borrow it;
  2. no `allow` entry is reachable only by defeating an always-deny entry;
  3. the deny set still equals the render of `catalog/agent-harness.json` `fences` (drift check);
  4. the branch-scope and secret-scope test tables still pass with the new spelling;
  5. the file is site-free (placeholders only) and the runtime file stays git-ignored.
- **CI drift check.** The generator renders the profile from the template + the harness fences and
  `--check` fails on any drift, in the style of the registry renders
  (`tools/registry.py render … --check`, "registry: no generated file drifts"). A hand-edited
  runtime file, or a deny pattern that no longer matches the harness, fails the suite instead of
  shipping.

## 6. Not a bypass of Claude Code's permission system or classifier

This is the load-bearing section. A1 exists to make launches **more** legible to the guards, never
to get past them.

- **Claude Code's own permission system and classifier stay fully in force.** A profile supplies
  *inputs* to that system — the documented `--settings` fields, `--allowedTools`,
  `--disallowedTools`, `--permission-mode` and `--permission-prompts` (`claude --help`, verified in
  locally 2026-09-28). It does not replace, patch or disable any of them.
- **`--dangerously-skip-permissions` is never set, by any profile** (A1-D6). That is the one flag
  that would make the whole exercise a bypass, and it is out of bounds.
- **The classifier is not argued with.** If the classifier refuses a launch anyway, the correct
  behaviour is to surface the refusal to the parent as one line and stop — never to retry with a
  broader grant until it passes. A refusal is a signal to re-scope the task or ask the operator, not
  a challenge (Q7).
- **Defense in depth, four layers, each independent:**
  1. Claude Code's permission system + classifier (unchanged, outermost);
  2. the profile's always-deny set, evaluated first, with no override (A1-D5);
  3. the profile's narrow, pre-reviewed allow set (A1-D4);
  4. repo-level fences in `catalog/agent-harness.json`, asserted by both suites.
  Round 13 changed what the layers *do*, not how they stack: layer 3 is one entry, so every push
  that layer 2 does not fence is decided by layer 1 - the classifier, reading the real argv. The
  guard that replaces the deleted text classes is a hook that reads argv too (HOOKS H2's
  `git-push-to-main` guard, §3.3), which is why each removed hazard names an owner instead of
  hoping a glob covers it.
- **A narrower grant, not a wider one.** The pre-review happens *once*, before the session runs,
  on a named operation a reviewer can read. An agent that would otherwise prompt its way to an
  action — or quietly work around a guard — instead runs inside a set someone already approved.
  "How to get past a refusal" is the opposite of A1's goal and none of its mechanics are in scope.

## 7. Open questions

Each with a suggested default; anything without a default is flagged for review rather than
guessed.

- **Q1 — Profile path and suffix?** Default: `configuration/launch-profiles/<role>.settings.json`
  (A1-D2), `.example` tracked / runtime ignored. The suffix is chosen so the existing
  `**/*settings*.json` HIGH rule applies with no new rule.
- **Q2 — One role key for the three vocabularies?** *Flagged for review.* The restart spec §8
  run.json `role` field names three MCP stems, while a profile needs the six launch identities in
  §2. Default: extend the `role` field's domain to the launch identities and keep the MCP document
  as a field on the profile, so `spawn`, `run` and REVIVE read one key. The alternative — derive the
  profile from (level, session name) — is a second home for the same fact and is rejected unless
  routing-00 prefers it.
- **Q3 — Runtime profile owner.** Default: generated from the tracked `.example` at install / first
  launch, git-ignored, with the same ignore-rule treatment restart spec §8 gives
  `configuration/mcp/<role>.json`.
- **Q4 — `gh workflow run` scope.** **Answered by round 13 (D-159): no grant at all.** Every
  dispatch is an unlisted command the classifier judges, and the only fence left is the one naming
  `main` (`--ref main`, `--ref=main`, and the `heads/main` / `refs/heads/main` spellings, both
  separators, with or without an argument after them). The bare run (default branch = main),
  `-R` / `--repo` (another repo), a second `--ref`, `-r` (last-flag-wins), an empty `--ref` and the
  quoting / whitespace forms are MED-5 residuals owned by HOOKS H2 (§3.3), not classes this file
  fences. The phase-1 measured check still stands: a dispatch cannot cross the fence onto `main`
  without naming it — crossing it some *other* way is exactly why the residual is named here and
  closed in the hooks lane.
- **Q5 — Gateway apply scope.** Default: `l1-routing` only (A1-D4), the MISTRALFIX precedent.
  Confirm routing-00 agrees; if not, the entry moves to the coordinator base grant.
- **Q6 — How does `main` advance?** *Flagged for review; still open.* Default: never through a
  launch profile — `main` is updated by the operator (or an explicit, separate privileged path),
  never by a profile grant. Round 13 removed the last grant that could be read as an indirect
  route, so what is left of the question is server-side: `main` has no branch protection and no
  rulesets (measured, §3.3), and enabling them is a deferred operator action that is not this
  lane's. Until then the surviving fence plus the classifier is all that stands between a session
  and `main`.
- **Q7 — A classifier refusal at launch.** Default: surface one line naming the role and stop;
  never retry with a wider grant.
- **Q8 — `permissionPrompts`.** Default: `none` (fail closed). The alternative, `host`, could hang
  an unattended run on a dialog, which is the failure this repo keeps designing out.
- **Q9 — Do workers/reviewers get pre-grants at all?** Default: **no** pre-grants beyond read-only
  work; leaves inherit the leaf fences unchanged. A leaf profile with an `allow` entry would need
  the strongest justification in the set. Round 13 made that answer the same for every role except
  `l1-routing`: the only `allow` entry in any template is the reviewed gateway apply.
- **Q10 — CI drift check.** Default: yes — the generator's `--check` runs in the Linux suite
  (`tests/linux/33`), so a profile that no longer equals its render fails the build.

## 8. Phased build-out (a LATER lane)

- **Phase 0 — this spec.** Design only: operator/routing-00 OK of A1-D1…A1-D10 and the open
  questions. **No code, no settings files, no enforcement, no tests** in this lane (A1-D10).
- **Phase 1 — author and enforce (LATER).** Tracked `.example` profiles, the generator that renders
  them from `catalog/agent-harness.json` `fences`, the branch-scope and secret-scope test tables,
  the contradiction test (a later `allow` cannot beat an always-deny), and the CI drift check.
  HIGH review: 2 cross-family + Sonnet final.
- **Phase 2 — consume (LATER).** `autoos-agent run` / MCP `spawn` pass `--settings` +
  `--mcp-config … --strict-mcp-config` for claude/qoder from the launch role; a missing profile for
  a launching role errors naming the role.
- **Phase 3 — fleet cutover (LATER; the DONE-criterion).** Every orchestrator and worker is launched
  from a per-role settings file, proven by tests; REVIVE (ORCH-C1) re-issues the same profile.
- Each phase is a separate lane with its own writer; nothing in phases 1–3 is in scope for the
  commit that lands this spec.

## Files and interfaces

- **Profile (new, later):** `configuration/launch-profiles/<role>.settings.example.json` (tracked,
  placeholders only) and `<role>.settings.json` (git-ignored runtime).
- **Deny source (existing, one home):** `catalog/agent-harness.json` `fences` —
  `read_deny_all`, `read_allow_all`, `bash_deny_all`, `bash_deny_leaf`, `bash_allow_all`.
- **MCP document (restart spec §8):** `configuration/mcp/<role>.json`, referenced by the profile.
- **Consumers (later):** `tools/autoos-agent.py` (`run`, ~L1925 argv build), `tools/autoos_agent_mcp.py`
  (`spawn` / `build_argv`), `tools/autoos_clients.py` (`MCP_STRICT_CLIENTS = ("claude", "qoder")`).
- **Risk and review (existing):** `tools/autoos_risk.py`, `catalog/ai-registry.json`
  `policy.risk_rules` (`**/*settings*.json`) and `policy.review_counts.high`.
- **Claude Code surface used (verified, `claude --help`, CLI 2.1.283, 2026-09-28):** `--settings`,
  `--allowedTools` / `--allowed-tools`, `--disallowedTools` / `--disallowed-tools`,
  `--permission-mode`, `--permission-prompts`, `--mcp-config`, `--strict-mcp-config`.
- **Refusal evidence:** `.agents/skills/unattended-orchestration/references/l3-routing.md:148-149`.

## Out of scope

- The code, settings files, enforcement and tests (phases 1–3; A1-D10).
- The REVIVE / watchdog build itself (ORCH-C1); A1 fixes only the interface it consumes.
- `policy/*.yml` as a versioned policy home (ORCH-A2) and the `enforced`/`advisory` rule tags
  (ORCH-D1); A1 is one input to those, not their owner.
- Rendering the allow/deny model into non-claude clients' overlays (opencode, qwen, gemini, codex).
- Any change to `catalog/agent-harness.json` `fences` themselves; A1 reads them.
- Any hook, wrapper or flag that would weaken Claude Code's permission system or classifier.
