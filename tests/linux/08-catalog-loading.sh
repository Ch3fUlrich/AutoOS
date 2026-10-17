# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Catalog loading (the tab-delimiter regression) ─────────────────────────
describe "catalog loading"
detect_system
catalog_load catalog/linux.json x64 0

if it "loads every component"; then
    [[ ${#CAT_ID[@]} -gt 20 ]] && pass || fail "only ${#CAT_ID[@]} components loaded"
fi

if it "fields do not shift when 'requires' is empty"; then
    # Regression: tab is an IFS whitespace char, so consecutive tabs collapsed
    # and every empty field shifted the remaining columns left.
    i="$(catalog_index_of git)"
    assert_eq "${CAT_PROFILES[i]}" "workstation,ai-coding,light,server"
fi

if it "group is populated, not swallowed into another column"; then
    i="$(catalog_index_of git)"
    assert_eq "${CAT_GROUP[i]}" "Core CLI"
fi

if it "postInstall stays in its own column"; then
    i="$(catalog_index_of monitoring)"
    assert_eq "${CAT_POST[i]}" "install_fastfetch"
fi

if it "arch filter hides x64-only entries on arm64"; then
    catalog_load catalog/linux.json arm64 0
    if catalog_index_of antigravity >/dev/null 2>&1; then
        fail "antigravity is x64-only but appeared on arm64"
    else pass; fi
    catalog_load catalog/linux.json x64 0
fi

if it "headless hides the desktop category"; then
    catalog_load catalog/linux.json x64 1
    if catalog_index_of firefox >/dev/null 2>&1; then
        fail "desktop component offered on a headless machine"
    else pass; fi
    catalog_load catalog/linux.json x64 0
fi

