# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── End-to-end plan stability ──────────────────────────────────────────────
describe "end-to-end (dry run only)"

# e2e_setup <setup.sh args...> — the real entry point, for a machine that has
# nothing on it. HOME is a scratch dir and SUDO_USER a name with no passwd entry,
# so SYS_HOME falls back to HOME (lib/linux/detect.sh:200; the same device as
# tests/linux/22-herdr-sessions.sh:1049). Against the developer's own home a run
# answers for that machine instead of for the plan: here
# ~/.local/bin/graphify-mcp is uv's link, the graphify post-install refuses to
# move it, and the dry run exits 1 for a reason no check here has anything to do
# with. Prints the output, returns the exit code.
e2e_setup() {
    local home out rc
    home="$(mktemp -d)"
    out="$(SUDO_USER='autoos-no-such-user-e2e' HOME="$home" \
        bash setup.sh "$@" 2>&1)"; rc=$?
    rm -rf "$home"
    printf '%s\n' "$out"
    return "$rc"
}

if it "a dry run exits cleanly"; then
    out="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "exit $rc: $(printf '%s' "$out" | tail -5)"; fi
fi

if it "a dry run executes no commands at all"; then
    # Asserts the property directly rather than sampling mtimes, which slid with
    # the clock and made this flaky: every action must be announced as "would
    # run:", and none may appear as an executed "run:".
    out="$(bash setup.sh --profile workstation --dry-run --yes --no-color 2>&1)"; rc=$?
    executed="$(printf '%s' "$out" | grep -c '^run:' || true)"
    planned="$(printf '%s' "$out" | grep -c 'would ' || true)"
    # rc too: a prompt the dry run never asks must not fail it (review 2026-09-25).
    if [[ "$rc" -eq 0 && "$executed" -eq 0 && "$planned" -gt 0 ]]; then pass
    else fail "rc=$rc executed=$executed planned=$planned (expected rc 0, 0 executed, >0 planned)"; fi
fi

if it "a dry run creates none of the files its installers would"; then
    marker="$SYS_HOME/.autoos-omnigraph.env"
    had_marker=0; [[ -e "$marker" ]] && had_marker=1
    bash setup.sh --only omnigraph-client --dry-run --yes --no-color >/dev/null 2>&1
    now_marker=0; [[ -e "$marker" ]] && now_marker=1
    assert_eq "$now_marker" "$had_marker"
fi

if it "--only agent-skills plans the components that took its work"; then
    # A7: agent-skills is a tombstone, so a hand-chosen retired id is expanded
    # before the plan (docs/catalog.md, "replaced_by"): the row that was chosen
    # stays and reports `skipped: retired`, and the successors do the work the
    # state file, the menu or the flag could no longer ask of it.
    out="$(e2e_setup --only agent-skills --dry-run --yes --no-color)"; rc=$?
    planned="$(printf '%s\n' "$out" | grep -E '^\s+[0-9]+\.' || true)"
    problems=""
    (( rc == 0 )) || problems+="[exit $rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" == *"agent-skills is retired: replaced by"* ]] \
        || problems+="[the expansion was announced as nothing: $(printf '%s\n' "$out" | grep -i retired | head -3)] "
    for want in agent-skill-links omnigraph-client mcp-graphify mcp-serena \
                mcp-playwright mcp-context7; do
        grep -qE "^\s+[0-9]+\.\s.*\s$want\s*$" <<<"$planned" \
            || problems+="[$want is not in the plan] "
    done
    # The retired row stays in the plan and in the report — what the reader chose
    # is what they are told about — and it is labelled, never claimed installed.
    grep -qE '^\s+[0-9]+\.\s+agent-skills \(retired\).*\(retired\)$' <<<"$planned" \
        || problems+="[the retired row is missing or unlabelled: $(grep -F 'agent-skills' <<<"$planned")] "
    [[ "$out" != *"agent-skills is already installed"* ]] \
        || problems+="[the retired row was claimed already installed] "
    skipped="$(printf '%s\n' "$out" | grep -c 'skipped: retired' || true)"
    [[ "$skipped" == "1" ]] || problems+="[$skipped 'skipped: retired' lines, expected 1] "
    grep -qE 'skipped: retired \(.*moved to agent-skill-links' <<<"$out" \
        || problems+="[the skip line does not carry the retirement note: $(grep -F 'skipped: retired' <<<"$out")]"
    # Nothing that belonged to the retired step may leak back in: the fetch and
    # the skills link are the successors' work now, named by their own rows.
    grep -qiE '\bgit (clone|pull|-C)[[:space:]].*agent-skills' <<<"$out" \
        && problems+="[the plan fetches the retired agent-skills repo] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "--profile never plans the retired agent-skills id"; then
    # The profile list pre-selects what a run should get installed, and a retired
    # id installs nothing, so it has no business there. Both profiles that used to
    # carry agent-skills are asked.
    all_problems=""
    for profile in workstation ai-coding; do
        out="$(e2e_setup --profile "$profile" --dry-run --yes --no-color)"; rc=$?
        planned="$(printf '%s\n' "$out" | grep -E '^\s+[0-9]+\.' || true)"
        problems=""
        (( rc == 0 )) || problems+="[exit $rc: $(printf '%s\n' "$out" | tail -3)] "
        grep -qE '^\s+[0-9]+\.\s+agent-skills' <<<"$planned" \
            && problems+="[$profile planned the retired id] "
        # The successors are profile members in their own right, so the work the
        # retired id used to pre-tick is still planned by the same run.
        for want in agent-skill-links omnigraph-client; do
            grep -qE "^\s+[0-9]+\.\s.*\s$want\s*$" <<<"$planned" \
                || problems+="[$want is not in the $profile plan] "
        done
        [[ -z "$problems" ]] || all_problems+="$problems "
    done
    if [[ -z "$all_problems" ]]; then pass; else fail "$all_problems"; fi
fi

if it "from-state: a state file saved before the retirement replays to the replacements"; then
    # Hand-authored, because the file predates the tombstone: it names the
    # retired id and none of the ids that inherited its work, and a replay that
    # took it verbatim booked the retirement and installed nothing.
    state="$(mktemp)"
    printf '%s\n' '{"profile": "custom", "selected": ["agent-skills"], "answers": {}}' > "$state"
    out="$(e2e_setup --from-state "$state" --dry-run --yes --no-color)"; rc=$?
    planned="$(printf '%s\n' "$out" | grep -E '^\s+[0-9]+\.' || true)"
    rm -f "$state"
    problems=""
    (( rc == 0 )) || problems+="[exit $rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" == *"agent-skills is retired: replaced by"* ]] \
        || problems+="[a replayed retired id was not expanded] "
    for want in agent-skill-links omnigraph-client mcp-context7; do
        grep -qE "^\s+[0-9]+\.\s.*\s$want\s*$" <<<"$planned" \
            || problems+="[$want is not in the replayed plan] "
    done
    [[ "$out" == *"skipped: retired"* ]] || problems+="[the retired row reported nothing] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "the ai-coding plan carries the agent-skill-links and omnigraph-client homes"; then
    # The A7 verify row: a fresh ai-coding dry run never *touches* the
    # agent-skills repo. The machine this suite runs on has the retired clone or
    # does not; either way the plan must never send anyone to fetch it, and the
    # two components that inherited agent-skills' work must both be in the
    # profile that used to carry agent-skills alone. (Run for an empty machine —
    # see e2e_setup.)
    out="$(e2e_setup --profile ai-coding --dry-run --yes --no-color)"; rc=$?
    problems=""
    (( rc == 0 )) || problems+="[exit $rc] "
    for want in 'agent-skill-links' 'omnigraph-client'; do
        grep -qE "^\s+[0-9]+\.\s.*\s$want\s*$" <<<"$out" || problems+="[$want is not in the ai-coding plan] "
    done
    # Only the RETIRED repo may never be fetched. A machine that has not been set
    # up yet plans clones of its own and legitimately so — measured on a scratch
    # home: the two oh-my-zsh plugins and powerlevel10k
    # (lib/linux/install.sh:638-647), plus the LazyVim starter. Grepping the whole
    # output for `git clone` failed CI 36391119133 for exactly that reason. Both
    # fetch shapes count: the clone's URL or destination names the retired repo, and
    # so does the target of a `git -C <dir> pull` on a clone that is already there.
    grep -qiE '\bgit (clone|pull|-C)[[:space:]].*agent-skills' <<<"$out" \
        && problems+="[the plan fetches the retired agent-skills repo] "
    # The retired clone is never a skills source any more: the checkout is.
    grep -qE 'link skills from .*(Documents|\.autoos)/.*agent-skills' <<<"$out" \
        && problems+="[the retired clone is still named as the skills source] "
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "two consecutive dry runs produce the same plan"; then
    a="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    b="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    assert_eq "$a" "$b"
fi

if it "a dry run for --create-usb (usb write plan) leaves the filesystem untouched"; then
    # Task 6 Step 6: the same property every other dry-run test in this
    # block proves, for the USB feature specifically — a scratch cache dir
    # (never the real one) must come out exactly as it went in, proving
    # usb_plan/setup.sh's --create-usb handling never called fetch_verified
    # or touched the device.
    usb_scratch_cache="$(mktemp -d)"
    before_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    AUTOOS_CACHE_DIR="$usb_scratch_cache" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
        bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
        --engine ventoy --usb-device /dev/sdb >/dev/null 2>&1
    after_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    assert_eq "$after_listing" "$before_listing"
    rm -rf "$usb_scratch_cache"
fi

if it "an unknown component id is rejected"; then
    out="$(bash setup.sh --only definitely-not-a-thing --dry-run --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"Unknown component"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | tail -3)"; fi
fi

if it "--check-catalog succeeds"; then
    bash setup.sh --check-catalog >/dev/null 2>&1
    assert_ok $?
fi

if it "--check-catalog validates all five catalogs by type, not just component catalogs"; then
    # Regression: setup.sh used to run every catalog/*.json through the
    # component-catalog validator, which rejects images.json and
    # engines.json outright (they have no 'categories' key). Assert every
    # file is actually reported valid, not just that the overall rc is 0 —
    # rc could go green for the wrong reason (e.g. an empty glob).
    out="$(bash setup.sh --check-catalog 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"engines.json is valid"* && "$out" == *"images.json is valid"* \
        && "$out" == *"linux.json is valid"* && "$out" == *"macos.json is valid"* && "$out" == *"windows.json is valid"*         && "$out" == *"agent-harness.json is valid"* \
        && "$out" == *"ai-registry.json is valid"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
fi

if it "catalog_probe_installed identifies installed components"; then
    # is_installed() (lib/linux/install.sh) delegates to detect_installed_status()
    # (lib/linux/detect.sh), which for a package-manager provider runs
    # `python3 - <provider> <package> <cask>` and has THAT python3 process
    # shell out further to dpkg-query/snap/brew/npm to query the host's real
    # package database. Asserting against the real git/dpkg on this machine
    # only passes on a host with dpkg - and a stub further down that chain
    # (e.g. a fake dpkg-query) does not help either: this suite also runs on
    # Git Bash on Windows, where python3 is a native Windows build whose
    # subprocess calls cannot invoke an extension-less shebang script or a
    # .cmd stub without a real dpkg-query to fall back to. So, same style as
    # the rescue-bootstrap sandbox's stateful python3 stub below (BS_BIN):
    # stub python3 itself, on a narrowed PATH, at the exact call shape
    # detect_installed_status uses - `python3 - <provider> <package> <cask>`,
    # answering "installed"/"not-detected" straight from argv$3 (the package)
    # without touching a package database at all. That leaves
    # catalog_probe_installed's OWN mapping logic (CAT_INSTALLED[i] set from
    # the probe result) the only thing under test, deterministically on any
    # host bash can run on.
    cpi_bin="$(mktemp -d)"
    cat > "$cpi_bin/python3" <<'EOS'
#!/usr/bin/env bash
[[ "${1:-}" == "-" ]] || exit 1
case "${3:-}" in
    tmux) printf 'not-detected\n' ;;
    *)    printf 'installed\n' ;;
esac
EOS
    chmod +x "$cpi_bin/python3"

    catalog_load catalog/linux.json x64 0
    PATH="$cpi_bin:$PATH" catalog_probe_installed
    rc=$?
    git_idx="$(catalog_index_of git)"
    tmux_idx="$(catalog_index_of tmux)"
    rm -rf "$cpi_bin"
    assert_ok "$rc"
    assert_eq "${CAT_INSTALLED[git_idx]}:${CAT_INSTALLED[tmux_idx]}" "1:0"
fi

if it "--list shows installed components with a checkmark"; then
    out="$(bash setup.sh --list 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"✓"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | head -10)"; fi
fi

