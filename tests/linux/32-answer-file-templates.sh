# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Answer-file templates ──────────────────────────────────────────────────
describe "answer file templates"

if it "no template contains a credential"; then
    # A1 was a committed PLAINTEXT password, and the only alternative that
    # could ever have caught one — `password[[:space:]]+[^ ]` — cannot match
    # YAML, where `password` is followed by `:`. Setting
    # `password: "hunter2RealPassword"` in user-data.example passed this test.
    # The `password[[:space:]]*:` alternative below is the one that fires on
    # the shape the actual incident had; `\$6\$` keeps covering crypt hashes.
    cred_pattern='(\$6\$'
    cred_pattern+='|password[[:space:]]+[^ ]'
    cred_pattern+='|password[[:space:]]*:[[:space:]]*["'"'"']?[^C]'
    cred_pattern+='|passwd/user-password)'
    if grep -rnE "$cred_pattern" templates/ | grep -v 'CHANGE-ME'; then
        fail "a template carries something that looks like a real credential"
    else pass; fi
fi

if it "no template wipes a disk without being asked"; then
    # A3 was an unprompted whole-disk wipe. The old form of this test reduced
    # to "does the string AUTOOS_WIPE_TARGET_DISK appear anywhere under
    # templates/" — and it appears only in a comment, so it was assert(true):
    # replacing `interactive-sections: [storage]` with a live `storage:` block
    # passed. Assert the actual gate in each file instead.
    wipe_problems=""

    # user-data.example: subiquity only stops and asks a human when "storage"
    # is listed under interactive-sections...
    ia_sections="$(awk '
        /^[[:space:]]*interactive-sections:[[:space:]]*$/ { grab = 1; next }
        grab && /^[[:space:]]*-[[:space:]]*[A-Za-z]/ {
            sub(/^[[:space:]]*-[[:space:]]*/, "")
            print
            next
        }
        grab && /^[[:space:]]*#/ { next }
        grab                     { grab = 0 }
    ' templates/user-data.example)"
    [[ " $(printf '%s' "$ia_sections" | tr '\n' ' ') " == *" storage "* ]] \
        || wipe_problems+="user-data.example does not list 'storage' under interactive-sections; "

    # ...and only as long as no live top-level `storage:` key overrides it.
    # A commented one (`# storage:`) is the documented, inert example and is
    # not matched: `[[:space:]]*` cannot consume a '#'.
    live_storage="$(grep -nE '^[[:space:]]*storage:' templates/user-data.example || true)"
    [[ -z "$live_storage" ]] \
        || wipe_problems+="user-data.example has an uncommented storage: block ($live_storage); "

    # preseed.cfg.example: d-i partitions automatically only once
    # partman-auto/method is set. Unset (or commented) means it asks.
    live_partman="$(grep -nE '^[[:space:]]*(d-i[[:space:]]+)?partman-auto/method' templates/preseed.cfg.example || true)"
    [[ -z "$live_partman" ]] \
        || wipe_problems+="preseed.cfg.example sets partman-auto/method ($live_partman); "

    if [[ -n "$wipe_problems" ]]; then
        fail "automatic partitioning is not gated behind an interactive prompt: $wipe_problems"
    else pass; fi
fi

if it "rescue-bootstrap.sh installs exactly the catalog's rescue-profile apt packages (template)"; then
    # The catalog (catalog/linux.json) is the single source of truth for what
    # "rescue tooling" means. This is finding A12 in the installer-USB plan:
    # the old scripts kept a second, hand-written package list that quietly
    # drifted from the catalog. Cross-checking the two here means it can't
    # drift again without a test noticing.
    #
    # It enforced only half of that, and by the weakest possible means:
    # `grep -q -w "$pkg" templates/rescue-bootstrap.sh` matched COMMENTED-OUT
    # lines, so commenting out the volume-management and cloning groups —
    # removing LVM, RAID, LUKS and cloning from the rescue toolkit — still
    # passed. It also never looked the other way, so a package in the script
    # but not the catalog was invisible. Both are fixed below: the script's
    # list is parsed out of its live `install_apt_group` calls, and the two
    # lists are compared as sets in both directions.
    catalog_pkgs="$(python3 -c "
import json
data = json.load(open('catalog/linux.json'))
pkgs = set()
for cat in data['categories']:
    for c in cat['components']:
        if c.get('provider') == 'apt' and 'rescue' in c.get('profiles', []):
            pkgs.add(c['package'])
print('\n'.join(sorted(pkgs)))
" | tr -d '\r')"

    # Every argument of every live `install_apt_group "<group>" pkg...` call,
    # minus the group name. A leading '#' disqualifies the whole line, which
    # is the specific mutation the old test could not see.
    script_pkgs="$(awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*install_apt_group[[:space:]]/ {
            line = $0
            sub(/^[[:space:]]*install_apt_group[[:space:]]+/, "", line)
            # Drop the group name, quoted or not — exactly one of these.
            if (line ~ /^"/) sub(/^"[^"]*"[[:space:]]*/, "", line)
            else             sub(/^[^[:space:]]+[[:space:]]*/, "", line)
            n = split(line, a, /[[:space:]]+/)
            for (i = 1; i <= n; i++) if (a[i] != "") print a[i]
        }
    ' templates/rescue-bootstrap.sh | tr -d '\r' | LC_ALL=C sort -u)"

    # LC_ALL=C on both sides: comm needs one collation order, and a locale that
    # ignores hyphens would reorder names like lm-sensors against lvm2.
    catalog_sorted="$(printf '%s\n' "$catalog_pkgs" | sed '/^$/d' | LC_ALL=C sort -u)"
    script_sorted="$(printf '%s\n' "$script_pkgs" | sed '/^$/d')"

    only_in_catalog="$(comm -23 <(printf '%s\n' "$catalog_sorted") \
                                <(printf '%s\n' "$script_sorted") | tr '\n' ' ')"
    only_in_script="$(comm -13 <(printf '%s\n' "$catalog_sorted") \
                               <(printf '%s\n' "$script_sorted") | tr '\n' ' ')"

    if [[ -z "${only_in_catalog// /}" && -z "${only_in_script// /}" ]]; then
        pass
    else
        fail "catalog-only: [${only_in_catalog% }] | bootstrap-only: [${only_in_script% }]"
    fi
fi

if it "the rescue bootstrap template is shellcheck clean"; then
    files=(templates/rescue-bootstrap.sh templates/ai-dispatcher.sh)
    if has_cmd shellcheck; then
        run_shellcheck "${files[@]}"; report_shellcheck "$?"
    elif has_cmd docker && docker info >/dev/null 2>&1; then
        # MSYS_NO_PATHCONV: on Windows Git Bash, MSYS mangles the bare "/mnt"
        # argument into a host path before docker ever sees it. A no-op
        # elsewhere (native Linux/macOS docker never looks at this var).
        out="$(MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable \
               -S warning "${files[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "(via docker) $(printf '%s' "$out" | head -20)"; fi
    else
        skip "no shellcheck binary and no usable docker"
    fi
fi

if it "[trusted=yes] never appears on a real network apt source line (template)"; then
    # [trusted=yes] belongs only on the one local, generated-on-this-stick
    # file:// repo. The old form rejected exactly two literal hostnames, so
    # `deb [trusted=yes] http://mirror.corp/ubuntu jammy main` sailed through.
    # Inverted: every live (non-comment) line that carries trusted=yes must
    # also carry file: — which no network source ever can.
    offenders="$(grep -rn 'trusted=yes' templates/ \
                 | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' \
                 | grep -v 'file:' || true)"
    if [[ -n "$offenders" ]]; then
        fail "[trusted=yes] on a line that is not a local file:// source: ${offenders//$'\n'/ | }"
    else pass; fi
fi

if it "rescue-bootstrap registers the local offline cache as an unsigned local apt source, idempotently (template)"; then
    # Hermetic: an isolated fake "stick root" with a fake cache and a fake
    # apt-get, and AUTOOS_OFFLINE_APT_LIST redirected to a temp file so this
    # never touches a real /etc/apt/sources.list.d. A real copy of the
    # template (minus the trailing `main "$@"` call) is sourced from inside
    # the fake stick root so SCRIPT_DIR resolves exactly as it would on the
    # real stick, next to a real "rescue/debs" cache directory — see
    # detect_offline_cache() in templates/rescue-bootstrap.sh.
    fake_stick="$(mktemp -d)"
    mkdir -p "$fake_stick/rescue/debs"
    : > "$fake_stick/rescue/debs/Packages.gz"
    sed '$d' templates/rescue-bootstrap.sh > "$fake_stick/rescue-bootstrap.sh"

    fakebin="$(mktemp -d)"
    cat >"$fakebin/apt-get" <<'EOS'
#!/usr/bin/env bash
printf 'apt-get %s\n' "$*" >>"$FAKE_APT_LOG"
exit 0
EOS
    chmod +x "$fakebin/apt-get"

    fake_list="$fake_stick/autoos-offline.list"
    fake_apt_log="$fake_stick/apt.log"

    out="$(
        PATH="$fakebin:/usr/bin:/bin" \
        AUTOOS_OFFLINE_APT_LIST="$fake_list" \
        AUTOOS_SUDO="" \
        FAKE_APT_LOG="$fake_apt_log" \
        bash -c '
            source "$1"
            NETWORK_OK="no"   # force the offline branch without a real network probe
            register_offline_cache
            printf "REGISTERED=%s\n" "$OFFLINE_CACHE_REGISTERED"
            if apt_can_install; then printf "CAN_INSTALL=yes\n"; else printf "CAN_INSTALL=no\n"; fi
            register_offline_cache   # second call: must be idempotent, not append a duplicate line
        ' _ "$fake_stick/rescue-bootstrap.sh" 2>&1
    )"
    rc=$?
    list_content="$(cat "$fake_list" 2>/dev/null)"
    list_line_count="$(grep -c '^deb \[trusted=yes\]' "$fake_list" 2>/dev/null || true)"
    abs_cache="$(cd "$fake_stick/rescue/debs" && pwd)"
    rm -rf "$fake_stick" "$fakebin"

    if [[ $rc -eq 0 \
        && "$out" == *"UNSIGNED"* \
        && "$out" == *"REGISTERED=1"* \
        && "$out" == *"CAN_INSTALL=yes"* \
        && "$out" == *"offline cache already registered"* \
        && "$list_content" == *"deb [trusted=yes] file:${abs_cache} ./"* \
        && "$list_line_count" -eq 1 ]]; then
        pass
    else
        fail "rc=$rc list_lines=$list_line_count out=${out:0:400}"
    fi
fi

# ─── rescue-bootstrap double-run fixture (AGENTS.md §4) ─────────────────────
# install_rescue_tools, install_nodejs, install_ai_clis, install_ai_dispatcher,
# write_ai_profile and main() had ZERO execution coverage, and that is exactly
# how three idempotency defects survived to the final gate: both files an
# operator can edit were overwritten wholesale on every run, and a second run
# reported "installed=2 skipped=0" while the script's own header claimed it
# reported "skipped". Nothing static could catch that — only running it twice.
#
# Hermetic. apt-get, dpkg, npm, curl, sudo and id are all stubs on a fake PATH,
# AUTOOS_ROOT_PREFIX points every destination at a temp directory, and nothing
# is installed for real. Two deliberate choices:
#
#   * the fake `id` always reports a non-root uid, so require_root() takes the
#     sudo path. That is the normal Ubuntu live session (user "ubuntu" with
#     passwordless sudo) and the only path on which a privileged command that
#     forgot $AUTOOS_SUDO shows up at all.
#   * the fake `npm` is on the PATH from the start rather than being created by
#     the fake apt-get. A developer machine may well have a real /usr/bin/npm,
#     and `$AUTOOS_SUDO npm install -g` finding it would install software for
#     real — which no test in this suite may ever do. The cost is that
#     install_nodejs takes its "already installed" branch here; that branch is
#     asserted instead. PATH is otherwise restricted to /usr/bin:/bin for the
#     same reason the ai-dispatcher tests below restrict it.
bootstrap_sandbox_setup() {
    BS_STICK="$(mktemp -d)"
    BS_ROOT="$BS_STICK/fakeroot"
    BS_BIN="$BS_STICK/fakebin"
    BS_LOG="$BS_STICK/cmd.log"
    BS_DPKG_DB="$BS_STICK/dpkg-db"
    BS_REGISTRY="$BS_ROOT/etc/autoos/ai-clients.conf"
    BS_PROFILE="$BS_ROOT/etc/profile.d/autoos-ai.sh"
    BS_AI_BIN="$BS_ROOT/usr/local/bin/ai"
    mkdir -p "$BS_ROOT" "$BS_BIN"
    : > "$BS_LOG"
    : > "$BS_DPKG_DB"

    cp templates/rescue-bootstrap.sh templates/ai-clients.conf \
       templates/ai-dispatcher.sh templates/ollama-chat.sh "$BS_STICK/"

    cat > "$BS_BIN/id" <<'EOS'
#!/usr/bin/env bash
[[ "${1:-}" == "-u" ]] && { printf '1000\n'; exit 0; }
exit 1
EOS
    cat > "$BS_BIN/sudo" <<'EOS'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >>"$FAKE_CMD_LOG"
# Marks the child as privileged so a stub can record which side of
# $AUTOOS_SUDO it was invoked from. Real sudo scrubs the environment; here the
# point is precisely to observe that it was used at all.
export FAKE_SUDO_ACTIVE=1
exec "$@"
EOS
    cat > "$BS_BIN/curl" <<'EOS'
#!/usr/bin/env bash
# Covers two call shapes: network_reachable()'s `--head` probe (just needs
# exit 0) and install_agy_cli()'s `-o <file> <url>` download, which needs to
# leave behind something that looks like a real installer script (starts
# with "#!", non-empty) so install_agy_cli()'s own safety check passes. The
# written "installer" drops a fake `agy` binary into $FAKE_BIN_DIR — the same
# directory npm's fake binaries land in below — the way the real one would
# install a real binary somewhere on PATH.
out=""
prev=""
for a in "$@"; do
    [[ "$prev" == "-o" ]] && out="$a"
    prev="$a"
done
if [[ -n "$out" ]]; then
    cat > "$out" <<'INSTALLER'
#!/usr/bin/env bash
: > "$FAKE_BIN_DIR/agy"
chmod +x "$FAKE_BIN_DIR/agy"
INSTALLER
fi
exit 0
EOS
    cat > "$BS_BIN/dpkg" <<'EOS'
#!/usr/bin/env bash
# pkg_installed() only ever calls `dpkg -s <pkg>`.
[[ "${1:-}" == "-s" ]] || exit 1
grep -qx -- "${2:-}" "$FAKE_DPKG_DB" 2>/dev/null
EOS
    # python3: a stateful stub, so install_oterm never runs a REAL
    # `pip install --user oterm` on the machine running the suite (the Linux
    # CI runner did exactly that, and installed it twice because
    # ~/.local/bin is not on the sandbox PATH). `pip install` records a
    # marker; `pip show oterm` reports it; everything else is a no-op.
    cat > "$BS_BIN/python3" <<'EOS'
#!/usr/bin/env bash
printf 'python3 %s\n' "$*" >>"$FAKE_CMD_LOG"
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
    case "${3:-}" in
        install) touch "$FAKE_DPKG_DB.pip-oterm"; exit 0 ;;
        show)    [[ -e "$FAKE_DPKG_DB.pip-oterm" ]]; exit $? ;;
    esac
fi
exit 0
EOS
    cat > "$BS_BIN/apt-get" <<'EOS'
#!/usr/bin/env bash
printf 'apt-get %s\n' "$*" >>"$FAKE_CMD_LOG"
mode=""
for a in "$@"; do
    case "$a" in
        update)  exit 0 ;;
        install) mode=install ;;
        -*)      ;;
        *)       [[ "$mode" == install ]] && printf '%s\n' "$a" >>"$FAKE_DPKG_DB" ;;
    esac
done
exit 0
EOS
    cat > "$BS_BIN/npm" <<'EOS'
#!/usr/bin/env bash
printf 'npm(sudo=%s) %s\n' "${FAKE_SUDO_ACTIVE:-0}" "$*" >>"$FAKE_CMD_LOG"
if [[ "${1:-}" == "install" ]]; then
    for a in "$@"; do
        bin=""
        [[ "$a" == "@anthropic-ai/claude-code" ]] && bin=claude
        [[ -n "$bin" ]] || continue
        printf '#!/usr/bin/env bash\nexit 0\n' >"$FAKE_BIN_DIR/$bin"
        chmod +x "$FAKE_BIN_DIR/$bin"
    done
fi
exit 0
EOS
    chmod +x "$BS_BIN"/*
}

bootstrap_sandbox_run() {
    PATH="$BS_BIN:/usr/bin:/bin" \
    AUTOOS_ROOT_PREFIX="$BS_ROOT" \
    AUTOOS_OFFLINE_APT_LIST="$BS_STICK/offline.list" \
    FAKE_CMD_LOG="$BS_LOG" \
    FAKE_DPKG_DB="$BS_DPKG_DB" \
    FAKE_BIN_DIR="$BS_BIN" \
    bash "$BS_STICK/rescue-bootstrap.sh" 2>&1
}

bootstrap_sandbox_backups() {
    find "$BS_ROOT" -name '*.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '
}

if it "rescue-bootstrap run twice installs nothing the second time and says skipped (template)"; then
    bootstrap_sandbox_setup

    out1="$(bootstrap_sandbox_run)"; rc1=$?
    installed1="$(printf '%s\n' "$out1" | grep -c '^  installed ' || true)"
    # B4: npm install -g writes to /usr/lib/node_modules, so it must be
    # privileged like every other install here. Without $AUTOOS_SUDO it fails
    # EACCES on exactly the path require_root() exists to support. Only
    # claude goes through npm now — agy is a standalone downloaded binary.
    npm_sudo="$(grep -cF 'npm(sudo=1) install -g ' "$BS_LOG" || true)"
    npm_bare="$(grep -cF 'npm(sudo=0)' "$BS_LOG" || true)"
    agy_installed1="$(printf '%s\n' "$out1" | grep -cF '  installed agy' || true)"

    out2="$(bootstrap_sandbox_run)"; rc2=$?
    installed2="$(printf '%s\n' "$out2" | grep -c '^  installed ' || true)"
    bs_backups="$(bootstrap_sandbox_backups)"
    marker_present=0; [[ -f "$BS_ROOT/var/lib/autoos/rescue-bootstrap.done" ]] && marker_present=1

    rm -rf "$BS_STICK"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$installed1" -gt 0 \
        && "$npm_sudo" -eq 1 && "$npm_bare" -eq 0 \
        && "$agy_installed1" -eq 1 \
        && "$installed2" -eq 0 \
        && "$out2" == *"0 installed,"* \
        && "$out2" == *"ai registry"*"every shipped backend already registered"* \
        && "$out2" == *"ai dispatcher"*"unchanged"* \
        && "$out2" == *"autoos-ai.sh (unchanged)"* \
        && "$bs_backups" -eq 0 \
        && "$marker_present" -eq 1 ]]; then
        pass
    else
        fail "rc1=$rc1 rc2=$rc2 installed1=$installed1 installed2=$installed2 npm_sudo=$npm_sudo npm_bare=$npm_bare agy_installed1=$agy_installed1 backups=$bs_backups marker=$marker_present | run2='${out2:0:600}'"
    fi
fi

if it "rescue-bootstrap keeps the operator's API key and extra AI backend on a second run (template)"; then
    # The two files an operator edits. templates/ai-clients.conf documents
    # itself as THE extension point ("adding a third AI CLI later is a one-line
    # addition to THIS file") and /etc/profile.d/autoos-ai.sh says "Uncomment
    # and paste your own below" — and both were then replaced wholesale on the
    # next run, taking the operator's registered backend and their API key with
    # them (AGENTS.md hard rules 4 and 5).
    bootstrap_sandbox_setup

    out1="$(bootstrap_sandbox_run)"; rc1=$?

    # Now be the operator: register a third backend, drop one that isn't
    # installed on this machine, and paste an API key into the profile.
    # (Deliberately not key-shaped — this repository is public, AGENTS.md
    # hard rule 1.)
    printf 'ollama:ollama:Local Ollama\n' >> "$BS_REGISTRY"
    grep -v '^agy:' "$BS_REGISTRY" > "$BS_REGISTRY.edit" && mv "$BS_REGISTRY.edit" "$BS_REGISTRY"
    printf '# my own key, pasted here as the file invites\nexport ANTHROPIC_API_KEY=AUTOOS-TEST-FIXTURE-NOT-A-KEY\n' \
        >> "$BS_PROFILE"

    out2="$(bootstrap_sandbox_run)"; rc2=$?

    key_lines="$(grep -c 'AUTOOS-TEST-FIXTURE-NOT-A-KEY' "$BS_PROFILE" || true)"
    ollama_lines="$(grep -c '^ollama:ollama:Local Ollama$' "$BS_REGISTRY" || true)"
    claude_lines="$(grep -c '^claude:' "$BS_REGISTRY" || true)"
    agy_lines="$(grep -c '^agy:' "$BS_REGISTRY" || true)"
    profile_backups="$(find "$(dirname "$BS_PROFILE")" -name 'autoos-ai.sh.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' ')"
    registry_backups="$(find "$(dirname "$BS_REGISTRY")" -name 'ai-clients.conf.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' ')"

    rm -rf "$BS_STICK"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$key_lines" -eq 1 \
        && "$ollama_lines" -eq 1 \
        && "$claude_lines" -eq 1 \
        && "$agy_lines" -eq 1 \
        && "$profile_backups" -eq 1 \
        && "$registry_backups" -eq 1 \
        && "$out2" == *"kept your edits"* ]]; then
        pass
    else
        fail "rc1=$rc1 rc2=$rc2 key=$key_lines ollama=$ollama_lines claude=$claude_lines agy=$agy_lines profile_backups=$profile_backups registry_backups=$registry_backups | run2='${out2:0:600}'"
    fi
fi

# Same class as lib/linux/install.sh backup_path/backup_file (main fixed
# this there): rescue-bootstrap.sh's own backup_file used a plain
# <file>.autoos-backup-<stamp> name with a bare cp, so two edits backed up in
# the same second let the second cp overwrite the first backup and destroy
# it. Pin `date` on PATH (both call shapes it uses) so two runs land in the
# same "second".
if it "backup residual: rescue-bootstrap backs up the operator's profile twice in one second without overwriting (template)"; then
    bootstrap_sandbox_setup
    cat > "$BS_BIN/date" <<'EOS'
#!/usr/bin/env bash
case "$2" in
    '+%Y%m%d%H%M%S') echo '20260101000000' ;;
    *) echo '2026-01-01T00:00:00Z' ;;
esac
EOS
    chmod +x "$BS_BIN/date"

    bootstrap_sandbox_run >/dev/null   # first run: creates the profile fresh, no backup yet
    printf '# edit-1\n' >> "$BS_PROFILE"
    contentA="$(cat "$BS_PROFILE")"
    bootstrap_sandbox_run >/dev/null   # second run: profile differs from rendered -> first backup
    printf '# edit-2\n' >> "$BS_PROFILE"
    contentB="$(cat "$BS_PROFILE")"
    out3="$(bootstrap_sandbox_run)"    # third run, same pinned second -> must not clobber the first backup

    ok=1
    base="$BS_PROFILE.autoos-backup-20260101000000"
    [[ "$out3" == *"kept your edits"* ]] || { ok=0; echo "third run: $out3" >&2; }
    [[ -f "$base" ]] || { ok=0; echo "no first backup at the plain stamp name" >&2; }
    [[ -f "$base-1" ]] || { ok=0; echo "no second backup (overwrote the first?)" >&2; }
    [[ "$(cat "$base" 2>/dev/null)" == "$contentA" ]] || { ok=0; echo "first backup content wrong" >&2; }
    [[ "$(cat "$base-1" 2>/dev/null)" == "$contentB" ]] || { ok=0; echo "second backup content wrong" >&2; }
    [[ "$(find "$(dirname "$BS_PROFILE")" -maxdepth 1 -name 'autoos-ai.sh.autoos-backup-*' | wc -l | tr -d ' ')" == 2 ]] \
        || { ok=0; echo "expected exactly 2 backups" >&2; }
    [[ "$(cat "$BS_PROFILE")" == "$contentB" ]] || { ok=0; echo "the operator's file itself was rewritten" >&2; }
    rm -rf "$BS_STICK"
    if (( ok )); then pass; else fail "rescue-bootstrap overwrote a same-second profile backup"; fi
fi

# Same class in configuration/start-stack.sh: the OpenHands settings repair
# used a plain <file>.autoos-backup-<stamp> name in both its python
# (shutil.copy2) and its bash (cp) site, so two repairs in the same second
# let the second copy overwrite the first backup. AUTOOS_BACKUP_STAMP pins
# the python stamp so both runs land in the same "second". The repair code
# is the file's own, extracted, not a copy of it.
if it "backup residual: start-stack keeps two same-second OpenHands settings backups"; then
    d="$(mktemp -d)"
    sed -n "/^import json, os, shutil, sys, datetime$/,/^sys.exit(0)$/p" configuration/start-stack.sh >"$d/repair.py"
    f="$d/settings.json"
    printf '{"agent_settings": {"schema_version": 6, "mcp_config": {}}}' >"$f"
    contentA="$(cat "$f")"
    AUTOOS_BACKUP_STAMP=20260101-000000 python3 "$d/repair.py" "$f" >/dev/null
    printf '{"agent_settings": {"schema_version": 6, "mcp_config": {"s": {"command": "x"}}}}' >"$f"
    contentB="$(cat "$f")"
    AUTOOS_BACKUP_STAMP=20260101-000000 python3 "$d/repair.py" "$f" >/dev/null
    ok=1
    base="$f.autoos-backup-20260101-000000"
    [[ -f "$base" ]] || { ok=0; echo "no first backup at the plain stamp name" >&2; }
    [[ -f "$base-1" ]] || { ok=0; echo "no second backup (overwrote the first?)" >&2; }
    [[ "$(cat "$base" 2>/dev/null)" == "$contentA" ]] || { ok=0; echo "first backup content wrong" >&2; }
    [[ "$(cat "$base-1" 2>/dev/null)" == "$contentB" ]] || { ok=0; echo "second backup content wrong" >&2; }
    [[ "$(find "$d" -maxdepth 1 -name 'settings.json.autoos-backup-*' | wc -l | tr -d ' ')" == 2 ]] \
        || { ok=0; echo "expected exactly 2 backups" >&2; }
    if python3 - "$f" <<'PY'; then :; else ok=0; echo "the settings file itself was not repaired" >&2; fi
import json, sys
sys.exit(0 if json.load(open(sys.argv[1]))["agent_settings"]["schema_version"] == 4 else 1)
PY
    rm -rf "$d"
    if (( ok )); then pass; else fail "start-stack overwrote a same-second settings backup"; fi
fi

# Both start-stack sites only remove/replace the original after a successful
# copy: the bash move-aside (cp, stubbed by backup_fail_bin) and the python
# repair (shutil.copy2, failed through a sitecustomize wrapper - the stub
# only touches *.autoos-backup-* copies and delegates the rest).
if it "backup residual: start-stack leaves OpenHands settings in place when the backup copy fails"; then
    d="$(mktemp -d)"
    sed -n '/^ss_backup_path()/,/^}/p;/^ss_move_aside()/,/^}/p' configuration/start-stack.sh >"$d/fn.sh"
    sed -n "/^import json, os, shutil, sys, datetime$/,/^sys.exit(0)$/p" configuration/start-stack.sh >"$d/repair.py"
    mkdir -p "$d/shadow"
    cat >"$d/shadow/sitecustomize.py" <<'EOS'
import shutil as _s
_real_copy2 = _s.copy2
def _fail_copy2(src, dst, *a, **k):
    if ".autoos-backup-" in str(dst):
        raise OSError(28, "No space left on device (test stub)")
    return _real_copy2(src, dst, *a, **k)
_s.copy2 = _fail_copy2
EOS
    # shellcheck disable=SC1090
    source "$d/fn.sh"
    ok=1
    printf '{not json' >"$d/unparseable.json"; cp "$d/unparseable.json" "$d/unparseable.json.orig"
    bin="$(backup_fail_bin)"
    out="$(PATH="$bin:$PATH" ss_move_aside "$d/unparseable.json" 2>&1)"; rc=$?
    (( rc != 0 )) || { ok=0; echo "move-aside succeeded without a backup: rc=$rc" >&2; }
    [[ "$out" == *"could not back up $d/unparseable.json"* ]] || { ok=0; echo "no warning naming the file: ${out:0:300}" >&2; }
    cmp -s "$d/unparseable.json" "$d/unparseable.json.orig" || { ok=0; echo "the unparseable file was removed without a backup" >&2; }
    printf '{"agent_settings": {"schema_version": 6, "mcp_config": {}}}' >"$d/settings.json"
    cp "$d/settings.json" "$d/settings.json.orig"
    out="$(PYTHONPATH="$d/shadow" AUTOOS_BACKUP_STAMP=20260101-000000 python3 "$d/repair.py" "$d/settings.json" 2>&1)"; rc=$?
    (( rc != 0 )) || { ok=0; echo "repair succeeded without a backup: rc=$rc" >&2; }
    [[ "$out" == *"Could not back up $d/settings.json"* ]] || { ok=0; echo "no warning naming the file: ${out:0:300}" >&2; }
    cmp -s "$d/settings.json" "$d/settings.json.orig" || { ok=0; echo "the settings file was rewritten without a backup" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "start-stack drops settings it could not back up"; fi
fi

if it "ai dispatcher --list names all three backends, including local (template)"; then
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" bash templates/ai-dispatcher.sh --list 2>&1)"
    rc=$?
    if [[ $rc -eq 0 && "$out" == *"claude"* && "$out" == *"agy"* && "$out" == *"local"* && "$out" == *"ollama-chat"* ]]; then
        pass
    else
        fail "expected all three backends listed (rc=$rc): ${out:0:300}"
    fi
fi

if it "ai dispatcher local backend execs ollama-chat, never oterm (template)"; then
    # oterm is documented interactive-only (no piped/one-shot mode), so the
    # 'local' registry record must point at ollama-chat, not oterm — this
    # drives that fact through the real dispatcher rather than just reading
    # the registry file.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/ollama-chat" <<'EOS'
#!/usr/bin/env bash
printf 'ollama-chat-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/ollama-chat"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh local "why did this drive fail?" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "ollama-chat-fake:why did this drive fail?" ]]; then
        pass
    else
        fail "expected 'local' to exec ollama-chat verbatim (rc=$rc): ${out:0:200}"
    fi
fi

if it "ollama-chat wrapper runs the pinned model non-interactively when it is pulled (template)"; then
    fakebin="$(mktemp -d)"
    cat >"$fakebin/ollama" <<'EOS'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" ]]; then
    printf 'NAME\tID\tSIZE\tMODIFIED\n'
    printf 'qwen3:4b\tabc123\t2.5 GB\tnow\n'
    exit 0
fi
printf 'ollama-run:%s\n' "$*"
EOS
    chmod +x "$fakebin/ollama"
    out="$(PATH="$fakebin:/usr/bin:/bin" bash templates/ollama-chat.sh "diagnose this" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "ollama-run:run qwen3:4b diagnose this" ]]; then
        pass
    else
        fail "expected 'ollama run qwen3:4b ...' with no fallback warning (rc=$rc): ${out:0:200}"
    fi
fi

if it "ollama-chat wrapper falls back to whatever model IS pulled when the pinned default is missing (template)"; then
    fakebin="$(mktemp -d)"
    cat >"$fakebin/ollama" <<'EOS'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" ]]; then
    printf 'NAME\tID\tSIZE\tMODIFIED\n'
    printf 'qwen3:1.7b\tdef456\t1.4 GB\tnow\n'
    exit 0
fi
printf 'ollama-run:%s\n' "$*"
EOS
    chmod +x "$fakebin/ollama"
    out="$(PATH="$fakebin:/usr/bin:/bin" AUTOOS_OLLAMA_MODEL="qwen3:4b" bash templates/ollama-chat.sh "diagnose this" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 \
        && "$out" == *'model "qwen3:4b" is not pulled — using "qwen3:1.7b" instead'* \
        && "$out" == *"ollama-run:run qwen3:1.7b diagnose this"* ]]; then
        pass
    else
        fail "expected a fallback warning plus a run against the pulled model (rc=$rc): ${out:0:300}"
    fi
fi

if it "ai dispatcher falls back cleanly when the default binary is absent (template)"; then
    # Hermetic: a fake PATH provides only "agy", never touches the real
    # machine, and never installs anything. Restricted to /usr/bin:/bin so a
    # real claude/agy binary elsewhere on this machine's PATH (e.g. this
    # very agent's own `claude`) cannot leak into the test.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/agy" <<'EOS'
#!/usr/bin/env bash
printf 'agy-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/agy"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh "hello" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == *"agy-fake:hello"* && "$out" == *"not installed"* ]]; then
        pass
    else
        fail "expected a clean fallback to agy (rc=$rc): ${out:0:200}"
    fi
fi

if it "ai dispatcher honours an explicit backend even when it differs from the default (template)"; then
    # Same hermetic fake-PATH approach, but now both backends "exist" and the
    # caller names one explicitly — the dispatcher must not substitute a
    # different backend once the caller has been specific.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/claude" <<'EOS'
#!/usr/bin/env bash
printf 'claude-fake:%s\n' "$*"
EOS
    cat >"$fakebin/agy" <<'EOS'
#!/usr/bin/env bash
printf 'agy-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/claude" "$fakebin/agy"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh agy "hi" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "agy-fake:hi" ]]; then
        pass
    else
        fail "expected the explicitly-named backend to run (rc=$rc): ${out:0:200}"
    fi
fi

if it "ai dispatcher warns once on an unrecognized backend-shaped token instead of misrouting silently (template)"; then
    # D1: `ai gemeni "..."` (a typo) used to silently run the default backend
    # with "gemeni" as literal prompt text and exit 0 — no signal that the
    # backend name was never recognized. It must still fall through to the
    # default (real ambiguity: `ai "why did this fail?"` is a single arg and
    # must never be treated as an unknown backend), but now with one warning.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/claude" <<'EOS'
#!/usr/bin/env bash
printf 'claude-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/claude"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh gemeni "hi" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 \
        && "$out" == *'ai: "gemeni" is not a known backend, treating it as part of the prompt; see ai --list'* \
        && "$out" == *"claude-fake:gemeni hi"* ]]; then
        pass
    else
        fail "expected a warning plus the default backend fed 'gemeni hi' verbatim (rc=$rc): ${out:0:300}"
    fi
fi

if it "ai dispatcher prints no warning for a single-argument prompt, even one shaped like an id (template)"; then
    # The other half of D1's ambiguity guard: a lone word with nothing after
    # it must never be second-guessed as an unrecognized backend.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/claude" <<'EOS'
#!/usr/bin/env bash
printf 'claude-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/claude"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh diagnose 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "claude-fake:diagnose" && "$out" != *"not a known backend"* ]]; then
        pass
    else
        fail "expected no warning for a single-word prompt (rc=$rc): ${out:0:200}"
    fi
fi

