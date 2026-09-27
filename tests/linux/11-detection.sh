# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Detection ──────────────────────────────────────────────────────────────
describe "detection"

if it "detects Debian-like distributions"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/etc"
    cat >"$tmp/etc/os-release" <<'EOF'
ID=ubuntu
PRETTY_NAME="Ubuntu 22.04 LTS"
VERSION_ID="22.04"
VERSION_CODENAME="jammy"
ID_LIKE="debian"
EOF
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_DISTRO_ID" == "ubuntu" && "$SYS_DISTRO_NAME" == "Ubuntu 22.04 LTS" && "$SYS_IS_DEBIAN_LIKE" -eq 1 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect ubuntu as debian-like"
    rm -rf "$tmp"
fi

if it "detects WSL2 environment"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/proc/sys/kernel" "$tmp/run"
    cat >"$tmp/proc/version" <<'EOF'
Linux version 5.15.90.1-microsoft-standard-WSL2 (oe-user@oe-host) (x86_64-msft-linux-gcc (GCC) 9.3.0, GNU ld (GNU Binutils) 2.34.0.20200220) #1 SMP Fri Jan 27 02:56:13 UTC 2023
EOF
    cat >"$tmp/proc/sys/kernel/osrelease" <<'EOF'
5.15.90.1-microsoft-standard-WSL2
EOF
    mkdir -p "$tmp/run/WSL"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_IS_WSL" -eq 1 && "$SYS_WSL_VERSION" == "2" ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect WSL2"
    rm -rf "$tmp"
fi

if it "detects Raspberry Pi"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/proc/device-tree"
    echo -n "Raspberry Pi 4 Model B Rev 1.2" > "$tmp/proc/device-tree/model"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_IS_PI" -eq 1 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect Raspberry Pi"
    rm -rf "$tmp"
fi

if it "detects Docker container"; then
    tmp="$(mktemp -d)"
    touch "$tmp/.dockerenv"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_IS_CONTAINER" -eq 1 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect Docker container"
    rm -rf "$tmp"
fi

if it "detects non-Debian distributions"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/etc"
    cat >"$tmp/etc/os-release" <<'EOF'
ID=alpine
PRETTY_NAME="Alpine Linux v3.18"
VERSION_ID="3.18.2"
EOF
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        ID=""
        NAME=""
        ID_LIKE=""
        detect_system
        if [[ "$SYS_DISTRO_ID" == "alpine" && "$SYS_DISTRO_NAME" == "Alpine Linux v3.18" && "$SYS_IS_DEBIAN_LIKE" -eq 0 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to correctly detect alpine as non-debian-like"
    rm -rf "$tmp"
fi

if it "handles missing os-release gracefully"; then
    tmp="$(mktemp -d)"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        ID=""
        NAME=""
        ID_LIKE=""
        detect_system
        if [[ "$SYS_DISTRO_ID" == "unknown" && "$SYS_DISTRO_NAME" == "unknown" && "$SYS_IS_DEBIAN_LIKE" -eq 0 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to gracefully handle missing os-release"
    rm -rf "$tmp"
fi

if it "identifies the architecture"; then
    case "$SYS_ARCH" in x64|arm64|armhf) pass ;; *) fail "odd arch: $SYS_ARCH" ;; esac
fi

if it "suggests a valid profile"; then
    case "$(suggested_profile)" in
        workstation|ai-coding|light|server) pass ;;
        *) fail "invalid profile: $(suggested_profile)" ;;
    esac
fi

if it "a Raspberry Pi is offered the light profile"; then
    ( SYS_IS_PI=1; SYS_IS_CONTAINER=0; SYS_RAM_GB=8.0; SYS_CPU_CORES=4; SYS_IS_HEADLESS=1
      [[ "$(suggested_profile)" == "light" ]] ) && pass || fail "Pi did not map to light"
fi

if it "a big headless box is offered the server profile"; then
    ( SYS_IS_PI=0; SYS_IS_CONTAINER=0; SYS_RAM_GB=32.0; SYS_CPU_CORES=16; SYS_IS_HEADLESS=1
      [[ "$(suggested_profile)" == "server" ]] ) && pass || fail "expected server"
fi

if it "sudo is resolved into AUTOOS_SUDO exactly once"; then
    if (( SYS_IS_ROOT )); then assert_eq "$AUTOOS_SUDO" ""
    elif (( SYS_CAN_SUDO )); then assert_eq "$AUTOOS_SUDO" "sudo"
    else assert_eq "$AUTOOS_SUDO" ""; fi
fi

# "Is it already installed?" - PATH alone misses everything a user-scope
# installer, snap or flatpak drops outside it. Probed against a scratch HOME so
# the answers do not depend on what happens to be installed here.

if it "the bin directories PATH routinely omits are all probed"; then
    dirs="$( SYS_HOME=/home/nobody; _extra_bin_dirs )"
    missing=""
    for want in /home/nobody/.local/bin /snap/bin /usr/local/bin \
                /var/lib/flatpak/exports/bin /home/nobody/.local/share/flatpak/exports/bin; do
        [[ "$dirs" == *"$want"* ]] || missing="$missing $want"
    done
    if [[ -z "$missing" ]]; then pass; else fail "not probed:$missing"; fi
fi

if it "a command in ~/.local/bin is found when PATH does not list it"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/.local/bin"
    printf '#!/bin/sh\nexit 0\n' >"$tmp/.local/bin/autoos-probe"
    chmod +x "$tmp/.local/bin/autoos-probe"
    if ( SYS_HOME="$tmp"; PATH="/usr/bin:/bin"; has_bin autoos-probe ); then pass
    else fail "the home .local/bin directory was not searched"; fi
    rm -rf "$tmp"
fi

if it "a command that exists nowhere is never invented"; then
    tmp="$(mktemp -d)"
    if ( SYS_HOME="$tmp"; has_bin autoos-definitely-not-installed ); then
        fail "reported a command that does not exist"
    else pass; fi
    rm -rf "$tmp"
fi

if it "a user-scope gh install is detected without it being on PATH"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/.local/bin"
    printf '#!/bin/sh\nexit 0\n' >"$tmp/.local/bin/gh"
    chmod +x "$tmp/.local/bin/gh"
    if ( SYS_HOME="$tmp"; PATH="/usr/bin:/bin"; script_is_installed gh ); then pass
    else fail "gh in ~/.local/bin was not detected"; fi
    rm -rf "$tmp"
fi

if it "a script component that is genuinely absent stays absent"; then
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2123  # narrowing PATH is the point of this probe
    if ( SYS_HOME="$tmp"; PATH="$tmp/empty:$tmp/none"; script_is_installed xpipe ); then
        fail "xpipe reported installed with nothing on disk"
    else pass; fi
    rm -rf "$tmp"
fi

if it "the workstation profile carries git and the GitHub CLI on both platforms"; then
    if python3 - <<'PY'
import json, pathlib, sys
bad = []
for name in ("linux.json", "windows.json"):
    doc = json.loads((pathlib.Path("catalog") / name).read_text(encoding="utf-8"))
    have = {c["id"]: c.get("profiles", []) for cat in doc["categories"] for c in cat["components"]}
    for want in ("git", "gh"):
        if "workstation" not in have.get(want, []):
            bad.append(f"{name}:{want}")
if bad:
    print(f"not in the workstation profile: {bad}", file=sys.stderr)
    sys.exit(1)
PY
    then pass; else fail "git/gh missing from a workstation profile"; fi
fi

if it "windows asks for the same git identity linux already asks for"; then
    if python3 - <<'PY'
import json, pathlib, sys
root = pathlib.Path("catalog")
win = json.loads((root / "windows.json").read_text(encoding="utf-8"))
lin = json.loads((root / "linux.json").read_text(encoding="utf-8"))
missing = [k for k in ("git_user_name", "git_user_email") if k not in win.get("prompts", {})]
if missing:
    print(f"windows.json never asks {missing}", file=sys.stderr)
    sys.exit(1)
for key in ("git_user_name", "git_user_email"):
    if win["prompts"][key].get("question") != lin["prompts"][key].get("question"):
        print(f"{key} asks a different question on each platform", file=sys.stderr)
        sys.exit(1)
wired = [c for cat in win["categories"] for c in cat["components"]
         if "git_user_name" in (c.get("prompt") or "")]
if not wired:
    print("no windows component consumes git_user_name", file=sys.stderr)
    sys.exit(1)
if not all(c.get("postInstall") for c in wired):
    print("the git identity prompt has no post-install step to apply it", file=sys.stderr)
    sys.exit(1)
PY
    then pass; else fail "windows git identity is asked but never applied"; fi
fi

