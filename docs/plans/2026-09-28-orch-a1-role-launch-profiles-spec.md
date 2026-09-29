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
| A1-D4 | The **pre-granted allow set is narrow and role-scoped**: lane-prefix push and dispatch per `LANE_PREFIXES` (L1-, L2-, WS-, worktree-) — same-name lane push (`Bash(git push origin P*)` + `-u`/`-q` spellings), `FETCH_HEAD:refs/heads/<lane>` push, lane delete (`Bash(git push origin :P*)`), and `gh workflow run` with explicit `--ref` on `<lane>` (both `--ref` spellings) for coordinator roles - a push/dispatch must name its ref so the fences can see it; everything else falls to the prompt; registry lane prefixes: not yet recorded - add them to LANE_PREFIXES when they are; gateway `apply.sh` only in the `l1-routing` profile; `l2-orchestrator` pushes exactly one `L2-*` ref, same-name, no options except `-u`, and runs workflows only with `--ref` on them — per role, not per session: any `l2-orchestrator` session may push/dispatch on any `L2-*` lane branch (per-session scoping is not expressible in a per-role profile) — under the same always-deny fences plus the L2-only single-ref shape fences (round 3, D-138; tightened round 4; shaped round 5) plus the L2-only dispatch trailing shape (round 8). Any `git push` containing a tab, CR or LF is denied on every role (bash splits on them; the fences match spaces only) (round 6); any `git push` containing `;` or `#` is denied on every role (round 8); any `gh workflow run` containing a tab, CR or LF, a second `--ref`, or `-r`/`-R`/`--repo` is denied on every role (round 8). Profiles follow the session's tier, not its host: an L1 coordinator session (including a workstation L1) launches with l1-coordinator, whose grant already covers its own lane prefix (e.g. WS-*); only L2-tier sessions use l2-orchestrator (L2-* only). |
| A1-D5 | `git push` to `main` and every secret read/write are **always-deny, no override**; a later allow entry can never win (tested, §3.2). |
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
- Shape (the real rendered shape - excerpt of one real profile;
  exact matcher spellings are pinned in phase 1):

```jsonc
{
  "//": "site-free example; the runtime file is generated and git-ignored",
  "role": "l1-routing",
  "harnessRole": "orchestrator",
  "mcpConfig": "configuration/mcp/l2-orchestrator.json",
  "permissions": {
    "allow": [
      "Bash(git push origin *)",
      "Bash(git push -u origin *)",
      "Bash(git push -q origin *)",
      "Bash(gh workflow run * --ref *)",
      "Bash(gh workflow run * --ref=*)",
      "Bash(bash configuration/omniroute/apply.sh:*)"
    ],
    "deny": [
      "Bash(*git push * main)",
      "Bash(*git push *:main*)"
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

| Grant | Roles | Why pre-granted | Guard |
|---|---|---|---|
| Lane-prefix push: `Bash(git push origin P*)`, `Bash(git push -u origin P*)`, `Bash(git push -q origin P*)` for each `P` in `LANE_PREFIXES` (L1-, L2-, WS-, worktree-). Plus `Bash(git push origin FETCH_HEAD:refs/heads/P*)` and `Bash(git push -q origin FETCH_HEAD:refs/heads/P*)` for coordinator fetch-and-push. Plus `Bash(git push origin :P*)` for lane delete. | coordinators (`l1-*`) | the coordinator pushes lane branches and must not stop on a push prompt | the L1 grant requires an explicit origin ref - a push must name its ref so the fences can see it; forms matching no allow fall to the permission prompt (fail safe), never pre-granted; the always-deny below removes `main` and the no-ref / matching (`:`/`+:`) / `@` (HEAD) forms; a test enumerates the ways to spell "main"; any `git push` containing a tab, CR or LF, a `;`, or a `#` is denied on every role (bash splits on them or drops the comment; the fences match spaces and literal refs only). Registry lane prefixes: not yet recorded - add them to LANE_PREFIXES when they are. |
| Lane-prefix workflow dispatch: `Bash(gh workflow run * --ref P*)` and `Bash(gh workflow run * --ref=P*)` for each `P` in `LANE_PREFIXES` (L1-, L2-, WS-, worktree-). | coordinators | CI is triggered by workflow dispatch from a branch (the repo's own pre-merge gate, AGENTS.md §7) | the L1 grant requires an explicit `--ref` - a bare run (default branch = main) falls to prompt, `-R`/`--repo` (another repo), a second `--ref` or `-r` (last-flag-wins), and any tab/CR/LF deny outright; branch-scoped by the command; `main` dispatch stays deny |
| `Bash(git push origin L2-*)` (+ the `-u` spelling) and `Bash(gh workflow run * --ref L2-*)` (+ the `--ref=` spelling) | **`l2-orchestrator` only** (round 3, D-138; tightened round 4; shaped round 5) | the orchestrator pushes any `L2-*` lane branch and dispatches CI on it without a prompt — per-role scope: any `l2-orchestrator` session may use any `L2-*` branch, not only one it created | narrower than the coordinator grant: other prefixes, non-origin remotes and bare runs stay unlisted; the same always-deny fence set applies and deny wins (an `L2-x:main` refspec denies); the L2-only fences deny every refspec colon, `refs/` path and delete flag (round 4) and, by shape, anything after the branch token plus any option before the ref but `-u` (round 5), so only exactly one `L2-*` ref, same-name, no options except `-u` is allowed |
| `Bash(bash configuration/omniroute/apply.sh:*)` | **`l1-routing` only** | the gateway apply is a reviewed, idempotent, site-free script (APPLYIDEM `36be9c9`) | the model may *run* apply.sh but may not *read* its key (below); apply.sh reads `manage.key` itself |

- The gateway-apply entry is the sharpest illustration of "narrower pre-reviewed grant": the
  session is allowed to **run** the script but stays **denied** reading
  `configuration/api-keys.yml`, `~/.config/autoos/ai-stack/client.key` and `manage.key`. The
  secret never enters the model's context; the reviewed tool reads it in its own process.
- The `L2-` prefix (round 3, D-138) is read off the repo's own branches (`L2-<name>/...`):
  no stated L2 branch-naming convention exists in the orchestration skill or docs text. If
  that convention ever changes, the grant prefix moves with it in the same change.
- Every grant is scoped by role. The set is deliberately tiny; adding an entry is a HIGH-risk
  change (§5) and needs a test that the new entry is reachable and the deny set still wins.
- Profiles follow the session's tier, not its host: an L1 coordinator session (including a workstation L1) launches with l1-coordinator, whose grant already covers its own lane prefix (e.g. WS-*); only L2-tier sessions use l2-orchestrator (L2-* only).

### 3.3 PRE-DENIED always-deny (no override)

- **Push to `main`.** No profile grants it and every profile denies it. The deny is a *fence set*,
  not one string: `git push origin main`, `git push origin main:<ref>`, `git push origin HEAD:main`,
  `git push <remote> +main`, `git push --force*`, and the `-u`/`--tags` spellings; plus the closed
  no-ref / matching / `@` spellings (round 8 - a bare remote with no refspec, `:` / `+:` matching
  pushes, `@` = HEAD), the trailing-`;` spellings, and the `#` comment spellings. Round 11: `main` is matched as a whole ref name, not a substring - git's ref-name rules give exactly three spellings that resolve to `refs/heads/main` (`main`, `heads/main`, `refs/heads/main`), and all three are fenced in every position (bare, as source, as destination; `tests/test_launch_profiles.py` RealGitPremiseTests proves the `heads/main` forms really advance main), so lane names that merely contain `main` (`L1-x:heads/main-fix`, `L1-main-fix`, `L1-routing/maintenance`) stay allowed; a `:HEAD` destination is denied too (`<src>:HEAD` updates the remote's HEAD target (main on AutoOS), so it is denied). Round 12: tag push forms are denied on every role (`git push origin refs/tags/*`, `--tags`, `--follow-tags`, `tag *`). Round 12: glob characters `*`, `?`, `[` in a push command after `git push` are denied on every role (the rule syntax cannot express a literal `*`, so `refs/heads/[*?]*`, `refs/[*?]*`, `*[?*]:refs/heads/*` are denied explicitly). The L1 grant now
  requires an explicit origin ref, so none of these is pre-granted. A test table
  drives each spelling through the matcher and asserts `deny` — the KEYDENY lesson (`2704196`):
  *a fence is a decision on a real command, not a list of names*, and a substring allow once
  licensed the real key file the moment its name shared a line with the template
  (`tests/linux/33-documentation.sh` "shell-substring-abuse"). AutoOS main has no branch protection and no rulesets (GitHub API: branch not protected; rulesets empty); these profiles are the only fence against a push to main. How `main` advances without a profile grant is **Q6** (OPEN - operator action: enable branch protection on main).
- **Dispatch to `main` or another repo.** No profile grants it and every profile denies the fenced
  shapes: an explicit `--ref main` (both spellings), any `-R` / `--repo` (another repo), any second
  `--ref` or any `-r` (gh's short `--ref`, last-flag-wins), and any tab/CR/LF separator (round 8).
  A bare `gh workflow run` with no `--ref` dispatches on the default branch (main) and is never
  pre-granted - the L1 grant requires an explicit `--ref`, so the bare form falls to the permission
  prompt. A test table drives each fenced spelling through the matcher and asserts `deny`.
- **Any secret access.** Reading, writing, copying or echoing `configuration/api-keys.yml`,
  `*.env` / `*.env.*`, `*client.key*`, `*manage.key*`, `*auth.json*`, `**/*.vault.yml`, `**/*.pem`
  and the token/config files named in `catalog/agent-harness.json` `fences`. The pattern list is
  the harness file's; the profile renders it (A1-D3). The `*.example` templates stay readable
  (`read_allow_all`), because a placeholder is not a secret.
- **Any write to `~/.claude.json`.** `Edit`, `Write` and the shell forms (`tee`, `>>`, `sed -i`)
  against `*.claude.json*`, plus the same pattern already in `fences.read_deny_all` /
  `fences.bash_deny_all`. A profile may not edit the file that holds another session's trust and
  permission state.
- **No override.** Claude Code evaluates its deny rules before allow rules, and the profile
  additionally renders the always-deny set through the session's disallow list so it does not
  depend on a later allow entry losing a precedence race. This is asserted, not assumed: the
  phase-1 test adds a contradictory `allow` for each always-deny entry and asserts the decision is
  still `deny` (A1-D5). The exact CLI precedence is verified against `claude --help` in phase 0,
  the way restart spec §8 verified its argv.
- **Text-only fencing is blind to git config and the checked-out branch.** A bare `git push` on `main`, or a push via an alias, carries no `main` token for a command-text fence to match, so server-side branch protection on `main` is not a backstop — these profiles are the only fence. The text fences see literal refs only: a quoted, variable or substituted ref (`"main"`, `$REF`, `$(...)`, backticks) is denied outright on every role (fenced here: quoting/expansion class), and so is a quoted, variable or substituted `gh workflow run --ref` value (fenced here: quoting/expansion class); the ref must be plain literal text, so any bash metacharacter that rewrites or ends a word next to it (backslash, braces, `&`, `>`, `<`) is denied outright on every role too (round 7, fail closed; fenced here: metacharacter class) — while glob characters `?`, `[`, `*` cannot be fenced in rule syntax (`*`/`?` are pattern characters there) and only expand when a matching file exists in the cwd; these profiles deny them explicitly (fenced here: glob character class). Two further residuals: a literal `*` typed in an l2 push (`git push origin L2-*`) is expanded by bash against cwd file names, so a file named `L2-x:main` would form a refspec (OPEN - operator action: enable branch protection on main); and `git config remote.origin.push` is unfenced (it only matters for pushes without an explicit refspec, which are now denied or not pre-granted) (OPEN - operator action: enable branch protection on main).

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
- **Q4 — `gh workflow run` scope.** Default: allow `gh workflow run * --ref *` (plus the
  `--ref=` spelling) - an explicit `--ref` is required so the fences can see the target; the
  workflow itself is
  CI on a lane branch, and dispatching against `main` stays covered by the `main` deny. A bare run
  (default branch = main) is never pre-granted, and `-R` / `--repo` (another repo), a second
  `--ref` or `-r` (last-flag-wins), and tab/CR/LF separators deny outright. The text
  fences see literal refs only, so a quoted, variable or substituted `--ref` value is denied
  outright. Needs one measured check in phase 1 that the dispatch cannot target `main`.
- **Q5 — Gateway apply scope.** Default: `l1-routing` only (A1-D4), the MISTRALFIX precedent.
  Confirm routing-00 agrees; if not, the entry moves to the coordinator base grant.
- **Q6 — How does `main` advance?** *Flagged for review.* Default: never through a launch
  profile — `main` is updated by the operator (or an explicit, separate privileged path), never by
  a profile grant. Confirm this matches the current merge/push practice before build.
- **Q7 — A classifier refusal at launch.** Default: surface one line naming the role and stop;
  never retry with a wider grant.
- **Q8 — `permissionPrompts`.** Default: `none` (fail closed). The alternative, `host`, could hang
  an unattended run on a dialog, which is the failure this repo keeps designing out.
- **Q9 — Do workers/reviewers get pre-grants at all?** Default: **no** pre-grants beyond read-only
  work; leaves inherit the leaf fences unchanged. A leaf profile with an `allow` entry would need
  the strongest justification in the set.
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
