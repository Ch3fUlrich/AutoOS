# Multihost rollout — which hosts, in what order, how each authenticates, how work is placed (spec, 2026-09-28)

Status: **spec only, no implementation.** This file composes with — and does not redesign — three
things that live elsewhere: fleet-node's per-session capability tokens (sibling branch
`L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md` §5), fleetd's API surface
(sibling branch `L2-general/fleetd-spec`, `docs/plans/2026-09-28-fleetd-eventbus-spec.md`), and the
`ollama-ws` OmniRoute leg (this checkout, `docs/tasks.md:35` — still queued, not live; §5 says so
plainly). The gap it fills is routing/DECISIONS.md D-108 item 6 (inference — no `routing/` dir
exists in this checkout, verified by listing): fleet-node + fleetd are single-host designs, and
nothing yet says which hosts get fleet-node, in what order, how each host authenticates to
OmniRoute as itself, or how work gets placed on the right host.

**The two tokens, stated once up front because conflating them is the easiest way to get this
spec wrong.** (1) fleet-node's **per-session capability token**: minted locally by a host's own
fleet-node instance on each launch, scoped to one launched work unit — sibling branch
`L2-general/fleetnode` §5 Security, not redesigned here. (2) OmniRoute's **`cli_access_tokens`**:
a **host-level** named token per fleet host — "one named token per fleet host → attribution +
one-click revocation" (sibling branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md` §12 — sibling reference, not a checkout
citation: that file does not exist in this branch, verified by glob for `docs/plans/*fleet*`
returning nothing). This spec designs only token (2): issuance, shape, revocation, and install-time
pickup. Token (1) is cited, never re-derived.

Placeholder discipline follows the repo's own site-free precedent: every host, address, domain,
and account here is a placeholder (`<tailnet>`, `<tailnet-cidr>`, `example.internal`), the way
`docs/plans/2026-09-27-omnigraph-mcp-catalog-spec.md:6-9` keeps that spec site-free with
`<omnigraph-url>`, `<server-vm>`, `<lan-cidr>`, `<tailnet-cidr>`, `<owner>`.

## 1. Summary

fleet-node (per-host registration, admission, watchdog) and fleetd (the central hub) are each
designed as if they run on one host. This spec is the rollout plan that turns those two
single-host designs into a fleet: the host classes and their trust tiers (§2), the per-host
OmniRoute `cli_access_tokens` entry that gives each host its own identity — a different,
host-level mechanism sitting above fleet-node's per-session capability tokens, which stay
untouched (§3) — placement by capability match plus per-host RAM headroom, reusing fleet-node's
admission-budget concept instead of inventing a second one, with secret-touching work restricted
to trusted hosts (§4), the `ollama-ws` GPU leg as the worked template for adding future specialty
legs (§5), the rollout order with its cross-host spawn+steer acceptance gate (§6), the security
invariants (§7), the additive migration (§8), open questions (§9), and the lane/cost table (§10).

## 2. Host classes

Roles and notes below come from the private plan §9 / D-021 (inference — neither exists in this
checkout); each row is grounded against this checkout wherever a host is already referenced, and
marked `(inference)` where it is not.

| Host class | Role / notes | Trusted for secret-touching work? | Checkout grounding |
|---|---|---|---|
| Coding VM | Claude orchestrators/workers; ~15GB RAM admission budget from measured per-session RAM — the model fleet-node's admission check uses (inference — sibling branch `L2-general/fleetnode` admission §, not readable from this branch) | **Yes** | This host measures 15988 MB total RAM (`free -m`, measured on this machine 2026-09-28 — consistent with the ~15GB budget; live measurement, not a file citation) |
| Proxmox host | Orchestrators + heavy tests (inference) | **Yes** | (inference — no Proxmox reference found in this checkout) |
| Homelab VMs | Light workers/watchers; keep headroom for their own services (inference) | No | (inference) |
| Windows GPU workstation (`Workstation-AutoOS`) | i9-13900KF, RTX 3060 12GB, 128GB RAM, WSL2 (inference — hardware details from the private plan, not this checkout); already live as an L1 lane family under `L1-main` | **Yes** (GPU host holding model weights and serving legs counts as trusted; §7) | `docs/tasks.md:34` — "Workstation-AutoOS L1 lanes … Running", several lanes merged (`3f2df52`, `4cbd009`, `d17d1a4`, `b3e69c2`, `19db2c8`, `89eee6d`, `3a1e689`). No `briefs/Workstation.md` in this branch (verified — no `briefs/` dir exists), so the `L1-main` parenting and hardware spec are (inference) |
| Laptops | Overflow workers, online-only, never hold state (inference) | No | (inference) |
| Raspberry Pi | Light API-calling workers/watchers, ARM64 (inference) | No | Architecture gating precedent exists in-checkout: `catalog/linux.json:1560` and `catalog/linux.json:1571` carry `"arch": ["x64"]` (so ARM64 hosts already skip x64-only components); per-CLI ARM64 availability must be verified per component at onboarding time |
| Android/Termux | Light API workers or console-client-only (inference) | No | (inference) |

Every host joins the tailnet with tag `fleet-node` and uses the same layout (`~/fleet/<project>`,
`~/fleet/<project>-worktrees/`) (inference — stated in the gap description, no checkout source).
The tailnet-over-LAN pattern is already this repo's idiom: `README.md:70` serves the browser UI to
"your LAN / tailnet" via `--bind 0.0.0.0`.

## 3. Two-token design

**Not this spec's job (cited, not redesigned):** fleet-node's per-session capability token —
sibling branch `L2-general/fleetnode` §5 Security ("on each launch, fleet-node mints a token"):
per-unit, scoped to one launched work unit, minted locally by the host's fleet-node instance.
Nothing below changes its shape, lifetime, or minting.

**This spec's job:** the per-host OmniRoute `cli_access_tokens` entry — the host's identity to
OmniRoute, separate from and above the per-session tokens that host's fleet-node instance mints
for work it launches.

Field shape (one entry per fleet host: coding VM, Proxmox host, Windows PC, Pi, phone):

| Field | Meaning |
|---|---|
| `name` | Human-stable host name, e.g. `fleet-coding-vm`, `fleet-workstation` (placeholder names; real names stay in git-ignored operator files per `AGENTS.md:16-19`) |
| `scope` | Least-privilege OmniRoute scope the host needs (run + steer legs it serves; never manage) |
| `expiry` | Bounded lifetime with rotation; expiry forces re-issuance, never silent persistence |
| `revocation` | One-click revocation at the OmniRoute server — the fast "kick a compromised host off the fleet" lever (§7) |

**Who issues it: an operator step.** Issuance is done by the operator outside this public repo,
recorded in the DONE note — the same pattern this checkout already uses for operator-only steps:
`configuration/hostexec/README.md:34-36` ("enabling hostexec on a real host is an operator step,
done outside this public repository"), `docs/plans/2026-09-25-routing-v2-spec.md:430` (operator
steps "recorded in the DONE note"), and `tools/probe-rtk.py:694` (missing manage key "exits …
operator step"). No lane, worker, or fleet-node instance ever mints a host token for itself;
self-issuance would let any compromised host enroll new hosts.

**Install-time pickup: env var first, git-ignored config file as fallback.** When fleet-node is
installed on a new host, it reads the host's token from `AUTOOS_OMNIROUTE_KEY` in the environment;
if unset, from the host-local git-ignored config file the installers already use (the
`~/.config/autoos` family — cf. `configuration/hostexec/README.md:42-44`, which copies the
example policy to the git-ignored runtime path and edits it there). Justification: env-first
keeps the secret out of every file the installer writes (composing with `AGENTS.md:16-21`,
which bans secrets in tracked files absolutely), matches the existing env-var precedent
(`AUTOOS_OMNIROUTE_KEY` is already read this way — `tests/linux/05-registry-model-reads.sh:165`),
and the file fallback covers non-interactive/systemd launches where the environment is thin. The
installer never echoes the value; `--dry-run` prints `set`/`unset`, never the token.

## 4. Placement by capability + RAM

A spawn decision picks a host in this order; no step redesigns fleet-node's admission check, each
step reuses it per-host (sibling branch `L2-general/fleetnode` admission §):

1. **Capability match.** Does this host run this client/model? GPU work needs the GPU host
   (`docs/tasks.md:35` names the RTX-class workstation leg); ARM64 hosts skip x64-only components
   per the existing `"arch"` gating (`catalog/linux.json:1560`, `catalog/linux.json:1571`); a host
   that cannot serve the leg is not considered, never attempted-and-failed.
2. **RAM headroom.** The host's fleet-node admission budget (measured per-session RAM against the
   host's ~15GB-class budget — §2) must admit the unit; a host that fails admission reports
   `skipped`-equivalent (not-placed), never half-launched. One admission concept fleet-wide: this
   spec adds no second budget, no parallel accounting.
3. **Trust gate (hard).** Secret-touching work runs **only** on trusted hosts: the coding VM, the
   Proxmox host, and the Windows GPU workstation (§2 table). Laptops, Pi, Termux, and homelab VMs
   never receive secret-touching units however idle they are. This is an invariant, not an
   optimization preference (§7).

## 5. The ollama-ws leg as worked example

Ground truth first, from this checkout — not from the private plan: `docs/tasks.md:35` records
"WS-OLLAMA workstation GPU as OmniRoute provider `ollama-ws` over Tailscale (D-105)" with DONE
criteria "service on tailnet only, provider + registry leg + drift 0 + smoke" and status
**"Queued (tailnet ACL via prox)"**. That status is doing real work in this spec: the leg is
**designed and queued, not yet live**, so this section treats it as the template whose pattern is
already decided, not as a live leg to re-specify — and §6 records the Windows-PC step as partially
(not fully) complete for the same reason.

The decided pattern a future GPU/specialty leg copies:

- **Tailnet-only bind, ACL by host+port.** The leg serves on the tailnet only, with the ACL
  restricting access to the coding VM on port 11434 (inference — port and peer from the gap
  description; the `:11434` convention itself is checkout-grounded: `catalog/ai-registry.json:726`
  serves the local Ollama leg at `http://127.0.0.1:11434/v1`, and `README.md:70` binds wider only
  for LAN/tailnet reach). The local-fallback behavior composes with the existing resolver
  precedent: `lib/windows/AutoOS.Install.psm1:161-177` already probes `127.0.0.1` first, then
  `host.docker.internal`, then falls back — a new tailnet leg slots into that probe order rather
  than replacing it.
- **Free, slow, explicit purpose.** The leg exists for tests and for when free tiers run out —
  price-zero legs already exist in-checkout (`catalog/ai-registry.json:736-737` prices the local
  Ollama leg at 0), and the resolver already prefers free pools first (`.claude/handoff.config.json:58`
  brief template: "free pools first … Paid overflow ONLY on free 429/exhaustion").
- **Catalog-shaped like every local leg.** The install side is ordinary catalog data, not new
  machinery: the Ollama runtime is a `script`-provider component (`catalog/linux.json:845-863`,
  `verify: ollama --version` at `catalog/linux.json:861`), so onboarding the same runtime on a new
  host is a catalog/profile operation, and the registry leg is one more `direct` entry beside
  `catalog/ai-registry.json:725-729`.

To add the *next* specialty leg (e.g. a second GPU worker, §6 step 4): bind tailnet-only, ACL to
the consumer host(s), register provider + registry leg, prove drift 0 + smoke exactly per
`docs/tasks.md:35`'s DONE criteria, keep a local fallback. Nothing in that checklist is new
mechanism — it is the ollama-ws row copied to a new host.

## 6. Rollout order

Decided order (private plan §10 phase E — inference, no checkout source; the order is cited here,
not redesigned):

1. **fleet-node on the Proxmox host FIRST.**
2. **Then the Windows PC.** Status correction, stated plainly: this step is **partially already
   done**. `docs/tasks.md:34` shows the Workstation-AutoOS lane family Running with seven lanes
   merged — the host is enrolled, building, and merging under `L1-main`. What is *not* done is the
   leg that motivated the step: `docs/tasks.md:35` (`ollama-ws`) is still Queued. So the remaining
   Windows-PC work is the `ollama-ws` leg (tailnet ACL via prox, provider + registry leg, drift 0
   + smoke), not the host enrollment this spec must not claim as pending.
3. **Then move one orchestrator to a remote host.**
4. **Then a GPU worker** (§5's template applied to a second GPU host).

**Acceptance gate for phase E:** "a cross-host spawn + steer works end-to-end" (inference — gap
description wording, no checkout source). Concretely: an orchestrator on host A spawns a unit on
host B through B's fleet-node admission, steers it mid-run, and the unit's per-session token
(sibling branch `L2-general/fleetnode` §5) verifies end-to-end — while host A's and host B's
distinct `cli_access_tokens` entries attribute each side (§3). The gate fails if placement fell
back to the local host silently: a cross-host test that ran locally proves nothing (the same
fallacy the repo's test rules already ban — tests assert on the planned command, never on a
convenient substitute).

**Capacity cap during rollout:** building capacity is capped at one control-plane lane
fleet-wide (inference — stated in the gap description as a live rule of this run; no
"one control-plane lane" / slot-language example was found in this checkout's status/inbox files
— searched, none present — so no checkout citation is claimed).

## 7. Security

Two invariants, both hard:

1. **Secret-touching work stays on trusted hosts** (§2, §4 step 3). This composes with the
   adjacent existing constraint `AGENTS.md:16-21` — no passwords, tokens, hostnames, IPs,
   usernames, e-mail addresses or share paths in any tracked file, "not in YAML, not in a
   comment, not in an example, not 'just a placeholder that looks real'", because "this
   repository is **public** and has already leaked credentials once". The placement trust gate
   is the runtime half of that rule: AGENTS.md keeps secrets out of the repo, this spec keeps
   secret-touching *work* off untrusted hosts. A placement decision that would send secret work
   to an untrusted host is refused at plan time (`--dry-run` shows the refusal), never attempted
   and logged.
2. **The `cli_access_tokens` revocation path is the fast lever.** A compromised host is kicked
   off the fleet by revoking its single named entry server-side (§3 table) — one revocation,
   total removal: its fleet-node instance can no longer authenticate to OmniRoute, while every
   other host's entry is untouched and no per-session token needs rotating (those are
   short-lived and locally minted — sibling branch `L2-general/fleetnode` §5). Revocation must
   not require taking any other host offline.

## 8. Migration

- **Additive rollout.** Each host is onboarded independently: install fleet-node, issue its
  `cli_access_tokens` entry (operator step, §3), verify admission + trust tier, place work. No
  onboarding step requires taking existing hosts offline, restarting fleetd, or re-issuing other
  hosts' tokens.
- **One control-plane lane at a time** (§6 cap) is respected across the whole migration: a new
  host's onboarding never competes with an in-flight control-plane lane.
- **Idempotent per `AGENTS.md:113-120`.** "Every script in this repository must be safe to run
  **twice in a row on the same machine**" (`AGENTS.md:115`); onboarding re-runs report `skipped`
  for hosts already enrolled (`AGENTS.md:120` prefers "the package manager's own idempotency …
  over your own check" — same principle: re-issuing a host token when one is already valid is a
  defect, not diligence). Guard token issuance with an existence check, guard tailnet/ACL writes
  with marker checks, never blind-append.

## 9. Open questions

Format follows this branch's own card-`threads` Q-line convention
(`docs/plans/2026-09-28-restart-spec.md:145`: `<id> | <where> | <state> | <next>`; e.g.
`docs/plans/2026-09-28-restart-spec.md:197`: `Q-008 | routing-00 | asked 22:33Z | default a`) —
no fleet-console §12 format exists in this branch (verified: no `*fleet*` file under
`docs/plans/`), so the restart-spec convention is the one this branch can actually cite.

- `Q: MULTIHOST-01 | trust tiers | asked 2026-09-28 | default: coding VM + Proxmox + Workstation trusted, all others untrusted (§2)` — is the homelab-VM exclusion right, or do some homelab VMs hold their own secrets and need a finer tier?
- `Q: MULTIHOST-02 | token scope | asked 2026-09-28 | default: least-privilege run+steer, never manage (§3)` — which exact OmniRoute scopes does a fleet host need beyond serve/run/steer?
- `Q: MULTIHOST-03 | expiry | asked 2026-09-28 | default: bounded with rotation (§3)` — what lifetime, and does expiry revoke in-flight units or only block new spawns?
- `Q: MULTIHOST-04 | ollama-ws ACL | asked 2026-09-28 | default: tailnet-only, coding VM on 11434 (§5)` — (inference) confirm the ACL peer/port before the leg leaves Queued.
- `Q: MULTIHOST-05 | phase-E gate | asked 2026-09-28 | default: cross-host spawn + steer end-to-end (§6)` — who witnesses the gate, and does the first remote orchestrator move require the operator present?
- `Q: MULTIHOST-06 | Pi/Termux clients | asked 2026-09-28 | default: console-client-only until a per-CLI ARM64 path is proven (§2)` — which CLIs are actually installable on ARM64/Termux, and does that change any host's trust tier?

## 10. Closing table

Budget mode per D-102 (inference — D-102 itself lives in the private plan; its checkout-visible
form is `docs/tasks.md:31`: "Claude legs off non-final routes … est. Claude ~120k
(orchestration + Sonnet final)"): writers and first-reviewers are free models, Claude only
reviews and finals — every row's Claude estimate is kept small, and any row over ~1M weighted
tokens would be flagged rather than under-estimated (no row below is near that line).

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| MH-TOKEN | `cli_access_tokens` host-issuance runbook + pickup (env-first, file fallback) + revocation drill on one test entry (§3, §7) | ~40k (final only) | ~2 h | Operator (issuance is an operator step) |
| MH-ACL | `ollama-ws` tailnet ACL + provider/registry leg + drift 0 + smoke = move `docs/tasks.md:35` Queued → Done (§5) | ~50k (matches the `docs/tasks.md:35` row's own est.) | ~3 h | Operator (tailnet ACL via prox) |
| MH-PROXMOX | fleet-node on the Proxmox host first: install, host token, admission verify (§6 step 1) | ~60k (final only) | ~4 h | MH-TOKEN |
| MH-ORCH-MOVE | Move one orchestrator to a remote host (§6 step 3) | ~60k (final only) | ~4 h | MH-PROXMOX, MH-ACL |
| MH-GATE | Cross-host spawn + steer end-to-end proof (§6 gate) | ~30k (final only) | ~2 h | MH-ORCH-MOVE |
| MH-GPU2 | Second GPU worker via the §5 template (bind, ACL, leg, smoke) | ~50k (final only) | ~3 h | MH-GATE |
