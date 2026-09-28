#!/usr/bin/env bash
# Chunked cross-family review through paid DeepSeek on the OmniRoute gateway, under the
# monthly cap (via deepseek_call.py). The diff is split into <=900-line parts, each reviewed with the
# same standing instruction, outputs concatenated. The split dates from opencode (measured
# 2026-09-17, OpenCode 1.18.30: its --file attachment silently truncated to ~1,000 lines); the
# direct API call has no such limit, but a part that size still gets a closer read than one
# 5,000-line diff, and the per-part output format is kept. Keys are read inside deepseek_call.py
# from AutoOS configuration/api-keys.yml and never printed or put on a command line.
#
#   deepseek_chunked_review.sh <label> <base-sha> <merge-sha> <done-means-file> <out-md> [repo] [paths...]
#
# repo        defaults to `git rev-parse --show-toplevel` of the caller's cwd.
# paths...    limits the diff to these pathspecs; omitted means the whole diff.
# Env:
#   DSR_WORKDIR       scratch dir base (default: ${TMPDIR:-/tmp}); work dir is $DSR_WORKDIR/dsr_<label>
#   AUTOOS_API_KEYS   path to AutoOS api-keys.yml (a missing file is an error); else $AUTOOS_ROOT,
#                     else the AutoOS checkout is searched — see deepseek_call.py. Single keys:
#                     AUTOOS_OMNIROUTE_KEY. Same lookup as deepseek_review.sh
#   DSR_BRANCH_DESC   how the merge is described in the prompt (default: "branch $label into its target")
#   DSR_REPO_DESC     one-line description of the reviewed repository (default: "this repository")
#   PYTHON            interpreter for deepseek_call.py (default: first working python3/python/py)
# Per-part failures and the "served: <model> via <leg>" lines go to $work/err.log.
set -euo pipefail
label="${1:?label}"; base="${2:?base}"; merge="${3:?merge}"; donefile="${4:?done-means file}"; out="${5:?out}"
shift 5
repo="${1:-$(git rev-parse --show-toplevel)}"
[ $# -gt 0 ] && shift
paths=("$@")

work="${DSR_WORKDIR:-${TMPDIR:-/tmp}}/dsr_${label}"
mkdir -p "$work"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
py="${PYTHON:-}"
if [ -z "$py" ]; then
  for c in python3 python py; do "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1 && { py=$c; break; }; done
fi
[ -n "$py" ] || { echo "no python >= 3.9 found (set PYTHON)" >&2; exit 2; }
caller="$(native "$script_dir/deepseek_call.py")"
branch_desc="${DSR_BRANCH_DESC:-branch $label into its target}"
repo_desc="${DSR_REPO_DESC:-this repository}"

if [ "${#paths[@]}" -eq 0 ]; then
  git -C "$repo" diff "$base" "$merge" > "$work/full.diff"
else
  git -C "$repo" diff "$base" "$merge" -- "${paths[@]}" > "$work/full.diff"
fi
total=$(wc -l < "$work/full.diff")
rm -f "$work"/part_*
split -l 900 -d -a 2 "$work/full.diff" "$work/part_"
parts=("$work"/part_*)
n=${#parts[@]}
: > "$out"
echo "# DeepSeek review of merge $merge ($label) — $n parts of a ${total}-line diff, $(date -u +%Y-%m-%dT%H:%MZ)" >> "$out"
i=0
for p in "${parts[@]}"; do
  i=$((i+1))
  prompt="$work/prompt_$i.txt"
  {
    echo "DO NOT USE ANY TOOLS. You are a read-only code reviewer. Answer as plain text only. The whole task is in this file; do not try to read any other file."
    echo
    echo "Task: review PART $i of $n of the merge diff of $branch_desc (commit $merge) in $repo_desc. Other parts are reviewed separately; do not comment on missing context, review what is here. The session's 'Done means' was:"
    cat "$donefile"
    echo
    echo "Report, concretely and briefly, no praise: (1) defects with file and hunk line and severity; (2) tests in this part that cannot fail (mock the thing they claim to test, assert only a call was made, fixture too small to reach the branch); (3) behaviour changed beyond the listed fixes."
    echo
    echo "=== diff part $i/$n ==="
    cat "$p"
  } > "$prompt"
  echo "" >> "$out"; echo "## Part $i/$n" >> "$out"
  echo "part $i/$n:" >> "$work/err.log"
  "$py" "$caller" "$(native "$prompt")" --timeout 900 2>>"$work/err.log" >> "$out" \
    || echo "(part $i failed; see err.log)" >> "$out"
done
echo "done: $out ($(wc -c < "$out") bytes)"
