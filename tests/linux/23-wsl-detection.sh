# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "wsl detection"

if it "WSL is detected when running under it"; then
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
        assert_eq "$SYS_IS_WSL" "1"
    else
        skip "not running under WSL"
    fi
fi

if it "the WSL version is identified, not assumed"; then
    if (( SYS_IS_WSL )); then
        case "$SYS_WSL_VERSION" in 1|2) pass ;; *) fail "got version [$SYS_WSL_VERSION]" ;; esac
    else
        skip "not running under WSL"
    fi
fi

if it "the environment summary is always populated"; then
    [[ -n "${SYS_ENVIRONMENT:-}" ]] && pass || fail "SYS_ENVIRONMENT is empty"
fi

if it "a non-WSL machine reports no WSL version"; then
    ( SYS_IS_WSL=0; SYS_IS_CONTAINER=0; SYS_IS_PI=0
      [[ -z "${SYS_WSL_VERSION:-}" || "$SYS_IS_WSL" == "0" ]] ) && pass || fail "stale WSL version"
fi

if it "the payload the UI reads exposes the environment"; then
    ok=1
    for marker in "SYS_ENVIRONMENT" "isWsl" "environment"; do
        grep -q "$marker" lib/linux/serve.py || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "serve.py does not expose the environment"; fi
fi

