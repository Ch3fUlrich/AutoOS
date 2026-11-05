#!/usr/bin/env bash
# Explicit-device disk provisioning; sourced by build-server.sh.
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
    local mounts
    mounts="$(lsblk -ln -o MOUNTPOINT "$dev")" || { ui_err "cannot inspect device mounts - refusing"; return 1; }
    if [[ -n "$(printf '%s' "$mounts" | tr -d '[:space:]')" ]]; then
        ui_err "a partition of $dev is already mounted elsewhere - refusing"
        return 1
    fi
    # Refusal is absolute, including when an obsolete wipe flag is supplied.
    has_cmd blkid || { ui_err "blkid required - refusing"; return 1; }
    local existing_fs="" rc field
    for field in TYPE PTTYPE; do
        if existing_fs="$($AUTOOS_SUDO blkid -p -o value -s "$field" "$dev" 2>/dev/null)"; then
            [[ -z "$existing_fs" ]] || break
        else
            rc=$?
            case "$rc" in
                2) : ;;
                *) ui_err "cannot read $dev - refusing"; return 1 ;;
            esac
        fi
    done
    if [[ -n "$existing_fs" ]]; then
        ui_err "$dev already holds a filesystem/partition table ($existing_fs) - refusing"
        return 1
    fi
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would ERASE $dev, create one ext4 partition, and mount it at $mnt"
        return 0
    fi
    ui_warn "This will ERASE all data on $dev and create one ext4 partition mounted at $mnt."
    local answer=""
    [[ -t 0 ]] || { ui_err "interactive confirmation required"; return 1; }
    read -r -p "Type the device name (${dev}) to confirm: " answer || { ui_err "confirmation failed"; return 1; }
    if [[ "$answer" != "$dev" ]]; then
        ui_muted "disk formatting skipped - typed name did not match ${dev}"
        return 0
    fi
    $AUTOOS_SUDO umount "${dev}"?* 2>/dev/null || true
    $AUTOOS_SUDO wipefs -a "$dev" || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO parted -s "$dev" mklabel gpt || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO parted -s "$dev" mkpart primary ext4 0% 100% || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO partprobe "$dev" || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO udevadm settle || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO mkfs.ext4 -F -L "$label" "${dev}1" || { ui_err "required installation step failed"; return 1; }
    local uuid; uuid="$($AUTOOS_SUDO blkid -s UUID -o value "${dev}1")" || { ui_err "UUID lookup failed"; return 1; }
    [[ -n "$uuid" ]] || { ui_err "could not read UUID of ${dev}1"; return 1; }
    $AUTOOS_SUDO mkdir -p "$mnt" || { ui_err "required installation step failed"; return 1; }
    if ! grep -q "UUID=$uuid" /etc/fstab 2>/dev/null; then
        $AUTOOS_SUDO cp /etc/fstab "/etc/fstab.bak.$(date +%s)" || { ui_err "required installation step failed"; return 1; }
        printf 'UUID=%s %s ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2\n' "$uuid" "$mnt" |
            $AUTOOS_SUDO tee -a /etc/fstab >/dev/null || { ui_err "required installation step failed"; return 1; }
    fi
    $AUTOOS_SUDO systemctl daemon-reload || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO mount "$mnt" || { ui_err "required installation step failed"; return 1; }
    $AUTOOS_SUDO chown "$SYS_USER:$SYS_USER" "$mnt" || { ui_err "required installation step failed"; return 1; }
    ui_ok "mounted $dev at $mnt (UUID=$uuid)"
}
