#!/usr/bin/env bash
# Cross-family review through DeepSeek — the OmniRoute combo first, then OpenRouter direct —
# via deepseek_call.py, which reads the keys from AutoOS configuration/api-keys.yml itself, so a
# key never appears in a command line or a log.
#
#   deepseek_review.sh <prompt-file> [model] [timeout-seconds]
#
# <prompt-file> is a Windows, Git-Bash or POSIX path to a UTF-8 text file whose first line is
# the standing instruction "DO NOT USE ANY TOOLS ..."; the whole file is sent as one user
# message. [model] may only be deepseek-v4.1-flash (the combo), deepseek/deepseek-v4.1-flash or
# empty; anything else exits 2. Which served models count is catalog/ai-registry.json's call
# (route deepseek-v4.1-flash, policy.leg_rules; V4 Pro never passes). Output:
# the model's plain-text answer on stdout; one "served: <model> via <leg>" line on stderr.
# Keys: $AUTOOS_API_KEYS (path) or $AUTOOS_ROOT, else the AutoOS checkout is searched; env
# AUTOOS_OMNIROUTE_KEY / OPENROUTER_API_KEY override single keys. Details: deepseek_call.py.
# Adopted 2026-09-17 when agy's Gemini quota kept failing mid-run (owner); moved off
# opencode + ~/.config/autoos/api_keys.conf on 2026-09-25 when that file no longer existed.
set -euo pipefail
prompt="${1:?prompt file}"; model="${2:-}"; tmo="${3:-900}"
[ -s "$prompt" ] || { echo "prompt file missing or empty: $prompt" >&2; exit 2; }
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
# First interpreter that actually runs: on Windows `python3` is often the Store stub, which
# exists on PATH and does nothing useful.
py="${PYTHON:-}"
if [ -z "$py" ]; then
  for c in python3 python py; do "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1 && { py=$c; break; }; done
fi
[ -n "$py" ] || { echo "no python >= 3.9 found (set PYTHON)" >&2; exit 2; }
exec "$py" "$(native "$script_dir/deepseek_call.py")" "$(native "$prompt")" --model "$model" --timeout "$tmo"
