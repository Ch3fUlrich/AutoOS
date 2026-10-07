# OpenCode runtime sources (host-local copies, 2026-10-05)

These files run today on the central host, outside the repo. They are saved here so nothing lives only on one machine.
They are UNTESTED copies (`.txt` so no tool runs or lints them). Lane c2 moves them into `tools/oc_runtime/` with install docs and
tests using fake sessions. Until then, restore them by copying to the paths below and removing `.txt`.

| File | Host path | What it does |
|---|---|---|
| `watch2.py.txt` | `~/fleet/oc-pilot/watch2.py` | One watcher per lane (env `LANE`, `CLONE`, `ROLE_TEXT`, `BLOCK_GATE`, `NUDGE_EVERY`, `ROTATE_MSGS`, `SESSION_GATEWAY_URL`, `STATUS_FILE`, `L1_OUTBOX`): nudge an idle session, restart a dead or stuck-tool session, start a lane that has no state, rotate by message count (never while a spawned run is active), cap a spawned run at 850k input, read the host cost gate and switch the lane model gemini -> ovh gpt-oss at block and back at the UTC roll, keep only the newest task in the inbox. |
| `throttle.py.txt` | `~/fleet/oc-pilot/throttle.py` | Reverse proxy in front of the gateway: minimum seconds between model calls of one session (longer while a spawned run is active). |
| `write-block.py.txt` | `~/fleet/oc-pilot/write-block.py` | Writes a gate file with verdict `block` (refreshed every 10 minutes) so the L1's spawner refuses every Google-paid start. |
| `*.service.txt` | `~/.config/systemd/user/*.service` | User units: `oc-pilot-watch`, `oc-l0-watch` (KillMode=process: restarting a watcher must not kill the sessions), `oc-throttle-l1` (port 47181), `oc-throttle-l0` (port 47180), `oc-pilot-gate-block` (+ its timer, every 10 minutes). |

Host-local config (never committed): `~/.config/autoos/oc-l1.json` (lanes `oc-pilot` = AutoOS L1, `oc-l0` = router; model key and gateway model id per lane), `~/.config/autoos/oc-l1.pw` (mode 600, session password), `~/fleet/<lane>/` (state, scratch, logs).

The unit files carry an `OPENCODE_CONFIG_CONTENT` overlay (JSON, merged last by opencode): `permission.edit` deny for `*` and allow for `.oc-pilot/**`; `provider.omniroute.options.baseURL` = the lane's own throttle proxy (the project `opencode.jsonc` of an AutoOS clone pins the gateway address and overrides the scratch config, so without this the throttle is silently bypassed); `permission.external_directory` allow only for the paths the lane needs (L0: its own clone, the L1 clone, the gate state dir).

Exact commands: see `docs/handoff/OPENCODE-L1-AUTOOS.md`, section "Restart from the card".
