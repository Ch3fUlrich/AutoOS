# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── End-to-end plan stability ──────────────────────────────────────────────
describe "end-to-end (dry run only)"

if it "a dry run exits cleanly"; then
    out="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "exit $rc: $(printf '%s' "$out" | tail -5)"; fi
fi

if it "a dry run executes no commands at all"; then
    # Asserts the property directly rather than sampling mtimes, which slid with
    # the clock and made this flaky: every action must be announced as "would
    # run:", and none may appear as an executed "run:".
    out="$(bash setup.sh --profile workstation --dry-run --yes --no-color 2>&1)"; rc=$?
    executed="$(printf '%s' "$out" | grep -c '^run:' || true)"
    planned="$(printf '%s' "$out" | grep -c 'would ' || true)"
    # rc too: a prompt the dry run never asks must not fail it (review 2026-09-25).
    if [[ "$rc" -eq 0 && "$executed" -eq 0 && "$planned" -gt 0 ]]; then pass
    else fail "rc=$rc executed=$executed planned=$planned (expected rc 0, 0 executed, >0 planned)"; fi
fi

if it "a dry run creates none of the files its installers would"; then
    marker="$SYS_HOME/.autoos-omnigraph.env"
    had_marker=0; [[ -e "$marker" ]] && had_marker=1
    bash setup.sh --only agent-skills --dry-run --yes --no-color >/dev/null 2>&1
    now_marker=0; [[ -e "$marker" ]] && now_marker=1
    assert_eq "$now_marker" "$had_marker"
fi

if it "two consecutive dry runs produce the same plan"; then
    a="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    b="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    assert_eq "$a" "$b"
fi

if it "a dry run for --create-usb (usb write plan) leaves the filesystem untouched"; then
    # Task 6 Step 6: the same property every other dry-run test in this
    # block proves, for the USB feature specifically — a scratch cache dir
    # (never the real one) must come out exactly as it went in, proving
    # usb_plan/setup.sh's --create-usb handling never called fetch_verified
    # or touched the device.
    usb_scratch_cache="$(mktemp -d)"
    before_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    AUTOOS_CACHE_DIR="$usb_scratch_cache" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
        bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
        --engine ventoy --usb-device /dev/sdb >/dev/null 2>&1
    after_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    assert_eq "$after_listing" "$before_listing"
    rm -rf "$usb_scratch_cache"
fi

if it "an unknown component id is rejected"; then
    out="$(bash setup.sh --only definitely-not-a-thing --dry-run --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"Unknown component"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | tail -3)"; fi
fi

if it "--check-catalog succeeds"; then
    bash setup.sh --check-catalog >/dev/null 2>&1
    assert_ok $?
fi

if it "--check-catalog validates all five catalogs by type, not just component catalogs"; then
    # Regression: setup.sh used to run every catalog/*.json through the
    # component-catalog validator, which rejects images.json and
    # engines.json outright (they have no 'categories' key). Assert every
    # file is actually reported valid, not just that the overall rc is 0 —
    # rc could go green for the wrong reason (e.g. an empty glob).
    out="$(bash setup.sh --check-catalog 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"engines.json is valid"* && "$out" == *"images.json is valid"* \
        && "$out" == *"linux.json is valid"* && "$out" == *"macos.json is valid"* && "$out" == *"windows.json is valid"*         && "$out" == *"agent-harness.json is valid"* \
        && "$out" == *"providers.json is valid"* && "$out" == *"ai-registry.json is valid"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
fi

if it "catalog_probe_installed identifies installed components"; then
    # is_installed() (lib/linux/install.sh) delegates to detect_installed_status()
    # (lib/linux/detect.sh), which for a package-manager provider runs
    # `python3 - <provider> <package> <cask>` and has THAT python3 process
    # shell out further to dpkg-query/snap/brew/npm to query the host's real
    # package database. Asserting against the real git/dpkg on this machine
    # only passes on a host with dpkg - and a stub further down that chain
    # (e.g. a fake dpkg-query) does not help either: this suite also runs on
    # Git Bash on Windows, where python3 is a native Windows build whose
    # subprocess calls cannot invoke an extension-less shebang script or a
    # .cmd stub without a real dpkg-query to fall back to. So, same style as
    # the rescue-bootstrap sandbox's stateful python3 stub below (BS_BIN):
    # stub python3 itself, on a narrowed PATH, at the exact call shape
    # detect_installed_status uses - `python3 - <provider> <package> <cask>`,
    # answering "installed"/"not-detected" straight from argv$3 (the package)
    # without touching a package database at all. That leaves
    # catalog_probe_installed's OWN mapping logic (CAT_INSTALLED[i] set from
    # the probe result) the only thing under test, deterministically on any
    # host bash can run on.
    cpi_bin="$(mktemp -d)"
    cat > "$cpi_bin/python3" <<'EOS'
#!/usr/bin/env bash
[[ "${1:-}" == "-" ]] || exit 1
case "${3:-}" in
    tmux) printf 'not-detected\n' ;;
    *)    printf 'installed\n' ;;
esac
EOS
    chmod +x "$cpi_bin/python3"

    catalog_load catalog/linux.json x64 0
    PATH="$cpi_bin:$PATH" catalog_probe_installed
    rc=$?
    git_idx="$(catalog_index_of git)"
    tmux_idx="$(catalog_index_of tmux)"
    rm -rf "$cpi_bin"
    assert_ok "$rc"
    assert_eq "${CAT_INSTALLED[git_idx]}:${CAT_INSTALLED[tmux_idx]}" "1:0"
fi

if it "--list shows installed components with a checkmark"; then
    out="$(bash setup.sh --list 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"✓"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | head -10)"; fi
fi

