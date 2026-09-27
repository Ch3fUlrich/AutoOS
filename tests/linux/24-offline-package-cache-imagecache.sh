# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Answer-file templates & rescue bootstrap (installer USB, task 9) ──────
describe "offline package cache (imagecache)"

if it "imagecache_codename extracts the release codename from .disk/info (cache)"; then
    # The exact string proven live against Ubuntu 26.04.1: a naive
    # `sed 's/.*"\(...\)/'` is greedy and runs to the LAST quote, silently
    # returning empty. grep -o is what must be used instead.
    tmp_stick="$(mktemp -d)"
    mkdir -p "$tmp_stick/.disk"
    printf 'Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)\n' > "$tmp_stick/.disk/info"
    got="$(imagecache_codename "$tmp_stick" 2>/dev/null)"
    rm -rf "$tmp_stick"
    assert_eq "$got" "resolute"
fi

if it "imagecache_codename hard-fails on an empty codename instead of returning it silently (cache)"; then
    tmp_stick="$(mktemp -d)"
    mkdir -p "$tmp_stick/.disk"
    printf 'no quoted codename on this line at all\n' > "$tmp_stick/.disk/info"
    out="$(imagecache_codename "$tmp_stick" 2>&1)"; rc=$?
    rm -rf "$tmp_stick"
    if [[ $rc -ne 0 && -n "$out" ]]; then pass; else fail "expected a non-zero exit and an error message, got rc=$rc: $out"; fi
fi

if it "imagecache_packages matches the catalog's rescue-profile apt packages exactly, same as the template test below (cache)"; then
    # Independent cross-check against the same source of truth the
    # rescue-bootstrap template test below uses (plan finding A12): two
    # different readers of catalog/linux.json must agree, or the catalog has
    # stopped being the single source of truth.
    catalog_pkgs="$(python3 -c "
import json
data = json.load(open('catalog/linux.json'))
pkgs = set()
for cat in data['categories']:
    for c in cat['components']:
        if c.get('provider') == 'apt' and 'rescue' in c.get('profiles', []):
            pkgs.add(c['package'])
print('\n'.join(sorted(pkgs)))
" | tr -d '\r')"
    got_pkgs="$(imagecache_packages catalog/linux.json | tr -d '\r')"
    assert_eq "$got_pkgs" "$catalog_pkgs"
fi

if it "imagecache_build never re-downloads a package whose .deb is already cached (cache)"; then
    # Fully hermetic: fake apt-get and dpkg-scanpackages on PATH, a synthetic
    # catalog, a throwaway stick root. Never touches the real apt, a real USB
    # device, or /mnt/j.
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"
    catalog_json="$tmp_stick/catalog.json"
    fake_apt_log="$tmp_stick/apt.log"

    cat > "$catalog_json" <<'JSON'
{
  "categories": [
    { "id": "test", "name": "Test", "components": [
      {"id":"foo","name":"Foo","description":"d","provider":"apt","package":"foo","profiles":["rescue"]},
      {"id":"bar","name":"Bar","description":"d","provider":"apt","package":"bar","profiles":["rescue"]}
    ] }
  ],
  "profiles": { "rescue": {"name":"Rescue"} }
}
JSON

    cat > "$fakebin/apt-get" <<'EOS'
#!/usr/bin/env bash
# Fake apt-get: logs every invocation, and for a download-only install drops
# an empty placeholder .deb per requested package into the cache dir named
# by -o Dir::Cache=... . Never touches the real system.
printf 'apt-get %s\n' "$*" >>"$FAKE_APT_LOG"
cache="" mode="" pkgs=()
for arg in "$@"; do
    case "$arg" in
        -o) continue ;;
        Dir::Cache=*) cache="${arg#Dir::Cache=}" ;;
        Dir::*|APT::*|Acquire::*) : ;;
        update) mode="update" ;;
        install) mode="install" ;;
        -d|-y|--no-install-recommends) : ;;
        -*) : ;;
        *) pkgs+=("$arg") ;;
    esac
done
if [[ "$mode" == "install" && -n "$cache" ]]; then
    mkdir -p "$cache/archives"
    for p in "${pkgs[@]}"; do : > "$cache/archives/${p}_1.0_amd64.deb"; done
fi
exit 0
EOS
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
# Fake dpkg-scanpackages: enough structure for gzip to have something to
# compress and for the caller to count entries; not a real Packages file.
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/apt-get" "$fakebin/dpkg-scanpackages"

    mkdir -p "$tmp_stick/.disk"
    printf 'Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)\n' > "$tmp_stick/.disk/info"

    # AUTOOS_SUDO="": without this override the function inherits whatever
    # the earlier "sudo is resolved into AUTOOS_SUDO exactly once" detection
    # test left behind (this machine's Windows 11 ships its own sudo.exe),
    # and `sudo mkdir ...` under Git Bash silently fails to create the
    # directory — hermetic tests must not depend on that leaking in.
    out1="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc1=$?
    log1="$(cat "$fake_apt_log" 2>/dev/null)"
    : > "$fake_apt_log"
    out2="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc2=$?
    log2="$(cat "$fake_apt_log" 2>/dev/null)"
    deb_count=$(find "$tmp_stick/rescue/debs" -maxdepth 1 -name '*.deb' 2>/dev/null | wc -l | tr -d ' ')
    has_index=0; [[ -f "$tmp_stick/rescue/debs/Packages.gz" ]] && has_index=1
    has_release=0; [[ -s "$tmp_stick/rescue/debs/Release" ]] && has_release=1

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$log1" == *"install"*"foo"* && "$log1" == *"bar"* \
        && -z "$log2" \
        && "$out2" == *"already cached"* \
        && "$out2" == *"verified"* \
        && "$has_release" -eq 1 \
        && "$deb_count" -eq 2 && "$has_index" -eq 1 ]]; then
        pass
    else
        fail "run1 rc=$rc1 log='${log1:0:150}' | run2 rc=$rc2 log='${log2:0:150}' out2='${out2: -200}' debs=$deb_count index=$has_index release=$has_release"
    fi
fi

if it "imagecache_build fails loudly on a partial cache and names exactly what's missing, then a top-up fetches only that (cache)"; then
    # Reproduces the exact defect a manual build hit: the builder's own
    # "N .deb files" summary looked complete while one catalog package
    # (mdadm) was silently absent. Here a 3-package catalog and a fake
    # apt-get that "forgets" one package on the first run stand in for that:
    # imagecache_build must report failure and name the missing package, and
    # a second run (the package now available) must fetch ONLY that one
    # package — never re-touching the two already cached — and end verified.
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"
    catalog_json="$tmp_stick/catalog.json"
    fake_apt_log="$tmp_stick/apt.log"

    cat > "$catalog_json" <<'JSON'
{
  "categories": [
    { "id": "test", "name": "Test", "components": [
      {"id":"foo","name":"Foo","description":"d","provider":"apt","package":"foo","profiles":["rescue"]},
      {"id":"bar","name":"Bar","description":"d","provider":"apt","package":"bar","profiles":["rescue"]},
      {"id":"baz","name":"Baz","description":"d","provider":"apt","package":"baz","profiles":["rescue"]}
    ] }
  ],
  "profiles": { "rescue": {"name":"Rescue"} }
}
JSON

    # FAKE_APT_DROP names one package this fake apt-get pretends never came
    # down, just like the real dependency-resolution quirk that dropped mdadm.
    cat > "$fakebin/apt-get" <<'EOS'
#!/usr/bin/env bash
printf 'apt-get %s\n' "$*" >>"$FAKE_APT_LOG"
cache="" mode="" pkgs=()
for arg in "$@"; do
    case "$arg" in
        -o) continue ;;
        Dir::Cache=*) cache="${arg#Dir::Cache=}" ;;
        Dir::*|APT::*|Acquire::*) : ;;
        update) mode="update" ;;
        install) mode="install" ;;
        -d|-y|--no-install-recommends) : ;;
        -*) : ;;
        *) pkgs+=("$arg") ;;
    esac
done
if [[ "$mode" == "install" && -n "$cache" ]]; then
    mkdir -p "$cache/archives"
    for p in "${pkgs[@]}"; do
        [[ -n "${FAKE_APT_DROP:-}" && "$p" == "$FAKE_APT_DROP" ]] && continue
        : > "$cache/archives/${p}_1.0_amd64.deb"
    done
fi
exit 0
EOS
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/apt-get" "$fakebin/dpkg-scanpackages"

    mkdir -p "$tmp_stick/.disk"
    printf 'Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)\n' > "$tmp_stick/.disk/info"

    out1="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" FAKE_APT_DROP="baz" \
            imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc1=$?
    : > "$fake_apt_log"
    out2="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" \
            imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc2=$?
    log2="$(cat "$fake_apt_log" 2>/dev/null)"
    pkg_count_in_index="$(gunzip -c "$tmp_stick/rescue/debs/Packages.gz" 2>/dev/null | grep -c '^Package:')"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -ne 0 && "$out1" == *"missing"* && "$out1" == *"baz"* \
        && $rc2 -eq 0 && "$out2" == *"verified"* \
        && "$log2" == *"baz"* && "$log2" != *"foo"* && "$log2" != *"bar"* \
        && "$pkg_count_in_index" -eq 3 ]]; then
        pass
    else
        fail "rc1=$rc1 out1='${out1: -200}' | rc2=$rc2 log2='$log2' index_count=$pkg_count_in_index"
    fi
fi

if it "imagecache_index writes a Release file that always matches the current Packages, never a stale one (cache)"; then
    # Reproduces the finding from exercising this against the physical stick:
    # a repo with Packages/Packages.gz but no Release makes apt probe for
    # InRelease/Release, fail, and log a scary "Err: … Method gave a blank
    # filename" before falling back and succeeding anyway — fine for apt,
    # indistinguishable from real breakage for a human on a rescue stick.
    # This test needs no apt-get at all: imagecache_index only reads whatever
    # .deb files are already on disk. Directly proves the risk the coordinator
    # called out — a Release whose checksums don't match Packages is worse
    # than no Release, because apt rejects the whole index — by corrupting
    # Packages after a first index and confirming a second pass fixes both
    # Packages (dpkg-scanpackages always rebuilds it from the .deb files) and
    # Release's checksums to match, rather than leaving either stale.
    tmp_stick="$(mktemp -d)"
    out="$tmp_stick/rescue/debs"
    mkdir -p "$out"
    : > "$out/foo_1.0_amd64.deb"
    : > "$out/bar_1.0_amd64.deb"

    fakebin="$(mktemp -d)"
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/dpkg-scanpackages"

    # A fake apt-ftparchive that FAILS, so the hand-written fallback genuinely
    # is the path under test. Restricting PATH to /usr/bin:/bin did not do
    # that: apt-utils puts the real apt-ftparchive at /usr/bin/apt-ftparchive,
    # so on WSL2 Ubuntu and Ubuntu Desktop this test was silently exercising
    # the OTHER branch while its comment claimed otherwise. Shadowing the name
    # from $fakebin is the only way to pin the branch on every host.
    cat > "$fakebin/apt-ftparchive" <<'EOS'
#!/usr/bin/env bash
exit 1
EOS
    chmod +x "$fakebin/apt-ftparchive"

    out1="$(AUTOOS_SUDO="" PATH="$fakebin:/usr/bin:/bin" imagecache_index "$out" 2>&1)"; rc1=$?
    # Proof the fallback is really the branch being measured below.
    used_fallback=0; [[ "$out1" == *"writing one by hand"* ]] && used_fallback=1
    has_release1=0; [[ -s "$out/Release" ]] && has_release1=1
    sha_release1="$(release_sha256_of "$out/Release" Packages)"
    sha_actual1="$(sha256sum "$out/Packages" | cut -d' ' -f1)"

    # Corrupt Packages directly (stands in for any way the index could go
    # stale between runs) and index again. dpkg-scanpackages always rebuilds
    # Packages from the .deb files on disk, so — since those .deb files never
    # changed — the *fixed* content, and hence its hash, is expected to come
    # back identical to run 1's; what actually proves "not stale" is that
    # Release's checksum tracks that corrected content and NOT the corrupted
    # one it was briefly overwritten with.
    printf 'STALE-GARBAGE\n' >> "$out/Packages"
    sha_corrupted="$(sha256sum "$out/Packages" | cut -d' ' -f1)"
    out2="$(AUTOOS_SUDO="" PATH="$fakebin:/usr/bin:/bin" imagecache_index "$out" 2>&1)"; rc2=$?
    packages_after="$(cat "$out/Packages")"
    sha_release2="$(release_sha256_of "$out/Release" Packages)"
    sha_actual2="$(sha256sum "$out/Packages" | cut -d' ' -f1)"
    # A temp Release.XXXXXX left inside the repo would end up served by apt.
    leftovers="$(find "$out" -maxdepth 1 -name 'Release.*' 2>/dev/null | wc -l | tr -d ' ')"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$used_fallback" -eq 1 \
        && "$has_release1" -eq 1 \
        && -n "$sha_release1" && "$sha_release1" == "$sha_actual1" \
        && "$packages_after" != *"STALE-GARBAGE"* \
        && -n "$sha_release2" && "$sha_release2" == "$sha_actual2" \
        && "$sha_release2" != "$sha_corrupted" \
        && "$leftovers" -eq 0 ]]; then
        pass
    else
        fail "rc1=$rc1 rc2=$rc2 used_fallback=$used_fallback has_release1=$has_release1 sha_release1=$sha_release1 sha_actual1=$sha_actual1 sha_release2=$sha_release2 sha_actual2=$sha_actual2 sha_corrupted=$sha_corrupted leftovers=$leftovers out1='${out1:0:150}' out2='${out2:0:150}'"
    fi
fi

if it "imagecache_index writes a correct Release through apt-ftparchive when apt-utils is present (cache)"; then
    # The companion to the test above, and the half that never ran: on a host
    # with apt-utils, imagecache_write_release takes the apt-ftparchive branch,
    # and nothing asserted anything about it. Real apt-ftparchive output lists
    # MD5Sum before SHA256, which is exactly what made the old unanchored
    # `awk '/^ .*Packages$/'` read an MD5 and compare it to a sha256sum.
    # The fixture reproduces that ordering with genuine checksums, so this
    # pins the branch deterministically on hosts with and without apt-utils.
    tmp_stick="$(mktemp -d)"
    out="$tmp_stick/rescue/debs"
    mkdir -p "$out"
    : > "$out/foo_1.0_amd64.deb"
    : > "$out/bar_1.0_amd64.deb"

    fakebin="$(mktemp -d)"
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/dpkg-scanpackages"
    cat > "$fakebin/apt-ftparchive" <<'EOS'
#!/usr/bin/env bash
# Same output shape as apt-utils' apt-ftparchive: MD5Sum BEFORE SHA256, real
# checksums, run with the repo directory as cwd.
printf 'ran\n' >>"$FAKE_FTPARCHIVE_LOG"
printf 'Origin: \nLabel: \nSuite: \nCodename: ./\nArchitectures: amd64\nComponents: \n'
printf 'MD5Sum:\n'
for f in Packages Packages.gz; do
    [[ -f "$f" ]] || continue
    printf ' %s %16s %s\n' "$(md5sum "$f" | cut -d' ' -f1)" "$(wc -c <"$f" | tr -d ' ')" "$f"
done
printf 'SHA256:\n'
for f in Packages Packages.gz; do
    [[ -f "$f" ]] || continue
    printf ' %s %16s %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(wc -c <"$f" | tr -d ' ')" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/apt-ftparchive"

    ftp_log="$tmp_stick/ftparchive.log"
    : > "$ftp_log"
    out1="$(AUTOOS_SUDO="" PATH="$fakebin:/usr/bin:/bin" FAKE_FTPARCHIVE_LOG="$ftp_log" \
            imagecache_index "$out" 2>&1)"; rc1=$?

    ftp_ran="$(wc -l <"$ftp_log" | tr -d ' ')"
    sha_release="$(release_sha256_of "$out/Release" Packages)"
    sha_actual="$(sha256sum "$out/Packages" | cut -d' ' -f1)"
    md5_actual="$(md5sum "$out/Packages" | cut -d' ' -f1)"
    # What the old, unanchored parse would have returned. Asserting it differs
    # documents why the anchoring exists rather than leaving it to a comment.
    naive="$(awk '/^ .*Packages$/{print $1; exit}' "$out/Release" 2>/dev/null)"
    leftovers="$(find "$out" -maxdepth 1 -name 'Release.*' 2>/dev/null | wc -l | tr -d ' ')"
    release_mode="$(find "$out" -maxdepth 1 -name Release -printf '%m\n' 2>/dev/null)"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 \
        && "$ftp_ran" -ge 1 \
        && "$out1" != *"writing one by hand"* \
        && -n "$sha_release" && "$sha_release" == "$sha_actual" \
        && "$naive" == "$md5_actual" \
        && "$leftovers" -eq 0 \
        && ( -z "$release_mode" || "$release_mode" == "644" ) ]]; then
        pass
    else
        fail "rc1=$rc1 ftp_ran=$ftp_ran sha_release=$sha_release sha_actual=$sha_actual naive=$naive md5_actual=$md5_actual leftovers=$leftovers mode=$release_mode out1='${out1:0:200}'"
    fi
fi

if it "imagecache_verify audits an existing cache without rebuilding it (cache)"; then
    # Pure function, no apt-get/dpkg-scanpackages needed: it only globs for
    # <pkg>_*.deb, so this proves the audit works standalone against
    # whatever is already on a stick — the "run it later without a rebuild"
    # requirement.
    tmp_stick="$(mktemp -d)"
    catalog_json="$tmp_stick/catalog.json"
    cat > "$catalog_json" <<'JSON'
{
  "categories": [
    { "id": "test", "name": "Test", "components": [
      {"id":"foo","name":"Foo","description":"d","provider":"apt","package":"foo","profiles":["rescue"]},
      {"id":"bar","name":"Bar","description":"d","provider":"apt","package":"bar","profiles":["rescue"]}
    ] }
  ],
  "profiles": { "rescue": {"name":"Rescue"} }
}
JSON
    mkdir -p "$tmp_stick/rescue/debs"
    : > "$tmp_stick/rescue/debs/foo_2.1_amd64.deb"
    # "bar" deliberately absent.

    out="$(imagecache_verify "$tmp_stick" "$catalog_json" 2>&1)"; rc=$?
    rm -rf "$tmp_stick"

    if [[ $rc -ne 0 && "$out" == *"bar"* && "$out" != *"foo"* ]]; then
        pass
    else
        fail "expected failure naming only 'bar' as missing (rc=$rc): ${out:0:200}"
    fi
fi

if it "imagecache_wheelhouse_packages reads oterm's package name off its own postInstall, from the real catalog (cache)"; then
    # Same principle as imagecache_packages' catalog cross-check above, one
    # layer up for pip: the wheelhouse builder must never hand-maintain a
    # second copy of "which package is oterm" — it has to come from the
    # catalog entries that actually install it (install_oterm /
    # Install-AutoOSOterm), or the two can drift silently.
    got="$(imagecache_wheelhouse_packages catalog/linux.json | tr -d '\r')"
    assert_eq "$got" "oterm"
fi

if it "imagecache_wheelhouse_build downloads oterm's full dependency tree and verifies it resolves fully offline (cache)"; then
    # Hermetic: python3/pip is faked so this touches no real network and
    # installs nothing — the fake still routes catalog-reading invocations
    # (the "python3 - <script>" shape imagecache_wheelhouse_packages uses)
    # through the REAL python3, since faking a JSON parser in bash would just
    # be a second, driftable copy of the parsing logic this test exists to
    # avoid.
    real_python3="$(command -v python3)"
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"
    fake_pip_log="$tmp_stick/pip.log"
    : > "$fake_pip_log"

    cat > "$fakebin/python3" <<EOS
#!/usr/bin/env bash
if [[ "\$1" == "-m" && "\$2" == "pip" ]]; then
    shift 2
    case "\$1" in
        --version) echo "pip 24.0 from fake"; exit 0 ;;
        download)
            shift
            dest=""
            while [[ \$# -gt 0 ]]; do
                case "\$1" in --dest) dest="\$2"; shift 2 ;; *) shift ;; esac
            done
            printf 'download %s\n' "\$*" >>"$fake_pip_log"
            mkdir -p "\$dest"
            # A real \`pip download\` fetches the whole dependency tree, not
            # just the leaf package — this fake drops a stand-in for a few of
            # oterm's actual transitive dependencies too, so a test asserting
            # "more than one file landed" is asserting something real.
            : > "\$dest/oterm-0.24.0-py3-none-any.whl"
            : > "\$dest/textual-8.2.8-py3-none-any.whl"
            : > "\$dest/pydantic-2.13.5-py3-none-any.whl"
            exit 0
            ;;
        install)
            printf 'install %s\n' "\$*" >>"$fake_pip_log"
            exit 0
            ;;
        *) exit 1 ;;
    esac
fi
exec "$real_python3" "\$@"
EOS
    chmod +x "$fakebin/python3"

    out1="$(PATH="$fakebin:$PATH" imagecache_wheelhouse_build "$tmp_stick" catalog/linux.json 2>&1)"; rc1=$?
    file_count=$(find "$tmp_stick/rescue/wheels" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) 2>/dev/null | wc -l | tr -d ' ')
    downloads="$(grep -c '^download ' "$fake_pip_log" || true)"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 && "$file_count" -eq 3 && "$downloads" -eq 1 \
        && "$out1" == *"verified"*"resolve fully from"*"with no network"* ]]; then
        pass
    else
        fail "rc1=$rc1 file_count=$file_count downloads=$downloads out1='${out1:0:400}'"
    fi
fi

if it "imagecache_wheelhouse_build reports nothing new on a second run against an already-complete cache (cache)"; then
    real_python3="$(command -v python3)"
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"

    cat > "$fakebin/python3" <<EOS
#!/usr/bin/env bash
if [[ "\$1" == "-m" && "\$2" == "pip" ]]; then
    shift 2
    case "\$1" in
        --version) echo "pip 24.0 from fake"; exit 0 ;;
        download)
            shift
            dest=""
            while [[ \$# -gt 0 ]]; do
                case "\$1" in --dest) dest="\$2"; shift 2 ;; *) shift ;; esac
            done
            mkdir -p "\$dest"
            # Idempotent stand-in for pip's own behaviour: a file already
            # sitting in --dest is left alone, nothing new appears.
            : > "\$dest/oterm-0.24.0-py3-none-any.whl"
            exit 0
            ;;
        install) exit 0 ;;
        *) exit 1 ;;
    esac
fi
exec "$real_python3" "\$@"
EOS
    chmod +x "$fakebin/python3"

    PATH="$fakebin:$PATH" imagecache_wheelhouse_build "$tmp_stick" catalog/linux.json >/dev/null 2>&1
    out2="$(PATH="$fakebin:$PATH" imagecache_wheelhouse_build "$tmp_stick" catalog/linux.json 2>&1)"; rc2=$?

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc2 -eq 0 && "$out2" == *"unchanged"*"already cached"* ]]; then
        pass
    else
        fail "rc2=$rc2 out2='${out2:0:400}'"
    fi
fi

