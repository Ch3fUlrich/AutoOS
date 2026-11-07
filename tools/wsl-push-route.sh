#!/usr/bin/env bash
set -euo pipefail

refuse() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# == 3 ]] || refuse 'expected sha, source-clone and verify-clone'
sha=$1
source_clone=$2
verify_clone=$3
[[ $sha =~ ^[0-9a-f]{40}$ ]] || refuse 'sha must be 40 lowercase hex characters'
git -C "$source_clone" rev-parse --git-dir >/dev/null 2>&1 || refuse 'source is not a git repo'
git -C "$verify_clone" rev-parse --git-dir >/dev/null 2>&1 || refuse 'verify is not a git repo'
source_clone=$(cd "$source_clone" && pwd -P)
verify_clone=$(cd "$verify_clone" && pwd -P)
git -C "$verify_clone" fetch origin main >&2 || refuse 'cannot fetch origin main'
git -C "$verify_clone" fetch "$source_clone" "$sha" >&2 || refuse "$sha: source will not serve the requested commit"
resolved=$(git -C "$verify_clone" rev-parse "$sha^{commit}" 2>/dev/null) || refuse "$sha: not a commit"
[[ $resolved == "$sha" ]] || refuse "$sha: commit identity mismatch"
git -C "$verify_clone" merge-base --is-ancestor origin/main "$sha" || refuse "$sha: origin/main is not an ancestor"
worktree=$(mktemp -d "${TMPDIR:-/tmp}/wsl-route.XXXXXXXX")
cleanup() {
    git -C "$verify_clone" worktree remove --force "$worktree" >&2
    git -C "$verify_clone" worktree prune >&2
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
git -C "$verify_clone" worktree add --detach "$worktree" "$sha" >&2
cd "$worktree"
if python3 tools/prepush.py --check-ready "$sha" >/dev/null 2>&1; then
    printf 'skipped: %s already has a green record\n' "$sha"
else
    log=$(mktemp "${TMPDIR:-/tmp}/wsl-route-log.XXXXXXXX")
    printf 'gate log: %s\n' "$log"
    python3 tools/prepush.py >"$log" 2>&1 || refuse "gate failed; log: $log"
fi
python3 tools/prepush.py --check-ready "$sha" >/dev/null 2>&1 || refuse 'green record not ready'
printf '%s\n' 'The script did not push; run the next line as a plain command (rule 3.27).'
printf 'MSYS_NO_PATHCONV=1 wsl.exe -- git -C %q push origin %s:refs/heads/main\n' "$verify_clone" "$sha"
