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

# apt_repo_key_install <label> <url> <dest> <dearmor 0|1>
# Puts a vendor's apt signing key at <dest> (root-owned, 0644) and returns 0, or
# warns why not and returns 1. <dest> is trusted by the caller's guard on later
# runs, so it is written ONLY from a temp file that already holds a non-empty
# key: install_component runs an installer with errexit off, and the unchecked
# `curl | gpg --dearmor >tmp; install tmp dest` used to leave an EMPTY key that
# the old `-f` guard then trusted forever (apt failed on every later run).
# A non-empty key already in place is left alone; an empty one from such a
# run is replaced. <dearmor> 1 = the key is served ASCII-armored.
apt_repo_key_install() {
    local label="$1" url="$2" dest="$3" dearmor="$4"
    [[ -s "$dest" ]] && return 0
    local tmp need="curl"
    [[ "$dearmor" == 1 ]] && need="curl and gpg"
    tmp="$(mktemp)" || { ui_warn "${label} not installed: could not create a temp file for the apt signing key."; return 1; }
    if (
        # pipefail: a failed curl must fail the pipeline even though gpg
        # (which then reads nothing) is the last command in it.
        set -o pipefail
        if [[ "$dearmor" == 1 ]]; then curl -fsSL "$url" | gpg --dearmor; else curl -fsSL "$url"; fi
    ) >"$tmp" \
        && [[ -s "$tmp" ]] \
        && $AUTOOS_SUDO install -D -o root -g root -m 644 "$tmp" "$dest"; then
        rm -f "$tmp"
        return 0
    fi
    rm -f "$tmp"
    ui_warn "${label} not installed: could not fetch and install the apt signing key from ${url} (needs ${need} and network access); re-run once that works."
    return 1
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
        litellm)
            has_cmd litellm || [[ -x "$SYS_HOME/.local/bin/litellm" ]]
            ;;
        ai-stack-docker)
            # Installed = the compose stack owns the services (ai-stack.sh
            # wrote its stack.active marker after every service answered),
            # not merely pulled, and not a container a failed migrate left.
            bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/configuration/docker/ai-stack/ai-stack.sh" is-active >/dev/null 2>&1
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
        qodercli)        install_qodercli ;;
        devin-cli)       install_devin_cli ;;
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
    # Google's own apt repository (antigravity.google/download/linux), so no
    # download URL has to be pasted. Measured 2026-09-25: the repo is FROZEN at
    # 1.23.2 (Release dated 2026-04-16); the 2.x apps are tarball-only. Google
    # publishes no fingerprint for the signing key, so none is pinned here - a
    # fingerprint written down now would be our own unverified assertion.
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would add Google's signed apt repo (us-central1-apt.pkg.dev) and install antigravity (repo frozen at 1.23.2)"
        return 0
    fi
    # AUTOOS_APT_PREFIX is a test seam (DESTDIR-style): where the two files are
    # written. The source line keeps the /etc path apt itself will read.
    local prefix="${AUTOOS_APT_PREFIX:-}"
    local key=/etc/apt/keyrings/antigravity-repo-key.gpg
    local list=/etc/apt/sources.list.d/antigravity.list
    if [[ ! -f "${prefix}${key}" ]]; then
        # The .gpg name is misleading: the key is served ASCII-armored, so it
        # needs dearmoring before apt accepts it in signed-by. Each step is
        # checked - a curl failure must never leave an empty key that the
        # file guard above would then trust forever.
        local armored dearmored
        armored="$(mktemp)"; dearmored="$(mktemp)"
        if curl -fsSL -o "$armored" https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg \
            && gpg --dearmor <"$armored" >"$dearmored" \
            && [[ -s "$dearmored" ]] \
            && $AUTOOS_SUDO install -D -o root -g root -m 644 "$dearmored" "${prefix}${key}"; then
            rm -f "$armored" "$dearmored"
        else
            rm -f "$armored" "$dearmored"
            ui_err "Antigravity not installed: could not fetch and install Google's apt signing key."
            ui_muted "    Needs curl, gpg and network access to us-central1-apt.pkg.dev; re-run once that works."
            return 1
        fi
    fi
    if [[ ! -f "${prefix}${list}" ]]; then
        if ! printf 'deb [signed-by=%s] https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ antigravity-debian main\n' \
            "$key" | $AUTOOS_SUDO tee "${prefix}${list}" >/dev/null; then
            ui_err "Antigravity not installed: could not write ${list}."
            return 1
        fi
        APT_UPDATED=0   # the new repo has to be fetched before install
    fi
    ui_warn "Antigravity: Google's apt repo is frozen at 1.23.2 (Release dated 2026-04-16); the 2.x apps are tarball-only, so apt will not bring them."
    apt_update_once
    run $AUTOOS_SUDO apt-get install -y antigravity
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
    # AUTOOS_APT_PREFIX is a test seam (DESTDIR-style), as in install_antigravity:
    # where the files are written. The source line keeps the /etc path apt reads.
    local prefix="${AUTOOS_APT_PREFIX:-}"
    local key=/etc/apt/keyrings/packages.microsoft.gpg
    local list=/etc/apt/sources.list.d/vscode.list
    apt_repo_key_install "VS Code" https://packages.microsoft.com/keys/microsoft.asc "${prefix}${key}" 1 || return 1
    if [[ ! -f "${prefix}${list}" ]]; then
        printf 'deb [arch=amd64,arm64,armhf signed-by=%s] https://packages.microsoft.com/repos/code stable main\n' \
            "$key" | $AUTOOS_SUDO tee "${prefix}${list}" >/dev/null
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
    # The vendor script ends with `agy install`, which appends a PATH line to
    # these profiles (measured 2026-09-25) - keep the originals.
    local rc=0 f stamp; stamp="$(date +%Y%m%d-%H%M%S)"
    for f in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.profile" "$HOME/.config/fish/config.fish"; do
        [[ -f "$f" ]] && cp "$f" "$f.autoos-backup-$stamp"
    done
    bash "$tmp" || rc=$?
    rm -f "$tmp"
    return $rc
}

install_qodercli() {
    # Qoder CLI (Alibaba terminal coding agent). Same download-to-file bar
    # as install_agy(): the vendor publishes no checksum, so verify
    # non-empty + shebang and execute the FILE, never the pipe.
    # Headless use needs QODER_PERSONAL_ACCESS_TOKEN (api-keys.yml qoder_pat,
    # see docs/api-keys.md) - export it yourself; installers never duplicate
    # secrets into shell rcs.
    local url="https://qoder.com/install"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would download and run the Qoder CLI installer from $url"
        return 0
    fi
    ui_muted "downloading Qoder CLI installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -fsSL -o "$tmp" "$url"; then
        ui_warn "failed to download Qoder CLI installer from $url"
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_warn "Qoder CLI installer from $url does not look like a script - aborting"
        rm -f "$tmp"
        return 1
    fi
    local rc=0
    bash "$tmp" || rc=$?
    rm -f "$tmp"
    return $rc
}

install_devin_cli() {
    # Devin CLI (Cognition terminal coding agent). Same download-to-file bar
    # as install_agy(). Needs an interactive `devin auth login` afterwards;
    # the gateway side connects via `omniroute providers add devin-cli
    # --oauth` or the dashboard (the `devin` API-key provider lists no
    # models - broken upstream, see docs/api-keys.md).
    local url="https://cli.devin.ai/install.sh"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would download and run the Devin CLI installer from $url"
        return 0
    fi
    ui_muted "downloading Devin CLI installer from $url"
    local tmp; tmp="$(mktemp)"
    if ! curl -fsSL -o "$tmp" "$url"; then
        ui_warn "failed to download Devin CLI installer from $url"
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" || "$(head -c2 -- "$tmp")" != '#!' ]]; then
        ui_warn "Devin CLI installer from $url does not look like a script - aborting"
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
    local prefix="${AUTOOS_APT_PREFIX:-}"   # test seam, see install_vscode
    local key=/etc/apt/keyrings/google-chrome.gpg
    local list=/etc/apt/sources.list.d/google-chrome.list
    apt_repo_key_install "Google Chrome" https://dl.google.com/linux/linux_signing_key.pub "${prefix}${key}" 1 || return 1
    if [[ ! -f "${prefix}${list}" ]]; then
        printf 'deb [arch=amd64 signed-by=%s] http://dl.google.com/linux/chrome/deb/ stable main\n' \
            "$key" | $AUTOOS_SUDO tee "${prefix}${list}" >/dev/null
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
        local prefix="${AUTOOS_APT_PREFIX:-}"   # test seam, see install_vscode
        local key=/etc/apt/keyrings/githubcli-archive-keyring.gpg
        local list=/etc/apt/sources.list.d/github-cli.list
        $AUTOOS_SUDO mkdir -p -m 755 "${prefix}/etc/apt/keyrings"
        # The keyring is already binary (dearmor 0): served as it is.
        apt_repo_key_install "GitHub CLI" https://cli.github.com/packages/githubcli-archive-keyring.gpg "${prefix}${key}" 0 || return 1
        local arch; arch="$(dpkg --print-architecture)"
        if [[ ! -f "${prefix}${list}" ]]; then
            printf 'deb [arch=%s signed-by=%s] https://cli.github.com/packages stable main\n' \
                "$arch" "$key" | $AUTOOS_SUDO tee "${prefix}${list}" >/dev/null
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

install_ai_stack() {
    # The server profile's AI services as hardened containers (compose.yml
    # next to ai-stack.sh; docs/web-services.md, Server profile). init only adds missing
    # env keys; `up` starts nothing on a port a native service holds - that
    # host moves with the opt-in `ai-stack.sh migrate --yes` instead.
    local stack line rc=0
    stack="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/configuration/docker/ai-stack/ai-stack.sh"
    if (( AUTOOS_DRY_RUN )); then
        while IFS= read -r line; do ui_muted "$line"; done < <(bash "$stack" --dry-run init 2>&1 || true)
        ui_muted "would build/pull the stack images and start them (ports held by native services are left alone)"
        return 0
    fi
    if ! has_cmd docker; then
        ui_warn "docker CLI not found - install Docker first, then re-run"
        return 1
    fi
    if ! docker info >/dev/null 2>&1; then
        ui_warn "Docker daemon not reachable (not running, or log in again for the docker group) - then: $stack up"
        return 1
    fi
    local step
    for step in init up; do
        while IFS= read -r line; do
            [[ "$line" == rc=* ]] && { rc="${line#rc=}"; continue; }
            ui_muted "$line"
        done < <(bash "$stack" "$step" 2>&1; echo "rc=$?")
        if (( rc != 0 )); then ui_warn "ai-stack.sh $step failed - see: $stack status"; return 1; fi
    done
    ui_ok "AI stack ready - status: $stack status"
}

install_litellm_proxy() {
    if has_cmd litellm; then ui_muted "litellm already installed"; return 0; fi
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install litellm[proxy] via uv tool (or pipx)"; return 0; fi
    # uv first: Ubuntu 24.04 / Debian 12 mark the system python as externally
    # managed (PEP 668), so `pip install --user` fails there by design. uv may
    # have been installed earlier in this same run, before PATH picked it up.
    local uv_bin=""
    if has_cmd uv; then uv_bin="uv"
    elif [[ -x "$SYS_HOME/.local/bin/uv" ]]; then uv_bin="$SYS_HOME/.local/bin/uv"
    fi
    local rc=1
    if [[ -n "$uv_bin" ]]; then
        "$uv_bin" tool install 'litellm[proxy]' && rc=0
    elif has_cmd pipx; then
        pipx install 'litellm[proxy]' && rc=0
    else
        ui_warn "neither uv nor pipx found - run: ./setup.sh --only uv,litellm --yes"
        return 1
    fi
    if (( rc != 0 )); then
        ui_warn "litellm install failed - manual step: uv tool install 'litellm[proxy]' (docs/models.md)"
        return 1
    fi
    ui_info "next: python3 tools/mirror-litellm-env.py (writes configuration/litellm/.env from api-keys.yml)"
    return 0
}

route_claude_to_gateway() {
    # Claude Code gateway routing is opt-in and reversible: catalog prompt
    # claude_gateway_routing, default "login". ANTHROPIC_BASE_URL/AUTH_TOKEN
    # in ~/.claude/settings.json "env" disable the claude.ai connectors, so
    #   gateway  merges both keys, pointing Claude Code at the local OmniRoute
    #            gateway (:20128). Needs AUTOOS_OMNIROUTE_KEY. Subscription
    #            models need the gateway `claude` OAuth connection first.
    #   login    (any other answer) removes exactly those two keys again;
    #            every other key stays, "env" goes only if it ends up empty.
    #            Needs no key.
    # Read-modify-write; a timestamped backup is taken only by a run that
    # changes the file, so a second run reports skipped and writes nothing.
    # The file (and every backup of it) can hold the token, so both are
    # mode 0600, and the new content lands via a fsynced temp file plus
    # os.replace - a crash mid-write must never leave an empty settings.json.
    # A dry run reads the file to say what it would do, and writes nothing.
    local cfg="$SYS_HOME/.claude/settings.json"
    local mode
    mode="$(answer claude_gateway_routing login)"
    # Trim the ends and lower-case, exactly like the PowerShell side:
    # " Gateway " is gateway, "gate way" is not (so it means login).
    mode="${mode#"${mode%%[![:space:]]*}"}"; mode="${mode%"${mode##*[![:space:]]}"}"
    mode="${mode,,}"
    [[ "$mode" == gateway ]] || mode=login
    if [[ "$mode" == gateway && -z "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then
        ui_warn "AUTOOS_OMNIROUTE_KEY not set - export the OmniRoute client key before pointing Claude Code at the gateway"
        return 0
    fi
    local status rc=0
    # The key travels in the environment only, never on a command line.
    status="$(AUTOOS_OMNIROUTE_KEY="${AUTOOS_OMNIROUTE_KEY:-}" \
        python3 - "$cfg" "$mode" "$AUTOOS_DRY_RUN" <<'PY'
import json, os, shutil, sys, tempfile, time
path, mode, dry = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
keys = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
url = "http://127.0.0.1:20128"
existed = os.path.isfile(path)
cfg = {}
if existed:
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = None
env = cfg.get("env") if isinstance(cfg, dict) else None
if not isinstance(cfg, dict) or not isinstance(env, (dict, type(None))):
    print("INVALID")  # never rewrite a file we cannot read back faithfully
    sys.exit(0)
if mode == "gateway":
    token = os.environ["AUTOOS_OMNIROUTE_KEY"]
    if env is not None and env.get(keys[0]) == url and env.get(keys[1]) == token:
        print("SKIPPED")
        sys.exit(0)
    if env is None:
        env = cfg["env"] = {}
    env[keys[0]] = url
    env[keys[1]] = token
    status = "ROUTED"
else:
    if env is None or not any(k in env for k in keys):
        print("SKIPPED")
        sys.exit(0)
    for k in keys:
        env.pop(k, None)
    if not env:
        del cfg["env"]
    status = "REMOVED"
if dry:
    print("WOULD_" + status)
    sys.exit(0)
folder = os.path.dirname(path)
os.makedirs(folder, exist_ok=True)
if existed:
    # Created 0600 from the start (shutil.copy2 would carry a 0644 over); the
    # chmod covers a same-second backup that already exists with another mode.
    backup = "%s.autoos-backup-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "rb") as src, \
         os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.chmod(backup, 0o600)
# mkstemp: same directory (so os.replace is atomic), O_EXCL, mode 0600.
fd, tmp = tempfile.mkstemp(dir=folder, prefix="settings.json.autoos-tmp-")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
except BaseException:
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise
print(status)
PY
)" || rc=$?
    if (( rc != 0 )); then
        ui_warn "could not update Claude Code settings in $cfg (exit $rc) - left as it was"
        return 0
    fi
    case "$mode:$status" in
        gateway:SKIPPED)     ui_ok "Claude Code already points at OmniRoute - skipped" ;;
        login:SKIPPED)       ui_ok "Claude Code already uses its claude.ai login (no gateway keys in $cfg) - skipped" ;;
        gateway:WOULD_ROUTED) ui_muted "would point Claude Code at OmniRoute in $cfg" ;;
        login:WOULD_REMOVED) ui_muted "would remove ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN from $cfg (Claude Code back on its claude.ai login)" ;;
        gateway:ROUTED)
            ui_ok "Claude Code points at OmniRoute ($cfg)"
            ui_warn "claude.ai connectors stay disabled while Claude Code routes through the gateway - answer claude_gateway_routing=login to undo"
            ;;
        login:REMOVED)       ui_ok "Claude Code gateway keys removed from $cfg - claude.ai login and connectors are back (backup kept)" ;;
        *:INVALID)           ui_warn "$cfg is not a JSON object - Claude Code settings left untouched, fix the file by hand" ;;
        *)                   ui_warn "unexpected result '$status' updating $cfg" ;;
    esac
}

route_detected_clis_to_gateway() {
    # Setup-time routing for pre-installed CLIs (postInstall only fires on
    # install, so without this step they would never point at the gateway).
    # Each step skips quietly when its CLI is absent; dry runs announce.
    # Keys bridge from the repo keys file when the env does not carry them
    # (same file-first pattern as the openhands writer below; never printed).
    # Without keys the claude step warns and the qwen step is skipped.
    if [[ -z "${OMNIROUTE_API_KEY:-}" || -z "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then
        _keys_yml="${AUTOOS_KEYS_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/configuration/api-keys.yml}"
        _file_key="$(python3 - "$_keys_yml" 2>/dev/null <<'PY'
import sys
try:
    found = ""
    with open(sys.argv[1], encoding="utf-8") as fh:
        for line in fh:
            t = line.strip()
            if t.startswith("omniroute:") and "REPLACE" not in t:
                found = t.split(":", 1)[1].strip().strip("\"'")
                break
    print(found)
except Exception:
    print("")
PY
)"
        if [[ -z "${OMNIROUTE_API_KEY:-}" && -n "$_file_key" ]]; then
            export OMNIROUTE_API_KEY="$_file_key"
        fi
        if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" && -n "$_file_key" ]]; then
            AUTOOS_OMNIROUTE_KEY="$_file_key"
        fi
        unset _file_key
    fi
    if has_cmd claude; then
        route_claude_to_gateway
    else
        ui_muted "Claude Code not installed - skipping gateway routing"
    fi
    if has_cmd qwen; then
        if ! has_cmd omniroute; then
            ui_warn "omniroute CLI not on PATH - cannot route Qwen Code"
        elif [[ -z "${OMNIROUTE_API_KEY:-}" ]]; then
            ui_warn "OMNIROUTE_API_KEY not set - export the OmniRoute client key before routing Qwen Code (docs/api-keys.md)"
        elif (( AUTOOS_DRY_RUN )); then
            ui_muted "would route Qwen Code at OmniRoute (model t2-worker)"
        else
            if [[ -f "$SYS_HOME/.qwen/settings.json" ]]; then
                cp "$SYS_HOME/.qwen/settings.json" "$SYS_HOME/.qwen/settings.json.autoos-backup-$(date +%Y%m%d-%H%M%S)"
            fi
            if ! omniroute setup-qwen --model t2-worker --yes >/dev/null 2>&1; then
                ui_warn "Qwen Code gateway routing failed - configure it by hand (docs/api-keys.md)"
            else
                ui_ok "Qwen Code routed at OmniRoute (model t2-worker)"
            fi
        fi
    else
        ui_muted "Qwen Code not installed - skipping gateway routing"
    fi
}

# ide_models_file: this checkout's catalog/ide-models.json, the one list of
# gateway models the Zed, OpenCode and OpenHands writers project.
# AUTOOS_IDE_MODELS_FILE overrides it (the suite points it at a broken copy).
ide_models_file() {
    printf '%s\n' "${AUTOOS_IDE_MODELS_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/catalog/ide-models.json}"
}

# ide_models_readable <file> <what happens instead>: 0 when <file> is a
# usable model list (every entry has id, name, context, output, surfaces).
# Otherwise ONE error line naming the file - never a traceback - and 1, so
# a writer stops before it backs up or touches anything.
ide_models_readable() {
    if python3 - "$1" >/dev/null 2>&1 <<'PY'
import json, sys
models = json.load(open(sys.argv[1], encoding="utf-8"))["models"]
ok = isinstance(models, list) and bool(models) and all(
    isinstance(m, dict) and isinstance(m.get("id"), str) and isinstance(m.get("name"), str)
    and isinstance(m.get("context"), int) and isinstance(m.get("output"), int)
    and isinstance(m.get("surfaces"), dict) for m in models)
sys.exit(0 if ok else 1)
PY
    then
        return 0
    fi
    ui_err "cannot use the gateway model list $1 (missing, not JSON, or an entry without id/name/context/output/surfaces) - $2"
    return 1
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
    local ide
    ide="$(ide_models_file)"
    ide_models_readable "$ide" "Zed settings left unchanged" || return 1
    mkdir -p "$cfg_dir"
    if [[ -f "$cfg" ]]; then
        cp "$cfg" "$cfg.autoos-backup-$(date +%Y%m%d-%H%M%S)"
    fi
    python3 - "$cfg" "$AUTOOS_HARNESS" "$ide" <<'PY'
import json, os, sys
path = sys.argv[1]
harness_file = sys.argv[2]
ide_file = sys.argv[3]
cfg = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
pins = json.load(open(harness_file, encoding="utf-8"))["mcp_servers"]
# Model ids, display names, context windows and membership come from
# catalog/ide-models.json at run time (single source) - never inline tiers
# here: the two Zed writers once drifted from every other surface.
# max_tokens is Zed's context window; reasoning_effort only where the
# catalog sets one.
with open(ide_file, encoding="utf-8") as fh:
    ide_models = json.load(fh)["models"]


def zed_models(gateway):
    out = []
    for m in ide_models:
        if "zed" not in m["surfaces"].get(gateway, []):
            continue
        entry = {"name": m["id"], "display_name": m["name"], "max_tokens": m["context"]}
        if m.get("reasoning_effort"):
            entry["reasoning_effort"] = m["reasoning_effort"]
        out.append(entry)
    return out


lm = cfg.setdefault("language_models", {})
oc = lm.setdefault("openai_compatible", {})
oc["autoos-omniroute"] = {
    "api_url": "http://127.0.0.1:20128/v1",
    "available_models": zed_models("omniroute"),
}
oc["autoos-litellm"] = {
    "api_url": "http://127.0.0.1:4000/v1",
    "available_models": zed_models("litellm"),
}
# MCP context servers for the agent panel (Settings -> AI -> MCP Servers
# shows their status dots). Serena resolves its project per workspace at
# call time (the agent activates by absolute path); graphify serves the
# cwd-relative graph; omnigraph/playwright/context7 mirror the harness
# pins. Pins match catalog/agent-harness.json.
ctx = cfg.setdefault("context_servers", {})
ctx["serena"] = {
    "command": "uvx",
    "args": ["--from", pins["serena"]["package"], "serena", "start-mcp-server"],
}
ctx["graphify"] = {
    "command": "uv",
    "args": ["run", "--with", pins["graphify"]["package"], "python",
             "-m", "graphify.serve", "graphify-out/graph.json"],
}
# The bridge exits at start-up without a base URL and a graph id ("Connection
# closed" in the panel). The token is never written here: Zed inherits it from
# the session env (~/.config/environment.d, see write_omnigraph_env).
ctx["omnigraph"] = {
    "command": "npx",
    "args": ["-y", pins["omnigraph"]["package"]],
    "env": {"OMNIGRAPH_BASE_URL": "http://localhost:8080", "OMNIGRAPH_GRAPH_ID": "autoos"},
}
ctx["playwright"] = {
    "command": "npx",
    "args": ["-y", pins["playwright"]["package"]],
}
ctx["context7"] = {
    "command": "npx",
    "args": ["-y", pins["context7"]["package"]],
}
_repo = os.path.dirname(os.path.dirname(os.path.abspath(harness_file)))
ctx["autoos-agent"] = {
    "command": "uv",
    "args": ["--quiet", "run", "--no-project", "--with", pins["autoos-agent"]["package"], "python",
             os.path.join(_repo, pins["autoos-agent"]["script"])],
}
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
    "enable_all_context_servers": True,
    "context_servers": {},
    "default_model": {"provider": "autoos-omniroute", "model": "t1-orchestrator"},
}
tp = agent.setdefault("tool_permissions", {})
tp["default"] = "allow"
# omniroute-first: the litellm proxy currently has zero healthy endpoints,
# so a default pointing at autoos-litellm/* is broken. Converge it to
# {autoos-omniroute, t1-orchestrator}; never touch a default already on omniroute/*.
_dm = agent.get("default_model")
if isinstance(_dm, dict) and str(_dm.get("provider", "")).startswith("autoos-litellm"):
    agent["default_model"] = {"provider": "autoos-omniroute", "model": "t1-orchestrator"}
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

# ─── Qoder MCP wiring ───────────────────────────────────────────────────────
# Qoder CLI has first-class MCP commands (mcp list / get / add-json / remove,
# with user|local|project scopes), so - exactly as with Claude Code's
# `claude mcp add` above - AutoOS drives those and NEVER hand-edits Qoder's own
# ~/.qoder/settings.json. Rewriting the client's JSON store to change one key is
# the "overwrite a config wholesale" failure this repository has shipped before.
#
# Qoder cannot ride the OmniRoute gateway the way Zed and opencode do: its
# Custom Models accept only a curated provider list (Alibaba Cloud Model
# Studio, DeepSeek, Z.ai, Kimi, MiniMax, Xiaomi MIMO) with no arbitrary
# OpenAI-compatible base URL, and the store is encrypted. There is therefore
# nothing to route and no provider/base-URL/api-key config is written here -
# this function wires MCP servers ONLY. (Do not rename it route_qoder_*: that
# would imply a gateway path that does not exist.)

qoder_mcp_has_server() {
    # `qodercli mcp list` prints one line per configured server with a
    # Connected/Disconnected status. Match the name as a whole word so the check
    # survives format changes; `mcp add-json` refuses to overwrite an existing
    # entry anyway, so a missed match only re-attempts and warns - it never
    # clobbers the user's config.
    local want="$1"
    has_cmd qodercli || return 1
    qodercli mcp list 2>/dev/null | grep -qwF -- "$want"
}

register_qoder_mcp_server() {
    # register_qoder_mcp_server <name> <json-body>
    # Adds one server at USER scope through Qoder's own command. Idempotent:
    # an already-registered server is reported and left alone, so a second run
    # says "skipped", never "installed" (AGENTS.md section 4).
    local name="$1" json="$2"
    if qoder_mcp_has_server "$name"; then
        ui_muted "Qoder MCP server '${name}' is already registered - left alone."
        return 0
    fi
    ui_muted "run: qodercli mcp add-json ${name} '<json>' -s user"
    local rc=0
    qodercli mcp add-json "$name" "$json" -s user || rc=$?
    if (( rc == 0 )); then
        ui_ok "registered Qoder MCP server '${name}' (user scope)"
    else
        ui_warn "could not register Qoder MCP server '${name}'"
    fi
    return 0
}

setup_qoder_mcp() {
    # postInstall for the qodercli catalog entry. Called with NO arguments by
    # run_post_install, on both the installed and the skipped path, so it must
    # be safe to run twice. Pins come from catalog/agent-harness.json via
    # mcp_package - never as literals here (the mcp-pins test greps lib/ for
    # them and fails).
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would register Qoder MCP servers (serena, graphify, playwright, context7) at user scope"
        return 0
    fi
    if ! has_cmd qodercli; then
        ui_warn "qodercli is not on PATH - cannot register Qoder MCP servers. Install qodercli first."
        return 0
    fi
    ui_info "Setting up Qoder MCP servers (user scope)"

    # omnigraph is deliberately NOT registered here. Its graph is
    # per-repository (OMNIGRAPH_GRAPH_ID for this repo) and this repository
    # already ships it at project scope in .mcp.json; a machine-global
    # user-scope omnigraph would pin the wrong graph for every other repo. The
    # four servers below are machine-global and safe at user scope.

    local pkg key spec

    pkg="$(mcp_package serena)"
    spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "uvx", "args": ["--from", sys.argv[1], "serena", "start-mcp-server", "--open-web-dashboard", "false", "--enable-gui-log-window", "false"]}))
' "$pkg")"
    register_qoder_mcp_server serena "$spec"

    pkg="$(mcp_package graphify)"
    spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "uv", "args": ["--quiet", "run", "--with", sys.argv[1], "python", "-m", "graphify.serve", "graphify-out/graph.json"]}))
' "$pkg")"
    register_qoder_mcp_server graphify "$spec"

    pkg="$(mcp_package playwright)"
    spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "npx", "args": ["-y", sys.argv[1]]}))
' "$pkg")"
    register_qoder_mcp_server playwright "$spec"

    # context7 takes an API key only when one is available; without it the
    # server still runs on the default rate limit, so register it keyless
    # rather than skipping (mirrors install_mcp_context7).
    pkg="$(mcp_package context7)"
    key="$(answer context7_api_key "${CONTEXT7_API_KEY:-}")"
    if [[ -n "$key" ]]; then
        spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "npx", "args": ["-y", sys.argv[1], "--api-key", sys.argv[2]]}))
' "$pkg" "$key")"
    else
        spec="$(python3 -c '
import json, sys
print(json.dumps({"command": "npx", "args": ["-y", sys.argv[1]]}))
' "$pkg")"
    fi
    register_qoder_mcp_server context7 "$spec"
    return 0
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

# AutoOS's own repo runs the bridge through npx (.mcp.json); the agent-skills
# checkout and sibling repos run it as a container on the graph server's Docker
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
    if [[ -z "${OMNIGRAPH_TOKEN:-}" ]] && ! grep -qE '^OMNIGRAPH_TOKEN=.' "$SYS_HOME/.autoos-omnigraph.env" 2>/dev/null; then
        # Never invent one. An empty bearer fails as "missing bearer token",
        # which at least names itself; a made-up value fails as a 401 nobody can
        # explain — and this repository is public, so a real-looking secret in it
        # is a leak whether or not it happens to work.
        ui_warn "OMNIGRAPH_TOKEN is not set — the server will reject every call."
        ui_muted "    it is issued by the graph server, not by AutoOS. Add"
        ui_muted "    OMNIGRAPH_TOKEN=<token> to ${SYS_HOME}/.autoos-omnigraph.env (mode 600)."
        ready=0
    fi
    (( ready ))
}

# write_omnigraph_env <base-url>
# The per-user omnigraph env file, ~/.autoos-omnigraph.env, mode 600: the ONE
# place the bearer token lives on this machine. Tracked configs name the token
# (${OMNIGRAPH_TOKEN} in .mcp.json; opencode, Zed and OpenHands inherit it), so
# something must put the value into the env of whatever launches a client. A
# token exported only from an interactive ~/.zshrc reaches terminals and
# nothing else: systemd user services, a desktop-launched Zed and `bash -c`
# agents got none, still showed omnigraph "connected" (the bridge answers
# health without a token) and failed every read (measured 2026-09-24). So the
# file is
#   * linked as ~/.config/environment.d/60-autoos-omnigraph.conf, which the
#     systemd user manager and desktop sessions load at login, and
#   * sourced from ~/.bashrc / ~/.zshrc when OMNIGRAPH_TOKEN is not set yet.
# The token comes from $OMNIGRAPH_TOKEN, else from the local omnigraph-server
# container, else stays whatever the file already had. Never invented, never
# printed. Read-modify-write: other keys in the file are kept.
write_omnigraph_env() {
    local base="$1" file="$SYS_HOME/.autoos-omnigraph.env"
    local link="$SYS_HOME/.config/environment.d/60-autoos-omnigraph.conf"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would write ${file} (mode 600) and link it into ${link%/*}"
        return 0
    fi
    local token="${OMNIGRAPH_TOKEN:-}"
    if [[ -z "$token" ]] && has_cmd docker; then
        token="$(docker inspect omnigraph-server --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
            | sed -n 's/^OMNIGRAPH_SERVER_BEARER_TOKEN=//p' | head -n 1)" || token=""
    fi

    local status rc=0
    status="$(OMNI_BASE="$base" OMNI_TOKEN="$token" python3 - "$file" <<'PY'
import os, shutil, sys, time
path = sys.argv[1]
want = {"OMNIGRAPH_BASE_URL": os.environ["OMNI_BASE"]}
if os.environ.get("OMNI_TOKEN"):
    want["OMNIGRAPH_TOKEN"] = os.environ["OMNI_TOKEN"]
old = ""
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        old = f.read()
lines, seen = [], set()
for line in old.splitlines():
    key = line.split("=", 1)[0].strip()
    if key in want:
        if key in seen:
            continue
        line = "%s=%s" % (key, want[key])
        seen.add(key)
    lines.append(line)
lines += ["%s=%s" % (k, v) for k, v in want.items() if k not in seen]
new = "\n".join(lines) + "\n"
has_token = any(l.startswith("OMNIGRAPH_TOKEN=") and l.strip() != "OMNIGRAPH_TOKEN=" for l in lines)
if new == old:
    os.chmod(path, 0o600)
    print("unchanged", "token" if has_token else "no-token")
    sys.exit(0)
if old:
    backup = "%s.autoos-backup-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
tmp = path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(new)
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print("written", "token" if has_token else "no-token")
PY
)" || rc=$?
    if (( rc != 0 )); then
        ui_warn "could not write ${file} (exit ${rc})"
        return 0
    fi
    if [[ "$status" == unchanged* ]]; then
        ui_muted "omnigraph env file unchanged (${file})"
    else
        ui_ok "omnigraph env written to ${file} (mode 600)"
    fi
    if [[ "$status" == *no-token ]]; then
        ui_warn "OMNIGRAPH_TOKEN is not set and no local omnigraph-server holds one."
        ui_muted "    add OMNIGRAPH_TOKEN=<token issued by the graph server> to ${file}"
    fi

    mkdir -p "${link%/*}"
    if [[ -L "$link" && "$(readlink "$link")" == "$file" ]]; then
        :
    elif [[ -e "$link" || -L "$link" ]]; then
        ui_warn "${link} exists and is not AutoOS's link — left alone."
    else
        ln -s "$file" "$link"
        ui_ok "linked ${link} (systemd user services and desktop apps)"
    fi

    # Literal $HOME/${...}: this line is written into the rc file and expands
    # there, at shell start-up, not here. It READS the three keys literally
    # (export "$k=$v" never evaluates $v); the v1 line sourced the file, so a
    # value holding $(...) ran in every new shell (review finding 2026-09-25).
    # Works in bash and zsh; drops a trailing CR and reads a last line that has
    # no newline.
    # shellcheck disable=SC2016
    local rc_line
    rc_line="$(omnigraph_rc_line)"
    local shell_rc
    for shell_rc in "$SYS_HOME/.bashrc" "$SYS_HOME/.zshrc"; do
        [[ -f "$shell_rc" ]] || continue
        replace_or_append_marked_line "$shell_rc" "AutoOS:omnigraph-env" "AutoOS:omnigraph-env-v2" "$rc_line"
    done
}

omnigraph_rc_line() {
    # The rc-file line write_omnigraph_env installs (one place, so the suite
    # tests the exact text). Literal $HOME/${...}: it expands in the rc file at
    # shell start-up, not here.
    # shellcheck disable=SC2016
    printf '%s\n' '[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && while IFS= read -r _ag_l || [ -n "$_ag_l" ]; do _ag_l=${_ag_l%$'"'"'\r'"'"'}; case "$_ag_l" in OMNIGRAPH_TOKEN=*|OMNIGRAPH_BASE_URL=*|OMNIGRAPH_GRAPH_ID=*) export "${_ag_l%%=*}=${_ag_l#*=}" ;; esac; done < "$HOME/.autoos-omnigraph.env"; unset _ag_l  # AutoOS:omnigraph-env-v2'
}

replace_or_append_marked_line() {
    # replace_or_append_marked_line <file> <old marker> <new marker> <line>
    # Current line present -> nothing. A line with the OLD marker (and not the
    # new one) -> replaced in place, after a backup. Otherwise appended once.
    local file="$1" old_marker="$2" new_marker="$3" line="$4"
    if grep -qF -- "$new_marker" "$file" 2>/dev/null; then
        # A stale old line next to the current one still runs first: purge it.
        if grep -F -- "$old_marker" "$file" | grep -qvF -- "$new_marker"; then
            if (( AUTOOS_DRY_RUN )); then
                ui_muted "would remove the stale '${old_marker}' line from ${file}"
                return 0
            fi
            cp "$file" "${file}.autoos-backup-$(date +%Y%m%d-%H%M%S)"
            AUTOOS_OLD="$old_marker" AUTOOS_NEW="$new_marker" python3 - "$file" <<'PY'
import os, sys
path = sys.argv[1]
old, new = os.environ["AUTOOS_OLD"], os.environ["AUTOOS_NEW"]
with open(path, encoding="utf-8") as f:
    lines = f.read().split("\n")
lines = [l for l in lines if not (old in l and new not in l)]
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
PY
            ui_ok "removed the stale '${old_marker}' line from ${file}"
            return 0
        fi
        ui_muted "already configured (${new_marker}) in ${file}"
        return 0
    fi
    if ! grep -F -- "$old_marker" "$file" 2>/dev/null | grep -qvF -- "$new_marker"; then
        append_line_once "$file" "$new_marker" "$line"
        return 0
    fi
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would replace the '${old_marker}' line in ${file}"
        return 0
    fi
    cp "$file" "${file}.autoos-backup-$(date +%Y%m%d-%H%M%S)"
    AUTOOS_OLD="$old_marker" AUTOOS_LINE="$line" python3 - "$file" <<'PY'
import os, sys
path = sys.argv[1]
old, new = os.environ["AUTOOS_OLD"], os.environ["AUTOOS_LINE"]
with open(path, encoding="utf-8") as f:
    lines = f.read().split("\n")
lines = [new if (old in l) else l for l in lines]
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
PY
    ui_ok "replaced the '${old_marker}' line in ${file}"
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

    write_omnigraph_env "$base"

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
    # The agent spawner is declared in this repo's own .mcp.json.
    if [[ -n "${AUTOOS_ROOT:-}" && -f "$AUTOOS_ROOT/.mcp.json" ]]; then
        enable_project_mcp_server "$AUTOOS_ROOT" autoos-agent
    fi

    local omni_pkg omni_spec
    omni_pkg="$(mcp_package omnigraph)"
    omni_spec="$(python3 -c "
import json, os
# bridge 0.8 refuses to start without a graph id (there is no fallback graph
# any more), so an unset one pins this repo's graph like the other clients.
env_vars = {'OMNIGRAPH_BASE_URL': '$base',
            'OMNIGRAPH_GRAPH_ID': os.environ.get('OMNIGRAPH_GRAPH_ID') or 'autoos'}
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

    # Repo skills into project .claude/skills (Claude Code reads only that
    # dir). Symlinks, created at install time (never committed - see
    # .gitignore). Guarded: existing entries win.
    repo_root="${AUTOOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
    repo_skills="$repo_root/.agents/skills"
    repo_claude="$repo_root/.claude/skills"
    if [[ -d "$repo_skills" ]]; then
        if (( AUTOOS_DRY_RUN )); then
            ui_muted "would link repo skills into $repo_claude"
        else
            mkdir -p "$repo_claude"
            for s_dir in "$repo_skills"/*; do
                [[ -d "$s_dir" ]] || continue
                s_name="$(basename "$s_dir")"
                if [[ ! -e "$repo_claude/$s_name" ]]; then
                    ln -snf "$s_dir" "$repo_claude/$s_name" 2>/dev/null || ui_warn "could not link repo skill $s_name"
                fi
            done
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
    # Repo-vendored .agents/skills first (single source of truth, including
    # the native rewrites), external agent-skills clone second.
    local repo_root="${AUTOOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
    if [[ -d "$repo_root/.agents/skills" ]]; then
        printf '%s\n' "$repo_root/.agents/skills"
        return 0
    fi
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

# setup_opencode_config
# Merges the AutoOS providers (omniroute + litellm tiers, ollama, meta), MCP
# servers and harness into the user's GLOBAL OpenCode config, so tier routing
# works in every cwd. Both files are merged in place, never replaced: V2
# (@opencode/cli) reads opencode.json, V1 also config.json. Each file is read
# JSONC-tolerantly (comments, trailing commas); one that still does not parse
# is left untouched. A file is backed up only when this run changes it, so a
# second run changes nothing. Keys are {env:NAME} references, never values.
setup_opencode_config() {
    local config_dir="$SYS_HOME/.config/opencode"

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would merge the AutoOS providers into $config_dir/opencode.json and config.json"
        ui_muted "would apply the agent harness (catalog/agent-harness.json)"
        return 0
    fi
    ide_models_readable "$(ide_models_file)" "OpenCode configuration left unchanged" || return 0

    mkdir -p "$config_dir"
    if [[ -f "$config_dir/opencode.jsonc" ]]; then
        ui_muted "$config_dir/opencode.jsonc is yours and stays as is; AutoOS merges into opencode.json"
    fi
    local config_file snapshot merge_rc merged_first=0
    for config_file in "$config_dir/config.json" "$config_dir/opencode.json"; do
        # First V2 run on a V1 machine: opencode.json starts as a copy of the
        # merged config.json, as the old writer's cp did - but never replaces
        # an opencode.json that exists.
        local seeded=0
        if [[ "$config_file" == */opencode.json && ! -e "$config_file" ]] && (( merged_first )); then
            cp -p "$config_dir/config.json" "$config_file"
            seeded=1
        fi
        snapshot=""
        if [[ -f "$config_file" ]] && (( ! seeded )); then
            snapshot="$(mktemp)"
            cp -p "$config_file" "$snapshot"
        fi
        # V2 reads opencode.json's `providers` block; V1 reads config.json.
        local v2_source=""
        if [[ "$config_file" == */opencode.json ]] && opencode_is_v2; then
            # Anchored off this file, like models_file: never the caller's cwd.
            v2_source="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/opencode.jsonc"
        fi
        merge_rc=0
        AUTOOS_OPENCODE_V2_SOURCE="$v2_source" _opencode_merge_config "$config_file" || merge_rc=$?
        if (( merge_rc == 3 )); then
            ui_warn "$config_file is not valid JSON or JSONC - left alone (fix it, then re-run)"
            [[ -n "$snapshot" ]] && rm -f "$snapshot"
            continue
        elif (( merge_rc != 0 )); then
            ui_warn "OpenCode configuration not written to $config_file (exit $merge_rc)"
            [[ -n "$snapshot" ]] && rm -f "$snapshot"
            continue
        fi

        # Merge the shared agent harness (roles, skills link) after the config
        # is written. Judge by exit code only; the generator's notes are muted.
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

        [[ "$config_file" == */config.json ]] && merged_first=1
        if [[ -z "$snapshot" ]]; then
            ui_ok "OpenCode configuration written to $config_file"
        elif cmp -s "$snapshot" "$config_file"; then
            rm -f "$snapshot"
            ui_muted "$config_file already current"
        else
            local backup
            backup="${config_file}.autoos-backup-$(date +%Y%m%d%H%M%S)"
            mv "$snapshot" "$backup"
            ui_ok "OpenCode configuration merged into $config_file (backup: $backup)"
        fi
    done
    return 0
}

# opencode_is_v2: the installed `opencode` is the V2 CLI (@opencode/cli).
opencode_is_v2() {
    has_cmd opencode || return 1
    [[ "$(opencode --version 2>/dev/null)" =~ (^|[^0-9.])v?2\. ]]
}

# _opencode_merge_config <file>: the provider/MCP merge for one file.
# Exit 3 = the existing file does not parse (left untouched).
_opencode_merge_config() {
    local config_file="$1"

    local secrets_file="$SYS_HOME/Documents/Code/agent-skills/secrets/api_keys.conf"
    [[ -f "$secrets_file" ]] || secrets_file="$SYS_HOME/Documents/code/agent-skills/secrets/api_keys.conf"

    # catalog/llm-models.json is the single source of truth for model data.
    # The repo root is anchored off this script, never off the caller's cwd.
    local models_file
    models_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/catalog/llm-models.json"

    local ollama_url
    ollama_url="$(resolve_ollama_base_url)"

    local ide_file
    ide_file="$(ide_models_file)"

    OLLAMA_BASE_URL="$ollama_url" python3 -c "
import json, os, sys

config_path = sys.argv[1]
secrets_path = sys.argv[2]
models_file = sys.argv[3]
ide_file = sys.argv[4]

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

def _strip_jsonc(text):
    # Drop // and /* */ comments outside strings, then trailing commas.
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == '\\\\' and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == '\"':
                in_str = False
            i += 1; continue
        if c == '\"':
            in_str = True; out.append(c); i += 1; continue
        if text.startswith('//', i):
            j = text.find('\\n', i)
            i = n if j < 0 else j; continue
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2; continue
        out.append(c); i += 1
    import re as _re
    return _re.sub(r',(\\s*[}\\]])', r'\\1', ''.join(out))

data = {}
if os.path.isfile(config_path):
    with open(config_path, 'r', encoding='utf-8-sig') as f:
        _raw = f.read()
    if _raw.strip():
        try:
            data = json.loads(_strip_jsonc(_raw))
        except Exception:
            # Never start from {}: that would replace the user's whole config.
            sys.exit(3)
        if not isinstance(data, dict):
            sys.exit(3)
# The parsed original, serialised: a run that changes nothing must not
# rewrite the file at all. A byte compare alone cannot see that - the harness
# step writes the same document in its own formatting (AGENTS.md section 4).
_before = json.dumps(data) if os.path.isfile(config_path) else None

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

# Direct meta entry (muse-spark contributor) so the model the user asked to
# keep is selectable in opencode; the key stays out of the file.
_muse = REPO_BY_ID['muse-spark']['direct']
_muse_model_id = _muse['model'].split('/', 1)[1]
providers['meta'] = {
    'npm': _muse['npm'],
    'name': 'Meta',
    'options': {
        'baseURL': _muse['base_url'],
        'apiKey': '{env:META_API_KEY}'
    },
    'models': {
        _muse_model_id: {
            'name': REPO_BY_ID['muse-spark']['name'],
            'reasoning': True,
            'limit': {'context': REPO_BY_ID['muse-spark']['context'],
                      'output': REPO_BY_ID['muse-spark']['output']},
            'options': {'reasoningEffort': _muse['reasoning_effort']}
        }
    }
}

muse_key = os.environ.get('META_API_KEY') or os.environ.get('MUSE_API_KEY') or secrets.get('muse')
deepseek_key = os.environ.get('DEEPSEEK_API_KEY') or secrets.get('deepseek')
# NOTE: META_API_KEY is the canonical name (same as litellm .env + api-keys.yml
# meta:); MUSE_API_KEY stays as a legacy fallback. The muse key feeds
# _profile_for (muse-spark contributor below) and the direct meta provider.
# NOTE: the muse key feeds _profile_for (muse-spark contributor below) and the
# direct meta provider. No direct DEEPSEEK provider is emitted: tier routing
# (omniroute on :20128, litellm on :4000) is offered here GLOBALLY so every
# cwd gets the tiers.

# Gateway tiers from catalog/ide-models.json (single source; the same list
# tools/sync-ide-models.py writes into the repo opencode.jsonc). Never inline
# tier ids or windows here.
# setup_opencode_config checked the file (ide_models_readable) before this ran.
with open(ide_file, 'r', encoding='utf-8') as _imf:
    IDE_MODELS = json.load(_imf)['models']

def _gateway_tiers(gateway):
    return {m['id']: {'name': m['name'], 'limit': {'context': m['context'], 'output': m['output']}}
            for m in IDE_MODELS if 'opencode' in m['surfaces'].get(gateway, [])}

providers['omniroute'] = {
    'npm': '@ai-sdk/openai-compatible',
    'name': 'AutoOS OmniRoute gateway',
    'options': {
        'baseURL': 'http://127.0.0.1:20128/v1',
        'apiKey': '{env:AUTOOS_OMNIROUTE_KEY}'
    },
    'models': _gateway_tiers('omniroute')
}

providers['litellm'] = {
    'npm': '@ai-sdk/openai-compatible',
    'name': 'AutoOS LiteLLM fallback',
    'options': {
        'baseURL': 'http://127.0.0.1:4000/v1',
        'apiKey': '{env:LITELLM_MASTER_KEY}'
    },
    'models': _gateway_tiers('litellm')
}

openrouter_key = os.environ.get('OPENROUTER_API_KEY') or secrets.get('openrouter')
if openrouter_key:
    providers['openrouter'] = {
        'npm': '@ai-sdk/openai-compatible',
        'name': 'OpenRouter',
        'options': {
            'baseURL': 'https://openrouter.ai/api/v1',
            # A reference, never the value: the key stays in the environment
            # (configuration/litellm/.env for the served UI).
            'apiKey': '{env:OPENROUTER_API_KEY}'
        },
        'models': _openrouter_models()
    }

# Retired 2026-09-22: drop the direct deepseek provider a previous setup
# wrote, so a re-run converges instead of preserving it via the merge above.
# The meta provider (muse-spark contributor) above is intentional and stays.
for _dead in ('deepseek',):
    providers.pop(_dead, None)
data['provider'] = providers

if not data.get('model'):
    data['model'] = 'ollama/' + REPO_BY_ID['ollama-qwen2.5-coder']['direct']['model'].split('/', 1)[1]
elif data['model'].split('/', 1)[0] == 'deepseek' and 'deepseek' not in providers:
    # The default pointed at a removed direct provider (deepseek):
    # fall back to keyless local Ollama instead of leaving it dangling.
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

# Serena memory tools are always off (memory belongs to omnigraph+graphify).
# Read the tool list from the harness field at runtime, never as literals.
with open(_harness_file, 'r', encoding='utf-8') as _hf2:
    _mem_tools = json.load(_hf2)['mcp_servers']['serena']['memory_tools']
data['tools'] = {'serena_' + _t: False for _t in _mem_tools}

# V2 (@opencode/cli) ignores the V1 provider block above and reads
# providers (package/env/settings). Project the gateway entries from the
# repo's opencode.jsonc - the single source - so every cwd gets the tiers.
_v2_source = os.environ.get('AUTOOS_OPENCODE_V2_SOURCE')
if _v2_source and os.path.isfile(_v2_source):
    with open(_v2_source, 'r', encoding='utf-8-sig') as _vf:
        _repo = json.loads(_strip_jsonc(_vf.read()))
    _v2 = data.get('providers') if isinstance(data.get('providers'), dict) else {}
    for _name in ('omniroute', 'litellm'):
        if _name in _repo.get('providers', {}):
            _v2[_name] = _repo['providers'][_name]
    data['providers'] = _v2
    # The V1 default (local Ollama) has no V2 provider entry: point V2 at the
    # repo default instead, but only when it is still the AutoOS default.
    _ollama_default = 'ollama/' + REPO_BY_ID['ollama-qwen2.5-coder']['direct']['model'].split('/', 1)[1]
    if data.get('model') in (None, '', _ollama_default) and _repo.get('model'):
        data['model'] = _repo['model']

if _before is not None and json.dumps(data) == _before:
    sys.exit(0)
tmp_file = config_path + '.tmp'
with open(tmp_file, 'w', encoding='utf-8') as f:
    # ensure_ascii=False, as lib/agent_harness.py writes: the two writers
    # share this file, and an escaped-vs-literal em-dash in the tier names
    # made every second run a spurious merge with a fresh backup.
    json.dump(data, f, indent=2, ensure_ascii=False)
    # Trailing newline, as the harness writes it: otherwise every re-run
    # differs by one byte and takes a pointless backup.
    f.write('\\n')
os.replace(tmp_file, config_path)
" "$config_file" "$secrets_file" "$models_file" "$ide_file"
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

    # The gateway default takes its windows from the model catalog; a broken
    # one is reported here and only costs the default its windows.
    local ide_file
    ide_file="$(ide_models_file)"
    ide_models_readable "$ide_file" "the OpenHands default LLM gets no token windows" || ide_file=""

    OLLAMA_BASE_URL="$ollama_url" AUTOOS_OMNIROUTE_KEY="${AUTOOS_OMNIROUTE_KEY:-}" AUTOOS_IDE_MODELS="$ide_file" \
        python3 - "$openhands_dir" "$secrets_file" "$models_file" <<'PY'
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
    # keys are truthy and would be written into a profile as if real (401s).
    _read_secrets_file(secrets_file, secrets)

muse_key = os.environ.get("META_API_KEY") or os.environ.get("MUSE_API_KEY") or secrets.get("muse")
deepseek_key = os.environ.get("DEEPSEEK_API_KEY") or secrets.get("deepseek")
# NOTE: no direct Meta/DeepSeek provider is emitted by the writers; both keys
# feed _profile_for (muse-spark contributor + deepseek-v4-flash below).
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
# Target the docker image's supported version (measured: AGENT_SETTINGS_SCHEMA_VERSION=4
# in docker.openhands.dev/openhands/openhands:latest). Newer writers (agent-canvas 1.20
# writes 6) migrate forward on load, so 4 is readable by both; 5+ 500s the image's
# /api settings load. Only clamp DOWN: older payloads must keep their version so the
# image's own migrations still run.
agent_settings.setdefault("schema_version", 4)
if isinstance(agent_settings.get("schema_version"), int) and agent_settings["schema_version"] > 4:
    agent_settings["schema_version"] = 4
# No 'enabled' key on any MCP entry: the live MCPServer schema (additionalProperties
# false) rejects it with extra_forbidden and 500s /api/v1/settings. Presence in
# mcp_config means active. Strip it from inherited files (agent-canvas writes it).
for _srv in (agent_settings.get("mcp_config") or {}).values():
    if isinstance(_srv, dict):
        _srv.pop("enabled", None)
agent_settings.setdefault("agent_kind", "openhands")
agent_settings.setdefault("agent", "CodeActAgent")

llm = agent_settings.setdefault("llm", {})
# reasoning_effort must follow the chosen default: only thinking models get
# "high". The unconditional "high" used to poison the local/Ollama fallback
# with thinking params Ollama rejects outright.
_default_reasoning = False
# OmniRoute client key rides AUTOOS_OMNIROUTE_KEY (same env the tier-profile
# writer below reads). AUTOOS_KEYS_FILE overrides the fallback keys file
# (the suite points it at a stub for a hermetic keyless run).
_gw_key = os.environ.get("AUTOOS_OMNIROUTE_KEY")
if not _gw_key:
    # Fall back to the repo's single source of truth for keys.
    _keys_yml = os.environ.get("AUTOOS_KEYS_FILE") or os.path.join(os.path.dirname(os.path.dirname(models_file)), "configuration", "api-keys.yml")
    try:
        with open(_keys_yml, "r", encoding="utf-8") as _kf:
            for _line in _kf:
                _t = _line.strip()
                if _t.startswith("omniroute:") and "REPLACE" not in _t:
                    _gw_key = _t.split(":", 1)[1].strip().strip("\"'")
                    break
    except Exception:
        pass
if _gw_key:
    # Gateway default (mirrors the opencode t1 setup): the whole
    # 3-level hierarchy routes through OmniRoute, so OpenHands' own default
    # must too - otherwise the UI shows no usable agent and every chat
    # falls back to local Ollama. Container-side base URL.
    llm["model"] = "openai/t1-orchestrator"
    llm["base_url"] = "http://host.docker.internal:20128/v1"
    llm["api_key"] = _gw_key
    _default_reasoning = True
    # The gateway default IS the t1 tier: its windows come from
    # catalog/ide-models.json, like every other surface's. The installer
    # passes the path only when the file checked out (it reported otherwise).
    _default_window = None
    try:
        with open(os.environ.get("AUTOOS_IDE_MODELS") or "", "r", encoding="utf-8") as _imf:
            _default_window = next(m for m in json.load(_imf)["models"] if m["id"] == "t1-orchestrator")
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        pass
elif openrouter_key:
    llm["model"] = "openrouter/openrouter/free"
    llm["base_url"] = "https://openrouter.ai/api/v1"
    llm["api_key"] = openrouter_key
    _default_reasoning = bool(REPO_BY_ID["openrouter-free"].get("reasoning"))
    _default_window = REPO_BY_ID["openrouter-free"]
else:
    _local = REPO_BY_ID["ollama-qwen2.5-coder"]["direct"]
    llm["model"] = _local["model"]
    llm["base_url"] = _local["base_url"]
    _default_reasoning = bool(REPO_BY_ID["ollama-qwen2.5-coder"].get("reasoning"))
    _default_window = REPO_BY_ID["ollama-qwen2.5-coder"]

# The chosen default's own windows (catalog context/output), never one
# literal for all three: a 1M ceiling on the local 32k model overflowed it.
if _default_window:
    llm["max_input_tokens"] = _default_window["context"]
    llm["max_output_tokens"] = _default_window["output"]
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
# No "enabled" key on any entry: the live MCPServer schema
# (openhands.sdk.mcp.config, additionalProperties false) rejects it with
# extra_forbidden and 500s /api/v1/settings (measured 2026-09-21). Presence
# in mcp_config means active; the suite pins this.
mcp_cfg["serena"] = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["--from", MCP_PACKAGES["serena"], "serena", "start-mcp-server", "--context", "claude-code", "--open-web-dashboard", "false", "--enable-gui-log-window", "false"],
    "description": "Code navigation, symbol index, semantic editing",
}
mcp_cfg["graphify"] = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["--from", MCP_PACKAGES["graphify"], "python", "-m", "graphify.serve", "graphify-out/graph.json"],
    "description": "Codebase knowledge graph and dependency intelligence",
}
# The agent-server may run in a container or under systemd; neither sees a
# token exported from an interactive shell rc. Take it from the env, else the
# per-user 0600 file write_omnigraph_env keeps (this settings file already
# holds the LLM keys and is never tracked).
# Container-side URL, like llm.base_url above: this file is read by the
# OpenHands app and its sandbox containers, where localhost is the container
# itself. omnigraph-server is published on the host's :8080 and both
# containers resolve host.docker.internal (compose extra_hosts host-gateway).
omni_env = {"OMNIGRAPH_BASE_URL": "http://host.docker.internal:8080", "OMNIGRAPH_GRAPH_ID": "autoos"}
omni_token = os.environ.get("OMNIGRAPH_TOKEN", "")
if not omni_token:
    _omni_file = os.path.join(os.path.dirname(os.path.abspath(openhands_dir)), ".autoos-omnigraph.env")
    try:
        with open(_omni_file, encoding="utf-8") as _of:
            for _line in _of:
                if _line.startswith("OMNIGRAPH_TOKEN="):
                    omni_token = _line.split("=", 1)[1].strip()
    except OSError:
        pass
if omni_token:
    omni_env["OMNIGRAPH_TOKEN"] = omni_token
mcp_cfg["omnigraph"] = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", MCP_PACKAGES["omnigraph"]],
    "env": omni_env,
    "description": "Project memory graph for this repository (repo-scoped, not global)",
}
# The agent spawner: spawn/status/result/cancel for every agent client.
mcp_cfg["autoos-agent"] = {
    "transport": "stdio",
    "command": "uv",
    "args": ["--quiet", "run", "--no-project", "--with", MCP_PACKAGES["autoos-agent"], "python",
             os.path.join(REPO_ROOT, "tools", "autoos_agent_mcp.py")],
    "description": "Spawn AutoOS agents (opencode, claude, qwen, gemini, codex, agy, qoder) by task card",
}
ctx7_args = ["-y", MCP_PACKAGES["context7"]]
if context7_key:
    ctx7_args.extend(["--api-key", context7_key])
mcp_cfg["context7"] = {
    "transport": "stdio",
    "command": "npx",
    "args": ctx7_args,
    "description": "Upstash Context7 semantic search and retrieval",
}
mcp_cfg["playwright"] = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", MCP_PACKAGES["playwright"]],
    "description": "Browser automation and end-to-end verification",
}
mcp_cfg["cao-ops"] = {
    "transport": "stdio",
    "command": "bash",
    "args": ["-c", "export PATH=\"$HOME/.local/bin:$PATH\"; export CAO_HOME_DIR=\"$HOME/.cao\"; cao-ops-mcp-server"],
    "description": "CLI Agent Orchestrator 3-level coordination bridge",
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
    _profile_for("deepseek-v4-flash", deepseek_key),
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
for name, p_data in profiles.items():
    with open(os.path.join(profiles_dir, name), "w", encoding="utf-8") as f:
        json.dump(p_data, f, indent=2)
omni_key = os.environ.get("AUTOOS_OMNIROUTE_KEY") or secrets.get("omniroute")
# LiteLLM master key for the litellm-tier* fallback profiles: env first
# (LITELLM_MASTER_KEY, then the Zed-side AUTOOS_LITELLM_API_KEY), never argv.
_lit_key = os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("AUTOOS_LITELLM_API_KEY")
# A direct-provider tier (the effort-ladder surface, e.g. OpenRouter spark)
# carries its own key: it does NOT go through either gateway.
_or_key = os.environ.get("OPENROUTER_API_KEY") or secrets.get("openrouter")
_spec_file = os.path.join(REPO_ROOT, "configuration", "openhands", "tier-profiles.json") if REPO_ROOT else ""
if (omni_key or _lit_key or _or_key) and _spec_file and os.path.isfile(_spec_file):
    # Gateway-routed tier profiles for the 3-level hierarchy, read from the
    # spec (single source - never inline tiers here). These are NOT catalog
    # models (check-vendored covers repo-vendored profiles only). The openai/
    # prefix is the LiteLLM transport selector OpenHands requires; litellm-*
    # tiers address the :4000 fallback proxy by its plain model_name; a
    # `gateway: openrouter` tier names its own endpoint and key.
    try:
        with open(_spec_file, "r", encoding="utf-8") as _sf:
            _spec = json.load(_sf)
        _gw = _spec["gateway_base_url"]
        _lit_base = _spec.get("litellm_base_url", _gw)
        _gateway_keys = {"litellm": _lit_key, "openrouter": _or_key}
        for _t in _spec["tiers"]:
            _g = _t.get("gateway")
            if _g in _gateway_keys:
                _t_key = _gateway_keys[_g]
                _t_base = _t.get("base_url") if _g == "openrouter" else _lit_base
                _t_base = _t_base or _gw
            else:
                _t_key, _t_base = omni_key, _gw
            if not _t_key:
                continue
            _gp = {"auth_type": "api_key", "api_mode": "auto", "stream": False,
                   "drop_params": True, "modify_params": True,
                   "disable_stop_word": False, "caching_prompt": True,
                   "log_completions": False, "native_tool_calling": True,
                   "is_subscription": False, "capability_overrides": {},
                   "litellm_extra_body": {}, "model": _t["model"],
                   "base_url": _t_base,
                   "max_input_tokens": _t["max_input_tokens"],
                   "max_output_tokens": _t["max_output_tokens"],
                   "input_cost_per_token": 0, "output_cost_per_token": 0,
                   "api_key": _t_key}
            if _t.get("reasoning"):
                _gp["reasoning_effort"] = "high"
            else:
                _gp["reasoning_effort"] = "none"
                _gp["enable_encrypted_reasoning"] = False
                _gp["extended_thinking_budget"] = None
            with open(os.path.join(profiles_dir, "%s.json" % _t["id"]), "w", encoding="utf-8") as _ff:
                json.dump(_gp, _ff, indent=2)
    except Exception:
        pass

agent_profiles_dir = os.path.join(openhands_dir, "agent-profiles")
# Publish profiles into settings.json llm_profiles: the app NEVER reads
# profiles/*.json from disk (no glob in its settings code) - that directory
# is installer-managed desired state only. Without this merge the UI shows
# just the fossil Default profile no matter how many sidecars exist.
# Only installer-managed entries are written (never touch anything else in
# llm_profiles); active becomes omniroute-t1-orchestrator (or litellm-t1-orchestrator when only
# the fallback key is in play) only when the current selection is missing
# (None or dangling) - a live user selection is never yanked. The fossil
# Default (ollama fallback this installer wrote before any key existed) is
# refreshed to mirror the current default llm; anything else stays untouched.
_lp = settings.setdefault("llm_profiles", {})
_managed = _lp.setdefault("profiles", {})
for _fn in sorted(os.listdir(profiles_dir)):
    if not _fn.endswith(".json"):
        continue
    try:
        with open(os.path.join(profiles_dir, _fn), "r", encoding="utf-8") as _pf:
            _managed[_fn[:-5]] = json.load(_pf)
    except Exception:
        pass
if (omni_key and "omniroute-t1-orchestrator" in _managed) or (_lit_key and "litellm-t1-orchestrator" in _managed):
    _want_active = "omniroute-t1-orchestrator" if (omni_key and "omniroute-t1-orchestrator" in _managed) else "litellm-t1-orchestrator"
    if _lp.get("active") is None or _lp.get("active") not in _managed:
        _lp["active"] = _want_active
    _default_entry = _managed.get("Default")
    if isinstance(_default_entry, dict):
        _dm = _default_entry.get("model", "")
        if _dm.startswith("ollama/") or _dm.startswith("ollama_chat/"):
            _default_entry["model"] = llm.get("model")
            _default_entry["base_url"] = llm.get("base_url")
            _default_entry["api_key"] = llm.get("api_key")
with open(settings_file, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)
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
