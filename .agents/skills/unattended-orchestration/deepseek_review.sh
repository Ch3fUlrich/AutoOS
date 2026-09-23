#!/usr/bin/env bash
# Cross-family review through DeepSeek (OpenCode in WSL), reading the key from
# the untracked secrets file so it never appears in a command line or a log.
#
#   deepseek_review.sh <prompt-file> [model] [timeout-seconds]
#
# <prompt-file> is a Windows or Git-Bash path to a UTF-8 text file whose first
# line is the standing instruction "DO NOT USE ANY TOOLS ..."; the whole file
# is attached with --file (argv cannot carry a 100 KB diff). Default model deepseek/deepseek-v4-flash (the
# configured provider also serves deepseek/deepseek-chat and
# deepseek/deepseek-reasoner). Output: the model's plain-text answer on stdout.
# Adopted 2026-09-17 when agy's Gemini quota kept failing mid-run (owner).
set -euo pipefail
prompt="${1:?prompt file}"; model="${2:-deepseek/deepseek-v4-flash}"; tmo="${3:-900}"
[ -s "$prompt" ] || { echo "prompt file missing or empty: $prompt" >&2; exit 2; }
win=$(cygpath -w "$prompt")                       # Git-Bash -> C:\... ; wslpath does the rest inside WSL
# Secrets lookup: $DEEPSEEK_SECRETS or $AUTOOS_SECRETS when set (a missing file is an
# error, never a silent switch to another file); otherwise ~/.config/autoos/api_keys.conf,
# then (legacy, with a notice) this script's repo's secrets/api_keys.conf, resolved
# through --git-common-dir when run from a worktree.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
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
secrets=$(cygpath -w "$secrets")
wsl -e bash -lc "p=\$(wslpath -u '$win'); s=\$(wslpath -u '$secrets'); [ -s \"\$p\" ] || { echo \"prompt not visible in WSL: \$p\" >&2; exit 2; }; export DEEPSEEK_API_KEY=\$(grep '^deepseek=' \"\$s\" | cut -d= -f2-); [ -n \"\$DEEPSEEK_API_KEY\" ] || { echo 'deepseek key missing in secrets file' >&2; exit 2; }; mkdir -p /tmp/cross-review && cd /tmp/cross-review && timeout '$tmo' opencode run --model '$model' 'Follow the instructions in the attached file exactly; it is the whole task. Answer directly as plain text.' --file \"\$p\" < /dev/null" 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g' | grep -v '^> build ·' | sed '/^\s*$/d'
