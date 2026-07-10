#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Mock environment
MOCK_GNOME_SHELL_VERSION="45.0"
MOCK_IS_RPI=1 # 1 means not RPi, 0 means is RPi
MOCK_WARN_CALLED=0
MOCK_GNOME_SHELL_EXISTS=0 # 0 means yes, 1 means no

# Mock functions
command_exists() {
    if [ "$1" = "gnome-shell" ]; then
        return $MOCK_GNOME_SHELL_EXISTS
    fi
    # fallback to real one (or just assume false for other things we don't care about)
    return 1
}

gnome-shell() {
    if [ "$1" = "--version" ]; then
        echo "GNOME Shell $MOCK_GNOME_SHELL_VERSION"
    fi
}

is_raspberry_pi() {
    return $MOCK_IS_RPI
}

warn() {
    MOCK_WARN_CALLED=1
}

# Source the target file
source "${SCRIPT_DIR}/gnome-extensions-core.sh"

# Mock functions MUST be defined after sourcing because the sourced script loads utils.sh which would override our mocks
command_exists() {
    if [ "$1" = "gnome-shell" ]; then
        return $MOCK_GNOME_SHELL_EXISTS
    fi
    # fallback to real one (or just assume false for other things we don't care about)
    return 1
}

gnome-shell() {
    if [ "$1" = "--version" ]; then
        echo "GNOME Shell $MOCK_GNOME_SHELL_VERSION"
    fi
}
export -f gnome-shell

is_raspberry_pi() {
    return $MOCK_IS_RPI
}

warn() {
    MOCK_WARN_CALLED=1
}

# Test framework
TESTS_RUN=0
TESTS_PASSED=0

assert_eq() {
    local expected=$1
    local actual=$2
    local msg=$3
    ((TESTS_RUN++)) || true
    if [ "$expected" -ne "$actual" ]; then
        echo "❌ FAIL: $msg (Expected $expected, got $actual)"
        return 1
    else
        echo "✅ PASS: $msg"
        ((TESTS_PASSED++)) || true
        return 0
    fi
}

echo "Testing check_extension_compat..."

# Setup some fake metadata
EXT_META["unknown_ext"]=""
EXT_META["compat_ext"]="Compat|40.0|45.0|maybe"
EXT_META["too_old_ext"]="TooOld|30.0|40.0|maybe"
EXT_META["too_new_ext"]="TooNew|46.0|50.0|maybe"
EXT_META["rpi_yes"]="RPiYes|40.0|45.0|yes"
EXT_META["rpi_no"]="RPiNo|40.0|45.0|no"
EXT_META["rpi_maybe"]="RPiMaybe|40.0|45.0|maybe"
EXT_META["rpi_unknown"]="RPiUnknown|40.0|45.0|unknown"

# Reset mocks helper
reset_mocks() {
    MOCK_GNOME_SHELL_VERSION="45.0"
    MOCK_IS_RPI=1
    MOCK_WARN_CALLED=0
    MOCK_GNOME_SHELL_EXISTS=0
}

# Disable errexit and ERR trap so we can capture return codes from check_extension_compat
set +e
trap - ERR

# 1. Unknown extension ID
reset_mocks
check_extension_compat "unknown_ext"
assert_eq 0 $? "Unknown extension should return 0 (conservative)"

# 2. Compatible GNOME version
reset_mocks
check_extension_compat "compat_ext"
assert_eq 0 $? "Compatible version should return 0"
assert_eq 0 $MOCK_WARN_CALLED "Compatible version should not warn"

# 3. Incompatible GNOME version (too old)
reset_mocks
check_extension_compat "too_old_ext"
assert_eq 2 $? "Too old version should return 2"
assert_eq 1 $MOCK_WARN_CALLED "Too old version should warn"

# 4. Incompatible GNOME version (too new)
reset_mocks
check_extension_compat "too_new_ext"
assert_eq 2 $? "Too new version should return 2"
assert_eq 1 $MOCK_WARN_CALLED "Too new version should warn"

# 5. GNOME shell does not exist
reset_mocks
MOCK_GNOME_SHELL_EXISTS=1
# When gnome-shell does not exist, version is "0.0.0", which is < 40.0
check_extension_compat "compat_ext"
assert_eq 2 $? "Missing gnome-shell should fallback to 0.0.0 and fail compat check"

# 6. Raspberry Pi scenarios
# a. RPi yes
reset_mocks
MOCK_IS_RPI=0
check_extension_compat "rpi_yes"
assert_eq 0 $? "RPi flag 'yes' should return 0 on RPi"

# b. RPi no
reset_mocks
MOCK_IS_RPI=0
check_extension_compat "rpi_no"
assert_eq 3 $? "RPi flag 'no' should return 3 on RPi"
assert_eq 1 $MOCK_WARN_CALLED "RPi flag 'no' should warn on RPi"

# c. RPi maybe
reset_mocks
MOCK_IS_RPI=0
check_extension_compat "rpi_maybe"
assert_eq 0 $? "RPi flag 'maybe' should return 0 on RPi"
assert_eq 1 $MOCK_WARN_CALLED "RPi flag 'maybe' should warn on RPi"

# d. RPi unknown
reset_mocks
MOCK_IS_RPI=0
check_extension_compat "rpi_unknown"
assert_eq 0 $? "RPi flag 'unknown' should return 0 on RPi"

set -e

echo "----------------------------------------"
echo "Tests Run: $TESTS_RUN"
echo "Tests Passed: $TESTS_PASSED"

if [ "$TESTS_RUN" -eq "$TESTS_PASSED" ]; then
    echo "All tests passed! 🎉"
    exit 0
else
    echo "$((TESTS_RUN - TESTS_PASSED)) tests failed. 😢"
    exit 1
fi
