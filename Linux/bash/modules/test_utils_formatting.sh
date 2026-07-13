#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/utils.sh"

assert_equals() {
    local expected="$1"
    local actual="$2"
    local msg="$3"
    if [[ "$expected" != "$actual" ]]; then
        echo "❌ FAIL: $msg"
        echo "  Expected: '$expected'"
        echo "  Actual:   '$actual'"
        return 1
    else
        echo "✅ PASS: $msg"
    fi
}

strip_colors() {
    echo "$1" | sed -r 's/\x1b\[[0-9;]*m//g'
}

test_info_formatting() {
    local output clean_output
    output=$(info "test message")
    clean_output=$(strip_colors "$output")
    assert_equals "💡 [INFO]  test message" "$clean_output" "info() formats correctly"
}

test_ok_formatting() {
    local output clean_output
    output=$(ok "success")
    clean_output=$(strip_colors "$output")
    assert_equals "✅ success" "$clean_output" "ok() formats correctly"
}

test_warn_formatting() {
    local output clean_output
    output=$(warn "warning message")
    clean_output=$(strip_colors "$output")
    assert_equals "⚠️  warning message" "$clean_output" "warn() formats correctly"
}

test_err_formatting() {
    local output clean_output
    output=$(err "error message")
    clean_output=$(strip_colors "$output")
    assert_equals "❌ error message" "$clean_output" "err() formats correctly"
}

test_section_header() {
    local output clean_output
    # Command substitution strips trailing newlines, so we only expect the leading newline
    output=$(section_header "Test Title")
    clean_output=$(strip_colors "$output")
    local expected="
==============================
  Test Title
=============================="
    assert_equals "$expected" "$clean_output" "section_header() formats correctly"
}

test_success_message() {
    local output clean_output
    output=$(success_message "Installation completed")
    clean_output=$(strip_colors "$output")
    local expected="
✅ SUCCESS: Installation completed"
    assert_equals "$expected" "$clean_output" "success_message() formats correctly"
}

test_warning_message() {
    local output clean_output
    output=$(warning_message "Needs attention")
    clean_output=$(strip_colors "$output")
    local expected="
⚠️  WARNING: Needs attention"
    assert_equals "$expected" "$clean_output" "warning_message() formats correctly"
}

test_error_message() {
    local output clean_output
    output=$(error_message "Something went wrong")
    clean_output=$(strip_colors "$output")
    local expected="
❌ ERROR: Something went wrong"
    assert_equals "$expected" "$clean_output" "error_message() formats correctly"
}


test_multiple_args() {
    local output clean_output
    output=$(info "msg part 1" "and part 2")
    clean_output=$(strip_colors "$output")
    assert_equals "💡 [INFO]  msg part 1 and part 2" "$clean_output" "formatting handles multiple arguments"
}

test_color_output() {
    # Test cecho by forcing color variables and ensuring the ansi codes appear
    # We create a subshell so we can redefine colors without polluting the current test environment
    (
        export COLOR_RESET='\033[0m'
        export COLOR_CYAN='\033[0;36m'
        export COLOR_BOLD='\033[1m'

        # Call cecho
        local output
        output=$(cecho "${COLOR_CYAN}${COLOR_BOLD}" "💡 [INFO]  colored")

        # Expected string containing ESC bytes
        local expected
        expected="$(printf '%b' "\033[0;36m\033[1m💡 [INFO]  colored\033[0m")"

        assert_equals "$expected" "$output" "cecho() applies colors correctly"
    )
}

echo "Running utils.sh formatting tests..."
test_info_formatting
test_ok_formatting
test_warn_formatting
test_err_formatting
test_section_header
test_success_message
test_warning_message
test_error_message
test_multiple_args
test_color_output
echo "All tests passed!"
