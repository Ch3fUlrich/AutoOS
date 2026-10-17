#!/usr/bin/env bash
# Thin wrapper: the browser UI lives in serve.py, which drives ./setup.sh so the
# browser path and the terminal path cannot drift apart.
#
# shellcheck shell=bash

serve_start() {
    local root="$1" port="${2:-8777}" bind="${3:-127.0.0.1}"
    catalog_require_python || return 1
    if [[ ! -f "$root/web/index.html" ]]; then
        ui_err "web/index.html is missing - cannot start the browser UI."
        return 1
    fi
    exec python3 "$root/lib/linux/serve.py" "$root" "$port" "$bind" "${AUTOOS_DRY_RUN:-0}"
}
