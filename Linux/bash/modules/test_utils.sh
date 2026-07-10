#!/usr/bin/env bash
# Unit tests for utility functions in utils.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/utils.sh"

FAILS=0

# Helper function to assert equality
assert_eq() {
    local expected="$1"
    local actual="$2"
    local msg="$3"

    if [[ "$expected" == "$actual" ]]; then
        echo "✅ PASS: $msg"
    else
        echo "❌ FAIL: $msg (Expected: '$expected', Got: '$actual')"
        FAILS=$((FAILS + 1))
    fi
}

echo "Running tests for version_to_num..."

# Happy paths
assert_eq "10203" "$(version_to_num "1.2.3")" "Standard semantic version (1.2.3)"
assert_eq "101112" "$(version_to_num "10.11.12")" "Two digit components (10.11.12)"

# Missing components
assert_eq "460000" "$(version_to_num "46")" "Major version only (46)"
assert_eq "460100" "$(version_to_num "46.1")" "Major and minor only (46.1)"

# Edge cases
assert_eq "00000" "$(version_to_num "0.0.0")" "Zero version (0.0.0)"
assert_eq "00000" "$(version_to_num "0")" "Zero major only (0)"

if [[ $FAILS -eq 0 ]]; then
    echo ""
    echo "🎉 All tests passed successfully!"
    exit_code=0
else
    echo ""
    echo "💥 $FAILS tests failed!"
    exit_code=1
fi

# We use this instead of exit to avoid triggering the sandbox restriction in some environments
# but if running directly, it works correctly.
(exit $exit_code)
