#!/usr/bin/env bash
# AutoOS "ollama-chat" — the binary behind the `local` backend in
# templates/ai-clients.conf ("local:ollama-chat:..."), installed to
# /usr/local/bin/ollama-chat by templates/rescue-bootstrap.sh.
#
# WHY THIS EXISTS INSTEAD OF POINTING THE REGISTRY STRAIGHT AT ollama OR
# oterm:
#
#   templates/ai-dispatcher.sh execs a registry backend's binary field
#   verbatim with the caller's own arguments — `exec "$bin" "$@"`. That is a
#   single binary name, not a command line, so `ai local "why did this
#   fail?"` needs field 2 of the registry to be something that itself knows
#   how to turn one argument into a model reply.
#
#   - `oterm` cannot be that binary: it is a full-screen Textual TUI with no
#     documented one-shot/headless mode (checked ggozad.github.io/oterm's
#     installation and commands pages, and its `--command` flag, which still
#     opens the interactive UI — it only preloads a saved prompt template).
#     Piped a prompt or run non-interactively, it does nothing useful.
#   - `ollama` alone is not enough either: it is a subcommand CLI
#     (`ollama run <model> ...`), so execing it directly against a bare
#     prompt string would try to run that string AS an ollama subcommand
#     ("ollama 'why did this fail?'") and fail immediately.
#   - `ollama run <model>` DOES work non-interactively: given a prompt as a
#     trailing argument (or piped on stdin with none), it prints one reply
#     and exits — exactly what a dispatcher backend needs. This script only
#     supplies the missing "run <model>" so the registry can still name one
#     plain binary.
#
# AUTOOS_OLLAMA_MODEL picks the model explicitly; default is qwen3:4b, the
# pre-ticked local-ai default (see catalog/linux.json — 2.5 GB, 256K
# context). If that tag isn't actually pulled on this machine (the rescue
# stick's model partition is built separately from this bootstrap and may
# carry a different subset), this falls back to whatever `ollama list`
# reports first, so `ai local "..."` still works with zero configuration as
# long as at least one model has been pulled.
set -euo pipefail

MODEL="${AUTOOS_OLLAMA_MODEL:-qwen3:4b}"

if ! command -v ollama >/dev/null 2>&1; then
    printf 'ollama-chat: ollama is not installed\n' >&2
    exit 127
fi

pulled="$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')"

if ! printf '%s\n' "$pulled" | grep -qxF -- "$MODEL"; then
    fallback="$(printf '%s\n' "$pulled" | head -n1)"
    if [[ -n "$fallback" ]]; then
        printf 'ollama-chat: model "%s" is not pulled — using "%s" instead\n' "$MODEL" "$fallback" >&2
        MODEL="$fallback"
    else
        printf 'ollama-chat: no Ollama models are pulled on this machine (try: ollama pull %s)\n' "$MODEL" >&2
        exit 1
    fi
fi

exec ollama run "$MODEL" "$@"
