#!/usr/bin/env bash
# configuration/hostexec/install.sh -- wire the hostexec broker into agent
# clients and install the systemd USER unit file.
#
# This driver never enables or starts anything. Enabling/starting is an
# operator step, printed (not executed) by this driver:
#
#   systemctl --user daemon-reload && systemctl --user enable --now autoos-hostexec.service
#
# Usage:
#   install.sh [--dry-run] [--clients openhands,claude,codex,opencode,qoder]
#              [--unit] [--unregister]
#
#   --dry-run    print every action, write nothing.
#   --clients    comma-separated client list to wire (default: none).
#   --unit       install the systemd USER unit file only (ignore --clients).
#   --unregister remove only the hostexec entries/unit this driver writes.
#
# Rules (AGENTS.md): read the existing file, set only our own key, write
# back; back every replaced file up first; installing twice reports
# "already current" instead of rewriting. The bearer token is read from a
# 0600 file and never appears on any argv, or in output/logs.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="$REPO/configuration/hostexec/autoos-hostexec.service"
UNIT_NAME="autoos-hostexec.service"
KNOWN_CLIENTS="openhands claude codex opencode qoder"

DRY_RUN=0
UNIT_ONLY=0
UNREGISTER=0
CLIENTS=""
PORT=""
REQUESTED=()

TMP_FILES=()
tmp_cleanup() {
    if (( ${#TMP_FILES[@]} > 0 )); then
        rm -f "${TMP_FILES[@]}"
    fi
}
trap tmp_cleanup EXIT

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

usage() {
    cat <<'EOF'
Usage: install.sh [--dry-run] [--clients a,b,...] [--unit] [--unregister]

  --dry-run    print every action, write nothing.
  --clients    comma-separated list of clients to wire
               (any of openhands,claude,codex,opencode,qoder; default: none).
  --unit       install the systemd USER unit file only; ignore --clients.
  --unregister remove only the hostexec entries/unit this driver writes.

The driver never enables or starts the unit; it prints the operator
command instead. Tokens come from 0600 files
(~/.config/autoos/exec/<client>.token, or AUTOOS_EXEC_TOKEN_FILE_<CLIENT>)
and never appear on any argv or in output.
EOF
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --dry-run) DRY_RUN=1; shift ;;
            --unit) UNIT_ONLY=1; shift ;;
            --unregister) UNREGISTER=1; shift ;;
            --clients)
                if (( $# < 2 )); then
                    err "install: --clients needs a comma-separated list"
                    return 2
                fi
                CLIENTS="$2"; shift 2 ;;
            --clients=*) CLIENTS="${1#--clients=}"; shift ;;
            -h|--help) usage; exit 0 ;;
            *)
                err "install: unknown argument: $1"
                usage >&2
                return 2 ;;
        esac
    done
    return 0
}

is_known_client() {
    case " ${KNOWN_CLIENTS} " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# default_port: the broker default, read from tools/hostexec/server.py so
# the driver tracks it (AUTOOS_EXEC_PORT overrides when set).
default_port() {
    local parsed
    parsed="$(grep -E '^DEFAULT_PORT[[:space:]]*=[[:space:]]*[0-9]+' \
        "$REPO/tools/hostexec/server.py" 2>/dev/null | sed -E 's/^[^=]*=[[:space:]]*([0-9]+).*/\1/' \
        | head -n 1 || true)"
    if [[ "$parsed" =~ ^[0-9]+$ ]]; then
        printf '%s' "$parsed"
    else
        printf '8765'
    fi
}

new_tmp() {
    local made
    made="$(mktemp)"
    TMP_FILES+=("$made")
    printf '%s' "$made"
}

# backup_file <path>: copy to <path>.autoos-backup-<stamp>, then -1, -2,
# ... while taken, so two backups in the same second keep both. Missing
# path: no-op. Dry-run: print, create nothing.
backup_file() {
    local path="$1" stamp dest idx
    if [[ ! -e "$path" && ! -L "$path" ]]; then
        return 0
    fi
    stamp="$(date +%Y%m%d%H%M%S)"
    dest="${path}.autoos-backup-${stamp}"
    if [[ -e "$dest" || -L "$dest" ]]; then
        idx=1
        while [[ -e "${dest}-${idx}" || -L "${dest}-${idx}" ]]; do
            idx=$((idx + 1))
        done
        dest="${dest}-${idx}"
    fi
    if (( DRY_RUN )); then
        say "dry-run: would back up ${path} to ${dest}"
        return 0
    fi
    cp -p "$path" "$dest" || return 1
    say "backup: ${path} -> ${dest}"
}

# install_unit: render the template with the repo path and install it to
# ~/.config/systemd/user/ only when it differs (cmp). A failed copy stops
# with the existing file untouched (stage + rename, never truncate).
install_unit() {
    local dest rendered esc stage
    dest="$HOME/.config/systemd/user/${UNIT_NAME}"
    if [[ "${AUTOOS_HOSTEXEC_BREAK:-}" == "no-template" ]]; then
        err "install: refusing: unit template is unavailable (test hook)"
        return 1
    fi
    if [[ ! -f "$TEMPLATE" ]]; then
        err "install: refusing: unit template not found: ${TEMPLATE}"
        return 1
    fi
    rendered="$(new_tmp)"
    esc="$REPO"
    esc="${esc//\\/\\\\}"
    esc="${esc//&/\\&}"
    esc="${esc//|/\\|}"
    if ! sed "s|<repo>|${esc}|g" "$TEMPLATE" > "$rendered"; then
        err "install: refusing: could not render unit template"
        return 1
    fi
    if [[ -f "$dest" ]] && cmp -s "$rendered" "$dest"; then
        say "unit: already current: ${dest}"
    else
        if (( DRY_RUN )); then
            say "dry-run: would install unit: ${dest}"
        else
            backup_file "$dest" || return 1
            mkdir -p "$(dirname "$dest")"
            stage="$(dirname "$dest")/.${UNIT_NAME}.tmp.$$"
            TMP_FILES+=("$stage")
            if ! cp "$rendered" "$stage"; then
                rm -f "$stage"
                err "install: refusing: copy failed, leaving untouched: ${dest}"
                return 1
            fi
            mv "$stage" "$dest"
            say "unit: installed: ${dest}"
        fi
    fi
    say "unit: operator step (NOT run by this driver): systemctl --user daemon-reload && systemctl --user enable --now ${UNIT_NAME}"
    return 0
}

# token_file_for <client>: the token file path -- env
# AUTOOS_EXEC_TOKEN_FILE_<CLIENT> (uppercased) wins, else the
# ~/.config/autoos/exec/<client>.token default.
token_file_for() {
    local client="$1" varname
    varname="AUTOOS_EXEC_TOKEN_FILE_${client^^}"
    if [[ -n "${!varname:-}" ]]; then
        printf '%s' "${!varname}"
    else
        printf '%s' "$HOME/.config/autoos/exec/${client}.token"
    fi
}

# file_mode <path>: numeric mode (e.g. 600), portable GNU/BSD stat.
file_mode() {
    local mode
    mode="$(stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null \
        || printf 'unknown')"
    printf '%s' "$mode"
}

# token_file_ok <client>: the token file exists and is mode 0600. The
# value is never printed -- neither here nor by any caller.
token_file_ok() {
    local client="$1" path mode
    path="$(token_file_for "$client")"
    if [[ ! -f "$path" ]]; then
        err "install: refusing: token file not found for ${client}: ${path}"
        err "install: create it first, e.g.: python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ${path} && chmod 600 ${path}"
        return 1
    fi
    mode="$(file_mode "$path")"
    if [[ "$mode" != "600" ]]; then
        err "install: refusing: token file for ${client} has mode ${mode}, need 0600: ${path}"
        return 1
    fi
    return 0
}

# read_token <client>: print the token on stdout -- the only channel that
# ever carries the value. Callers capture it into a variable, and must
# never print it, log it, or place it on any argv.
read_token() {
    local client="$1" path value
    token_file_ok "$client" || return 1
    path="$(token_file_for "$client")"
    value="$(<"$path")"
    if [[ -z "$value" ]]; then
        err "install: refusing: token file is empty: ${path}"
        return 1
    fi
    printf '%s' "$value"
    return 0
}

loopback_url() {
    printf 'http://127.0.0.1:%s/mcp' "$PORT"
}

# publish_candidate <file> <cand>: backup the existing file first (keeping
# its mode), then install the candidate via stage + rename, so a failed
# copy leaves the existing file untouched.
publish_candidate() {
    local file="$1" cand="$2" mode="" stage
    if [[ -e "$file" || -L "$file" ]]; then
        backup_file "$file" || return 1
        mode="$(file_mode "$file")"
    fi
    mkdir -p "$(dirname "$file")"
    stage="$(dirname "$file")/.hostexec.tmp.$$"
    TMP_FILES+=("$stage")
    if ! cp "$cand" "$stage"; then
        rm -f "$stage"
        err "install: refusing: copy failed, leaving untouched: ${file}"
        return 1
    fi
    if [[ -n "$mode" && "$mode" != "unknown" ]]; then
        chmod "$mode" "$stage"
    fi
    mv "$stage" "$file"
    return 0
}

# apply_candidate <client> <file> <status> <cand> <what>: fold a rendered
# candidate into place -- already-current, dry-run notice, or backup +
# publish.
apply_candidate() {
    local client="$1" file="$2" status="$3" cand="$4" what="$5"
    if [[ "$status" == "same" ]]; then
        say "${client}: already current: ${file}"
        return 0
    fi
    if (( DRY_RUN )); then
        say "dry-run: would set ${what} in ${file}"
        return 0
    fi
    publish_candidate "$file" "$cand" || return 1
    say "${client}: wrote ${what}: ${file}"
    return 0
}

# json_candidate <op> <file> <client> <url> <etype> <cand>: render the
# candidate JSON (set|remove the client's own hostexec entry, keeping every
# other key) to <cand> and print changed|same. The token arrives through
# HOSTEXEC_TOKEN in the environment, never on argv. A file that looks like
# JSONC (comments outside strings) is refused, never rewritten. Exit 3 on
# refusal.
json_candidate() {
    HOSTEXEC_TOKEN="${HOSTEXEC_TOKEN:-}" python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PYEOF'
import json, os, sys

PARENTS = {
    "openhands": ["agent_settings", "mcp_config"],
    "claude": ["mcpServers"],
    "opencode": ["mcp"],
}

def has_comments(text):
    i, n, in_str, esc = 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "/" and i + 1 < n and (text[i + 1] == "/" or text[i + 1] == "*"):
            return True
        i += 1
    return False

def main(argv):
    op, path, client, url, etype, out = argv[1:7]
    token = os.environ.get("HOSTEXEC_TOKEN", "")
    keys = PARENTS[client]
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if has_comments(text):
            print("install: refusing: %s looks like JSONC (comments); "
                  "refusing to rewrite it" % path, file=sys.stderr)
            return 3
        try:
            data = json.loads(text)
        except ValueError as exc:
            print("install: refusing: %s is not valid JSON: %s" % (path, exc),
                  file=sys.stderr)
            return 3
        if not isinstance(data, dict):
            print("install: refusing: %s top level is not an object" % path,
                  file=sys.stderr)
            return 3
    else:
        data = {}
    node = data
    for key in keys:
        if key not in node:
            node[key] = {}
        child = node.get(key)
        if not isinstance(child, dict):
            print("install: refusing: %s key '%s' is not an object "
                  "(refusing to replace user value)" % (path, key),
                  file=sys.stderr)
            return 3
        node = child
    if op == "set":
        entry = {}
        if etype:
            entry["type"] = etype
        entry["url"] = url
        entry["headers"] = {"Authorization": "Bearer " + token}
        current = node.get("hostexec")
        popped = False
        if isinstance(current, dict) and "enabled" in current:
            del current["enabled"]
            popped = True
        if not popped and current == entry:
            status = "same"
        else:
            node["hostexec"] = entry
            status = "changed"
    elif op == "remove":
        if "hostexec" not in node:
            status = "same"
        else:
            del node["hostexec"]
            status = "changed"
    else:
        print("install: bad json op", file=sys.stderr)
        return 2
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    sys.stdout.write(status)
    return 0

sys.exit(main(sys.argv))
PYEOF
}

# wire_json_client <client> <file> <url> <etype>: set the client's own
# hostexec entry, keeping every other key, backing up before replacing.
wire_json_client() {
    local client="$1" file="$2" url="$3" etype="$4"
    local tok cand rc=0 status=""
    tok="$(read_token "$client")" || return 1
    cand="$(new_tmp)"
    export HOSTEXEC_TOKEN="$tok"
    tok=""
    status="$(json_candidate set "$file" "$client" "$url" "$etype" "$cand")" || rc=$?
    HOSTEXEC_TOKEN=""
    export HOSTEXEC_TOKEN
    if (( rc == 3 )); then
        return 1
    elif (( rc != 0 )); then
        err "install: ${client}: config helper failed (exit ${rc})"
        return 1
    fi
    apply_candidate "$client" "$file" "$status" "$cand" "hostexec entry" || return 1
    return 0
}

# toml_candidate <op> <file> <url> <cand>: line-based edit of the codex
# config -- set|remove the [mcp_servers.hostexec] section, keeping every
# other key. The token itself is never stored here, only
# bearer_token_env_var = "AUTOOS_EXEC_TOKEN". Prints changed|same.
toml_candidate() {
    python3 - "$1" "$2" "$3" "$4" <<'PYEOF'
import os, sys

SECTION = "[mcp_servers.hostexec]"

def norm(header):
    return "".join(header.split())

def main(argv):
    op, path, url, out = argv[1:5]
    url_line = 'url = "%s"' % url
    env_line = 'bearer_token_env_var = "AUTOOS_EXEC_TOKEN"'
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    lines = text.split("\n")
    start = None
    end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if norm(stripped) == SECTION:
                start = i
            elif start is not None:
                end = i
                break
    if op == "set":
        if start is None:
            out_lines = [line for line in lines]
            while out_lines and out_lines[-1] == "":
                out_lines.pop()
            if out_lines:
                out_lines.append("")
            out_lines += [SECTION, url_line, env_line]
        else:
            body_end = end if end is not None else len(lines)
            body = lines[start + 1:body_end]
            if body and body[-1] == "":
                body = body[:-1]
            new_body = []
            seen_url = False
            seen_env = False
            for line in body:
                key = line.split("=", 1)[0].strip() if "=" in line else ""
                if key == "url" and not seen_url:
                    new_body.append(url_line)
                    seen_url = True
                elif key == "bearer_token_env_var" and not seen_env:
                    new_body.append(env_line)
                    seen_env = True
                else:
                    new_body.append(line)
            if not seen_url:
                new_body.append(url_line)
            if not seen_env:
                new_body.append(env_line)
            out_lines = lines[:start + 1] + new_body
            if end is not None:
                out_lines += lines[body_end:]
        while out_lines and out_lines[-1] == "":
            out_lines.pop()
        new = "\n".join(out_lines) + "\n" if out_lines else ""
    elif op == "remove":
        if start is None:
            new = text
        else:
            body_end = end if end is not None else len(lines)
            pre = start
            if pre > 0 and lines[pre - 1].strip() == "":
                pre -= 1
            out_lines = lines[:pre]
            if end is not None:
                out_lines += lines[body_end:]
            while out_lines and out_lines[-1] == "":
                out_lines.pop()
            new = "\n".join(out_lines) + "\n" if out_lines else ""
    else:
        print("install: bad toml op", file=sys.stderr)
        return 2
    orig = text if text == "" or text.endswith("\n") else text + "\n"
    status = "same" if new == orig else "changed"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(new if status == "changed" else orig)
    sys.stdout.write(status)
    return 0

sys.exit(main(sys.argv))
PYEOF
}

wire_openhands() {
    wire_json_client openhands "$HOME/.openhands/settings.json" \
        "http://host.docker.internal:${PORT}/mcp" "" || return 1
    return 0
}

wire_claude() {
    wire_json_client claude "$HOME/.claude.json" "$(loopback_url)" "http" || return 1
    return 0
}

wire_codex() {
    local file cand rc=0 status="" path
    file="$HOME/.codex/config.toml"
    token_file_ok codex || return 1
    path="$(token_file_for codex)"
    cand="$(new_tmp)"
    status="$(toml_candidate set "$file" "$(loopback_url)" "$cand")" || rc=$?
    if (( rc != 0 )); then
        err "install: codex: config helper failed (exit ${rc})"
        return 1
    fi
    apply_candidate codex "$file" "$status" "$cand" "hostexec section" || return 1
    say "codex: the token itself stays in ${path}; export AUTOOS_EXEC_TOKEN from it before starting codex."
    return 0
}

wire_opencode() {
    local file
    file="$HOME/.config/opencode/opencode.json"
    if [[ ! -e "$file" && -e "$HOME/.config/opencode/opencode.jsonc" ]]; then
        file="$HOME/.config/opencode/opencode.jsonc"
    fi
    wire_json_client opencode "$file" "$(loopback_url)" "remote" || return 1
    return 0
}

wire_qoder() {
    local path url
    token_file_ok qoder || return 1
    path="$(token_file_for qoder)"
    url="$(loopback_url)"
    if (( DRY_RUN )); then
        say "dry-run: would print the qoder operator command (token stays in ${path})"
    fi
    say "qoder: run this yourself (the token is read from the file at run time, never printed):"
    printf '%s\n' "qodercli mcp add-json hostexec \"{\\\"url\\\": \\\"${url}\\\", \\\"headers\\\": {\\\"Authorization\\\": \\\"Bearer \$(cat ${path})\\\"}}\""
    return 0
}

# wire_client <client>: set up one client's hostexec entry.
wire_client() {
    case "$1" in
        openhands) wire_openhands ;;
        claude) wire_claude ;;
        codex) wire_codex ;;
        opencode) wire_opencode ;;
        qoder) wire_qoder ;;
        *) err "install: unknown client: $1"; return 2 ;;
    esac
}

REMOVED_ANY=0

# apply_remove_candidate <client> <file> <status> <cand> <what>: fold a
# removal candidate into place; marks REMOVED_ANY when something left.
apply_remove_candidate() {
    local client="$1" file="$2" status="$3" cand="$4" what="$5"
    if [[ "$status" == "same" ]]; then
        say "${client}: nothing to remove in ${file}"
        return 0
    fi
    if (( DRY_RUN )); then
        say "dry-run: would remove ${what} from ${file}"
        return 0
    fi
    publish_candidate "$file" "$cand" || return 1
    say "${client}: removed ${what} from ${file}"
    REMOVED_ANY=1
    return 0
}

# remove_json_client <client> <file>: drop the client's own hostexec entry
# (backup first). No token needed -- removal names the key, not the value.
remove_json_client() {
    local client="$1" file="$2"
    local cand rc=0 status=""
    cand="$(new_tmp)"
    status="$(json_candidate remove "$file" "$client" "" "" "$cand")" || rc=$?
    if (( rc != 0 )); then
        err "install: ${client}: config helper failed (exit ${rc})"
        return 1
    fi
    apply_remove_candidate "$client" "$file" "$status" "$cand" "hostexec entry" || return 1
    return 0
}

remove_openhands() {
    remove_json_client openhands "$HOME/.openhands/settings.json" || return 1
}

remove_claude() {
    remove_json_client claude "$HOME/.claude.json" || return 1
}

remove_codex() {
    local file cand rc=0 status=""
    file="$HOME/.codex/config.toml"
    cand="$(new_tmp)"
    status="$(toml_candidate remove "$file" "" "$cand")" || rc=$?
    if (( rc != 0 )); then
        err "install: codex: config helper failed (exit ${rc})"
        return 1
    fi
    apply_remove_candidate codex "$file" "$status" "$cand" "hostexec section" || return 1
    return 0
}

remove_opencode() {
    local file
    file="$HOME/.config/opencode/opencode.json"
    if [[ ! -e "$file" && -e "$HOME/.config/opencode/opencode.jsonc" ]]; then
        file="$HOME/.config/opencode/opencode.jsonc"
    fi
    remove_json_client opencode "$file" || return 1
}

remove_qoder() {
    say "qoder: no local state is written by this driver; nothing to remove here."
    return 0
}

remove_unit() {
    local dest
    dest="$HOME/.config/systemd/user/${UNIT_NAME}"
    if [[ ! -e "$dest" && ! -L "$dest" ]]; then
        say "unit: nothing to remove: ${dest}"
        return 0
    fi
    if (( DRY_RUN )); then
        say "dry-run: would remove unit: ${dest}"
        return 0
    fi
    backup_file "$dest" || return 1
    rm -f "$dest"
    say "unit: removed: ${dest}"
    REMOVED_ANY=1
    return 0
}

remove_client() {
    case "$1" in
        openhands) remove_openhands ;;
        claude) remove_claude ;;
        codex) remove_codex ;;
        opencode) remove_opencode ;;
        qoder) remove_qoder ;;
        *) err "install: unknown client: $1"; return 2 ;;
    esac
}

# unregister_all: remove only what this driver writes (backup first).
# Scoped to --clients when given, else all five clients. A second run
# reports "nothing to remove".
unregister_all() {
    local fail=0 name targets=()
    if (( ${#REQUESTED[@]} > 0 )); then
        targets=("${REQUESTED[@]}")
    else
        read -ra targets <<< "$KNOWN_CLIENTS"
    fi
    if ! remove_unit; then
        fail=1
    fi
    for name in "${targets[@]}"; do
        if ! remove_client "$name"; then
            fail=1
        fi
    done
    if (( fail != 0 )); then
        return 1
    fi
    if (( REMOVED_ANY )); then
        say "unregister: done."
    else
        say "unregister: nothing to remove."
    fi
    return 0
}

main() {
    local name fail=0
    parse_args "$@" || return 2
    if [[ -n "${AUTOOS_EXEC_PORT:-}" ]]; then
        PORT="${AUTOOS_EXEC_PORT}"
    else
        PORT="$(default_port)"
    fi
    if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
        err "install: refusing: bad port: ${PORT}"
        return 2
    fi
    REQUESTED=()
    if [[ -n "$CLIENTS" ]]; then
        local IFS=',' entry
        read -ra REQUESTED <<< "$CLIENTS"
        for entry in "${REQUESTED[@]}"; do
            if ! is_known_client "$entry"; then
                err "install: unknown client: ${entry} (known: ${KNOWN_CLIENTS// /, })"
                return 2
            fi
        done
    fi
    if (( UNREGISTER )); then
        unregister_all || return $?
        return 0
    fi
    if ! install_unit; then
        fail=1
    fi
    if (( UNIT_ONLY )); then
        if (( ${#REQUESTED[@]} > 0 )); then
            say "install: note: --unit given; ignoring --clients"
        fi
    else
        if (( ${#REQUESTED[@]} > 0 )); then
            for name in "${REQUESTED[@]}"; do
                if ! wire_client "$name"; then
                    fail=1
                fi
            done
        fi
    fi
    return "$fail"
}

main "$@"
