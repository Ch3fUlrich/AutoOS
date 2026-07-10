#!/usr/bin/env bash

set -euo pipefail

# Source utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/utils.sh"

echo "Running tests for get_distro()..."
echo "================================================="

# Keep track of test results
TESTS_RUN=0
TESTS_FAILED=0

# Helper to report test result
report_test() {
    local test_name="$1"
    local expected="$2"
    local actual="$3"

    TESTS_RUN=$((TESTS_RUN + 1))

    if [ "$expected" = "$actual" ]; then
        echo "✅ PASS: $test_name"
    else
        echo "❌ FAIL: $test_name"
        echo "   Expected: '$expected'"
        echo "   Actual:   '$actual'"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# --- Test Case 1: Standard Output ---
# Ensure it returns something (can't predict the exact distro on arbitrary runner,
# but it should be a non-empty string). We'll test against /etc/os-release directly.
echo -n "Test Case 1 (Standard Output): "
actual_distro=$(unset OS_RELEASE_FILE && get_distro)
if [ -n "$actual_distro" ]; then
    echo "✅ PASS (returned '$actual_distro')"
    TESTS_RUN=$((TESTS_RUN + 1))
else
    echo "❌ FAIL (returned empty string)"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# --- Test Case 2: Missing File ---
export OS_RELEASE_FILE="/path/to/nonexistent/os-release"
actual=$(get_distro)
report_test "Test Case 2: Missing os-release file returns 'Unknown'" "Unknown" "$actual"

# --- Test Case 3: Mocked OS ---
MOCK_OS_RELEASE=$(mktemp)
cat << 'MOCK' > "$MOCK_OS_RELEASE"
NAME="TestOS"
VERSION_ID="42.0"
MOCK

export OS_RELEASE_FILE="$MOCK_OS_RELEASE"
actual=$(get_distro)
report_test "Test Case 3: Mocked os-release file parses NAME and VERSION_ID" "TestOS 42.0" "$actual"

# Cleanup
rm -f "$MOCK_OS_RELEASE"
unset OS_RELEASE_FILE

# --- Test Case 4: Missing VERSION_ID ---
MOCK_OS_RELEASE=$(mktemp)
cat << 'MOCK' > "$MOCK_OS_RELEASE"
NAME="TestOS"
MOCK

export OS_RELEASE_FILE="$MOCK_OS_RELEASE"
actual=$(get_distro)
report_test "Test Case 4: Mocked os-release file without VERSION_ID" "TestOS" "$actual"

# Cleanup
rm -f "$MOCK_OS_RELEASE"
unset OS_RELEASE_FILE

# --- Summary ---
echo "================================================="
echo "Tests run: $TESTS_RUN"
if [ "$TESTS_FAILED" -eq 0 ]; then
    echo "All tests passed! 🎉"
    exit 0
else
    echo "$TESTS_FAILED tests failed. 😢"
    exit 1
fi
