# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── custom-url / custom-local (an image the catalog does not describe) ────
# usb_plan used to refuse both pseudo-entries outright: they have no
# writeMode, so the engine check failed on an empty string. They are now
# driven by --image-url / --image-path / --image-sha256 / --write-mode
# (the USB_IMAGE_* / USB_WRITE_MODE globals here). Every name carries "usb"
# and "custom" so --filter usb and --filter custom both reach them.
describe "usb custom image"

_custom_img="$(mktemp)"
printf 'AUTOOS CUSTOM TEST IMAGE\n' >"$_custom_img"
_custom_sha="$(sha256sum "$_custom_img" | awk '{print $1}')"
_custom_bytes="$(_usb_file_size "$_custom_img")"

if it "usb custom: custom-local plans the file itself as the write source, with its real size and - for no digest"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$_custom_img" \
           usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc=$?
    first="$(printf '%s\n' "$out" | head -1)"
    if [[ $rc -eq 0 && "$first" == "usb_fetch_image custom-local $_custom_img - $_custom_img" \
          && "$out" == *"usb_reverify /dev/sdb unmounted $_custom_bytes "* \
          && "$out" == *"usb_copy_image /dev/sdb $_custom_img"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb custom: a custom image refuses without --write-mode, and a raw one is refused on ventoy but planned on native (A11)"; then
    no_mode="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_IMAGE_PATH="$_custom_img" \
               usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc1=$?
    raw_ventoy="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=raw USB_IMAGE_PATH="$_custom_img" \
                  usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc2=$?
    raw_native="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=raw USB_IMAGE_PATH="$_custom_img" \
                  usb_plan custom-local installer native /dev/sdb 2>&1)"; rc3=$?
    bad_mode="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=iso USB_IMAGE_PATH="$_custom_img" \
                usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc4=$?
    if [[ $rc1 -ne 0 && "$no_mode" == *"needs --write-mode"* && $rc2 -ne 0 && "$raw_ventoy" == *"cannot write a 'raw' image"* \
          && $rc3 -eq 0 && "$raw_native" == *"usb_write_raw /dev/sdb $_custom_img $_custom_bytes"* \
          && $rc4 -ne 0 && "$bad_mode" == *"must be 'hybrid' or 'raw'"* ]]; then pass
    else fail "no_mode=[$no_mode] raw_ventoy=[$raw_ventoy] raw_native=[$raw_native] bad_mode=[$bad_mode]"; fi
fi

if it "usb custom: custom-local refuses a missing file, an empty file, a block device and a path with whitespace"; then
    empty="$(mktemp)"
    missing="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH=/no/such/file.iso \
               usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc1=$?
    empty_out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$empty" \
                 usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc2=$?
    # A block device is not a regular file: -f refuses it, so a typo can
    # never make a disk the SOURCE of a write.
    device="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH=/dev/null \
              usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc3=$?
    spaced="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="/tmp/has space.iso" \
              usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc4=$?
    rm -f "$empty"
    if [[ $rc1 -ne 0 && "$missing" == *"is not a readable file"* && $rc2 -ne 0 && "$empty_out" == *"is empty"* \
          && $rc3 -ne 0 && "$device" == *"is not a readable file"* && $rc4 -ne 0 && "$spaced" == *"must not contain whitespace"* ]]; then pass
    else fail "missing=[$missing] empty=[$empty_out] device=[$device] spaced=[$spaced]"; fi
fi

if it "usb custom: custom-url refuses without a digest, with a malformed digest and with a non-http scheme; plans a digest-keyed cache path otherwise"; then
    sha="$(printf 'ab%.0s' {1..32})"
    no_sha="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_URL=https://example.invalid/x.iso \
              usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc1=$?
    bad_sha="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_URL=https://example.invalid/x.iso \
               USB_IMAGE_SHA256=nothex usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc2=$?
    ftp="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_URL=ftp://example.invalid/x.iso \
           USB_IMAGE_SHA256="$sha" usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc3=$?
    ok="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_CACHE_DIR=/scratch/images USB_WRITE_MODE=hybrid \
          USB_IMAGE_URL=https://example.invalid/x.iso USB_IMAGE_SHA256="${sha^^}" \
          usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc4=$?
    first="$(printf '%s\n' "$ok" | head -1)"
    if [[ $rc1 -ne 0 && "$no_sha" == *"needs --image-sha256"* && $rc2 -ne 0 && "$bad_sha" == *"64 hexadecimal"* \
          && $rc3 -ne 0 && "$ftp" == *"http:// or https://"* && $rc4 -eq 0 \
          && "$first" == "usb_fetch_image custom-url /scratch/images/custom-url-${sha:0:16}.iso $sha https://example.invalid/x.iso" ]]; then pass
    else fail "no_sha=[$no_sha] bad_sha=[$bad_sha] ftp=[$ftp] ok=[$ok]"; fi
fi

if it "usb custom: executing custom-local verifies the digest - a wrong one stops before any write, not touched"; then
    good_plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$_custom_img" \
                 USB_IMAGE_SHA256="$_custom_sha" usb_plan custom-local installer ventoy /dev/sdb 2>/dev/null | head -1)"
    bad_plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$_custom_img" \
                USB_IMAGE_SHA256="$(printf '%064d' 0)" usb_plan custom-local installer ventoy /dev/sdb 2>/dev/null | head -1)"
    good="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$good_plan" 2>&1)"; rc1=$?
    bad="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$bad_plan" 2>&1)"; rc2=$?
    if [[ $rc1 -eq 0 && "$good" == *"verified image"* && $rc2 -ne 0 \
          && "$bad" == *"does not match the SHA-256 you supplied"* && "$bad" == *"not touched"* ]]; then pass
    else fail "rc1=$rc1 good=[$good] rc2=$rc2 bad=[$bad]"; fi
fi

if it "usb custom: executing custom-url downloads and verifies against the supplied digest, and a second run is skipped"; then
    srv_dir="$(mktemp -d)"
    cp "$_custom_img" "$srv_dir/custom.iso"
    read -r srv_pid srv_port < <(_start_test_http_server "$srv_dir")
    cache="$(mktemp -d)"
    plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_CACHE_DIR="$cache" USB_WRITE_MODE=hybrid \
            USB_IMAGE_URL="http://127.0.0.1:${srv_port}/custom.iso" USB_IMAGE_SHA256="$_custom_sha" \
            usb_plan custom-url installer ventoy /dev/sdb 2>/dev/null | head -1)"
    first="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$plan" 2>&1)"; rc1=$?
    second="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$plan" 2>&1)"; rc2=$?
    dest="$cache/custom-url-${_custom_sha:0:16}.iso"
    if [[ $rc1 -eq 0 && $rc2 -eq 0 && "$first" == *"verified image"* && "$second" == *"skipped"* ]] \
        && cmp -s "$dest" "$_custom_img"; then pass
    else fail "rc1=$rc1 rc2=$rc2 first=[$first] second=[$second]"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$srv_dir" "$cache" 2>/dev/null
fi

if it "usb custom: setup.sh --create-usb with a custom-local image and --dry-run shows the plan and writes nothing"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" bash setup.sh --create-usb --image custom-local \
           --image-path "$_custom_img" --write-mode hybrid --engine ventoy --usb-device /dev/sdb \
           --dry-run --no-color 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"usb_fetch_image custom-local $_custom_img - $_custom_img"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

rm -f "$_custom_img"

