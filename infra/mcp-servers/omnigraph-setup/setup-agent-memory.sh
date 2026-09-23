#!/usr/bin/env bash
# setup-agent-memory.sh — make the AGENT's memory bridge work on this machine (Linux/macOS).
#
# The sibling of setup-sync.sh. They fix two different things and are often confused:
#
#     setup-sync.sh          the TIMER that reconciles local <-> central. Reads .env.
#     setup-agent-memory.sh  the MCP BRIDGE your agent reads memory through. Reads the
#                            environment + each repo's .mcp.json. THIS one.
#
# Every failure it repairs is SILENT — the bridge starts, answers, and is simply wrong:
#
#   * a same-named `omnigraph` in ~/.claude.json (USER SCOPE) overrides every repo's
#     .mcp.json. On 2026-07-17 one pinned to graph_id `memory` hid every project's graph.
#     An agent read memory's 2 Preferences, concluded sibling-analysis-repo (135 nodes, intact)
#     had been WIPED, and started rebuilding it into the globals-only graph.
#   * OMNIGRAPH_TOKEN unset  -> empty bearer -> "missing bearer token"
#   * OMNIGRAPH_NET wrong    -> "fetch failed". The network can EXIST but be EMPTY, so
#                               docker run succeeds and only DNS quietly fails.
#   * omnigraph-mcp:latest not built -> "pull access denied" (it is on no registry)
#
# Usage:
#   ./setup-agent-memory.sh              # diagnose AND fix
#   ./setup-agent-memory.sh --check      # diagnose only, change nothing
#   ./setup-agent-memory.sh --keep-user-scope   # leave ~/.claude.json alone
#   ./setup-agent-memory.sh --no-env            # do not touch your shell rc
#   ./setup-agent-memory.sh --code-root ~/src   # where sibling repos live
#
# Restart your agent afterwards — MCP servers and env vars are only read at session start.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mcp_root="$(cd "$here/.." && pwd)"
repo="$(cd "$mcp_root/../.." && pwd)"
shared="$mcp_root/.env.shared"
claude_json="$HOME/.claude.json"
IMAGE="omnigraph-mcp:latest"
CODE_ROOT="$(cd "$repo/.." && pwd)"
CHECK=0; KEEP_USER_SCOPE=0; NO_ENV=0
problems=0; fixed=0

log()  { printf '\033[36m[agent-memory]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[agent-memory] OK  \033[0m %s\n' "$*"; }
warn() { printf '\033[33m[agent-memory] WARN\033[0m %s\n' "$*"; }
bad()  { printf '\033[31m[agent-memory] FAIL\033[0m %s\n' "$*"; problems=$((problems+1)); }
die()  { printf '\033[31m[agent-memory] FAIL\033[0m %s\n' "$*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --keep-user-scope) KEEP_USER_SCOPE=1; shift ;;
    --no-env) NO_ENV=1; shift ;;
    --code-root) CODE_ROOT="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done
PY="${PYTHON:-python3}"
[ "$CHECK" = 1 ] && log "CHECK mode — nothing will be changed."

# --- 1. the token, from the file the server was started with ---------------------------
[ -f "$shared" ] || die "missing $shared — create it (cp .env.shared.example .env.shared) and set OMNIGRAPH_TOKEN."
TOKEN="$(sed -nE 's/^[[:space:]]*OMNIGRAPH_TOKEN[[:space:]]*=[[:space:]]*(.*)$/\1/p' "$shared" \
  | tail -1 | sed -E 's/[[:space:]]+$//; s/^"(.*)"$/\1/')"
case "${TOKEN:-}" in
  ""|generate-with-*|change-me*) die "OMNIGRAPH_TOKEN in .env.shared is empty or still the placeholder." ;;
esac
ok "token found in .env.shared (${#TOKEN} chars) — never printed"

# --- 2. the network, from the RUNNING container ----------------------------------------
NET=""
if command -v docker >/dev/null 2>&1; then
  NET="$(docker inspect omnigraph-server \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' 2>/dev/null \
    | sed '/^$/d' | head -1)"
fi
if [ -n "$NET" ]; then
  ok "docker network detected: $NET"
else
  bad "omnigraph-server is not running — the network cannot be detected and the bridge has
     nothing to talk to. Start the stack, then re-run:
       cd $mcp_root && docker compose --env-file .env.shared --env-file .env.server --env-file ~/.config/autoos/site.env -f docker-compose.server.yml up -d"
fi

# --- 3. the bridge image (docker form only; on no registry, so it must be built) --------
HAVE_IMAGE=0
if command -v docker >/dev/null 2>&1 && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  HAVE_IMAGE=1; ok "$IMAGE present"
elif [ "$CHECK" = 1 ]; then
  bad "$IMAGE MISSING — every docker-form bridge fails with 'pull access denied'. Re-run without --check."
else
  log "$IMAGE missing — building it (it is published to no registry)"
  if docker build -q -t "$IMAGE" "$mcp_root/servers/omnigraph-mcp" >/dev/null; then
    ok "built $IMAGE"; HAVE_IMAGE=1; fixed=$((fixed+1))
  else
    bad "could not build $IMAGE"
  fi
fi

# --- 4. the user-scope override — the one that fakes a data loss ------------------------
# Unable-to-check is NOT a clean bill of health: a same-named user-scope `omnigraph`
# silently outranks every repo's .mcp.json, and not knowing whether one exists is not the
# same as knowing one does not. So a parse failure is a PROBLEM, never an OK.
if [ ! -f "$claude_json" ]; then
  ok "~/.claude.json does not exist — no user-scope servers at all"
else
  OVERRIDE="$("$PY" - "$claude_json" <<'PY' 2>/dev/null
import json, pathlib, sys
try:
    d = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception as e:
    print("PARSE_ERROR: %s" % e); raise SystemExit(0)
cfg = (d.get("mcpServers") or {}).get("omnigraph")
print("" if cfg is None else (cfg.get("env", {}) or {}).get("OMNIGRAPH_GRAPH_ID", "?"))
PY
)"
  case "$OVERRIDE" in
    PARSE_ERROR*)
      bad "COULD NOT PARSE $claude_json — the user-scope override check DID NOT RUN.
     Reported as a problem rather than passed over. Check by hand:
       $PY -c \"import json,pathlib;print(sorted((json.loads((pathlib.Path.home()/'.claude.json').read_text()).get('mcpServers') or {})))\"
     (${OVERRIDE#PARSE_ERROR: })" ;;
    "")
      ok 'no user-scope `omnigraph` in ~/.claude.json — each repo keeps its own pin' ;;
    *)
      bad "USER-SCOPE OVERRIDE PRESENT: ~/.claude.json defines \`omnigraph\` (graph_id=$OVERRIDE).
     A same-named user-scope server SILENTLY WINS over every repo's .mcp.json, so every
     project reads '$OVERRIDE' no matter what its own config says. Nothing errors. This is
     how an intact 135-node graph was mistaken for a wiped one."
      if [ "$KEEP_USER_SCOPE" = 1 ]; then warn "left in place (--keep-user-scope)"
      elif [ "$CHECK" = 1 ];       then warn "run without --check to remove it (a backup is made first)"
      else
        bak="$claude_json.bak-$(date -u +%Y%m%dT%H%M%SZ)"
        cp -p "$claude_json" "$bak"
        if "$PY" - "$claude_json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]); d = json.loads(p.read_text(encoding="utf-8"))
(d.get("mcpServers") or {}).pop("omnigraph", None)
p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
PY
        then ok "removed the user-scope override (backup: $(basename "$bak"))"; fixed=$((fixed+1)); problems=$((problems-1))
        else bad "could not rewrite $claude_json — restore from $(basename "$bak") if needed"; fi
      fi ;;
  esac
fi

# --- 5. the environment the tracked .mcp.json files interpolate ------------------------
# Written to a marked block in your shell rc so it is idempotent and easy to remove by
# hand. The token is read from .env.shared, never echoed.
rc=""
for cand in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
  [ -f "$cand" ] && { rc="$cand"; break; }
done
[ -n "$rc" ] || rc="$HOME/.profile"
MARK_START="# >>> omnigraph agent memory (managed by setup-agent-memory.sh) >>>"
MARK_END="# <<< omnigraph agent memory <<<"
env_ok=0
if [ "${OMNIGRAPH_TOKEN:-}" = "$TOKEN" ] && [ -n "$NET" ] && [ "${OMNIGRAPH_NET:-}" = "$NET" ]; then
  env_ok=1; ok "OMNIGRAPH_TOKEN and OMNIGRAPH_NET already correct in this shell"
elif grep -qF "$MARK_START" "$rc" 2>/dev/null && [ "$CHECK" = 1 ]; then
  ok "$rc already has the managed block (open a new shell for it to apply)"; env_ok=1
fi
if [ "$env_ok" = 0 ]; then
  if [ "$CHECK" = 1 ] || [ "$NO_ENV" = 1 ]; then
    bad "OMNIGRAPH_TOKEN / OMNIGRAPH_NET are not set for your agent.
       OMNIGRAPH_TOKEN: $([ -n "${OMNIGRAPH_TOKEN:-}" ] && echo 'set but does not match .env.shared' || echo 'NOT SET -> empty bearer -> "missing bearer token"')
       OMNIGRAPH_NET:   ${OMNIGRAPH_NET:-NOT SET -> .mcp.json falls back to mcp-servers_default}
     Fix: re-run without --check/--no-env, or add to $rc yourself:
       export OMNIGRAPH_TOKEN=\$(sed -nE 's/^OMNIGRAPH_TOKEN=(.*)\$/\\1/p' $shared)
       export OMNIGRAPH_NET=$NET"
  else
    tmp="$(mktemp)"
    if [ -f "$rc" ]; then sed "/$(printf '%s' "$MARK_START" | sed 's/[][\.*^$/]/\\&/g')/,/$(printf '%s' "$MARK_END" | sed 's/[][\.*^$/]/\\&/g')/d" "$rc" > "$tmp"; fi
    {
      cat "$tmp" 2>/dev/null
      echo "$MARK_START"
      echo "# The MCP bridge in each repo's .mcp.json interpolates these. Unset => empty bearer"
      echo "# (\"missing bearer token\") or the wrong docker network (\"fetch failed\") — both silent."
      echo "export OMNIGRAPH_TOKEN=\$(sed -nE 's/^[[:space:]]*OMNIGRAPH_TOKEN[[:space:]]*=[[:space:]]*(.*)\$/\\1/p' '$shared' | tail -1)"
      [ -n "$NET" ] && echo "export OMNIGRAPH_NET='$NET'"
      echo "$MARK_END"
    } > "$rc.new" && mv -f "$rc.new" "$rc"
    rm -f "$tmp"
    fixed=$((fixed+1))
    ok "wrote the managed block to $rc (token is read from .env.shared at shell start, not copied)"
    warn "run 'source $rc' or open a new shell, then RESTART your agent"
  fi
fi

# --- 6. audit every sibling repo's .mcp.json -------------------------------------------
log "auditing .mcp.json under $CODE_ROOT"
GRAPHS=""
if [ -n "$TOKEN" ]; then
  GRAPHS="$(curl -fsS -m 20 "http://127.0.0.1:8080/graphs" -H "Authorization: Bearer $TOKEN" 2>/dev/null \
    | grep -o '"graph_id":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
  [ -n "$GRAPHS" ] && log "graphs the server actually has: $GRAPHS"
fi
for d in "$CODE_ROOT"/*/; do
  f="$d/.mcp.json"; [ -f "$f" ] || continue
  name="$(basename "$d")"
  out="$("$PY" - "$f" "$name" "$GRAPHS" <<'PY'
import json, pathlib, re, sys
f, name, graphs = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3].split()
try: d = json.loads(f.read_text(encoding="utf-8"))
except Exception as e: print(f"BAD|{name}/.mcp.json is not valid JSON: {e}"); raise SystemExit(0)
servers = {n: c for n, c in (d.get("mcpServers") or {}).items() if "omnigraph" in n}
if not servers: print(f"LOG|{name} : no omnigraph bridge (nothing to check)"); raise SystemExit(0)
for n, c in servers.items():
    blob = " ".join(map(str, c.get("args", []))); env = c.get("env", {}) or {}
    if n == "omnigraph-globals":
        print(f"BAD|{name} : declares 'omnigraph-globals' — removed 2026-07-17, now only yields 'invalid bearer token'. Delete the block.")
        continue
    m = re.search(r'OMNIGRAPH_GRAPH_ID=(\S+)', blob); gid = m.group(1) if m else env.get("OMNIGRAPH_GRAPH_ID")
    m = re.search(r'OMNIGRAPH_TOKEN=(\S*)', blob);    tok = m.group(1) if m else env.get("OMNIGRAPH_TOKEN", "")
    issues = []
    if not gid: issues.append("no OMNIGRAPH_GRAPH_ID (bridge has no graph)")
    elif graphs and gid not in graphs: issues.append(f"graph '{gid}' does not exist on the server")
    if tok and not tok.startswith("${"): issues.append("OMNIGRAPH_TOKEN is HARDCODED in a tracked file — use ${OMNIGRAPH_TOKEN}")
    m = re.search(r'--network\s+(\S+)', blob)
    if m and not m.group(1).startswith("${"):
        issues.append(f"docker --network hardcoded to '{m.group(1)}' — use ${{OMNIGRAPH_NET:-...}}, it differs per host")
    for i in issues: print(f"BAD|{name} : {i}")
    if not issues: print(f"OK|{name} : '{n}' -> graph '{gid}', token from env")
PY
)"
  while IFS='|' read -r kind msg; do
    [ -n "$msg" ] || continue
    case "$kind" in OK) ok "$msg" ;; BAD) bad "$msg" ;; *) log "$msg" ;; esac
  done <<< "$out"
done

# --- 7. prove it: drive the real bridge and count rows ---------------------------------
if [ "$HAVE_IMAGE" = 1 ] && [ -n "$NET" ] && [ "$CHECK" = 0 ]; then
  log "probing the bridge for real (initialize + snapshot)"
  for g in $GRAPHS; do
    rows="$(printf '%s\n%s\n' \
      '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}' \
      '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"snapshot","arguments":{}}}' \
      | timeout 90 docker run -i --rm --network "$NET" \
          -e OMNIGRAPH_BASE_URL=http://omnigraph-server:8080 \
          -e "OMNIGRAPH_GRAPH_ID=$g" -e "OMNIGRAPH_TOKEN=$TOKEN" "$IMAGE" 2>/dev/null \
      | "$PY" -c "
import sys, json
for ln in sys.stdin:
    ln = ln.strip()
    if not ln.startswith('{'): continue
    try: m = json.loads(ln)
    except Exception: continue
    if m.get('id') != 2: continue
    try:
        d = json.loads(m['result']['content'][0]['text'])
        print(sum(t.get('rowCount', 0) for t in d.get('tables', [])))
    except Exception: pass
" 2>/dev/null)"
    if [ -n "$rows" ]; then ok "bridge -> graph '$g' : $rows rows"; else bad "bridge -> graph '$g' : no usable response"; fi
  done
fi

# --- verdict ---------------------------------------------------------------------------
echo
if [ "$problems" -eq 0 ]; then
  ok "everything checks out$([ "$fixed" -gt 0 ] && echo " ($fixed fix(es) applied — RESTART your agent)")"
  exit 0
fi
if [ "$CHECK" = 1 ]; then
  bad "$problems problem(s) found. Re-run without --check to fix what is fixable."; exit 1
fi
warn "$problems problem(s) remain — see above.$([ "$fixed" -gt 0 ] && echo " $fixed fixed.")"
cat <<EOF

  Not everything here is auto-fixable. A .mcp.json belongs to its own repo, so edit those by
  hand (agent-skills/.mcp.json is the reference form) and commit them there.
EOF
exit 1
