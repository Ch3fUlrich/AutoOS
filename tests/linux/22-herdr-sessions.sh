# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "herdr-sessions"

# Boot-restore engine for Claude Code sessions in Herdr
# (configuration/herdr-sessions/, imported from the Server repo's
# Applications/herdr-sessions at 12f0ff7; the catalog component and its
# dispatch are tested further down). Dry-run only: no live systemctl, no live herdr --
# install.sh --dry-run never execs either (its `run()` wrapper only echoes
# "would: ...", it never calls systemctl or herdr for real), so nothing here
# needs an env/PATH stub the way a live-call test would.
if it "herdr-sessions: smoke (bash -n, py_compile, install --dry-run)"; then
    out="$(bash configuration/herdr-sessions/tests/test_smoke.sh 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Finding 6 (qoder review, L1-backlog.review-herdr-qoder.md, low): prof_key's
# `tr -d '[:space:]'` deleted whitespace INSIDE the value too, not just around
# it -- HS_WORKDIR=/opt/My Files parsed as /opt/MyFiles, the units installed
# cleanly, and the service would fail at boot with a nonexistent
# WorkingDirectory, nothing at install time saying so. prof_key also parses
# HERDR_BIN (used only for the system-scope dry-run precondition message,
# which is safe to run with no root and no stubs), so that key -- not
# HS_WORKDIR, which render_unit only ever splices into the system-scope
# herdr-server.service and a REAL system-scope install needs root and writes
# to a real /etc, disallowed for this suite -- is what exercises prof_key's
# trimming here without live systemctl or a real /etc write.
if it "herdr-sessions: prof_key trims only leading/trailing whitespace, not spaces inside the value"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=system
HERDR_BIN=$tmp/My Herdr/bin/herdr
FALLBACK=none
EOF
    out="$(bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" --dry-run 2>&1)"; rc=$?
    rm -rf "$tmp"
    if [ "$rc" = "0" ] && [[ "$out" == *"herdr at $tmp/My Herdr/bin/herdr"* ]]; then
        pass
    else
        fail "rc=$rc out=${out:0:400}"
    fi
fi

# Bug (b) from the proposal doc: which uuid a restored pane resumes, when a
# background job's transcript shares the pane's own directory (unit tests).
if it "herdr-sessions: uuid picking excludes background sessions (unit tests)"; then
    out="$(python3 tests/test_herdr_sessions.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# ADDENDUM item 7: --profile accepts a path as well as a name under profiles/,
# a re-run reports "already current" (cmp) or backs up a differing unit before
# replacing it, and --unregister removes exactly what was installed. These
# ARE real (non-dry-run) installs -- into a throwaway HOME, with systemctl and
# loginctl stubbed via PATH so no live systemd is ever touched.
hs_stub_bin() {
    local dir="$1"
    mkdir -p "$dir"
    cat > "$dir/systemctl" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    cat > "$dir/loginctl" <<'STUB'
#!/usr/bin/env bash
[ "$1" = "show-user" ] && echo yes
exit 0
STUB
    chmod +x "$dir/systemctl" "$dir/loginctl"
}

if it "herdr-sessions: install.sh accepts an absolute-path profile, not only a name"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    out="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" 2>&1)"; rc=$?
    got="$(grep -h HERDR_PROFILE "$tmp/home/.config/systemd/user/herdr-sessions-restore.service" 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$rc" = "0" ] && [ "$got" = "Environment=HERDR_PROFILE=$tmp/site.conf" ]; then
        pass
    else
        fail "rc=$rc got=[$got] out=$out"
    fi
fi

# Finding 5 (qoder review, L1-backlog.review-herdr-qoder.md, low): render_unit
# spliced user-supplied paths into sed replacement text with no escaping -- a
# profile at /srv/a&b/hs.conf rendered HERDR_PROFILE=/srv/a@PROFILE_PATH@b/hs.conf
# (& expands to the matched token in sed's replacement), the install reported
# success, and restore would fail at the next boot with a bogus profile path.
if it "herdr-sessions: render_unit renders a profile path containing '&' literally, not as a sed backreference"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    site_dir="$tmp/a&b"; mkdir -p "$site_dir"
    conf="$site_dir/hs.conf"
    cat > "$conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    out="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$conf" 2>&1)"; rc=$?
    got="$(grep -h HERDR_PROFILE "$tmp/home/.config/systemd/user/herdr-sessions-restore.service" 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$rc" = "0" ] && [ "$got" = "Environment=HERDR_PROFILE=$conf" ]; then
        pass
    else
        fail "rc=$rc got=[$got] out=${out:0:300}"
    fi
fi

if it "herdr-sessions: re-run reports already current; a drifted unit is backed up before replacing"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" >/dev/null 2>&1
    unit="$tmp/home/.config/systemd/user/herdr-sessions-restore.service"
    rerun="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" 2>&1)"
    echo "# hand edit" >> "$unit"
    drift="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" 2>&1)"
    n_bak="$(find "$tmp/home/.config/systemd/user" -maxdepth 1 -name '*autoos-backup*' | wc -l | tr -d ' ')"
    rm -rf "$tmp"
    ok=1
    printf '%s\n' "$rerun" | grep -q "herdr-sessions-restore.service: already current" || ok=0
    printf '%s\n' "$drift" | grep -q "herdr-sessions-restore.service: differs from the installed copy -- backed up" || ok=0
    [ "$n_bak" = "1" ] || ok=0
    if [ "$ok" = "1" ]; then pass; else fail "rerun=[$rerun] drift=[$drift] backups=$n_bak"; fi
fi

if it "herdr-sessions: install.sh prints the all-current summary only when no unit changed"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    printf 'HS_SCOPE=user\nHS_WORKDIR=%s/proj\nFALLBACK=none\n' "$tmp" > "$tmp/site.conf"
    run_hs() { HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" 2>&1; }
    first="$(run_hs)"; rerun="$(run_hs)"
    echo "# hand edit" >> "$tmp/home/.config/systemd/user/herdr-sessions-restore.service"
    drift="$(run_hs)"
    rm -rf "$tmp"
    ok=1
    [[ "$first" != *"herdr-sessions: all units already current"* ]] || { ok=0; echo "fresh install claims all current" >&2; }
    [[ "$rerun" == *"herdr-sessions: all units already current"* ]] || { ok=0; echo "unchanged re-run lacks the summary" >&2; }
    [[ "$drift" != *"herdr-sessions: all units already current"* ]] || { ok=0; echo "a run that replaced a unit claims all current" >&2; }
    if (( ok )); then pass; else fail "first=[${first:0:200}] rerun=[${rerun:0:200}] drift=[${drift:0:300}]"; fi
fi

if it "herdr-sessions: two drifted re-runs in the same second keep two distinct backups"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    printf '#!/usr/bin/env bash\necho 20260926120000\n' > "$stub/date"; chmod +x "$stub/date"
    printf 'HS_SCOPE=user\nHS_WORKDIR=%s/proj\nFALLBACK=none\n' "$tmp" > "$tmp/site.conf"
    run_hs() { HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" >/dev/null 2>&1; }
    unit="$tmp/home/.config/systemd/user/herdr-sessions-restore.service"
    run_hs
    echo "# edit one" >> "$unit"; run_hs
    echo "# edit two" >> "$unit"; run_hs
    n="$(find "$tmp/home/.config/systemd/user" -maxdepth 1 -name 'herdr-sessions-restore.service.autoos-backup*' | wc -l | tr -d ' ')"
    one="$(grep -l '# edit one' "$tmp"/home/.config/systemd/user/herdr-sessions-restore.service.autoos-backup* 2>/dev/null | wc -l)"
    rm -rf "$tmp"
    if [[ "$n" == 2 && "$one" -ge 1 ]]; then pass; else fail "backups=$n, backups holding the first edit=$one (a same-second backup overwrote the earlier one)"; fi
fi

if it "herdr-sessions: --unregister removes what it installed, twice is 'nothing to remove'"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" >/dev/null 2>&1
    first="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" --unregister 2>&1)"
    left="$(find "$tmp/home/.config/systemd/user" -mindepth 1 -maxdepth 1 ! -name '*autoos-backup*' 2>/dev/null | wc -l | tr -d ' ')"
    second="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" --unregister 2>&1)"
    rm -rf "$tmp"
    if printf '%s\n' "$first" | grep -q "removed 5 unit(s)" && [ "$left" = "0" ] && printf '%s\n' "$second" | grep -q "nothing to remove"; then
        pass
    else
        fail "first=[$first] left=$left second=[$second]"
    fi
fi

# Finding 2 (qoder review, L1-backlog.review-herdr-qoder.md, medium):
# remove_unit's backup had no taken-suffix loop, and cp -p overwrites -- an
# --unregister backup in the same second as a drifted install's own backup
# silently destroyed the only copy of the user's original unit. Both paths
# now share one helper (unique_backup_path + back_up_or_die) instead of
# drifting apart.
if it "herdr-sessions: remove_unit's backup uses install_unit's collision loop -- a same-second unregister keeps both backups"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    printf '#!/usr/bin/env bash\necho 20260926130000\n' > "$stub/date"; chmod +x "$stub/date"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    run_hs2() { HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" "$@" >/dev/null 2>&1; }
    run_hs2   # fresh install: no backup yet
    unit="$tmp/home/.config/systemd/user/herdr-sessions-restore.service"
    echo "# original edit" >> "$unit"
    run_hs2   # drift, same stubbed second: backs up "# original edit" content
    run_hs2 --unregister   # same stubbed second: must not overwrite that backup
    n="$(find "$tmp/home/.config/systemd/user" -maxdepth 1 -name 'herdr-sessions-restore.service.autoos-backup*' 2>/dev/null | wc -l | tr -d ' ')"
    original_kept="$(grep -l '# original edit' "$tmp"/home/.config/systemd/user/herdr-sessions-restore.service.autoos-backup* 2>/dev/null | wc -l)"
    rm -rf "$tmp"
    if [[ "$n" == 2 && "$original_kept" -ge 1 ]]; then
        pass
    else
        fail "backups=$n, backups holding the original edit=$original_kept (unregister overwrote the install backup)"
    fi
fi

if it "herdr-sessions: remove_unit is fail-closed -- a backup that cannot be written leaves the unit in place"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; hs_stub_bin "$stub"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" >/dev/null 2>&1
    unit="$tmp/home/.config/systemd/user/herdr-sessions-restore.service"
    cat > "$stub/cp" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
    chmod +x "$stub/cp"
    out="$(HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" --unregister 2>&1)"; rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "rc=0 although the backup copy failed: ${out:0:300}" >&2; }
    [[ "$out" == *"FATAL"*"back up"* ]] || { ok=0; echo "no FATAL backup message: ${out:0:300}" >&2; }
    [[ -f "$unit" ]] || { ok=0; echo "the unit was removed although its backup failed" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "remove_unit is not fail-closed on a backup failure"; fi
fi

# Finding 3 (qoder review, L1-backlog.review-herdr-qoder.md, medium):
# remove_unit disabled but never stopped, and install starts the snapshot
# timer with --now -- so --unregister left the timer active in memory,
# still firing and writing snapshots, while detection now (correctly, after
# finding 1/2's fixes) reports herdr-sessions gone -- letting claude-autostart
# install and run concurrently.
if it "herdr-sessions: --unregister disables --now (stops the timer) before removing, not disable alone"; then
    tmp="$(mktemp -d)"; stub="$tmp/stub"; mkdir -p "$stub"
    log="$tmp/systemctl.log"
    cat > "$stub/systemctl" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$log"
exit 0
STUB
    chmod +x "$stub/systemctl"
    cat > "$stub/loginctl" <<'STUB'
#!/usr/bin/env bash
[ "$1" = "show-user" ] && echo yes
exit 0
STUB
    chmod +x "$stub/loginctl"
    cat > "$tmp/site.conf" <<EOF
HS_SCOPE=user
HS_WORKDIR=$tmp/proj
FALLBACK=none
EOF
    run_hs3() { HOME="$tmp/home" PATH="$stub:$PATH" bash configuration/herdr-sessions/install.sh --profile "$tmp/site.conf" "$@" >/dev/null 2>&1; }
    run_hs3
    : > "$log"
    run_hs3 --unregister
    n="$(grep -c '^--user disable --now ' "$log" 2>/dev/null || true)"
    stale="$(grep -c '^--user disable [^-]' "$log" 2>/dev/null || true)"
    rm -rf "$tmp"
    if [[ "$n" -ge 1 && "$stale" -eq 0 ]]; then
        pass
    else
        fail "disable --now calls=$n, disable-without-now calls=$stale"
    fi
fi

# Item 8 (L0): a single pane process killed by the kernel OOM killer must not
# take the whole herdr-server unit down with it -- systemd's default
# OOMPolicy=stop did exactly that, twice, ending every pane in the session.
if it "herdr-sessions: both server unit templates set OOMPolicy=continue so an OOM-killed pane doesn't stop the whole service"; then
    ok=1
    for f in configuration/herdr-sessions/systemd/user/herdr-server.service configuration/herdr-sessions/systemd/system/herdr-server.service; do
        grep -q '^OOMPolicy=continue$' "$f" || { ok=0; echo "missing OOMPolicy=continue in $f" >&2; }
    done
    if (( ok )); then pass; else fail "OOMPolicy=continue missing from one or both server unit templates"; fi
fi

# herdr_stub_driver <dir> [mode]: writes a fake configuration/herdr-sessions/
# driver (the interface herdr-a imports separately - see spec.herdr-b.md's
# PARALLEL note: `install.sh --profile <path> [--dry-run]`) into
# <dir>/install.sh. --dry-run always prints a "would" line and NEVER touches
# <dir>/installed-marker, regardless of mode - that is exactly what "dry run
# changes nothing" checks. A real run's behaviour depends on mode:
#   installed (default) - touches the marker, prints "installed from <profile>"
#   current             - prints "already current", touches no marker (the
#                         driver's OWN idempotency, as on a second call)
#   fail                - exits 1
herdr_stub_driver() {
    local dir="$1" mode="${2:-installed}"
    mkdir -p "$dir"
    cat >"$dir/install.sh" <<STUB
#!/usr/bin/env bash
set -euo pipefail
profile=""; dry=0
while [[ \$# -gt 0 ]]; do
    case "\$1" in
        --profile) profile="\$2"; shift 2 ;;
        --dry-run) dry=1; shift ;;
        *) shift ;;
    esac
done
if (( dry )); then
    printf 'would restore panes from %s\n' "\$profile"
    exit 0
fi
case "$mode" in
    fail) printf 'driver: boom\n' >&2; exit 1 ;;
    current) printf '    x.service: already current\nherdr-sessions: all units already current\n'; exit 0 ;;
    partial) printf '    x.service: already current\n    y.service: installed\n'; exit 0 ;;
    *) printf 'installed\n' >"$dir/installed-marker"; printf 'installed from %s\n' "\$profile"; exit 0 ;;
esac
STUB
    chmod +x "$dir/install.sh"
}

# herdr_run <scratch> <driver-dir> <profile-or-empty> [dry:0|1] [plan_ids]
# One install_herdr_sessions call against a scratch SYS_HOME, with
# AUTOOS_HERDR_SESSIONS_DIR pointed at the stub driver (the test seam
# lib/linux/install.sh adds next to install_herdr_sessions for exactly this).
# INSTALL_SCRIPT_STATE cannot cross the subshell boundary back to the caller
# (lib/linux/install.sh's own note on that global), so it is printed as a
# trailer line and parsed back out - the pattern the antigravity tests use.
herdr_run() {
    local sb="$1" drv="$2" profile="$3" dry="${4:-0}" plan="${5:-}"
    (
        AUTOOS_ROOT="$ROOT"; SYS_HOME="$sb/home"; AUTOOS_DRY_RUN="$dry"
        AUTOOS_HERDR_SESSIONS_DIR="$drv"; PLAN_IDS="$plan"
        if [[ -n "$profile" ]]; then AUTOOS_ANSWERS[herdr_sessions_profile]="$profile"
        else unset 'AUTOOS_ANSWERS[herdr_sessions_profile]'; fi
        INSTALL_SCRIPT_STATE=""
        rc=0
        install_herdr_sessions || rc=$?
        printf 'HERDR_RESULT %s %s\n' "${INSTALL_SCRIPT_STATE:-installed}" "$rc"
    ) 2>&1
}
herdr_state() { sed -n 's/^HERDR_RESULT \([a-z]*\) [0-9]*$/\1/p' <<<"$1"; }
herdr_rc()    { sed -n 's/^HERDR_RESULT [a-z]* \([0-9]*\)$/\1/p' <<<"$1"; }

if it "herdr-sessions: a dry run calls the driver with --dry-run and changes nothing"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    out="$(herdr_run "$sb" "$drv" "$profile" 1)"
    rc="$(herdr_rc "$out")"
    (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:300}" >&2; }
    [[ "$out" == *"would run: bash"* && "$out" == *"--dry-run"* ]] || { ok=0; echo "no 'would run ... --dry-run' line: ${out:0:300}" >&2; }
    [[ "$out" == *"would restore panes from $profile"* ]] || { ok=0; echo "the driver's own dry-run output is missing: ${out:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "a dry run touched the driver's marker file" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "install_herdr_sessions dry run is not side-effect free"; fi
fi

# Finding 4 (qoder review, L1-backlog.review-herdr-qoder.md, low): the dry-run
# branch ran the driver uncaptured, so its output bypassed the ui_* layer --
# under NO_COLOR it printed the driver's raw ANSI escapes and none of its
# would-lines reached the AutoOS log file, unlike the real (non-dry) path,
# which captures and re-emits via ui_muted. Proven here via ui_muted's own
# side effect (it also calls _log, which writes to AUTOOS_LOG) rather than by
# stdout content alone, since stdout is captured either way by this test's
# own subshell.
if it "herdr-sessions: a dry run's driver output goes through ui_muted, reaching the AutoOS log like the real path"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile
' >"$profile"
    logfile="$sb/autoos.log"
    out="$( ( AUTOOS_ROOT="$ROOT"; SYS_HOME="$sb/home"; AUTOOS_DRY_RUN=1
              AUTOOS_HERDR_SESSIONS_DIR="$drv"; PLAN_IDS=""; AUTOOS_LOG="$logfile"
              AUTOOS_ANSWERS[herdr_sessions_profile]="$profile"
              INSTALL_SCRIPT_STATE=""
              install_herdr_sessions ) 2>&1 )"
    [[ "$out" == *"would restore panes from $profile"* ]] || { ok=0; echo "driver dry-run line missing from stdout: ${out:0:300}" >&2; }
    grep -q "would restore panes from $profile" "$logfile" 2>/dev/null \
        || { ok=0; echo "driver dry-run line never reached the AutoOS log (ui_muted bypassed): $(cat "$logfile" 2>/dev/null)" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "install_herdr_sessions dry-run does not route the driver's output through ui_muted"; fi
fi

if it "herdr-sessions: an empty profile answer is skipped, never a guessed path"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    out="$(herdr_run "$sb" "$drv" "")"
    state="$(herdr_state "$out")"; rc="$(herdr_rc "$out")"
    (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:300}" >&2; }
    [[ "$state" == skipped ]] || { ok=0; echo "state=[$state] want skipped" >&2; }
    [[ "$out" == *"skipped: no profile"* ]] || { ok=0; echo "no 'skipped: no profile' line: ${out:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "the driver ran although no profile was given" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "install_herdr_sessions guessed at a profile instead of skipping"; fi
fi

if it "herdr-sessions: a profile path that is not a regular file fails, naming the path"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/does-not-exist.conf"
    out="$(herdr_run "$sb" "$drv" "$profile")"
    rc="$(herdr_rc "$out")"
    (( rc != 0 )) || { ok=0; echo "rc=0 for a missing profile: ${out:0:300}" >&2; }
    [[ "$out" == *"$profile"* ]] || { ok=0; echo "the error does not name the path: ${out:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "the driver ran against a missing profile" >&2; }
    # A directory is not a regular file either.
    mkdir -p "$sb/adir"
    out2="$(herdr_run "$sb" "$drv" "$sb/adir")"; rc2="$(herdr_rc "$out2")"
    (( rc2 != 0 )) || { ok=0; echo "rc=0 for a directory given as the profile: ${out2:0:300}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "install_herdr_sessions did not refuse a bad profile path"; fi
fi

if it "herdr-sessions: a missing driver directory is a clear error, not a silent success"; then
    sb="$(mktemp -d)"; ok=1
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    out="$(herdr_run "$sb" "$sb/no-such-driver-dir" "$profile")"
    rc="$(herdr_rc "$out")"
    (( rc != 0 )) || { ok=0; echo "rc=0 with no driver present: ${out:0:300}" >&2; }
    [[ "$out" == *"driver not found"* && "$out" == *"$sb/no-such-driver-dir"* ]] || { ok=0; echo "no clear error naming the driver dir: ${out:0:300}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "install_herdr_sessions silently accepted a missing driver"; fi
fi

if it "herdr-sessions: the driver reporting already current is skipped, never installed a second time"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    out1="$(herdr_run "$sb" "$drv" "$profile")"
    state1="$(herdr_state "$out1")"; rc1="$(herdr_rc "$out1")"
    (( rc1 == 0 )) || { ok=0; echo "first run rc=$rc1: ${out1:0:300}" >&2; }
    [[ "$state1" != skipped ]] || { ok=0; echo "the FIRST run already reports skipped - the test is not isolating the second run" >&2; }
    [[ -e "$drv/installed-marker" ]] || { ok=0; echo "the driver never ran on the first call" >&2; }
    # AGENTS.md hard rule 3: safe to run twice, second run reports skipped. Here
    # the DRIVER is the one deciding it is current (its own idempotency) - the
    # dispatch must pass that straight through, not report installed again.
    herdr_stub_driver "$drv" current
    out2="$(herdr_run "$sb" "$drv" "$profile")"
    state2="$(herdr_state "$out2")"; rc2="$(herdr_rc "$out2")"
    (( rc2 == 0 )) || { ok=0; echo "second run rc=$rc2: ${out2:0:300}" >&2; }
    [[ "$state2" == skipped ]] || { ok=0; echo "second run state=[$state2] want skipped" >&2; }
    [[ "$out2" == *"already current"* ]] || { ok=0; echo "no 'already current' line: ${out2:0:300}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a second herdr-sessions run reported installed instead of skipped"; fi
fi

if it "herdr-sessions: refuses when claude-autostart was selected this run or is already installed, writing nothing"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"

    out="$(herdr_run "$sb" "$drv" "$profile" 0 "claude-autostart")"
    rc="$(herdr_rc "$out")"
    (( rc != 0 )) || { ok=0; echo "rc=0 with claude-autostart selected: ${out:0:300}" >&2; }
    [[ "$out" == *"claude-autostart"* ]] || { ok=0; echo "no mention of claude-autostart: ${out:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "the driver ran despite the conflict (selected)" >&2; }

    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/claude-sessions-restore.service"
    out2="$(herdr_run "$sb" "$drv" "$profile" 0 "")"
    state2="$(herdr_state "$out2")"; rc2="$(herdr_rc "$out2")"
    (( rc2 == 0 )) || { ok=0; echo "rc=$rc2 want 0 with claude-autostart already installed: ${out2:0:300}" >&2; }
    [[ "$state2" == skipped ]] || { ok=0; echo "state=[$state2] want skipped (installed case)" >&2; }
    [[ "$out2" == *"claude-autostart"* && "$out2" == *"skipped"* ]] || { ok=0; echo "no skip naming claude-autostart (installed case): ${out2:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "the driver ran despite the skip (installed)" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "herdr-sessions installed alongside claude-autostart"; fi
fi

if it "herdr-sessions: claude-autostart refuses when herdr-sessions was selected this run or is already installed, writing nothing"; then
    sb="$(mktemp -d)"; ok=1
    out="$(autostart_run_full "$sb" 0 "herdr-sessions")"
    rc="$(autostart_rc "$out")"
    (( rc != 0 )) || { ok=0; echo "rc=0 with herdr-sessions selected: ${out:0:300}" >&2; }
    [[ "$out" == *"herdr-sessions"* ]] || { ok=0; echo "no mention of herdr-sessions: ${out:0:300}" >&2; }
    [[ ! -d "$sb/home/.config/systemd/user" ]] || { ok=0; echo "claude-autostart wrote units despite the conflict (selected)" >&2; }

    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/herdr-sessions-restore.service"
    out2="$(autostart_run_full "$sb" 0 "")"
    state2="$(autostart_state "$out2")"; rc2="$(autostart_rc "$out2")"
    (( rc2 == 0 )) || { ok=0; echo "rc=$rc2 want 0 with herdr-sessions already installed: ${out2:0:300}" >&2; }
    [[ "$state2" == skipped ]] || { ok=0; echo "state=[$state2] want skipped (installed case)" >&2; }
    [[ "$out2" == *"herdr-sessions"* && "$out2" == *"skipped"* ]] || { ok=0; echo "no skip naming herdr-sessions (installed case): ${out2:0:300}" >&2; }
    [[ -z "$(find "$sb/home/.config/systemd/user" -maxdepth 1 -name 'claude-sessions-*')" ]] \
        || { ok=0; echo "claude-autostart wrote units despite the skip (installed)" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "claude-autostart installed alongside herdr-sessions"; fi
fi

if it "herdr-sessions detect: true only when the restore service unit exists (user scope)"; then
    sb="$(mktemp -d)"; ok=1
    ( SYS_HOME="$sb/home"; AUTOOS_ETC_SYSTEMD_SYSTEM_DIR="$sb/no-etc"
      ! script_is_installed herdr-sessions ) || { ok=0; echo "reported installed with no unit file present" >&2; }
    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/herdr-sessions-restore.service"
    ( SYS_HOME="$sb/home"; AUTOOS_ETC_SYSTEMD_SYSTEM_DIR="$sb/no-etc"
      script_is_installed herdr-sessions ) || { ok=0; echo "reported NOT installed although the user-scope unit file exists" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "herdr-sessions detection does not match the user-scope unit file's presence"; fi
fi

# Finding 1 (qoder review, L1-backlog.review-herdr-qoder.md): a SYSTEM-scope
# install (HS_SCOPE=system, units in /etc/systemd/system - see
# configuration/herdr-sessions/install.sh) was invisible here, so a later
# claude-autostart install passed the install_claude_autostart mutual-exclusion
# gate (lib/linux/install.sh: the other selected in this plan is refused, the
# other already installed is skipped, rc 0) and ran beside a live herdr
# restore. /etc/systemd/system is injectable
# via AUTOOS_ETC_SYSTEMD_SYSTEM_DIR, the same seam pattern as SYS_HOME, so
# this never needs a real /etc write to test.
if it "herdr-sessions detect: also true for a system-scope unit, independent of the user-scope path"; then
    sb="$(mktemp -d)"; ok=1
    ( SYS_HOME="$sb/home"; AUTOOS_ETC_SYSTEMD_SYSTEM_DIR="$sb/etc"
      ! script_is_installed herdr-sessions ) || { ok=0; echo "reported installed with neither path present" >&2; }
    mkdir -p "$sb/etc"
    touch "$sb/etc/herdr-sessions-restore.service"
    ( SYS_HOME="$sb/home"; AUTOOS_ETC_SYSTEMD_SYSTEM_DIR="$sb/etc"
      script_is_installed herdr-sessions ) || { ok=0; echo "reported NOT installed although the system-scope unit file exists" >&2; }
    [[ ! -d "$sb/home" ]] || { ok=0; echo "the user-scope dir was touched by a system-scope check" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "herdr-sessions detection does not see a system-scope install"; fi
fi

if it "herdr-sessions: claude-autostart's mutual-exclusion gate sees a system-scope herdr-sessions install too"; then
    sb="$(mktemp -d)"; ok=1
    mkdir -p "$sb/home/.config/systemd/user" "$sb/etc"
    touch "$sb/etc/herdr-sessions-restore.service"
    out="$(autostart_run_full "$sb" 0 "" "$sb/etc")"
    state="$(autostart_state "$out")"; rc="$(autostart_rc "$out")"
    (( rc == 0 )) || { ok=0; echo "rc=$rc want 0 with herdr-sessions installed system-scope only: ${out:0:300}" >&2; }
    [[ "$state" == skipped ]] || { ok=0; echo "state=[$state] want skipped (system-scope case)" >&2; }
    [[ "$out" == *"herdr-sessions"* && "$out" == *"skipped"* ]] || { ok=0; echo "no skip naming herdr-sessions (system-scope case): ${out:0:300}" >&2; }
    [[ -z "$(find "$sb/home/.config/systemd/user" -maxdepth 1 -name 'claude-sessions-*')" ]] \
        || { ok=0; echo "claude-autostart wrote units despite the system-scope skip" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "claude-autostart installed alongside a system-scope-only herdr-sessions"; fi
fi

if it "herdr-sessions: claude-autostart skips when herdr-sessions is already installed (not selected)"; then
    sb="$(mktemp -d)"; ok=1
    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/herdr-sessions-restore.service"
    out="$(autostart_run_full "$sb" 0 "")"
    state="$(autostart_state "$out")"; rc="$(autostart_rc "$out")"
    (( rc == 0 )) || { ok=0; echo "rc=$rc want 0: ${out:0:300}" >&2; }
    [[ "$state" == skipped ]] || { ok=0; echo "state=[$state] want skipped" >&2; }
    [[ "$out" == *"herdr-sessions"* ]] || { ok=0; echo "no mention of herdr-sessions: ${out:0:300}" >&2; }
    [[ -z "$(find "$sb/home/.config/systemd/user" -maxdepth 1 -name 'claude-sessions-*')" ]] \
        || { ok=0; echo "claude-autostart wrote units despite the skip" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "claude-autostart did not skip when herdr-sessions is already installed"; fi
fi

if it "herdr-sessions: claude-autostart dry run skips when herdr-sessions is already installed"; then
    sb="$(mktemp -d)"; ok=1
    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/herdr-sessions-restore.service"
    out="$(autostart_run_full "$sb" 1 "")"
    state="$(autostart_state "$out")"; rc="$(autostart_rc "$out")"
    (( rc == 0 )) || { ok=0; echo "dry-run rc=$rc want 0: ${out:0:300}" >&2; }
    [[ "$state" == skipped ]] || { ok=0; echo "dry-run state=[$state] want skipped" >&2; }
    [[ "$out" == *"herdr-sessions"* ]] || { ok=0; echo "dry-run names nothing: ${out:0:300}" >&2; }
    [[ -z "$(find "$sb/home/.config/systemd/user" -maxdepth 1 -name 'claude-sessions-*')" ]] \
        || { ok=0; echo "dry run wrote units despite the skip" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "claude-autostart dry run did not skip when herdr-sessions is already installed"; fi
fi

if it "herdr-sessions: herdr-sessions skips when claude-autostart is already installed (not selected)"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/claude-sessions-restore.service"
    out="$(herdr_run "$sb" "$drv" "$profile" 0 "")"
    state="$(herdr_state "$out")"; rc="$(herdr_rc "$out")"
    (( rc == 0 )) || { ok=0; echo "rc=$rc want 0: ${out:0:300}" >&2; }
    [[ "$state" == skipped ]] || { ok=0; echo "state=[$state] want skipped" >&2; }
    [[ "$out" == *"claude-autostart"* ]] || { ok=0; echo "no mention of claude-autostart: ${out:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "the driver ran despite the skip" >&2; }
    outdry="$(herdr_run "$sb" "$drv" "$profile" 1 "")"
    rcdry="$(herdr_rc "$outdry")"; statedry="$(herdr_state "$outdry")"
    (( rcdry == 0 )) || { ok=0; echo "dry-run rc=$rcdry want 0: ${outdry:0:300}" >&2; }
    [[ "$statedry" == skipped ]] || { ok=0; echo "dry-run state=[$statedry] want skipped" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "herdr-sessions did not skip when claude-autostart is already installed"; fi
fi

if it "herdr-sessions: with both units already present, both installers report skipped, rc 0"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    mkdir -p "$sb/home/.config/systemd/user"
    printf 'seeded claude restore\n' >"$sb/home/.config/systemd/user/claude-sessions-restore.service"
    printf 'seeded herdr restore\n' >"$sb/home/.config/systemd/user/herdr-sessions-restore.service"
    out_h="$(herdr_run "$sb" "$drv" "$profile" 0 "")"
    state_h="$(herdr_state "$out_h")"; rc_h="$(herdr_rc "$out_h")"
    (( rc_h == 0 )) || { ok=0; echo "herdr rc=$rc_h want 0: ${out_h:0:300}" >&2; }
    [[ "$state_h" == skipped ]] || { ok=0; echo "herdr state=[$state_h] want skipped" >&2; }
    [[ "$out_h" == *"claude-autostart"* && "$out_h" == *"skipped"* ]] || { ok=0; echo "herdr skip names nothing: ${out_h:0:300}" >&2; }
    [[ ! -e "$drv/installed-marker" ]] || { ok=0; echo "the driver ran despite the skip" >&2; }
    out_a="$(autostart_run_full "$sb" 0 "")"
    state_a="$(autostart_state "$out_a")"; rc_a="$(autostart_rc "$out_a")"
    (( rc_a == 0 )) || { ok=0; echo "autostart rc=$rc_a want 0: ${out_a:0:300}" >&2; }
    [[ "$state_a" == skipped ]] || { ok=0; echo "autostart state=[$state_a] want skipped" >&2; }
    [[ "$out_a" == *"herdr-sessions"* && "$out_a" == *"skipped"* ]] || { ok=0; echo "autostart skip names nothing: ${out_a:0:300}" >&2; }
    [[ "$(cat "$sb/home/.config/systemd/user/claude-sessions-restore.service")" == "seeded claude restore" ]] || { ok=0; echo "claude-autostart rewrote the seeded unit" >&2; }
    [[ -z "$(find "$sb/home/.config/systemd/user" -maxdepth 1 -name '*.autoos-backup-*')" ]] || { ok=0; echo "autostart took a backup during a skip" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "both installers did not skip with both units already present"; fi
fi

if it "herdr-sessions: both selected in one plan is still refused"; then
    sb="$(mktemp -d)"; drv="$sb/driver"; ok=1
    herdr_stub_driver "$drv" installed
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    out="$(herdr_run "$sb" "$drv" "$profile" 0 "claude-autostart herdr-sessions")"
    rc="$(herdr_rc "$out")"
    (( rc != 0 )) || { ok=0; echo "herdr rc=0 with both selected: ${out:0:300}" >&2; }
    [[ "$out" == *"claude-autostart"* ]] || { ok=0; echo "herdr names nothing: ${out:0:300}" >&2; }
    out2="$(autostart_run_full "$sb" 0 "claude-autostart herdr-sessions")"
    rc2="$(autostart_rc "$out2")"
    (( rc2 != 0 )) || { ok=0; echo "autostart rc=0 with both selected: ${out2:0:300}" >&2; }
    [[ "$out2" == *"herdr-sessions"* ]] || { ok=0; echo "autostart names nothing: ${out2:0:300}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "both-selected plan was not refused"; fi
fi

if it "dry run with herdr-sessions installed still exits 0 (workstation selects claude-autostart)"; then
    sb="$(mktemp -d)"; ok=1
    mkdir -p "$sb/home/.config/systemd/user"
    touch "$sb/home/.config/systemd/user/herdr-sessions-restore.service"
    out="$(SUDO_USER="autoos-no-such-user-excl-skip" HOME="$sb/home" bash setup.sh --profile workstation --dry-run --yes --no-color 2>&1)"; rc=$?
    (( rc == 0 )) || { ok=0; echo "rc=$rc want 0: $(printf '%s' "$out" | tail -n 5)" >&2; }
    executed="$(printf '%s' "$out" | grep -c '^run:' || true)"
    [[ "$executed" == 0 ]] || { ok=0; echo "executed=$executed want 0" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "workstation dry run fails when herdr-sessions is already installed"; fi
fi

if it "herdr-sessions: a driver run that replaced some units is installed, not skipped"; then
    sb="$(mktemp -d)"; drv="$sb/driver"
    herdr_stub_driver "$drv" partial
    profile="$sb/site.conf"; printf '# site profile\n' >"$profile"
    out="$(herdr_run "$sb" "$drv" "$profile")"
    state="$(herdr_state "$out")"
    rm -rf "$sb"
    if [[ "$state" != skipped ]]; then pass; else fail "one unit changed but the component reports skipped: ${out:0:300}"; fi
fi

