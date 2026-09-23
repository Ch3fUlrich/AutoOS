#!/usr/bin/env bash
# Chunked cross-family review through DeepSeek (opencode in WSL). Works around two limits
# measured 2026-09-17 (OpenCode 1.18.30): opencode's --file attachment silently truncates to
# ~1,000 lines (the model reviews the start of a bigger file and says nothing about the rest,
# with no error), and opencode auto-rejects a prompt read from outside /tmp/cross-review as an
# "external directory" — so the prompt is copied there before every call. The diff is split into
# <=900-line parts, each reviewed with the same standing instruction, outputs concatenated. Runs
# from Git Bash; the key is read inside WSL from the untracked secrets file and never printed.
#
#   deepseek_chunked_review.sh <label> <base-sha> <merge-sha> <done-means-file> <out-md> [repo] [paths...]
#
# repo        defaults to `git rev-parse --show-toplevel` of the caller's cwd.
# paths...    limits the diff to these pathspecs; omitted means the whole diff.
# Env:
#   DSR_WORKDIR       scratch dir base (default: ${TMPDIR:-/tmp}); work dir is $DSR_WORKDIR/dsr_<label>
#   DEEPSEEK_SECRETS  path to the secrets file with a `deepseek=<key>` line. Lookup:
#                     DEEPSEEK_SECRETS or AUTOOS_SECRETS when set (a missing file is an error);
#                     otherwise ~/.config/autoos/api_keys.conf, then (legacy, with a notice) this
#                     script's repo's secrets/api_keys.conf — the same order deepseek_review.sh uses
#   DSR_BRANCH_DESC   how the merge is described in the prompt (default: "branch $label into its target")
#   DSR_REPO_DESC     one-line description of the reviewed repository (default: "this repository")
set -euo pipefail
label="${1:?label}"; base="${2:?base}"; merge="${3:?merge}"; donefile="${4:?done-means file}"; out="${5:?out}"
shift 5
repo="${1:-$(git rev-parse --show-toplevel)}"
[ $# -gt 0 ] && shift
paths=("$@")

work="${DSR_WORKDIR:-${TMPDIR:-/tmp}}/dsr_${label}"
mkdir -p "$work"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# secrets/api_keys.conf is UNTRACKED, so it exists only in the MAIN checkout - never in a
# worktree, which is exactly where the runner puts every session. Resolve the main checkout
# through --git-common-dir (a worktree's own --git-dir points at .git/worktrees/<name>).
# That legacy file is now the LAST fallback, read with a notice (D4).
if [ -n "${DEEPSEEK_SECRETS:-}${AUTOOS_SECRETS:-}" ]; then
  secrets="${DEEPSEEK_SECRETS:-$AUTOOS_SECRETS}"   # explicit: a wrong path fails loudly
else
  secrets="$HOME/.config/autoos/api_keys.conf"
fi
if [ ! -s "$secrets" ] && [ -z "${DEEPSEEK_SECRETS:-}${AUTOOS_SECRETS:-}" ]; then
  secrets="$(cd "$script_dir/../.." && pwd)/secrets/api_keys.conf"
  if [ ! -s "$secrets" ]; then
    common="$(git -C "$script_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    [ -n "$common" ] && secrets="$(cd "$common/.." && pwd)/secrets/api_keys.conf"
  fi
  [ -s "$secrets" ] && echo "notice: using legacy secrets file $secrets; move it to ~/.config/autoos/api_keys.conf (DEEPSEEK_SECRETS or AUTOOS_SECRETS override it)" >&2
fi
[ -s "$secrets" ] || { echo "secrets file not found: $secrets (set DEEPSEEK_SECRETS or AUTOOS_SECRETS, or create ~/.config/autoos/api_keys.conf)" >&2; exit 2; }
branch_desc="${DSR_BRANCH_DESC:-branch $label into its target}"
repo_desc="${DSR_REPO_DESC:-this repository}"

if [ "${#paths[@]}" -eq 0 ]; then
  git -C "$repo" diff "$base" "$merge" > "$work/full.diff"
else
  git -C "$repo" diff "$base" "$merge" -- "${paths[@]}" > "$work/full.diff"
fi
total=$(wc -l < "$work/full.diff")
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
  # cygpath/wslpath round-trip (not a /c/... string hack): $work may live outside C:\ entirely
  # once DSR_WORKDIR is configurable, so a hardcoded drive-letter substitution is not safe here.
  win_prompt=$(cygpath -w "$prompt")
  win_secrets=$(cygpath -w "$secrets")
  echo "" >> "$out"; echo "## Part $i/$n" >> "$out"
  wsl -e bash -lc "p=\$(wslpath -u '$win_prompt'); s=\$(wslpath -u '$win_secrets'); [ -s \"\$p\" ] || { echo \"prompt not visible in WSL: \$p\" >&2; exit 2; }; mkdir -p /tmp/cross-review && cp \"\$p\" /tmp/cross-review/prompt.txt && export DEEPSEEK_API_KEY=\$(grep '^deepseek=' \"\$s\" | cut -d= -f2-) && [ -n \"\$DEEPSEEK_API_KEY\" ] || { echo 'deepseek key missing in secrets file' >&2; exit 2; }; cd /tmp/cross-review && timeout 900 opencode run --model deepseek/deepseek-v4-flash 'Follow the instructions in the attached file exactly; it is the whole task. Do not call any tool. Answer directly as plain text.' --file /tmp/cross-review/prompt.txt < /dev/null" 2>>"$work/err.log" | sed -e 's/\x1b\[[0-9;]*m//g' | grep -v '^> build ·' >> "$out" || echo "(part $i failed; see err.log)" >> "$out"
done
echo "done: $out ($(wc -c < "$out") bytes)"
