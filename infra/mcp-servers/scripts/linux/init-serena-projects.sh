#!/usr/bin/env bash
# MCP Server Stack — Serena Project Initialization Script (Linux / macOS / Git-Bash)
# ============================================================================
# Detects which programming languages are actually present in a repository by
# scanning file extensions (ignoring vendored / build / cache directories) and
# creates a Serena project.yml that enables language servers ONLY for those
# languages -- and only when their toolchain is actually installed.
#
# Mirrors scripts/windows/init-serena-projects.ps1 -- keep the two in sync.
# A language pinned without a working toolchain does not just lose its own
# symbols: Serena's LanguageServerManager fails all-or-nothing, so ONE
# unstartable language server disables every symbolic tool for the whole
# project (find_symbol, get_symbols_overview, etc.), not just that language's.
#
# Usage:
#   init-serena-projects.sh [PATH]              Initialize one repo (default: cwd)
#   init-serena-projects.sh --code-root DIR     Batch: every git repo directly under DIR
#   init-serena-projects.sh --force [...]       Recreate project.yml even if one exists
#   init-serena-projects.sh --dry-run [...]     Detect + print, write nothing
#   init-serena-projects.sh --skip-toolchain-check [...]  Keep detected compiled
#                                                languages even if their toolchain is missing
#   init-serena-projects.sh --no-index [...]    Create project.yml but skip symbol indexing
#
# NOTE: batch mode (--code-root) touches every git repo directly under DIR that
# does not already have a .serena/project.yml. It is opt-in, not the default --
# never invoke it against a shared code root without confirming that is intended.
# ============================================================================
set -euo pipefail

CODE_ROOT=""
TARGET_PATH=""
FORCE=0
DRY_RUN=0
SKIP_TOOLCHAIN_CHECK=0
NO_INDEX=0

while [ $# -gt 0 ]; do
    case "$1" in
        --code-root) CODE_ROOT="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --skip-toolchain-check) SKIP_TOOLCHAIN_CHECK=1; shift ;;
        --no-index) NO_INDEX=1; shift ;;
        -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) TARGET_PATH="$1"; shift ;;
    esac
done

# --- file extension -> Serena language (mirror windows/init-serena-projects.ps1) ---
lang_for_ext() {
    case "$1" in
        py|pyi|pyw) echo python ;;
        ts|tsx|mts|cts|js|jsx|mjs|cjs) echo typescript ;;
        html|htm) echo html ;;
        css|scss|sass) echo scss ;;
        md|markdown) echo markdown ;;
        yaml|yml) echo yaml ;;
        json|jsonc) echo json ;;
        toml) echo toml ;;
        cs) echo csharp ;;
        sh|bash) echo bash ;;
        ps1|psm1|psd1) echo powershell ;;
        rs) echo rust ;;
        c|h|cpp|cc|cxx|hpp|hh|hxx) echo cpp ;;
        go) echo go ;;
        java) echo java ;;
        rb) echo ruby ;;
        php) echo php ;;
        lua) echo lua ;;
        swift) echo swift ;;
        kt|kts) echo kotlin ;;
        dart) echo dart ;;
        r) echo r ;;
        jl) echo julia ;;
        ex|exs) echo elixir ;;
        erl|hrl) echo erlang ;;
        zig) echo zig ;;
        vue) echo vue ;;
        svelte) echo svelte ;;
        tf|tfvars) echo terraform ;;
        clj|cljs|cljc) echo clojure ;;
        scala|sc) echo scala ;;
        hs) echo haskell ;;
        nix) echo nix ;;
        sol) echo solidity ;;
    esac
}

# Compiled languages whose language server needs an external toolchain on PATH.
# csharp is handled separately in apply_toolchain_guard: `dotnet` merely
# existing is not enough, Serena's C# LSP needs .NET runtime 10.x specifically.
toolchain_for() {
    case "$1" in
        go) echo go ;;
        rust) echo rustc ;;
        cpp) echo clangd ;;
        java) echo java ;;
    esac
}

dotnet_has_runtime_10() {
    command -v dotnet >/dev/null 2>&1 || return 1
    dotnet --list-runtimes 2>/dev/null | grep -q '^Microsoft\.NETCore\.App 10\.'
}

# Directories never worth scanning (vendored deps, build output, caches, VCS).
# Walk the repo (pruning at any depth) and print detected Serena languages,
# most-prevalent first -- the first one becomes Serena's default/fallback.
detect_languages() {
    local repo="$1"
    find "$repo" \( \
        -name .git -o -name .serena -o -name .cursor -o -name .idea -o -name .vscode -o -name .vs \
        -o -name .venv -o -name venv -o -name env -o -name ENV -o -name node_modules -o -name __pycache__ \
        -o -name .mypy_cache -o -name .ruff_cache -o -name .pytest_cache -o -name .tox -o -name .nox \
        -o -name build -o -name dist -o -name target -o -name out -o -name obj -o -name bin \
        -o -name site-packages -o -name _build -o -name htmlcov -o -name .eggs -o -name __marimo__ \
        -o -name .next -o -name .cache -o -name .gradle \
    \) -prune -o -type f -print 2>/dev/null \
    | sed -n 's/.*\.\([A-Za-z0-9]*\)$/\1/p' \
    | tr '[:upper:]' '[:lower:]' \
    | while IFS= read -r ext; do lang_for_ext "$ext"; done \
    | grep -v '^$' \
    | sort | uniq -c | sort -rn | awk '{print $2}' \
    || true
}

# Toolchain guard: drop detected compiled languages whose toolchain (or, for
# csharp, whose required runtime version) is not actually installed.
apply_toolchain_guard() {
    local lang tool
    for lang in "$@"; do
        case "$lang" in
            csharp)
                if [ "$SKIP_TOOLCHAIN_CHECK" = "1" ]; then
                    echo "$lang"
                elif dotnet_has_runtime_10; then
                    echo "$lang"
                elif command -v dotnet >/dev/null 2>&1; then
                    echo "      (skipping 'csharp': dotnet found but no .NET 10.x runtime installed)" >&2
                else
                    echo "      (skipping 'csharp': 'dotnet' not on PATH)" >&2
                fi
                ;;
            go|rust|cpp|java)
                tool=$(toolchain_for "$lang")
                if [ "$SKIP_TOOLCHAIN_CHECK" = "1" ] || command -v "$tool" >/dev/null 2>&1; then
                    echo "$lang"
                else
                    echo "      (skipping '$lang': '$tool' not on PATH)" >&2
                fi
                ;;
            *) echo "$lang" ;;
        esac
    done
}

init_repo() {
    local repo="$1" name yml detected kept
    name=$(basename "$repo")
    yml="$repo/.serena/project.yml"

    if [ -f "$yml" ]; then
        if [ "$FORCE" != "1" ]; then
            echo "  - $name : already configured (use --force to regenerate)"
            return 0
        fi
        # `serena project create` hard-refuses when project.yml already exists
        # (no overwrite flag on its own CLI) -- --force must remove it first.
        [ "$DRY_RUN" = "1" ] || rm -f "$yml"
    fi

    detected=$(detect_languages "$repo")
    kept=""
    if [ -n "$detected" ]; then
        # shellcheck disable=SC2086  # word-splitting is deliberate: $detected is a newline list of language tokens
        kept=$(apply_toolchain_guard $detected)
    fi

    if [ -z "$kept" ]; then
        echo "  - $name : (none detected -> Serena will infer)"
    else
        echo "  - $name : $(echo "$kept" | tr '\n' ',' | sed 's/,/, /g; s/, $//')"
    fi

    [ "$DRY_RUN" = "1" ] && return 0

    local create_args=(project create "$repo" --name "$name") l
    # shellcheck disable=SC2086  # word-splitting is deliberate: $kept is a newline list of language tokens
    for l in $kept; do
        create_args+=(--language "$l")
    done
    [ "$NO_INDEX" = "1" ] || create_args+=(--index)

    if printf 'n\n%.0s' {1..5} | serena "${create_args[@]}" >/dev/null 2>&1; then
        if [ "$NO_INDEX" = "1" ]; then
            echo "      created (not indexed)"
        else
            echo "      created + indexed"
        fi
    else
        echo "      X error creating project for $name" >&2
        return 1
    fi
}

echo -e "\033[36m======================================================================\033[0m"
echo -e "\033[36m  Serena - Initialize Projects (per-repo language autodetection)      \033[0m"
echo -e "\033[36m======================================================================\033[0m"

if [ "$DRY_RUN" != "1" ] && ! command -v serena >/dev/null 2>&1; then
    echo "X 'serena' is not on PATH. Install it first: uv tool install serena-agent" >&2
    exit 1
fi

# Version guard. `serena project create` writes project.yml in the schema of the
# serena that RUNS IT, and that key is version-coupled: <=1.6.1 read `languages:`,
# 1.7.0+ read `language_servers:`, and nothing maps the new key backward. Generate
# with a different version than the one serving MCP and every symbolic tool dies in
# every repo, with nothing visibly wrong in the file (as-rule-serena-project-yml-key-
# version-coupled). PATH serena and the MCP server are routinely different builds --
# the MCP entry pins a uvx version while PATH holds a `uv tool install`.
mcp_serena_version() {
    # The server's own startup log is the only authority on what is actually running;
    # a pin in ~/.claude.json is what will run NEXT restart, not what runs now.
    # Every step tolerates absence: under `set -e` a bare failure here would abort
    # the script instead of degrading to the "could not determine" warning below.
    local log
    log=$(ls -t "$HOME"/.serena/logs/*/mcp_*.txt 2>/dev/null | head -1 || true)
    [ -n "$log" ] || return 0
    sed -n 's/.*Starting Serena server (version=\([0-9][0-9.]*\).*/\1/p' "$log" 2>/dev/null | head -1 || true
}

if [ "$DRY_RUN" != "1" ] && [ "${SKIP_VERSION_CHECK:-0}" != "1" ]; then
    cli_v=$(serena --version 2>/dev/null | sed -n 's/.*[Ss]erena \([0-9][0-9.]*\).*/\1/p' | head -1 || true)
    mcp_v=$(mcp_serena_version || true)
    if [ -n "$mcp_v" ] && [ -n "$cli_v" ] && [ "$cli_v" != "$mcp_v" ]; then
        cat >&2 <<MSG
X REFUSING TO WRITE -- serena version mismatch.
    PATH 'serena' (would generate project.yml) : $cli_v
    serena actually serving MCP (its own log)  : $mcp_v

  project.yml's language key is version-coupled: <=1.6.1 use 'languages:',
  1.7.0+ use 'language_servers:', and there is no backward mapping. Generating
  with $cli_v while MCP runs $mcp_v breaks activate_project in EVERY repo.

  Fix one of:
    - run via the version serving MCP:
        uvx --from serena-agent==$mcp_v serena project create ...
      (or re-run this script with that on PATH first)
    - align the install:  uv tool install --force serena-agent==$mcp_v
    - override deliberately:  SKIP_VERSION_CHECK=1 $0 ...
MSG
        exit 1
    fi
    if [ -z "$mcp_v" ]; then
        echo "! Could not read the MCP serena version from ~/.serena/logs/*/mcp_*.txt;" >&2
        echo "  proceeding with PATH serena ${cli_v:-unknown}. Verify activate_project after." >&2
    fi
fi

ERRORS=0

if [ -n "$CODE_ROOT" ]; then
    [ -d "$CODE_ROOT" ] || { echo "X CodeRoot not found: $CODE_ROOT" >&2; exit 1; }
    echo "Batch mode: scanning git repos directly under $CODE_ROOT"
    echo
    for repo in "$CODE_ROOT"/*/; do
        [ -d "${repo}.git" ] || continue
        init_repo "${repo%/}" || ERRORS=$((ERRORS + 1))
    done
else
    TARGET_PATH="${TARGET_PATH:-$(pwd)}"
    TARGET_PATH=$(cd "$TARGET_PATH" && pwd)
    echo "Single repo: $TARGET_PATH"
    echo
    init_repo "$TARGET_PATH" || ERRORS=$((ERRORS + 1))
fi

echo
echo -e "\033[36m----------------------------------------------------------------------\033[0m"
echo -e "\033[36m  errors=$ERRORS\033[0m"
echo -e "\033[36m======================================================================\033[0m"
