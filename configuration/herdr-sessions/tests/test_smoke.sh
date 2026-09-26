#!/usr/bin/env bash
# Smoke test for herdr-sessions: syntax-check every script, then dry-run
# install.sh for all three profiles. Never touches the system (dry-run only)
# and never requires root, herdr, or a live host.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
fail=0

echo "== bash -n =="
while IFS= read -r -d '' f; do
    if bash -n "$f"; then
        echo "  ok   $f"
    else
        echo "  FAIL $f"
        fail=1
    fi
done < <(find "$APP" -name '*.sh' -print0)
# The `herdr-sessions.sh` driver is the only executable without a .sh-only
# match issue, already covered above by its name.

echo "== python3 -m py_compile =="
while IFS= read -r -d '' f; do
    if python3 -m py_compile "$f"; then
        echo "  ok   $f"
    else
        echo "  FAIL $f"
        fail=1
    fi
done < <(find "$APP" -name '*.py' -print0)

echo "== install.sh --profile X --dry-run (must exit 0, touch nothing) =="
# Every profile that is actually present, not a hardcoded list: a checkout that
# ships only profiles/example.conf (the public export) must still pass.
profiles="$(ls -1 "$APP/profiles/" | sed 's/\.conf$//')"
[ -n "$profiles" ] || { echo "  FAIL no profiles/*.conf found"; fail=1; }
for p in $profiles; do
    echo "-- profile: $p --"
    if out="$(bash "$APP/install.sh" --profile "$p" --dry-run 2>&1)"; then
        echo "$out"
    else
        echo "$out"
        echo "  FAIL install.sh --profile $p --dry-run exited non-zero"
        fail=1
    fi
done

if [ "$fail" -eq 0 ]; then
    echo "== ALL OK =="
else
    echo "== FAILURES ABOVE =="
fi
exit "$fail"
