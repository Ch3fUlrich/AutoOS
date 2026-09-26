# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── hostexec (status/L1-backlog.spec-host-shell.md) ───────────────────────
describe "hostexec"

if it "hostexec: every module compiles (python3 -m py_compile)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        # shellcheck disable=SC2086  # deliberate: an unquoted glob of tools/hostexec/*.py
        out="$(python3 -m py_compile tools/hostexec.py tools/hostexec/*.py 2>&1)" \
            && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    fi
fi

if it "hostexec: policy.py deny-list decision table + check CLI (unit tests)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_hostexec_policy.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 30)"
    fi
fi

if it "hostexec: audit.py fail-closed JSONL log + log CLI (unit tests)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_hostexec_log.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 30)"
    fi
fi

if it "hostexec: runner.py subprocess execution, group timeout, ssh quoting (unit tests)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_hostexec_runner.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 30)"
    fi
fi

if it "hostexec: server.py MCP-over-HTTP broker (unit tests; live HTTP round-trip via uv+mcp when available)"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    elif has_cmd uv && uv run --no-project --with 'mcp<2' python3 -c 'import mcp, starlette, uvicorn' >/dev/null 2>&1; then
        # mcp/starlette/uvicorn resolve via uv: the full file runs, including the
        # live 127.0.0.1:0 HTTP round-trip (EndToEndHttpTests).
        out="$(uv run --no-project --with 'mcp<2' python3 tests/test_hostexec_server.py 2>&1)" \
            && pass || fail "$(printf '%s\n' "$out" | tail -n 30)"
    else
        # No uv, or mcp/starlette/uvicorn are not installable right now (offline
        # or blocked): the plain-function tests still run under plain python3;
        # the file's own __main__ prints why EndToEndHttpTests is skipped, and
        # unittest records that skip with a reason too.
        out="$(python3 tests/test_hostexec_server.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 30)"
    fi
fi

if it "hostexec: install driver"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        hostexec_install_out="$(python3 tests/test_hostexec_install.py 2>&1)" \
            && pass || fail "$(printf '%s\n' "$hostexec_install_out" | tail -n 30)"
    fi
fi

