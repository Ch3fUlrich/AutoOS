# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── shellcheck (optional) ──────────────────────────────────────────────────
describe "static analysis"

if it "no installer pipes a downloaded script into a shell (A14)"; then
    # A pipe can't be inspected before it runs and can be swapped mid-stream;
    # a downloaded file can be both (see install_agy(), install_ollama() and
    # install_uv() in lib/linux/install.sh for the fixed pattern). This is
    # the regression guard for A14: no `curl`/`wget` line may feed a shell
    # interpreter through a pipe, in this file or in templates/. Comment
    # lines are skipped deliberately — several installers document the old,
    # vulnerable one-liner in a comment (`curl ... | bash`) as the pattern
    # they replaced, and that prose must not itself trip the check.
    files=(lib/linux/install.sh templates/*.sh)
    bad=""
    for f in "${files[@]}"; do
        [[ -f "$f" ]] || continue
        while IFS=: read -r lineno line; do
            trimmed="${line#"${line%%[![:space:]]*}"}"
            [[ "$trimmed" == \#* ]] && continue
            bad+="  $f:$lineno: ${line#"${line%%[![:space:]]*}"}"$'\n'
        done < <(grep -nE '\b(curl|wget)\b.*\|[[:space:]]*(sudo[[:space:]]+)?[^|]*\b(sh|bash)\b' "$f")
    done
    if [[ -z "$bad" ]]; then pass; else fail "$(printf '%s' "$bad")"; fi
fi

if it "shellcheck is clean"; then
    # Fall back to the official image when shellcheck is not installed. This
    # check being skipped locally is precisely how a shellcheck failure reached
    # CI unnoticed, so "no binary" should not silently mean "no check".
    if has_cmd shellcheck; then
        run_shellcheck "${SHELLCHECK_FILES[@]}"; report_shellcheck "$?"
    elif has_cmd docker && docker info >/dev/null 2>&1; then
        # MSYS_NO_PATHCONV: same Windows Git Bash trap as the answer-file
        # template lint above — MSYS rewrites the bare "/mnt" into a host path
        # before docker ever sees it, and the container then refuses to start.
        # Without this the whole lint FAILS (not skips) on Windows, which is
        # where this repository is developed.
        out="$(MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable \
               -S warning "${SHELLCHECK_FILES[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "(via docker) $(printf '%s' "$out" | head -20)"; fi
    else
        skip "no shellcheck binary and no usable docker"
    fi
fi

# The describe blocks live in tests/linux/*.sh and are linted one file per
# process, as CI does: one shellcheck over the whole suite needed more than
# 2.5 GB and OOM-killed a 16 GB host twice (2026-09-26); each part alone peaks
# near 1.2 GB. Stops at the first part with a finding or out of memory and
# reports it through the same pass / fail / loud-skip rules as above.
if it "shellcheck per part: every tests/linux file is clean, one process each"; then
    if has_cmd shellcheck; then
        part_rc=0
        for part in tests/linux/*.sh; do
            run_shellcheck "$part"; part_rc=$?
            if (( part_rc != 0 )); then SHELLCHECK_OUT="$part: $SHELLCHECK_OUT"; break; fi
        done
        report_shellcheck "$part_rc"
    else
        skip "no shellcheck binary"
    fi
fi

# Windows CI failed 8x on Python tests with sh stubs/chmod/signals and 3x on a
# .ps1 without a BOM before a Linux check existed (K1, 300 CI runs measured
# 2026-09-27): catch both here, where they are cheap to fix.
if it "tests: POSIX-only Python tests are guarded for Windows; every .ps1/.psm1 has a BOM"; then
    out="$(python3 tests/test_windows_portability.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# The public-scrub gate existed with no caller before asm-a2, so a private
# literal could reach a tracked file unscanned. Assert CI actually invokes the
# scanner, and that the scanner is clean over the imported infra/ tree plus the
# scrubber's own rules. A whole-tree scan is deliberately NOT asserted: the
# generic unix-home/rfc1918 shapes legitimately match test fixtures,
# example.conf and autoinstall placeholders elsewhere in the repo, so the gate
# is scoped to the area under review (the same command ci.yml runs).
if it "public-scrub: ci.yml invokes scan.py and it is clean over infra/ + scripts/"; then
    ci_yml="$ROOT/.github/workflows/ci.yml"
    if ! grep -qE 'public-scrub/scan\.py' "$ci_yml"; then
        fail "ci.yml does not invoke scripts/public-scrub/scan.py — the no-secrets gate is wired to nothing"
    elif ! has_cmd python3; then
        skip "no python3 to run the scanner"
    else
        out="$(python3 "$ROOT/scripts/public-scrub/scan.py" \
                 --patterns "$ROOT/scripts/public-scrub/patterns.txt" \
                 "$ROOT/infra/" "$ROOT/scripts/" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then
            pass
        else
            fail "scan.py reported hits (exit $rc):"$'\n'"$(printf '%s\n' "$out" | head -20)"
        fi
    fi
fi

