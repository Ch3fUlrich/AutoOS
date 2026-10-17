#!/usr/bin/env bash
# shellcheck shell=bash
#
# omnigraph-mcp-autoos — AutoOS's launcher for the pinned Omnigraph MCP bridge.
#
# An MCP client starts its server from a non-interactive shell (`bash -c`),
# which never reads ~/.bashrc or ~/.zshrc, so the token cannot ride in from an
# rc file. This script reads AutoOS's own env file and execs the bridge that
# `omnigraph-client` pre-installed into a private npm prefix — pre-installed
# rather than `npx`, because 16 parallel npx start-ups measured 6.7-9.3 s each
# (spec 2026-09-27 decision D9).
#
# Written by lib/linux/install.sh (install_omnigraph_client); the tracked source
# lives in tools/omnigraph-mcp-autoos.sh. That step recognises its own copy of
# this file by the marker line below, and leaves a file without it alone.
# AutoOS:omnigraph-mcp-autoos
#
# The PowerShell twin is tools/omnigraph-mcp-autoos.ps1 — change both.
set -euo pipefail

env_file="${HOME}/.autoos-omnigraph.env"
bridge="${HOME}/.local/share/autoos/omnigraph-mcp/bin/omnigraph-mcp"

if [[ -r "$env_file" ]]; then
    # Values are assigned, never evaluated: a token or URL holding $(...) must not
    # run here (the same rule as the rc-file line install.sh writes). Only the
    # three keys the bridge uses, and a value the caller already exported wins.
    # A file that a person edited by hand is read the way they meant it: an
    # optional indent, an optional `export `, whitespace round the value (which
    # is never part of it), and one layer of matching quotes round a value are
    # stripped — nothing else, so a value that only starts with a quote keeps its
    # own text, and a quote inside one survives. The same rule as the PowerShell
    # twin and the rc line install.sh writes, so one env file yields one set of
    # tokens whichever reader gets there first.
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        line="${line#"${line%%[![:space:]]*}"}"
        case "$line" in
            export[[:blank:]]*) line="${line#export}" ;;
        esac
        line="${line#"${line%%[![:space:]]*}"}"
        case "$line" in
            OMNIGRAPH_BASE_URL=*|OMNIGRAPH_TOKEN=*|OMNIGRAPH_GRAPH_ID=*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        value="${line#*=}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        [[ -n "$value" ]] || continue
        if [[ -z "${!key:-}" ]]; then
            printf -v "$key" '%s' "$value"
            # Exporting the NAME, not the literal word "key": $key is one of the
            # three whitelisted names above, never anything else.
            # shellcheck disable=SC2163
            export "$key"
        fi
    done <"$env_file"
fi

if [[ ! -x "$bridge" ]]; then
    # Never print the token or the file's contents — this goes to a client's log.
    printf '%s\n' "omnigraph-mcp-autoos: the pinned bridge is missing at ${bridge} - re-run ./setup.sh --only omnigraph-client" >&2
    exit 127
fi

exec "$bridge" "$@"
