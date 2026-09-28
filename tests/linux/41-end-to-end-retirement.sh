# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── End-to-end retirement (dry run only) ───────────────────────────────────
# Split out of tests/linux/13-end-to-end-dry-run-only.sh (WS-PART13): part 13
# alone was the slowest CI shard, so these four retirement tests run here on
# their own shard (`g 41` in tests/ci-shards.txt) in parallel with it. All four
# drive the real entry point over the shipped catalogs through the memo defined
# in part 13 (memo_dry_run; parts are sourced in number order, so it is already
# defined when this file is sourced): each inspects only output + rc.
describe "end-to-end retirement (dry run only)"

if it "--only agent-skills plans the components that took its work"; then
    # A7: agent-skills is a tombstone, so a hand-chosen retired id is expanded
    # before the plan (docs/catalog.md, "replaced_by"): the row that was chosen
    # stays and reports `skipped: retired`, and the successors do the work the
    # state file, the menu or the flag could no longer ask of it.
    memo_dry_run out rc e2e -- --only agent-skills --dry-run --yes --no-color
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
        memo_dry_run out rc e2e -- --profile "$profile" --dry-run --yes --no-color
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
    # took it verbatim booked the retirement and installed nothing. The memo key
    # holds this run's mktemp path, so it is always a miss — safe, and the file
    # still exists while the memo runs it (it is removed after).
    state="$(mktemp)"
    printf '%s\n' '{"profile": "custom", "selected": ["agent-skills"], "answers": {}}' > "$state"
    memo_dry_run out rc e2e -- --from-state "$state" --dry-run --yes --no-color
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
    # see e2e_setup.) A memo hit on the ai-coding entry the loop above ran in a
    # full-suite run: same argv, same env, same e2e home mode.
    memo_dry_run out rc e2e -- --profile ai-coding --dry-run --yes --no-color
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
