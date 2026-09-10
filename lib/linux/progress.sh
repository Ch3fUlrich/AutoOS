#!/usr/bin/env bash
# Progress is presentation state; only the execution loop counts completions.
# shellcheck shell=bash
# shellcheck disable=SC2034
AUTOOS_CURRENT_PROGRESS='{}'
export AUTOOS_CURRENT_PROGRESS
AUTOOS_PROGRESS_STARTED=0

progress_start() {
    AUTOOS_PROGRESS_STARTED=$SECONDS
    AUTOOS_CURRENT_PROGRESS="$(python3 - "$1" "$2" "$3" "$4" <<'PY'
import json,sys
print(json.dumps(dict(id=sys.argv[1], name=sys.argv[2], done=int(sys.argv[3]), total=int(sys.argv[4]),
                     phase='checking', percent=None, elapsedSeconds=0, status='running')))
PY
)"
    progress_update checking
}

progress_update() {
    local phase="$1" complete="${2:-0}" output
    AUTOOS_CURRENT_PROGRESS="$(python3 - "$AUTOOS_CURRENT_PROGRESS" "$phase" "$complete" "$((SECONDS-AUTOOS_PROGRESS_STARTED))" <<'PY'
import json,sys
p=json.loads(sys.argv[1]); complete=sys.argv[3]=='1'
p.update(phase=sys.argv[2], elapsedSeconds=int(sys.argv[4]), percent=None)
if complete: p.update(done=p['done']+1, status='complete', percent=100 if sys.argv[2] in ('installed','skipped') else None)
print(json.dumps(p))
PY
)"
    if [[ "${AUTOOS_PROGRESS_EVENTS:-0}" == 1 ]]; then
        ui_line "@@AUTOOS_PROGRESS $AUTOOS_CURRENT_PROGRESS"
    else
        output="$(python3 - "$AUTOOS_CURRENT_PROGRESS" <<'PY'
import json,sys
p=json.loads(sys.argv[1]); pct=int(100*p['done']/max(1,p['total'])); n=pct//5
print(f"Overall [{'#'*n}{'-'*(20-n)}] {p['done']}/{p['total']} ({pct}%)")
print(f"Current {p['name']}: {p['phase']} ({p['elapsedSeconds']}s)")
PY
)"
        ui_line "$output"
    fi
}
