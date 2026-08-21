#!/usr/bin/env bash
# AutoOS provider dispatch and post-install steps for Linux.
#
# Every installer is idempotent and honours AUTOOS_DRY_RUN. Nothing here asks a
# question: by the time execution starts, every answer has been collected.
#
# shellcheck shell=bash
# shellcheck disable=SC2034
#   The SYS_*/CAT_*/MENU_*/*_STATE globals below are this module's public
#   interface - they are read by setup.sh, serve.py's probe and the tests,
#   none of which shellcheck can see from here.


AUTOOS_DRY_RUN="${AUTOOS_DRY_RUN:-0}"
declare -A AUTOOS_ANSWERS=()
APT_UPDATED=0

answer() {  # answer <key> [default]
    local key="$1" default="${2:-}"
    if [[ -v AUTOOS_ANSWERS[$key] ]]; then printf '%s' "${AUTOOS_ANSWERS[$key]}"
    else printf '%s' "$default"; fi
}

run() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would run: $*"
        return 0
    fi
    ui_muted "run: $*"
    "$@"
}

# ─── Idempotent file editing ────────────────────────────────────────────────
append_line_once() {
    # append_line_once <file> <marker> <line...>
    local file="$1" marker="$2"; shift 2
    local content="$*"
    if [[ -f "$file" ]] && grep -qF -- "$marker" "$file" 2>/dev/null; then
        ui_muted "already configured (${marker}) in ${file}"
        return 0
    fi
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would append '${marker}' to ${file}"
        return 0
    fi
    mkdir -p "$(dirname "$file")"
    # Never modify a user's file without a copy of the original.
    [[ -f "$file" ]] && cp "$file" "${file}.autoos-backup-$(date +%Y%m%d-%H%M%S)"
    printf '\n# added by AutoOS\n%s\n' "$content" >>"$file"
    ui_ok "updated ${file}"
}

apt_update_once() {
    (( APT_UPDATED )) && return 0
    run $AUTOOS_SUDO apt-get update -y
    APT_UPDATED=1
}

# ─── Idempotency checks ─────────────────────────────────────────────────────
is_installed() {
    local provider="$1" package="$2" pkg
    case "$provider" in
        apt)
            for pkg in $package; do
                dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "^install ok installed" || return 1
            done
            return 0 ;;
        snap)   snap list "$package" >/dev/null 2>&1 ;;
        brew)
            for pkg in $package; do
                brew list --versions "$pkg" >/dev/null 2>&1 || return 1
            done
            return 0 ;;
        npm)    npm ls -g --depth=0 2>/dev/null | grep -q -- "$package" ;;
        script) script_is_installed "$package" ;;
        *)      return 1 ;;
    esac
}

script_is_installed() {
    case "$1" in
        oh-my-zsh)       [[ -d "$SYS_HOME/.oh-my-zsh" ]] ;;
        zsh-plugins)     [[ -d "$SYS_HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]] ;;
        powerlevel10k)   [[ -d "$SYS_HOME/.oh-my-zsh/custom/themes/powerlevel10k" ]] ;;
        meslo-nerd-font) [[ -f "$SYS_HOME/.local/share/fonts/MesloLGS NF Regular.ttf" ]] ;;
        nodesource-lts)  has_cmd node ;;
        docker)          has_cmd docker ;;
        tailscale)       has_cmd tailscale ;;
        antigravity)     has_cmd antigravity ;;
        xpipe)           has_cmd xpipe ;;
        herdr)           has_cmd herdr ;;
        handy)           has_cmd handy || [[ -x /usr/bin/handy ]] ;;
        vscode)          has_cmd code ;;
        *)               return 1 ;;
    esac
}

# ─── Provider dispatch ──────────────────────────────────────────────────────
# install_component <provider> <package>
# Result lands in INSTALL_STATE (installed|skipped|failed) rather than on stdout:
# these functions also print progress, so a `$(...)` capture would swallow the UI
# output into the status string and match none of the cases.
INSTALL_STATE=""
install_component() {
    local provider="$1" package="$2" CASK_FLAG="${3:-0}"
    INSTALL_STATE="failed"

    if is_installed "$provider" "$package"; then
        ui_muted "${package} is already installed"
        INSTALL_STATE="skipped"; return 0
    fi

    local rc=0
    case "$provider" in
        apt)
            apt_update_once
            # shellcheck disable=SC2086  # package may legitimately be several names
            run $AUTOOS_SUDO apt-get install -y $package || rc=$?
            ;;
        snap)   run $AUTOOS_SUDO snap install "$package" || rc=$? ;;
        brew)
            # Homebrew must never run under sudo - it refuses, and on the rare
            # setup where it does not, it leaves a root-owned prefix behind.
            # shellcheck disable=SC2086  # package may be several formulae
            if [[ "$CASK_FLAG" == "1" ]]; then
                run brew install --cask $package || rc=$?
            else
                run brew install $package || rc=$?
            fi
            ;;
        npm)    run npm install -g "$package" || rc=$? ;;
        script) install_script "$package" || rc=$? ;;
        custom) rc=0 ;;   # handled entirely by postInstall
        *)      ui_err "unknown provider '${provider}'"; rc=1 ;;
    esac

    if (( rc != 0 )); then INSTALL_STATE="failed"; else INSTALL_STATE="installed"; fi
    return 0
}

install_script() {
    case "$1" in
        oh-my-zsh)       install_oh_my_zsh ;;
        zsh-plugins)     install_zsh_plugins ;;
        powerlevel10k)   install_powerlevel10k ;;
        meslo-nerd-font) install_meslo_font ;;
        nodesource-lts)  install_nodejs ;;
        docker)          install_docker ;;
        tailscale)       install_tailscale ;;
        antigravity)     install_antigravity ;;
        xpipe)           install_xpipe ;;
        herdr)           install_herdr ;;
        handy)           install_handy ;;
        vscode)          install_vscode ;;
        *) ui_err "no script installer for '$1'"; return 1 ;;
    esac
}

# ─── Script installers ──────────────────────────────────────────────────────
clone_or_update() {
    local url="$1" dest="$2" depth="${3:-}"
    if [[ -d "$dest/.git" ]]; then
        run git -C "$dest" pull --ff-only
    elif [[ -d "$dest" ]]; then
        ui_muted "${dest} exists but is not a git checkout - leaving it alone"
    else
        if [[ -n "$depth" ]]; then run git clone --depth "$depth" "$url" "$dest"
        else run git clone "$url" "$dest"; fi
    fi
}

install_oh_my_zsh() {
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install oh-my-zsh into $SYS_HOME/.oh-my-zsh"; return 0; fi
    # RUNZSH=no keeps the installer from exec'ing a shell and swallowing the
    # rest of this script - the bug that stopped the old install.sh halfway.
    RUNZSH=no CHSH=no KEEP_ZSHRC=yes \
        sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
}

install_zsh_plugins() {
    local custom="$SYS_HOME/.oh-my-zsh/custom/plugins"
    clone_or_update https://github.com/zsh-users/zsh-autosuggestions      "$custom/zsh-autosuggestions" 1
    clone_or_update https://github.com/zsh-users/zsh-syntax-highlighting  "$custom/zsh-syntax-highlighting" 1
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:plugins" \
        'plugins=(git z zsh-autosuggestions zsh-syntax-highlighting colored-man-pages)  # AutoOS:plugins'
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:history" \
        'HISTSIZE=500000  # AutoOS:history'$'\n''SAVEHIST=100000'
}

install_powerlevel10k() {
    clone_or_update https://github.com/romkatv/powerlevel10k.git \
        "$SYS_HOME/.oh-my-zsh/custom/themes/powerlevel10k" 1
    append_line_once "$SYS_HOME/.zshrc" "AutoOS:theme" \
        'ZSH_THEME="powerlevel10k/powerlevel10k"  # AutoOS:theme'
}

install_meslo_font() {
    local dir="$SYS_HOME/.local/share/fonts"
    local target="$dir/MesloLGS NF Regular.ttf"
    [[ -f "$target" ]] && { ui_muted "font already installed"; return 0; }
    if (( AUTOOS_DRY_RUN )); then ui_muted "would download MesloLGS NF to $dir"; return 0; fi
    mkdir -p "$dir"
    curl -fsSL -o "$target" \
        'https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Regular.ttf'
    has_cmd fc-cache && fc-cache -f >/dev/null 2>&1
    ui_ok "installed MesloLGS NF"
}

install_nodejs() {
    if (( AUTOOS_DRY_RUN )); then ui_muted "would add the NodeSource LTS repo and install nodejs"; return 0; fi
    curl -fsSL https://deb.nodesource.com/setup_lts.x | $AUTOOS_SUDO -E bash -
    $AUTOOS_SUDO apt-get install -y nodejs
}

install_docker() {
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install Docker via get.docker.com"; return 0; fi
    local tmp; tmp="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$tmp"
    $AUTOOS_SUDO sh "$tmp"
    rm -f "$tmp"
}

install_tailscale() {
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install Tailscale via tailscale.com/install.sh"; return 0; fi
    curl -fsSL https://tailscale.com/install.sh | $AUTOOS_SUDO sh
    ui_info "Run '${AUTOOS_SUDO} tailscale up' to authenticate this machine."
}

install_antigravity() {
    local url; url="$(answer antigravity_url '')"
    if [[ -z "$url" ]]; then
        ui_warn "No Antigravity download URL given - skipping."
        ui_muted "    Re-run and answer the Antigravity question, or use --only to skip it."
        return 0
    fi
    if (( AUTOOS_DRY_RUN )); then ui_muted "would download and install Antigravity from $url"; return 0; fi
    local tmp; tmp="$(mktemp --suffix=.deb)"
    curl -fsSL -o "$tmp" "$url"
    $AUTOOS_SUDO apt-get install -y "$tmp"
    rm -f "$tmp"
}

install_xpipe() {
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install XPipe via get-xpipe.sh"; return 0; fi
    # The upstream one-liner uses bash process substitution, which fails under sh.
    local tmp; tmp="$(mktemp)"
    curl -fsSL -o "$tmp" https://github.com/xpipe-io/xpipe/raw/master/get-xpipe.sh
    bash "$tmp"
    rm -f "$tmp"
}

install_vscode() {
    # Microsoft's own documented route: their signed apt repository, so `apt
    # upgrade` keeps it current instead of it going stale as a one-off .deb.
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would add Microsoft's signed apt repo and install code"
        return 0
    fi
    local key=/etc/apt/keyrings/packages.microsoft.gpg
    if [[ ! -f "$key" ]]; then
        local tmp; tmp="$(mktemp)"
        curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor >"$tmp"
        $AUTOOS_SUDO install -D -o root -g root -m 644 "$tmp" "$key"
        rm -f "$tmp"
    fi
    if [[ ! -f /etc/apt/sources.list.d/vscode.list ]]; then
        printf 'deb [arch=amd64,arm64,armhf signed-by=%s] https://packages.microsoft.com/repos/code stable main\n' \
            "$key" | $AUTOOS_SUDO tee /etc/apt/sources.list.d/vscode.list >/dev/null
        APT_UPDATED=0   # the new repo has to be fetched before install
    fi
    apt_update_once
    run $AUTOOS_SUDO apt-get install -y code
}

install_handy() {
    # No apt repository exists, so take the .deb the project publishes. The asset
    # name is resolved from the release API rather than pinned, so this does not
    # go stale, and python3 parses it because a grep over JSON is a trap.
    local suffix
    case "$SYS_ARCH" in
        x64)   suffix="amd64" ;;
        arm64) suffix="arm64" ;;
        *) ui_warn "Handy publishes no .deb for ${SYS_ARCH} - skipping."; return 0 ;;
    esac
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install the latest Handy ${suffix}.deb from github.com/cjpais/Handy"
        return 0
    fi
    catalog_require_python || return 1
    local url
    url="$(curl -fsSL https://api.github.com/repos/cjpais/Handy/releases/latest 2>/dev/null |
           python3 -c "
import json, sys
suffix = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for asset in data.get('assets', []):
    if asset['name'].endswith('_' + suffix + '.deb'):
        print(asset['browser_download_url'])
        break
" "$suffix")"
    if [[ -z "$url" ]]; then
        ui_warn "Could not find a Handy .deb for ${suffix} in the latest release."
        return 1
    fi
    local tmp; tmp="$(mktemp --suffix=.deb)"
    curl -fsSL -o "$tmp" "$url"
    run $AUTOOS_SUDO apt-get install -y "$tmp"
    rm -f "$tmp"
}

install_herdr() {
    local source; source="$(answer herdr_source 'npm:herdr')"
    case "$source" in
        npm:*)  run npm install -g "${source#npm:}" ;;
        git:*)  clone_or_update "${source#git:}" "$SYS_HOME/.herdr" 1 ;;
        http*)  if (( AUTOOS_DRY_RUN )); then ui_muted "would download Herdr from $source";
                else local t; t="$(mktemp)"; curl -fsSL -o "$t" "$source"; bash "$t"; rm -f "$t"; fi ;;
        *)      ui_warn "Unrecognised Herdr source '$source' - skipping."; return 0 ;;
    esac
}

# ─── Post-install steps ─────────────────────────────────────────────────────
install_fastfetch() {
    # neofetch was archived upstream in 2024; fastfetch is the maintained successor.
    if is_installed apt fastfetch; then ui_muted "fastfetch already installed"; return 0; fi
    apt_update_once
    run $AUTOOS_SUDO apt-get install -y fastfetch || \
        ui_warn "fastfetch is not in this release's repositories - skipping."
}

add_user_to_docker_group() {
    if (( AUTOOS_DRY_RUN )); then ui_muted "would add ${SYS_USER} to the docker group"; return 0; fi
    if id -nG "$SYS_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        ui_muted "${SYS_USER} is already in the docker group"; return 0
    fi
    $AUTOOS_SUDO groupadd -f docker
    $AUTOOS_SUDO usermod -aG docker "$SYS_USER"
    ui_ok "added ${SYS_USER} to the docker group"
    ui_info "Log out and back in for the group change to take effect."
}

install_lazyvim() {
    local dest="$SYS_HOME/.config/nvim"
    if [[ -e "$dest" ]]; then ui_muted "nvim config already exists - leaving it alone"; return 0; fi
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install the LazyVim starter into $dest"; return 0; fi
    git clone --depth 1 https://github.com/LazyVim/starter "$dest"
    rm -rf "$dest/.git"
    ui_ok "LazyVim starter installed"
}

install_agent_skills() {
    local code_root="$SYS_HOME/Documents/Code"
    local dest="$code_root/agent-skills"
    (( AUTOOS_DRY_RUN )) || mkdir -p "$code_root"
    clone_or_update https://github.com/Ch3fUlrich/agent-skills.git "$dest"

    local omni base
    omni="$(answer omnigraph_url '')"
    if [[ -z "$omni" ]]; then base="http://localhost:8080"; else base="${omni%/}"; fi
    ui_info "Omnigraph base URL: ${base}"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would write ${SYS_HOME}/.autoos-omnigraph.env"
        return 0
    fi
    printf 'OMNIGRAPH_BASE_URL=%s\n' "$base" >"$SYS_HOME/.autoos-omnigraph.env"
    ui_ok "Omnigraph URL saved to ${SYS_HOME}/.autoos-omnigraph.env"

    # Must not be the bare `[[ ]] && ...` form: as the last statement of a
    # function that returns 1, `set -e` would abort the whole run.
    local setup="$dest/infra/mcp-servers/scripts/linux/init-serena-projects.sh"
    if [[ -f "$setup" ]]; then
        ui_muted "    MCP helper scripts: ${dest}/infra/mcp-servers/scripts/linux/"
    fi
    return 0
}

run_post_install() {
    local fn="$1"
    [[ -z "$fn" ]] && return 0
    if ! declare -F "$fn" >/dev/null; then
        ui_warn "post-install '${fn}' not found"
        return 0
    fi
    ui_step "post-install: ${fn}"
    "$fn"
}

# ─── Post-install verification ──────────────────────────────────────────────
# A package manager reporting success is not proof the thing actually works: a
# binary can land outside PATH, or a shim can be created without its runtime.
# `verify` in the catalog is the command that proves it.
AUTOOS_VERIFY=1
# Result lands in VERIFY_STATE, not on stdout: this function also prints progress,
# so a $(...) capture would swallow the UI output into the status string.
VERIFY_STATE="unchecked"

verify_component() {
    local cmd="$1" name="$2"
    VERIFY_STATE="unchecked"
    [[ -z "$cmd" ]] && return 0
    (( AUTOOS_VERIFY )) || return 0
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would verify: $cmd"
        return 0
    fi
    # A fresh install often lands in a directory this shell has not picked up.
    local probe_path="$PATH:/usr/local/bin:/usr/bin:/snap/bin:$SYS_HOME/.local/bin"
    if PATH="$probe_path" bash -lc "$cmd" >/dev/null 2>&1; then
        ui_ok "verified: $name"
        VERIFY_STATE="verified"
    else
        ui_warn "$name installed but '$cmd' did not succeed - it may need a new login."
        VERIFY_STATE="unverified"
    fi
    return 0
}

# ─── Run state: save and replay ─────────────────────────────────────────────
# Re-imaging a machine should not mean re-choosing 30 checkboxes.
autoos_state_save() {
    local path="$1" profile="$2" selected="$3" installed="$4" skipped="$5" failed="$6"
    (( AUTOOS_DRY_RUN )) && { ui_muted "would save run state to $path"; return 0; }
    catalog_require_python || return 0
    local answers_json="{}"
    if (( ${#AUTOOS_ANSWERS[@]} )); then
        answers_json="$(
            for k in "${!AUTOOS_ANSWERS[@]}"; do printf '%s\x1f%s\n' "$k" "${AUTOOS_ANSWERS[$k]}"; done |
            python3 -c "
import json,sys
print(json.dumps({l.split(chr(31),1)[0]: l.split(chr(31),1)[1]
                  for l in sys.stdin.read().splitlines() if chr(31) in l}))"
        )"
    fi
    python3 - "$path" "$profile" "$selected" "$installed" "$skipped" "$failed" "$answers_json" <<'PY'
import json, sys, datetime
path, profile, selected, installed, skipped, failed, answers = sys.argv[1:8]
json.dump({
    "version": 1,
    "savedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "platform": "linux",
    "profile": profile,
    "selected": selected.split(),
    "answers": json.loads(answers or "{}"),
    "results": {"installed": installed.split(), "skipped": skipped.split(), "failed": failed.split()},
}, open(path, "w", encoding="utf-8"), indent=2)
PY
    ui_ok "run state saved to $path"
}

# Sets STATE_PROFILE / STATE_SELECTED and fills AUTOOS_ANSWERS.
autoos_state_load() {
    local path="$1"
    [[ -f "$path" ]] || { ui_err "No state file at $path"; return 1; }
    catalog_require_python || return 1
    while IFS=$'\x1f' read -r key value; do
        case "$key" in
            __profile)  STATE_PROFILE="$value" ;;
            __selected) STATE_SELECTED="$value" ;;
            *)          [[ -n "$key" ]] && AUTOOS_ANSWERS["$key"]="$value" ;;
        esac
    done < <(python3 - "$path" <<'PY'
import json, sys
US = chr(31)
d = json.load(open(sys.argv[1], encoding="utf-8"))
print("__profile"  + US + (d.get("profile") or "custom"))
print("__selected" + US + " ".join(d.get("selected") or []))
for k, v in (d.get("answers") or {}).items():
    print(k + US + str(v))
PY
    )
    ui_ok "loaded state from $path (profile: ${STATE_PROFILE:-custom})"
}

# ─── Undo ───────────────────────────────────────────────────────────────────
# Restores files AutoOS backed up. It deliberately does NOT uninstall packages:
# guessing which of a package manager's changes were "ours" is how an undo
# turns into a second incident.
autoos_undo() {
    local assume_yes="${1:-0}"
    local -a originals=()
    local b orig
    while IFS= read -r b; do
        orig="${b%.autoos-backup-*}"
        [[ " ${originals[*]} " == *" $orig "* ]] || originals+=("$orig")
    done < <(find "$SYS_HOME" -maxdepth 4 -name '*.autoos-backup-*' -type f 2>/dev/null | sort)

    if (( ${#originals[@]} == 0 )); then
        ui_info "Nothing to undo - AutoOS has not backed up any file on this machine."
        return 0
    fi

    ui_section "Files AutoOS can restore"
    local -a newest=()
    for orig in "${originals[@]}"; do
        b="$(find "$(dirname "$orig")" -maxdepth 1 -name "$(basename "$orig").autoos-backup-*" -type f 2>/dev/null | sort | tail -1)"
        newest+=("$b")
        printf '  %-52s <- %s\n' "$orig" "$(basename "$b")"
    done
    ui_muted "Installed packages are NOT removed - only these files are restored."

    if (( AUTOOS_DRY_RUN )); then ui_warn "DRY RUN - nothing restored."; return 0; fi
    if (( ! assume_yes )); then
        if ! ui_confirm "Restore ${#originals[@]} file(s) from backup?" n; then
            ui_warn "Cancelled - nothing was changed."
            return 0
        fi
    fi
    local i
    for i in "${!originals[@]}"; do
        cp "${newest[i]}" "${originals[i]}"
        ui_ok "restored ${originals[i]}"
    done
}
