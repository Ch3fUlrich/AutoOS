#!/usr/bin/env bash
# AutoOS offline package cache builder for the rescue USB stick (plan B19).
#
# rescue-bootstrap.sh needs apt to reach the internet, and the machines a
# rescue stick exists for are exactly the ones with no working network. So
# the stick can carry its own .deb cache under <stick_root>/rescue/debs,
# built ahead of time by imagecache_build() below and consumed at boot by
# templates/rescue-bootstrap.sh.
#
# The version trap this exists to avoid: packages must match the *live
# image's* release, never the machine building the cache. A build host on
# Ubuntu 24.04 producing a cache for a 26.04 ISO would yield packages that
# fail dependency resolution on the target — worse than no cache, because it
# fails after someone has already booted a broken rescue stick. The codename
# is therefore always read from the image itself (imagecache_codename),
# never hardcoded or passed in as a literal.
#
# No mmdebstrap/debootstrap/chroot is used or needed. apt is pointed at a
# throwaway "sandbox" directory via Dir::Etc::sourcelist / Dir::State::* /
# Dir::Cache, which installs nothing on the host building the cache. The
# sandbox's dpkg status is seeded from the image's own
# casper/minimal.standard.live.manifest.full so apt resolves against what the
# live session already has instead of a full dependency closure from empty.
#
# shellcheck shell=bash
#
# Sourced alongside lib/linux/ui.sh (ui_ok/ui_info/ui_warn/ui_err) and
# lib/linux/detect.sh (has_cmd, AUTOOS_SUDO) — like every other lib/linux/*.sh
# module, this file is never executed standalone and deliberately carries no
# top-level `set -e`/`set -u`: sourcing a script that sets shell options
# changes them for whoever sourced it too (setup.sh, tests/run-tests.sh), and
# none of catalog.sh/detect.sh/install.sh/ui.sh do this either. Every
# function below instead checks its own exit codes explicitly, quotes every
# expansion and uses `local` throughout.

# AUTOOS_SUDO is normally exported by lib/linux/detect.sh (empty when already
# root, "sudo" otherwise); default it here too so this file behaves when
# sourced on its own, e.g. by a test. No function below calls `sudo` directly.
AUTOOS_SUDO="${AUTOOS_SUDO:-}"

# imagecache_codename <stick_root>
# Prints the release codename derived from the stick's own .disk/info, e.g.
#   Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)
# becomes "resolute". Never hardcode a codename: it must always come from the
# image actually on the stick, verified live against Ubuntu 26.04.1.
imagecache_codename() {
    local stick_root="$1" info_file codename
    info_file="$stick_root/.disk/info"

    if [[ ! -r "$info_file" ]]; then
        ui_err "imagecache: no .disk/info at $info_file — is $stick_root an Ubuntu live stick root?"
        return 1
    fi

    # grep -o, not `sed 's/.*"\([^"]*\)".*/\1/'`: sed's `.*` is greedy and runs
    # to the LAST quote in the line, so the capture group matches empty and
    # the codename comes back silently blank. This guard is what turned that
    # exact silent-wrong-release bug into a loud, clean error during the
    # manual run this function codifies — keep it.
    codename="$(grep -o '"[^"]*"' "$info_file" | tr -d '"' | awk '{print tolower($1)}')"
    if [[ -z "$codename" ]]; then
        ui_err "imagecache: could not derive a release codename from $info_file"
        return 1
    fi
    printf '%s\n' "$codename"
}

# imagecache_packages <catalog_path>
# Prints, one per line, the apt package names of every component in the
# catalog's `rescue` profile. The catalog is the single source of truth
# (plan finding A12: a second hand-maintained list drifted from it silently
# before) — nothing here is a second copy of that list.
imagecache_packages() {
    local catalog_path="$1"

    if [[ ! -r "$catalog_path" ]]; then
        ui_err "imagecache: catalog not found at $catalog_path"
        return 1
    fi
    if ! has_cmd python3; then
        ui_err "imagecache: python3 is required to read the component catalog but was not found."
        return 1
    fi

    # tr -d '\r': a python3 invoked from a native Windows install (this repo
    # is tested from Git Bash as well as WSL2/Linux, per AGENTS.md §5) writes
    # CRLF line endings to a text-mode stdout even inside a Unix-style shell.
    # An un-stripped "\r" is invisible in every log line but breaks *every*
    # exact match downstream — the `<pkg>_*.deb` cache-hit glob, an explicit
    # `==` comparison, all of it — silently and only on that platform.
    python3 - "$catalog_path" <<'PY' | tr -d '\r'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    cat = json.load(fh)
pkgs = set()
for grp in cat.get("categories", []):
    for c in grp.get("components", []):
        if c.get("provider") == "apt" and "rescue" in c.get("profiles", []):
            pkgs.update(str(c["package"]).split())
for p in sorted(pkgs):
    print(p)
PY
}

# imagecache_build <stick_root> <catalog_path>
# Populates <stick_root>/rescue/debs with the .deb files for every rescue-
# profile apt package matched to the release actually on the stick, and
# publishes them as a local apt repository (dpkg-scanpackages + Packages.gz).
#
# Safe to run twice (AGENTS.md §4): a package whose .deb is already sitting
# in the cache is never re-downloaded — dpkg filenames are always
# `<name>_<version>_<arch>.deb` and a package name itself never contains an
# underscore, so `<name>_*.deb` is an exact, cheap "already have it" check —
# and a run that fetches nothing new reports that plainly instead of
# re-touching the network.
imagecache_build() {
    local stick_root="$1" catalog_path="$2"
    local codename out manifest sandbox packages_raw pkg have count
    local -a pkgs to_fetch apt_opts

    codename="$(imagecache_codename "$stick_root")" || return 1
    out="$stick_root/rescue/debs"
    manifest="$stick_root/casper/minimal.standard.live.manifest.full"

    if ! has_cmd apt-get; then
        ui_err "imagecache: apt-get not found — this must run on a Debian/Ubuntu machine"
        return 1
    fi
    if ! has_cmd dpkg-scanpackages; then
        ui_err "imagecache: dpkg-scanpackages not found (apt-get install dpkg-dev)"
        return 1
    fi

    packages_raw="$(imagecache_packages "$catalog_path")" || return 1
    pkgs=()
    if [[ -n "$packages_raw" ]]; then
        while IFS= read -r pkg; do
            [[ -n "$pkg" ]] && pkgs+=("$pkg")
        done <<<"$packages_raw"
    fi
    if (( ${#pkgs[@]} == 0 )); then
        ui_warn "imagecache: no rescue-profile apt packages found in $catalog_path"
        return 1
    fi

    # shellcheck disable=SC2086  # AUTOOS_SUDO is intentionally unquoted: empty, or the single word "sudo"
    $AUTOOS_SUDO mkdir -p "$out"

    to_fetch=()
    for pkg in "${pkgs[@]}"; do
        have=0
        compgen -G "$out/${pkg}_*.deb" >/dev/null 2>&1 && have=1
        if (( have )); then
            ui_info "imagecache: $pkg — skipped (cached .deb already present)"
        else
            to_fetch+=("$pkg")
        fi
    done

    if (( ${#to_fetch[@]} == 0 )); then
        ui_info "imagecache: every rescue package already cached — nothing to download"
    else
        sandbox="$(mktemp -d)"

        mkdir -p \
            "$sandbox/etc/apt/preferences.d" \
            "$sandbox/etc/apt/apt.conf.d" \
            "$sandbox/var/lib/apt/lists/partial" \
            "$sandbox/var/cache/apt/archives/partial" \
            "$sandbox/var/lib/dpkg"

        cat >"$sandbox/etc/apt/sources.list" <<EOF
deb http://archive.ubuntu.com/ubuntu $codename main universe multiverse restricted
deb http://archive.ubuntu.com/ubuntu $codename-updates main universe multiverse restricted
deb http://security.ubuntu.com/ubuntu $codename-security main universe multiverse restricted
EOF

        if [[ -r "$manifest" ]]; then
            awk '{printf "Package: %s\nStatus: install ok installed\nVersion: %s\nArchitecture: amd64\n\n", $1, $2}' \
                "$manifest" >"$sandbox/var/lib/dpkg/status"
            ui_info "imagecache: seeded dpkg status from the image manifest ($manifest)"
        else
            : >"$sandbox/var/lib/dpkg/status"
            ui_warn "imagecache: no manifest at $manifest — resolving from an empty system (a larger download)"
        fi

        apt_opts=(
            -o "Dir::Etc::sourcelist=$sandbox/etc/apt/sources.list"
            -o "Dir::Etc::sourceparts=$sandbox/etc/apt/sources.list.d"
            -o "Dir::Etc::preferences=$sandbox/etc/apt/preferences"
            -o "Dir::Etc::preferencesparts=$sandbox/etc/apt/preferences.d"
            -o "Dir::State::lists=$sandbox/var/lib/apt/lists"
            -o "Dir::State::status=$sandbox/var/lib/dpkg/status"
            -o "Dir::Cache=$sandbox/var/cache/apt"
            -o "APT::Architecture=amd64"
            -o "APT::Architectures::=amd64"
            -o "Acquire::Languages=none"
        )

        ui_info "imagecache: apt-get update against $codename (sandboxed — installs nothing on this host)"
        if ! apt-get "${apt_opts[@]}" update >/tmp/autoos-imagecache-update.log 2>&1; then
            ui_err "imagecache: apt-get update failed (see /tmp/autoos-imagecache-update.log)"
            rm -rf "$sandbox"
            return 1
        fi

        ui_info "imagecache: downloading ${#to_fetch[@]} package(s)"
        if apt-get "${apt_opts[@]}" -d -y --no-install-recommends install "${to_fetch[@]}" \
            >/tmp/autoos-imagecache-download.log 2>&1; then
            for pkg in "${to_fetch[@]}"; do ui_ok "imagecache: $pkg — downloaded"; done
        else
            # A12: one bad/unavailable package name must not cost the rest of
            # the group — retry one at a time so the log names precisely
            # what is unavailable in this release.
            ui_warn "imagecache: bulk download failed together — retrying one package at a time"
            for pkg in "${to_fetch[@]}"; do
                if apt-get "${apt_opts[@]}" -d -y --no-install-recommends install "$pkg" \
                    >/tmp/autoos-imagecache-download.log 2>&1; then
                    ui_ok "imagecache: $pkg — downloaded"
                else
                    ui_warn "imagecache: $pkg — unavailable in $codename, skipping"
                fi
            done
        fi

        # cp -n: never clobber a .deb a previous/concurrent build already placed.
        if compgen -G "$sandbox/var/cache/apt/archives/*.deb" >/dev/null 2>&1; then
            # shellcheck disable=SC2086
            $AUTOOS_SUDO cp -n "$sandbox"/var/cache/apt/archives/*.deb "$out"/
        fi
        rm -rf "$sandbox"
    fi

    # Re-index unconditionally, whether this run just did a full build, a
    # top-up of one missing package, or found nothing new at all: a .deb that
    # lands on disk but never makes it into Packages.gz is invisible to apt
    # on the target — it just sits there, and the failure looks exactly like
    # "package not available" with no clue why.
    if ! ( cd "$out" && dpkg-scanpackages . /dev/null 2>/dev/null >Packages && gzip -9kf Packages ); then
        ui_err "imagecache: failed to index $out as an apt repository"
        return 1
    fi

    count="$(find "$out" -maxdepth 1 -name '*.deb' 2>/dev/null | wc -l | tr -d ' ')"
    ui_ok "imagecache: $count .deb file(s) indexed as an apt repo at $out"

    # A file count is not evidence the cache is complete (see imagecache_verify
    # below for why) — always finish with the real audit, and let its exit
    # status be this function's exit status.
    imagecache_verify "$stick_root" "$catalog_path"
}

# imagecache_verify <stick_root> <catalog_path>
# Audits an already-built cache against the catalog's rescue-profile package
# list and reports, by name, every package with no matching .deb in
# <stick_root>/rescue/debs. Exits non-zero iff at least one is missing.
#
# Why this exists as its own check rather than trusting a file count: the
# cache that first proved this design reported "413 .deb files, 211M" and
# looked complete, but `mdadm` was silently absent — the build had started
# before `lvm2`/`mdadm`/`cryptsetup` were added to the catalog, and of those
# three only two happened to arrive anyway as another package's dependency. A
# total is not per-package evidence. This is also independently callable, so
# an existing cache can be audited without rebuilding it.
imagecache_verify() {
    local stick_root="$1" catalog_path="$2"
    local out packages_raw pkg
    local -a pkgs missing

    out="$stick_root/rescue/debs"
    packages_raw="$(imagecache_packages "$catalog_path")" || return 1
    pkgs=()
    if [[ -n "$packages_raw" ]]; then
        while IFS= read -r pkg; do
            [[ -n "$pkg" ]] && pkgs+=("$pkg")
        done <<<"$packages_raw"
    fi

    missing=()
    for pkg in "${pkgs[@]}"; do
        # dpkg filenames are always <name>_<version>_<arch>.deb and a package
        # name itself never contains an underscore (same exact-glob argument
        # as the "already cached" check in imagecache_build above).
        compgen -G "$out/${pkg}_*.deb" >/dev/null 2>&1 || missing+=("$pkg")
    done

    if (( ${#missing[@]} == 0 )); then
        ui_ok "imagecache: verified — all ${#pkgs[@]} rescue package(s) present in $out"
        return 0
    fi

    ui_err "imagecache: ${#missing[@]} rescue package(s) missing from $out: ${missing[*]}"
    return 1
}
