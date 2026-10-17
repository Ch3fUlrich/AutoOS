#!/usr/bin/env bash
# MCP Server Stack — Graphify Scope Check (Linux)
# ============================================================================
# Graphify is wired as ONE cwd-relative `graphify` entry in USER scope
# (~/.claude.json) — NEVER per repo. graphify.serve is stdio and inherits its
# launch directory, so a single entry with the RELATIVE path
# graphify-out/graph.json serves whichever repo you started Claude Code in.
#   Workstation: command `uv` ... graphify.serve graphify-out/graph.json
#   Server:      command `graphify-mcp` (the bin/ wrapper: docker -v "$PWD:/repo")
# See skills/mcp-servers-setup/SKILL.md -> Graphify.
#
# What this checks:
#   USER scope  ~/.claude.json
#     - exactly one `graphify` entry, cwd-relative (uv, or the graphify-mcp wrapper)
#         missing        -> repos with a graph won't be served
#         hardcoded mount-> serves ONE repo to EVERY repo (the retired bug)
#     - NO `omnigraph` or `graphify-docker` in user scope
#         omnigraph is genuinely per-repo (project scope); graphify-docker is retired
#   PER repo  (any repo with a graphify-out/ graph)
#     - NO graphify entry in <repo>/.mcp.json — graphify is user-scope, not per repo
#     - NO stale graphify approval in <repo>/.claude/settings.local.json. Checked
#       independently of the entry: an approval outlives the server it approved, so
#       once the entry is gone nothing would ever look at it again.
#     - a built graph at <repo>/graphify-out/graph.json
#     - no root-owned graphify-out (a Docker rebuild without --user leaves files you
#       cannot overwrite) — only reachable on the server/Docker path
#
# Usage:  bash linux/check-graphify-scope.sh [--code-root DIR] [--fix]
#   --fix  apply the safe, UNTRACKED-only repairs: drop a stray graphify approval
#          from a repo's settings.local.json and chown root-owned graphify-out
#          back to you. Never edits tracked files, never registers user-scope
#          entries (that is `claude mcp add`, printed for you), never builds a graph.
#
# Exit 0 = user scope correct and every repo with a graph is served by it.
# ============================================================================
set -uo pipefail

CODE_ROOT="${CODE_ROOT:-$HOME/code}"
FIX=0
while [ $# -gt 0 ]; do
    case "$1" in
        --code-root) CODE_ROOT="$2"; shift 2 ;;
        --fix) FIX=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[90m'; C=$'\033[36m'; N=$'\033[0m'
PROBLEMS=0

echo "${C}======================================================================${N}"
echo "${C}  Graphify — scope check   (code root: $CODE_ROOT)${N}"
echo "${C}======================================================================${N}"

# ---------------------------------------------------------------- user scope
# Graphify BELONGS here as one cwd-relative entry; omnigraph/graphify-docker do not.
echo ""
echo "USER SCOPE  ~/.claude.json"
read -r GSTATUS BAD < <(python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / '.claude.json'
try:
    ms = json.loads(p.read_text()).get('mcpServers') or {}
except Exception:
    ms = {}
bad = [n for n in ('omnigraph', 'graphify-docker') if n in ms]
g = ms.get('graphify')
status = 'MISSING'
if g:
    args = g.get('args') or []
    cmd = g.get('command', '')
    # A hardcoded absolute bind mount (…:/repo where the host side is not $PWD/${..})
    # is the retired per-repo style; a cwd-relative graph path or the wrapper is correct.
    hard = any(':/repo' in a and not a.lstrip().startswith('$') for a in args)
    rel = ('graphify-out/graph.json' in args) or cmd == 'graphify-mcp'
    if hard and not rel:
        status = 'HARDCODED'
    elif rel or cmd in ('uv', 'uvx', 'python', 'graphify-mcp'):
        status = 'OK'
    else:
        status = 'UNKNOWN'
print(status, ' '.join(bad))
PY
)
case "$GSTATUS" in
    OK)       echo "  ${G}v graphify present and cwd-relative${N}" ;;
    MISSING)  echo "  ${R}X graphify MISSING — repos with a graph won't be served${N}"
              echo "    ${D}Workstation: claude mcp add -s user graphify -- \\${N}"
              echo "    ${D}  uv run --with 'graphifyy[mcp]' python -m graphify.serve graphify-out/graph.json${N}"
              echo "    ${D}Server: build graphify-mcp:latest, put bin/graphify-mcp on PATH, then${N}"
              echo "    ${D}  claude mcp add -s user graphify -- graphify-mcp${N}"
              PROBLEMS=$((PROBLEMS + 1)) ;;
    HARDCODED) echo "  ${R}X graphify hardcodes a repo mount — serves ONE repo to EVERY repo${N}"
              echo "    ${D}Replace with the cwd-relative uv entry or the graphify-mcp wrapper.${N}"
              PROBLEMS=$((PROBLEMS + 1)) ;;
    *)        echo "  ${Y}~ graphify present but its command/args are unrecognised — verify by hand${N}" ;;
esac
if [ -n "${BAD:-}" ]; then
    echo "  ${R}X must NOT be in user scope: $BAD${N}"
    echo "    ${D}omnigraph is per-repo (project scope); graphify-docker is retired. Remove:${N}"
    for s in $BAD; do echo "    ${D}claude mcp remove -s user $s${N}"; done
    PROBLEMS=$((PROBLEMS + 1))
fi

# ------------------------------------------------------------------ per repo
for repo in "$CODE_ROOT"/*/; do
    repo="${repo%/}"
    name="$(basename "$repo")"
    [ -d "$repo/.git" ] || continue
    # "Participating" = has a built graph. graphify is no longer declared per repo,
    # so a project .mcp.json is not what opts a repo in — the graphify-out/ dir is.
    [ -d "$repo/graphify-out" ] || continue

    echo ""
    echo "${C}$name${N}  ${D}$repo${N}"

    # -- gate 1: NO graphify entry in the project .mcp.json ----------------
    STRAY=$(python3 - "$repo" <<'PY'
import json, sys, pathlib
repo = pathlib.Path(sys.argv[1])
try:
    ms = json.loads((repo/'.mcp.json').read_text()).get('mcpServers') or {}
except Exception:
    ms = {}
print(' '.join(n for n in ms if 'graphify' in n))
PY
)
    if [ -z "$STRAY" ]; then
        echo "  ${G}v gate 1  no project-scope graphify entry (correct — it's user scope)${N}"
    else
        echo "  ${R}X gate 1  stray project graphify entry: $STRAY${N}"
        echo "    ${D}graphify is a single user-scope entry; remove '$STRAY' from${N}"
        echo "    ${D}$name/.mcp.json (tracked — edit by hand) and its approval below.${N}"
        PROBLEMS=$((PROBLEMS + 1))
    fi

    # -- stray approval: checked INDEPENDENTLY of gate 1 -------------------
    # An approval outlives the server it approved. Once graphify-docker is gone from
    # .mcp.json, gate 1 passes and a leftover `graphify-docker` in enabledMcpjsonServers
    # would never be looked at again — silent drift that reads as "still per-repo".
    SETTINGS="$repo/.claude/settings.local.json"
    APPROVED=""
    [ -f "$SETTINGS" ] && APPROVED=$(python3 - "$SETTINGS" <<'PY'
import json, pathlib, sys
try: d = json.loads(pathlib.Path(sys.argv[1]).read_text())
except Exception: raise SystemExit
lst = d.get('enabledMcpjsonServers')
print(' '.join(s for s in lst if 'graphify' in s) if isinstance(lst, list) else '')
PY
)
    if [ -n "${APPROVED:-}" ]; then
        # --fix only touches the UNTRACKED approval, never the tracked .mcp.json
        if [ "$FIX" -eq 1 ]; then
            python3 - "$SETTINGS" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
try: d = json.loads(p.read_text())
except Exception: raise SystemExit
lst = d.get('enabledMcpjsonServers')
if isinstance(lst, list):
    d['enabledMcpjsonServers'] = [s for s in lst if 'graphify' not in s]
    p.write_text(json.dumps(d, indent=2) + '\n')
PY
            echo "  ${Y}~ approval  FIXED — removed stale '$APPROVED' from settings.local.json${N}"
        else
            echo "  ${R}X approval  stale graphify approval '$APPROVED' in settings.local.json${N}"
            echo "    ${D}It approves a server that no longer exists. Re-run with --fix.${N}"
            PROBLEMS=$((PROBLEMS + 1))
        fi
    fi

    # -- gate 2: a graph exists -------------------------------------------
    if [ -f "$repo/graphify-out/graph.json" ]; then
        AGE=$(( ( $(date +%s) - $(stat -c %Y "$repo/graphify-out/graph.json") ) / 86400 ))
        if [ "$AGE" -gt 14 ]; then
            echo "  ${Y}~ gate 2  graph exists but is ${AGE}d old — consider rebuilding${N}"
        else
            echo "  ${G}v gate 2  graph present (${AGE}d old)${N}"
        fi
    else
        echo "  ${R}X gate 2  no graphify-out/graph.json — server would serve nothing${N}"
        echo "    ${D}cd $repo && uv run --with 'graphifyy[mcp]' graphify update .${N}"
        echo "    ${D}(server without uv: docker run --rm -v \"\$PWD:/repo\" -w /repo \\${N}"
        echo "    ${D}   --entrypoint python graphify-mcp:latest -m graphify update .)${N}"
        PROBLEMS=$((PROBLEMS + 1))
    fi

    # -- trap: root-owned artefacts from a --user-less Docker rebuild -------
    if [ -d "$repo/graphify-out" ]; then
        ROOTED=$(find "$repo/graphify-out" ! -user "$(id -un)" 2>/dev/null | wc -l)
        if [ "$ROOTED" -gt 0 ]; then
            if [ "$FIX" -eq 1 ]; then
                docker run --rm -v "$repo:/repo" --entrypoint chown graphify-mcp:latest \
                    -R "$(id -u):$(id -g)" /repo/graphify-out >/dev/null 2>&1 \
                    && echo "  ${Y}~ owner  FIXED — chowned $ROOTED file(s) back to you${N}" \
                    || { echo "  ${R}X owner  $ROOTED file(s) not yours; chown failed${N}"; PROBLEMS=$((PROBLEMS + 1)); }
            else
                echo "  ${R}X owner  $ROOTED file(s) not owned by you (Docker rebuild without --user)${N}"
                echo "    ${D}The next rebuild will fail with permission denied. Run with --fix${N}"
                PROBLEMS=$((PROBLEMS + 1))
            fi
        fi
    fi
done

echo ""
echo "${C}----------------------------------------------------------------------${N}"
if [ "$PROBLEMS" -eq 0 ]; then
    echo "${G}  Graphify wiring correct — one user-scope entry serves every repo's own graph.${N}"
else
    echo "${R}  $PROBLEMS problem(s) found — see the fixes above (or re-run with --fix).${N}"
fi
echo "${C}======================================================================${N}"
[ "$PROBLEMS" -eq 0 ]
