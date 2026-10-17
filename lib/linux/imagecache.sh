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
# publishes them as a local apt repository (dpkg-scanpackages, Packages.gz
# and a Release file — see imagecache_index below for why Release matters).
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
    if ! imagecache_index "$out"; then
        return 1
    fi

    count="$(find "$out" -maxdepth 1 -name '*.deb' 2>/dev/null | wc -l | tr -d ' ')"
    ui_ok "imagecache: $count .deb file(s) indexed as an apt repo at $out"

    # A file count is not evidence the cache is complete (see imagecache_verify
    # below for why) — always finish with the real audit, and let its exit
    # status be this function's exit status.
    imagecache_verify "$stick_root" "$catalog_path"
}

# imagecache_index <out>
# (Re)builds <out>/Packages, Packages.gz and Release from whatever .deb files
# are actually on disk right now. Called unconditionally at the end of
# imagecache_build — full build, top-up, or a no-op run that fetched nothing
# new — so the index is always freshly derived from reality, never left
# stale: a Packages file mutated or corrupted between runs is simply
# overwritten by the next dpkg-scanpackages pass, and Release is generated
# from THAT output, never a cached one.
#
# Release matters even though apt works without it: a repo shipping only
# Packages/Packages.gz makes apt probe for InRelease/Release, fail, and log
#   Err:3 file:<repo> ./ Packages
#     Method gave a blank filename
# before falling back and succeeding anyway — proven live against the actual
# stick. It still works, but a red Err: line is indistinguishable from real
# breakage to someone reading it on a broken machine at 2am, which is exactly
# what a rescue stick exists for. Release is always written LAST, after
# Packages/Packages.gz, since it checksums them — get that order backwards
# and apt is handed a Release whose checksums don't match, which it rejects
# outright (worse than no Release at all).
#
# Privilege: imagecache_build() creates $out and copies the .deb files into it
# with $AUTOOS_SUDO, so $out can perfectly well be root-owned. The index used
# to be written without it and only worked by accident on a FAT stick, where
# ownership comes from the mount options rather than the filesystem — on ext4,
# or any root-owned $out, the shell redirection below failed. Every write into
# $out now goes through $AUTOOS_SUDO like the rest of this file. Note that a
# plain `$AUTOOS_SUDO cmd >file` would NOT have been enough: the redirection
# is performed by *this* shell, before sudo ever runs.
imagecache_index() {
    local out="$1" staging

    # Generated into a temp file this process is certain it can write, then
    # placed with the same privilege the .deb files were copied with.
    staging="$(mktemp)"
    if ! ( cd "$out" && dpkg-scanpackages . /dev/null 2>/dev/null ) >"$staging"; then
        rm -f "$staging"
        ui_err "imagecache: failed to index $out as an apt repository"
        return 1
    fi
    # shellcheck disable=SC2086  # AUTOOS_SUDO is intentionally unquoted: empty, or the single word "sudo"
    if ! $AUTOOS_SUDO install -m 644 "$staging" "$out/Packages"; then
        rm -f "$staging"
        ui_err "imagecache: cannot write $out/Packages"
        return 1
    fi
    rm -f "$staging"

    # shellcheck disable=SC2086
    if ! ( cd "$out" && $AUTOOS_SUDO gzip -9kf Packages ); then
        ui_err "imagecache: cannot write $out/Packages.gz"
        return 1
    fi

    imagecache_write_release "$out"
}

# imagecache_write_release <out>
# Prefers `apt-ftparchive release .` (present via apt-utils on the machines
# that build this cache); falls back to a hand-written minimum when it isn't
# available. Both paths write through a temp file and `mv` into place so an
# interrupted run can never leave a truncated Release for apt to reject.
#
# Same privilege rule as imagecache_index above: the content is generated into
# a temp file outside $out (always writable), then staged and renamed inside
# $out under $AUTOOS_SUDO. The rename stays *within* $out so it is atomic —
# apt rejects a repository whose Release does not match Packages outright, so a
# half-written one is worse than none. Mode 644 is explicit because mktemp
# creates 600 and apt's file: method fetches as the unprivileged _apt user.
imagecache_write_release() {
    local out="$1"
    local tmp staging

    if has_cmd apt-ftparchive; then
        staging="$(mktemp)"
        if ( cd "$out" && apt-ftparchive release . ) >"$staging" 2>/dev/null && [[ -s "$staging" ]]; then
            if imagecache_place_release "$staging" "$out"; then
                rm -f "$staging"
                return 0
            fi
            rm -f "$staging"
            return 1
        fi
        rm -f "$staging"
        ui_warn "imagecache: apt-ftparchive failed to generate Release — writing one by hand"
    fi

    if ! has_cmd sha256sum; then
        ui_err "imagecache: sha256sum not found — cannot write a Release file"
        return 1
    fi

    # The minimum apt accepts for a flat file:// repo. The SHA256 entries MUST
    # match Packages/Packages.gz exactly (size in bytes, not disk blocks) or
    # apt rejects the whole index outright — always read from the files that
    # are actually on disk right now, never a cached/assumed value.
    local tmp2 f sha size
    tmp2="$(mktemp)"
    {
        printf 'Origin: AutoOS\n'
        printf 'Label: AutoOS rescue offline cache\n'
        printf 'Suite: stable\n'
        printf 'Codename: ./\n'
        printf 'Architectures: amd64\n'
        printf 'Components: \n'
        printf 'Date: %s\n' "$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S UTC')"
        printf 'SHA256:\n'
        for f in Packages Packages.gz; do
            [[ -f "$out/$f" ]] || continue
            sha="$(sha256sum "$out/$f" | cut -d' ' -f1)"
            size="$(wc -c <"$out/$f" | tr -d ' ')"
            printf ' %s %s %s\n' "$sha" "$size" "$f"
        done
    } >"$tmp2"
    if ! imagecache_place_release "$tmp2" "$out"; then
        rm -f "$tmp2"
        return 1
    fi
    rm -f "$tmp2"
}

# imagecache_place_release <staged-content> <out>
# Moves generated Release content into <out>/Release atomically and with the
# same privilege every other write into <out> uses. The intermediate lives
# inside <out> so the final `mv` is a same-filesystem rename; apt must never
# observe a partially written Release.
imagecache_place_release() {
    local src="$1" out="$2" tmp
    # shellcheck disable=SC2086  # AUTOOS_SUDO is intentionally unquoted: empty, or the single word "sudo"
    tmp="$($AUTOOS_SUDO mktemp "$out/Release.XXXXXX")" || {
        ui_err "imagecache: cannot create a temporary Release in $out"
        return 1
    }
    # shellcheck disable=SC2086
    if ! $AUTOOS_SUDO install -m 644 "$src" "$tmp"; then
        # shellcheck disable=SC2086
        $AUTOOS_SUDO rm -f "$tmp"
        ui_err "imagecache: cannot write $out/Release"
        return 1
    fi
    # shellcheck disable=SC2086
    if ! $AUTOOS_SUDO mv "$tmp" "$out/Release"; then
        # shellcheck disable=SC2086
        $AUTOOS_SUDO rm -f "$tmp"
        ui_err "imagecache: cannot replace $out/Release"
        return 1
    fi
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

# ─── offline pip wheelhouse (Task 13) ──────────────────────────────────────
# oterm (the local-ai TUI client, catalog/*.json) ships no apt/snap package,
# only pip — and the machines a rescue stick exists for are exactly the ones
# with no network to reach PyPI from. This is the same B19 problem the .deb
# cache above solves, one layer up the stack: <stick_root>/rescue/wheels is
# built here, ahead of time, on a machine WITH network, and consumed offline
# by templates/rescue-bootstrap.sh's `pip install --no-index --find-links`.
#
# imagecache_wheelhouse_packages <catalog_path>
# Prints, one per line, the "package" field of every "custom"-provider
# component whose postInstall matches the oterm installers this repo ships
# (install_oterm / Install-AutoOSOterm) and that carries the "local-ai"
# profile — the catalog stays the single source of truth even though there
# is currently exactly one such package, same principle as
# imagecache_packages() above for apt.
imagecache_wheelhouse_packages() {
    local catalog_path="$1"

    if [[ ! -r "$catalog_path" ]]; then
        ui_err "imagecache: catalog not found at $catalog_path"
        return 1
    fi
    if ! has_cmd python3; then
        ui_err "imagecache: python3 is required to read the component catalog but was not found."
        return 1
    fi

    # tr -d '\r': see imagecache_packages() above for why this is needed even
    # on Linux/WSL2 test runs.
    python3 - "$catalog_path" <<'PY' | tr -d '\r'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    cat = json.load(fh)
pkgs = set()
for grp in cat.get("categories", []):
    for c in grp.get("components", []):
        post = str(c.get("postInstall") or "")
        if post in ("install_oterm", "Install-AutoOSOterm") and "local-ai" in c.get("profiles", []):
            pkgs.add(str(c["package"]))
for p in sorted(pkgs):
    print(p)
PY
}

# imagecache_wheelhouse_build <stick_root> <catalog_path>
# Populates <stick_root>/rescue/wheels with wheels/sdists for oterm AND its
# full dependency tree (`pip download` resolves and fetches every
# transitive dependency, not just the top-level package — oterm alone pulls
# in textual, pydantic-ai, typer, and each of those has its own tree) —
# --no-deps is deliberately never passed, since the whole point is the
# complete closure, not just the leaf package.
#
# Safe to run twice (AGENTS.md §4): a file count before and after is compared
# so a run that fetches nothing new reports it plainly, matching the .deb
# cache's own "skipped" reporting above — though unlike imagecache_build's
# per-.deb `<pkg>_*.deb` glob check, individual wheel filenames are not
# skipped here before download: pip itself decides what it still needs to
# fetch into --dest, and a wheel already present is left alone rather than
# re-fetched, so no separate pre-check duplicates that decision.
imagecache_wheelhouse_build() {
    local stick_root="$1" catalog_path="$2"
    local out before after count pkg
    local -a pkgs

    out="$stick_root/rescue/wheels"

    if ! has_cmd python3; then
        ui_err "imagecache: python3 not found — cannot build the pip wheelhouse"
        return 1
    fi
    if ! python3 -m pip --version >/dev/null 2>&1; then
        ui_err "imagecache: python3 has no pip — cannot build the pip wheelhouse (apt-get install python3-pip)"
        return 1
    fi

    pkgs=()
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] && pkgs+=("$pkg")
    done < <(imagecache_wheelhouse_packages "$catalog_path")
    if (( ${#pkgs[@]} == 0 )); then
        ui_warn "imagecache: no oterm-shaped local-ai package found in $catalog_path"
        return 1
    fi

    mkdir -p "$out"
    before="$(find "$out" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) 2>/dev/null | wc -l | tr -d ' ')"

    ui_info "imagecache: downloading ${pkgs[*]} + its full dependency tree into $out"
    if ! python3 -m pip download --dest "$out" "${pkgs[@]}" \
        >/tmp/autoos-imagecache-wheelhouse.log 2>&1; then
        ui_err "imagecache: pip download failed (see /tmp/autoos-imagecache-wheelhouse.log)"
        return 1
    fi

    after="$(find "$out" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) 2>/dev/null | wc -l | tr -d ' ')"
    count="$after"
    if (( after == before )); then
        ui_info "imagecache: wheelhouse unchanged — every wheel already cached ($count file(s))"
    else
        ui_ok "imagecache: $count wheel/sdist file(s) in $out ($((after - before)) new)"
    fi

    # A file count is not evidence the cache actually resolves offline (the
    # exact lesson imagecache_verify's own comment above names: a cache that
    # "looked complete" was silently missing a package) — always finish with
    # the real audit below, and let its exit status be this function's exit
    # status.
    imagecache_wheelhouse_verify "$stick_root" "$catalog_path"
}

# imagecache_wheelhouse_verify <stick_root> <catalog_path>
# Proves the wheelhouse is actually usable offline: asks pip to resolve the
# catalog's oterm-shaped package from <stick_root>/rescue/wheels ALONE
# (--no-index --find-links, plus --dry-run so nothing is installed and no
# network is touched — --dry-run needs pip >=22.2, well inside Ubuntu
# 26.04.1's shipped version) rather than trusting a file count. Independently
# callable, like imagecache_verify above, so an existing wheelhouse can be
# audited without rebuilding it.
imagecache_wheelhouse_verify() {
    local stick_root="$1" catalog_path="$2"
    local out pkg
    local -a pkgs

    out="$stick_root/rescue/wheels"

    if [[ ! -d "$out" ]]; then
        ui_err "imagecache: no wheelhouse at $out"
        return 1
    fi
    if ! has_cmd python3; then
        ui_err "imagecache: python3 not found — cannot verify the wheelhouse"
        return 1
    fi

    pkgs=()
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] && pkgs+=("$pkg")
    done < <(imagecache_wheelhouse_packages "$catalog_path")
    if (( ${#pkgs[@]} == 0 )); then
        ui_warn "imagecache: no oterm-shaped local-ai package found in $catalog_path"
        return 1
    fi

    if python3 -m pip install --dry-run --no-index --find-links "$out" "${pkgs[@]}" \
        >/tmp/autoos-imagecache-wheelhouse-verify.log 2>&1; then
        ui_ok "imagecache: verified — ${pkgs[*]} resolve fully from $out with no network"
        return 0
    fi

    ui_err "imagecache: ${pkgs[*]} do NOT fully resolve from $out alone (see /tmp/autoos-imagecache-wheelhouse-verify.log)"
    return 1
}
