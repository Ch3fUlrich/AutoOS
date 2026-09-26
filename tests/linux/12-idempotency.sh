# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Idempotency ────────────────────────────────────────────────────────────
describe "idempotency"

if it "append_line_once writes once, not twice"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "MARKER" "export FOO=1  # MARKER" >/dev/null
    append_line_once "$tmp" "MARKER" "export FOO=1  # MARKER" >/dev/null
    n="$(grep -c 'MARKER' "$tmp" || true)"
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
    assert_eq "$n" "1"
fi

if it "append_line_once backs the original up before touching it"; then
    tmp="$(mktemp)"
    printf 'original content\n' >"$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "M2" "line  # M2" >/dev/null
    backup="$(ls "$tmp".autoos-backup-* 2>/dev/null | head -1)"
    if [[ -f "$backup" ]] && grep -q 'original content' "$backup"; then pass
    else fail "no usable backup written"; fi
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
fi

if it "dry run never writes"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=1
    append_line_once "$tmp" "M3" "line  # M3" >/dev/null
    AUTOOS_DRY_RUN=0
    if [[ -f "$tmp" ]]; then rm -f "$tmp"; fail "dry run created the file"; else pass; fi
fi

if it "append_line_once joins multi-word lines and adds the AutoOS header"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "M4" "export" "FOO=1" "# M4" >/dev/null
    out="$(cat "$tmp")"
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
    if [[ "$out" == $'\n# added by AutoOS\nexport FOO=1 # M4' ]]; then pass
    else fail "unexpected content: $(printf '%q' "$out")"; fi
fi

if it "append_line_once prevents duplicate lines on multiple calls"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "M_DUP" "line 1 # M_DUP" >/dev/null
    append_line_once "$tmp" "M_DUP" "line 1 # M_DUP" >/dev/null
    append_line_once "$tmp" "M_DUP" "line 1 # M_DUP" >/dev/null
    out="$(cat "$tmp")"
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
    if [[ "$out" == $'\n# added by AutoOS\nline 1 # M_DUP' ]]; then pass
    else fail "duplicate lines found: $(printf '%q' "$out")"; fi
fi

if it "append_line_once creates missing parent directories"; then
    tmp="$(mktemp -d)"
    target="$tmp/missing/dir/file"
    AUTOOS_DRY_RUN=0
    append_line_once "$target" "M5" "line  # M5" >/dev/null
    if [[ -f "$target" ]]; then pass
    else fail "parent directories or file were not created"; fi
    rm -rf "$tmp"
fi

