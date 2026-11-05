#!/usr/bin/env bash
# build-server profile: postInstall steps for a headless CI/agent-routing node.
# Split out of install.sh (its size-ratchet baseline may only shrink); like
# lib/linux/cost-gate.sh, install.sh keeps thin wrappers that source this file.
# ui_*, has_cmd, run, AUTOOS_SUDO, AUTOOS_DRY_RUN, SYS_USER, SYS_HOME are already
# in scope (install.sh is sourced after lib/linux/detect.sh + ui.sh, see setup.sh).

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
        $AUTOOS_SUDO install -d -m 0755 "$font_dst" || { ui_err "required installation step failed"; return 1; }
        find "$font_src" -maxdepth 1 -type f \( -name '*.ttf' -o -name '*.otf' \) -print0 |
            xargs -0 -I{} $AUTOOS_SUDO install -m 0644 {} "$font_dst/" || { ui_err "font installation failed"; return 1; }
        $AUTOOS_SUDO fc-cache -f "$font_dst" >/dev/null || { ui_err "required installation step failed"; return 1; }
    fi
    $AUTOOS_SUDO install -d -m 0755 /etc/kmscon || { ui_err "required installation step failed"; return 1; }
    cat <<'KMSCON_CONF' | $AUTOOS_SUDO tee /etc/kmscon/kmscon.conf >/dev/null || { ui_err "console configuration failed"; return 1; }
font-engine=pango
font-name=JetBrainsMono Nerd Font Mono
font-size=12
font-dpi=96
term=xterm-256color
palette=soft-black
sb-size=10000
dpms-timeout=0
KMSCON_CONF
    $AUTOOS_SUDO rm -f /etc/systemd/system/autovt@.service /etc/systemd/system/autovt@tty*.service || { ui_err "required installation step failed"; return 1; }
    local n
    for n in 1 2 3 4 5 6; do
        $AUTOOS_SUDO systemctl enable "kmsconvt@tty$n.service" >/dev/null 2>&1 || true
        $AUTOOS_SUDO systemctl disable "getty@tty$n.service" >/dev/null 2>&1 || true
    done
    $AUTOOS_SUDO systemctl daemon-reload || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO systemctl disable gdm.service >/dev/null 2>&1 || true
    $AUTOOS_SUDO systemctl set-default multi-user.target || { ui_err "required installation step failed"; return 1; }

    $AUTOOS_SUDO install -m 0755 /dev/stdin /usr/local/bin/start-gnome <<'START_GNOME' || { ui_err "console configuration failed"; return 1; }
#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then exec sudo -- "$0" "$@"; fi
systemctl is-active --quiet gdm.service && { echo "GNOME already running."; exit 0; }
systemd-run --quiet --collect --on-active=1 --unit="start-gnome-$$-$(date +%s)" /bin/sh -c '
    for n in 1 2 3 4 5 6; do systemctl stop "kmsconvt@tty$n.service" "getty@tty$n.service" 2>/dev/null || true; done
    systemctl start gdm.service'
echo "GDM start requested."
START_GNOME

    $AUTOOS_SUDO install -m 0755 /dev/stdin /usr/local/bin/stop-gnome <<'STOP_GNOME' || { ui_err "console configuration failed"; return 1; }
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
    if has_cmd tailscale; then
        $AUTOOS_SUDO tailscale set --ssh=false || { ui_err "Tailscale SSH disable failed"; return 1; }
    fi
    $AUTOOS_SUDO apt-get install -y openssh-server || { ui_err "required installation step failed"; return 1; }
    if ! ss -tlnH 2>/dev/null | grep -qE '[:.]22[[:space:]]'; then
        if systemctl list-unit-files --no-legend ssh.socket | grep -q ssh.socket; then
            $AUTOOS_SUDO systemctl enable --now ssh.socket || { ui_err "required installation step failed"; return 1; }
        else
            $AUTOOS_SUDO systemctl enable --now ssh || { ui_err "required installation step failed"; return 1; }
        fi
    fi
    if ufw status 2>/dev/null | grep -q '^Status: active'; then
        $AUTOOS_SUDO ufw allow 22/tcp || { ui_err "required installation step failed"; return 1; }
        $AUTOOS_SUDO ufw allow in on tailscale0 || { ui_err "required installation step failed"; return 1; }
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
# Verify pinned downloads before extraction.
_GH_RUNNER_VERSION="2.338.2"
_GH_RUNNER_SHA256_X64="baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40"
_GH_RUNNER_SHA256_ARM64="670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945"

install_github_actions_runner() (
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
    local visibility; visibility="$(gh api "repos/${repo}" --jq .visibility 2>/dev/null)" || visibility=""
    if [[ -z "$visibility" ]]; then
        ui_err "could not read visibility of ${repo} - check gh auth and the repo name"
        return 1
    fi
    if [[ "${visibility,,}" != "private" ]]; then
        ui_err "${repo} is ${visibility,,}, not private - refusing to attach a runner (forked/public-repo workflows must not run on this hardware)"
        return 1
    fi
    local dir="$SYS_HOME/actions-runner"
    if [[ -f "$dir/.runner" ]]; then
        ui_muted "a runner is already configured in $dir - skipping"
        return 0
    fi
    mkdir -p "$dir" && cd "$dir" || { ui_err "required installation step failed"; return 1; }
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
            "https://github.com/actions/runner/releases/download/v${ver}/${tarball}" || { ui_err "runner download failed"; return 1; }
    fi
    local got; got="$(sha256sum "$tarball" | awk '{print $1}')" || { ui_err "required lookup failed"; return 1; }
    if [[ "$got" != "$sha256" ]]; then
        ui_err "checksum mismatch for ${tarball}: expected ${sha256}, got ${got} - refusing to extract"
        rm -f "$tarball"
        return 1
    fi
    tar xzf "$tarball" || { ui_err "runner extraction failed"; return 1; }
    local token; token="$(gh api -X POST "repos/${repo}/actions/runners/registration-token" --jq .token)" || { ui_err "required lookup failed"; return 1; }
    [[ -n "$token" ]] || { ui_err "could not obtain a runner registration token - check gh auth and repo access"; return 1; }
    local url="https://github.com/${repo}"
    local labels="${GH_RUNNER_LABELS:-self-hosted,$(hostname -s)}"
    ./config.sh --unattended --url "$url" --token "$token" \
        --name "$(hostname -s)" --labels "$labels" --work _work || { ui_err "runner registration failed"; return 1; }
    $AUTOOS_SUDO ./svc.sh install "$SYS_USER" || { ui_err "runner service installation failed"; return 1; }
    $AUTOOS_SUDO ./svc.sh start || { ui_err "runner service start failed"; return 1; }
    ui_ok "registered and started a self-hosted runner for ${repo} (labels: $labels)"
)

# install_plangraph_omniroute_node: clone plangraph, install it in its own
# venv, and scaffold the spawner's benchmark catalog directory so this box can
# be a routing target. Does not write any gateway URL or API key - those are
# the operator's secrets and belong in the environment, not the catalog.
_PLANGRAPH_SHA="ee033fa6aaec984b7732235eb2fd2970cb81f4f3"
install_plangraph_omniroute_node() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would set up plangraph + its spawner catalog under ~/code/repos/plangraph"
        return 0
    fi
    local repo_dir="$SYS_HOME/code/repos/plangraph"
    local lock="${AUTOOS_PLANGRAPH_REQUIREMENTS_LOCK:-}"
    [[ -f "$lock" ]] || { ui_err "set AUTOOS_PLANGRAPH_REQUIREMENTS_LOCK to a reviewed hash-locked requirements file"; return 1; }
    mkdir -p "$(dirname "$repo_dir")" || { ui_err "required installation step failed"; return 1; }
    if [[ -d "$repo_dir/.git" ]]; then
        ui_muted "plangraph already cloned at $repo_dir"
    else
        git clone https://github.com/Ch3fUlrich/plangraph.git "$repo_dir" || { ui_err "required installation step failed"; return 1; }
        git -C "$repo_dir" checkout --detach "$_PLANGRAPH_SHA" || { ui_err "plangraph checkout failed"; return 1; }
    fi
    local head; head="$(git -C "$repo_dir" rev-parse HEAD)" || { ui_err "plangraph revision read failed"; return 1; }
    [[ "$head" == "$_PLANGRAPH_SHA" ]] || { ui_err "plangraph revision differs from pin"; return 1; }
    if [[ -f "$repo_dir/.autoos-ready" ]]; then
        ui_muted "plangraph already configured"; return 0
    fi
    if [[ ! -x "$repo_dir/.venv/bin/python3" ]]; then
        python3 -m venv "$repo_dir/.venv" || { ui_err "required installation step failed"; return 1; }
    fi
    # plangraph ships no setup.py/pyproject.toml - it runs via PYTHONPATH=src
    # (see its README), so there is nothing here to `pip install -e`.
    "$repo_dir/.venv/bin/pip" install -q --require-hashes -r "$lock" || { ui_err "plangraph dependency installation failed"; return 1; }
    mkdir -p "$SYS_HOME/.cache/plangraph" || { ui_err "required installation step failed"; return 1; }
    touch "$repo_dir/.autoos-ready" || { ui_err "plangraph completion marker failed"; return 1; }
    ui_ok "plangraph ready at $repo_dir (run with PYTHONPATH=src, e.g. PYTHONPATH=src ${repo_dir}/.venv/bin/python3 -m pytest tests -q)"
    ui_info "Set PLANGRAPH_GATEWAY_URL (and this host's provider credentials) in your shell env to let OmniRoute route work here - AutoOS does not invent or store those secrets."
}

# Pinned, authenticated fallback; enforce loopback independently of fork defaults.
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
        git clone --branch "$_OMNIROUTE_FORK_BRANCH" "$_OMNIROUTE_FORK_REPO" "$dir" || { ui_err "required installation step failed"; return 1; }
    fi
    local head; head="$(git -C "$dir" rev-parse HEAD)" || { ui_err "required installation step failed"; return 1; }
    if [[ "$head" != "$_OMNIROUTE_FORK_SHA" ]]; then
        ui_err "OmniRoute checkout differs from the pinned revision - refusing to build"
        return 1
    fi
    if [[ ! -f "$dir/.env" ]]; then
        python3 - "$dir" <<'PY'
import os, pathlib, re, secrets, sys
directory = pathlib.Path(sys.argv[1])
text = (directory / '.env.example').read_text()
values = {key: secrets.token_urlsafe(48) for key in
          ('JWT_SECRET', 'API_KEY_SECRET', 'INITIAL_PASSWORD')}
values.update(APP_BIND_HOST='127.0.0.1', REQUIRE_API_KEY='true')
for key, value in values.items():
    text = re.sub(rf'(?m)^{key}=.*\n?', '', text) + f'{key}={value}\n'
fd = os.open(directory / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(text)
PY
        [[ $? == 0 ]] || { ui_err "could not create gateway secrets"; return 1; }
        ui_info "Generated JWT_SECRET/API_KEY_SECRET/INITIAL_PASSWORD into ${dir}/.env (mode 0600) - the dashboard password is in that file, AutoOS does not print secrets to its own log."
    else
        ui_muted "$dir/.env already exists - leaving it untouched"
    fi
    # A standalone compose document avoids merge rules retaining unsafe publishes.
    ( cd "$dir" && APP_BIND_HOST=127.0.0.1 REQUIRE_API_KEY=true \
      $AUTOOS_SUDO docker compose -f - up -d --build <<'COMPOSE'
services:
  omniroute:
    build: {context: ., target: runner-base}
    container_name: omniroute
    restart: unless-stopped
    env_file: .env
    environment: {APP_BIND_HOST: 127.0.0.1, REQUIRE_API_KEY: "true", DATA_DIR: /app/data}
    volumes: ["./data:/app/data"]
    ports:
      - "127.0.0.1:${DASHBOARD_PORT:-20128}:${DASHBOARD_PORT:-20128}"
      - "127.0.0.1:${API_PORT:-20129}:${API_PORT:-20129}"
      - "127.0.0.1:${LIVE_WS_PORT:-20132}:${LIVE_WS_PORT:-20132}"
COMPOSE
    ) || { ui_err "gateway compose failed"; return 1; }
    ui_ok "OmniRoute fallback gateway up on 127.0.0.1 (loopback only) - point OMNIROUTE_BASE_URL at it manually if the central gateway is down"
}

# Noninteractive client configuration; existing keys always win.
install_omniroute_client_config() {
    local env_file="$SYS_HOME/.config/autoos/ai-stack/opencode.env"
    local base_url="${OMNIROUTE_BASE_URL:-}"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would write ${env_file} (mode 0600) with OMNIROUTE_BASE_URL and AUTOOS_OMNIROUTE_KEY"
        return 0
    fi
    if [[ -f "$env_file" ]] && grep -q '^AUTOOS_OMNIROUTE_KEY=' "$env_file" 2>/dev/null; then
        ui_muted "${env_file} already has a key - leaving it untouched"
        return 0
    fi
    [[ ! -e "$env_file" && ! -L "$env_file" ]] || { ui_err "existing client configuration has no key - refusing"; return 1; }
    local key="${OMNIROUTE_API_KEY:-}"
    [[ -n "$base_url" ]] || { ui_err "set OMNIROUTE_BASE_URL before installing this component"; return 1; }
    if [[ -z "$key" ]]; then
        ui_err "set OMNIROUTE_API_KEY in your shell (not in a file this tool reads from disk) before installing this component - AutoOS will not prompt for or invent a gateway key"
        return 1
    fi
    mkdir -p "$(dirname "$env_file")" || { ui_err "required installation step failed"; return 1; }
    ( umask 077; set -o noclobber
      cat > "$env_file" <<EOF_ENV
OMNIROUTE_BASE_URL=${base_url}
AUTOOS_OMNIROUTE_KEY=${key}
EOF_ENV
    ) || { ui_err "client configuration write failed"; return 1; }
    ui_ok "wrote ${env_file} (mode 0600) - source it before spawner launches, e.g. 'set -a; . ${env_file}; set +a'"
}

# install_gitleaks: pinned version + verified SHA-256, same rule as the
# GitHub Actions runner above.
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
    if [[ -x "$SYS_HOME/.local/bin/gitleaks" ]] && [[ "$("$SYS_HOME/.local/bin/gitleaks" version)" == "$ver" ]]; then return 0; fi
    local tmp; tmp="$(mktemp -d)" || { ui_err "required lookup failed"; return 1; }
    local tarball="gitleaks_${ver}_linux_${arch}.tar.gz"
    curl -fsSL -o "$tmp/$tarball" \
        "https://github.com/gitleaks/gitleaks/releases/download/v${ver}/${tarball}" || { ui_err "gitleaks download failed"; rm -rf "$tmp"; return 1; }
    local got; got="$(sha256sum "$tmp/$tarball" | awk '{print $1}')" || { ui_err "required lookup failed"; return 1; }
    if [[ "$got" != "$sha256" ]]; then
        ui_err "checksum mismatch for ${tarball}: expected ${sha256}, got ${got} - refusing to extract"
        rm -rf "$tmp"
        return 1
    fi
    tar xzf "$tmp/$tarball" -C "$tmp" gitleaks && install -d -m 0755 "$SYS_HOME/.local/bin" &&
        install -m 0755 "$tmp/gitleaks" "$SYS_HOME/.local/bin/gitleaks" || { ui_err "gitleaks installation failed"; rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"
    ui_ok "gitleaks v${ver} installed to ~/.local/bin/gitleaks"
}
# shellcheck source=lib/linux/build-server-rust.sh
. "${AUTOOS_ROOT}/lib/linux/build-server-rust.sh"
# shellcheck source=lib/linux/build-server-disk.sh
. "${AUTOOS_ROOT}/lib/linux/build-server-disk.sh"
