# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Dependency resolution ──────────────────────────────────────────────────
describe "dependency resolution"

if it "pulls in transitive requirements"; then
    catalog_resolve claude-code >/dev/null
    assert_contains "$PLAN_IDS" "nodejs"
fi

if it "orders dependencies before dependents"; then
    catalog_resolve claude-code >/dev/null
    order=""; for id in $PLAN_IDS; do order+="$id "; done
    node_pos=0; cc_pos=0; n=0
    for id in $order; do
        n=$((n+1))
        [[ "$id" == "nodejs" ]] && node_pos=$n
        [[ "$id" == "claude-code" ]] && cc_pos=$n
    done
    if (( node_pos > 0 && node_pos < cc_pos )); then pass
    else fail "nodejs at $node_pos, claude-code at $cc_pos in [$order]"; fi
fi

if it "flags auto-added dependencies"; then
    catalog_resolve claude-code >/dev/null
    assert_contains "$PLAN_AUTO" "nodejs"
fi

if it "does not flag what was explicitly requested"; then
    catalog_resolve claude-code nodejs >/dev/null
    assert_not_contains "$PLAN_AUTO" "nodejs"
fi

if it "resolves a multi-level chain"; then
    catalog_resolve powerlevel10k >/dev/null
    ok=1
    for want in zsh git oh-my-zsh powerlevel10k; do
        [[ " $PLAN_IDS " == *" $want "* ]] || ok=0
    done
    if (( ok )); then pass; else fail "chain incomplete: $PLAN_IDS"; fi
fi

