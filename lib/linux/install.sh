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
    python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/process.py" "$@"
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
    detect_installed_status "$1" "$2" "${3:-0}"
    [[ "$INSTALLED_STATUS" == installed ]]
}

custom_is_installed() {
    case "$1" in
        git-config)
            has_cmd git && [[ -n "$(git config --global user.name 2>/dev/null || true)" ]]
            ;;
        wsl-agent-home)
            [[ -d "$SYS_HOME/.cao" && -n "$(ls -A "$SYS_HOME/.cao" 2>/dev/null || true)" ]]
            ;;
        agent-skills)
            [[ -d "$SYS_HOME/Documents/Code/agent-skills" || -d "$SYS_HOME/Documents/code/agent-skills" ]]
            ;;
        mcp-serena)
            mcp_has_server serena || antigravity_has_server serena
            ;;
        mcp-graphify)
            mcp_has_server graphify || antigravity_has_server graphify
            ;;
        mcp-playwright)
            mcp_has_server playwright || antigravity_has_server playwright
            ;;
        mcp-context7)
            mcp_has_server context7 || antigravity_has_server context7
            ;;
        # local-ai (B21) models — pulled via `ollama pull`, so "installed"
        # means "ollama list already names this tag", not a package-manager
        # record.
        qwen3:4b | qwen3:1.7b | qwen2.5-coder:7b)
            has_cmd ollama && ollama list 2>/dev/null | grep -qF -- "$1"
            ;;
        oterm)
            has_cmd oterm
            ;;
        *) return 1 ;;
    esac
}

# ─── Catalog installation probe ─────────────────────────────────────────────
declare -a CAT_INSTALLED=()

catalog_probe_installed() {
    CAT_INSTALLED=()
    local i
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        if is_installed "${CAT_PROVIDER[i]}" "${CAT_PACKAGE[i]}" "${CAT_CASK[i]:-0}"; then
            CAT_INSTALLED+=(1)
        else
            CAT_INSTALLED+=(0)
        fi
    done
}

catalog_installed_names() {
    local i
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        if (( ${CAT_INSTALLED[i]:-0} )); then
            printf '%s\n' "${CAT_NAME[i]}"
        fi
    done
}

catalog_installed_ids() {
    local i ids=""
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        if (( ${CAT_INSTALLED[i]:-0} )); then
            ids+="${CAT_ID[i]} "
        fi
    done
    printf '%s' "${ids% }"
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

    if is_installed "$provider" "$package" "$CASK_FLAG"; then
        ui_ok "✓ ${package} is already installed - skipping package"
        INSTALL_STATE="skipped"; return 0
    fi

    local rc=0
    case "$provider" in
        manual) ui_warn "Action required: use the vendor download link in the plan."; INSTALL_STATE=manual; return 0 ;;
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
    if (( rc == 124 )); then return 124; fi
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
        agy)             install_agy ;;
        gh)              install_gh ;;
        uv)              install_uv ;;
        ollama)          install_ollama ;;
        claude-autostart) install_claude_autostart ;;
        google-chrome)   install_google_chrome ;;
        bitwarden-chrome) install_bitwarden_chrome ;;
        zed)             install_zed ;;
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
    local tmp; tmp="$(mktemp)"
    local url="https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/76ac9fcddc4e93c15dc778b4c6234755ad714e5a/tools/install.sh"
    local expected_hash="5574b96e94dbcb769f0d1592fa83aeb6ca2caf41c6ae5d76fcc7f04c524b4f55"

    if ! curl -fsSL "$url" -o "$tmp"; then
        ui_err "failed to download oh-my-zsh installer"
        rm -f "$tmp"
        return 1
    fi

    local actual_hash=""
    if has_cmd sha256sum; then
        actual_hash="$(sha256sum "$tmp" | awk '{print $1}')"
    elif has_cmd shasum; then
        actual_hash="$(shasum -a 256 "$tmp" | awk '{print $1}')"
    fi

    if [[ -n "$actual_hash" && "$actual_hash" != "$expected_hash" ]]; then
        ui_err "oh-my-zsh installer checksum mismatch"
        rm -f "$tmp"
        return 1
    fi

    # RUNZSH=no keeps the installer from exec'ing a shell and swallowing the
    # rest of this script.
    RUNZSH=no CHSH=no KEEP_ZSHRC=yes sh "$tmp" --unattended
    rm -f "$tmp"
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
    local tmp; tmp="$(mktemp)"
    curl -fsSL https://deb.nodesource.com/setup_lts.x -o "$tmp"
    $AUTOOS_SUDO -E bash "$tmp"
    rm -f "$tmp"
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
    local tmp; tmp="$(mktemp)"
    curl -fsSL https://tailscale.com/install.sh -o "$tmp"
    $AUTOOS_SUDO sh "$tmp"
    rm -f "$tmp"
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

install_agy() {
    # Google's Antigravity CLI, replacing Gemini CLI at the human partner's
    # direction (Gemini CLI is not deprecated - this is a deliberate product
    # choice, not a response to a broken package).
    #
    # This used to be `curl -fsSL $url | bash` - an unverified remote script
    # piped straight into a shell (A14, the exact finding this branch exists
    # to remove: a pipe can't be inspected and can be swapped mid-stream).
    # Google publishes no checksum for this installer, unlike oh-my-zsh's
    # pinned sha256 in install_oh_my_zsh() above, so a hash can't be verified
    # here either. The minimum acceptable bar instead: download to a file,
    # log the exact URL, verify it is non-empty and actually looks like a
    # script, and only then execute the FILE - never the pipe.
    local url="https://antigravity.google/cli/install.sh"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would download and run the Antigravity CLI installer from $url"
        return 0
    fi
    ui_muted "downloading Antigravity CLI installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -fsSL -o "$tmp" "$url"; then
        ui_err "failed to download Antigravity CLI installer from $url"
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_err "Antigravity CLI installer from $url does not look like a script - aborting"
        rm -f "$tmp"
        return 1
    fi
    local rc=0
    bash "$tmp" || rc=$?
    rm -f "$tmp"
    return $rc
}

install_claude_autostart() {
    local appdir="${AUTOOS_ROOT}/lib/linux"
    local udest="${SYS_HOME}/.config/systemd/user"
    local units=(claude-sessions-snapshot.service claude-sessions-snapshot.timer claude-sessions-restore.service)
    local interval; interval="$(claude_autostart_interval "$appdir")"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install ${#units[@]} systemd user units into $udest"
        ui_muted "would snapshot live sessions every ${interval} minutes and restore them at boot"
        ui_muted "would enable lingering for ${USER:-$(whoami)} so the timer runs before you log in"
        claude_autostart_report_host
        return 0
    fi

    if ! has_cmd systemctl; then
        # macOS reaches lib/linux for everything else, but there is no systemd here
        # and writing unit files nothing will ever read is how the first version
        # reported success on a machine where the feature could not work.
        ui_err "systemd is required for claude-autostart and systemctl was not found"
        return 1
    fi

    mkdir -p "$udest"
    local u src changed=0
    for u in "${units[@]}"; do
        src="${appdir}/systemd/user/${u}"
        if [[ ! -f "$src" ]]; then
            ui_err "missing unit template: $src"
            return 1
        fi
        # Render to a temp file first so an unchanged unit is genuinely untouched:
        # rewriting it would restart the timer on every run of an idempotent script.
        local tmp; tmp="$(mktemp)"
        sed -e "s#@APPDIR@#${appdir}#g" \
            -e "s#@APPROOT@#${AUTOOS_ROOT}#g" \
            -e "s#@INTERVAL@#${interval}#g" "$src" > "$tmp"
        if [[ -f "${udest}/${u}" ]] && cmp -s "$tmp" "${udest}/${u}"; then
            ui_info "$u is already current"
            rm -f "$tmp"
        else
            mv -f "$tmp" "${udest}/${u}"
            ui_ok "installed $u"
            changed=1
        fi
    done

    if (( changed )); then
        systemctl --user daemon-reload && ui_ok "reloaded the user unit files" \
            || ui_warn "systemctl --user daemon-reload failed — is there a user manager on this session?"
    fi

    if systemctl --user enable claude-sessions-restore.service >/dev/null 2>&1; then
        ui_ok "enabled claude-sessions-restore.service (runs at boot)"
    else
        ui_warn "could not enable claude-sessions-restore.service"
    fi
    if systemctl --user enable --now claude-sessions-snapshot.timer >/dev/null 2>&1; then
        ui_ok "enabled claude-sessions-snapshot.timer (every ${interval} minutes)"
    else
        ui_warn "could not enable claude-sessions-snapshot.timer"
    fi

    claude_autostart_enable_linger
    claude_autostart_report_host
}

# Without lingering, the user manager only exists while somebody is logged in, so
# the timer never runs at boot and there is nothing to restore from. Enabling it
# is a system-level change, so it is named before it happens and the exact command
# is printed when we cannot make it ourselves.
claude_autostart_enable_linger() {
    has_cmd loginctl || { ui_warn "loginctl not found — cannot enable lingering"; return 0; }
    local user_name="${USER:-$(whoami)}"

    if [[ "$(loginctl show-user "$user_name" -p Linger --value 2>/dev/null || true)" == "yes" ]]; then
        ui_info "lingering is already enabled for $user_name"
        return 0
    fi
    if [[ -z "$AUTOOS_SUDO" ]] && [[ "$(id -u)" != "0" ]]; then
        ui_warn "lingering is off: the snapshot timer will not run until you log in"
        ui_info "enable it with: sudo loginctl enable-linger $user_name"
        return 0
    fi

    ui_step "enabling lingering for $user_name (lets the timer run without a login)"
    if $AUTOOS_SUDO loginctl enable-linger "$user_name"; then
        ui_ok "lingering enabled for $user_name"
    else
        ui_warn "could not enable lingering — run: sudo loginctl enable-linger $user_name"
    fi
}

# A restore with nowhere to draw does nothing (ADR 0002). Say so at install time
# rather than letting the user discover it after a reboot.
claude_autostart_report_host() {
    if has_cmd herdr || has_cmd tmux; then
        ui_info "terminal host for restored sessions: $(has_cmd herdr && echo herdr || echo tmux)"
    else
        ui_warn "neither herdr nor tmux is installed — restore will refuse to start a session it cannot show you"
        ui_info "install one of them, or set claude_autostart.terminal_host in autoos.config.json"
    fi
}

claude_autostart_interval() {
    local appdir="$1" value
    value="$(AUTOOS_CONFIG_FILE="${AUTOOS_CONFIG_FILE:-$AUTOOS_ROOT/autoos.config.json}" \
             python3 "$appdir/claude_sessions.py" config 2>/dev/null |
             sed -n "s/^AUTOOS_CLAUDE_SNAPSHOT_INTERVAL_MINS='\(.*\)'$/\1/p")"
    [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= 59 )) || value=5
    printf '%s' "$value"
}

install_google_chrome() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would add Google's signed apt repo and install google-chrome-stable"
        return 0
    fi
    local key=/etc/apt/keyrings/google-chrome.gpg
    if [[ ! -f "$key" ]]; then
        local tmp; tmp="$(mktemp)"
        curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor >"$tmp"
        $AUTOOS_SUDO install -D -o root -g root -m 644 "$tmp" "$key"
        rm -f "$tmp"
    fi
    if [[ ! -f /etc/apt/sources.list.d/google-chrome.list ]]; then
        printf 'deb [arch=amd64 signed-by=%s] http://dl.google.com/linux/chrome/deb/ stable main\n' \
            "$key" | $AUTOOS_SUDO tee /etc/apt/sources.list.d/google-chrome.list >/dev/null
        APT_UPDATED=0
    fi
    apt_update_once
    run $AUTOOS_SUDO apt-get install -y google-chrome-stable
}

install_bitwarden_chrome() {
    local ext_id="nngceckbapebfimnlniiiahkandclblb"
    local update_url="https://clients2.google.com/service/update2/crx"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would configure Bitwarden extension (${ext_id}) for Chrome"
        return 0
    fi
    local dirs=(
        "/opt/google/chrome/extensions"
        "/usr/share/google-chrome/extensions"
    )
    if has_cmd chromium || has_cmd chromium-browser || [[ -d /snap/chromium ]]; then
        dirs+=("/usr/share/chromium/extensions")
    fi
    for d in "${dirs[@]}"; do
        $AUTOOS_SUDO mkdir -p "$d"
        printf '{\n  "external_update_url": "%s"\n}\n' "$update_url" | \
            $AUTOOS_SUDO tee "$d/${ext_id}.json" >/dev/null
        $AUTOOS_SUDO chmod 644 "$d/${ext_id}.json"
    done

    local policy_dir="/etc/opt/chrome/policies/managed"
    $AUTOOS_SUDO mkdir -p "$policy_dir"
    printf '{\n  "ExtensionInstallForcelist": [\n    "%s;%s"\n  ]\n}\n' "$ext_id" "$update_url" | \
        $AUTOOS_SUDO tee "$policy_dir/bitwarden.json" >/dev/null
    $AUTOOS_SUDO chmod 644 "$policy_dir/bitwarden.json"

    if [[ -d /var/snap/chromium/current ]]; then
        local snap_policy="/var/snap/chromium/current/policies/managed"
        $AUTOOS_SUDO mkdir -p "$snap_policy"
        printf '{\n  "ExtensionInstallForcelist": [\n    "%s;%s"\n  ]\n}\n' "$ext_id" "$update_url" | \
            $AUTOOS_SUDO tee "$snap_policy/bitwarden.json" >/dev/null
        $AUTOOS_SUDO chmod 644 "$snap_policy/bitwarden.json"
    fi
    ui_ok "configured Bitwarden Chrome extension"
}

install_gh() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would add GitHub CLI apt repo and install gh"
        return 0
    fi
    if has_cmd apt-get; then
        local key=/etc/apt/keyrings/githubcli-archive-keyring.gpg
        $AUTOOS_SUDO mkdir -p -m 755 /etc/apt/keyrings
        if [[ ! -f "$key" ]]; then
            local tmp; tmp="$(mktemp)"
            curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg >"$tmp"
            $AUTOOS_SUDO install -D -o root -g root -m 644 "$tmp" "$key"
            rm -f "$tmp"
        fi
        local arch; arch="$(dpkg --print-architecture)"
        if [[ ! -f /etc/apt/sources.list.d/github-cli.list ]]; then
            printf 'deb [arch=%s signed-by=%s] https://cli.github.com/packages stable main\n' \
                "$arch" "$key" | $AUTOOS_SUDO tee /etc/apt/sources.list.d/github-cli.list >/dev/null
            APT_UPDATED=0
        fi
        apt_update_once
        run $AUTOOS_SUDO apt-get install -y gh
    elif has_cmd brew; then
        run brew install gh
    else
        ui_err "no supported package manager for gh"
        return 1
    fi
}

install_uv() {
    # Found alongside the install_ollama fix while adding the A14 regression
    # guard below: this had the identical unverified pipe-to-shell pattern
    # (curl ... | sh) that this branch exists to remove — a pipe can't be
    # inspected before it runs and can be swapped mid-stream; a downloaded
    # file can be both. Astral publishes no checksum for this installer,
    # unlike oh-my-zsh's pinned sha256 in install_oh_my_zsh() above, so a
    # hash can't be verified here either. Same minimum bar as install_agy()
    # and install_ollama(): download to a file, log the exact URL, verify it
    # is non-empty and actually looks like a script, and only then execute
    # the FILE - never the pipe.
    local url="https://astral.sh/uv/install.sh"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would download and run the uv installer from $url"
        return 0
    fi
    ui_muted "downloading uv installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -LsSf -o "$tmp" "$url"; then
        ui_err "failed to download uv installer from $url"
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_err "uv installer from $url does not look like a script - aborting"
        rm -f "$tmp"
        return 1
    fi
    local rc=0
    sh "$tmp" || rc=$?
    rm -f "$tmp"
    return $rc
}

install_ollama() {
    # This used to be `curl -fsSL $url | sh` - an unverified remote script
    # piped straight into a shell (A14, the exact finding this branch exists
    # to remove: a pipe can't be inspected and can be swapped mid-stream).
    # Ollama publishes no checksum for this installer, unlike oh-my-zsh's
    # pinned sha256 in install_oh_my_zsh() above, so a hash can't be verified
    # here either. The minimum acceptable bar instead: download to a file,
    # log the exact URL, verify it is non-empty and actually looks like a
    # script, and only then execute the FILE - never the pipe.
    local url="https://ollama.com/install.sh"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would download and run the Ollama installer from $url"
        return 0
    fi
    ui_muted "downloading Ollama installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -fsSL -o "$tmp" "$url"; then
        ui_err "failed to download Ollama installer from $url"
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_err "Ollama installer from $url does not look like a script - aborting"
        rm -f "$tmp"
        return 1
    fi
    local rc=0
    sh "$tmp" || rc=$?
    rm -f "$tmp"
    return $rc
}

setup_ollama_models() {
    local models
    models="$(answer ollama_models 'nomic-embed-text')"
    [[ -z "$models" || "$models" == "none" ]] && return 0
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would pull ollama model(s): ${models}"
        return 0
    fi
    if ! has_cmd ollama; then
        ui_warn "ollama command not found; skipping model download"
        return 0
    fi
    local m
    for m in $models; do
        ui_step "pulling Ollama model: $m"
        ollama pull "$m" || ui_warn "failed to pull ollama model: $m"
    done
}

# ─── local-ai profile (B21) ─────────────────────────────────────────────────
# One catalog component per model, each `requires: ["ollama"]` and each with
# its own postInstall — a postInstall is invoked with no arguments (see
# run_post_install), so a shared implementation needs one thin wrapper per
# model rather than one function parameterised by catalog data.
#
# `ollama list` is checked first so an already-pulled model reports nothing
# new to do here too — belt and braces alongside custom_is_installed's own
# pre-check below, since that pre-check also gates whether postInstall runs
# at all only for the "skipped" path, not the "installed" one.
pull_ollama_model() {
    local model="$1"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would pull ollama model: $model"
        return 0
    fi
    if ! has_cmd ollama; then
        ui_warn "ollama command not found; skipping model pull for $model"
        return 0
    fi
    if ollama list 2>/dev/null | grep -qF -- "$model"; then
        ui_muted "ollama model $model already pulled"
        return 0
    fi
    ui_step "pulling Ollama model: $model"
    ollama pull "$model" || ui_warn "failed to pull ollama model: $model"
}

# qwen3:4b is the pre-ticked default (2.5 GB, 256K context — long enough to
# paste a dmesg/SMART dump into); qwen3:1.7b and qwen2.5-coder:7b are offered
# under the local-ai profile but not pre-ticked. Sizes are verified against
# ollama.com and live in each catalog entry's description, which a test
# enforces.
pull_ollama_model_qwen3_4b()        { pull_ollama_model "qwen3:4b"; }
pull_ollama_model_qwen3_1_7b()      { pull_ollama_model "qwen3:1.7b"; }
pull_ollama_model_qwen25_coder_7b() { pull_ollama_model "qwen2.5-coder:7b"; }

# oterm (Task 13): the TUI client for Ollama. Verified 2026-09-12 that oterm
# ships no apt/snap/winget/choco package — pip (and brew, macOS-only, handled
# by catalog/macos.json's own "brew" provider instead of this function) is
# the only cross-platform install path the upstream docs
# (ggozad.github.io/oterm/installation) name. Debian/Ubuntu marks the system
# Python "externally managed" (PEP 668) since ~24.04, so a bare
# `pip install` is refused outright; `--user` keeps it out of site-packages
# and `--break-system-packages` (pip's own documented escape hatch for
# exactly this case) is only tried once the safer plain call is refused.
install_oterm() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install oterm via pip (python3 -m pip install --user oterm)"
        return 0
    fi
    if has_cmd oterm; then
        ui_muted "oterm already installed"
        return 0
    fi
    if ! has_cmd python3; then
        ui_warn "python3 not found; skipping oterm"
        return 0
    fi
    if ! python3 -m pip --version >/dev/null 2>&1; then
        if ! has_cmd apt-get; then
            ui_warn "pip not available and no apt-get to install it; skipping oterm"
            return 0
        fi
        ui_step "installing python3-pip (required by oterm)"
        apt_update_once
        if ! $AUTOOS_SUDO apt-get install -y --no-install-recommends python3-pip \
            >/tmp/autoos-oterm-pip-bootstrap.log 2>&1; then
            ui_warn "could not install python3-pip; skipping oterm (see /tmp/autoos-oterm-pip-bootstrap.log)"
            return 0
        fi
    fi
    ui_step "installing oterm (pip)"
    if python3 -m pip install --user oterm >/tmp/autoos-oterm-pip.log 2>&1 \
        || python3 -m pip install --user --break-system-packages oterm >/tmp/autoos-oterm-pip.log 2>&1; then
        ui_ok "oterm installed"
    else
        ui_warn "oterm install failed (see /tmp/autoos-oterm-pip.log)"
    fi
}

setup_git_config() {
    local name email
    name="$(answer git_user_name '')"
    email="$(answer git_user_email '')"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would configure git: user.name='${name}' user.email='${email}'"
        return 0
    fi
    if ! has_cmd git; then
        ui_warn "git not found; skipping git config"
        return 0
    fi
    if [[ -n "$name" ]]; then
        git config --global user.name "$name"
        ui_ok "configured git user.name: $name"
    fi
    if [[ -n "$email" ]]; then
        git config --global user.email "$email"
        ui_ok "configured git user.email: $email"
    fi
    git config --global init.defaultBranch main 2>/dev/null || true
}

link_fdfind() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would symlink ~/.local/bin/fd -> /usr/bin/fdfind"
        return 0
    fi
    if has_cmd fdfind && ! has_cmd fd; then
        mkdir -p "$SYS_HOME/.local/bin"
        ln -sf "$(command -v fdfind)" "$SYS_HOME/.local/bin/fd"
        ui_ok "symlinked ~/.local/bin/fd -> $(command -v fdfind)"
    fi
}

CONFIG_PROFILE=""
autoos_config_load() {
    local cfg_path="$1"
    [[ -f "$cfg_path" ]] || return 0
    catalog_require_python || return 1
    local py_script='
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)
    prof = data.get("profile", "")
    print(f"PROFILE={prof}")
    answers = data.get("answers", {})
    for k, v in answers.items():
        if isinstance(v, (str, int, float, bool)):
            print(f"ANS_{k}={v}")
except Exception:
    pass
'
    local line
    # tr -d '\r': a python3 invoked from a native Windows install (this repo
    # is tested from Git Bash as well as WSL2/Linux, per AGENTS.md §5) writes
    # CRLF line endings to a text-mode stdout even inside a Unix-style shell.
    # Left in, the trailing \r rides along on CONFIG_PROFILE - "light\r" never
    # equals any known profile, so a saved run replayed with --config fails
    # with "Unknown profile 'light'" even though the file says "light".
    while IFS= read -r line; do
        if [[ "$line" =~ ^PROFILE=(.*)$ ]]; then
            CONFIG_PROFILE="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^ANS_([^=]+)=(.*)$ ]]; then
            local k="${BASH_REMATCH[1]}"
            local v="${BASH_REMATCH[2]}"
            AUTOOS_ANSWERS["$k"]="$v"
        fi
    done < <(python3 -c "$py_script" "$cfg_path" | tr -d '\r')
}

autoos_config_save() {
    local cfg_path="$1" prof="${2:-${PROFILE:-}}"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would save configuration to ${cfg_path}"
        return 0
    fi
    catalog_require_python || return 1
    local env_args=()
    local k safe_k
    for k in "${!AUTOOS_ANSWERS[@]}"; do
        safe_k="$(printf '%s' "$k" | tr '[:lower:]-' '[:upper:]_')"
        env_args+=("AUTOOS_SAVE_ANSWER_${safe_k}=${AUTOOS_ANSWERS[$k]}")
    done
    env "${env_args[@]}" python3 - "$cfg_path" "$prof" <<'PY'
import json, os, sys, tempfile

cfg_path = sys.argv[1]
prof = sys.argv[2] if len(sys.argv) > 2 else ""

data = {"version": 1, "profile": prof or "workstation", "answers": {}}
if os.path.isfile(cfg_path):
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
            if isinstance(existing, dict):
                data["version"] = existing.get("version", 1)
                if not prof and "profile" in existing:
                    data["profile"] = existing["profile"]
                if "answers" in existing and isinstance(existing["answers"], dict):
                    data["answers"].update(existing["answers"])
    except Exception:
        pass

if prof:
    data["profile"] = prof

for k, v in os.environ.items():
    if k.startswith("AUTOOS_SAVE_ANSWER_"):
        raw_k = k[len("AUTOOS_SAVE_ANSWER_"):].lower()
        data["answers"][raw_k] = v

dir_name = os.path.dirname(os.path.abspath(cfg_path))
os.makedirs(dir_name, exist_ok=True)
with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
    json.dump(data, tf, indent=2)
    tf.write("\n")
    temp_name = tf.name

os.replace(temp_name, cfg_path)
try:
    os.chmod(cfg_path, 0o644)
except OSError:
    pass
PY
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
    if [[ -e "$dest" ]]; then
        ui_muted "nvim config already exists - leaving it alone"
    else
        if (( AUTOOS_DRY_RUN )); then ui_muted "would install the LazyVim starter into $dest"; else
            git clone --depth 1 https://github.com/LazyVim/starter "$dest"
            rm -rf "$dest/.git"
            ui_ok "LazyVim starter installed"
        fi
    fi
    enable_sidekick_extra
}

enable_sidekick_extra() {
    # Folke's sidekick.nvim embeds the opencode CLI in Neovim (<leader>aa).
    # LazyVim ships it as an extra; enabling = one id in lazyvim.json.
    local lj="$SYS_HOME/.config/nvim/lazyvim.json"
    if (( AUTOOS_DRY_RUN )); then ui_muted "would enable the sidekick extra in $lj"; return 0; fi
    [[ -d "$SYS_HOME/.config/nvim" ]] || { ui_muted "no nvim config - skipping sidekick"; return 0; }
    python3 - "$lj" <<'PY'
import json, os, sys
path = sys.argv[1]
extra = "lazyvim.plugins.extras.ai.sidekick"
cfg = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
extras = cfg.setdefault("extras", [])
if extra not in extras:
    extras.append(extra)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    print("enabled")
else:
    print("already")
PY
    ui_ok "sidekick extra enabled (<leader>aa toggles the opencode panel)"
}

install_zed() {
    # Zed publishes no checksum for this installer, so the minimum bar (same
    # as install_agy/install_uv/install_ollama, guard A14): download to a
    # file, verify it is non-empty and actually looks like a script, and only
    # then execute the FILE - never the pipe.
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install Zed via https://zed.dev/install.sh"; return 0; fi
    local url="https://zed.dev/install.sh"
    ui_muted "downloading Zed installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -fsSL -o "$tmp" "$url"; then
        ui_err "failed to download Zed installer from $url"
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_err "Zed installer from $url does not look like a script - aborting"
        rm -f "$tmp"
        return 1
    fi
    local rc=0
    sh "$tmp" || rc=$?
    rm -f "$tmp"
    if (( rc != 0 )); then return $rc; fi
    ui_ok "Zed installed"
}

install_openhands() {
    # Pull the OpenHands image; the container itself is started on demand by
    # configuration/start-stack.sh openhands, wired to the OmniRoute gateway.
    local image="docker.openhands.dev/openhands/openhands:latest"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would pull ${image} (Docker daemon must be running)"
        return 0
    fi
    if ! has_cmd docker; then
        ui_warn "docker CLI not found - install Docker first, then re-run"
        return 0
    fi
    if ! docker info >/dev/null 2>&1; then
        ui_warn "Docker daemon is not running - start it, then: docker pull ${image}"
        return 0
    fi
    run docker pull "$image"
    ui_ok "OpenHands image ready"
    ui_info "start it with: ./configuration/start-stack.sh openhands"
}

install_litellm_proxy() {
    if has_cmd litellm; then ui_muted "litellm already installed"; return 0; fi
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install litellm[proxy] via pipx or pip"; return 0; fi
    if has_cmd pipx; then
        pipx install 'litellm[proxy]' || ui_warn "pipx install failed - see docs/models.md for the manual step"
    elif has_cmd python3; then
        python3 -m pip install --user 'litellm[proxy]' || ui_warn "pip install failed - see docs/models.md for the manual step"
    else
        ui_warn "no python3 on PATH - install Python, then: pip install 'litellm[proxy]'"
    fi
    ui_info "next: copy configuration/litellm/.env.example to .env, add keys (docs/api-keys.md)"
    return 0
}

route_zed_to_proxy() {
    # Point Zed's agent panel at the local OmniRoute gateway (:20128) plus
    # the LiteLLM fallback (:4000). Only the provider ids
    # 'autoos-omniroute' / 'autoos-litellm' are written; every other
    # setting is kept. Keys NEVER go into settings.json (Zed docs: provider
    # keys come from the keychain/UI or env). Zed derives the env name from
    # the provider id, so export AUTOOS_OMNIROUTE_API_KEY and
    # AUTOOS_LITELLM_API_KEY before starting Zed; missing keys warn here and
    # the providers stay hidden until a restart picks them up.
    local cfg_dir="$SYS_HOME/.config/zed"
    local cfg="$cfg_dir/settings.json"
    if (( AUTOOS_DRY_RUN )); then ui_muted "would route Zed agents to OmniRoute in $cfg"; return 0; fi
    mkdir -p "$cfg_dir"
    if [[ -f "$cfg" ]]; then
        cp "$cfg" "$cfg.autoos-backup-$(date +%Y%m%d-%H%M%S)"
    fi
    python3 - "$cfg" <<'PY'
import json, os, sys
path = sys.argv[1]
cfg = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
tiers = [
    ("tier1", "tier1 orchestrator (contributor)", 1048576, "xhigh"),
    ("tier1-clean", "tier1-clean (paid contributor)", 1048576, None),
    ("tier2", "tier2 smart (free-first)", 131072, None),
    ("tier2-clean", "tier2-clean (paid)", 131072, None),
    ("tier3", "tier3 driver (cheapest)", 131072, None),
    ("tier3-clean", "tier3-clean (paid)", 131072, None),
]
auto = [
    {"name": "auto/smart", "display_name": "tier1 orchestrator (auto smart)",
     "max_tokens": 131072, "reasoning_effort": "xhigh"},
    {"name": "auto", "display_name": "tier2 smart (auto balanced)",
     "max_tokens": 131072},
    {"name": "auto/cheap", "display_name": "tier3 driver (auto cheap)",
     "max_tokens": 131072},
]
tier_models = []
for name, disp, mx, effort in tiers:
    m = {"name": name, "display_name": disp, "max_tokens": mx}
    if effort:
        m["reasoning_effort"] = effort
    tier_models.append(m)
lm = cfg.setdefault("language_models", {})
oc = lm.setdefault("openai_compatible", {})
omni = {
    "api_url": "http://127.0.0.1:20128/v1",
    "available_models": auto + tier_models,
}
oc["autoos-omniroute"] = omni
lit_models = [
    {"name": n, "display_name": "%s (litellm fallback)" % n,
     "max_tokens": mx}
    for n, _, mx, _ in [
        ("tier1", None, 1048576, None), ("tier1-paid", None, 1048576, None),
        ("tier2", None, 131072, None), ("tier2-paid", None, 131072, None),
        ("tier3", None, 131072, None), ("tier3-paid", None, 131072, None),
    ]
]
lit = {
    "api_url": "http://127.0.0.1:4000/v1",
    "available_models": lit_models,
}
oc["autoos-litellm"] = lit
# Bypass profile: every built-in tool on, no confirmations (global
# tool_permissions.default allow). Existing profiles and per-tool rules stay.
agent = cfg.setdefault("agent", {})
profiles = agent.setdefault("profiles", {})
bypass_tools = {t: True for t in [
    "ask_user", "create_directory", "copy_path", "delete_path",
    "diagnostics", "edit_file", "fetch", "find_path", "grep",
    "list_directory", "move_path", "skill", "read_file", "spawn_agent",
    "terminal", "search_web", "write_file"]}
profiles["bypass"] = {
    "name": "bypass",
    "tools": bypass_tools,
    "enable_all_context_servers": False,
    "context_servers": {},
    "default_model": {"provider": "autoos-omniroute", "model": "tier1"},
}
tp = agent.setdefault("tool_permissions", {})
tp["default"] = "allow"
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
PY
    # A failed merge must not report success: setup.sh runs under set -e,
    # but sourced/test contexts do not, so check explicitly.
    if (( PIPESTATUS[0] != 0 )); then
        ui_warn "could not update $cfg - is python3 working?"
        return 1
    fi
    [[ -n "${AUTOOS_OMNIROUTE_API_KEY:-}" ]] || ui_warn "AUTOOS_OMNIROUTE_API_KEY not set - export the OmniRoute client key before starting Zed, or the provider stays hidden"
    [[ -n "${AUTOOS_LITELLM_API_KEY:-}" ]] || ui_warn "AUTOOS_LITELLM_API_KEY not set - export the LiteLLM master key before starting Zed, or the fallback stays hidden"
    ui_ok "Zed agents routed to OmniRoute + LiteLLM (keys via env, never settings.json)"
    return 0
}

# ─── MCP wiring ─────────────────────────────────────────────────────────────
# Claude Code owns ~/.claude.json, so `claude mcp add` edits it, not us. That
# file is tens of kilobytes of the user's own session state, and rewriting all
# of it to change one key is exactly the "overwrite a config wholesale" failure
# this repository has shipped before.

mcp_server_names() {
    has_cmd claude || return 0
    # Lines read "name: command - status". Plugin-provided servers are prefixed
    # "plugin:<plugin>:<name>", so the last colon-separated field is the name
    # that matters — a serena from a plugin is still a serena.
    claude mcp list 2>/dev/null | sed -n 's/^[[:space:]]*\([^: ]*\):[[:space:]].*/\1/p' |
        awk -F: '{print $NF}'
}

mcp_has_server() {
    local want="$1" name
    while IFS= read -r name; do
        [[ "$name" == "$want" ]] && return 0
    done < <(mcp_server_names)
    return 1
}

# MCP package specs live in catalog/agent-harness.json, never inline. AUTOOS_ROOT
# is set by setup.sh before this file is sourced; the fallback keeps the helpers
# usable when install.sh is sourced directly (the test suite).
AUTOOS_HARNESS="${AUTOOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/catalog/agent-harness.json"

mcp_package() {
    python3 -c '
import json, sys
with open(sys.argv[2], encoding="utf-8") as f:
    print(json.load(f)["mcp_servers"][sys.argv[1]]["package"])
' "$1" "$AUTOOS_HARNESS"
}

serena_excluded_tools() {
    python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    for tool in json.load(f)["mcp_servers"]["serena"]["excluded_tools"]:
        print(tool)
' "$AUTOOS_HARNESS"
}

register_mcp_server() {
    local name="$1" scope="$2" workdir="$3"; shift 3
    if ! has_cmd claude; then
        ui_warn "claude is not on PATH — cannot register '${name}'. Install claude-code first."
        return 0
    fi
    if mcp_has_server "$name"; then
        ui_muted "MCP server '${name}' is already registered — left alone."
        return 0
    fi
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would run: claude mcp add --scope ${scope} ${name} -- $*"
        return 0
    fi
    ui_muted "run: claude mcp add --scope ${scope} ${name} -- $*"
    if ( cd "$workdir" 2>/dev/null || cd "$SYS_HOME"; claude mcp add --scope "$scope" "$name" -- "$@" ); then
        ui_ok "registered MCP server '${name}' (${scope} scope)"
    else
        ui_warn "could not register '${name}'"
    fi
    return 0
}

antigravity_mcp_config_path() {
    printf '%s/.gemini/config/mcp_config.json' "$SYS_HOME"
}

antigravity_has_server() {
    local want="$1"
    local cfg_path
    cfg_path="$(antigravity_mcp_config_path)"
    [[ -f "$cfg_path" && -s "$cfg_path" ]] || return 1
    python3 - "$cfg_path" "$want" <<'PY' >/dev/null 2>&1
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    sys.exit(0 if sys.argv[2] in data.get("mcpServers", {}) else 1)
except Exception:
    sys.exit(1)
PY
}

register_antigravity_mcp_server() {
    local name="$1" spec_json="$2"
    local cfg_path
    cfg_path="$(antigravity_mcp_config_path)"
    local cfg_dir
    cfg_dir="$(dirname "$cfg_path")"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would merge '${name}' into Antigravity MCP config (${cfg_path})"
        return 0
    fi

    mkdir -p "$cfg_dir"
    if [[ -f "$cfg_path" && -s "$cfg_path" ]]; then
        cp "$cfg_path" "${cfg_path}.autoos-backup-$(date +%Y%m%d-%H%M%S)"
    fi

    local out
    out="$(python3 - "$cfg_path" "$name" "$spec_json" <<'PY'
import json, pathlib, sys

path = pathlib.Path(sys.argv[1])
name = sys.argv[2]
try:
    spec = json.loads(sys.argv[3])
except Exception as exc:
    print(f"invalid-spec: {exc}")
    sys.exit(2)

data = {}
if path.exists() and path.stat().st_size > 0:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print("not-json")
        sys.exit(1)

servers = data.setdefault("mcpServers", {})
if servers.get(name) == spec:
    print("already")
    sys.exit(0)

servers[name] = spec
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print("added")
PY
)"
    local rc=$?
    if [[ $rc -eq 0 ]]; then
        if [[ "$out" == "already" ]]; then
            ui_muted "MCP server '${name}' already configured in Antigravity"
        else
            ui_ok "configured MCP server '${name}' in Antigravity (${cfg_path})"
        fi
    elif [[ "$out" == "not-json" ]]; then
        ui_warn "${cfg_path} is not valid JSON — leaving it alone."
    else
        ui_warn "could not update Antigravity MCP config at ${cfg_path}"
    fi
    return 0
}

install_mcp_serena() {
    ui_info "Setting up Serena MCP server (Claude Code + Antigravity)"
    local serena_pkg
    serena_pkg="$(mcp_package serena)"
    register_mcp_server serena user "$SYS_HOME" \
        uvx --from "$serena_pkg" serena start-mcp-server --open-web-dashboard false --enable-gui-log-window false

    local serena_home="$SYS_HOME/.serena"
    local spec
    spec="$(serena_excluded_tools | python3 -c '
import json, sys
tools = [line.rstrip("\n") for line in sys.stdin if line.strip()]
print(json.dumps({
    "command": "uvx",
    "args": ["--from", sys.argv[1], "serena", "start-mcp-server", "--open-web-dashboard", "false", "--enable-gui-log-window", "false"],
    "env": {"SERENA_HOME": sys.argv[2]},
    "excludeTools": tools
}))
' "$serena_pkg" "$serena_home")"
    register_antigravity_mcp_server serena "$spec"

    # shellcheck disable=SC2119  # config_path is optional; the default (SYS_HOME/.serena/serena_config.yml) is what we want here
    ensure_serena_exclusions

    # excludeTools above only reaches Serena when Claude Code/Antigravity start
    # it with that flag. Serena's OWN global config is what every other MCP
    # client (or a bare `serena start-mcp-server`) gets instead, so the probe
    # below actually starts Serena and asks it, over real JSON-RPC, which
    # tools it exposes - the only way to know ensure_serena_exclusions'
    # file edit really took effect. It must run here, after the call above,
    # and never inside ensure_serena_exclusions itself, so tests that call
    # that function directly never spawn a real Serena process.
    if has_cmd python3 && (has_cmd serena || has_cmd uvx) && (( ! AUTOOS_DRY_RUN )); then
        local probe_out
        if probe_out="$(python3 "${AUTOOS_ROOT}/tools/check-serena-tools.py" 2>&1)"; then
            ui_ok "Serena tool exclusion probe: ${probe_out}"
        else
            ui_warn "Serena tool exclusion probe failed: ${probe_out}"
        fi
    fi
}

# ensure_serena_exclusions [config_path]
# Serena's global config (~/.serena/serena_config.yml by default) has its own
# excluded_tools list, independent of the excludeTools flag register_mcp_*
# above bakes into each client's invocation. Anything missing from THIS list
# is exposed to any MCP client that talks to Serena directly - onboarding and
# the memory tools in particular, which this repo deliberately routes through
# Omnigraph instead (see CLAUDE.md's "one tool per job"). This adds the
# canonical 15 while leaving every other line of the file, including the
# user's own extra exclusions and any comments, untouched.
#
# The whole read-merge-backup-write cycle lives in ONE python3 process
# (below), never in bash: an earlier version captured python's stdout with
# `result="$(...)"` and blindly `printf`'d it over the config, so a missing
# python3, an unreadable/undecodable file, or config_path being a directory
# silently truncated the file to a stray newline. Now python does its own
# read, backup (shutil.copy2) and atomic write (temp file + os.replace) and
# reports exactly one status word on stdout - SKIP, UPDATED or CREATED - or
# a non-zero exit with a reason on stderr and the file left untouched.
# shellcheck disable=SC2120  # config_path is optional: callers with no args get $SYS_HOME/.serena/serena_config.yml; tests pass a temp path explicitly
ensure_serena_exclusions() {
    local config_path="${1:-$SYS_HOME/.serena/serena_config.yml}"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would ensure Serena's excluded_tools list is complete in ${config_path}"
        return 0
    fi

    if ! has_cmd python3; then
        ui_warn "python3 is not installed - cannot verify Serena's excluded_tools list in ${config_path}"
        return 0
    fi

    local canonical
    mapfile -t canonical < <(serena_excluded_tools)

    local result rc=0
    result="$(python3 - "$config_path" "${canonical[@]}" 2>&1 <<'PY'
import os
import re
import shutil
import sys
import time

path = sys.argv[1]
canonical = sys.argv[2:]
canonical_set = set(canonical)


def parse_scalar(s):
    # Quote-aware, comment-aware scalar value: a quoted value runs up to its
    # matching closing quote (a '#' inside the quotes is just a character);
    # an unquoted value ends at the first whitespace-then-'#' (a trailing
    # comment) or at end of string.
    s = s.strip()
    if not s:
        return ""
    if s[0] in ("'", '"'):
        q = s[0]
        i = 1
        while i < len(s) and s[i] != q:
            i += 1
        return s[1:i] if i < len(s) else s[1:]
    if s.startswith("#"):
        return ""
    m = re.search(r"\s#", s)
    return s[: m.start()].strip() if m else s.strip()


def split_flow(inner):
    # Comma-split that does not split on a comma inside quotes.
    items, cur, q = [], "", None
    for c in inner:
        if q:
            cur += c
            if c == q:
                q = None
        elif c in ("'", '"'):
            q = c
            cur += c
        elif c == ",":
            items.append(cur)
            cur = ""
        else:
            cur += c
    if cur.strip():
        items.append(cur)
    return items


def strip_item_line(raw):
    # Like parse_scalar, but also returns a trailing comment on the item
    # line itself (e.g. "- 'read_file'  # canonical"), quote-aware: text
    # after a quoted value's closing quote is a comment candidate exactly
    # like text after an unquoted value. Returns (value, comment_or_None).
    s = re.sub(r"^-\s*", "", raw.strip(), count=1)
    if s and s[0] in ("'", '"'):
        q = s[0]
        i = 1
        while i < len(s) and s[i] != q:
            i += 1
        if i < len(s):
            value, tail = s[1:i], s[i + 1:]
        else:
            value, tail = s[1:], ""
    elif s.startswith("#"):
        value, tail = "", s
    else:
        m = re.search(r"\s#", s)
        if m:
            value, tail = s[: m.start()].strip(), s[m.start():]
        else:
            value, tail = s.strip(), ""
    comment = tail.strip()
    return value, (comment if comment.startswith("#") else None)


def is_block_item(s):
    # "-" is a list item only when followed by whitespace or nothing; "---"
    # (a document separator) or "-foo" is ordinary text, not an item.
    return s == "-" or (s.startswith("-") and s[1:2].isspace())


def scan_flow(lines, newline, j, text_here, idx):
    # Scans a bracketed flow list starting at text_here[idx:] (text_here is
    # lines[j] with its terminator stripped), consuming further lines until
    # the matching unquoted ']'. A '#' outside quotes, at the start of a
    # physical line or preceded by whitespace, starts a comment that runs to
    # the end of that line: it is stripped out of the buffer (so it can't
    # swallow the next item) and returned separately.
    buf, q, tail = "", None, None
    inner_comments = []
    n = len(lines)
    while True:
        seg = text_here
        while idx < len(seg):
            c = seg[idx]
            if q:
                buf += c
                if c == q:
                    q = None
            elif c in ("'", '"'):
                q = c
                buf += c
            elif c == "]" and not q:
                tail = seg[idx + 1:]
                break
            elif c == "#" and not q and (idx == 0 or seg[idx - 1] in (" ", "\t")):
                comment = seg[idx:].rstrip("\r\n")
                if comment.strip():
                    inner_comments.append(comment + newline)
                break
            else:
                buf += c
            idx += 1
        if tail is not None:
            break
        j += 1
        if j >= n:
            tail = ""  # malformed/unterminated - stop at EOF
            break
        buf += "\n"
        text_here = lines[j].rstrip("\r\n")
        idx = 0
    return buf, j, tail, inner_comments


try:
    with open(path, "rb") as fh:
        raw_bytes = fh.read()
    existed = True
except FileNotFoundError:
    raw_bytes = None
    existed = False
except OSError as exc:
    print(f"cannot read {path}: {exc}", file=sys.stderr)
    sys.exit(1)

# surrogateescape round-trips ANY byte sequence (valid UTF-8 or not) exactly:
# an invalid byte becomes a lone surrogate on decode and the identical byte
# again on encode, so a stray non-UTF-8 byte anywhere outside the block we
# touch survives unchanged.
text = raw_bytes.decode("utf-8", errors="surrogateescape") if raw_bytes is not None else ""
newline = "\r\n" if "\r\n" in text else "\n"
lines = text.splitlines(keepends=True) if text else []

key_start = None
key_end = None  # exclusive
existing = []
interior_comments = []
new_key_line_text = None

i, n = 0, len(lines)
while i < n:
    line = lines[i]
    if line.startswith("excluded_tools:"):
        key_start = i
        rest_nolnend = line[len("excluded_tools:"):].rstrip("\r\n")
        rest = rest_nolnend.strip()

        if rest.startswith("["):
            # Flow form: [a, b] - possibly with a trailing comment, possibly
            # spanning several lines before its closing ']'.
            idx0 = rest_nolnend.index("[") + 1
            buf, j, tail, inner = scan_flow(lines, newline, i, rest_nolnend, idx0)
            for part in split_flow(buf):
                val = parse_scalar(part)
                if val:
                    existing.append(val)
            interior_comments.extend(inner)
            key_end = j + 1
            if tail and tail.strip().startswith("#"):
                new_key_line_text = "excluded_tools:" + tail + newline
            else:
                new_key_line_text = "excluded_tools:" + newline

        elif rest == "" or rest.startswith("#"):
            # Bare key (or one with a trailing comment). The value is
            # normally an indented block of "- item" lines starting on the
            # next line, but it can also be a flow list moved to its own
            # line ("excluded_tools:\n  [a, b]") - peek past any blank/
            # comment lines for the first real content and dispatch on it.
            k = i + 1
            peek_comments = []
            while k < n:
                tk = lines[k].strip()
                if tk == "":
                    k += 1
                elif tk.startswith("#"):
                    peek_comments.append(lines[k])
                    k += 1
                else:
                    break

            if k < n and lines[k].strip().startswith("["):
                text_k = lines[k].rstrip("\r\n")
                idx0 = text_k.index("[") + 1
                buf, j, tail, inner = scan_flow(lines, newline, k, text_k, idx0)
                for part in split_flow(buf):
                    val = parse_scalar(part)
                    if val:
                        existing.append(val)
                interior_comments.extend(peek_comments)
                interior_comments.extend(inner)
                if tail and tail.strip().startswith("#"):
                    interior_comments.append(tail.strip() + newline)
                key_end = j + 1
            else:
                # Block form. A comment line only belongs to the block if
                # another list item follows it later - move those to
                # directly after the key line, in original order. A
                # trailing comment after the last item (or before the next
                # top-level key) is NOT ours and is left where it is. Blank
                # lines inside the block are simply dropped.
                j = i + 1
                committed_end = i + 1
                pending_comments = []
                while j < n:
                    l = lines[j]
                    st = l.strip()
                    if is_block_item(st):
                        val, item_comment = strip_item_line(l)
                        existing.append(val)
                        interior_comments.extend(pending_comments)
                        pending_comments = []
                        if item_comment:
                            interior_comments.append(item_comment + newline)
                        j += 1
                        committed_end = j
                    elif st == "":
                        j += 1
                    elif st.startswith("#"):
                        pending_comments.append(l)
                        j += 1
                    else:
                        break
                key_end = committed_end

            # A key line reused verbatim (bare, commented, or the flow-on-
            # its-own-line case above) must end with a real newline even
            # when it was the last line of the file - otherwise the first
            # generated item glues onto it ("excluded_tools:- item").
            new_key_line_text = lines[key_start]
            if not new_key_line_text.endswith("\n"):
                new_key_line_text += newline
        else:
            # Some other scalar (e.g. "excluded_tools: null") - not a list
            # AutoOS understands; replace it outright.
            key_end = i + 1
            new_key_line_text = "excluded_tools:" + newline
        break
    i += 1

merged = list(canonical)
seen = set(canonical_set)
for item in existing:
    if item and item not in seen:
        merged.append(item)
        seen.add(item)

if canonical_set.issubset(set(existing)):
    print("SKIP")
    sys.exit(0)

item_lines = [f"- {item}{newline}" for item in merged]

if key_start is None:
    prefix_lines = list(lines)
    if prefix_lines and not prefix_lines[-1].endswith("\n"):
        prefix_lines[-1] = prefix_lines[-1] + "\n"
    new_lines = prefix_lines + [f"excluded_tools:{newline}"] + item_lines
else:
    block_lines = [new_key_line_text] + interior_comments + item_lines
    new_lines = lines[:key_start] + block_lines + lines[key_end:]

new_bytes = "".join(new_lines).encode("utf-8", errors="surrogateescape")

directory = os.path.dirname(path)
try:
    if directory:
        os.makedirs(directory, exist_ok=True)
except OSError as exc:
    print(f"could not create directory {directory}: {exc}", file=sys.stderr)
    sys.exit(1)

if existed:
    try:
        shutil.copy2(path, f"{path}.autoos-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    except OSError as exc:
        print(f"could not back up {path}: {exc}", file=sys.stderr)
        sys.exit(1)

tmp_path = f"{path}.autoos-tmp-{os.getpid()}"
try:
    with open(tmp_path, "wb") as fh:
        fh.write(new_bytes)
    os.replace(tmp_path, path)
except OSError as exc:
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    print(f"could not write {path}: {exc}", file=sys.stderr)
    sys.exit(1)

print("CREATED" if not existed else "UPDATED")
PY
)"
    rc=$?

    if (( rc != 0 )); then
        ui_warn "could not update Serena's excluded_tools in ${config_path}: ${result}"
        return 0
    fi

    case "$result" in
        SKIP)    ui_muted "Serena excluded_tools already complete (skipped)" ;;
        CREATED) ui_ok "created ${config_path} with Serena's excluded_tools" ;;
        UPDATED) ui_ok "updated Serena excluded_tools in ${config_path}" ;;
        *)       ui_warn "unexpected result updating Serena excluded_tools: ${result}" ;;
    esac
    return 0
}

install_mcp_graphify() {
    ui_info "Setting up Graphify MCP server (Claude Code + Antigravity)"
    local graphify_pkg
    graphify_pkg="$(mcp_package graphify)"
    register_mcp_server graphify user "$SYS_HOME" \
        uv --quiet run --with "$graphify_pkg" python -m graphify.serve graphify-out/graph.json

    local spec
    spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "uv", "args": ["--quiet", "run", "--with", sys.argv[1], "python", "-m", "graphify.serve", "${workspaceFolder}/graphify-out/graph.json"]}))
' "$graphify_pkg")"
    register_antigravity_mcp_server graphify "$spec"
}

install_mcp_playwright() {
    ui_info "Setting up Playwright MCP server (Claude Code + Antigravity)"
    local playwright_pkg
    playwright_pkg="$(mcp_package playwright)"
    register_mcp_server playwright user "$SYS_HOME" \
        npx -y "$playwright_pkg"

    local spec
    spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "npx", "args": ["-y", sys.argv[1]]}))
' "$playwright_pkg")"
    register_antigravity_mcp_server playwright "$spec"
}

install_mcp_context7() {
    ui_info "Setting up Context7 MCP server (Claude Code + Antigravity)"
    local context7_pkg key
    context7_pkg="$(mcp_package context7)"
    key="$(answer context7_api_key "${CONTEXT7_API_KEY:-}")"
    if [[ -n "$key" ]]; then
        register_mcp_server context7 user "$SYS_HOME" \
            npx -y "$context7_pkg" --api-key "$key"
        local spec
        spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "npx", "args": ["-y", sys.argv[1], "--api-key", sys.argv[2]]}))
' "$context7_pkg" "$key")"
        register_antigravity_mcp_server context7 "$spec"
    else
        register_mcp_server context7 user "$SYS_HOME" \
            npx -y "$context7_pkg"
        local spec
        spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "npx", "args": ["-y", sys.argv[1]]}))
' "$context7_pkg")"
        register_antigravity_mcp_server context7 "$spec"
    fi
}

# A tracked .mcp.json cannot approve itself: Claude Code skips a project server
# until it is named in that repo's own untracked .claude/settings.local.json. It
# skips it *silently*, which is the real problem — an unapproved server looks
# exactly like a broken one.
enable_project_mcp_server() {
    local repo="$1" name="$2"
    local path="$repo/.claude/settings.local.json"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would approve project MCP server '${name}' in ${path}"
        return 0
    fi
    mkdir -p "$repo/.claude"
    [[ -f "$path" ]] && cp "$path" "${path}.autoos-backup-$(date +%Y%m%d-%H%M%S)"
    if ! python3 - "$path" "$name" <<'PY'; then
import json, pathlib, sys
path, name = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.read_text(encoding="utf-8").strip() else {}
except (json.JSONDecodeError, OSError):
    print("not-json")
    raise SystemExit(2)
enabled = list(data.get("enabledMcpjsonServers") or [])
if name in enabled:
    print("already")
    raise SystemExit(0)
enabled.append(name)
data["enabledMcpjsonServers"] = enabled
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print("added")
PY
        ui_warn "${path} is not valid JSON — leaving it alone."
        return 0
    fi
    ui_ok "approved project MCP server '${name}' in ${path}"
    return 0
}

# The omnigraph MCP server is a container talking to a graph server over a Docker
# network. Miss the image, the network or the token and MCP start-up fails with
# "pull access denied", "fetch failed" or "missing bearer token" respectively —
# none of which say which of the three it was. AutoOS does not build or start
# that stack; it reports what is not ready yet.
omnigraph_readiness() {
    local dir="$1" ready=1
    if ! has_cmd docker; then
        ui_warn "docker is not installed — omnigraph runs as a container."
        return 1
    fi
    if ! docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -q '^omnigraph-mcp:latest$'; then
        ui_warn "omnigraph-mcp:latest is not built. Build it with:"
        ui_muted "    docker build -t omnigraph-mcp:latest ${dir}/infra/mcp-servers/servers/omnigraph-mcp"
        ready=0
    fi
    if ! docker network ls --format '{{.Name}}' 2>/dev/null | grep -q 'mcp-server'; then
        ui_warn "no mcp-server Docker network — the graph server stack is not up."
        ui_muted "    docker compose -f ${dir}/infra/mcp-servers/docker-compose.client.yml up -d"
        ready=0
    fi
    if [[ -z "${OMNIGRAPH_TOKEN:-}" ]]; then
        # Never invent one. An empty bearer fails as "missing bearer token",
        # which at least names itself; a made-up value fails as a 401 nobody can
        # explain — and this repository is public, so a real-looking secret in it
        # is a leak whether or not it happens to work.
        ui_warn "OMNIGRAPH_TOKEN is not set — the server will reject every call."
        ui_muted "    it is issued by the graph server, not by AutoOS. Copy"
        ui_muted "    ${dir}/infra/mcp-servers/.env.client.example to .env.client and fill it in."
        ready=0
    fi
    (( ready ))
}

install_agent_skills() {
    local code_root="$SYS_HOME/Documents/Code"
    if [[ -d "$SYS_HOME/Documents/code" ]]; then
        code_root="$SYS_HOME/Documents/code"
    fi
    local dest="$code_root/agent-skills"
    (( AUTOOS_DRY_RUN )) || mkdir -p "$code_root"
    clone_or_update https://github.com/Ch3fUlrich/agent-skills.git "$dest"

    local omni base
    omni="$(answer omnigraph_url '')"
    if [[ -z "$omni" ]]; then base="http://localhost:8080"; else base="${omni%/}"; fi
    ui_info "Omnigraph base URL: ${base}"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would write ${SYS_HOME}/.autoos-omnigraph.env"
    else
        printf 'OMNIGRAPH_BASE_URL=%s\n' "$base" >"$SYS_HOME/.autoos-omnigraph.env"
        ui_ok "Omnigraph URL saved to ${SYS_HOME}/.autoos-omnigraph.env"
    fi

    # Wire user-scope MCP servers across Claude Code and Antigravity
    install_mcp_graphify
    install_mcp_serena
    install_mcp_playwright
    install_mcp_context7

    # omnigraph is the opposite: project scope only, pinned per repo by
    # OMNIGRAPH_GRAPH_ID. A user-scope entry silently WINS over the project one
    # and answers from the wrong graph, so this never creates one.
    if mcp_has_server omnigraph; then
        ui_warn "A user-scope omnigraph server exists. It silently overrides the"
        ui_warn "per-repo one and answers from the wrong graph. Remove it with:"
        ui_muted "    claude mcp remove omnigraph --scope user"
    fi
    if [[ -f "$dest/.mcp.json" ]]; then
        ui_muted "omnigraph is declared per-repo in ${dest}/.mcp.json"
        enable_project_mcp_server "$dest" omnigraph
    else
        ui_warn "no .mcp.json in ${dest} — nothing to pin omnigraph to."
    fi

    local omni_pkg omni_spec
    omni_pkg="$(mcp_package omnigraph)"
    omni_spec="$(python3 -c "
import json, os
env_vars = {'OMNIGRAPH_BASE_URL': '$base'}
if os.environ.get('OMNIGRAPH_GRAPH_ID'):
    env_vars['OMNIGRAPH_GRAPH_ID'] = os.environ['OMNIGRAPH_GRAPH_ID']
if os.environ.get('OMNIGRAPH_TOKEN'):
    env_vars['OMNIGRAPH_TOKEN'] = os.environ['OMNIGRAPH_TOKEN']
print(json.dumps({
    'command': 'npx',
    'args': ['-y', '$omni_pkg'],
    'env': env_vars
}))
")"
    register_antigravity_mcp_server omnigraph "$omni_spec"

    # Wire skills into Antigravity and Claude Code global skills directories
    local agy_skills="$SYS_HOME/.gemini/config/skills"
    local claude_skills="$SYS_HOME/.claude/skills"
    if [[ -d "$dest/skills" ]]; then
        if (( AUTOOS_DRY_RUN )); then
            ui_muted "would link skills from $dest/skills to $agy_skills and $claude_skills"
        else
            mkdir -p "$agy_skills" "$claude_skills"
            local s_dir s_name
            for s_dir in "$dest/skills"/*; do
                [[ -d "$s_dir" ]] || continue
                s_name="$(basename "$s_dir")"
                if [[ ! -e "$agy_skills/$s_name" ]]; then
                    ln -snf "$s_dir" "$agy_skills/$s_name" 2>/dev/null || cp -r "$s_dir" "$agy_skills/$s_name"
                fi
                if [[ ! -e "$claude_skills/$s_name" ]]; then
                    ln -snf "$s_dir" "$claude_skills/$s_name" 2>/dev/null || cp -r "$s_dir" "$claude_skills/$s_name"
                fi
            done
            ui_ok "Agent skills linked to Antigravity and Claude Code"
        fi
    fi

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would check the omnigraph image, network and token"
        return 0
    fi
    if omnigraph_readiness "$dest"; then
        ui_ok "omnigraph prerequisites are all present."
    fi
    ui_info "Restart Claude Code and Antigravity — MCP servers are only read at session start."
    return 0
}

# resolve_ollama_base_url
# Prints the Ollama endpoint the generated OpenCode/OpenHands configs should
# use, or nothing to keep the catalog default (http://127.0.0.1:11434/v1).
# OpenHands' agent-server runs on the host under agent-canvas (uvx) but in a
# container under docker compose, and 127.0.0.1 inside a container is the
# container itself. So:
#   OLLAMA_BASE_URL set             -> that, normalised to end in /v1
#   Ollama answers on
#   host.docker.internal            -> that (Docker Desktop: reachable from the
#                                      host AND from containers)
#   otherwise                       -> nothing: the catalog default. A native
#                                      Linux host has no host.docker.internal,
#                                      and a host-run agent-canvas needs 127.0.0.1.
resolve_ollama_base_url() {
    if [[ -n "${OLLAMA_BASE_URL:-}" ]]; then
        local url="${OLLAMA_BASE_URL%/}"
        [[ "$url" == */v1 ]] || url="$url/v1"
        printf '%s\n' "$url"
        return 0
    fi
    if curl -fsS --max-time 2 -o /dev/null "http://host.docker.internal:11434/api/version" 2>/dev/null; then
        printf '%s\n' "http://host.docker.internal:11434/v1"
    fi
    return 0
}

# Prints the machine's agent-skills skills directory, or nothing when absent.
autoos_skills_source() {
    local code_root="$SYS_HOME/Documents/Code"
    if [[ -d "$SYS_HOME/Documents/code" ]]; then
        code_root="$SYS_HOME/Documents/code"
    fi
    local skills_source="$code_root/agent-skills/skills"
    if [[ -d "$skills_source" ]]; then
        printf '%s\n' "$skills_source"
    fi
    return 0
}

setup_opencode_config() {
    local config_dir="$SYS_HOME/.config/opencode"
    local config_file="$config_dir/config.json"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would configure OpenCode in $config_file"
        ui_muted "would apply the agent harness (catalog/agent-harness.json)"
        return 0
    fi

    mkdir -p "$config_dir"
    if [[ -f "$config_file" ]]; then
        local ts
        ts="$(date +%Y%m%d%H%M%S)"
        cp "$config_file" "${config_file}.autoos-backup-${ts}"
    fi

    local secrets_file="$SYS_HOME/Documents/Code/agent-skills/secrets/api_keys.conf"
    [[ -f "$secrets_file" ]] || secrets_file="$SYS_HOME/Documents/code/agent-skills/secrets/api_keys.conf"

    # catalog/llm-models.json is the single source of truth for model data.
    # The repo root is anchored off this script, never off the caller's cwd.
    local models_file
    models_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/catalog/llm-models.json"

    local ollama_url
    ollama_url="$(resolve_ollama_base_url)"

    OLLAMA_BASE_URL="$ollama_url" python3 -c "
import json, os, sys

config_path = sys.argv[1]
secrets_path = sys.argv[2]
models_file = sys.argv[3]

# Model data lives in catalog/llm-models.json (single source of truth).
# Everything below projects it into OpenCode's shape; nothing here
# duplicates an id, a context window or a price.
with open(models_file, 'r', encoding='utf-8') as _mf:
    REPO_MODELS = json.load(_mf)['models']
REPO_BY_ID = {m['id']: m for m in REPO_MODELS}
# MCP package specs live in catalog/agent-harness.json, never inline.
_harness_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(models_file))), 'catalog', 'agent-harness.json')
with open(_harness_file, 'r', encoding='utf-8') as _hf:
    MCP_PACKAGES = {k: v['package'] for k, v in json.load(_hf)['mcp_servers'].items()}

def _opencode_cost(m):
    base = m.get('paid_input_price', m['input_price']), m.get('paid_output_price', m['output_price'])
    cost = {'input': base[0] * 1e6, 'output': base[1] * 1e6}
    if m.get('cache_read_price'):
        cost['cache_read'] = m['cache_read_price'] * 1e6
    return cost

def _openrouter_models():
    out = {}
    for m in REPO_MODELS:
        if not m.get('openrouter_id'):
            continue
        entry = {'name': m['name'], 'limit': {'context': m['context'], 'output': m['output']}, 'cost': _opencode_cost(m)}
        if m.get('reasoning'):
            entry['reasoning'] = True
        out[m['openrouter_id']] = entry
    return out

data = {}
if os.path.isfile(config_path):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {}

def _read_secrets_into(path, secrets):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    # First occurrence of a key wins.
                    secrets.setdefault(k.strip().lower(), v.strip().strip('\"\''))
    except Exception:
        pass

secrets = {}
# Only the real conf. api_keys.conf.example is never read: its placeholder
# keys are truthy and would be written into the config as if real (401s).
if os.path.isfile(secrets_path):
    _read_secrets_into(secrets_path, secrets)

providers = data.get('provider', {})
_ollama = REPO_BY_ID['ollama-qwen2.5-coder']['direct']
# resolve_ollama_base_url's answer; empty keeps the catalog default.
if os.environ.get('OLLAMA_BASE_URL'):
    _ollama['base_url'] = os.environ['OLLAMA_BASE_URL']
providers['ollama'] = {
    'npm': _ollama['npm'],
    'name': 'Ollama (Local)',
    'options': {
        'baseURL': _ollama['base_url']
    },
    'models': {
        'qwen2.5-coder:7b': {'name': 'Qwen 2.5 Coder 7B'},
        'qwen3:30b': {'name': 'Qwen 3 30B'}
    }
}

muse_key = os.environ.get('MUSE_API_KEY') or secrets.get('muse')
if muse_key:
    _muse = REPO_BY_ID['muse-spark']['direct']
    providers['meta'] = {
        'npm': _muse['npm'],
        'name': 'Meta AI (Muse Spark)',
        'options': {
            'baseURL': _muse['base_url'],
            'apiKey': muse_key
        },
        'models': {
            _muse['model'].split('/', 1)[1]: {
                'name': REPO_BY_ID['muse-spark']['name'],
                'reasoning': True,
                'limit': {'context': REPO_BY_ID['muse-spark']['context'], 'output': REPO_BY_ID['muse-spark']['output']},
                'options': {'reasoningEffort': _muse['reasoning_effort']}
            }
        }
    }

deepseek_key = os.environ.get('DEEPSEEK_API_KEY') or secrets.get('deepseek')
if deepseek_key:
    def _deepseek_entry(mid):
        m = REPO_BY_ID[mid]
        entry = {'name': m['name'], 'limit': {'context': m['context'], 'output': m['output']}}
        if m.get('reasoning'):
            entry['reasoning'] = True
        return entry
    _ds = REPO_BY_ID['deepseek-chat']['direct']
    providers['deepseek'] = {
        'npm': _ds['npm'],
        'name': 'DeepSeek',
        'options': {
            'baseURL': _ds['base_url'],
            'apiKey': deepseek_key
        },
        'models': {
            'deepseek-chat': _deepseek_entry('deepseek-chat'),
            'deepseek-reasoner': _deepseek_entry('deepseek-reasoner')
        }
    }

openrouter_key = os.environ.get('OPENROUTER_API_KEY') or secrets.get('openrouter')
if openrouter_key:
    providers['openrouter'] = {
        'npm': '@ai-sdk/openai-compatible',
        'name': 'OpenRouter',
        'options': {
            'baseURL': 'https://openrouter.ai/api/v1',
            'apiKey': openrouter_key
        },
        'models': _openrouter_models()
    }

data['provider'] = providers

if not data.get('model'):
    if muse_key:
        data['model'] = 'meta/' + REPO_BY_ID['muse-spark']['direct']['model'].split('/', 1)[1]
    else:
        data['model'] = 'ollama/' + REPO_BY_ID['ollama-qwen2.5-coder']['direct']['model'].split('/', 1)[1]

mcps = data.get('mcp', {})
mcps['serena'] = {
    'type': 'local',
    'command': ['uvx', '--from', MCP_PACKAGES['serena'], 'serena', 'start-mcp-server', '--context', 'claude-code', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false'],
    'enabled': True
}
mcps['graphify'] = {
    'type': 'local',
    'command': ['uvx', '--from', MCP_PACKAGES['graphify'], 'python', '-m', 'graphify.serve', 'graphify-out/graph.json'],
    'enabled': True
}
mcps['context7'] = {
    'type': 'local',
    'command': ['npx', '-y', MCP_PACKAGES['context7']],
    'enabled': True
}
mcps['omnigraph'] = {
    'type': 'local',
    'command': ['npx', '-y', MCP_PACKAGES['omnigraph']],
    'enabled': True,
    'environment': {'OMNIGRAPH_BASE_URL': 'http://localhost:8080', 'OMNIGRAPH_GRAPH_ID': 'autoos'}
}
mcps['playwright'] = {
    'type': 'local',
    'command': ['npx', '-y', MCP_PACKAGES['playwright']],
    'enabled': True
}
data['mcp'] = mcps

tmp_file = config_path + '.tmp'
with open(tmp_file, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
os.replace(tmp_file, config_path)
" "$config_file" "$secrets_file" "$models_file"

    ui_ok "OpenCode configuration written to $config_file"

    # Merge the shared agent harness (roles, skills link) after the config is
    # written. Judge by exit code only; the generator's own notes are muted.
    if has_cmd python3; then
        local harness_out harness_rc skills_source
        skills_source="$(autoos_skills_source)"
        [[ -n "$skills_source" ]] || skills_source="$SYS_HOME/Documents/Code/agent-skills/skills"
        harness_rc=0
        harness_out="$(python3 "$AUTOOS_ROOT/lib/agent_harness.py" opencode --config "$config_file" --repo-root "$AUTOOS_ROOT" --skills-source "$skills_source" 2>&1)" || harness_rc=$?
        if (( harness_rc != 0 )); then
            ui_warn "agent harness not applied to OpenCode (exit $harness_rc)"
        else
            while IFS= read -r _harness_line; do
                [[ -n "$_harness_line" ]] && ui_muted "$_harness_line"
            done <<< "$harness_out"
        fi
    else
        ui_warn "agent harness not applied: python3 not found"
    fi

    cp "$config_file" "$config_dir/opencode.json"
}

setup_openhands_config() {
    local openhands_dir="$SYS_HOME/.openhands"
    local settings_file="$openhands_dir/settings.json"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would configure OpenHands in $openhands_dir"
        return 0
    fi

    mkdir -p "$openhands_dir/profiles" "$openhands_dir/agent-profiles" "$openhands_dir/automation"

    if [[ -f "$settings_file" ]]; then
        local ts
        ts="$(date +%Y%m%d-%H%M%S)"
        cp "$settings_file" "${settings_file}.autoos-backup-${ts}"
    fi

    local code_root="$SYS_HOME/Documents/Code"
    if [[ -d "$SYS_HOME/Documents/code" ]]; then
        code_root="$SYS_HOME/Documents/code"
    fi
    local skills_source
    skills_source="$(autoos_skills_source)"
    local skills_target="$openhands_dir/skills"
    if [[ -n "$skills_source" && ! -e "$skills_target" ]]; then
        ln -s "$skills_source" "$skills_target" 2>/dev/null || true
        ui_ok "Linked agent-skills to OpenHands skills directory"
    fi

    local secrets_file="$code_root/agent-skills/secrets/api_keys.conf"

    catalog_require_python || return 0

    local models_file
    models_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/catalog/llm-models.json"

    local ollama_url
    ollama_url="$(resolve_ollama_base_url)"

    OLLAMA_BASE_URL="$ollama_url" AUTOOS_OMNIROUTE_KEY="${AUTOOS_OMNIROUTE_KEY:-}" python3 - "$openhands_dir" "$secrets_file" "$models_file" <<'PY'
import os, sys, json

openhands_dir = sys.argv[1]
secrets_file = sys.argv[2] if len(sys.argv) > 2 else ""
models_file = sys.argv[3] if len(sys.argv) > 3 else ""

with open(models_file, "r", encoding="utf-8") as _mf:
    REPO_MODELS = json.load(_mf)["models"]
REPO_BY_ID = {m["id"]: m for m in REPO_MODELS}
# resolve_ollama_base_url's answer; empty keeps the catalog default. Applied to
# the catalog entry so the profiles and the settings.json fallback agree.
if os.environ.get("OLLAMA_BASE_URL"):
    REPO_BY_ID["ollama-qwen2.5-coder"]["direct"]["base_url"] = os.environ["OLLAMA_BASE_URL"]
# OpenHands reaches Ollama through LiteLLM, whose ollama routes append /api/... to the base:
# the OpenAI-compatible /v1 base (right for OpenCode) gives 404 there (measured 2026-09-19).
# ollama_chat/ uses /api/chat, which supports tool calls. OpenHands only; OpenCode keeps /v1.
_ol = REPO_BY_ID["ollama-qwen2.5-coder"]["direct"]
_ol["model"] = "ollama_chat/" + _ol["model"].split("/", 1)[1]
_ol["base_url"] = _ol["base_url"].rstrip("/")
if _ol["base_url"].endswith("/v1"):
    _ol["base_url"] = _ol["base_url"][:-3]

def _read_secrets_file(path, secrets):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    # First occurrence of a key wins.
                    secrets.setdefault(k.strip().lower(), v.strip().strip("\"'"))
    except Exception:
        pass


def _profile_for(mid, key, name=None):
    m = REPO_BY_ID[mid]
    profile_name = (name or mid) + ".json"
    # Vendored structural template (openhands/profiles/<name>.json): model id,
    # base_url, auth shape and the thinking flags. Token windows, prices and
    # the api_key are projected below, so the catalog stays the single source
    # of truth for everything numeric.
    p = dict(TEMPLATES.get(profile_name, {}))
    if m.get("openrouter_id"):
        model = "openrouter/" + m["openrouter_id"]
    else:
        model = m["direct"]["model"]
    p.update({"model": model, "max_input_tokens": m["context"],
              "max_output_tokens": m["output"],
              "input_cost_per_token": m["input_price"],
              "output_cost_per_token": m["output_price"]})
    if m.get("direct", {}).get("base_url"):
        p["base_url"] = m["direct"]["base_url"]
    elif "base_url" in p and m.get("openrouter_id"):
        # OpenRouter endpoints resolve server-side; never persist a stale URL.
        del p["base_url"]
    if m.get("reasoning"):
        p["reasoning_effort"] = "high"
    else:
        # Explicit opt-out. The OpenHands SDK LLM model defaults to
        # reasoning_effort=high + encrypted reasoning + a 200k thinking
        # budget, and any sparse profile is materialized through those
        # defaults. Non-thinking providers (Ollama) hard-fail such requests
        # with '"model" does not support thinking'.
        p["reasoning_effort"] = "none"
        p["enable_encrypted_reasoning"] = False
        p["extended_thinking_budget"] = None
    for opt in ("paid_input_price", "paid_output_price", "cache_read_price"):
        dst = {"paid_input_price": "paid_input_cost_per_token",
               "paid_output_price": "paid_output_cost_per_token",
               "cache_read_price": "cache_read_cost_per_token"}[opt]
        if m.get(opt) is not None:
            p[dst] = m[opt]
    p["api_key"] = key
    return profile_name, p

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(models_file)))
# MCP package specs live in catalog/agent-harness.json, never inline.
with open(os.path.join(REPO_ROOT, "catalog", "agent-harness.json"), "r", encoding="utf-8") as _hf:
    MCP_PACKAGES = {k: v["package"] for k, v in json.load(_hf)["mcp_servers"].items()}
TEMPLATES = {}
_templates_dir = os.path.join(REPO_ROOT, "openhands", "profiles")
if os.path.isdir(_templates_dir):
    for _fn in os.listdir(_templates_dir):
        if _fn.endswith(".json"):
            try:
                with open(os.path.join(_templates_dir, _fn), "r", encoding="utf-8") as _tf:
                    TEMPLATES[_fn] = json.load(_tf)
            except Exception:
                pass

secrets = {}
if secrets_file and os.path.isfile(secrets_file):
    # Only the real conf. api_keys.conf.example is never read: its placeholder
    # keys are truthy and would make muse the default LLM with a key the
    # provider rejects, instead of falling through to the local Ollama model.
    _read_secrets_file(secrets_file, secrets)

muse_key = os.environ.get("MUSE_API_KEY") or secrets.get("muse")
deepseek_key = os.environ.get("DEEPSEEK_API_KEY") or secrets.get("deepseek")
openrouter_key = os.environ.get("OPENROUTER_API_KEY") or secrets.get("openrouter")
context7_key = os.environ.get("CONTEXT7_API_KEY") or secrets.get("context7")

settings_file = os.path.join(openhands_dir, "settings.json")
settings = {}
if os.path.isfile(settings_file):
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        settings = {}

settings.setdefault("schema_version", 2)
agent_settings = settings.setdefault("agent_settings", {})
agent_settings.setdefault("schema_version", 5)
agent_settings.setdefault("agent_kind", "openhands")
agent_settings.setdefault("agent", "CodeActAgent")

llm = agent_settings.setdefault("llm", {})
_muse = REPO_BY_ID["muse-spark"]["direct"]
_ds = REPO_BY_ID["deepseek-chat"]["direct"]
# reasoning_effort must follow the chosen default: only thinking models get
# "high". The unconditional "high" used to poison the local/Ollama fallback
# (and deepseek-chat) with thinking params Ollama rejects outright.
_default_reasoning = False
if muse_key:
    llm["model"] = _muse["model"]
    llm["base_url"] = _muse["base_url"]
    llm["api_key"] = muse_key
    _default_reasoning = bool(REPO_BY_ID["muse-spark"].get("reasoning"))
elif deepseek_key:
    llm["model"] = _ds["model"]
    llm["base_url"] = _ds["base_url"]
    llm["api_key"] = deepseek_key
    _default_reasoning = bool(REPO_BY_ID["deepseek-chat"].get("reasoning"))
elif openrouter_key:
    llm["model"] = "openrouter/openrouter/free"
    llm["base_url"] = "https://openrouter.ai/api/v1"
    llm["api_key"] = openrouter_key
    _default_reasoning = bool(REPO_BY_ID["openrouter-free"].get("reasoning"))
else:
    _local = REPO_BY_ID["ollama-qwen2.5-coder"]["direct"]
    llm["model"] = _local["model"]
    llm["base_url"] = _local["base_url"]
    _default_reasoning = bool(REPO_BY_ID["ollama-qwen2.5-coder"].get("reasoning"))

llm["max_input_tokens"] = 1048576
llm["max_output_tokens"] = 65536
if _default_reasoning:
    llm["reasoning_effort"] = "high"
else:
    llm["reasoning_effort"] = "none"
    llm["enable_encrypted_reasoning"] = False
    llm["extended_thinking_budget"] = None
llm["drop_params"] = True
llm["modify_params"] = True

agent_context = agent_settings.setdefault("agent_context", {})
agent_context["load_user_skills"] = True

mcp_cfg = agent_settings.setdefault("mcp_config", {})
mcp_cfg["serena"] = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["--from", MCP_PACKAGES["serena"], "serena", "start-mcp-server", "--context", "claude-code", "--open-web-dashboard", "false", "--enable-gui-log-window", "false"],
    "description": "Code navigation, symbol index, semantic editing",
    "enabled": True,
}
mcp_cfg["graphify"] = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["--from", MCP_PACKAGES["graphify"], "python", "-m", "graphify.serve", "graphify-out/graph.json"],
    "description": "Codebase knowledge graph and dependency intelligence",
    "enabled": True,
}
mcp_cfg["omnigraph"] = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", MCP_PACKAGES["omnigraph"]],
    "env": {"OMNIGRAPH_BASE_URL": "http://localhost:8080", "OMNIGRAPH_GRAPH_ID": "autoos"},
    "description": "Project memory graph for this repository (repo-scoped, not global)",
    "enabled": True,
}
ctx7_args = ["-y", MCP_PACKAGES["context7"]]
if context7_key:
    ctx7_args.extend(["--api-key", context7_key])
mcp_cfg["context7"] = {
    "transport": "stdio",
    "command": "npx",
    "args": ctx7_args,
    "description": "Upstash Context7 semantic search and retrieval",
    "enabled": True,
}
mcp_cfg["playwright"] = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", MCP_PACKAGES["playwright"]],
    "description": "Browser automation and end-to-end verification",
    "enabled": True,
}
mcp_cfg["cao-ops"] = {
    "transport": "stdio",
    "command": "bash",
    "args": ["-c", "export PATH=\"$HOME/.local/bin:$PATH\"; export CAO_HOME_DIR=\"$HOME/.cao\"; cao-ops-mcp-server"],
    "description": "CLI Agent Orchestrator 3-level coordination bridge",
    "enabled": True,
}
if "github" in mcp_cfg:
    del mcp_cfg["github"]

with open(settings_file, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)

profiles_dir = os.path.join(openhands_dir, "profiles")
# Prices are USD per token from catalog/llm-models.json. Free variants bill
# $0 while under the daily cap; paid_*_cost_per_token applies past it, so
# spend = in_tokens*in_price + out_tokens*out_price stays auditable.
profiles = dict([
    _profile_for("deepseek-chat", deepseek_key),
    _profile_for("deepseek-reasoner", deepseek_key),
    _profile_for("deepseek-v4-flash", deepseek_key),
    _profile_for("muse-spark", muse_key, "muse-spark-1.3"),
    _profile_for("muse-spark", muse_key, "muse-spark-1.3-contributor"),
    _profile_for("openrouter-free", openrouter_key),
    _profile_for("openrouter-nemotron-ultra", openrouter_key),
    _profile_for("openrouter-nemotron-super", openrouter_key),
    _profile_for("openrouter-nemotron-lightning", openrouter_key),
    _profile_for("openrouter-nemotron-nano-omni", openrouter_key),
    _profile_for("openrouter-laguna", openrouter_key),
    _profile_for("openrouter-laguna-xs", openrouter_key),
    _profile_for("openrouter-north-mini-code", openrouter_key),
    _profile_for("openrouter-nex-pro", openrouter_key),
    _profile_for("openrouter-nex-mini", openrouter_key),
    _profile_for("openrouter-inkling", openrouter_key),
    _profile_for("openrouter-inkling-small", openrouter_key),
    _profile_for("openrouter-dots3-note", openrouter_key),
    _profile_for("openrouter-ling-fin", openrouter_key),
    _profile_for("openrouter-ling-sante", openrouter_key),
    _profile_for("openrouter-ling-vl", openrouter_key),
    _profile_for("ollama-qwen2.5-coder", None),
])
# Legacy alias: older setups wrote ollama-qwen-coder.json and existing UI
# selections point at it. Keep it byte-identical to the canonical profile.
_alias_src, _alias_data = _profile_for("ollama-qwen2.5-coder", None)
profiles["ollama-qwen-coder.json"] = _alias_data
for name, p_data in profiles.items():
    with open(os.path.join(profiles_dir, name), "w", encoding="utf-8") as f:
        json.dump(p_data, f, indent=2)
omni_key = os.environ.get("AUTOOS_OMNIROUTE_KEY") or secrets.get("omniroute")
if omni_key:
    # Gateway-routed tier profiles for the 3-level hierarchy. These are NOT
    # catalog models (check-vendored covers repo-vendored profiles only), so
    # costs stay 0 - billing happens at the gateway, not per profile. The
    # openai/ prefix is the LiteLLM transport selector OpenHands requires;
    # without it: "LLM Provider NOT provided". Base URL is container-side.
    _gw = "http://host.docker.internal:20128/v1"
    for _tn, _tm, _tctx, _tout, _treason in [
            ("tier1", "openai/tier1", 1048576, 65536, True),
            ("tier2", "openai/tier2", 131072, 32768, False),
            ("tier3", "openai/tier3", 131072, 16384, False)]:
        _gp = {"auth_type": "api_key", "api_mode": "auto", "stream": False,
               "drop_params": True, "modify_params": True,
               "disable_stop_word": False, "caching_prompt": True,
               "log_completions": False, "native_tool_calling": True,
               "is_subscription": False, "capability_overrides": {},
               "litellm_extra_body": {}, "model": _tm, "base_url": _gw,
               "max_input_tokens": _tctx, "max_output_tokens": _tout,
               "input_cost_per_token": 0, "output_cost_per_token": 0,
               "api_key": omni_key}
        if _treason:
            _gp["reasoning_effort"] = "high"
        else:
            _gp["reasoning_effort"] = "none"
            _gp["enable_encrypted_reasoning"] = False
            _gp["extended_thinking_budget"] = None
        with open(os.path.join(profiles_dir, "autoos-%s.json" % _tn), "w", encoding="utf-8") as _ff:
            json.dump(_gp, _ff, indent=2)

agent_profiles_dir = os.path.join(openhands_dir, "agent-profiles")
# Vendored agent profiles (openhands/agent-profiles/*.json in the repo) are
# the desired state and are copied verbatim on every setup. Their
# llm_profile_ref values point at the canonical profile names written above.
# Role profiles are generated from the harness below, not copied: copying them
# here would race the generator and freeze the role's managed keys.
_role_profiles = {r['openhands']['profile'] + '.json' for r in json.load(open(os.path.join(REPO_ROOT, 'catalog', 'agent-harness.json'), encoding='utf-8'))['roles'].values()}
_vendored_agents = os.path.join(REPO_ROOT, "openhands", "agent-profiles")
if os.path.isdir(_vendored_agents):
    for _fn in sorted(os.listdir(_vendored_agents)):
        if not _fn.endswith(".json") or _fn in _role_profiles:
            continue
        try:
            with open(os.path.join(_vendored_agents, _fn), "r", encoding="utf-8") as _af:
                _a_data = json.load(_af)
            with open(os.path.join(agent_profiles_dir, _fn), "w", encoding="utf-8") as _of:
                json.dump(_a_data, _of, indent=2)
        except Exception:
            pass
PY

    # The role agent profiles are the generator's job: it merges the harness
    # into whatever the embedded script left, so the roles stay in one place.
    local harness_root harness_out harness_rc
    harness_root="${AUTOOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
    # "|| harness_rc=$?" keeps a failing generator from ending the run under set -e.
    harness_rc=0
    harness_out="$(python3 "$harness_root/lib/agent_harness.py" openhands --openhands-dir "$openhands_dir" --repo-root "$harness_root" 2>&1)" || harness_rc=$?
    if (( harness_rc != 0 )); then
        ui_warn "agent harness not applied to OpenHands (exit $harness_rc)"
    else
        while IFS= read -r _harness_line; do
            [[ -n "$_harness_line" ]] && ui_muted "$_harness_line"
        done <<< "$harness_out"
    fi

    ui_ok "OpenHands configuration and profiles written to $openhands_dir"
}

# ─── WSL agent home (native ext4) ───────────────────────────────────────────
# drvfs (/mnt/c, 9p) cannot host FIFOs or AF_UNIX sockets, so anything that
# creates named pipes or sockets must live on the native ext4 filesystem.
# Known victim: cli-agent-orchestrator's FIFO_DIR (os.mkfifo -> Errno 95
# EOPNOTSUPP when CAO_HOME_DIR sits under ~/.aws symlinked to /mnt/c).
# This function is a deliberate no-op off WSL: on bare metal ~/.aws is real
# ext4 and the default CAO home works fine.
setup_wsl_agent_home() {
    (( SYS_IS_WSL )) || { ui_muted "not WSL - native agent home not needed"; return 0; }
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would relocate CAO home to $SYS_HOME/.cao on native ext4"
        return 0
    fi
    local cao_native="$SYS_HOME/.cao"
    local cao_legacy="$SYS_HOME/.aws/cli-agent-orchestrator"
    local cao_home="${CAO_HOME_DIR:-$cao_native}"

    mkdir -p "$cao_native"
    # A drvfs-backed CAO home cannot host FIFOs: move live state (not logs or
    # locks) onto ext4, keep a timestamped backup, leave an empty dir behind
    # so the legacy path never dangles.
    if [[ -d "$cao_legacy" && ! -L "$cao_legacy" ]]; then
        if ! python3 -c "import os; os.mkfifo('$cao_legacy/.autoos-fifo-probe')" 2>/dev/null; then
            local ts backup
            ts="$(date +%Y%m%d-%H%M%S)"
            backup="${cao_legacy}.backup-${ts}"
            ui_info "CAO home $cao_legacy is on drvfs (no FIFO support) - relocating live state to $cao_native"
            cp -a "$cao_legacy" "$backup"
            for sub in agent-context agent-store db workflows skills profiles; do
                [[ -d "$cao_legacy/$sub" ]] && cp -a "$cao_legacy/$sub/." "$cao_native/$sub/"
            done
            [[ -f "$cao_legacy/settings.json" ]] && cp -a "$cao_legacy/settings.json" "$cao_native/settings.json"
            ui_ok "legacy CAO home backed up to $backup"
        else
            rm -f "$cao_legacy/.autoos-fifo-probe"
        fi
    fi
    # Every CAO entry point must see the same home: export once per shell.
    append_line_once "$SYS_HOME/.bashrc" "CAO_HOME_DIR" "export CAO_HOME_DIR=\"$cao_home\"  # added by AutoOS (wsl-agent-home)"
    append_line_once "$SYS_HOME/.profile" "CAO_HOME_DIR" "export CAO_HOME_DIR=\"$cao_home\"  # added by AutoOS (wsl-agent-home)"
    ui_ok "CAO home on native ext4: $cao_home (CAO_HOME_DIR exported)"
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
    local path="$1" profile="$2" selected="$3" installed="$4" skipped="$5" failed="$6" manual="${7:-}"
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
    python3 - "$path" "$profile" "$selected" "$installed" "$skipped" "$failed" "$answers_json" "$manual" <<'PY'
import json, sys, datetime
path, profile, selected, installed, skipped, failed, answers = sys.argv[1:8]
json.dump({
    "version": 1,
    "savedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "platform": "linux",
    "profile": profile,
    "selected": selected.split(),
    "answers": json.loads(answers or "{}"),
    "results": {"installed": installed.split(), "skipped": skipped.split(), "failed": failed.split(), "manual": sys.argv[8].split()},
}, open(path, "w", encoding="utf-8"), indent=2)
PY
    ui_ok "run state saved to $path"
}

# Sets STATE_PROFILE / STATE_SELECTED and fills AUTOOS_ANSWERS.
autoos_state_load() {
    local path="$1"
    [[ -f "$path" ]] || { ui_err "No state file at $path"; return 1; }
    catalog_require_python || return 1
    # tr -d '\r': a python3 invoked from a native Windows install (this repo
    # is tested from Git Bash as well as WSL2/Linux, per AGENTS.md §5) writes
    # CRLF line endings to a text-mode stdout even inside a Unix-style shell.
    # Left in, the \r rides along on the LAST field of every line - here,
    # every answer value - so a save/load round trip comes back with a value
    # that looks identical when printed but never equals the one that was
    # saved.
    while IFS=$'\x1f' read -r key value; do
        case "$key" in
            __profile)  STATE_PROFILE="$value" ;;
            __selected) STATE_SELECTED="$value" ;;
            *)          [[ -n "$key" ]] && AUTOOS_ANSWERS["$key"]="$value" ;;
        esac
    done < <(python3 - "$path" <<'PY' | tr -d '\r'
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
