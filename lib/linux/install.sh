#!/usr/bin/env bash
# AutoOS provider dispatch and post-install steps for Linux.
#
# Every installer is idempotent and honours AUTOOS_DRY_RUN. Nothing here asks a
# question: by the time execution starts, every answer has been collected.
#
# shellcheck shell=bash

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
    local provider="$1" package="$2"
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
