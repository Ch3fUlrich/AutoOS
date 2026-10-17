#!/usr/bin/env bash
# SPEC-OMNI A5 — the Omnigraph MCP bridge benchmark (D9: npx or pre-installed).
#
# A thin wrapper: every argument is forwarded to tools/check_omnigraph_bridge.py,
# which owns all the logic. One home per fact — add flags there, not here.
#
#   tools/check-omnigraph-bridge.sh --cold            # fresh npm cache
#   tools/check-omnigraph-bridge.sh --warm            # primed default cache
#   tools/check-omnigraph-bridge.sh --fake-server     # offline, no npm or network
#
# Exit codes are the python file's: 0 healthy and D9 met, 1 a failure, 2 bad input.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
tool="$root/tools/check_omnigraph_bridge.py"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'ERROR: python3 is required to run the bridge benchmark but was not found.\n' >&2
    exit 2
fi
if [[ ! -f "$tool" ]]; then
    printf 'ERROR: missing %s\n' "$tool" >&2
    exit 2
fi

exec python3 "$tool" "$@"
