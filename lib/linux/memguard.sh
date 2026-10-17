#!/usr/bin/env bash
# memguard.sh — stop the newest ACTIVE agent-run scope only below 1 GiB MemAvailable.
#
# MEMGUARD (operator D-438): on a memory-pressured workstation the runaway
# thing is most often one AutoOS worker run, not the session around it. This
# guard fires from a systemd user timer and, only when MemAvailable is under
# the threshold on TWO consecutive samples (two-sample hysteresis: the
# previous sample must have been low too, at most 30 s ago), stops the NEWEST
# active `autoos-worker-*.scope` — at most one unit per invocation, chosen by
# ActiveEnterTimestampMonotonic so re-stopping the same victim twice in a run
# cannot happen.
#
# It stops (via `systemctl --user stop`, so systemd SIGKILLs the scope's cgroup):
#   * autoos-worker-*.scope  — agent RUNS only. One scope per run; each run's
#     runner + its client + MCP child processes live inside it (see
#     tools/autoos-agent.py worker_scope_launch).
#
# It never touches, with no exceptions:
#   * the login session (`session-*.scope`), sshd, tailscaled, the OpenCode
#     gateway or the MCP gateway — outside our cgroup namespace entirely;
#   * anything not matching ^autoos-worker-[A-Za-z0-9_.-]+\.scope$ — names are
#     re-validated character-by-character here even though the list came from
#     systemd, so a crafted or corrupted unit name can never be stopped;
#   * anything by PID — there is no kill(1) call in this file at all.
#
# Two facts about `systemctl` are load-bearing (D-493 hard conditions):
#
#   * discovery asks for `list-units --state=active`, so ONLY running scopes
#     are candidates. An inactive or failed scope keeps its
#     ActiveEnterTimestampMonotonic, so a dead scope used to win "newest",
#     the real memory hog survived, and the stop/log repeated every tick.
#   * any `systemctl` failure is a LOG line and nothing else — never an
#     abort. If `show` fails or returns a non-numeric timestamp for ANY listed
#     unit, or if there is a tie for newest timestamp, the tick is ambiguous
#     and stops nothing; a failed `list-units` writes one rate-limited line
#     and stops nothing; a failed `stop` writes one rate-limited line. This runs
#     every 10 s from a timer: a non-zero exit is a crash loop, not a report.
#
# The stop log is bounded: when it exceeds 256 KiB it is moved to
# `$STOP_LOG.1` (one generation) before the next line is appended, and every
# line is written to BOTH the log file and stdout, so the unit's
# `StandardOutput=journal` claim is true.
#
# Design notes (why not earlyoom): earlyoom needs root, decides for whole
# processes rather than runs, and would happily kill sshd. This guard runs as
# the user, is scope-precise, and is a no-op until the pressure is real.
#
# Seams for testing (never set on a real machine):
#   AUTOOS_MEMGUARD_MEMINFO  — read MemAvailable from this file instead of
#                              /proc/meminfo (tests inject a synthetic one).
#   AUTOOS_MEMGUARD_UNITS    — newline-separated scope names to consider
#                              instead of asking `systemctl --user list-units`
#                              (tests inject synthetic unit lists).
#   AUTOOS_MEMGUARD_STOP_LOG — where `stop`/`no agent run` lines are written
#                              (tests inject a scratch file; defaults to LOG).
#   AUTOOS_MEMGUARD_STATE    — ONE line holding both halves of the guard's
#                              memory: "<verdict> <sample epoch> <told epoch>"
#                              (verdict = low|high, the two-sample verdict;
#                              told = the quiet-window marker). A bare
#                              epoch-only line from an older copy is read as
#                              the quiet-window marker alone.
#   AUTOOS_MEMGUARD_DISABLE  — set to a non-empty value to turn the guard off.
#
# Idempotent by construction: above threshold it exits 0 without side effects;
# with nothing to stop it logs at most one line per 10 minutes.

set -euo pipefail

THRESHOLD_KIB="${AUTOOS_MEMGUARD_MIN_KIB:-1048576}"
MEMINFO="${AUTOOS_MEMGUARD_MEMINFO:-/proc/meminfo}"
LOG="${AUTOOS_MEMGUARD_LOG:-${HOME}/.local/state/autoos/memguard.log}"
STOP_LOG="${AUTOOS_MEMGUARD_STOP_LOG:-$LOG}"
STATE="${AUTOOS_MEMGUARD_STATE:-${LOG%.log}.state}"
QUIET_WINDOW_SECS=600
# Two-sample hysteresis: how stale the previous "low" verdict may be and
# still count as "also low". Older than this and the next sample starts a new
# pair, so one moment of pressure printed as two lines can never stop a run.
LOW_SAMPLE_WINDOW_SECS=30
# One generational cap on the stop log (256 KiB).
STOP_LOG_CAP_BYTES=262144

# The only unit shapes this script will ever stop. Two gates on purpose: the
# prefix+suffix shape (the contract) and a character whitelist, so shell metacharacters,
# spaces, path separators or a name that is merely *contained* by a pattern are
# rejected before anything reaches systemctl.
WORKER_SCOPE_RE='^autoos-worker-[A-Za-z0-9_.-]+\.scope$'

utc_now() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log() {
  local line="$1" size=0
  mkdir -p "$(dirname "$STOP_LOG")" 2>/dev/null || true
  if [[ -f "$STOP_LOG" ]]; then
    size="$(wc -c <"$STOP_LOG" 2>/dev/null || printf 0)"
    size="${size//[[:space:]]/}"
    [[ "$size" =~ ^[0-9]+$ ]] || size=0
    # Soft cap: the file rotates after exceeding STOP_LOG_CAP_BYTES, not at the exact byte boundary.
    if (( size > STOP_LOG_CAP_BYTES )); then
      # One generation only: keep the overflow as .1 and start a fresh file.
      mv -f "$STOP_LOG" "${STOP_LOG}.1" 2>/dev/null || true
    fi
  fi
  { printf '%s\n' "$line" >>"$STOP_LOG"; } 2>/dev/null || true
  # Same line to stdout: StandardOutput=journal in memguard.service promises
  # the journal this line, and a write only into a file would break that.
  printf '%s\n' "$line"
  return 0
}

is_worker_scope() {
  case "$1" in
  autoos-worker-*.scope) ;;
  *) return 1 ;;
  esac
  [[ "$1" =~ $WORKER_SCOPE_RE ]]
}

mem_available_kib() {
  awk '/^MemAvailable:/ {print $2; found=1} END {if (!found) exit 1}' "$MEMINFO"
}

# ─── state: the two-sample verdict and the quiet window, one line ───────────
#
# "<verdict> <sample epoch> <told epoch>". The verdict is only ever written
# from inside this file: a missing, garbled or legacy bare-epoch line parses
# as verdict "none", and "none" is NOT low, so hysteresis never fires on
# state the guard cannot vouch for (a stale line is likewise not low — see
# main).
STATE_VERDICT="none"
STATE_SAMPLE_TS=0
STATE_TOLD_TS=0

state_read() {
  STATE_VERDICT="none"
  STATE_SAMPLE_TS=0
  STATE_TOLD_TS=0
  [[ -f "$STATE" ]] || return 0
  local raw=""
  IFS= read -r raw <"$STATE" || true
  if [[ "$raw" =~ ^(low|high|none)[[:space:]]+([0-9]+)[[:space:]]+([0-9]+)$ ]]; then
    STATE_VERDICT="${BASH_REMATCH[1]}"
    STATE_SAMPLE_TS="${BASH_REMATCH[2]}"
    STATE_TOLD_TS="${BASH_REMATCH[3]}"
  elif [[ "$raw" =~ ^[0-9]+$ ]]; then
    # An older copy wrote only the quiet-window marker. Keep that meaning.
    STATE_TOLD_TS="$raw"
  fi
}

# Atomic: tmp file in the same directory, then mv. A tick that starts while a
# previous one is still writing can never read half a line. Never aborts under
# set -e; an unwritable directory must not crash-loop the timer. Returns 0 on
# success, 1 on failure.
state_write() {
  local tmp dir
  dir="$(dirname "$STATE")"
  mkdir -p "$dir" 2>/dev/null || true
  tmp="${STATE}.tmp.$$"
  if { printf '%s %s %s\n' "$STATE_VERDICT" "$STATE_SAMPLE_TS" "$STATE_TOLD_TS" >"$tmp"; } 2>/dev/null; then
    if mv -f "$tmp" "$STATE" 2>/dev/null; then
      return 0
    fi
    rm -f "$tmp" 2>/dev/null || true
  else
    rm -f "$tmp" 2>/dev/null || true
  fi
  return 1
}

# told_recently <now-epoch>: the quiet window the "no agent run" line obeys.
told_recently() {
  local now="$1"
  [[ "$STATE_TOLD_TS" =~ ^[0-9]+$ ]] || return 1
  (( STATE_TOLD_TS > 0 )) || return 1
  (( now - STATE_TOLD_TS < QUIET_WINDOW_SECS )) && (( now >= STATE_TOLD_TS ))
}

# Newest FIRST: order by the unit's own ActiveEnterTimestampMonotonic, the
# kernel boot tick at which the scope went active — so ordering is stable even
# across concurrent runs. If `show` fails or returns a non-numeric timestamp
# for ANY listed unit, that tick is ambiguous: stop nothing, return 2.
# A tie for the newest numeric timestamp is also ambiguous: stop nothing, return 3.
# Returns 1 ONLY when `list-units` itself failed: the caller must then log
# and stop nothing, because an empty list is indistinguishable from "no run"
# and a claim we cannot make.
newest_first() {
  local names name raw_show show_rc ts
  local sorted max_ts tie_count unknown_count=0
  local -a valid_names=() pairs=()
  # --state=active, never --all: an inactive/failed scope still reports its
  # ActiveEnterTimestampMonotonic, so a dead scope would be chosen as newest
  # while the real memory hog lives on and every tick repeats the same
  # no-op stop. Only RUNNING scopes are candidates.
  if ! names="$(systemctl --user list-units --state=active --no-legend --plain \
      'autoos-worker-*.scope' 2>/dev/null | awk '{print $1}')"; then
    return 1
  fi

  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    if is_worker_scope "$name"; then
      valid_names+=("$name")
    fi
  done <<<"$names"

  (( ${#valid_names[@]} > 0 )) || return 0

  for name in "${valid_names[@]}"; do
    show_rc=0
    raw_show="$(systemctl --user show "$name" -p ActiveEnterTimestampMonotonic 2>/dev/null)" || show_rc=$?
    ts=""
    if (( show_rc == 0 )); then
      ts="$(printf '%s\n' "$raw_show" | sed -n 's/^ActiveEnterTimestampMonotonic=//p')"
    fi
    if [[ "$ts" =~ ^[0-9]+$ ]]; then
      pairs+=("${ts}"$'\t'"${name}")
    else
      (( unknown_count++ )) || true
    fi
  done

  if (( unknown_count > 0 )); then
    # Ambiguous: at least one listed unit has an unknown or non-numeric timestamp.
    # Print nothing for stopping purposes, output count only, return 2.
    printf '%s\n' "$unknown_count"
    return 2
  fi

  sorted="$(printf '%s\n' "${pairs[@]}" | sort -k1,1rn)"
  max_ts="$(printf '%s\n' "$sorted" | awk -F'\t' 'NR==1 {print $1}')"
  tie_count="$(printf '%s\n' "$sorted" | awk -F'\t' -v m="$max_ts" '$1 == m {count++} END {print count+0}')"

  if (( tie_count > 1 )); then
    # Ambiguous: tie for newest numeric timestamp between two or more units.
    # Print nothing for stopping purposes, return 3.
    return 3
  fi

  printf '%s\n' "$sorted" | cut -f2
}

candidate_units() {
  # Tests inject the unit list directly; on a real machine it comes from
  # systemd, newest (highest ActiveEnterTimestampMonotonic) emitted first.
  # Status propagates: newest_first's 1 (list-units failed) reaches main.
  if [[ -n "${AUTOOS_MEMGUARD_UNITS:-}" ]]; then
    printf '%s\n' "$AUTOOS_MEMGUARD_UNITS"
  else
    newest_first
  fi
}

main() {
  if [[ -n "${AUTOOS_MEMGUARD_DISABLE:-}" ]]; then
    return 0
  fi

  local avail_kib
  if ! avail_kib="$(mem_available_kib)"; then
    return 0
  fi

  state_read
  local now
  now="$(date +%s)"

  if (( avail_kib >= THRESHOLD_KIB )); then
    # A sample at/above the threshold clears the low verdict: the pair is
    # broken, so the next low sample has to prove itself all over again.
    # Nothing to clear = no side effect at all (no file is ever CREATED here).
    if [[ "$STATE_VERDICT" == "low" ]]; then
      STATE_VERDICT="high"
      STATE_SAMPLE_TS="$now"
      state_write || true
    fi
    return 0
  fi

  # Below the floor: was the PREVIOUS sample low too? Unknown, garbled or
  # stale state counts as "not low".
  local prev_low=0
  if [[ "$STATE_VERDICT" == "low" ]] && [[ "$STATE_SAMPLE_TS" =~ ^[0-9]+$ ]] &&
    (( now >= STATE_SAMPLE_TS )) && (( now - STATE_SAMPLE_TS <= LOW_SAMPLE_WINDOW_SECS )); then
    prev_low=1
  fi

  # This sample IS low: remember it before anything else can fail, so the
  # next tick sees a pair member even if discovery goes wrong.
  STATE_VERDICT="low"
  STATE_SAMPLE_TS="$now"

  local units rc=0
  units="$(candidate_units)" || rc=$?
  if (( rc != 0 )); then
    if (( rc == 1 )); then
      # list-units failed (no user manager, bus error). Log only, stop nothing,
      # never abort: this runs every 10 s from a timer, so a non-zero exit is a
      # crash loop, not a report.
      if ! told_recently "$now"; then
        log "$(utc_now) MemAvailable=${avail_kib} list-units failed, nothing stopped"
        STATE_TOLD_TS="$now"
      fi
    elif (( rc == 2 )); then
      local unk="${units//[[:space:]]/}"
      [[ "$unk" =~ ^[0-9]+$ ]] || unk=1
      if ! told_recently "$now"; then
        log "$(utc_now) MemAvailable=${avail_kib} timestamp unknown for ${unk} unit(s), nothing stopped"
        STATE_TOLD_TS="$now"
      fi
    elif (( rc == 3 )); then
      if ! told_recently "$now"; then
        log "$(utc_now) MemAvailable=${avail_kib} newest scope ambiguous"
        STATE_TOLD_TS="$now"
      fi
    fi
    state_write || true
    return 0
  fi

  local unit chosen=""
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    # Reject anything but a plain worker-scope name before it can be named
    # again below. This is the last gate between a unit string and a stop.
    if is_worker_scope "$unit"; then
      chosen="$unit"
      break
    fi
  done <<<"$units"

  if [[ -z "$chosen" ]]; then
    if ! told_recently "$now"; then
      log "$(utc_now) MemAvailable=${avail_kib} below threshold, no agent run to stop"
      STATE_TOLD_TS="$now"
    fi
    state_write || true
    return 0
  fi

  if (( ! prev_low )); then
    # First low sample: a candidate exists but the pressure is not confirmed
    # yet. Record, stop NOTHING, log NOTHING — the verdict alone would be a
    # promise we have not kept.
    state_write || true
    return 0
  fi

  # Consume the pair BEFORE the stop is issued: even if systemctl crashes,
  # times out, or the log write fails, the verdict cannot remain "low".
  # If state is not writable, fail closed: do not stop.
  STATE_VERDICT="none"
  STATE_SAMPLE_TS="$now"
  if ! state_write; then
    # The log() itself never aborts; this line cannot be rate-limited through
    # the state file, so rate-limit nothing and rely on the soft log cap.
    log "$(utc_now) MemAvailable=${avail_kib} state not writable, nothing stopped"
    return 0
  fi

  # Two consecutive low samples and one unambiguous target. On a failed stop
  # nothing happened and nothing is claimed: the log line is conditional.
  if systemctl --user stop "$chosen"; then
    log "$(utc_now) MemAvailable=${avail_kib} stopped ${chosen}"
  else
    if ! told_recently "$now"; then
      log "$(utc_now) MemAvailable=${avail_kib} stop of ${chosen} failed, nothing claimed"
      STATE_TOLD_TS="$now"
    fi
  fi
  state_write || true
}

main "$@"
