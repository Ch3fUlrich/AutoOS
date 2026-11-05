#!/usr/bin/env bash
# build-server profile: Rust toolchain postInstall, split out of
# build-server.sh (its own size-ratchet baseline may only shrink); like
# lib/linux/cost-gate.sh, build-server.sh keeps a thin wrapper that sources
# this file. ui_*, has_cmd, run, AUTOOS_SUDO, AUTOOS_DRY_RUN, SYS_USER,
# SYS_HOME are already in scope (build-server.sh is sourced after install.sh,
# see setup.sh).

# install_rust_toolchain: postInstall for the `rustup` catalog entry (apt
# installs the distro-pinned rustup binary; this configures the actual
# toolchain). Installs the stable toolchain with rustfmt and clippy, plus the
# mingw-w64 target AutoOS cross-compiles plangraph/pg-worker for. Safe to run
# every time: `rustup toolchain install` and `rustup target add` are both
# no-ops when the thing asked for is already present.
install_rust_toolchain() {
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install the stable Rust toolchain (rustfmt, clippy, x86_64-pc-windows-gnu target) via rustup"
        return 0
    fi
    if ! has_cmd rustup; then
        ui_err "rustup not found on PATH - the apt package may not have landed; skipping toolchain setup"
        return 1
    fi
    run rustup toolchain install stable --component rustfmt clippy --target x86_64-pc-windows-gnu || { ui_err "Rust toolchain installation failed"; return 1; }
    ui_ok "Rust stable toolchain ready (rustfmt, clippy, x86_64-pc-windows-gnu target)"
}
