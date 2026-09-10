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
        google-chrome)   install_google_chrome ;;
        bitwarden-chrome) install_bitwarden_chrome ;;
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

install_agy() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install Antigravity CLI via antigravity.google/cli/install.sh"
        return 0
    fi
    curl -fsSL https://antigravity.google/cli/install.sh | bash
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
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install uv via astral.sh/uv/install.sh"
        return 0
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
}

install_ollama() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install Ollama via ollama.com/install.sh"
        return 0
    fi
    curl -fsSL https://ollama.com/install.sh | sh
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
    while IFS= read -r line; do
        if [[ "$line" =~ ^PROFILE=(.*)$ ]]; then
            CONFIG_PROFILE="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^ANS_([^=]+)=(.*)$ ]]; then
            local k="${BASH_REMATCH[1]}"
            local v="${BASH_REMATCH[2]}"
            AUTOOS_ANSWERS["$k"]="$v"
        fi
    done < <(python3 -c "$py_script" "$cfg_path")
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
    if [[ -e "$dest" ]]; then ui_muted "nvim config already exists - leaving it alone"; return 0; fi
    if (( AUTOOS_DRY_RUN )); then ui_muted "would install the LazyVim starter into $dest"; return 0; fi
    git clone --depth 1 https://github.com/LazyVim/starter "$dest"
    rm -rf "$dest/.git"
    ui_ok "LazyVim starter installed"
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
    register_mcp_server serena user "$SYS_HOME" \
        uvx --from serena-agent serena start-mcp-server --open-web-dashboard false --enable-gui-log-window false

    local serena_home="$SYS_HOME/.serena"
    local spec
    spec="$(python3 -c "
import json
print(json.dumps({
    'command': 'uvx',
    'args': ['--from', 'serena-agent', 'serena', 'start-mcp-server', '--open-web-dashboard', 'false', '--enable-gui-log-window', 'false'],
    'env': {'SERENA_HOME': '$serena_home'},
    'excludeTools': ['onboarding', 'open_dashboard', 'initial_instructions', 'write_memory', 'read_memory', 'list_memories', 'delete_memory', 'rename_memory', 'edit_memory']
}))
")"
    register_antigravity_mcp_server serena "$spec"
}

install_mcp_graphify() {
    ui_info "Setting up Graphify MCP server (Claude Code + Antigravity)"
    register_mcp_server graphify user "$SYS_HOME" \
        uv --quiet run --with 'graphifyy[mcp]' python -m graphify.serve graphify-out/graph.json

    register_antigravity_mcp_server graphify '{"command":"uv","args":["--quiet","run","--with","graphifyy[mcp]","python","-m","graphify.serve","${workspaceFolder}/graphify-out/graph.json"]}'
}

install_mcp_playwright() {
    ui_info "Setting up Playwright MCP server (Claude Code + Antigravity)"
    register_mcp_server playwright user "$SYS_HOME" \
        npx -y @playwright/mcp@latest

    register_antigravity_mcp_server playwright '{"command":"npx","args":["-y","@playwright/mcp@latest"]}'
}

install_mcp_context7() {
    ui_info "Setting up Context7 MCP server (Claude Code + Antigravity)"
    local key
    key="$(answer context7_api_key "${CONTEXT7_API_KEY:-}")"
    if [[ -n "$key" ]]; then
        register_mcp_server context7 user "$SYS_HOME" \
            npx -y @upstash/context7-mcp --api-key "$key"
        local spec
        spec="$(python3 -c "
import json
print(json.dumps({'command': 'npx', 'args': ['-y', '@upstash/context7-mcp', '--api-key', '$key']}))
")"
        register_antigravity_mcp_server context7 "$spec"
    else
        register_mcp_server context7 user "$SYS_HOME" \
            npx -y @upstash/context7-mcp
        register_antigravity_mcp_server context7 '{"command":"npx","args":["-y","@upstash/context7-mcp"]}'
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

    local omni_spec
    omni_spec="$(python3 -c "
import json, os
env_vars = {'OMNIGRAPH_BASE_URL': '$base'}
if os.environ.get('OMNIGRAPH_GRAPH_ID'):
    env_vars['OMNIGRAPH_GRAPH_ID'] = os.environ['OMNIGRAPH_GRAPH_ID']
if os.environ.get('OMNIGRAPH_TOKEN'):
    env_vars['OMNIGRAPH_TOKEN'] = os.environ['OMNIGRAPH_TOKEN']
print(json.dumps({
    'command': 'npx',
    'args': ['-y', '@modernrelay/omnigraph-mcp'],
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
