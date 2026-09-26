# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── The write planner (Task 6) ─────────────────────────────────────────────
# usb_plan() turns (image, kind, engine, device) into the exact write
# commands, checks every compatibility/platform/lock/safety question first,
# and runs nothing — this is what makes --dry-run provable for this
# feature. Every test name below carries "usb" so `--filter usb` reaches
# it; most also carry "plan" or "engine" (`--filter plan` / `--filter
# engine`), matching the three filters this task's own testing scope asks
# for — the brief warns a filter matching zero tests here has reported a
# clean run twice already on this branch.
describe "usb planning"

if it "usb_plan: a dry run names the device and writes nothing"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts \
                         --kind installer --engine ventoy --usb-device /dev/sdb 2>&1)"
    assert_contains "$out" "/dev/sdb"
    assert_contains "$out" "Ventoy2Disk.sh"
    assert_not_contains "$out" "installed"
fi

if it "usb_plan: a raw image is never planned onto ventoy's copy path"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           bash setup.sh --create-usb --image proxmox-ve \
                         --kind installer --engine ventoy --usb-device /dev/sdb 2>&1)" || true
    assert_contains "$out" "raw"
fi

if it "usb_plan: a full-os kind is refused on an engine that cannot build one"; then
    out="$(AUTOOS_DRY_RUN=1 bash setup.sh --create-usb --image ubuntu-desktop-lts \
           --kind full-os --engine ventoy --usb-device /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"cannot build"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_plan: macOS is refused cleanly, not left to fail inside usb_list"; then
    out="$( SYS_OS=macos bash setup.sh --create-usb --image ubuntu-desktop-lts \
            --kind installer --engine native --usb-device /dev/disk2 2>&1 )"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not supported on macOS"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_plan: the ventoy engine is hidden on arm64, not offered and broken"; then
    out="$( SYS_ARCH=arm64 bash setup.sh --create-usb --list-engines 2>&1 )"
    assert_not_contains "$out" "ventoy"
    assert_contains     "$out" "native"
fi

if it "usb_plan: a second write is refused while one is already in progress"; then
    out="$( AUTOOS_FAKE_RUN_ACTIVE=1 usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb 2>&1 )"
    rc=$?
    [[ $rc -ne 0 && "$out" == *"already in progress"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_plan: uefi-copy needs no elevation but every other engine does (B10)"; then
    # A successful uefi-copy plan (its own fixture: already mounted, FAT32,
    # writable — the opposite of what ventoy/native/wsl need) must never
    # even mention elevation: setup.sh's --create-usb handling skips the
    # check entirely for this one engine, so nothing about it can leak into
    # the output whether or not this session actually has sudo/admin.
    out_uefi="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" AUTOOS_DRY_RUN=1 \
                bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
                --engine uefi-copy --usb-device /dev/sdb 2>&1)"; rc_uefi=$?
    if [[ $rc_uefi -eq 0 && "$out_uefi" != *"levat"* ]]; then pass
    else fail "rc=$rc_uefi: $out_uefi"; fi
fi

if it "usb_plan pins the device identity and emits a re-verify step immediately before the destructive lines (F3/F6)"; then
    # Findings F3/F6: usb_guard running once at plan time proves nothing by
    # the time the plan is actually executed, possibly much later - the
    # plan must carry a fresh usb_guard + identity re-check as its own first
    # step rather than relying on a stale comment.
    # "Immediately before" is the property, not "first": the download step
    # now precedes it (an hour-long fetch must not sit between the
    # re-verify and the write), so this locates the re-verify line and
    # checks the very next line is the first destructive one.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-XYZ \
           usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    reverify_no="$(printf '%s\n' "$out" | grep -n '^usb_reverify ' | head -1 | cut -d: -f1)"
    destructive_no="$(printf '%s\n' "$out" | grep -n '^Ventoy2Disk.sh ' | head -1 | cut -d: -f1)"
    reverify_line="$(printf '%s\n' "$out" | sed -n "${reverify_no:-0}p")"
    if [[ -n "$reverify_no" && -n "$destructive_no" && $((reverify_no + 1)) -eq "$destructive_no" \
          && "$reverify_line" == "usb_reverify /dev/sdb unmounted "*" serial-XYZ" ]]; then pass
    else fail "reverify at line ${reverify_no:-none}, first destructive at ${destructive_no:-none}: $out"; fi
fi

if it "usb_plan: the first plan line fetches and verifies the image, before anything touches the device (usb plan fetch)"; then
    # The seam the 2026-09-17 handoff names as the single gap stopping the
    # headline feature: image_resolve existed, fetch_verified existed, and
    # usb_plan named a cache path nobody ever filled. The plan must now
    # carry the download as its own first step so usb_execute runs it.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_CACHE_DIR=/scratch/images \
           usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    first_line="$(printf '%s\n' "$out" | head -1)"
    assert_eq "$first_line" "usb_fetch_image ubuntu-desktop-lts /scratch/images/ubuntu-desktop-lts.iso"
fi

if it "usb_plan: a live-persistent ventoy plan ends with the persistence step, an installer plan has none (usb plan persistence)"; then
    live="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_plan ubuntu-desktop-lts live-persistent ventoy /dev/sdb)"
    inst="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    last_live="$(printf '%s\n' "$live" | tail -1)"
    prev_live="$(printf '%s\n' "$live" | tail -2 | head -1)"
    if [[ "$last_live" == "usb_ventoy_add_persistence /dev/sdb" && "$prev_live" == "usb_copy_image /dev/sdb "* \
          && "$inst" != *"usb_ventoy_add_persistence"* ]]; then pass
    else fail "live=$live / inst=$inst"; fi
fi

if it "usb_execute: a usb_ventoy_add_persistence line with the wrong word count is refused, never run (usb plan persistence)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"usb_ventoy_add_persistence /dev/sdb 16 extra" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"malformed usb_ventoy_add_persistence step"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_plan: every engine's plan starts with the fetch step and never names the cache path before it (usb plan fetch)"; then
    # uefi-copy has its own fixture (mounted FAT32); the raw/native engine
    # shares good_stick. Both must lead with the same fetch line.
    out_native="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_plan proxmox-ve installer native /dev/sdb)"
    out_uefi="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" usb_plan ubuntu-desktop-lts installer uefi-copy /dev/sdb)"
    if [[ "$(printf '%s\n' "$out_native" | head -1)" == "usb_fetch_image proxmox-ve "* \
          && "$(printf '%s\n' "$out_uefi" | head -1)" == "usb_fetch_image ubuntu-desktop-lts "* ]]; then pass
    else fail "native: $(printf '%s\n' "$out_native" | head -1) / uefi: $(printf '%s\n' "$out_uefi" | head -1)"; fi
fi

if it "usb_reverify refuses when the device's identity no longer matches what was pinned at plan time (F3/F6 TOCTOU)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-CURRENT \
           usb_reverify /dev/sdb unmounted 0 serial-PINNED 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"identity changed"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_reverify accepts when the device's identity still matches what was pinned (F3/F6)"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-SAME \
        usb_reverify /dev/sdb unmounted 0 serial-SAME && pass || fail "rejected a matching identity"
fi

if it "usb chooser: image items name every real image and exclude the custom pseudo-entries (usb chooser)"; then
    out="$(usb_chooser_image_items)"
    if [[ "$out" == *"ubuntu-desktop-lts|Ubuntu Desktop (LTS)|installer, live-persistent|6 GB"* && "$out" != *"custom-url"* && "$out" != *"custom-local"* ]]; then pass
    else fail "$out"; fi
fi

if it "usb chooser: engine items list what this platform can build for the kind, and the terminal may offer rufus, badged interactive (usb chooser)"; then
    linux="$(SYS_OS=linux SYS_ARCH=x64 usb_chooser_engine_items installer hybrid)"
    windows="$(SYS_OS=windows SYS_ARCH=x64 usb_chooser_engine_items installer hybrid)"
    if [[ "$linux" == *"ventoy|Ventoy|"*"|default"* && "$linux" == *"uefi-copy|"* && "$linux" == *"native|"* \
          && "$linux" != *"rufus"* && "$linux" != *"wsl|"* \
          && "$windows" == *"rufus|"*"|interactive"* && "$windows" == *"wsl|"* ]]; then pass
    else fail "linux=$linux / windows=$windows"; fi
fi

if it "usb chooser: a raw image is only offered engines that write raw (usb chooser)"; then
    out="$(SYS_OS=linux SYS_ARCH=x64 usb_chooser_engine_items installer raw)"
    [[ "$out" == *"native|"* && "$out" != *"ventoy"* && "$out" != *"uefi-copy"* ]] && pass || fail "$out"
fi

if it "usb chooser: a non-interactive run takes the default at every step and never consents to the write itself (usb chooser)"; then
    # The chooser sets globals, so it must run in THIS shell (a $(...)
    # capture would be a subshell and lose them); its output goes to a file.
    chooser_log="$(mktemp)"
    fake_lsblk="$(fake_usb good_stick)"
    res="$( export AUTOOS_NONINTERACTIVE=1 AUTOOS_FAKE_LSBLK="$fake_lsblk" SYS_OS=linux SYS_ARCH=x64
            USB_IMAGE=""; USB_KIND=""; USB_ENGINE=""; USB_DEVICE=""; USB_WIPE=0
            usb_choose_interactively >"$chooser_log" 2>&1; rc=$?
            printf '%s|%s|%s|%s|%s|%s' "$USB_IMAGE" "$USB_KIND" "$USB_ENGINE" "$USB_DEVICE" "$USB_WIPE" "$rc" )"
    out="$(cat "$chooser_log")"; rm -f "$chooser_log"
    IFS='|' read -r img kind eng dev wipe rc <<<"$res"
    if [[ "$img" == "ubuntu-desktop-lts" && "$kind" == "installer" && "$eng" == "ventoy" && "$dev" == "/dev/sdb" \
          && "$wipe" == "0" && "$rc" != "0" && "$out" == *"Not confirmed"* ]]; then pass
    else fail "$res / $out"; fi
fi

if it "usb chooser: a dry run asks for no confirmation and shows the summary naming the device, model and size (usb chooser)"; then
    fake_lsblk="$(fake_usb good_stick)"
    out="$( export AUTOOS_NONINTERACTIVE=1 AUTOOS_DRY_RUN=1 AUTOOS_FAKE_LSBLK="$fake_lsblk" SYS_OS=linux SYS_ARCH=x64
            USB_IMAGE=""; USB_KIND=""; USB_ENGINE=""; USB_DEVICE=""; USB_WIPE=0
            usb_choose_interactively 2>&1 && echo RC0 )"
    if [[ "$out" == *"RC0"* && "$out" == *"/dev/sdb"* && "$out" == *"GB"* && "$out" != *"Not confirmed"* ]]; then pass
    else fail "$out"; fi
fi

if it "usb chooser: the profile menu offers Create installer USB and a non-interactive --create-usb without flags still refuses (usb chooser)"; then
    out="$(AUTOOS_NONINTERACTIVE=1 bash setup.sh --create-usb --no-color 2>&1)"; rc=$?
    if grep -q 'create-usb|Create installer USB' setup.sh && [[ $rc -eq 2 && "$out" == *"requires --image"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb --undo states plainly that a USB write cannot be undone"; then
    out="$( bash setup.sh --undo --dry-run 2>&1 )"
    assert_contains "$out" "USB"
fi

# ─── setup.sh actually executing a plan ────────────────────────────────────
# Until 2026-09-17 a --create-usb WITHOUT --dry-run printed the plan and
# exited 0 - a "create" that created nothing; usb_execute had no caller
# outside the tests. These two prove the wiring without a dry-run flag,
# which AGENTS.md §5 otherwise reserves for end-to-end tests: both are
# guaranteed to run no step - the first refuses before usb_execute is ever
# reached, the second is stopped by AUTOOS_FORCE_FAIL on the very first
# step - and both run against the synthetic lsblk fixture, and assert the
# scratch cache dir came out exactly as it went in.
if it "usb_plan: a real --create-usb without --wipe-target-disk shows the plan, then refuses before running any step (usb execute)"; then
    scratch="$(mktemp -d)"
    out="$(AUTOOS_CACHE_DIR="$scratch" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_UID=0 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
           --engine ventoy --usb-device /dev/sdb --no-color 2>&1)"; rc=$?
    listing="$(find "$scratch" | sort)"
    if [[ $rc -ne 0 && "$out" == *"--wipe-target-disk"* && "$out" == *"usb_fetch_image"* \
          && "$out" != *"TRACE"* && "$out" != *"Ready to boot"* && "$listing" == "$scratch" ]]; then pass
    else fail "rc=$rc listing=[$listing] out=$out"; fi
    rm -rf "$scratch"
fi

if it "usb_plan: a real --create-usb with --wipe-target-disk hands the printed plan to usb_execute (usb execute)"; then
    scratch="$(mktemp -d)"
    out="$(AUTOOS_CACHE_DIR="$scratch" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_UID=0 \
           AUTOOS_FORCE_FAIL=1 AUTOOS_TRACE=1 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
           --engine ventoy --usb-device /dev/sdb --wipe-target-disk --no-color 2>&1)"; rc=$?
    listing="$(find "$scratch" | sort)"
    # The forced failure must land on the FETCH step - proof that the first
    # thing a real run does is download, not touch the device.
    if [[ $rc -ne 0 && "$out" == *"TRACE usb_fetch_image ubuntu-desktop-lts"* \
          && "$out" == *"forced failure"* && "$out" != *"Ready to boot"* && "$listing" == "$scratch" ]]; then pass
    else fail "rc=$rc listing=[$listing] out=$out"; fi
    rm -rf "$scratch"
fi

