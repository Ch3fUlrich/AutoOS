#!/usr/bin/env bash
# build-server profile: postInstall steps for a headless CI/agent-routing node.
# Split out of install.sh (its size-ratchet baseline may only shrink); like
# lib/linux/cost-gate.sh, install.sh keeps thin wrappers that source this file.
# ui_*, has_cmd, run, AUTOOS_SUDO, AUTOOS_DRY_RUN, SYS_USER, SYS_HOME are already
# in scope (install.sh is sourced after lib/linux/detect.sh + ui.sh, see setup.sh).

# install_data_disk_mount: format the secondary disk ext4 and mount at /mnt/data.
# Destructive (wipes the disk), so it only acts on an explicit confirmation and
# skips outright if something is already mounted there.
install_data_disk_mount() {
    local dev="${AUTOOS_DATA_DISK:-}" mnt="${AUTOOS_DATA_MOUNT:-/mnt/data}" label="data"
    if findmnt "$mnt" >/dev/null 2>&1; then
        ui_muted "$mnt is already mounted - skipping"
        return 0
    fi
    if [[ -z "$dev" ]]; then
        ui_err "set AUTOOS_DATA_DISK=/dev/sdX before installing this component - there is no default device"
        return 1
    fi
    if [[ ! -b "$dev" ]]; then
        ui_err "$dev not present - refusing"
        return 1
    fi
    if [[ "$(findmnt -no SOURCE / 2>/dev/null)" == "$dev"* ]]; then
        ui_err "$dev holds the running root filesystem - refusing"
        return 1
    fi
    if [[ -n "$(lsblk -ln -o MOUNTPOINT "$dev" 2>/dev/null | tr -d '[:space:]')" ]]; then
        ui_err "a partition of $dev is already mounted elsewhere - refusing"
        return 1
    fi
    # blkid exits 2 with no output when a device holds no recognisable
    # filesystem/label/partition table - that is the only case this proceeds
    # without an explicit wipe flag.
    local existing_fs=""
    existing_fs="$($AUTOOS_SUDO blkid -o value -s TYPE "$dev" 2>/dev/null)" || true
    if [[ -z "$existing_fs" ]]; then
        existing_fs="$($AUTOOS_SUDO blkid -o value -s PTTYPE "$dev" 2>/dev/null)" || true
    fi
    if [[ -n "$existing_fs" && "${AUTOOS_DATA_DISK_WIPE:-0}" != "1" ]]; then
        ui_err "$dev already holds a filesystem/partition table ($existing_fs) - set AUTOOS_DATA_DISK_WIPE=1 to erase it anyway"
        return 1
    fi
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would ERASE $dev, create one ext4 partition, and mount it at $mnt"
        return 0
    fi
    ui_warn "This will ERASE all data on $dev and create one ext4 partition mounted at $mnt."
    local answer=""
    read -r -p "Type the device name (${dev}) to confirm: " answer
    if [[ "$answer" != "$dev" ]]; then
        ui_muted "disk formatting skipped - typed name did not match ${dev}"
        return 0
    fi
    $AUTOOS_SUDO umount "${dev}"?* 2>/dev/null || true
    $AUTOOS_SUDO wipefs -a "$dev"
    $AUTOOS_SUDO parted -s "$dev" mklabel gpt
    $AUTOOS_SUDO parted -s "$dev" mkpart primary ext4 0% 100%
    $AUTOOS_SUDO partprobe "$dev"
    $AUTOOS_SUDO udevadm settle
    $AUTOOS_SUDO mkfs.ext4 -F -L "$label" "${dev}1"
    local uuid; uuid="$($AUTOOS_SUDO blkid -s UUID -o value "${dev}1")"
    [[ -n "$uuid" ]] || { ui_err "could not read UUID of ${dev}1"; return 1; }
    $AUTOOS_SUDO mkdir -p "$mnt"
    if ! grep -q "UUID=$uuid" /etc/fstab 2>/dev/null; then
        $AUTOOS_SUDO cp /etc/fstab "/etc/fstab.bak.$(date +%s)"
        printf 'UUID=%s %s ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2\n' "$uuid" "$mnt" |
            $AUTOOS_SUDO tee -a /etc/fstab >/dev/null
    fi
    $AUTOOS_SUDO systemctl daemon-reload
    $AUTOOS_SUDO mount "$mnt"
    $AUTOOS_SUDO chown "$SYS_USER:$SYS_USER" "$mnt"
    ui_ok "mounted $dev at $mnt (UUID=$uuid)"
}

# install_tty_desktop_kmscon: Nerd-Font kmscon on tty1-6, GNOME off by default,
# start-gnome/stop-gnome to toggle it on demand. Boots to multi-user.target.
install_tty_desktop_kmscon() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install kmscon on tty1-6, disable gdm autostart, and add start-gnome/stop-gnome"
        return 0
    fi
    if ! has_cmd kmscon; then
        $AUTOOS_SUDO apt-get install -y kmscon || { ui_warn "kmscon unavailable - leaving the default getty console"; return 0; }
    fi
    local font_src="$SYS_HOME/.local/share/fonts/JetBrainsMonoNF" font_dst="/usr/local/share/fonts/JetBrainsMonoNF"
    if [[ -d "$font_src" ]]; then
        $AUTOOS_SUDO install -d -m 0755 "$font_dst"
        find "$font_src" -maxdepth 1 -type f \( -name '*.ttf' -o -name '*.otf' \) -print0 |
            xargs -0 -I{} $AUTOOS_SUDO install -m 0644 {} "$font_dst/"
        $AUTOOS_SUDO fc-cache -f "$font_dst" >/dev/null
    fi
    $AUTOOS_SUDO install -d -m 0755 /etc/kmscon
    cat <<'KMSCON_CONF' | $AUTOOS_SUDO tee /etc/kmscon/kmscon.conf >/dev/null
font-engine=pango
font-name=JetBrainsMono Nerd Font Mono
font-size=12
font-dpi=96
term=xterm-256color
palette=soft-black
sb-size=10000
dpms-timeout=0
KMSCON_CONF
    $AUTOOS_SUDO rm -f /etc/systemd/system/autovt@.service /etc/systemd/system/autovt@tty*.service
    local n
    for n in 1 2 3 4 5 6; do
        $AUTOOS_SUDO systemctl enable "kmsconvt@tty$n.service" >/dev/null 2>&1 || true
        $AUTOOS_SUDO systemctl disable "getty@tty$n.service" >/dev/null 2>&1 || true
    done
    $AUTOOS_SUDO systemctl daemon-reload
    $AUTOOS_SUDO systemctl disable gdm.service >/dev/null 2>&1 || true
    $AUTOOS_SUDO systemctl set-default multi-user.target

    $AUTOOS_SUDO install -m 0755 /dev/stdin /usr/local/bin/start-gnome <<'START_GNOME'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then exec sudo -- "$0" "$@"; fi
systemctl is-active --quiet gdm.service && { echo "GNOME already running."; exit 0; }
systemd-run --quiet --collect --on-active=1 --unit="start-gnome-$$-$(date +%s)" /bin/sh -c '
    for n in 1 2 3 4 5 6; do systemctl stop "kmsconvt@tty$n.service" "getty@tty$n.service" 2>/dev/null || true; done
    systemctl start gdm.service'
echo "GDM start requested."
START_GNOME

    $AUTOOS_SUDO install -m 0755 /dev/stdin /usr/local/bin/stop-gnome <<'STOP_GNOME'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then exec sudo -- "$0" "$@"; fi
start_kmscons() {
    local n
    for n in 1 2 3 4 5 6; do
        systemctl is-enabled --quiet "kmsconvt@tty$n.service" && systemctl start "kmsconvt@tty$n.service" 2>/dev/null ||
            systemctl start "getty@tty$n.service" 2>/dev/null || true
    done
}
systemctl is-active --quiet gdm.service || { start_kmscons; exit 0; }
systemd-run --quiet --collect --on-active=1 --unit="stop-gnome-$$-$(date +%s)" /bin/sh -c '
    systemctl stop gdm.service
    for n in 1 2 3 4 5 6; do
        systemctl is-enabled --quiet "kmsconvt@tty$n.service" && systemctl start "kmsconvt@tty$n.service" 2>/dev/null ||
            systemctl start "getty@tty$n.service" 2>/dev/null || true
    done'
echo "GDM stop requested."
STOP_GNOME
    ui_ok "kmscon console on tty1-6; GNOME stays off until 'start-gnome' is run"
}

# install_ssh_tailscale_access: enable openssh-server, disable Tailscale's own
# SSH so there is exactly one auth path, open the firewall if ufw is active.
install_ssh_tailscale_access() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would enable openssh-server, disable 'tailscale ssh', and allow 22/tcp through ufw"
        return 0
    fi
    has_cmd tailscale && $AUTOOS_SUDO tailscale set --ssh=false 2>/dev/null || true
    $AUTOOS_SUDO apt-get install -y openssh-server
    if ! ss -tlnH 2>/dev/null | grep -qE '[:.]22[[:space:]]'; then
        if systemctl list-unit-files ssh.socket >/dev/null 2>&1; then
            $AUTOOS_SUDO systemctl enable --now ssh.socket
        else
            $AUTOOS_SUDO systemctl enable --now ssh
        fi
    fi
    if ufw status 2>/dev/null | grep -q '^Status: active'; then
        $AUTOOS_SUDO ufw allow 22/tcp
        $AUTOOS_SUDO ufw allow in on tailscale0
    fi
    ui_ok "sshd enabled; Tailscale SSH disabled"
}

# install_github_actions_runner: register this box as a self-hosted Actions
# runner for exactly one PRIVATE repo (GH_RUNNER_REPO=owner/repo; no org mode -
# a runner's blast radius on a personal box should never cover every repo at
# once, and a public repo's workflows can run on forks/arbitrary PRs, which
# must never land on hardware someone actually uses). gh must be authenticated.
# Optional GH_RUNNER_LABELS, default "self-hosted,<hostname>".
#
# The runner tarball is pinned to a known version and verified by SHA-256
# rather than "whatever /releases/latest returns today" - this repo is public
# and ships no vendor binaries, so the one thing it can safely commit to is a
# hash to check a download against.
_GH_RUNNER_VERSION="2.338.2"
_GH_RUNNER_SHA256_X64="baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40"
_GH_RUNNER_SHA256_ARM64="670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945"

install_github_actions_runner() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would register a self-hosted GitHub Actions runner for a private repo (needs GH_RUNNER_REPO)"
        return 0
    fi
    local repo="${GH_RUNNER_REPO:-}"
    if [[ -z "$repo" ]]; then
        ui_err "set GH_RUNNER_REPO=owner/repo before installing this component (no org-wide mode - see comment above)"
        return 1
    fi
    if [[ -n "${GH_RUNNER_ORG:-}" ]]; then
        ui_err "GH_RUNNER_ORG is not supported - a runner here attaches to one private repo only"
        return 1
    fi
    if ! has_cmd gh; then ui_err "gh is required - add it to this run first"; return 1; fi
    local visibility; visibility="$(gh api "repos/${repo}" --jq .visibility 2>/dev/null)"
    if [[ -z "$visibility" ]]; then
        ui_err "could not read visibility of ${repo} - check gh auth and the repo name"
        return 1
    fi
    if [[ "$visibility" != "PRIVATE" ]]; then
        ui_err "${repo} is ${visibility,,}, not private - refusing to attach a runner (forked/public-repo workflows must not run on this hardware)"
        return 1
    fi
    local dir="$SYS_HOME/actions-runner"
    if [[ -d "$dir/.runner" ]]; then
        ui_muted "a runner is already configured in $dir - skipping"
        return 0
    fi
    mkdir -p "$dir" && cd "$dir" || return 1
    local os="linux" arch sha256
    case "$(uname -m)" in
        x86_64)  arch="x64";   sha256="$_GH_RUNNER_SHA256_X64" ;;
        aarch64|arm64) arch="arm64"; sha256="$_GH_RUNNER_SHA256_ARM64" ;;
        *) ui_err "unsupported arch $(uname -m)"; return 1 ;;
    esac
    local ver="$_GH_RUNNER_VERSION"
    local tarball="actions-runner-${os}-${arch}-${ver}.tar.gz"
    if [[ ! -f "$tarball" ]]; then
        curl -fsSL -o "$tarball" \
            "https://github.com/actions/runner/releases/download/v${ver}/${tarball}"
    fi
    local got; got="$(sha256sum "$tarball" | awk '{print $1}')"
    if [[ "$got" != "$sha256" ]]; then
        ui_err "checksum mismatch for ${tarball}: expected ${sha256}, got ${got} - refusing to extract"
        rm -f "$tarball"
        return 1
    fi
    tar xzf "$tarball"
    local token; token="$(gh api -X POST "repos/${repo}/actions/runners/registration-token" --jq .token)"
    [[ -n "$token" ]] || { ui_err "could not obtain a runner registration token - check gh auth and repo access"; return 1; }
    local url="https://github.com/${repo}"
    local labels="${GH_RUNNER_LABELS:-self-hosted,$(hostname -s)}"
    ./config.sh --unattended --url "$url" --token "$token" \
        --name "$(hostname -s)" --labels "$labels" --work _work
    $AUTOOS_SUDO ./svc.sh install "$SYS_USER"
    $AUTOOS_SUDO ./svc.sh start
    ui_ok "registered and started a self-hosted runner for ${repo} (labels: $labels)"
}

# install_plangraph_omniroute_node: clone plangraph, install it in its own
# venv, and scaffold the spawner's benchmark catalog directory so this box can
# be a routing target. Does not write any gateway URL or API key - those are
# the operator's secrets and belong in the environment, not the catalog.
install_plangraph_omniroute_node() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would set up plangraph + its spawner catalog under ~/code/repos/plangraph"
        return 0
    fi
    local repo_dir="$SYS_HOME/code/repos/plangraph"
    mkdir -p "$(dirname "$repo_dir")"
    if [[ -d "$repo_dir/.git" ]]; then
        ui_muted "plangraph already cloned at $repo_dir"
    else
        git clone https://github.com/Ch3fUlrich/plangraph.git "$repo_dir"
    fi
    if [[ ! -x "$repo_dir/.venv/bin/python3" ]]; then
        python3 -m venv "$repo_dir/.venv"
    fi
    "$repo_dir/.venv/bin/pip" install -q --upgrade pip
    # plangraph ships no setup.py/pyproject.toml - it runs via PYTHONPATH=src
    # (see its README), so there is nothing here to `pip install -e`.
    "$repo_dir/.venv/bin/pip" install -q pytest
    if [[ -f "$repo_dir/requirements-ci.txt" ]]; then
        "$repo_dir/.venv/bin/pip" install -q -r "$repo_dir/requirements-ci.txt"
    fi
    mkdir -p "$SYS_HOME/.cache/plangraph"
    ui_ok "plangraph ready at $repo_dir (run with PYTHONPATH=src, e.g. PYTHONPATH=src ${repo_dir}/.venv/bin/python3 -m pytest tests -q)"
    ui_info "Set PLANGRAPH_GATEWAY_URL (and this host's provider credentials) in your shell env to let OmniRoute route work here - AutoOS does not invent or store those secrets."
}

# install_omniroute_gateway_selfhost: builds a loopback-only OmniRoute gateway
# from the operator's own fork (pinned branch+commit, never "latest"), as a
# fallback if the central gateway is unreachable. Not the primary route -
# install_omniroute_client_config / OMNIROUTE_BASE_URL still points at the
# central gateway by default. REQUIRE_API_KEY and APP_BIND_HOST are left at
# their secure-by-default values (false / 127.0.0.1) - this never becomes a
# second gateway reachable off this box.
_OMNIROUTE_FORK_REPO="https://github.com/Ch3fUlrich/OmniRoute.git"
_OMNIROUTE_FORK_BRANCH="autoos/v3.8.53"
_OMNIROUTE_FORK_SHA="ff1ad94196f20448b5e2e3a926a81470272ad9b0"

install_omniroute_gateway_selfhost() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would clone Ch3fUlrich/OmniRoute@${_OMNIROUTE_FORK_BRANCH} and build the loopback-only 'base' profile with docker compose"
        return 0
    fi
    if ! has_cmd docker; then ui_err "docker is required - add it to this run first"; return 1; fi
    local dir="$SYS_HOME/code/OmniRoute"
    if [[ -d "$dir/.git" ]]; then
        ui_muted "OmniRoute fork already cloned at $dir"
    else
        git clone --branch "$_OMNIROUTE_FORK_BRANCH" "$_OMNIROUTE_FORK_REPO" "$dir"
    fi
    local head; head="$(git -C "$dir" rev-parse HEAD)"
    if [[ "$head" != "$_OMNIROUTE_FORK_SHA" ]]; then
        ui_warn "OmniRoute checkout at $dir is at ${head} - pinned sha is ${_OMNIROUTE_FORK_SHA}; leaving it as-is (not force-resetting a checkout that may have local changes)"
    fi
    if [[ ! -f "$dir/.env" ]]; then
        cp "$dir/.env.example" "$dir/.env"
        local jwt_secret api_key_secret initial_password
        jwt_secret="$(openssl rand -base64 48)"
        api_key_secret="$(openssl rand -hex 32)"
        initial_password="$(openssl rand -base64 24)"
        python3 - "$dir/.env" "$jwt_secret" "$api_key_secret" "$initial_password" <<'PY'
import re, sys
path, jwt, apikey, pw = sys.argv[1:5]
with open(path) as f:
    text = f.read()
text = re.sub(r'(?m)^JWT_SECRET=.*$', f'JWT_SECRET={jwt}', text)
text = re.sub(r'(?m)^API_KEY_SECRET=.*$', f'API_KEY_SECRET={apikey}', text)
text = re.sub(r'(?m)^INITIAL_PASSWORD=.*$', f'INITIAL_PASSWORD={pw}', text)
with open(path, 'w') as f:
    f.write(text)
PY
        chmod 0600 "$dir/.env"
        ui_info "Generated JWT_SECRET/API_KEY_SECRET/INITIAL_PASSWORD into ${dir}/.env (mode 0600) - the dashboard password is in that file, AutoOS does not print secrets to its own log."
    else
        ui_muted "$dir/.env already exists - leaving it untouched"
    fi
    ( cd "$dir" && $AUTOOS_SUDO docker compose --profile base up -d --build )
    ui_ok "OmniRoute fallback gateway up on 127.0.0.1 (loopback only) - point OMNIROUTE_BASE_URL at it manually if the central gateway is down"
}

# install_omniroute_client_config: points this host's plangraph spawner at the
# central OmniRoute gateway, matching the pattern already used on other hosts
# (e.g. coding-vm's ~/.config/autoos/ai-stack/opencode.env). Needs
# OMNIROUTE_API_KEY set in the environment before this runs - AutoOS does not
# prompt for secrets over a non-interactive run, and never invents one.
install_omniroute_client_config() {
    local env_file="$SYS_HOME/.config/autoos/ai-stack/opencode.env"
    local base_url="${OMNIROUTE_BASE_URL:-https://omniroute.ohje.ooguy.com/v1}"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would write ${env_file} (mode 0600) with OMNIROUTE_BASE_URL and AUTOOS_OMNIROUTE_KEY"
        return 0
    fi
    if [[ -f "$env_file" ]] && grep -q '^AUTOOS_OMNIROUTE_KEY=' "$env_file" 2>/dev/null; then
        ui_muted "${env_file} already has a key - leaving it untouched"
        return 0
    fi
    local key="${OMNIROUTE_API_KEY:-}"
    if [[ -z "$key" ]]; then
        ui_err "set OMNIROUTE_API_KEY in your shell (not in a file this tool reads from disk) before installing this component - AutoOS will not prompt for or invent a gateway key"
        return 1
    fi
    mkdir -p "$(dirname "$env_file")"
    ( umask 077
      cat > "$env_file" <<EOF_ENV
OMNIROUTE_BASE_URL=${base_url}
AUTOOS_OMNIROUTE_KEY=${key}
EOF_ENV
    )
    chmod 0600 "$env_file"
    ui_ok "wrote ${env_file} (mode 0600) - source it before spawner launches, e.g. 'set -a; . ${env_file}; set +a'"
}

# install_gitleaks: pinned version + verified SHA-256, same rule as the
# GitHub Actions runner above - this repo's own CI gates expect gitleaks on
# PATH, and "whatever /releases/latest is today" is not something this public
# repo should commit anyone to running unchecked.
_GITLEAKS_VERSION="8.30.1"
_GITLEAKS_SHA256_X64="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
_GITLEAKS_SHA256_ARM64="e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"

install_gitleaks() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install gitleaks v${_GITLEAKS_VERSION} to ~/.local/bin, verified by SHA-256"
        return 0
    fi
    local arch sha256
    case "$(uname -m)" in
        x86_64) arch="x64"; sha256="$_GITLEAKS_SHA256_X64" ;;
        aarch64|arm64) arch="arm64"; sha256="$_GITLEAKS_SHA256_ARM64" ;;
        *) ui_err "unsupported arch $(uname -m)"; return 1 ;;
    esac
    local ver="$_GITLEAKS_VERSION"
    local tmp; tmp="$(mktemp -d)"
    local tarball="gitleaks_${ver}_linux_${arch}.tar.gz"
    curl -fsSL -o "$tmp/$tarball" \
        "https://github.com/gitleaks/gitleaks/releases/download/v${ver}/${tarball}"
    local got; got="$(sha256sum "$tmp/$tarball" | awk '{print $1}')"
    if [[ "$got" != "$sha256" ]]; then
        ui_err "checksum mismatch for ${tarball}: expected ${sha256}, got ${got} - refusing to extract"
        rm -rf "$tmp"
        return 1
    fi
    tar xzf "$tmp/$tarball" -C "$tmp" gitleaks
    install -d -m 0755 "$SYS_HOME/.local/bin"
    install -m 0755 "$tmp/gitleaks" "$SYS_HOME/.local/bin/gitleaks"
    rm -rf "$tmp"
    ui_ok "gitleaks v${ver} installed to ~/.local/bin/gitleaks"
}
