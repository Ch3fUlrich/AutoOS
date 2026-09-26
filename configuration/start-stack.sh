#!/usr/bin/env bash
# Start the AutoOS AI stack: OmniRoute gateway, then the app you pick.
#
#   export AUTOOS_OMNIROUTE_KEY='sk-...'   # dashboard -> api-manager
#   ./configuration/start-stack.sh opencode|zed|nvim|openhands|opencode-serve
set -euo pipefail

GATEWAY="http://127.0.0.1:20128"
APP="${1:-none}"
# The key file sits next to this script: configuration/api-keys.yml.
KEYS_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/api-keys.yml"
if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" && -f "$KEYS_FILE" ]]; then
    AUTOOS_OMNIROUTE_KEY="$(sed -n 's/^omniroute[[:space:]]*:[[:space:]]*//p' "$KEYS_FILE" | head -1 | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')"
    export AUTOOS_OMNIROUTE_KEY
fi
if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then
    echo "No OmniRoute client key. Add 'omniroute: sk-...' to configuration/api-keys.yml,"
    echo "or export AUTOOS_OMNIROUTE_KEY. Then configure providers: ./configuration/omniroute/apply.sh"
    exit 1
fi

gateway_ok() {
    # /api/health, not /v1/models: the latter 401s for a normal client key in
    # this build, so probing it would call a healthy gateway "down" forever.
    curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1
}

# ss_backup_path <path>: the name for a NEW backup of <path> -
# <path>.autoos-backup-<stamp>, then -1, -2, ... while that name is taken.
# The stamp has one-second resolution and a plain overwrite used to destroy
# an earlier same-second backup; see lib/linux/install.sh backup_path for
# the source of this rule. This launcher is standalone (does not source
# that lib), so it gets its own copy.
ss_backup_path() {
    local path="$1" stamp="${2:-}" base candidate n=0
    [[ -n "$stamp" ]] || stamp="$(date +%Y%m%d-%H%M%S)"
    base="$path.autoos-backup-$stamp"
    candidate="$base"
    while [[ -e "$candidate" || -L "$candidate" ]]; do
        n=$((n + 1))
        candidate="$base-$n"
    done
    printf '%s\n' "$candidate"
}

# ss_move_aside <path>: move an unparseable file aside to a fresh backup
# name, printing where it went. Returns non-zero - with the original left
# in place and nothing left behind - when the copy failed, so callers keep
# a file OpenHands regenerates rather than deleting one with no backup
# (AGENTS.md hard rule 5).
ss_move_aside() {
    local path="$1" backup
    backup="$(ss_backup_path "$path")" || return 1
    if cp -p -- "$path" "$backup"; then
        rm -f -- "$path"
        echo "Unparseable OpenHands settings moved aside to $backup (OpenHands regenerates)."
    else
        rm -f -- "$backup"
        echo "  ! could not back up $path - leaving it untouched."
        return 1
    fi
}

# Docker AI stack (server profile, configuration/docker/ai-stack): the
# gateway, opencode serve and OpenHands are compose services there.
AI_STACK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker/ai-stack/ai-stack.sh"
IN_DOCKER=0
# AUTOOS_AI_STACK_MIGRATING=1 is set by `ai-stack.sh migrate` for its OpenHands
# step only: the stack owns nothing yet (its marker is written once every
# service answered), but this run must start the compose service.
if [[ "${AUTOOS_AI_STACK_MIGRATING:-}" == 1 ]] || bash "$AI_STACK" is-active >/dev/null 2>&1; then IN_DOCKER=1; fi

if ! gateway_ok && [[ $IN_DOCKER -eq 1 ]]; then
    echo "Starting the gateway container..."
    bash "$AI_STACK" up omniroute
    gateway_ok || { echo "Gateway did not answer. Run: $AI_STACK status"; exit 1; }
elif ! gateway_ok; then
    command -v omniroute >/dev/null || { echo "omniroute is not installed. Run: ./setup.sh --only omniroute --yes"; exit 1; }
    echo "Starting OmniRoute in the background..."
    nohup omniroute --no-open --port 20128 >/tmp/omniroute.log 2>&1 &
    for _ in $(seq 1 24); do gateway_ok && break; sleep 5; done
    gateway_ok || { echo "Gateway did not answer. Run: omniroute doctor"; exit 1; }
fi
echo "Gateway OK on $GATEWAY"

case "$APP" in
    opencode)  opencode ;;
    zed)       zed . ;;
    nvim)      nvim ;;
    openhands)
        # Docker must be running; the container reaches the gateway via host IP.
        # Image name is the current upstream one (the old docker.all-hands.dev
        # registry is gone). The sandbox/agent-server image is chosen by
        # OpenHands itself on first conversation - do not pin it.
        # The client key is exported and inherited with `-e LLM_API_KEY` (no
        # value on the command line, so `ps` never shows it).
        # User settings drift newer than the image (measured 2026-09-23:
        # agent-canvas 1.20 writes agent_settings.schema_version 6 + an
        # `enabled` key on every MCP server, while the
        # docker.openhands.dev/openhands/openhands:latest image supports
        # version 4 and rejects `enabled` with extra_forbidden -> every
        # /api settings route 500s. Repair in place (with a backup, never
        # a delete): clamp the version DOWN to 4 (older payloads keep
        # theirs so the image's own migrations still run) and strip the
        # `enabled` keys. Unparseable files still move aside - OpenHands
        # regenerates. NOTE: the version lives under agent_settings, NOT
        # top-level schema_version (top stays 2-3 on both good and bad
        # files, so checking the top level misses the breakage).
        oh_settings="$HOME/.openhands/settings.json"
        if [[ -f "$oh_settings" ]]; then
            oh_repair_rc=0
            python3 - "$oh_settings" <<'PY' || oh_repair_rc=$?
import json, os, shutil, sys, datetime
path = sys.argv[1]
try:
    doc = json.load(open(path, encoding="utf-8"))
except Exception:
    print("Unparseable OpenHands settings.")
    sys.exit(2)
agent = doc.get("agent_settings") if isinstance(doc.get("agent_settings"), dict) else {}
ver = agent.get("schema_version")
has_enabled = any(isinstance(s, dict) and "enabled" in s for s in (agent.get("mcp_config") or {}).values())
if (isinstance(ver, int) and ver > 4) or has_enabled:
    # AUTOOS_BACKUP_STAMP pins the stamp so a test can force the clash;
    # otherwise the stamp is now (one-second resolution, like the bash site).
    ts = os.environ.get("AUTOOS_BACKUP_STAMP") or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = path + ".autoos-backup-" + ts
    backup = base
    n = 0
    while os.path.lexists(backup):
        n += 1
        backup = "%s-%d" % (base, n)
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        print("Could not back up %s: %s - leaving it untouched." % (path, exc))
        sys.exit(1)
    if isinstance(ver, int) and ver > 4:
        agent["schema_version"] = 4
    for srv in (agent.get("mcp_config") or {}).values():
        if isinstance(srv, dict):
            srv.pop("enabled", None)
    json.dump(doc, open(path, "w", encoding="utf-8"), indent=2)
    print("Repaired OpenHands settings (agent_settings.schema_version %s -> 4, stripped enabled keys; backup kept)." % ver)
sys.exit(0)
PY
            if [[ $oh_repair_rc -eq 2 ]]; then
                # Best-effort: a failed copy keeps the file, never deletes it.
                ss_move_aside "$oh_settings" || true
            elif [[ $oh_repair_rc -ne 0 ]]; then
                echo "OpenHands settings repair reported a problem (exit $oh_repair_rc) - continuing anyway."
            fi
        fi
        # Re-project the tier profiles from the spec on every start: a rotated
        # key, a re-curated spec, or a hand edit converges back automatically.
        _ss_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
        if command -v python3 >/dev/null; then
            # Read by the docker app below: host.docker.internal resolves in
            # there (--add-host on native Linux, natively on Docker Desktop).
            python3 "$_ss_root/tools/sync-openhands-profiles.py" --openhands-dir "$HOME/.openhands" --consumer container \
                || echo "tier profile sync reported a problem - continuing with existing profiles"
        else
            echo "python3 not found - tier profile sync skipped (the installer covers it)"
        fi
        # Docker creates a missing bind-mount source as root; create it as
        # the user first. A root-owned tree from an older run (the image
        # defaults to SANDBOX_USER_ID=0) cannot be fixed without sudo: say so.
        mkdir -p "$HOME/.openhands"
        if [[ -n "$(find "$HOME/.openhands" -maxdepth 2 ! -user "$(id -u)" -print -quit 2>/dev/null)" ]]; then
            echo "Some files under ~/.openhands are not yours (an older root-run container). Fix once with:"
            echo "  sudo chown -R $(id -un):$(id -gn) ~/.openhands"
        fi
        if [[ $IN_DOCKER -eq 1 ]]; then
            # The compose service: pinned image, hardening, SANDBOX_VOLUMES.
            bash "$AI_STACK" up openhands
        elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'openhands-app'; then
            docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'openhands-app' \
                || docker start openhands-app >/dev/null
        else
            # Remote browsers (the phone through the LAN proxy): the UI hands
            # the browser each sandbox's URL, http://localhost:<random port>
            # by default - unreachable from anywhere but this host. With
            # AUTOOS_OPENHANDS_SANDBOX_URL (a pattern with {port}, e.g. a
            # proxy path that maps back to <this-host>:{port}) and
            # AUTOOS_OPENHANDS_WEB_HOST (the public name, for CORS) set, the
            # container gets them; unset, nothing changes. Recreate the
            # container (docker rm -f openhands-app) after changing either.
            oh_remote=()
            if [[ -n "${AUTOOS_OPENHANDS_SANDBOX_URL:-}" ]]; then
                oh_remote+=(-e OH_SANDBOX_CONTAINER_URL_PATTERN="$AUTOOS_OPENHANDS_SANDBOX_URL")
            fi
            if [[ -n "${AUTOOS_OPENHANDS_WEB_HOST:-}" ]]; then
                oh_remote+=(-e WEB_HOST="$AUTOOS_OPENHANDS_WEB_HOST")
            fi
            export LLM_API_KEY="$AUTOOS_OMNIROUTE_KEY"
            # Detached, no -it: -it fails without a TTY (non-interactive
            # shells) and foreground -it never returns, so the URL line below
            # would lie.
            # SANDBOX_USER_ID: the entrypoint otherwise runs the app as root
            # (image default 0) and ~/.openhands fills with root-owned files.
            # --restart unless-stopped (not --rm): docker itself brings the
            # UI back after a reboot, and a hand `docker stop` sticks.
            # --memory: this stack shares a ~10 GB host with the gateway,
            # litellm and the agents; AUTOOS_OPENHANDS_MEMORY overrides.
            # Sandboxes (agent-server containers) are started by the app
            # with --add-host host.docker.internal:host-gateway already.
            # -p 3000:3000 binds every interface: the LAN reverse proxy is
            # another host. OpenHands has NO login of its own - restrict
            # :3000 to the proxy (docs/web-services.md).
            docker run -d --restart unless-stopped \
                --memory "${AUTOOS_OPENHANDS_MEMORY:-2g}" \
                -e SANDBOX_USER_ID="$(id -u)" \
                -e LLM_MODEL=openai/t1-orchestrator \
                -e LLM_API_KEY \
                -e LLM_BASE_URL="http://host.docker.internal:20128/v1" \
                -e LOG_ALL_EVENTS=true \
                "${oh_remote[@]}" \
                -p 3000:3000 \
                -v /var/run/docker.sock:/var/run/docker.sock \
                -v "$HOME/.openhands:/.openhands" \
                --add-host host.docker.internal:host-gateway \
                --name openhands-app \
                docker.openhands.dev/openhands/openhands:latest >/dev/null
            unset LLM_API_KEY
        fi
        # No URL line without a probe behind it.
        for _ in $(seq 1 36); do
            curl -s -m 5 -o /dev/null http://127.0.0.1:3000/ 2>/dev/null && break
            sleep 5
        done
        curl -s -m 5 -o /dev/null http://127.0.0.1:3000/ 2>/dev/null \
            || { echo "OpenHands did not answer on :3000 - see: docker logs openhands-app"; exit 1; }
        # This image keeps LLM profiles in its own settings store and never
        # reads profiles/*.json: save the tiers through its API (idempotent,
        # capped by the app at 10 - spec order decides which tiers make it).
        if command -v python3 >/dev/null; then
            # Keep the exit code: a failing sync (exit 2) must be reported, not
            # swallowed by the filter under pipefail, and must not abort the start.
            push_out="$(python3 "$_ss_root/tools/sync-openhands-profiles.py" --openhands-dir "$HOME/.openhands" \
                --consumer container --push-url http://127.0.0.1:3000 2>&1)" && push_rc=0 || push_rc=$?
            printf '%s\n' "$push_out" | grep -v ' skipped (up to date)$' || true
            if [[ $push_rc -ne 0 ]]; then
                echo "OpenHands tier-profile push failed (exit $push_rc) - the app keeps its old profiles"
            fi
        fi
        echo "OpenHands UI: http://localhost:3000"
        ;;
    opencode-serve)
        # Phone fallback UI (docs/openhands-runbook.md rung 2): resume when
        # down, no-op when up. The wrapper pins the Basic-auth password
        # (user "opencode", ~/.config/autoos/opencode-serve.password) and
        # exports the {env:...} keys the global opencode config references.
        _ss_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
        if [[ $IN_DOCKER -eq 1 ]]; then
            bash "$AI_STACK" up opencode
        elif curl -s -m 5 -o /dev/null "http://127.0.0.1:4096/" 2>/dev/null; then
            echo "opencode serve already up on :4096 - nothing to do."
        elif ! command -v opencode >/dev/null; then
            echo "opencode is not installed. Run: ./setup.sh --only opencode-cli --yes"
            exit 1
        else
            if systemctl --user cat autoos-opencode.service >/dev/null 2>&1; then
                echo "Starting the autoos-opencode unit..."
                systemctl --user start autoos-opencode.service
            else
                "$_ss_root/configuration/autostart/run-opencode-serve.sh" --detach
            fi
            for _ in $(seq 1 24); do
                curl -s -m 5 -o /dev/null "http://127.0.0.1:4096/" 2>/dev/null && break
                sleep 5
            done
            curl -s -m 5 -o /dev/null "http://127.0.0.1:4096/" 2>/dev/null \
                || { echo "opencode serve did not answer on :4096 - see ~/.local/state/autoos/opencode-serve.log"; exit 1; }
            echo "opencode serve up on http://localhost:4096 (user: opencode, password in ~/.config/autoos/opencode-serve.password)."
        fi
        ;;
esac
