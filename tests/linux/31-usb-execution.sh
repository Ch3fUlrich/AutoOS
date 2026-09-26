# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── The write executor (Task 7) ────────────────────────────────────────────
# usb_execute() is the only thing in usb.sh that ever runs a line usb_plan
# prints - every test here exercises it through the trace/fake runner it
# already has built in (AUTOOS_TRACE/AUTOOS_FORCE_FAIL/AUTOOS_DRY_RUN), or
# through the AUTOOS_FAKE_* escape hatches usb_write_ventoy/usb_copy_image
# grow for the same reason, never against a real device (AGENTS.md SS5).
# Every test name below carries "usb_execute" so both `--filter execute` and
# `--filter usb` reach it - the brief for this task warns a filter matching
# zero tests here has reported a clean run twice already on this branch.
describe "usb execution"

if it "usb_execute: executing a plan runs exactly the planned commands, in order"; then
    plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
            usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    ran="$(AUTOOS_DRY_RUN=1 AUTOOS_TRACE=1 usb_execute /dev/sdb <<<"$plan" 2>&1)"
    assert_eq "$(echo "$ran" | grep -c '^TRACE ')" "$(echo "$plan" | wc -l)"
fi

if it "usb_execute: a failed write never leaves the stick claimed as successful"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"echo x" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" != *"Ready to boot"* ]] && pass || fail "reported success on failure"
fi

if it "usb_execute: reports Ready to boot only once every step succeeds and the device still reads back (B15)"; then
    # A plain "echo hello" plan line used to exercise this test via eval -
    # finding F4 removed eval, so a real step here must match one of
    # usb_plan's own known command shapes. AUTOOS_DRY_RUN=1 keeps it from
    # actually touching anything, the same way every other dry-run test in
    # this file does.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           usb_execute /dev/sdb <<<"usb_write_raw /dev/sdb /tmp/x.iso 100" 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"Ready to boot: /dev/sdb"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: a plan step with an unrecognised command shape is refused, never executed (finding F4)"; then
    # Finding F4: usb_execute used to `eval "$line"` on anything that was
    # not the special-cased Ventoy2Disk.sh line, and device/image values in
    # a real plan reach here from a browser POST body. A line shaped like
    # "cmd; other-cmd args" proves the fix two ways at once: the whole line
    # is refused (unknown "echo" command shape), AND the "; touch $marker"
    # half is never independently executed - eval would have run it as a
    # second shell command, but read -ra only ever word-splits, so it stays
    # one inert literal argument.
    marker="$(mktemp -u)"
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"echo pwned; touch $marker" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && ! -e "$marker" && "$out" == *"known command shape"* ]]; then pass
    else fail "rc=$rc marker=$( [[ -e "$marker" ]] && echo EXISTS || echo absent ) out=$out"; fi
    rm -f "$marker"
fi

if it "usb_execute refuses to report success when the write does not actually read back (finding F10)"; then
    # AUTOOS_FAKE_READBACK=0 forces _usb_verify_readback's own check to
    # fail even though the device still enumerates (good_stick) - proving
    # this checks more than mere presence.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 AUTOOS_FAKE_READBACK=0 \
           usb_execute /dev/sdb <<<"usb_write_raw /dev/sdb /tmp/x.iso 100" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"did not read back"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: a step failure names the stick as removed when it truly vanished, not a generic error"; then
    # root_is_sda's fixture carries no sdb at all - the same "the device is
    # simply gone" state the unplug-mid-write finding describes.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"echo x" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"removed during the write"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: dispatches a Ventoy2Disk.sh plan line through usb_write_ventoy, not a bare command lookup"; then
    # usb_plan deliberately keeps "Ventoy2Disk.sh -i -g <dev>" literal in its
    # own output (the "usb planning" tests above assert that exact string),
    # even though the real binary is not on PATH until usb_write_ventoy has
    # fetched it. This proves usb_execute bridges that gap instead of just
    # eval-ing the literal plan line (which would fail: command not found).
    # AUTOOS_FAKE_VENTOY_RELEASE points at a fake tarball+sha256.txt served
    # over a throwaway loopback HTTP server (_start_test_http_server above) -
    # no real network, and AUTOOS_CACHE_DIR is "$scratch/images" (not
    # "$scratch" itself) so usb_write_ventoy's tools/ventoy cache, which
    # lives next to dirname(AUTOOS_CACHE_DIR), stays isolated per test
    # instead of colliding on a shared /tmp/tools/ventoy.
    rel="$(mktemp -d)"
    printf '#!/bin/sh\necho "fake ventoy $*"\n' >"$rel/Ventoy2Disk.sh"
    chmod +x "$rel/Ventoy2Disk.sh"
    tar -C "$rel" -czf "$rel/ventoy-9.9.9-linux.tar.gz" Ventoy2Disk.sh
    sum="$(sha256sum "$rel/ventoy-9.9.9-linux.tar.gz" | awk '{print $1}')"
    printf '%s  ventoy-9.9.9-linux.tar.gz\n' "$sum" >"$rel/sha256.txt"
    read -r http_pid http_port < <(_start_test_http_server "$rel")
    base="http://127.0.0.1:$http_port"
    release_json="{\"assets\":[{\"name\":\"ventoy-9.9.9-linux.tar.gz\",\"browser_download_url\":\"$base/ventoy-9.9.9-linux.tar.gz\"},{\"name\":\"sha256.txt\",\"browser_download_url\":\"$base/sha256.txt\"}]}"
    scratch="$(mktemp -d)"
    if [[ -n "$http_port" ]]; then
        out="$(AUTOOS_SUDO="" AUTOOS_CACHE_DIR="$scratch/images" AUTOOS_FAKE_VENTOY_RELEASE="$release_json" \
               AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
               usb_execute /dev/sdb <<<"Ventoy2Disk.sh -i -g /dev/sdb" 2>&1)"; rc=$?
        if [[ $rc -eq 0 && "$out" == *"fake ventoy -i -g /dev/sdb"* ]]; then pass
        else fail "rc=$rc out=$(printf '%s' "$out" | tail -5)"; fi
    else
        fail "test HTTP server never started listening"
    fi
    # wait, not just kill: on this host a killed python http.server can hold
    # its serving directory open for a moment afterward, and the rm -rf right
    # below would otherwise race it ("Device or resource busy").
    kill "$http_pid" 2>/dev/null
    wait "$http_pid" 2>/dev/null
    rm -rf "$rel" "$scratch"
fi

if it "usb_execute: usb_write_ventoy caches the extracted tool and skips re-fetching on a second call"; then
    rel="$(mktemp -d)"
    printf '#!/bin/sh\necho "fake ventoy $*"\n' >"$rel/Ventoy2Disk.sh"
    chmod +x "$rel/Ventoy2Disk.sh"
    tar -C "$rel" -czf "$rel/ventoy-9.9.9-linux.tar.gz" Ventoy2Disk.sh
    sum="$(sha256sum "$rel/ventoy-9.9.9-linux.tar.gz" | awk '{print $1}')"
    printf '%s  ventoy-9.9.9-linux.tar.gz\n' "$sum" >"$rel/sha256.txt"
    read -r http_pid http_port < <(_start_test_http_server "$rel")
    base="http://127.0.0.1:$http_port"
    release_json="{\"assets\":[{\"name\":\"ventoy-9.9.9-linux.tar.gz\",\"browser_download_url\":\"$base/ventoy-9.9.9-linux.tar.gz\"},{\"name\":\"sha256.txt\",\"browser_download_url\":\"$base/sha256.txt\"}]}"
    scratch="$(mktemp -d)"
    if [[ -n "$http_port" ]]; then
        AUTOOS_SUDO="" AUTOOS_CACHE_DIR="$scratch/images" AUTOOS_FAKE_VENTOY_RELEASE="$release_json" \
            usb_write_ventoy /dev/sdb >/dev/null 2>&1
        first_rc=$?
        # Second call is handed a release with NO usable assets at all - if
        # it still succeeds, the cache hit (not a re-fetch) is what made
        # that work.
        out="$(AUTOOS_SUDO="" AUTOOS_CACHE_DIR="$scratch/images" AUTOOS_FAKE_VENTOY_RELEASE='{"assets":[]}' \
               usb_write_ventoy /dev/sdb 2>&1)"; second_rc=$?
        if [[ $first_rc -eq 0 && $second_rc -eq 0 && "$out" == *"fake ventoy -i -g /dev/sdb"* ]]; then pass
        else fail "first_rc=$first_rc second_rc=$second_rc out=$out"; fi
    else
        fail "test HTTP server never started listening"
    fi
    # wait, not just kill: on this host a killed python http.server can hold
    # its serving directory open for a moment afterward, and the rm -rf right
    # below would otherwise race it ("Device or resource busy").
    kill "$http_pid" 2>/dev/null
    wait "$http_pid" 2>/dev/null
    rm -rf "$rel" "$scratch"
fi

if it "usb_execute: usb_write_raw's dry run names the dd command and writes nothing"; then
    out="$(AUTOOS_DRY_RUN=1 usb_write_raw /dev/sdb /path/to/image.iso 123456 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"dd if=/path/to/image.iso of=/dev/sdb"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_write_raw computes its own NN% progress from dd's byte counter (B14)"; then
    # dd status=progress prints bytes-and-rate but never a percentage
    # (lib/linux/process.py's %-regex would otherwise see nothing) - a fake
    # dd on PATH stands in for the real one so this proves the percentage
    # math without writing to anything.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/dd" <<'DDEOF'
#!/usr/bin/env bash
echo "52428800 bytes (52 MB, 50 MiB) copied, 1 s, 52 MB/s" >&2
echo "104857600 bytes (105 MB, 100 MiB) copied, 2 s, 52 MB/s" >&2
exit 0
DDEOF
    chmod +x "$fakebin/dd"
    img="$(mktemp)"; printf 'x' >"$img"
    out="$(PATH="$fakebin:$PATH" AUTOOS_SUDO="" usb_write_raw /dev/fake "$img" 104857600 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"50%"* && "$out" == *"100%"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
    rm -rf "$fakebin"; rm -f "$img"
fi

if it "usb_execute: usb_write_raw returns non-zero when dd itself fails"; then
    fakebin="$(mktemp -d)"
    cat >"$fakebin/dd" <<'DDEOF'
#!/usr/bin/env bash
echo "dd: fake write error" >&2
exit 1
DDEOF
    chmod +x "$fakebin/dd"
    img="$(mktemp)"; printf 'x' >"$img"
    PATH="$fakebin:$PATH" AUTOOS_SUDO="" usb_write_raw /dev/fake "$img" 104857600 >/dev/null 2>&1
    assert_eq "$?" "1"
    rm -rf "$fakebin"; rm -f "$img"
fi

if it "usb_execute: _usb_copy_readback reports a same-size content change, a missing file and a size change, and nothing for an identical tree (usb copy readback)"; then
    # The second real build on 2026-09-17 finished and printed "Ready to
    # boot" while the stick had silently replaced one 16 KB cluster of
    # md5sum.txt with garbage at the same size. Only reading back catches
    # that; this is the pure dir-vs-dir function the copy path calls.
    rb="$(mktemp -d)"
    mkdir -p "$rb/src/casper" "$rb/dst/casper" "$rb/src/EFI/boot" "$rb/dst/EFI/boot"
    for rel in md5sum.txt casper/minimal.squashfs EFI/boot/bootx64.efi casper/vmlinuz; do
        head -c 40000 /dev/urandom >"$rb/src/$rel"; cp "$rb/src/$rel" "$rb/dst/$rel"
    done
    clean_out="$(_usb_copy_readback "$rb/src" "$rb/dst" 2>&1)"; clean_rc=$?
    # Same size, one "cluster" of garbage in the middle - the live shape.
    head -c 16384 /dev/urandom | dd of="$rb/dst/md5sum.txt" bs=1 seek=8192 conv=notrunc status=none
    rm -f "$rb/dst/casper/vmlinuz"
    head -c 1000 "$rb/src/EFI/boot/bootx64.efi" >"$rb/dst/EFI/boot/bootx64.efi"
    out="$(_usb_copy_readback "$rb/src" "$rb/dst" 2>&1)"; rc=$?
    if [[ $clean_rc -eq 0 && -z "$clean_out" && $rc -ne 0 && "$out" == *"content differs (same size): md5sum.txt"* \
          && "$out" == *"missing on the stick: casper/vmlinuz"* && "$out" == *"size differs: EFI/boot/bootx64.efi"* ]]; then pass
    else fail "clean_rc=$clean_rc clean_out=[$clean_out] rc=$rc out=$out"; fi
    rm -rf "$rb"
fi

if it "usb_execute: usb_copy_image's dry run writes nothing"; then
    out="$(AUTOOS_DRY_RUN=1 usb_copy_image /dev/sdb /tmp/x.iso /tmp/extra.sh 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"would copy"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image refuses a mounted ISO9660 stick (finding A11)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_iso9660_mounted)" usb_copy_image /dev/sdb /tmp/x.iso 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"A11"* && "$out" == *"raw block copy"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image refuses an unmounted ISO9660 partition before mounting anything (A11, ventoy path)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_iso9660_unmounted)" usb_copy_image /dev/sdb /tmp/x.iso 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"A11"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image refuses a >4GiB inner file before copying anything (B16)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" AUTOOS_FAKE_ISO_MAX_FILE_BYTES=5000000000 \
           AUTOOS_FAKE_ISO_MAX_FILE_NAME="casper/big.squashfs" usb_copy_image /dev/sdb /tmp/x.iso 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"casper/big.squashfs"* && "$out" == *"4 GiB"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image fails the whole call when umount itself fails after the write (finding F9)"; then
    fakebin="$(mktemp -d)"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$fakebin/mount"
    printf '#!/usr/bin/env bash\necho "umount: fake failure" >&2\nexit 1\n' >"$fakebin/umount"
    chmod +x "$fakebin/mount" "$fakebin/umount"
    img="$(mktemp)"; printf 'x' >"$img"
    out="$(PATH="$fakebin:$PATH" AUTOOS_SUDO="" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           AUTOOS_FAKE_ISO_MAX_FILE_BYTES=1000000 AUTOOS_FAKE_ISO_MAX_FILE_NAME=x \
           usb_copy_image /dev/sdb "$img" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"could not unmount"* ]] && pass || fail "rc=$rc out=$out"
    rm -rf "$fakebin"; rm -f "$img"
fi

if it "usb_execute: usb_copy_image mounts ventoy's data partition by index, not by size (finding F7)"; then
    fakebin="$(mktemp -d)"
    record="$(mktemp)"
    printf '#!/usr/bin/env bash\necho "$1" >> %s\nexit 0\n' "$record" >"$fakebin/mount"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$fakebin/umount"
    chmod +x "$fakebin/mount" "$fakebin/umount"
    img="$(mktemp)"; printf 'x' >"$img"
    PATH="$fakebin:$PATH" AUTOOS_SUDO="" AUTOOS_FAKE_LSBLK="$(fake_usb ventoy_partitions_reversed)" \
        AUTOOS_FAKE_ISO_MAX_FILE_BYTES=1000000 AUTOOS_FAKE_ISO_MAX_FILE_NAME=x \
        usb_copy_image /dev/sdb "$img" >/dev/null 2>&1
    mounted="$(cat "$record")"
    [[ "$mounted" == "/dev/sdb1" ]] && pass || fail "mounted: $mounted (expected /dev/sdb1, the lower-index partition, not sdb2 which is larger)"
    rm -rf "$fakebin"; rm -f "$img" "$record"
fi

if it "usb_execute: usb_ventoy_add_persistence caps the default 16GB at half the stick's size (B17)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 usb_ventoy_add_persistence /dev/sdb 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"capping"* && "$out" == *"15GB"* ]] && pass || fail "rc=$rc out=$out"
fi

