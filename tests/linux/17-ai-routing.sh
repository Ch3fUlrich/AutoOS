# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── AI routing (omniroute / litellm / zed / opencode) ────────────────────
describe "AI routing"

if it "zed rides the script provider end to end"; then
    ok=1
    grep -q 'zed)             install_zed' lib/linux/install.sh || ok=0
    grep -q 'zed)             has_bin zed' lib/linux/detect.sh || ok=0
    # has_bin is stubbed so the test never depends on what this machine happens
    # to have installed (Zed is present on the dev box via WSL interop).
    ( has_bin() { return 1; }; script_is_installed zed ) >/dev/null 2>&1 && ok=0
    ( has_bin() { return 0; }; script_is_installed zed ) >/dev/null 2>&1 || ok=0
    if (( ok )); then pass; else fail "zed dispatch or detection is broken"; fi
fi

if it "openhands pulls the current image and announces in dry run"; then
    ok=1
    grep -q 'docker.openhands.dev/openhands/openhands:latest' lib/linux/install.sh || ok=0
    grep -q 'install_openhands' lib/linux/install.sh || ok=0
    out="$( ( AUTOOS_DRY_RUN=1; install_openhands ) 2>&1)"
    [[ "$out" == *"would pull"* ]] || ok=0
    if (( ok )); then pass; else fail "openhands installer is stale or silent in dry run"; fi
fi

if it "openhands wires the LLM through OmniRoute in start-stack"; then
    ok=1
    for f in configuration/start-stack.ps1 configuration/start-stack.sh; do
        grep -q 'LLM_MODEL=openai/t1-orchestrator' "$f" || { ok=0; echo "missing model in $f" >&2; }
        grep -q 'LLM_BASE_URL' "$f" || ok=0
        grep -q 'docker.openhands.dev/openhands/openhands:latest' "$f" || ok=0
        grep -q '3000:3000' "$f" || ok=0
    done
    if (( ok )); then pass; else fail "start-stack does not route OpenHands correctly"; fi
fi

if it "the OpenHands template carries the LiteLLM provider prefix"; then    # Every model line, not a sample: a new unprefixed line is the exact
    # "LLM Provider NOT provided" mismatch this guards against.
    bad="$(grep -nE '^[[:space:]]*model[[:space:]]*=' configuration/openhands/config.toml |
        grep -v 'openai/' || true)"
    ok=1
    grep -q 'model = "openai/t1-orchestrator"' configuration/openhands/config.toml || ok=0
    grep -q 'model = "openai/t3-driver"' configuration/openhands/config.toml || ok=0
    grep -q 'openai/t1-orchestrator-clean' configuration/openhands/config.toml || ok=0
    if [[ -n "$bad" ]]; then fail "model lines without the openai/ prefix: $bad"
    elif (( ok )); then pass
    else fail "template is missing the expected tier models"; fi
fi

if it "provider status never carries key values"; then
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null; then
        skip "python3 < 3.7 cannot import serve.py"
    else
        report="$(python3 - 2>&1 <<'PY'
import importlib.util, json, os, pathlib, sys, tempfile
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-serve-"))
(root / "configuration").mkdir()
(root / "configuration" / "api-keys.yml").write_text(
    "groq: gsk_SUPERSECRETVALUE123\ndeepseek:\nmistral: 5xLsSecretValue\n",
    encoding="utf-8")
spec = importlib.util.spec_from_file_location("autoos_serve", "lib/linux/serve.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["serve.py", str(root), "0", "127.0.0.1", "0"]
spec.loader.exec_module(mod)
mod.ROOT = root
status = mod.provider_status()
text = json.dumps(status)
problems = []
groq = [p for p in status if p["id"] == "groq"]
deepseek = [p for p in status if p["id"] == "deepseek"]
if not groq or not groq[0]["configured"]:
    problems.append("groq-not-configured")
if deepseek and deepseek[0]["configured"]:
    problems.append("empty-value-counted-as-configured")
if "SUPERSECRET" in text or "SecretValue" in text:
    problems.append("value-leaked")
print(" ".join(problems))
PY
)"
        assert_eq "$report" ""
    fi
fi

if it "server profile ticks the headless terminal stack"; then
    catalog_load catalog/linux.json x64 1
    defaults="$(catalog_profile_defaults server)"
    ok=1
    for c in opencode-cli omniroute litellm neovim; do
        [[ " $defaults" == *" $c "* ]] || { ok=0; echo "missing: $c" >&2; }
    done
    if (( ok )); then pass; else fail "server profile is missing headless components"; fi
fi

if it "python heredoc checks capture stderr"; then
    # Regression gate for the 2026-09-24 finding: a crashing python heredoc
    # prints to stderr, which $() does not capture, so an empty-expected
    # assert passed on empty stdout. Every heredoc opener feeding an
    # assert_eq "" must carry 2>&1 on the command line (this test included).
    bad="$(python3 - 2>&1 <<'PY'
import re, io, glob
bare = []
for path in ["tests/run-tests.sh"] + sorted(glob.glob("tests/linux/*.sh")):
    lines = io.open(path, encoding="utf-8").read().splitlines()
    for i, l in enumerate(lines):
        m = re.match(r"""\s*(\w+)="\$\(python3\b(.*)<<'PY'\s*$""", l)
        if m and "2>&1" not in m.group(2):
            var = m.group(1)
            for j in range(i + 1, min(i + 60, len(lines))):
                if re.match(r"""\s*assert_eq "\$%s" ""$""" % var, lines[j]):
                    bare.append("%s:%d" % (path, i + 1))
                    break
print(" ".join(bare))
PY
)"
    assert_eq "$bad" ""
fi

if it "zed requires the router on every platform"; then
    bad="$(python3 - 2>&1 <<'PY'
import json, glob
bad = []
for p in sorted(glob.glob("catalog/*.json")):
    for g in json.load(open(p, encoding="utf-8")).get("categories", []):
        for c in g["components"]:
            if c["id"] == "zed" and "litellm" not in c.get("requires", []):
                bad.append(p)
print(" ".join(bad))
PY
)"
    assert_eq "$bad" ""
fi

if it "route_detected_clis_to_gateway announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '{"theme":"mine"}' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; AUTOOS_OMNIROUTE_KEY="k1" OMNIROUTE_API_KEY="k2"; route_detected_clis_to_gateway ) 2>&1)"
    after="$(cat "$scratch/.claude/settings.json")"
    backups="$(find "$scratch" -name '*.autoos-backup-*' 2>/dev/null | wc -l)"
    rm -rf "$scratch"
    if [[ -n "$out" && "$before" == "$after" && "$backups" == "0" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
fi

if it "route_detected_clis_to_gateway bridges keys from the keys file"; then
    tmp="$(mktemp -d)"
    printf 'omniroute: test-file-key\n' >"$tmp/api-keys.yml"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=1
        unset OMNIROUTE_API_KEY AUTOOS_OMNIROUTE_KEY
        has_cmd() { return 1; }
        AUTOOS_KEYS_FILE="$tmp/api-keys.yml" route_detected_clis_to_gateway >/dev/null 2>&1
        printf '%s|%s' "${OMNIROUTE_API_KEY:-empty}" "${AUTOOS_OMNIROUTE_KEY:-empty}"
    )"
    rm -rf "$tmp"
    assert_eq "$out" "test-file-key|test-file-key"
fi

if it "zed routing announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; route_zed_to_proxy ) 2>&1)"
    if [[ "$out" == *"would route"* && ! -e "$scratch/.config/zed/settings.json" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
    rm -rf "$scratch"
fi

# Claude Code gateway routing is opt-in and reversible (catalog prompt
# claude_gateway_routing, default "login"). ANTHROPIC_BASE_URL/AUTH_TOKEN in
# ~/.claude/settings.json "env" disable the claude.ai connectors, so every answer
# other than "gateway" takes exactly those two keys out again. A backup is taken
# only by a run that changes the file, so each fixture below sees at most ONE
# modifying run and the exact backup count is safe from the per-second timestamp
# collision fixed in 0bb5952.
_claude_settings_report() {  # _claude_settings_report <settings.json>
    python3 - "$1" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
print("%s|%s|%s" % (cfg.get("theme"),
                    json.dumps(cfg.get("permissions"), sort_keys=True),
                    json.dumps(cfg.get("env"), sort_keys=True) if "env" in cfg else "no-env"))
PY
}
_claude_backups() {  # _claude_backups <home> -> number of settings.json backups
    find "$1/.claude" -maxdepth 1 -name 'settings.json.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '
}

if it "route_claude_to_gateway (default login answer) strips only the two gateway keys"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","permissions":{"allow":["Bash(ls)"]},"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:20128","ANTHROPIC_AUTH_TOKEN":"test-omni-key","KEEP_ME":"1"}}' \
        >"$scratch/.claude/settings.json"
    # A key in the environment must not matter any more: no answer means login.
    out1="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'
               AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    out2="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'
               AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    report="$(_claude_settings_report "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    backup_kept_keys="no"
    if grep -q 'ANTHROPIC_AUTH_TOKEN' "$scratch"/.claude/settings.json.autoos-backup-* 2>/dev/null; then backup_kept_keys="yes"; fi
    # "env" holding nothing but the two keys disappears entirely.
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch2/.claude/settings.json"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway >/dev/null 2>&1 )
    report2="$(_claude_settings_report "$scratch2/.claude/settings.json")"
    rm -rf "$scratch" "$scratch2"
    want='mine|{"allow": ["Bash(ls)"]}|{"KEEP_ME": "1"}'
    if [[ "$report" == "$want" && "$report2" == "mine|null|no-env" && "$backups" == 1 \
          && "$backup_kept_keys" == yes && "$out1" == *"removed"* && "$out2" == *"skipped"* \
          && "$out2" != *"removed"* ]]; then pass
    else fail "report=[$report] report2=[$report2] backups=$backups backup_kept_keys=$backup_kept_keys out1=[$out1] out2=[$out2]"; fi
fi

if it "route_claude_to_gateway (default login answer) leaves a file without the keys untouched"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"KEEP_ME":"1"}}' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway ) 2>&1)"
    after="$(cat "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    # No settings file at all: nothing is created, not even ~/.claude.
    scratch2="$(mktemp -d)"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway >/dev/null 2>&1 )
    created="no"; [[ -e "$scratch2/.claude" ]] && created="yes"
    rm -rf "$scratch" "$scratch2"
    if [[ "$before" == "$after" && "$backups" == 0 && "$created" == no && "$out" == *"skipped"* ]]; then pass
    else fail "changed=$([[ "$before" == "$after" ]] && echo no || echo yes) backups=$backups created=$created out=[$out]"; fi
fi

if it "route_claude_to_gateway (gateway answer) points Claude Code at OmniRoute, second run skipped"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"KEEP_ME":"1"}}' >"$scratch/.claude/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    out2="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
               AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    report="$(_claude_settings_report "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    # Gateway mode still needs the key: without it nothing is written.
    scratch2="$(mktemp -d)"
    nokey_out="$( ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
                    unset AUTOOS_OMNIROUTE_KEY; route_claude_to_gateway ) 2>&1)"
    nokey="no"; [[ -e "$scratch2/.claude/settings.json" ]] || nokey="yes"
    rm -rf "$scratch" "$scratch2"
    want='mine|null|{"ANTHROPIC_AUTH_TOKEN": "test-omni-key", "ANTHROPIC_BASE_URL": "http://127.0.0.1:20128", "KEEP_ME": "1"}'
    if [[ "$report" == "$want" && "$backups" == 1 && "$out2" == *"skipped"* \
          && "$nokey" == yes && "$nokey_out" == *"AUTOOS_OMNIROUTE_KEY not set"* ]]; then pass
    else fail "report=[$report] backups=$backups out2=[$out2] nokey=$nokey nokey_out=[$nokey_out]"; fi
fi

if it "route_claude_to_gateway dry run announces and writes nothing in either mode"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    login_out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway ) 2>&1)"
    after_login="$(cat "$scratch/.claude/settings.json")"
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.claude"
    printf '%s' '{"theme":"mine"}' >"$scratch2/.claude/settings.json"
    before2="$(cat "$scratch2/.claude/settings.json")"
    gw_out="$( ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=1; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
                 AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    after_gw="$(cat "$scratch2/.claude/settings.json")"
    scratch3="$(mktemp -d)"
    ( SYS_HOME="$scratch3" AUTOOS_DRY_RUN=1; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    created="no"; [[ -e "$scratch3/.claude" ]] && created="yes"
    backups="$(( $(_claude_backups "$scratch") + $(_claude_backups "$scratch2") ))"
    rm -rf "$scratch" "$scratch2" "$scratch3"
    if [[ "$before" == "$after_login" && "$before2" == "$after_gw" && "$backups" == 0 && "$created" == no \
          && "$login_out" == *"would remove"* && "$gw_out" == *"would point"* ]]; then pass
    else fail "backups=$backups created=$created login_out=[$login_out] gw_out=[$gw_out]"; fi
fi

if it "route_claude_to_gateway login mode works without AUTOOS_OMNIROUTE_KEY"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y","KEEP_ME":"1"}}' >"$scratch/.claude/settings.json"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=login
              unset AUTOOS_OMNIROUTE_KEY; route_claude_to_gateway ) 2>&1)"
    report="$(_claude_settings_report "$scratch/.claude/settings.json")"
    rm -rf "$scratch"
    if [[ "$report" == 'mine|null|{"KEEP_ME": "1"}' && "$out" != *"AUTOOS_OMNIROUTE_KEY"* ]]; then pass
    else fail "report=[$report] out=[$out]"; fi
fi

if it "route_claude_to_gateway leaves an unreadable settings file untouched in either mode"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme": "mine", "env": {"ANTHROPIC_BASE_URL": ' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    login_out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway ) 2>&1)"; rc1=$?
    gw_out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
                 AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"; rc2=$?
    after="$(cat "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    rm -rf "$scratch"
    if [[ "$before" == "$after" && "$backups" == 0 && "$rc1$rc2" == 00 \
          && "$login_out" == *"left untouched"* && "$gw_out" == *"left untouched"* ]]; then pass
    else fail "changed=$([[ "$before" == "$after" ]] && echo no || echo yes) backups=$backups rc=$rc1$rc2 login_out=[$login_out] gw_out=[$gw_out]"; fi
fi

_file_mode() {  # _file_mode <path> -> permission bits in octal, e.g. 600
    python3 -c 'import os, sys; print("%o" % (os.stat(sys.argv[1]).st_mode & 0o777))' "$1" 2>&1
}
_file_inode() {  # _file_inode <path>
    python3 -c 'import os, sys; print(os.stat(sys.argv[1]).st_ino)' "$1" 2>&1
}

if it "route_claude_to_gateway replaces settings.json atomically with mode 600, backups 600"; then
    # The file holds ANTHROPIC_AUTH_TOKEN, and an in-place rewrite that dies
    # mid-write (crash, ENOSPC) leaves it empty: temp file + os.replace, 0600.
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine"}' >"$scratch/.claude/settings.json"
    chmod 644 "$scratch/.claude/settings.json"
    inode_before="$(_file_inode "$scratch/.claude/settings.json")"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    inode_after="$(_file_inode "$scratch/.claude/settings.json")"
    gw_mode="$(_file_mode "$scratch/.claude/settings.json")"
    gw_backup_mode="$(_file_mode "$(find "$scratch/.claude" -name 'settings.json.autoos-backup-*' | head -1)")"
    leftovers="$(find "$scratch/.claude" -name '*autoos-tmp*' | wc -l | tr -d ' ')"
    # A settings file created from nothing is 600 as well.
    scratch2="$(mktemp -d)"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    new_mode="$(_file_mode "$scratch2/.claude/settings.json")"
    # The login-mode rewrite goes the same way, and its backup still holds the token.
    scratch3="$(mktemp -d)"
    mkdir -p "$scratch3/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch3/.claude/settings.json"
    chmod 644 "$scratch3/.claude/settings.json"
    ( SYS_HOME="$scratch3" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway >/dev/null 2>&1 )
    login_mode="$(_file_mode "$scratch3/.claude/settings.json")"
    login_backup_mode="$(_file_mode "$(find "$scratch3/.claude" -name 'settings.json.autoos-backup-*' | head -1)")"
    rm -rf "$scratch" "$scratch2" "$scratch3"
    got="$gw_mode|$gw_backup_mode|$new_mode|$login_mode|$login_backup_mode|$leftovers"
    if [[ "$got" == "600|600|600|600|600|0" && "$inode_before" != "$inode_after" ]]; then pass
    else fail "modes|leftovers=[$got] (want 600|600|600|600|600|0) inode $inode_before -> $inode_after (must change: replaced, not rewritten in place)"; fi
fi

if it "route_claude_to_gateway trims and lower-cases the answer, like the PowerShell side"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine"}' >"$scratch/.claude/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=" Gateway "
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    padded="$(_claude_settings_report "$scratch/.claude/settings.json")"
    # Inner whitespace is not stripped: "gate way" is not gateway, so it means login.
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch2/.claude/settings.json"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]="gate way"
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    split="$(_claude_settings_report "$scratch2/.claude/settings.json")"
    rm -rf "$scratch" "$scratch2"
    if [[ "$padded" == 'mine|null|{"ANTHROPIC_AUTH_TOKEN": "test-omni-key", "ANTHROPIC_BASE_URL": "http://127.0.0.1:20128"}' \
          && "$split" == "mine|null|no-env" ]]; then pass
    else fail "padded=[$padded] split=[$split]"; fi
fi

if it "claude_gateway_routing is asked by claude-code and omniroute on every platform, default login"; then
    bad="$(python3 - "$ROOT/catalog" 2>&1 <<'PY'
import json, pathlib, sys
problems = []
for name in ("linux", "macos", "windows"):
    cat = json.loads((pathlib.Path(sys.argv[1]) / f"{name}.json").read_text(encoding="utf-8"))
    spec = cat.get("prompts", {}).get("claude_gateway_routing")
    if not spec:
        problems.append(f"{name}: no claude_gateway_routing prompt"); continue
    if spec.get("default") != "login":
        problems.append(f"{name}: default is {spec.get('default')!r}, not 'login'")
    if not spec.get("question") or not spec.get("help"):
        problems.append(f"{name}: prompt needs question and help")
    comps = {c["id"]: c for g in cat["categories"] for c in g["components"]}
    for cid in ("claude-code", "omniroute"):
        keys = str(comps.get(cid, {}).get("prompt") or "").replace(",", " ").split()
        if "claude_gateway_routing" not in keys:
            problems.append(f"{name}: {cid} does not ask claude_gateway_routing")
print("; ".join(problems))
PY
)"
    assert_eq "$bad" ""
fi

if it "install_qodercli announces in dry run and writes nothing"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_qodercli ) 2>&1)"
    if [[ "$out" == *"would download and run the Qoder CLI installer"* ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
fi

if it "install_devin_cli announces in dry run and writes nothing"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_devin_cli ) 2>&1)"
    if [[ "$out" == *"would download and run the Devin CLI installer"* ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
fi

# Antigravity on Linux is the HUB (Antigravity 2.x, an Electron app), not the IDE
# (operator decision 2026-09-26). Windows keeps winget Google.Antigravity, which
# already is the Hub. The newest version is DISCOVERED at install time from the
# winget-pkgs manifests on GitHub (no pin); the Linux tarball hangs off the same
# <version>-<build> segment the manifest's windows-x64 URL carries. Facts measured
# 2026-09-26 that these fixtures mirror: the contents API lists 28 version
# directories in STRING order (2.17.0 sorts before 2.4.2), the tarball has NO
# published sha256 (only a crc32c header), every member sits under
# Antigravity-x64/, the ELF binary is Antigravity-x64/antigravity, chrome-sandbox
# is a plain 755 file and app.asar carries package.json and icon.png.
#
# Every test runs against a scratch tree with a curl stub that serves the listing,
# the manifest and a tiny fake tarball with the real layout, and that RECORDS the
# argv and stdin of every call. None reaches the network, the real HOME or sudo
# (AGENTS.md section 5).
AG_LISTING_URL="https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/g/Google/Antigravity"
AG_MANIFEST_BASE="https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/g/Google/Antigravity"
AG_BUCKET="https://storage.googleapis.com/antigravity-public/antigravity-hub"
AG_MARKER="autoos-antigravity-hub"
AG_VA="2.17.0"; AG_IDA="2.17.0-5217732355031040"
AG_VB="2.18.0"; AG_IDB="2.18.0-5300000000000001"
AG_RESET=1790000000

# antigravity_scratch: prints a fresh scratch dir (home/ tmp/ serve/ manifest/ oldbin/).
antigravity_scratch() {
    local sb; sb="$(mktemp -d)"
    mkdir -p "$sb/home" "$sb/tmp" "$sb/serve" "$sb/manifest" "$sb/oldbin"
    printf '%s\n' "$sb"
}

# antigravity_listing <scratch> [name[:type] ...]: the contents-API answer, in the
# string order GitHub returns (type defaults to dir).
antigravity_listing() {
    local sb="$1" e out="[" sep=""; shift
    for e in "$@"; do
        [[ "$e" == *:* ]] || e="$e:dir"
        out+="$sep{\"name\":\"${e%%:*}\",\"type\":\"${e#*:}\"}"; sep=","
    done
    printf '%s]\n' "$out" >"$sb/listing.json"
}

# antigravity_manifest <scratch> <version> <build-id>: the raw installer manifest,
# in the shape measured on 2026-09-26 (an x64 and an arm64 Windows installer).
antigravity_manifest() {
    local sb="$1" ver="$2" id="$3"
    cat >"$sb/manifest/$ver.yaml" <<EOF
PackageIdentifier: Google.Antigravity
PackageVersion: $ver
InstallerType: nullsoft
Protocols:
- antigravity
Installers:
- Architecture: x64
  InstallerUrl: $AG_BUCKET/$id/windows-x64/Antigravity-x64.exe
  InstallerSha256: 0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF
- Architecture: arm64
  InstallerUrl: $AG_BUCKET/$id/windows-arm64/Antigravity-arm64.exe
  InstallerSha256: FEDCBA9876543210FEDCBA9876543210FEDCBA9876543210FEDCBA9876543210
ManifestType: installer
ManifestVersion: 1.6.0
EOF
}

# antigravity_build <scratch> <build-id> [variant]
# serve/<build-id>.tar.gz: a tiny tarball with the real layout. variants: garbage
# (not a tarball), corrupt (a damaged gzip stream), dotdot, absolute, symlink,
# hardlink, fifo, setuid, setgid, wrongtop, nosandbox, sandboxlink, noasar, asarver,
# noelf, noicon, nestedicon (icon.png under resources/ inside the asar), unpackedicon
# (the icon lives in resources/app.asar.unpacked/), dotdoticon (the asar names the icon
# "../icon.png", unpacked, and a decoy PNG sits at resources/icon.png), size and
# sizehead (see antigravity_serve). Sets AG_SIZE and AG_SHA (the tarball's size and sha256).
antigravity_build() {
    local sb="$1" id="$2" variant="${3:-}" tree top asarver="${2%%-*}" out
    local -a icon_arg=()
    tree="$sb/pkg/$id"; top="$tree/Antigravity-x64"; out="$sb/serve/$id.tar.gz"
    rm -rf "$tree"; mkdir -p "$top/resources" "$top/locales"
    printf '\177ELF\002\001\001\000fake electron binary %s\n' "$id" >"$top/antigravity"
    [[ "$variant" != noelf ]] || printf '#!/bin/sh\necho not an ELF\n' >"$top/antigravity"
    printf 'sandbox %s\n' "$id" >"$top/chrome-sandbox"
    printf 'pak\n' >"$top/locales/en-US.pak"
    chmod 755 "$top/antigravity" "$top/chrome-sandbox"
    [[ "$variant" != asarver ]] || asarver="0.0.1"
    case "$variant" in noicon|nestedicon|unpackedicon|dotdoticon) icon_arg=("$variant") ;; esac
    python3 "$ROOT/tests/helpers/fake_antigravity_asar.py" "$top/resources/app.asar" "$asarver" "${icon_arg[@]}"
    case "$variant" in
        nosandbox)   rm -f "$top/chrome-sandbox" ;;
        sandboxlink) rm -f "$top/chrome-sandbox"; ln -s /usr/bin/true "$top/chrome-sandbox" ;;
        noasar)      rm -f "$top/resources/app.asar" ;;
        setuid)      chmod 4755 "$top/chrome-sandbox" ;;
        setgid)      chmod 2755 "$top/antigravity" ;;
        symlink)     ln -s /etc/passwd "$top/resources/link" ;;
        hardlink)    printf 'pak\n' >"$top/locales/en-GB.pak"; ln -f "$top/locales/en-GB.pak" "$top/locales/en-AU.pak" ;;
        fifo)        mkfifo "$top/resources/pipe" ;;
        dotdot|absolute) printf 'evil\n' >"$top/extra" ;;
        unpackedicon)    mkdir -p "$top/resources/app.asar.unpacked"; printf '\211PNG\r\n\032\nunpacked icon\n' >"$top/resources/app.asar.unpacked/icon.png" ;;
        dotdoticon)      mkdir -p "$top/resources/app.asar.unpacked"; printf '\211PNG\r\n\032\nOUTSIDE the unpacked dir\n' >"$top/resources/icon.png" ;;
    esac
    case "$variant" in
        garbage)  head -c 2000 /dev/zero >"$out" ;;
        dotdot)   tar -czf "$out" --transform 's,^Antigravity-x64/extra$,../extra,' -C "$tree" Antigravity-x64 2>/dev/null ;;
        absolute) tar -czf "$out" --transform 's,^Antigravity-x64/extra$,/tmp/autoos-extra,' -C "$tree" Antigravity-x64 2>/dev/null ;;
        wrongtop) tar -czf "$out" --transform 's,^Antigravity-x64,Antigravity,' -C "$tree" Antigravity-x64 ;;
        *)        tar -czf "$out" -C "$tree" Antigravity-x64 ;;
    esac
    if [[ "$variant" == corrupt ]]; then
        printf '\377\377\377\377\377\377\377\377' | dd of="$out" bs=1 seek=48 conv=notrunc 2>/dev/null
        ! gzip -t "$out" 2>/dev/null || echo "fixture: the corrupt tarball still passes gzip -t" >&2
    fi
    AG_SIZE="$(wc -c <"$out" | tr -d ' ')"
    AG_SHA="$(sha256sum "$out" | awk '{print $1}')"
}

# antigravity_serve <scratch> <version> <build-id> [variant]: publishes that version
# (its manifest, its tarball) and lists it next to three older ones, in string order.
# Calling it again for a newer version leaves the older one in the listing.
antigravity_serve() {
    local sb="$1" ver="$2" id="$3" variant="${4:-}" v
    printf '%s\n' "$ver" >>"$sb/versions"
    antigravity_build "$sb" "$id" "$variant"
    antigravity_manifest "$sb" "$ver" "$id"
    case "$variant" in
        size)     printf '%s' "$((AG_SIZE + 7))" >"$sb/get.size" ;;
        sizehead) printf '%s' "$((AG_SIZE + 7))" >"$sb/head.size" ;;
    esac
    local -a names=()
    while IFS= read -r v; do names+=("$v"); done < <({ printf '%s\n' 2.4.2 2.4.3 2.9.1; cat "$sb/versions"; } | LC_ALL=C sort -u)
    antigravity_listing "$sb" "${names[@]}"
}

# antigravity_run <scratch> [VAR=value ...]
# One run in its own subshell (fresh globals, like a fresh process). The entry is
# install_component script antigravity (what setup.sh calls) unless AG_ENTRY=direct
# (install_antigravity itself, past the is_installed gate - which is where --update
# lands) or AG_ENTRY=latest (antigravity_hub_latest alone). Sets AG_OUT, AG_STATE
# (installed|skipped|failed) and AG_RC. Everything that could touch the machine is
# a stub that logs to <scratch>/calls.log; VAR=value arguments are exported into
# the subshell (AUTOOS_DRY_RUN=1, AUTOOS_UPDATE=1, GITHUB_TOKEN=..., AG_OLD_APT=1,
# AG_SYSCTL=restricted|clone0|unreadable|allowed, AG_HOME=<other home>, ...).
antigravity_run() {
    local sb="$1"; shift
    AG_OUT="$(
        (
            export TMPDIR="$sb/tmp"
            CATALOG_PATH="$ROOT/catalog/linux.json"
            AUTOOS_DRY_RUN=0; AUTOOS_UPDATE=0; AUTOOS_SUDO=sudo_rec; AG_ENTRY=component
            AUTOOS_ANTIGRAVITY_MIN_BYTES=100; SYS_IS_ROOT=0
            unset GITHUB_TOKEN GH_TOKEN
            for kv in "$@"; do export "${kv?}"; done
            # AG_TRAPS: clear (default) - this subshell must not replay the harness's EXIT trap;
            # own - a caller trap set in the very shell that runs the install; inherited -
            # the caller's traps stay as this subshell got them (the traps test wraps the
            # call in a shell of its own that has one)
            case "${AG_TRAPS:-clear}" in
                clear)     trap - EXIT INT TERM ;;
                own)       trap - EXIT INT TERM; trap 'echo CALLER_EXIT' EXIT ;;
                inherited) ;;
            esac
            HOME="${AG_HOME:-$sb/home}"; SYS_HOME="$HOME"; export HOME SYS_HOME
            XDG_CONFIG_HOME="$HOME/.config"; export XDG_CONFIG_HOME
            PATH="${AG_PATH_FIRST:+$AG_PATH_FIRST:}$HOME/.local/bin:$PATH"; export PATH
            log="$sb/calls.log"
            # reply <status> <file|->: what one curl answer looks like - the -D file,
            # the -o file, and curl's exit 22 for --fail on an error status.
            reply() {
                local status="$1" file="$2"
                if [[ -n "$hdr" ]]; then
                    { printf 'HTTP/2 %s\r\n' "$status"; sed 's/$/\r/' "$extra"; printf '\r\n'; } >"$hdr"
                fi
                if (( fail && status >= 400 )); then return 22; fi
                if (( ! head )) && [[ -n "$body" && "$file" != - ]]; then cp -- "$file" "$body"; fi
                return 0
            }
            curl() {
                local a prev="" url="" hdr="" body="" cfg=0 head=0 fail=0 maxsize="" n extra size ver id file st
                n="$(( $(cat "$sb/ncalls" 2>/dev/null || echo 0) + 1 ))"; printf '%s' "$n" >"$sb/ncalls"
                printf 'curl %s\n' "$*" >>"$log"
                for a in "$@"; do
                    case "$prev" in -D) hdr="$a" ;; -o) body="$a" ;; --max-filesize) maxsize="$a" ;; --config) [[ "$a" != - ]] || cfg=1 ;; esac
                    case "$a" in --head) head=1 ;; --fail) fail=1 ;; esac
                    prev="$a"; url="$a"
                done
                if (( cfg )); then { printf '=== call %s %s\n' "$n" "$url"; cat; } >>"$sb/stdin.log"; fi
                [[ -z "$body" || "$body" == /dev/null ]] || stat -c %a "$(dirname "$body")" >>"$sb/stagemode.log"
                [[ ! -e "$sb/offline" ]] || return 6
                extra="$sb/extra.$n"; : >"$extra"
                case "$url" in
                    "$AG_LISTING_URL")
                        [[ ! -e "$sb/listing.offline" ]] || return 6
                        cp "$sb/listing.headers" "$extra" 2>/dev/null || true
                        reply "$(cat "$sb/listing.status" 2>/dev/null || echo 200)" "$sb/listing.json" ;;
                    "$AG_MANIFEST_BASE"/*/Google.Antigravity.installer.yaml)
                        ver="${url#"$AG_MANIFEST_BASE"/}"; ver="${ver%%/*}"
                        if [[ -f "$sb/manifest/$ver.yaml" ]]; then reply 200 "$sb/manifest/$ver.yaml"; else reply 404 -; fi ;;
                    "$AG_BUCKET"/*/linux-x64/Antigravity.tar.gz)
                        id="${url#"$AG_BUCKET"/}"; id="${id%%/*}"; file="$sb/serve/$id.tar.gz"
                        if [[ ! -f "$file" ]]; then reply 404 -; return; fi
                        size="$(wc -c <"$file" | tr -d ' ')"; st=200
                        if (( head )); then
                            [[ ! -f "$sb/head.size" ]] || size="$(<"$sb/head.size")"
                            [[ ! -f "$sb/head.status" ]] || st="$(<"$sb/head.status")"
                        else
                            [[ ! -f "$sb/get.size" ]] || size="$(<"$sb/get.size")"
                            [[ ! -f "$sb/get.status" ]] || st="$(<"$sb/get.status")"
                            # a file at the OLD predictable desktop temp name (.antigravity.desktop.<pid>)
                            [[ ! -e "$sb/plant-desktop-tmp" ]] || { mkdir -p "$HOME/.local/share/applications"; printf 'victim\n' >"$HOME/.local/share/applications/.antigravity.desktop.$BASHPID"; }
                            [[ ! -e "$sb/kill-on-download" ]] || kill -TERM "$BASHPID"
                            [[ ! -e "$sb/exit-on-download" ]] || exit 5
                            # like curl: a body announced as bigger than --max-filesize is refused
                            # (exit 63) after its headers, before any of it is written
                            if [[ -n "$maxsize" ]] && (( size > maxsize )); then
                                printf 'content-length: %s\n' "$size" >"$extra"; head=1; reply 200 -; return 63
                            fi
                        fi
                        printf 'content-length: %s\ncontent-type: application/x-tar\nx-goog-hash: crc32c=AAAAAA==\n' "$size" >"$extra"
                        reply "$st" "$file" ;;
                    *) printf 'unexpected URL %s\n' "$url" >>"$sb/unexpected.log"; return 22 ;;
                esac
            }
            # run() executes through python3 in real life, which no function stub can
            # see; here it logs and runs the stub function by name.
            run() { printf 'run %s\n' "$*" >>"$log"; "$@"; }
            sudo() { printf 'sudo %s\n' "$*" >>"$log"; }
            sudo_rec() { printf 'sudo %s\n' "$*" >>"$log"; }
            chown() { printf 'chown %s\n' "$*" >>"$log"; }
            # AG_MV_FAIL_DESKTOP=1: the final rename of the desktop entry's temp file fails
            mv() { if [[ "${AG_MV_FAIL_DESKTOP:-0}" == 1 && "$*" == *.antigravity.desktop.* ]]; then return 1; fi; command mv "$@"; }
            update-desktop-database() { printf 'update-desktop-database %s\n' "$*" >>"$log"; }
            xdg-mime() {
                printf 'xdg-mime %s\n' "$*" >>"$log"
                if [[ "$1 $2" == "query default" ]]; then printf '%s\n' "${AG_MIME_DEFAULT:-}"; fi
                return 0
            }
            dpkg() { [[ "${AG_OLD_APT:-0}" == 1 && "$1" == -s && "$2" == antigravity ]]; }
            sysctl() {
                case "$2:${AG_SYSCTL:-allowed}" in
                    kernel.apparmor_restrict_unprivileged_userns:restricted) echo 1 ;;
                    kernel.apparmor_restrict_unprivileged_userns:allowed) echo 0 ;;
                    kernel.unprivileged_userns_clone:clone0) echo 0 ;;
                    kernel.unprivileged_userns_clone:allowed|kernel.unprivileged_userns_clone:restricted) echo 1 ;;
                    *) return 1 ;;
                esac
            }
            getent() {
                if [[ "$1 $2" == "passwd root" ]]; then printf 'root:x:0:0:root:%s:/bin/sh\n' "${AG_ROOT_HOME:-/root}"
                else command getent "$@"; fi
            }
            _extra_bin_dirs() { printf '%s\n' "$SYS_HOME/.local/bin"; }
            if [[ -n "${AG_AFTER_STAGE:-}" ]]; then
                # AG_AFTER_STAGE=term|exit: a SIGTERM or an exit right after the staging
                # directory exists, before the first request or check runs.
                eval "ag_orig_$(declare -f antigravity_stage_make)"
                antigravity_stage_make() {
                    ag_orig_antigravity_stage_make "$@" || return
                    case "$AG_AFTER_STAGE" in
                        term) kill -TERM "$BASHPID" ;;
                        exit) exit 5 ;;
                    esac
                }
            fi
            rc=0
            case "$AG_ENTRY" in
                latest)
                    antigravity_hub_latest "$sb/tmp" || rc=$?
                    printf 'AGLATEST %s %s %s\n' "${ANTIGRAVITY_VERSION:-}" "${ANTIGRAVITY_ID:-}" "${ANTIGRAVITY_URL:-}"
                    st=installed; (( rc == 0 )) || st=failed ;;
                direct)
                    INSTALL_SCRIPT_STATE=""
                    install_antigravity || rc=$?
                    st="${INSTALL_SCRIPT_STATE:-installed}"; (( rc == 0 )) || st=failed ;;
                *)
                    install_component script antigravity 0 || rc=$?
                    st="$INSTALL_STATE" ;;
            esac
            printf 'AGRESULT %s %s\n' "$st" "$rc"
            [[ "${AG_TRAPS:-clear}" != own ]] || printf 'AGTRAPS %s\n' "$(trap -p EXIT | tr '\n' ' ')"
        ) 2>&1
    )"
    AG_STATE="$(sed -n 's/^AGRESULT \([a-z]*\) [0-9]*$/\1/p' <<<"$AG_OUT")"
    AG_RC="$(sed -n 's/^AGRESULT [a-z]* \([0-9]*\)$/\1/p' <<<"$AG_OUT")"
}

# antigravity_count <scratch> <ERE>: how many recorded calls match (0 when none).
antigravity_count() {
    local n
    n="$(grep -cE -- "$2" "$1/calls.log" 2>/dev/null || true)"
    printf '%s\n' "${n:-0}"
}

# antigravity_tree_state <scratch> [home]: every file (sha256 and mode), symlink
# (target) and directory under the home - equal before and after means "nothing
# was written, nothing was left behind" (a stage or .old directory shows up).
antigravity_tree_state() {
    (
        cd "${2:-$1/home}" || exit 1
        find . -type f -exec sha256sum {} +
        find . -type f -printf 'mode %p %m\n'
        find . -type l -printf 'link %p -> %l\n'
        find . -type d -printf 'dir %p\n'
    ) | LC_ALL=C sort
}

# antigravity_debris <scratch> [home]: leftovers of an install attempt.
antigravity_debris() {
    find "${2:-$1/home}" \( -name '.antigravity-stage.*' -o -name 'antigravity.old-*' -o -name 'antigravity.new' \
        -o -name '*.part' -o -name '.antigravity.desktop.*' -o -name 'pkg.tgz' \) -print 2>/dev/null
}

# antigravity_problems <scratch> <build-id> [home]: one line per thing that is not as
# a finished install of that build must leave it; empty when all is right.
antigravity_problems() {
    local sb="$1" id="$2" home="${3:-$1/home}" dir link dt magic want_size want_sha
    dir="$home/.local/opt/antigravity"; link="$home/.local/bin/antigravity"
    dt="$home/.local/share/applications/antigravity.desktop"
    want_size="$(wc -c <"$sb/serve/$id.tar.gz" | tr -d ' ')"; want_sha="$(sha256sum "$sb/serve/$id.tar.gz" | awk '{print $1}')"
    magic="$(head -c4 "$dir/antigravity" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
    [[ "$magic" == 7f454c46 ]] || printf 'antigravity in %s is not the ELF binary (magic [%s])\n' "$dir" "$magic"
    [[ -f "$dir/chrome-sandbox" && ! -L "$dir/chrome-sandbox" ]] || printf 'chrome-sandbox is missing or a link\n'
    [[ -f "$dir/resources/app.asar" ]] || printf 'resources/app.asar is missing (top dir not stripped?)\n'
    [[ "$(sed -n 1p "$dir/.autoos-version" 2>/dev/null)" == "$AG_MARKER" ]] || printf 'stamp line 1 is [%s]\n' "$(sed -n 1p "$dir/.autoos-version" 2>&1)"
    [[ "$(sed -n 2p "$dir/.autoos-version" 2>/dev/null)" == "$id" ]] || printf 'stamp line 2 is [%s], not %s\n' "$(sed -n 2p "$dir/.autoos-version" 2>&1)" "$id"
    [[ "$(sed -n 3p "$dir/.autoos-version" 2>/dev/null)" == "size=$want_size" ]] || printf 'stamp line 3 is [%s], not size=%s\n' "$(sed -n 3p "$dir/.autoos-version" 2>&1)" "$want_size"
    [[ "$(sed -n 4p "$dir/.autoos-version" 2>/dev/null)" == "sha256=$want_sha" ]] || printf 'stamp line 4 is [%s], not sha256=%s\n' "$(sed -n 4p "$dir/.autoos-version" 2>&1)" "$want_sha"
    [[ "$(readlink "$link" 2>/dev/null)" == "$dir/antigravity" ]] || printf 'command link is [%s], not %s\n' "$(readlink "$link" 2>&1)" "$dir/antigravity"
    grep -qxF 'Type=Application' "$dt" 2>/dev/null || printf 'desktop entry has no Type=Application\n'
    grep -qxF 'Name=Antigravity' "$dt" 2>/dev/null || printf 'desktop entry has no Name=Antigravity\n'
    grep -qxF 'Terminal=false' "$dt" 2>/dev/null || printf 'desktop entry has no Terminal=false\n'
    grep -qxF 'Categories=Development;IDE;' "$dt" 2>/dev/null || printf 'desktop entry has no Categories\n'
    grep -qxF 'StartupWMClass=Antigravity' "$dt" 2>/dev/null || printf 'desktop entry has no StartupWMClass\n'
    grep -qxF 'MimeType=x-scheme-handler/antigravity;' "$dt" 2>/dev/null || printf 'desktop entry has no scheme handler\n'
    grep -qxF "Exec=\"$dir/antigravity\" %U" "$dt" 2>/dev/null || printf 'desktop Exec line wrong: %s\n' "$(grep '^Exec' "$dt" 2>&1)"
    grep -qxF "Icon=$dir/icon.png" "$dt" 2>/dev/null || printf 'desktop Icon line wrong: %s\n' "$(grep '^Icon' "$dt" 2>&1)"
    [[ -f "$dir/icon.png" ]] || printf '%s/icon.png was not extracted from app.asar\n' "$dir"
    [[ -z "$(antigravity_debris "$sb" "$home")" ]] || printf 'left behind: %s\n' "$(antigravity_debris "$sb" "$home" | tr '\n' ' ')"
}

if it "antigravity fresh install: verified and installed into ~/.local/opt/antigravity with stamp, command link and desktop entry, and no sudo, chown or chmod"; then
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb"
    ok=1
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:900}" >&2; }
    probs="$(antigravity_problems "$sb" "$AG_IDA")"
    [[ -z "$probs" ]] || { ok=0; echo "$probs" >&2; }
    [[ "$(antigravity_count "$sb" '^(sudo|chown|chmod|run (sudo|chown|chmod)) ')" == 0 ]] \
        || { ok=0; echo "the installer privileged or re-moded something: $(grep -E '^(sudo|chown|chmod|run (sudo|chown|chmod)) ' "$sb/calls.log")" >&2; }
    [[ "$(stat -c %A "$sb/home/.local/opt/antigravity/chrome-sandbox" 2>/dev/null)" =~ ^-rwxr.xr.x$ ]] \
        || { ok=0; echo "chrome-sandbox is [$(stat -c %A "$sb/home/.local/opt/antigravity/chrome-sandbox" 2>&1)]: not a plain executable file, or setuid" >&2; }
    [[ "$AG_OUT" == *"no published sha256"* ]] || { ok=0; echo "the output does not say plainly that there is no published sha256: ${AG_OUT:0:600}" >&2; }
    [[ "$AG_OUT" != *"--no-sandbox"* ]] || { ok=0; echo "the output mentions --no-sandbox" >&2; }
    [[ "$AG_OUT" != *"apt-get remove"* ]] || { ok=0; echo "warned about an old apt package that is not installed" >&2; }
    [[ "$(antigravity_count "$sb" '^update-desktop-database ')" == 1 ]] \
        || { ok=0; echo "update-desktop-database was not run once: [$(grep '^update' "$sb/calls.log" 2>&1)]" >&2; }
    [[ -s "$sb/unexpected.log" ]] && { ok=0; echo "unexpected URLs: $(cat "$sb/unexpected.log")" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the Antigravity Hub install is not what the operator decision on 2026-09-26 describes"; fi
fi

if it "antigravity icon: taken from app.asar wherever icon.png sits; without one the desktop entry names the themed icon and the install stands"; then
    ok=1
    for variant in nestedicon noicon; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA" "$variant"
        antigravity_run "$sb"
        dir="$sb/home/.local/opt/antigravity"; dt="$sb/home/.local/share/applications/antigravity.desktop"
        [[ "$AG_STATE" == installed ]] || { ok=0; echo "$variant: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
        if [[ "$variant" == nestedicon ]]; then
            [[ "$(head -c4 "$dir/icon.png" 2>/dev/null | od -An -tx1 | tr -d ' \n')" == 89504e47 ]] || { ok=0; echo "$variant: icon.png was not extracted as a PNG" >&2; }
            grep -qxF "Icon=$dir/icon.png" "$dt" || { ok=0; echo "$variant: Icon line is [$(grep '^Icon' "$dt" 2>&1)]" >&2; }
        else
            [[ ! -e "$dir/icon.png" ]] || { ok=0; echo "$variant: an icon.png appeared from nowhere" >&2; }
            grep -qxF 'Icon=antigravity' "$dt" || { ok=0; echo "$variant: Icon line is [$(grep '^Icon' "$dt" 2>&1)], not the themed fallback" >&2; }
        fi
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "the desktop entry's icon is not taken from app.asar, or has no fallback"; fi
fi

if it "antigravity icon: an unpacked icon is read from app.asar.unpacked, but an asar key with a '..' component is never joined onto it (no icon is taken from outside)"; then
    ok=1
    for variant in unpackedicon dotdoticon; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA" "$variant"
        antigravity_run "$sb"
        dir="$sb/home/.local/opt/antigravity"; dt="$sb/home/.local/share/applications/antigravity.desktop"
        [[ "$AG_STATE" == installed ]] || { ok=0; echo "$variant: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
        if [[ "$variant" == unpackedicon ]]; then
            grep -q 'unpacked icon' "$dir/icon.png" 2>/dev/null || { ok=0; echo "$variant: the icon in app.asar.unpacked was not used" >&2; }
            grep -qxF "Icon=$dir/icon.png" "$dt" || { ok=0; echo "$variant: Icon line is [$(grep '^Icon' "$dt" 2>&1)]" >&2; }
        else
            [[ ! -e "$dir/icon.png" ]] || { ok=0; echo "$variant: an icon.png was taken from outside app.asar.unpacked through a '..' key: $(head -c 60 "$dir/icon.png" | tr -c '[:print:]' '.')" >&2; }
            grep -qxF 'Icon=antigravity' "$dt" || { ok=0; echo "$variant: Icon line is [$(grep '^Icon' "$dt" 2>&1)], not the themed fallback" >&2; }
            [[ "$AG_OUT" == *"no icon extracted"* ]] || { ok=0; echo "$variant: the refusal is not reported: ${AG_OUT:0:500}" >&2; }
        fi
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "an asar key can steer the icon lookup out of app.asar.unpacked"; fi
fi

if it "antigravity sandbox: the SUID commands are printed once, verbatim, only when this kernel restricts user namespaces; the installer never runs them"; then
    ok=1
    for mode in restricted clone0 unreadable allowed; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        antigravity_run "$sb" "AG_SYSCTL=$mode"
        dir="$sb/home/.local/opt/antigravity"
        want="sudo chown root:root '$dir/chrome-sandbox' && sudo chmod 4755 '$dir/chrome-sandbox'"
        [[ "$AG_STATE" == installed ]] || { ok=0; echo "$mode: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:500}" >&2; }
        n="$(grep -cF -- "$want" <<<"$AG_OUT")"
        if [[ "$mode" == allowed ]]; then
            [[ "$n" == 0 && "$AG_OUT" != *"sudo chown"* ]] || { ok=0; echo "$mode: the SUID commands were printed although nothing is needed" >&2; }
            [[ "$AG_OUT" == *"nothing is required"* ]] || { ok=0; echo "$mode: it does not say that nothing is required: ${AG_OUT:0:500}" >&2; }
        else
            [[ "$n" == 1 ]] || { ok=0; echo "$mode: the verbatim command line appeared $n times, not once: ${AG_OUT:0:900}" >&2; }
        fi
        [[ "$(antigravity_count "$sb" '(^|run )(sudo|chown|chmod)')" == 0 ]] || { ok=0; echo "$mode: a privileged call was made" >&2; }
        [[ "$AG_OUT" != *"--no-sandbox"* ]] || { ok=0; echo "$mode: suggests --no-sandbox" >&2; }
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "the sandbox step is printed when it is not needed, missing when it is, or run by the installer"; fi
fi

if it "antigravity discovery: the newest version is the numeric maximum (2.17.0 beats 2.9.1 and 2.4.3) and the Linux URL is built only from the constant and the build id"; then
    ok=1; sb="$(antigravity_scratch)"
    # string order, as GitHub returns it; a file, a non-version dir and a pre-release are not candidates
    antigravity_listing "$sb" 2.10.0:file 2.17.0 2.18.0-beta 2.4.2 2.4.3 2.9.1 3.0.0:file latest v2.20 README.md:file
    antigravity_manifest "$sb" "$AG_VA" "$AG_IDA"
    # decoys the manifest must not be able to steer the URL with
    cat >>"$sb/manifest/$AG_VA.yaml" <<EOF
  InstallerUrl: https://evil.example/antigravity-public/antigravity-hub/2.17.0-999/windows-x64/Antigravity-x64.exe
  InstallerUrl: $AG_BUCKET/2.17.0-999/windows-arm64/Antigravity-arm64.exe
ReleaseNotesUrl: $AG_BUCKET/2.17.0-999/windows-x64/notes.txt/extra
EOF
    antigravity_run "$sb" AG_ENTRY=latest
    want="AGLATEST $AG_VA $AG_IDA $AG_BUCKET/$AG_IDA/linux-x64/Antigravity.tar.gz"
    [[ "$AG_OUT" == *"$want"* && "$AG_RC" == 0 ]] || { ok=0; echo "expected [$want], got: ${AG_OUT:0:700}" >&2; }
    grep -qF "$AG_MANIFEST_BASE/$AG_VA/Google.Antigravity.installer.yaml" "$sb/calls.log" || { ok=0; echo "the manifest of $AG_VA was not read: $(grep '^curl' "$sb/calls.log")" >&2; }
    [[ "$(antigravity_count "$sb" '^curl ')" == 2 ]] || { ok=0; echo "expected the listing and one manifest, got: $(grep '^curl' "$sb/calls.log")" >&2; }
    # two-level numbers: 1.10.0 beats 1.9.10 (string order would say 1.9.10)
    antigravity_listing "$sb" 1.10.0 1.9.10 1.9.9
    antigravity_manifest "$sb" 1.10.0 1.10.0-77
    antigravity_run "$sb" AG_ENTRY=latest
    [[ "$AG_OUT" == *"AGLATEST 1.10.0 1.10.0-77 $AG_BUCKET/1.10.0-77/linux-x64/Antigravity.tar.gz"* ]] || { ok=0; echo "1.10.0 was not picked over 1.9.10: ${AG_OUT:0:500}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the version was picked by string order, or the tarball URL was built from something the manifest controls"; fi
fi

if it "antigravity discovery: a listing of 1000 or more entries may be truncated (the contents API returns at most 1000 and cannot be paginated), so it fails loudly and picks no version; 999 entries work"; then
    ok=1
    for shape in dirs-999 dirs-1000 dirs-1001 mixed-1000; do
        n="${shape#*-}"; sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        names=()
        if [[ "$shape" == mixed-* ]]; then for (( i = 1; i < n; i++ )); do names+=("notes-$i.md:file"); done
        else for (( i = 1; i < n; i++ )); do names+=("0.0.$i"); done; fi
        names+=("$AG_VA")                       # $n entries in all, 2.17.0 the newest directory
        antigravity_listing "$sb" "${names[@]}"
        antigravity_run "$sb" AG_ENTRY=latest
        if (( n < 1000 )); then
            [[ "$AG_OUT" == *"AGLATEST $AG_VA $AG_IDA $AG_BUCKET/$AG_IDA/linux-x64/Antigravity.tar.gz"* && "$AG_RC" == 0 ]] \
                || { ok=0; echo "$shape: a listing below the limit must work: rc=[$AG_RC] ${AG_OUT:0:400}" >&2; }
        else
            [[ "$AG_RC" != 0 && "$AG_OUT" == *"listing may be truncated; refusing to pick a newest version"* ]] \
                || { ok=0; echo "$shape: rc=[$AG_RC], no loud 'listing may be truncated; refusing to pick a newest version': ${AG_OUT:0:500}" >&2; }
            [[ "$AG_OUT" == *"AGLATEST   "* ]] || { ok=0; echo "$shape: a version was picked from a listing that may be truncated: ${AG_OUT:0:500}" >&2; }
            [[ "$(antigravity_count "$sb" '^curl ')" == 1 ]] || { ok=0; echo "$shape: something was requested after the listing: $(grep '^curl' "$sb/calls.log")" >&2; }
            antigravity_run "$sb" AG_ENTRY=direct
            [[ "$AG_STATE" == failed && "$AG_RC" != 0 && -z "$(find "$sb/home" -type f)" && -z "$(antigravity_debris "$sb")" ]] \
                || { ok=0; echo "$shape: the install must fail and leave nothing: state=[$AG_STATE] rc=[$AG_RC]" >&2; }
        fi
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "a listing that the GitHub API may have cut off is trusted to name the newest version"; fi
fi

if it "antigravity discovery: a rate-limited listing (403 or 429) fails loudly with the reset time, installs nothing and tries no other source"; then
    ok=1
    reset_txt="$(date -u -d "@$AG_RESET" '+%Y-%m-%d %H:%M:%S UTC')"
    for status in 403 429; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        printf '%s' "$status" >"$sb/listing.status"
        printf 'x-ratelimit-limit: 60\nx-ratelimit-remaining: 0\nx-ratelimit-reset: %s\n' "$AG_RESET" >"$sb/listing.headers"
        antigravity_run "$sb" AG_ENTRY=direct
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "$status: state=[$AG_STATE] rc=[$AG_RC] (must fail)" >&2; }
        [[ "$AG_OUT" == *"$reset_txt"* ]] || { ok=0; echo "$status: the reset time [$reset_txt] is not in the message: ${AG_OUT:0:700}" >&2; }
        [[ "$AG_OUT" == *"GITHUB_TOKEN"* && "$AG_OUT" == *"not set"* ]] || { ok=0; echo "$status: no hint about GITHUB_TOKEN (not set): ${AG_OUT:0:700}" >&2; }
        [[ "$AG_OUT" == *"listing"* ]] || { ok=0; echo "$status: the message does not name the step (listing): ${AG_OUT:0:400}" >&2; }
        [[ "$(antigravity_count "$sb" '^curl ')" == 1 && "$(grep '^curl' "$sb/calls.log")" == *"$AG_LISTING_URL" ]] \
            || { ok=0; echo "$status: another source was tried: $(grep '^curl' "$sb/calls.log")" >&2; }
        [[ -z "$(find "$sb/home" -type f)" && -z "$(antigravity_debris "$sb")" ]] || { ok=0; echo "$status: left files: $(find "$sb/home" -type f) $(antigravity_debris "$sb")" >&2; }
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "a rate-limited GitHub API is not a loud, clean failure"; fi
fi

if it "antigravity discovery: a GITHUB_TOKEN reaches only the listing request, through curl's stdin, and never appears on argv, in the output or in the calls log"; then
    ok=1
    tok="ghp_TESTTOKEN0123456789abcdefghijklmnop"
    for var in GITHUB_TOKEN GH_TOKEN; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        antigravity_run "$sb" "$var=$tok"
        [[ "$AG_STATE" == installed ]] || { ok=0; echo "$var: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
        grep -qF "$tok" "$sb/calls.log" && { ok=0; echo "$var: the token is in the recorded argv" >&2; }
        [[ "$AG_OUT" != *"$tok"* ]] || { ok=0; echo "$var: the token is in the output" >&2; }
        grep -qF "$tok" "$sb/stdin.log" 2>/dev/null || { ok=0; echo "$var: the token never reached curl's stdin" >&2; }
        grep -qF "Authorization: Bearer $tok" "$sb/stdin.log" 2>/dev/null || { ok=0; echo "$var: stdin has no bearer header" >&2; }
        [[ "$(grep -c '^=== call' "$sb/stdin.log" 2>/dev/null)" == 1 && "$(grep '^=== call' "$sb/stdin.log")" == *"$AG_LISTING_URL" ]] \
            || { ok=0; echo "$var: the token went somewhere other than the listing: $(grep '^=== call' "$sb/stdin.log")" >&2; }
        grep -E -- '--config[ =]' "$sb/calls.log" | grep -qv -- '--config -' && { ok=0; echo "$var: curl read its config from somewhere but stdin" >&2; }
        rm -rf "$sb"
    done
    # a rate-limited answer with a token set says so, still without printing it
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"; printf '403' >"$sb/listing.status"
    antigravity_run "$sb" AG_ENTRY=direct "GITHUB_TOKEN=$tok"
    [[ "$AG_OUT" != *"$tok"* ]] || { ok=0; echo "403: the token is in the output" >&2; }
    [[ "$AG_STATE" == failed && "$AG_OUT" == *"GITHUB_TOKEN"* && "$AG_OUT" == *" set"* ]] || { ok=0; echo "403 with a token: ${AG_OUT:0:500}" >&2; }
    rm -rf "$sb"
    # GitHub rejecting the token (401) is named as such, still without printing it
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"; printf '401' >"$sb/listing.status"
    antigravity_run "$sb" AG_ENTRY=direct "GITHUB_TOKEN=$tok"
    [[ "$AG_STATE" == failed && "$AG_OUT" == *"HTTP 401"* && "$AG_OUT" == *"GITHUB_TOKEN"* && "$AG_OUT" == *"rejected"* && "$AG_OUT" != *"$tok"* ]] \
        || { ok=0; echo "401 with a token: ${AG_OUT:0:500}" >&2; }
    rm -rf "$sb"
    # a value that could inject curl config lines is never sent, and the output does not repeat it
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" $'GITHUB_TOKEN=abc"\noutput = "/tmp/pwned'
    ! grep -q 'pwned' "$sb/stdin.log" 2>/dev/null || { ok=0; echo "a token with a newline and a quote reached curl's config" >&2; }
    [[ "$AG_STATE" == installed && "$AG_OUT" != *"pwned"* ]] || { ok=0; echo "implausible token: state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the GitHub token can leak, or the discovery ignores it"; fi
fi

if it "antigravity real run(): through process.py the token reaches curl's stdin and never its argv, and a rate-limited answer fails with the reset time"; then
    ok=1; sb="$(antigravity_scratch)"; mkdir -p "$sb/shim"
    # An inert curl on PATH: records argv and stdin, answers "rate limited" through the -D file, touches no network.
    cat >"$sb/shim/curl" <<'SHIM'
#!/bin/sh
printf 'argv %s\n' "$*" >>"$AG_SHIM_LOG"
hdr=""; prev=""; cfg=0
for a in "$@"; do
    [ "$prev" = -D ] && hdr="$a"
    [ "$prev" = --config ] && [ "$a" = - ] && cfg=1
    prev="$a"
done
if [ "$cfg" = 1 ]; then { echo "stdin:"; cat; } >>"$AG_SHIM_LOG"; fi
if [ -n "$hdr" ]; then printf 'HTTP/2 403\r\nx-ratelimit-reset: 1790000000\r\n\r\n' >"$hdr"; fi
exit 0
SHIM
    chmod +x "$sb/shim/curl"
    tok="ghp_REALRUNTOKEN0123456789abcdefghij"
    for mode in with-token no-token; do
        : >"$sb/shim.log"
        out="$( (
            trap - EXIT
            export HOME="$sb/home" SYS_HOME="$sb/home" PATH="$sb/shim:$PATH" AG_SHIM_LOG="$sb/shim.log" AUTOOS_INSTALL_TIMEOUT_SECONDS=60
            unset GITHUB_TOKEN GH_TOKEN
            [[ "$mode" != with-token ]] || export GITHUB_TOKEN="$tok"
            AUTOOS_DRY_RUN=0; SYS_IS_ROOT=0
            install_antigravity; echo "RC $?"
        ) 2>&1 )"
        [[ "$out" == *"RC 1"* ]] || { ok=0; echo "$mode: rc: ${out:0:600}" >&2; }
        [[ "$out" == *"$(date -u -d '@1790000000' '+%Y-%m-%d %H:%M:%S UTC')"* ]] || { ok=0; echo "$mode: the reset time is not in the message: ${out:0:600}" >&2; }
        [[ "$(grep -c '^argv' "$sb/shim.log")" == 1 ]] || { ok=0; echo "$mode: expected exactly the listing request: $(cat "$sb/shim.log")" >&2; }
        [[ "$out" != *"$tok"* ]] && ! grep -q "^argv.*$tok" "$sb/shim.log" || { ok=0; echo "$mode: the token is on argv or in the output" >&2; }
        if [[ "$mode" == with-token ]]; then
            [[ "$(sed -n '/^stdin:/,$p' "$sb/shim.log")" == $'stdin:\nheader = "Authorization: Bearer '"$tok"'"' ]] || { ok=0; echo "with-token: curl's stdin is [$(sed -n '/^stdin:/,$p' "$sb/shim.log")]" >&2; }
            grep -q -- '--config -' "$sb/shim.log" || { ok=0; echo "with-token: curl was not told to read its config from stdin" >&2; }
        else
            ! grep -q '^stdin:' "$sb/shim.log" || { ok=0; echo "no-token: curl was given a config on stdin" >&2; }
        fi
        [[ -z "$(find "$sb/home" -type f)" && -z "$(antigravity_debris "$sb")" ]] || { ok=0; echo "$mode: left $(find "$sb/home" -type f) $(antigravity_debris "$sb")" >&2; }
    done
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the real run() path loses the token pipe, leaks the token, or does not stop on a rate limit"; fi
fi

if it "antigravity discovery: every unusable answer fails loudly naming its step - no fallback to another version or source, nothing installed"; then
    ok=1
    # name | what the message must contain | how to break the scenario
    scenarios=(
        "unreachable-listing|listing|touch \"\$sb/listing.offline\""
        "non-json|listing|printf '<html>rate limited</html>' >\"\$sb/listing.json\""
        "no-versions|listing|antigravity_listing \"\$sb\" latest 2.10.0:file"
        "http-500|listing|printf 500 >\"\$sb/listing.status\""
        "manifest-404|manifest|rm \"\$sb/manifest/$AG_VA.yaml\""
        "manifest-no-x64|manifest|sed -i '/windows-x64/d' \"\$sb/manifest/$AG_VA.yaml\""
        "manifest-foreign-host|manifest|sed -i 's,https://storage.googleapis.com,https://evil.example,' \"\$sb/manifest/$AG_VA.yaml\""
        "manifest-id-of-another-version|manifest|antigravity_manifest \"\$sb\" $AG_VA 2.9.1-5"
        "manifest-two-builds|manifest|printf '  InstallerUrl: $AG_BUCKET/2.17.0-6/windows-x64/Antigravity-x64.exe\n' >>\"\$sb/manifest/$AG_VA.yaml\""
        "manifest-other-version|manifest|sed -i 's/^PackageVersion: .*/PackageVersion: 2.16.0/' \"\$sb/manifest/$AG_VA.yaml\""
        "head-404|Linux build|printf 404 >\"\$sb/head.status\""
        "head-403|Linux build|printf 403 >\"\$sb/head.status\""
        "head-too-small|Linux build|printf 52428799 >\"\$sb/head.size\""
        "head-no-length|Linux build|printf '' >\"\$sb/head.size\""
    )
    for row in "${scenarios[@]}"; do
        name="${row%%|*}"; rest="${row#*|}"; want="${rest%%|*}"; how="${rest#*|}"
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        # the older 2.9.1 must never be the fallback: it has a working manifest and tarball
        antigravity_manifest "$sb" 2.9.1 2.9.1-1; antigravity_build "$sb" 2.9.1-1 >/dev/null
        eval "$how"
        case "$name" in head-too-small|head-no-length) minb=52428800 ;; *) minb=100 ;; esac
        antigravity_run "$sb" AG_ENTRY=direct "AUTOOS_ANTIGRAVITY_MIN_BYTES=$minb"
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "$name: state=[$AG_STATE] rc=[$AG_RC] (must fail)" >&2; }
        [[ "$AG_OUT" == *"$want"* ]] || { ok=0; echo "$name: the message does not name the step [$want]: ${AG_OUT:0:500}" >&2; }
        [[ "$(antigravity_count "$sb" ' -o [^ ]*pkg\.tgz')" == 0 ]] || { ok=0; echo "$name: a download was attempted" >&2; }
        ! grep -q '2\.9\.1' <<<"$(grep -E '(linux-x64|installer\.yaml)' "$sb/calls.log")" || { ok=0; echo "$name: fell back to 2.9.1: $(grep '2.9.1' "$sb/calls.log")" >&2; }
        [[ "$(antigravity_count "$sb" "^curl .*$AG_LISTING_URL")" -le 1 ]] || { ok=0; echo "$name: the listing was requested more than once" >&2; }
        [[ -z "$(find "$sb/home" -type f)" && -z "$(antigravity_debris "$sb")" ]] || { ok=0; echo "$name: left $(find "$sb/home" -type f) $(antigravity_debris "$sb")" >&2; }
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "an unusable winget-pkgs or bucket answer was acted on, or the failure does not name its step"; fi
fi

if it "antigravity discovery: the minimum size defaults to 50 MB, and the HEAD and download requests are pinned to https with no redirects"; then
    ok=1
    [[ "$( ( unset AUTOOS_ANTIGRAVITY_MIN_BYTES; antigravity_min_bytes ) )" == 52428800 ]] || { ok=0; echo "the default minimum is [$( ( unset AUTOOS_ANTIGRAVITY_MIN_BYTES; antigravity_min_bytes ) )], not 52428800" >&2; }
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    for what in '--head' ' -o [^ ]*pkg\.tgz'; do
        line="$(grep -E -- "^curl .*$what" "$sb/calls.log" | grep -F 'Antigravity.tar.gz' | head -1)"
        [[ -n "$line" ]] || { ok=0; echo "no call for [$what]" >&2; continue; }
        for flag in '--fail' '--proto =https' '--proto-redir =https' '--max-redirs 0'; do
            [[ "$line" == *"$flag"* ]] || { ok=0; echo "[$what] call lacks [$flag]: $line" >&2; }
        done
    done
    grep -E -- ' -o [^ ]*pkg\.tgz' "$sb/calls.log" | grep -q -- '--retry 3' || { ok=0; echo "the download does not retry 3 times" >&2; }
    grep -E -- ' -o [^ ]*pkg\.tgz' "$sb/calls.log" | grep -q -- ' -D ' || { ok=0; echo "the download does not record its headers (-D)" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the size bound or the transport hardening is not what the design says"; fi
fi

if it "antigravity download bound: the tarball GET carries --max-filesize (the HEAD length plus 1 MiB), and a server announcing more stops the download with nothing left behind"; then
    ok=1
    # (1) the bound is in the argv, computed from the length the HEAD announced
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    line="$(grep -E -- ' -o [^ ]*pkg\.tgz' "$sb/calls.log")"
    [[ "$line" =~ --max-filesize\ ([0-9]+)($|\ ) ]] || { ok=0; echo "the download has no --max-filesize: $line" >&2; }
    [[ "${BASH_REMATCH[1]:-}" == "$((AG_SIZE + 1048576))" ]] || { ok=0; echo "--max-filesize is [${BASH_REMATCH[1]:-none}], expected $((AG_SIZE + 1048576)) (the $AG_SIZE bytes of the HEAD plus 1 MiB)" >&2; }
    rm -rf "$sb"
    # (2) the number comes from the HEAD, not from the file that happens to be served
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    printf '%s' "$((AG_SIZE + 1000))" >"$sb/head.size"
    antigravity_run "$sb" AG_ENTRY=direct
    line="$(grep -E -- ' -o [^ ]*pkg\.tgz' "$sb/calls.log")"
    [[ "$line" =~ --max-filesize\ ([0-9]+)($|\ ) && "${BASH_REMATCH[1]}" == "$((AG_SIZE + 1000 + 1048576))" ]] \
        || { ok=0; echo "the HEAD said $((AG_SIZE + 1000)) bytes, so --max-filesize must be $((AG_SIZE + 1000 + 1048576)): $line" >&2; }
    rm -rf "$sb"
    # (3) a server that announces more than the bound is stopped by curl: a failed download, nothing left
    for start in fresh update; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        if [[ "$start" == update ]]; then
            antigravity_run "$sb" >/dev/null
            antigravity_serve "$sb" "$AG_VB" "$AG_IDB"
        fi
        printf '%s' "$((AG_SIZE + 1048576 + 1))" >"$sb/get.size"
        before="$(antigravity_tree_state "$sb")"
        antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "$start: state=[$AG_STATE] rc=[$AG_RC] (must fail)" >&2; }
        [[ "$AG_OUT" == *"(download)"* && "$AG_OUT" == *"curl exit 63"* ]] || { ok=0; echo "$start: the failure is not the download being stopped by --max-filesize (curl exit 63): ${AG_OUT:0:500}" >&2; }
        [[ -z "$(antigravity_debris "$sb")" ]] || { ok=0; echo "$start: left behind: $(antigravity_debris "$sb" | tr '\n' ' ')" >&2; }
        if [[ "$start" == update ]]; then
            [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "$start: the existing install or its surroundings changed" >&2; }
        else
            [[ -z "$(find "$sb/home" -type f)" ]] || { ok=0; echo "$start: files were left: $(find "$sb/home" -type f | tr '\n' ' ')" >&2; }
        fi
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "a server that lies about the size can fill the disk before the size check"; fi
fi

if it "antigravity verification failures: each one leaves an existing install untouched and no staging directory behind"; then
    ok=1
    # variant | what the message must contain
    for row in "size|bytes" "sizehead|bytes" "corrupt|gzip" "garbage|gzip" "dotdot|'..'" "absolute|absolute" "symlink|symbolic link" \
               "hardlink|hard link" "fifo|FIFO" "setuid|setuid" "setgid|setuid" "wrongtop|Antigravity-x64/" \
               "nosandbox|chrome-sandbox" "sandboxlink|chrome-sandbox" "noasar|app.asar" "asarver|0.0.1" "noelf|ELF"; do
        variant="${row%%|*}"; want="${row#*|}"
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        antigravity_run "$sb" >/dev/null
        antigravity_serve "$sb" "$AG_VB" "$AG_IDB" "$variant"
        before="$(antigravity_tree_state "$sb")"
        antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "$variant: state=[$AG_STATE] rc=[$AG_RC] (must fail): ${AG_OUT:0:500}" >&2; }
        [[ "$AG_OUT" == *"verification"* && "$AG_OUT" == *"$want"* ]] || { ok=0; echo "$variant: no 'verification' error naming [$want]: ${AG_OUT:0:600}" >&2; }
        [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "$variant: the existing install or its surroundings changed: $(diff <(echo "$before") <(antigravity_tree_state "$sb") | head -5)" >&2; }
        [[ "$(sed -n 2p "$sb/home/.local/opt/antigravity/.autoos-version")" == "$AG_IDA" ]] || { ok=0; echo "$variant: the stamp moved" >&2; }
        [[ -z "$(antigravity_debris "$sb")" ]] || { ok=0; echo "$variant: left $(antigravity_debris "$sb")" >&2; }
        rm -rf "$sb"
    done
    # on a fresh machine the same failures leave nothing at all
    for variant in size corrupt dotdot symlink noelf; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA" "$variant"
        antigravity_run "$sb" AG_ENTRY=direct
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "fresh $variant: state=[$AG_STATE] rc=[$AG_RC]" >&2; }
        [[ -z "$(find "$sb/home" -type f)" && -z "$(antigravity_debris "$sb")" && ! -e "$sb/home/.local/opt/antigravity" ]] \
            || { ok=0; echo "fresh $variant: left $(find "$sb/home" -type f) $(antigravity_debris "$sb")" >&2; }
        [[ "$(antigravity_count "$sb" '^(sudo|chown|chmod) ')" == 0 ]] || { ok=0; echo "fresh $variant: a privileged call was made" >&2; }
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "a tarball that fails verification damaged the machine, was extracted, or left debris"; fi
fi

if it "antigravity ownership: a directory AutoOS did not stamp is refused and left byte-identical; unrelated .new and .antigravity-stage.* directories are never touched"; then
    ok=1
    for kind in plain old-marker symlink-dir symlink-stamp; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        opt="$sb/home/.local/opt"; dir="$opt/antigravity"; mkdir -p "$opt"
        case "$kind" in
            plain)          mkdir -p "$dir"; printf 'user data\n' >"$dir/notes.txt" ;;
            old-marker)     mkdir -p "$dir"; printf 'autoos-antigravity-ide\n2.5.5-1\n' >"$dir/.autoos-version" ;;
            symlink-dir)    mkdir -p "$sb/elsewhere"; printf '%s\n%s\n' "$AG_MARKER" "$AG_IDA" >"$sb/elsewhere/.autoos-version"; ln -s "$sb/elsewhere" "$dir" ;;
            symlink-stamp)  mkdir -p "$dir" "$sb/elsewhere"; printf '%s\n%s\n' "$AG_MARKER" "$AG_IDA" >"$sb/elsewhere/stamp"; ln -s "$sb/elsewhere/stamp" "$dir/.autoos-version" ;;
        esac
        before="$(antigravity_tree_state "$sb")"
        antigravity_run "$sb" AG_ENTRY=direct
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "$kind: state=[$AG_STATE] rc=[$AG_RC] (must refuse)" >&2; }
        [[ "$AG_OUT" == *"not installed by AutoOS"* && "$AG_OUT" == *"$dir"* ]] || { ok=0; echo "$kind: no warning naming $dir: ${AG_OUT:0:500}" >&2; }
        [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "$kind: the directory changed" >&2; }
        [[ "$(antigravity_count "$sb" '^curl ')" == 0 ]] || { ok=0; echo "$kind: the network was asked about a directory that is not ours" >&2; }
        # detection agrees: it is not an installed AutoOS component
        [[ "$(SYS_HOME="$sb/home" script_is_installed antigravity && echo installed || echo not-installed)" == not-installed ]] \
            || { ok=0; echo "$kind: detection calls it installed" >&2; }
        rm -rf "$sb"
    done
    # the user's own look-alike directories survive a fresh install, a failed one and an update
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    opt="$sb/home/.local/opt"; mkdir -p "$opt/antigravity.new" "$opt/.antigravity-stage.userdata" "$opt/antigravity.old-keep"
    printf 'mine 1\n' >"$opt/antigravity.new/keep"; printf 'mine 2\n' >"$opt/.antigravity-stage.userdata/pkg.tgz"; printf 'mine 3\n' >"$opt/antigravity.old-keep/keep"
    mine_before="$(antigravity_tree_state "$sb" "$opt" | grep -E 'antigravity\.new|antigravity-stage\.userdata|antigravity\.old-keep')"
    antigravity_run "$sb"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "fresh with look-alikes: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
    [[ "$(antigravity_tree_state "$sb" "$opt" | grep -E 'antigravity\.new|antigravity-stage\.userdata|antigravity\.old-keep')" == "$mine_before" ]] || { ok=0; echo "fresh: a look-alike directory was touched" >&2; }
    antigravity_serve "$sb" "$AG_VB" "$AG_IDB" corrupt
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
    [[ "$AG_STATE" == failed ]] || { ok=0; echo "corrupt update: state=[$AG_STATE]" >&2; }
    [[ "$(antigravity_tree_state "$sb" "$opt" | grep -E 'antigravity\.new|antigravity-stage\.userdata|antigravity\.old-keep')" == "$mine_before" ]] || { ok=0; echo "failed update: a look-alike directory was touched" >&2; }
    antigravity_serve "$sb" "$AG_VB" "$AG_IDB"
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "update: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
    [[ "$(antigravity_tree_state "$sb" "$opt" | grep -E 'antigravity\.new|antigravity-stage\.userdata|antigravity\.old-keep')" == "$mine_before" ]] || { ok=0; echo "update: a look-alike directory was touched" >&2; }
    # the staging directory is private (mode 700) and named .antigravity-stage.*, and the tarball never lands in a look-alike
    [[ -s "$sb/stagemode.log" && "$(sort -u "$sb/stagemode.log")" == 700 ]] || { ok=0; echo "the staging directory was not mode 700: [$(sort -u "$sb/stagemode.log" 2>&1)]" >&2; }
    grep -E -- ' -o [^ ]*pkg\.tgz' "$sb/calls.log" | grep -qF -- "-o $opt/.antigravity-stage.userdata/" && { ok=0; echo "a download went into the user's look-alike stage directory" >&2; }
    grep -E -- ' -o [^ ]*pkg\.tgz' "$sb/calls.log" | grep -qE -- " -o $opt/\.antigravity-stage\.[A-Za-z0-9]{6}/pkg\.tgz" || { ok=0; echo "the download did not go to a fresh .antigravity-stage.XXXXXX: $(grep ' -o ' "$sb/calls.log" | head -2)" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "AutoOS replaced or touched a directory it does not own, or its staging directory is not private"; fi
fi

if it "antigravity second run: skipped with no network call; --update on the same build is skipped; a newer build is swapped in; an older one is never installed"; then
    sb="$(antigravity_scratch)"; ok=1
    antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" >/dev/null
    dir="$sb/home/.local/opt/antigravity"; link="$sb/home/.local/bin/antigravity"; dt="$sb/home/.local/share/applications/antigravity.desktop"
    : >"$sb/calls.log"; before="$(antigravity_tree_state "$sb")"
    antigravity_run "$sb"
    [[ "$AG_STATE" == skipped && "$AG_RC" == 0 ]] || { ok=0; echo "plain second run: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:500}" >&2; }
    [[ ! -s "$sb/calls.log" ]] || { ok=0; echo "a plain second run made calls: $(cat "$sb/calls.log")" >&2; }
    [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "a plain second run changed the tree" >&2; }
    # --update on the same build: asks the listing and the manifest, downloads and writes nothing
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
    [[ "$AG_STATE" == skipped && "$AG_RC" == 0 ]] || { ok=0; echo "--update, same build: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:500}" >&2; }
    [[ "$(antigravity_count "$sb" ' -o [^ ]*pkg\.tgz')" == 0 && "$(antigravity_count "$sb" '^curl ')" == 2 ]] \
        || { ok=0; echo "--update, same build: expected the listing and the manifest only: $(grep '^curl' "$sb/calls.log")" >&2; }
    [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "--update on the current build changed the tree" >&2; }
    # a newer build exists: without --update nothing happens, with it the directory is swapped
    antigravity_serve "$sb" "$AG_VB" "$AG_IDB"
    : >"$sb/calls.log"
    antigravity_run "$sb"
    [[ "$AG_STATE" == skipped && ! -s "$sb/calls.log" && "$(sed -n 2p "$dir/.autoos-version")" == "$AG_IDA" ]] || { ok=0; echo "a newer build without --update: state=[$AG_STATE]" >&2; }
    t_dt="$(find "$dt" -printf '%T@')"
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
    [[ "$AG_STATE" == installed && "$AG_RC" == 0 ]] || { ok=0; echo "--update, newer build: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:600}" >&2; }
    probs="$(antigravity_problems "$sb" "$AG_IDB")"
    [[ -z "$probs" ]] || { ok=0; echo "$probs" >&2; }
    [[ -x "$link" && "$(readlink "$link")" == "$dir/antigravity" ]] || { ok=0; echo "the command link no longer resolves after the update" >&2; }
    [[ "$(find "$dt" -printf '%T@')" == "$t_dt" ]] || { ok=0; echo "an identical desktop entry was rewritten by the update" >&2; }
    [[ -z "$(find "$sb/home/.local/opt" -maxdepth 1 -name 'antigravity.old-*')" ]] || { ok=0; echo "the previous build is still there: $(ls -A "$sb/home/.local/opt")" >&2; }
    # the winget-pkgs latest is OLDER than what is installed (listing lags): not a downgrade
    rm -f "$sb/versions"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    before="$(antigravity_tree_state "$sb")"
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
    [[ "$AG_STATE" == skipped && "$AG_RC" == 0 && "$(sed -n 2p "$dir/.autoos-version")" == "$AG_IDB" ]] || { ok=0; echo "an older latest: state=[$AG_STATE], stamp [$(sed -n 2p "$dir/.autoos-version")]: ${AG_OUT:0:400}" >&2; }
    [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "an older latest changed the tree" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the second run, --update, or the swap does not behave"; fi
fi

if it "antigravity update with the API down fails loudly (non-zero) and leaves the install intact - it is never reported as current"; then
    ok=1
    for mode in offline 403 non-json; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        antigravity_run "$sb" >/dev/null
        before="$(antigravity_tree_state "$sb")"; : >"$sb/calls.log"
        case "$mode" in
            offline)  : >"$sb/offline" ;;
            403)      printf 403 >"$sb/listing.status"; printf 'x-ratelimit-reset: %s\n' "$AG_RESET" >"$sb/listing.headers" ;;
            non-json) printf 'nope' >"$sb/listing.json" ;;
        esac
        antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
        [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "$mode: state=[$AG_STATE] rc=[$AG_RC] (must fail)" >&2; }
        [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "$mode: the install changed" >&2; }
        [[ "$AG_OUT" == *"listing"* ]] || { ok=0; echo "$mode: the failure does not name the step: ${AG_OUT:0:400}" >&2; }
        antigravity_run "$sb" AUTOOS_UPDATE=1
        [[ "$AG_STATE" == failed ]] || { ok=0; echo "$mode: through install_component the state is [$AG_STATE], not failed" >&2; }
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "an update check that cannot look is reported as success"; fi
fi

if it "antigravity command link: created when absent; a regular file, a foreign symlink and a link merely pointing inside the install dir are kept with a warning; a dangling own link is kept and resolves"; then
    ok=1
    for kind in absent file foreign inside dangling-own; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        dir="$sb/home/.local/opt/antigravity"; link="$sb/home/.local/bin/antigravity"
        mkdir -p "$sb/home/.local/bin"
        case "$kind" in
            file)         printf 'mine\n' >"$link" ;;
            foreign)      ln -s "$sb/elsewhere" "$link" ;;
            inside)       ln -s "$dir/resources/app.asar" "$link" ;;
            dangling-own) ln -s "$dir/antigravity" "$link" ;;
        esac
        antigravity_run "$sb" AG_ENTRY=direct
        [[ "$AG_STATE" == installed && "$AG_RC" == 0 ]] || { ok=0; echo "$kind: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
        case "$kind" in
            absent|dangling-own)
                [[ "$(readlink "$link")" == "$dir/antigravity" && -x "$link" ]] || { ok=0; echo "$kind: link is [$(readlink "$link")] or does not resolve" >&2; } ;;
            file)
                [[ ! -L "$link" && "$(cat "$link")" == mine ]] || { ok=0; echo "file: a regular file was overwritten" >&2; } ;;
            foreign)
                [[ "$(readlink "$link")" == "$sb/elsewhere" ]] || { ok=0; echo "foreign: a foreign symlink was replaced: [$(readlink "$link")]" >&2; } ;;
            inside)
                [[ "$(readlink "$link")" == "$dir/resources/app.asar" ]] || { ok=0; echo "inside: a link pointing elsewhere inside the dir was replaced: [$(readlink "$link")]" >&2; } ;;
        esac
        if [[ "$kind" == file || "$kind" == foreign || "$kind" == inside ]]; then
            [[ "$AG_OUT" == *"$link"* && "$AG_OUT" == *"left alone"* ]] || { ok=0; echo "$kind: no warning naming $link: ${AG_OUT:0:500}" >&2; }
        fi
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "the antigravity command link clobbers something that is not AutoOS's, or is not created"; fi
fi

if it "antigravity desktop entry: a symlink is refused, a differing file is backed up byte-exact before it is replaced, an identical one is left untouched"; then
    ok=1
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    apps="$sb/home/.local/share/applications"; dt="$apps/antigravity.desktop"; mkdir -p "$apps"
    printf '[Desktop Entry]\nName=mine\n' >"$dt"
    antigravity_run "$sb" AG_ENTRY=direct
    [[ "$AG_STATE" == installed && "$AG_RC" == 0 ]] || { ok=0; echo "differing: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
    bak=("$dt".autoos-backup-*)
    [[ ${#bak[@]} == 1 && -f "${bak[0]}" && "$(cat "${bak[0]}")" == $'[Desktop Entry]\nName=mine' ]] || { ok=0; echo "the differing entry was not backed up first: ${bak[*]}" >&2; }
    grep -qxF 'MimeType=x-scheme-handler/antigravity;' "$dt" || { ok=0; echo "the entry was not replaced" >&2; }
    [[ -z "$(find "$apps" -name '.antigravity.desktop.*')" ]] || { ok=0; echo "a temp file was left in $apps" >&2; }
    t1="$(find "$dt" -printf '%T@')"
    antigravity_serve "$sb" "$AG_VB" "$AG_IDB"       # a new build; the launcher does not change
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "update: state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    [[ "$(find "$dt" -printf '%T@')" == "$t1" ]] || { ok=0; echo "an identical desktop entry was rewritten" >&2; }
    bak=("$dt".autoos-backup-*)
    [[ ${#bak[@]} == 1 ]] || { ok=0; echo "an identical entry got a backup: ${bak[*]}" >&2; }
    rm -rf "$sb"
    # a symlink at the desktop path is refused; whatever it points at is untouched
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    apps="$sb/home/.local/share/applications"; dt="$apps/antigravity.desktop"; mkdir -p "$apps"
    printf 'victim\n' >"$sb/victim"; ln -s "$sb/victim" "$dt"
    antigravity_run "$sb" AG_ENTRY=direct
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "symlink: the install itself must still succeed: state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    [[ -L "$dt" && "$(readlink "$dt")" == "$sb/victim" && "$(cat "$sb/victim")" == victim ]] || { ok=0; echo "symlink: the desktop path or its target was changed" >&2; }
    [[ "$AG_OUT" == *"$dt"* && "$AG_OUT" == *"symlink"* ]] || { ok=0; echo "symlink: no warning naming $dt: ${AG_OUT:0:500}" >&2; }
    [[ -z "$(find "$apps" -name '*.autoos-backup-*')" ]] || { ok=0; echo "symlink: a backup was made" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the desktop entry is not written compare-first with a backup, or follows a symlink"; fi
fi

if it "antigravity desktop entry: the temp file comes from mktemp, so a file at the old predictable name (.antigravity.desktop.<pid>) is never overwritten or removed, not even when the write fails"; then
    ok=1; old_umask="$(umask)"; umask 022
    for mode in ok mv-fails; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        apps="$sb/home/.local/share/applications"; dt="$apps/antigravity.desktop"
        : >"$sb/plant-desktop-tmp"
        if [[ "$mode" == mv-fails ]]; then antigravity_run "$sb" AG_ENTRY=direct AG_MV_FAIL_DESKTOP=1; else antigravity_run "$sb" AG_ENTRY=direct; fi
        [[ "$AG_STATE" == installed && "$AG_RC" == 0 ]] || { ok=0; echo "$mode: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
        planted=("$apps"/.antigravity.desktop.*)
        [[ ${#planted[@]} == 1 && -f "${planted[0]}" && "$(cat "${planted[0]}")" == victim ]] \
            || { ok=0; echo "$mode: the file at the old predictable name was removed or changed (left: ${planted[*]})" >&2; }
        if [[ "$mode" == ok ]]; then
            grep -qxF 'MimeType=x-scheme-handler/antigravity;' "$dt" 2>/dev/null || { ok=0; echo "$mode: the desktop entry was not written: ${AG_OUT:0:500}" >&2; }
            [[ "$(stat -c %a "$dt" 2>/dev/null)" == 644 ]] || { ok=0; echo "$mode: the desktop entry has mode [$(stat -c %a "$dt" 2>&1)], not 644 (umask 022)" >&2; }
        else
            [[ ! -e "$dt" && "$AG_OUT" == *"could not write"* ]] || { ok=0; echo "$mode: a failed write must warn and leave no desktop entry: ${AG_OUT:0:500}" >&2; }
        fi
        rm -rf "$sb"
    done
    umask "$old_umask"
    if (( ok )); then pass; else fail "the desktop entry's temp file name is predictable, or a failed write removes a file AutoOS did not create"; fi
fi

if it "antigravity desktop entry: the install path is quoted for the Desktop Entry spec (space, quote, dollar, percent, backslash) and the sandbox command is single-quoted"; then
    ok=1; sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    odd="$sb"'/h m"e$x%u\b`t'"'"'s'; mkdir -p "$odd"
    antigravity_run "$sb" "AG_HOME=$odd" AG_SYSCTL=restricted
    dir="$odd/.local/opt/antigravity"; dt="$odd/.local/share/applications/antigravity.desktop"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "state=[$AG_STATE]: ${AG_OUT:0:600}" >&2; }
    # expected by hand: " ` $ escaped with one backslash, \ written as four (string escape, then quote escape), % doubled
    want_exec='Exec="'"$sb"'/h m\"e\$x%%u\\\\b\`t'"'"'s/.local/opt/antigravity/antigravity" %U'
    grep -qxF "$want_exec" "$dt" || { ok=0; echo "Exec line is [$(grep '^Exec' "$dt" 2>&1)], expected [$want_exec]" >&2; }
    want_icon='Icon='"$sb"'/h m"e$x%u\\b`t'"'"'s/.local/opt/antigravity/icon.png'
    grep -qxF "$want_icon" "$dt" || { ok=0; echo "Icon line is [$(grep '^Icon' "$dt" 2>&1)], expected [$want_icon]" >&2; }
    [[ "$(grep -c '^Exec=' "$dt")" == 1 && "$(grep -c '^\[Desktop Entry\]' "$dt")" == 1 ]] || { ok=0; echo "the entry has stray lines: $(cat "$dt")" >&2; }
    # single quotes in the path are closed, escaped and reopened
    sq="'"; bs='\'; esc="${dir//$sq/$sq$bs$sq$sq}"
    want_sb="sudo chown root:root '$esc/chrome-sandbox' && sudo chmod 4755 '$esc/chrome-sandbox'"
    [[ "$AG_OUT" == *"$want_sb"* ]] || { ok=0; echo "the sandbox command is not shell-safe for $dir: ${AG_OUT:0:900}" >&2; }
    rm -rf "$sb"
    # a newline in the path cannot be put in a desktop entry: it is skipped with a warning, the install stands
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    odd="$sb/line"$'\n'"break"; mkdir -p "$odd"
    antigravity_run "$sb" "AG_HOME=$odd" AG_ENTRY=direct
    [[ "$AG_STATE" == installed && ! -e "$odd/.local/share/applications/antigravity.desktop" && "$AG_OUT" == *"desktop entry"* ]] \
        || { ok=0; echo "newline path: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a path with special characters breaks the desktop entry or the printed sandbox command"; fi
fi

if it "antigravity desktop registration: update-desktop-database runs on the applications dir; xdg-mime sets the scheme handler only when none is set, after backing up mimeapps.list"; then
    ok=1
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    mkdir -p "$sb/home/.config"; printf '[Default Applications]\ntext/x-foo=foo.desktop\n' >"$sb/home/.config/mimeapps.list"
    antigravity_run "$sb"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    grep -qxF "update-desktop-database $sb/home/.local/share/applications" "$sb/calls.log" || { ok=0; echo "update-desktop-database was not run on the applications dir: $(grep '^update' "$sb/calls.log" 2>&1)" >&2; }
    grep -qxF 'xdg-mime query default x-scheme-handler/antigravity' "$sb/calls.log" || { ok=0; echo "the current handler was not asked" >&2; }
    grep -qxF 'xdg-mime default antigravity.desktop x-scheme-handler/antigravity' "$sb/calls.log" || { ok=0; echo "the scheme handler was not registered: $(grep '^xdg' "$sb/calls.log")" >&2; }
    bak=("$sb/home/.config/mimeapps.list".autoos-backup-*)
    [[ ${#bak[@]} == 1 && "$(cat "${bak[0]}" 2>/dev/null)" == $'[Default Applications]\ntext/x-foo=foo.desktop' ]] || { ok=0; echo "mimeapps.list was not backed up byte-exact before xdg-mime ran: ${bak[*]}" >&2; }
    rm -rf "$sb"
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    mkdir -p "$sb/home/.config"; printf '[Default Applications]\n' >"$sb/home/.config/mimeapps.list"
    antigravity_run "$sb" AG_MIME_DEFAULT=other.desktop
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "handler set: state=[$AG_STATE]" >&2; }
    ! grep -q '^xdg-mime default' "$sb/calls.log" || { ok=0; echo "an existing scheme handler was overwritten" >&2; }
    [[ -z "$(find "$sb/home/.config" -name '*.autoos-backup-*')" ]] || { ok=0; echo "mimeapps.list was backed up although it was not touched" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the desktop registration overrides the user's handler, skips the backup, or is not attempted"; fi
fi

if it "antigravity refuses to run as root when the home is not root's own (sudo ./setup.sh would leave root-owned files in the user's home)"; then
    ok=1
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" AG_ENTRY=direct SYS_IS_ROOT=1 "AG_ROOT_HOME=$sb/rootshome"
    [[ "$AG_STATE" == failed && "$AG_RC" != 0 ]] || { ok=0; echo "root in a user's home: state=[$AG_STATE] rc=[$AG_RC] (must refuse)" >&2; }
    [[ "$AG_OUT" == *"run setup as your own user"* ]] || { ok=0; echo "root: the refusal does not say what to do: ${AG_OUT:0:400}" >&2; }
    [[ ! -s "$sb/calls.log" && -z "$(find "$sb/home" -mindepth 1)" ]] || { ok=0; echo "root: something was called or written: $(cat "$sb/calls.log" 2>&1)" >&2; }
    antigravity_run "$sb" SYS_IS_ROOT=1 "AG_ROOT_HOME=$sb/rootshome"
    [[ "$AG_STATE" == failed ]] || { ok=0; echo "root via install_component: state=[$AG_STATE]" >&2; }
    rm -rf "$sb"
    # root in its own home (a container, a real root login) is fine
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" AG_ENTRY=direct SYS_IS_ROOT=1 "AG_ROOT_HOME=$sb/home"
    [[ "$AG_STATE" == installed && "$AG_RC" == 0 ]] || { ok=0; echo "root in its own home: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:500}" >&2; }
    rm -rf "$sb"
    # not root: the home is the user's, no matter which
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" AG_ENTRY=direct SYS_IS_ROOT=0 "AG_ROOT_HOME=$sb/rootshome"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "a normal user was refused: ${AG_OUT:0:400}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "root can write a user's home through the Antigravity installer"; fi
fi

if it "antigravity dry run as root in a home that is not root's: says what would be refused and succeeds (a dry run changes nothing); the real run still refuses"; then
    ok=1
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    for entry in direct component; do
        antigravity_run "$sb" AG_ENTRY="$entry" AUTOOS_DRY_RUN=1 SYS_IS_ROOT=1 "AG_ROOT_HOME=$sb/rootshome"
        [[ "$AG_STATE" != failed && "$AG_RC" == 0 ]] || { ok=0; echo "$entry: a dry run as root reported state=[$AG_STATE] rc=[$AG_RC] (must succeed): ${AG_OUT:0:400}" >&2; }
        [[ "$AG_OUT" == *"would refuse: run setup as your own user"* ]] || { ok=0; echo "$entry: the dry run does not say what would be refused (would refuse: run setup as your own user): ${AG_OUT:0:400}" >&2; }
        [[ "$AG_OUT" != *"would look up"* && "$AG_OUT" != *"would download"* ]] || { ok=0; echo "$entry: the dry run previews an install that the real run would refuse: ${AG_OUT:0:400}" >&2; }
        [[ ! -s "$sb/calls.log" && -z "$(find "$sb/home" -mindepth 1)" ]] || { ok=0; echo "$entry: a dry run called or wrote something: $(cat "$sb/calls.log" 2>&1)" >&2; }
    done
    # the same machine without --dry-run still refuses, and says what to do
    antigravity_run "$sb" AG_ENTRY=direct SYS_IS_ROOT=1 "AG_ROOT_HOME=$sb/rootshome"
    [[ "$AG_STATE" == failed && "$AG_RC" != 0 && "$AG_OUT" == *"run setup as your own user"* && "$AG_OUT" != *"would refuse"* ]] \
        || { ok=0; echo "the real run as root in a user's home: state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
    [[ -z "$(find "$sb/home" -mindepth 1)" ]] || { ok=0; echo "the refused real run wrote: $(find "$sb/home" -mindepth 1 | tr '\n' ' ')" >&2; }
    # root in ITS OWN home gets the normal preview, not a refusal
    antigravity_run "$sb" AG_ENTRY=direct AUTOOS_DRY_RUN=1 SYS_IS_ROOT=1 "AG_ROOT_HOME=$sb/home"
    [[ "$AG_RC" == 0 && "$AG_OUT" == *"winget-pkgs"* && "$AG_OUT" != *"would refuse"* ]] || { ok=0; echo "root in its own home: rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a dry run as root in another user's home reports Antigravity as failed, or the real run stopped refusing"; fi
fi

if it "antigravity detection: a valid stamp counts as installed, the old apt command /usr/bin/antigravity does not, and neither do a wrong marker or a stamp behind a symlink"; then
    sb="$(antigravity_scratch)"; ok=1
    dir="$sb/home/.local/opt/antigravity"; mkdir -p "$sb/bin" "$dir"
    printf '#!/bin/sh\n' >"$sb/bin/antigravity"; chmod +x "$sb/bin/antigravity"      # what the old deb put on PATH
    # PATH holds only the scratch bin dir, so nothing real can answer.
    probe() { ( PATH="$sb/bin"; SYS_HOME="$sb/home"; _extra_bin_dirs() { printf '%s\n' "$SYS_HOME/.local/bin" "$sb/bin"; }
                script_is_installed antigravity && echo installed || echo not-installed ); }
    [[ "$(probe)" == not-installed ]] || { ok=0; echo "the old apt command /usr/bin/antigravity still counts as installed" >&2; }
    printf '%s\n%s\nsize=1\nsha256=abc\n' "$AG_MARKER" "$AG_IDA" >"$dir/.autoos-version"
    [[ "$(probe)" == installed ]] || { ok=0; echo "a valid stamp does not count as installed" >&2; }
    st="$( ( PATH="$sb/bin"; SYS_HOME="$sb/home"; detect_installed_status script antigravity 0; echo "$INSTALLED_STATUS" ) )"
    [[ "$st" == installed ]] || { ok=0; echo "detect_installed_status says [$st]" >&2; }
    printf 'autoos-antigravity-ide\n2.5.5-1\n' >"$dir/.autoos-version"
    [[ "$(probe)" == not-installed ]] || { ok=0; echo "the IDE-era marker counts as installed" >&2; }
    printf '%s x\n%s\n' "$AG_MARKER" "$AG_IDA" >"$dir/.autoos-version"
    [[ "$(probe)" == not-installed ]] || { ok=0; echo "a marker with trailing text counts as installed" >&2; }
    rm -f "$dir/.autoos-version"; printf '%s\n%s\n' "$AG_MARKER" "$AG_IDA" >"$sb/stamp"; ln -s "$sb/stamp" "$dir/.autoos-version"
    [[ "$(probe)" == not-installed ]] || { ok=0; echo "a stamp behind a symlink counts as installed" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "Antigravity detection keys on the old apt command, or accepts a stamp that is not AutoOS's"; fi
fi

if it "antigravity old apt package: one warning with the removal commands, nothing removed, the Hub is still installed; no warning when it is absent"; then
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" AG_OLD_APT=1
    ok=1
    [[ "$AG_STATE" == installed && "$AG_RC" == 0 ]] || { ok=0; echo "state=[$AG_STATE] rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
    probs="$(antigravity_problems "$sb" "$AG_IDA")"
    [[ -z "$probs" ]] || { ok=0; echo "$probs" >&2; }
    for want in "sudo apt-get remove antigravity" "/etc/apt/sources.list.d/antigravity.list" "/etc/apt/keyrings/antigravity-repo-key.gpg"; do
        [[ "$AG_OUT" == *"$want"* ]] || { ok=0; echo "the warning lacks '$want': ${AG_OUT:0:700}" >&2; }
    done
    [[ "$(grep -c 'sudo apt-get remove antigravity' <<<"$AG_OUT")" == 1 ]] || { ok=0; echo "the warning was not printed exactly once" >&2; }
    [[ "$AG_OUT" == *"1.23.2"* || "$AG_OUT" == *"IDE"* ]] || { ok=0; echo "the warning does not say what the old package is: ${AG_OUT:0:500}" >&2; }
    [[ "$(antigravity_count "$sb" 'apt-get|^sudo |^run sudo ')" == 0 ]] || { ok=0; echo "apt or sudo was run: $(grep -E 'apt-get|sudo' "$sb/calls.log")" >&2; }
    rm -rf "$sb"
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb"
    [[ "$AG_OUT" != *"apt-get remove"* ]] || { ok=0; echo "warned about an old apt package that is not installed" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the old apt package is not reported, or something was removed automatically"; fi
fi

if it "antigravity PATH check: warns naming the command that shadows ours (the old /usr/bin/antigravity first on PATH), says nothing when ours resolves"; then
    ok=1
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    printf '#!/bin/sh\n' >"$sb/oldbin/antigravity"; chmod +x "$sb/oldbin/antigravity"
    antigravity_run "$sb" "AG_PATH_FIRST=$sb/oldbin"
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "state=[$AG_STATE]: ${AG_OUT:0:400}" >&2; }
    [[ "$AG_OUT" == *"$sb/oldbin/antigravity"* && "$AG_OUT" == *"$sb/home/.local/bin/antigravity"* ]] || { ok=0; echo "no warning naming the shadowing command and ours: ${AG_OUT:0:700}" >&2; }
    [[ -x "$sb/home/.local/bin/antigravity" ]] || { ok=0; echo "our link was not created" >&2; }
    rm -rf "$sb"
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb"
    [[ "$AG_OUT" != *"shadow"* && "$AG_OUT" != *"resolves to"* ]] || { ok=0; echo "a warning although our link resolves first: ${AG_OUT:0:500}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a shadowing antigravity command goes unnoticed, or a correct PATH is warned about"; fi
fi

if it "antigravity dry run and --dry-run --update: name the winget-pkgs source and the target directory, make no call and write nothing"; then
    sb="$(antigravity_scratch)"; ok=1
    antigravity_run "$sb" AUTOOS_DRY_RUN=1
    [[ "$AG_RC" == 0 ]] || { ok=0; echo "rc=[$AG_RC]: ${AG_OUT:0:400}" >&2; }
    [[ "$AG_OUT" == *"winget-pkgs"* && "$AG_OUT" == *"$sb/home/.local/opt/antigravity"* ]] || { ok=0; echo "the dry run does not name winget-pkgs and the target directory: ${AG_OUT:0:600}" >&2; }
    [[ "$AG_OUT" == *"no published sha256"* ]] || { ok=0; echo "the dry run does not say the tarball has no published sha256" >&2; }
    [[ ! -s "$sb/calls.log" ]] || { ok=0; echo "a dry run made calls: $(cat "$sb/calls.log")" >&2; }
    [[ -z "$(find "$sb/home" -mindepth 1)" ]] || { ok=0; echo "a dry run wrote: $(find "$sb/home" -mindepth 1 | tr '\n' ' ')" >&2; }
    for bad in ".deb" "apt-get" "us-central1" "auto-updater" "sudo"; do
        [[ "$AG_OUT" != *"$bad"* ]] || { ok=0; echo "the dry run still mentions '$bad'" >&2; }
    done
    rm -rf "$sb"
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" >/dev/null
    before="$(antigravity_tree_state "$sb")"; : >"$sb/calls.log"
    antigravity_run "$sb" AUTOOS_DRY_RUN=1 AUTOOS_UPDATE=1
    [[ "$AG_RC" == 0 && "$AG_OUT" == *"winget-pkgs"* && "$AG_OUT" == *"$sb/home/.local/opt/antigravity"* ]] \
        || { ok=0; echo "update dry run: rc=[$AG_RC], does not name winget-pkgs and the directory: ${AG_OUT:0:500}" >&2; }
    [[ ! -s "$sb/calls.log" ]] || { ok=0; echo "an update dry run made calls: $(cat "$sb/calls.log")" >&2; }
    [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "an update dry run changed the tree" >&2; }
    antigravity_run "$sb" AUTOOS_DRY_RUN=1
    [[ "$AG_STATE" == skipped && "$AG_OUT" != *"would look up"* ]] || { ok=0; echo "a plain dry run of an installed Hub is not a skip: ${AG_OUT:0:400}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the Antigravity dry run asks the network, writes, or does not say what it would do"; fi
fi

if it "antigravity interrupted download: a SIGTERM or an exit from underneath removes the staging directory and leaves the existing install untouched"; then
    ok=1
    for how in kill exit; do
        sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
        antigravity_run "$sb" >/dev/null
        antigravity_serve "$sb" "$AG_VB" "$AG_IDB"
        before="$(antigravity_tree_state "$sb")"; : >"$sb/$how-on-download"; : >"$sb/calls.log"
        antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1
        # the install runs in a subshell of its own: that one dies of the TERM (143) or exits
        # (5) - and the caller is told the install failed and carries on
        want_rc=143; if [[ "$how" == exit ]]; then want_rc=5; fi
        [[ "$AG_STATE" == failed && "$AG_RC" == "$want_rc" ]] || { ok=0; echo "$how: state=[$AG_STATE] rc=[$AG_RC], expected failed with rc $want_rc: ${AG_OUT:0:300}" >&2; }
        [[ "$(antigravity_count "$sb" '^curl .* -o [^ ]*pkg\.tgz')" == 1 ]] || { ok=0; echo "$how: the download was never started: $(grep '^curl' "$sb/calls.log" | tail -2)" >&2; }
        [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "$how: the interrupt left something behind: $(diff <(echo "$before") <(antigravity_tree_state "$sb") | head -5)" >&2; }
        rm -rf "$sb"
    done
    if (( ok )); then pass; else fail "an interrupted install leaves a staging directory with a half-downloaded tarball"; fi
fi

if it "antigravity traps: a caller's EXIT trap does not run inside the install and is still armed afterwards (the install neither replays nor drops it)"; then
    ok=1
    # (1) the caller's trap lives in the shell AROUND the one that runs the install (the test
    # suite itself is like that): it must not be re-armed in the install's own shell, where it
    # would run at that shell's exit - inside a $(...) it lands in the captured output
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    ( trap 'echo CALLER_EXIT' EXIT
      antigravity_run "$sb" AG_ENTRY=direct AG_TRAPS=inherited
      printf '%s\n' "$AG_STATE" >"$sb/h.state"; printf '%s\n' "$AG_OUT" >"$sb/h.out"
      echo H_AFTER_INSTALL
      trap -p EXIT >"$sb/h.traps" ) >"$sb/h.stdout" 2>&1
    [[ "$(<"$sb/h.state")" == installed ]] || { ok=0; echo "inherited: state=[$(<"$sb/h.state")]: $(head -c 500 "$sb/h.out")" >&2; }
    ! grep -qx CALLER_EXIT "$sb/h.out" || { ok=0; echo "inherited: the caller's EXIT trap ran inside the install: $(grep -n CALLER_EXIT "$sb/h.out")" >&2; }
    [[ "$(<"$sb/h.stdout")" == $'H_AFTER_INSTALL\nCALLER_EXIT' ]] || { ok=0; echo "inherited: the caller's own trap did not run exactly once, after the install: [$(tr '\n' '|' <"$sb/h.stdout")]" >&2; }
    [[ "$(<"$sb/h.traps")" == "trap -- 'echo CALLER_EXIT' EXIT" ]] || { ok=0; echo "inherited: the caller's trap is [$(<"$sb/h.traps")] afterwards" >&2; }
    rm -rf "$sb"
    # (2) the caller's trap is set in the very shell that runs the install: not run during it, still there after it
    sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
    antigravity_run "$sb" AG_ENTRY=direct AG_TRAPS=own
    [[ "$AG_STATE" == installed ]] || { ok=0; echo "own: state=[$AG_STATE]: ${AG_OUT:0:500}" >&2; }
    [[ "$AG_OUT" == *"AGTRAPS trap -- 'echo CALLER_EXIT' EXIT"* ]] || { ok=0; echo "own: the caller's trap is not armed after the install: $(grep '^AGTRAPS' <<<"$AG_OUT")" >&2; }
    [[ "$(grep -c CALLER_EXIT <<<"$AG_OUT")" == 2 && "${AG_OUT##*$'\n'}" == CALLER_EXIT ]] \
        || { ok=0; echo "own: CALLER_EXIT must appear once in the AGTRAPS line and once at the very end, after the install: $(grep -n CALLER_EXIT <<<"$AG_OUT")" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the Antigravity install runs a caller's EXIT trap early or loses it"; fi
fi

if it "antigravity staging: a SIGTERM or an exit right after the staging directory is made, before any request or check, still removes it (the cleanup is armed before the directory exists)"; then
    ok=1
    for how in term exit; do
        for start in fresh update; do
            sb="$(antigravity_scratch)"; antigravity_serve "$sb" "$AG_VA" "$AG_IDA"
            if [[ "$start" == update ]]; then
                antigravity_run "$sb" >/dev/null
                antigravity_serve "$sb" "$AG_VB" "$AG_IDB"
            fi
            before="$(antigravity_tree_state "$sb")"; : >"$sb/calls.log"
            antigravity_run "$sb" AG_ENTRY=direct AUTOOS_UPDATE=1 "AG_AFTER_STAGE=$how"
            [[ "$AG_STATE" != installed && "$AG_STATE" != skipped ]] || { ok=0; echo "$how/$start: the run was reported as [$AG_STATE]" >&2; }
            [[ -z "$(antigravity_debris "$sb")" ]] || { ok=0; echo "$how/$start: left behind: $(antigravity_debris "$sb" | tr '\n' ' ')" >&2; }
            [[ "$(antigravity_count "$sb" '^curl ')" == 0 ]] || { ok=0; echo "$how/$start: a request was made before the signal: $(grep '^curl' "$sb/calls.log")" >&2; }
            if [[ "$start" == update ]]; then
                [[ "$(antigravity_tree_state "$sb")" == "$before" ]] || { ok=0; echo "$how/$start: the existing install or its surroundings changed: $(diff <(echo "$before") <(antigravity_tree_state "$sb") | head -5)" >&2; }
            else
                [[ -z "$(find "$sb/home" -type f)" ]] || { ok=0; echo "$how/$start: files were left: $(find "$sb/home" -type f | tr '\n' ' ')" >&2; }
            fi
            rm -rf "$sb"
        done
    done
    if (( ok )); then pass; else fail "a signal that arrives right after the staging directory is made leaves .antigravity-stage.XXXXXX behind"; fi
fi

if it "antigravity catalog entry: updatable, no prompt, x64-only, notes name the Hub, winget-pkgs, the command and that there is no published sha256"; then
    problems="$(python3 - 2>&1 <<'PY'
import json
doc = json.load(open("catalog/linux.json", encoding="utf-8"))
ent = [c for g in doc["categories"] for c in g["components"] if c["id"] == "antigravity"]
if len(ent) != 1:
    print("expected exactly one antigravity entry, found %d" % len(ent))
else:
    c = ent[0]
    notes = c.get("notes", "")
    if "frozen" in notes.lower() or "1.23.2" in notes: print("notes still describe the frozen apt repo: " + notes)
    if "antigravity-ide" in notes or c.get("verify", "").startswith("antigravity-ide"): print("the IDE command antigravity-ide is still named")
    if "Hub" not in notes: print("notes do not say this is the Hub")
    if "winget-pkgs" not in notes: print("notes do not say the version is discovered from winget-pkgs")
    if "no published sha256" not in notes: print("notes do not say there is no published sha256")
    if "`antigravity`" not in notes and "command: antigravity" not in notes: print("notes do not name the command antigravity")
    if "--update" not in notes: print("notes do not mention --update")
    if "antigravity" not in c.get("verify", ""): print("verify %r does not name the antigravity install" % c.get("verify"))
    if "--version" in c.get("verify", "") or "--help" in c.get("verify", ""): print("verify %r would start the Electron app (it has no CLI wrapper)" % c.get("verify"))
    if c.get("updatable") is not True: print("updatable is %r, not true" % c.get("updatable"))
    if c.get("arch") != ["x64"]: print("arch is %r, must stay x64-only" % c.get("arch"))
    if c.get("prompt"): print("carries prompt %r" % c["prompt"])
    if (c.get("provider"), c.get("package"), c.get("id"), c.get("name")) != ("script", "antigravity", "antigravity", "Antigravity"):
        print("id/name/provider/package changed: %r" % ((c.get("provider"), c.get("package"), c.get("id"), c.get("name")),))
PY
)"
    stale="$(grep -rIl 'antigravity_url' lib catalog setup.sh setup.ps1 README.md docs 2>/dev/null | tr '\n' ' ')"
    [[ -z "$stale" ]] || problems+="antigravity_url is still referenced in: $stale"
    assert_eq "$problems" ""
fi

if it "antigravity catalog text: describes the Hub, not the IDE, and claims to be hidden on headless machines only when its category needs a display"; then
    problems="$(python3 - 2>&1 <<'PY'
import json
doc = json.load(open("catalog/linux.json", encoding="utf-8"))
found = [(g, c) for g in doc["categories"] for c in g["components"] if c["id"] == "antigravity"]
if len(found) != 1:
    print("expected exactly one antigravity entry, found %d" % len(found))
else:
    grp, c = found[0]
    desc, notes = c.get("description", ""), c.get("notes", "")
    # the display requirement is a property of the CATEGORY (lib/linux/catalog.sh hides
    # a requiresDisplay category on a headless machine); a claim without it is false
    for field, text in (("description", desc), ("notes", notes)):
        if "headless" in text.lower() and not grp.get("requiresDisplay"):
            print("%s says 'headless' but category %r has no requiresDisplay, so the entry is shown there: %s" % (field, grp.get("id"), text[:120]))
    if "IDE" in desc or "agent-first" in desc:
        print("description still describes the IDE: %r" % desc)
    if "Antigravity 2" not in desc or "desktop app" not in desc:
        print("description does not describe the Hub (Antigravity 2.x, a desktop app): %r" % desc)
    if len(desc) > 70:
        print("description is %d characters, the catalog convention is <= 70: %r" % (len(desc), desc))
    if "Hub" not in notes:
        print("notes do not name the Hub: %r" % notes[:120])
    if "arm64" not in notes:
        print("notes lost the true part of the hiding claim (the entry is x64-only, so it is hidden on arm64): %r" % notes[:160])
PY
)"
    assert_eq "$problems" ""
fi

if it "antigravity launch hint: a verify command that is not the app (test -x ...) does not make 'Where to find them' say 'run test'"; then
    ok=1
    for verify in "test -x ~/.local/opt/antigravity/antigravity" "test -d ~/.cao" "[ -x /opt/x ]"; do
        if launch_hint "Antigravity" "antigravity" "$verify"; then
            [[ "$LAUNCH_HOW" != *"run  test"* && "$LAUNCH_HOW" != *"run  ["* ]] || { ok=0; echo "verify [$verify] gives the hint [$LAUNCH_HOW]" >&2; }
        fi
    done
    if (( ok )); then pass; else fail "the launch hint names the shell builtin that verifies the install instead of the app"; fi
fi


# ─── The version check: setup.sh --update ───────────────────────────────────
# There is no general update mechanism. --update (AUTOOS_UPDATE=1) makes an
# already-installed component run its own installer again - but only when its
# catalog entry says "updatable": true - and that installer decides. Without the
# flag a second run is `skipped` (AGENTS.md section 4).

if it "antigravity update flag: AUTOOS_UPDATE=1 re-runs only installed components the catalog marks updatable"; then
    ok=1
    # update_probe <update 0|1> <package> [catalog-path]: state and whether the installer ran.
    update_probe() {
        ( CATALOG_PATH="${3-$ROOT/catalog/linux.json}"; AUTOOS_UPDATE="$1"; AUTOOS_DRY_RUN=0; RAN=0
          is_installed() { return 0; }
          install_script() { RAN=1; [[ "${PROBE_SKIP:-0}" == 1 ]] && INSTALL_SCRIPT_STATE=skipped; return 0; }
          install_component script "$2" 0 >/dev/null 2>&1
          printf '%s ran=%s' "$INSTALL_STATE" "$RAN" )
    }
    [[ "$(update_probe 0 antigravity)" == "skipped ran=0" ]] || { ok=0; echo "without --update: [$(update_probe 0 antigravity)]" >&2; }
    [[ "$(update_probe 1 antigravity)" == "installed ran=1" ]] || { ok=0; echo "--update, updatable: [$(update_probe 1 antigravity)]" >&2; }
    [[ "$(update_probe 1 uv)" == "skipped ran=0" ]] || { ok=0; echo "--update, a component that is not updatable: [$(update_probe 1 uv)]" >&2; }
    [[ "$(update_probe 1 no-such-package)" == "skipped ran=0" ]] || { ok=0; echo "--update, an unknown package: [$(update_probe 1 no-such-package)]" >&2; }
    [[ "$(update_probe 1 antigravity /no/such/catalog.json)" == "skipped ran=0" ]] || { ok=0; echo "--update without a readable catalog must skip: [$(update_probe 1 antigravity /no/such/catalog.json)]" >&2; }
    [[ "$(PROBE_SKIP=1 update_probe 1 antigravity)" == "skipped ran=1" ]] || { ok=0; echo "an installer's own 'skipped' is reported as [$(PROBE_SKIP=1 update_probe 1 antigravity)]" >&2; }
    if (( ok )); then pass; else fail "install_component's update gate does not follow the catalog"; fi
fi

if it "antigravity catalog entry is the only updatable one, and its notes name the version check"; then
    problems="$(python3 - 2>&1 <<'PY'
import json
for p in ("catalog/linux.json", "catalog/macos.json", "catalog/windows.json"):
    doc = json.load(open(p, encoding="utf-8"))
    for g in doc["categories"]:
        for c in g["components"]:
            if "updatable" in c and not (p == "catalog/linux.json" and c["id"] == "antigravity"):
                print(p + ": " + c["id"] + " is marked updatable but has no version check")
            if p == "catalog/linux.json" and c["id"] == "antigravity":
                if c.get("updatable") is not True: print("antigravity: updatable is %r, not true" % c.get("updatable"))
                if "--update" not in c.get("notes", ""): print("antigravity: notes do not mention --update")
PY
)"
    assert_eq "$problems" ""
fi

# antigravity_inert_clients <dir>: stand-ins for the agent CLIs. setup.sh's
# detection asks them which MCP servers they know (`claude mcp list`,
# `qodercli mcp list`, `ollama list`), and that starts a real client process;
# a test about a flag or a plan line has no business doing that.
antigravity_inert_clients() {
    local c
    mkdir -p "$1"
    for c in claude qodercli opencode codex qwen agy gemini ollama; do printf '#!/bin/sh\nexit 0\n' >"$1/$c"; chmod +x "$1/$c"; done
}

if it "antigravity update flag: setup.sh knows --update, lists it in --help and still rejects unknown options"; then
    ok=1; sb="$(antigravity_scratch)"; antigravity_inert_clients "$sb/bin"
    out="$(bash setup.sh --help 2>&1)"
    [[ "$out" == *"--update"* ]] || { ok=0; echo "--help does not list --update" >&2; }
    out="$(PATH="$sb/bin:$PATH" HOME="$sb/home" bash setup.sh --update --list --no-color 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" != *"Unknown option"* ]] || { ok=0; echo "--update --list: rc=$rc: ${out:0:200}" >&2; }
    out="$(bash setup.sh --updat --list --no-color 2>&1)"; rc=$?
    [[ $rc -eq 2 && "$out" == *"Unknown option: --updat"* ]] || { ok=0; echo "--updat was not rejected: rc=$rc: ${out:0:200}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the --update flag is not wired into setup.sh's argument parsing"; fi
fi

if it "antigravity update flag: the plan says 'checking for a newer version' only with --update, a dry run asks nothing and writes nothing"; then
    if [[ "$(uname -m)" != x86_64 ]]; then skip "Antigravity is x64-only and hidden on this machine"
    elif [[ "$(id -u)" == 0 ]]; then skip "root refuses a scratch HOME on purpose (see the root test above)"
    else
        sb="$(antigravity_scratch)"; ok=1
        mkdir -p "$sb/home/.local/opt/antigravity"; printf '%s\n%s\nsize=1\nsha256=abc\n' "$AG_MARKER" "$AG_IDA" >"$sb/home/.local/opt/antigravity/.autoos-version"
        antigravity_inert_clients "$sb/bin"
        # USER names nobody, so detection falls back to HOME: the scratch home is the machine.
        # SUDO_USER wins over USER in detect.sh: CI's setpriv runner inherits it from
        # `sudo unshare`, which pointed the install at the runner's real home.
        run_setup() { env -u SUDO_USER PATH="$sb/bin:$PATH" USER=agy-test-nobody HOME="$sb/home" DISPLAY=:0 AUTOOS_CACHE_DIR="$sb/cache" \
            bash setup.sh --only antigravity --dry-run --yes --no-color "$@" 2>&1; }
        before="$(find "$sb/home/.local" -printf '%p|%T@\n' | sort)"
        out="$(run_setup)"; rc=$?
        [[ $rc -eq 0 && "$out" == *"Already installed - package will be skipped"* && "$out" != *"checking for a newer version"* ]] \
            || { ok=0; echo "without --update: rc=$rc: $(grep -i 'installed\|newer' <<<"$out" | head -5)" >&2; }
        out="$(run_setup --update)"; rc=$?
        [[ $rc -eq 0 && "$out" == *"checking for a newer version"* ]] || { ok=0; echo "--update: rc=$rc: $(grep -i 'installed\|newer' <<<"$out" | head -5)" >&2; }
        [[ "$out" == *"winget-pkgs"* ]] || { ok=0; echo "--update: the dry run does not say it would look in winget-pkgs" >&2; }
        [[ "$(grep -c '^run:' <<<"$out")" == 0 ]] || { ok=0; echo "--update: a dry run executed a command" >&2; }
        [[ "$(find "$sb/home/.local" -printf '%p|%T@\n' | sort)" == "$before" ]] || { ok=0; echo "the dry run wrote under ~/.local" >&2; }
        rm -rf "$sb"
        if (( ok )); then pass; else fail "setup.sh --update does not reach the Antigravity version check"; fi
    fi
fi

# VS Code, Google Chrome and the GitHub CLI share the pattern Antigravity was
# fixed for: guard on the key file, fetch, install, write the apt source line.
# install_component runs an installer with errexit OFF, so an unchecked failed
# fetch installed an EMPTY key file that the `-f` guard then trusted forever
# (apt failed on every later run). The key must exist only when it is non-empty.
#
# apt_key_case <vscode|chrome|gh>: sets AK_* for one installer.
apt_key_case() {
    case "$1" in
        vscode) AK_FN=install_vscode; AK_KEY=/etc/apt/keyrings/packages.microsoft.gpg
                AK_LIST=/etc/apt/sources.list.d/vscode.list; AK_PKG=code
                AK_URL=https://packages.microsoft.com/keys/microsoft.asc; AK_BODY="DEARMORED:ARMORED-KEY"
                AK_LINE="deb [arch=amd64,arm64,armhf signed-by=$AK_KEY] https://packages.microsoft.com/repos/code stable main" ;;
        chrome) AK_FN=install_google_chrome; AK_KEY=/etc/apt/keyrings/google-chrome.gpg
                AK_LIST=/etc/apt/sources.list.d/google-chrome.list; AK_PKG=google-chrome-stable
                AK_URL=https://dl.google.com/linux/linux_signing_key.pub; AK_BODY="DEARMORED:ARMORED-KEY"
                AK_LINE="deb [arch=amd64 signed-by=$AK_KEY] http://dl.google.com/linux/chrome/deb/ stable main" ;;
        gh)     AK_FN=install_gh; AK_KEY=/etc/apt/keyrings/githubcli-archive-keyring.gpg
                AK_LIST=/etc/apt/sources.list.d/github-cli.list; AK_PKG=gh
                AK_URL=https://cli.github.com/packages/githubcli-archive-keyring.gpg; AK_BODY="ARMORED-KEY"
                AK_LINE="deb [arch=amd64 signed-by=$AK_KEY] https://cli.github.com/packages stable main" ;;
    esac
}

# apt_key_run <scratch> <installer function> [fail-curl|empty-body]
# One installer run in its own subshell against a scratch apt tree
# (AUTOOS_APT_PREFIX). Every writing stub refuses a path outside the scratch
# tree, so even code that ignores the seam cannot touch the real /etc/apt, as
# root or not. Temp files go to <scratch>/tmp so leftovers are visible.
apt_key_run() {
    local sb="$1" fn="$2" mode="${3:-}"
    (
        AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""; AUTOOS_APT_PREFIX="$sb"; APT_UPDATED=0
        export TMPDIR="$sb/tmp"; mkdir -p "$TMPDIR"
        log="$sb/calls.log"
        inside() { [[ "$1" == "$sb"/* ]] || { printf 'REFUSED (outside the scratch tree) %s\n' "$1" >>"$log"; return 1; }; }
        curl() {
            printf 'curl %s\n' "$*" >>"$log"
            [[ "$mode" == fail-curl ]] && return 22
            [[ "$mode" == empty-body ]] && return 0
            printf 'ARMORED-KEY\n'
        }
        gpg() { printf 'gpg %s\n' "$*" >>"$log"; sed 's/^/DEARMORED:/'; }
        install() {
            printf 'install %s\n' "$*" >>"$log"
            local src="${*: -2:1}" dst="${*: -1}"
            inside "$dst" || return 1
            command mkdir -p "$(dirname "$dst")" && cp "$src" "$dst"
        }
        tee() {
            printf 'tee %s\n' "$*" >>"$log"
            inside "${*: -1}" || { cat >/dev/null; return 1; }
            command tee "$@"
        }
        mkdir() { printf 'mkdir %s\n' "$*" >>"$log"; inside "${*: -1}" || return 1; command mkdir "$@"; }
        # shellcheck disable=SC2120  # stub: the installers (sourced, not visible here) call it with arguments
        run() { printf 'run %s\n' "$*" >>"$log"; }
        has_cmd() { [[ "$1" == apt-get ]]; }
        dpkg() { printf 'amd64\n'; }
        "$fn"
    ) 2>&1
}

for _k in vscode chrome gh; do
    if it "apt keys: $_k leaves no key when the download fails"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; ok=1
        for mode in fail-curl empty-body; do
            rm -rf "${sb:?}/etc" "${sb:?}/tmp" "${sb:?}/calls.log"; mkdir -p "$sb/etc/apt/sources.list.d"
            out="$(apt_key_run "$sb" "$AK_FN" "$mode")"; rc=$?
            (( rc != 0 )) || { ok=0; echo "$mode: rc=0 counts a failed key download as installed" >&2; }
            [[ -z "$(find "$sb/etc" -type f)" ]] || { ok=0; echo "$mode: left files behind: $(find "$sb/etc" -type f | tr '\n' ' ')" >&2; }
            [[ -z "$(find "$sb/tmp" -type f)" ]] || { ok=0; echo "$mode: temp file not removed" >&2; }
            grep -qF -- "$AK_URL" "$sb/calls.log" 2>/dev/null || { ok=0; echo "$mode: the key download was never attempted" >&2; }
            ! grep -q 'apt-get install' "$sb/calls.log" 2>/dev/null || { ok=0; echo "$mode: went on to apt-get install" >&2; }
            [[ "$out" == *"not installed"* ]] || { ok=0; echo "$mode: no reason given: ${out:0:200}" >&2; }
        done
        rm -rf "$sb"
        if (( ok )); then pass; else fail "a failed $_k key download leaves an empty key or a source line apt cannot use"; fi
    fi

    if it "apt keys: $_k an existing empty key is replaced on the next successful run"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/keyrings" "$sb/etc/apt/sources.list.d"
        # What the old code left behind after a failed download: an empty key
        # and the source line that names it.
        : >"$sb$AK_KEY"; printf '%s\n' "$AK_LINE" >"$sb$AK_LIST"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc=$?
        ok=1
        (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:200}" >&2; }
        [[ "$(cat "$sb$AK_KEY" 2>/dev/null)" == "$AK_BODY" ]] || { ok=0; echo "the empty key was trusted, not replaced: [$(cat "$sb$AK_KEY" 2>/dev/null)]" >&2; }
        [[ "$(cat "$sb$AK_LIST")" == "$AK_LINE" ]] || { ok=0; echo "source line changed: $(cat "$sb$AK_LIST")" >&2; }
        grep -qx "run apt-get install -y $AK_PKG" "$sb/calls.log" 2>/dev/null || { ok=0; echo "the package was not installed: $(cat "$sb/calls.log" 2>/dev/null)" >&2; }
        rm -rf "$sb"
        if (( ok )); then pass; else fail "an empty key from an earlier bad run is trusted forever"; fi
    fi

    # The suite's install() stub only copies, so a key installed without the
    # ownership and mode would pass every other test here. The stub logs its argv;
    # install(1) without -m gives 0755, and the key must be root-owned and 0644
    # for apt (sandboxed as _apt) to read it.
    if it "apt keys: $_k the key is installed root-owned with mode 644"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc=$?
        ok=1
        (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:200}" >&2; }
        inst="$(grep '^install ' "$sb/calls.log" 2>/dev/null)"
        [[ "$(grep -c . <<<"$inst")" == 1 ]] || { ok=0; echo "expected exactly one install call, got: [$inst]" >&2; }
        for want in " -D " " -o root " " -g root " " -m 644 "; do
            [[ " $inst " == *"$want"* ]] || { ok=0; echo "the key install lacks [${want//[[:space:]]/}]: [$inst]" >&2; }
        done
        [[ "${inst##* }" == "$sb$AK_KEY" ]] || { ok=0; echo "the key was not installed to $AK_KEY: [$inst]" >&2; }
        rm -rf "$sb"
        if (( ok )); then pass; else fail "the $_k apt key is not installed root-owned with mode 644"; fi
    fi

    if it "apt keys: $_k a second successful run is unchanged"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc1=$?
        ok=1
        (( rc1 == 0 )) || { ok=0; echo "first run rc=$rc1: ${out:0:200}" >&2; }
        [[ "$(cat "$sb$AK_KEY" 2>/dev/null)" == "$AK_BODY" ]] || { ok=0; echo "first run: key is [$(cat "$sb$AK_KEY" 2>/dev/null)]" >&2; }
        [[ "$(cat "$sb$AK_LIST" 2>/dev/null)" == "$AK_LINE" ]] || { ok=0; echo "first run: source line is [$(cat "$sb$AK_LIST" 2>/dev/null)]" >&2; }
        [[ "$(grep '^run ' "$sb/calls.log" 2>/dev/null)" == $'run apt-get update -y\nrun apt-get install -y '"$AK_PKG" ]] \
            || { ok=0; echo "first run: apt commands: $(grep '^run ' "$sb/calls.log" 2>/dev/null | tr '\n' '|')" >&2; }
        before="$(cksum "$sb$AK_KEY" "$sb$AK_LIST" 2>/dev/null)"
        : >"$sb/calls.log"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc2=$?
        after="$(cksum "$sb$AK_KEY" "$sb$AK_LIST" 2>/dev/null)"
        (( rc2 == 0 )) || { ok=0; echo "second run rc=$rc2: ${out:0:200}" >&2; }
        writes="$(grep -E '^(curl|gpg|install|tee) ' "$sb/calls.log" || true)"
        [[ -z "$writes" ]] || { ok=0; echo "the second run wrote again: $writes" >&2; }
        [[ "$before" == "$after" ]] || { ok=0; echo "key or source list changed on the second run" >&2; }
        [[ "$(wc -l <"$sb$AK_LIST" 2>/dev/null)" == 1 ]] || { ok=0; echo "source list grew" >&2; }
        grep -qx "run apt-get install -y $AK_PKG" "$sb/calls.log" || { ok=0; echo "apt's own no-op install was skipped" >&2; }
        [[ -z "$(find "$sb/tmp" -type f)" ]] || { ok=0; echo "temp file left behind" >&2; }
        rm -rf "$sb"
        if (( ok )); then pass; else fail "a second $_k run is not a no-op"; fi
    fi
done

if it "the antigravity catalog entry needs no download URL: no prompt, and no catalog asks antigravity_url"; then
    problems="$(python3 - 2>&1 <<'PY'
import json
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
    doc = json.load(open(p, encoding="utf-8"))
    if "antigravity_url" in doc.get("prompts", {}):
        print(p + ": still asks antigravity_url")
    for g in doc["categories"]:
        for c in g["components"]:
            if c["id"] == "antigravity" and c.get("prompt"):
                print(p + ": antigravity still carries prompt " + str(c["prompt"]))
PY
)"
    assert_eq "$problems" ""
fi

# Measured 2026-09-25: the vendor agy installer ends with `agy install`, which
# appends a PATH line to ~/.zshrc, ~/.zprofile and ~/.profile. AutoOS runs it,
# so AutoOS keeps the originals (AGENTS.md: back up user-owned files).
if it "install_agy backs up the shell profiles its vendor installer edits"; then
    home="$(mktemp -d)"
    printf 'original zshrc\n' >"$home/.zshrc"
    printf 'original profile\n' >"$home/.profile"
    out="$( (
        HOME="$home"; AUTOOS_DRY_RUN=0
        curl() {
            local o="" p=""
            for a in "$@"; do [[ "$p" == "-o" ]] && o="$a"; p="$a"; done
            printf '#!/usr/bin/env bash\necho "export PATH=x" >>"$HOME/.zshrc"\necho "export PATH=x" >>"$HOME/.profile"\n' >"$o"
        }
        install_agy
    ) 2>&1)"; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc $out" >&2; }
    zb="$(cat "$home"/.zshrc.autoos-backup-* 2>/dev/null)"
    pb="$(cat "$home"/.profile.autoos-backup-* 2>/dev/null)"
    [[ "$zb" == "original zshrc" ]] || { ok=0; echo "zshrc backup: '$zb'" >&2; }
    [[ "$pb" == "original profile" ]] || { ok=0; echo "profile backup: '$pb'" >&2; }
    compgen -G "$home/.zprofile*" >/dev/null && { ok=0; echo "backed up a file that did not exist" >&2; }
    rm -rf "$home"
    if (( ok )); then pass; else fail "agy's vendor installer edits profiles without a backup"; fi
fi

if it "script dispatch covers qodercli and devin-cli"; then
    ok=1
    grep -q 'qodercli) *install_qodercli' lib/linux/install.sh || ok=0
    grep -q 'devin-cli) *install_devin_cli' lib/linux/install.sh || ok=0
    if (( ok )); then pass; else fail "dispatch missing"; fi
fi

if it "zed routing merges one provider and keeps the rest"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{"theme":"mine"}' >"$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="test-omni-key" AUTOOS_LITELLM_API_KEY="test-lit-key" route_zed_to_proxy >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="test-omni-key" AUTOOS_LITELLM_API_KEY="test-lit-key" route_zed_to_proxy >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
oc = cfg.get("language_models", {}).get("openai_compatible", {})
omni = oc.get("autoos-omniroute", {})
lit = oc.get("autoos-litellm", {})
# The model lists are catalog/ide-models.json projected at run time (ids,
# display names, windows, membership, order); the 1M tier is 1000000.
cat = json.load(open("catalog/ide-models.json", encoding="utf-8"))["models"]
def want(gateway):
    out = []
    for m in cat:
        if "zed" in m["surfaces"].get(gateway, []):
            e = {"name": m["id"], "display_name": m["name"], "max_tokens": m["context"]}
            if m.get("reasoning_effort"):
                e["reasoning_effort"] = m["reasoning_effort"]
            out.append(e)
    return out
got_models = {g: oc.get("autoos-" + g, {}).get("available_models") for g in ("omniroute", "litellm")}
bad = [g for g in got_models if got_models[g] != want(g)]
t1 = [m.get("max_tokens") for m in (got_models["omniroute"] or []) if m.get("name") == "t1-orchestrator"]
models = "catalog-ok" if not bad and t1 == [1000000] else "MISMATCH:%s t1=%s" % (",".join(bad), t1)
# Keys never land in settings.json (Zed docs: keychain/UI or env).
# Pins come from the harness at runtime, never as literals in lib/.
h = json.load(open("catalog/agent-harness.json", encoding="utf-8"))
ctx = cfg.get("context_servers", {})
pinok = ",".join(sorted(
    "pin-ok" if h["mcp_servers"][n]["package"] in " ".join(ctx.get(n, {}).get("args", []))
    else "MISSING:" + n
    for n in ("serena", "graphify", "omnigraph", "playwright", "context7", "autoos-agent")))
print("%s|%s|%s|%s|%s|%s|%s" % (
    cfg.get("theme"), omni.get("api_url"), models,
    "api_key" in omni, lit.get("api_url"), "api_key" in lit, pinok))
bp = cfg.get("agent", {}).get("profiles", {}).get("bypass", {})
btools = bp.get("tools", {})
off = sorted(k for k, v in btools.items() if v is not True)
print("bypass=%s|off=%s|provider=%s|model=%s|allow=%s|ctx=%s" % (
    bp.get("name"), ",".join(off),
    bp.get("default_model", {}).get("provider"),
    bp.get("default_model", {}).get("model"),
    cfg.get("agent", {}).get("tool_permissions", {}).get("default"),
    ",".join(sorted(cfg.get("context_servers", {})))))
PY
)"
    backups="$(ls "$scratch"/.config/zed/settings.json.autoos-backup-* 2>/dev/null | wc -l)"
    leaks="$(grep -cE 'sk-[A-Za-z0-9]{10,}|_API_KEY|REPLACE' "$scratch/.config/zed/settings.json" || true)"
    rm -rf "$scratch"
    # Two prints = one newline inside $report; assert each line separately.
    line1="$(printf '%s' "$report" | sed -n '1p')"
    line2="$(printf '%s' "$report" | sed -n '2p')"
    assert_eq "$line1" \
        "mine|http://127.0.0.1:20128/v1|catalog-ok|False|http://127.0.0.1:4000/v1|False|pin-ok,pin-ok,pin-ok,pin-ok,pin-ok,pin-ok"
    assert_eq "$line2" \
        "bypass=bypass|off=|provider=autoos-omniroute|model=t1-orchestrator|allow=allow|ctx=autoos-agent,context7,graphify,omnigraph,playwright,serena"
    assert_eq "leaks=$leaks" "leaks=0"
    # Two runs share second-precision backup names: same second -> 1 file,
    # straddling a boundary -> 2. Either proves backup-before-edit; an exact
    # count would flake on wall-clock timing.
    if (( backups >= 1 )); then pass; else fail "no backup written"; fi
fi

if it "zed routing without key env warns and stays key-free"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{}' >"$scratch/.config/zed/settings.json"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset AUTOOS_OMNIROUTE_API_KEY AUTOOS_LITELLM_API_KEY; route_zed_to_proxy ) 2>&1)"
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
oc = cfg.get("language_models", {}).get("openai_compatible", {})
print("%s|%s" % ("api_key" in oc.get("autoos-omniroute", {}),
                 "api_key" in oc.get("autoos-litellm", {})))
PY
)"
    rm -rf "$scratch"
    if [[ "$report" == "False|False" && "$out" == *"stays hidden"* ]]; then pass
    else fail "report=$report out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "litellm installer announces in dry run and detects presence"; then
    empty="$(mktemp -d)"
    out="$( ( PATH="$empty:/usr/bin:/bin" AUTOOS_DRY_RUN=1; install_litellm_proxy ) 2>&1)"
    stub="$(mktemp -d)"; printf '#!/bin/sh\nexit 0\n' >"$stub/litellm"; chmod +x "$stub/litellm"
    out2="$( ( PATH="$stub:$PATH" AUTOOS_DRY_RUN=0; install_litellm_proxy ) 2>&1)"
    rm -rf "$empty" "$stub"
    if [[ "$out" == *"would install"* && "$out2" == *"already installed"* ]]; then pass
    else fail "dry-run or presence path broken"; fi
fi

if it "sidekick extra is created once and never duplicated"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/nvim"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; enable_sidekick_extra >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; enable_sidekick_extra >/dev/null 2>&1 )
    n="$(grep -c 'lazyvim.plugins.extras.ai.sidekick' "$scratch/.config/nvim/lazyvim.json" || true)"
    rm -rf "$scratch"
    assert_eq "$n" "1"
fi

if it "sidekick enabling is a no-op without an nvim config"; then
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; enable_sidekick_extra >/dev/null 2>&1 )
    if [[ -e "$scratch/.config/nvim/lazyvim.json" ]]; then rm -rf "$scratch"; fail "created config unasked"
    else rm -rf "$scratch"; pass; fi
fi

if it "litellm fallback config is internally consistent"; then
    report="$(python3 - 2>&1 <<'PY'
import re, io
text = io.open("configuration/litellm/config.yaml", encoding="utf-8").read()
groups = set(re.findall(r"(?m)^\s*-\s*model_name:\s*(\S+)\s*$", text))
need = {"t1-orchestrator", "t1-orchestrator-paid", "t1-orchestrator-free-only", "t2-worker", "t2-worker-paid", "t2-worker-free-only", "t3-driver", "t3-driver-paid", "t3-driver-free-only"}
fb = text.split("fallbacks:", 1)[1]
refs = set(re.findall(r"[- ](\S+):\s*\[([^\]]*)\]", fb))
problems = sorted(list(need - groups))
for src, tgts in refs:
    if src not in groups:
        problems.append("src:" + src)
    for t in [x.strip() for x in tgts.split(",")]:
        if t not in groups:
            problems.append("tgt:" + t)
models = re.findall(r"(?m)^\s*model:\s*(\S+)\s*$", text)
# llama-3.3-70b left groq's free tier in 2026-08 and must never come back.
# cerebras is NOT stale: combos.json re-admits it as a credit/paid-capable
# leg (2026-09-20), so the old "any cerebras routed" check no longer holds.
# Its presence in the managed blocks is asserted against combos.json by
# tools/sync-router-tiers.py --check.
if [m for m in models if "llama-3.3-70b" in m]:
    problems.append("stale")
if "drop_params" not in text or "os.environ/LITELLM_MASTER_KEY" not in text:
    problems.append("settings")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "sync-router-tiers's unit tests pass (registry-sourced, task A5c)"; then
    out="$(python3 tests/test_sync_router_tiers_registry.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "free-only litellm groups mirror combos minus gateway-only legs"; then
    # The *-free-only groups are hand-curated (not sync-managed), so this
    # pins them to combos.json with hardcoded expectations: same legs in the
    # same order, and the ONLY permitted drops are the known OAuth-bridge
    # legs LiteLLM has no transport or key for. Anything else missing - or
    # any silently added leg - is drift. Free-only groups must also stay out
    # of fallbacks (zero spend means fail loudly, never bill silently).
    report="$(python3 - 2>&1 <<'PY'
import io, json, re
combos = {c["name"]: c["models"]
          for c in json.load(open("configuration/omniroute/combos.json",
                                   encoding="utf-8"))["combos"]}
# LiteLLM model strings: OmniRoute provider/model passes through except the
# OpenAI-compatible gateways (zen, cheaperinference -> openai/ + api_base).
# Only pre-existing, proven mappings appear here - no new inference.
# known_drops is deliberately hardcoded, NOT derived from GATEWAY_ONLY in
# tools/sync-router-tiers.py: the test must stay an independent second
# opinion - deriving it would make tool and test agree by construction.
transport = {"opencode-zen": "openai", "cheaperinference": "openai"}
def litellm_model(ref):
    prov, model = ref.split("/", 1)
    return "%s/%s" % (transport.get(prov, prov), model)
known_drops = {"antigravity/gemini-3.7-flash-medium",
               "antigravity/claude-opus-4-6-thinking"}
text = io.open("configuration/litellm/config.yaml", encoding="utf-8").read()
problems = []
for tier in ("t1-orchestrator-free-only", "t2-worker-free-only",
             "t3-driver-free-only"):
    want = [litellm_model(r) for r in combos[tier] if r not in known_drops]
    got = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^[ \t]*-[ \t]*model_name:[ \t]*(\S+)[ \t]*$", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"^[ \t]*model:[ \t]*(\S+)[ \t]*$", line)
        if m and cur == tier:
            got.append(m.group(1))
    if got != want:
        problems.append(tier + "-drift")
    # Every combos leg must be mirrored or declared-dropped: a new leg that
    # is neither fails here until known_drops explicitly acknowledges it.
    # (Checked against got, the parsed file - not against want above, which
    # would make this tautological.)
    unmirrored = [r for r in combos[tier] if litellm_model(r) not in got]
    if set(unmirrored) - known_drops:
        problems.append(tier + "-unpinned-drop")
fb = text.split("fallbacks:", 1)[1]
for tier in ("t1-orchestrator-free-only", "t2-worker-free-only",
             "t3-driver-free-only"):
    if re.search(r"(?m)^\s*-\s*" + tier + r"\s*:", fb):
        problems.append(tier + "-in-fallbacks")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "opencode repo config pins omniroute with litellm fallback"; then
    report="$(python3 - <<'PY'
import json, re, io
text = io.open("opencode.jsonc", encoding="utf-8").read()
text = re.sub(r"(?m)^\s*//.*$", "", text)
oc = json.loads(text)
h = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
p = oc["providers"]
pins = sorted(
    "pin-ok" if h["mcp_servers"][name]["package"] in " ".join(spec.get("command", []))
    else "MISSING:" + name
    for name, spec in oc["mcp"]["servers"].items())
print("%s|%s|%s|%s|%s|%s" % (
    oc["model"],
    p["omniroute"]["settings"]["baseURL"],
    ",".join(sorted(p["omniroute"]["models"].keys())),
    "litellm" in p,
    ",".join(sorted(oc["mcp"]["servers"].keys())),
    ",".join(pins)))
PY
)"
    assert_eq "$report" \
        "omniroute/t1-orchestrator|http://127.0.0.1:20128/v1|auto,auto/cheap,auto/smart,cheaperinference/glm-5.2,cheaperinference/kimi-k3,deepseek-v4.1-flash,gemini-3.8-flash,opus-4-6,samba/MiniMax-M3,samba/gpt-oss-120b,spark-1.3-contributor,t1-orchestrator,t1-orchestrator-clean,t1-orchestrator-free-only,t2-orchestrator,t2-worker,t2-worker-clean,t2-worker-free-only,t3-driver,t3-driver-clean,t3-driver-free-only,t4-rag|True|autoos-agent,context7,graphify,omnigraph,playwright,serena|pin-ok,pin-ok,pin-ok,pin-ok,pin-ok,pin-ok"
fi

if it "openhands template has tiers and no secrets"; then
    ok=1
    for s in '\[llm\]' '\[llm.t1-orchestrator\]' '\[llm.t2-worker\]' '\[llm.t3-driver\]' '\[llm.t1-orchestrator-clean\]' '\[llm.t2-worker-clean\]' '\[llm.t3-driver-clean\]' '\[llm.t4-rag\]' '\[llm.litellm-t1-orchestrator\]' '\[llm.litellm-t2-worker\]' '\[llm.litellm-t3-driver\]' '\[llm.draft_editor\]' '\[agent.CodeActAgent\]'; do
        grep -q "$s" configuration/openhands/config.toml || { ok=0; echo "missing: $s" >&2; }
    done
    grep -q 'host.docker.internal:20128' configuration/openhands/config.toml || ok=0
    grep -qE 'sk-[A-Za-z0-9]{10,}' configuration/openhands/config.toml && ok=0
    grep -q 'api_key = ""' configuration/openhands/config.toml || ok=0
    if (( ok )); then pass; else fail "openhands template is incomplete or leaks secrets"; fi
fi

if it "start-stack.sh is valid bash and names the client key"; then
    bash -n configuration/start-stack.sh || { fail "syntax error"; }
    ok=1
    grep -q 'AUTOOS_OMNIROUTE_KEY' configuration/start-stack.sh || ok=0
    grep -q 'host.docker.internal' configuration/start-stack.sh || ok=0
    grep -q 'opencode-serve' configuration/start-stack.sh || ok=0
    if (( ok )); then pass; else fail "start script is missing wiring"; fi
fi

if it "openhands launch is detached, probed and stale-settings safe"; then
    # -it fails without a TTY and foreground never returns; schema_version 6
    # settings 500 the current image. Both fixed 2026-09-21 - pin the shape.
    # 2026-09-23: the guard repairs in place instead of deleting (the old
    # shape moved the whole file aside and lost the user's profiles/keys).
    # It must target agent_settings.schema_version (top-level stays 2-3 on
    # broken files too) and strip the agent-canvas `enabled` MCP keys.
    ok=1
    for f in configuration/start-stack.ps1 configuration/start-stack.sh; do
        grep -q 'docker run -d ' "$f" || { ok=0; echo "not detached: $f" >&2; }
        grep -q 'docker run -it' "$f" && { ok=0; echo "still -it: $f" >&2; }
        grep -q 'agent_settings.schema_version\|agent_settings.*schema_version' "$f" || { ok=0; echo "wrong schema_version level: $f" >&2; }
        grep -q 'autoos-backup' "$f" || { ok=0; echo "no backup: $f" >&2; }
        grep -q 'docker logs openhands-app' "$f" || { ok=0; echo "no probe: $f" >&2; }
        # Tier profiles re-project from the spec on every start (never stale).
        grep -q 'sync-openhands-profiles' "$f" || { ok=0; echo "never syncs: $f" >&2; }
        # Repair, not delete: only versions NEWER than the image (> 4) clamp
        # down; older payloads keep theirs so the image's own migrations run.
        grep -qE '> 4|-gt 4' "$f" || { ok=0; echo "wrong clamp version: $f" >&2; }
        # The agent-canvas `enabled` MCP key 500s the image (extra_forbidden).
        grep -q 'enabled' "$f" || { ok=0; echo "no enabled-key strip: $f" >&2; }
        # Repair, not delete, for parseable files: the version-mismatch path
        # must clamp in place (at most one settings.json removal remains: the
        # unparseable-file fallback, where there is nothing to preserve).
        grep -qE 'schema_version.?\s?\]?\s?= 4' "$f" || { ok=0; echo "no in-place clamp: $f" >&2; }
        [[ "$(grep -cE 'Remove-Item \$ohSettings|rm -f "\$oh_settings"' "$f")" -le 1 ]] || { ok=0; echo "deletes parseable user settings: $f" >&2; }
    done
    if (( ok )); then pass; else fail "openhands launch shape regressed"; fi
fi

if it "OpenHands mcp_config carries no enabled key (live schema forbids it)"; then
    # The live MCPServer model (additionalProperties false) rejects 'enabled'
    # with extra_forbidden and 500s /api/v1/settings (measured 2026-09-21).
    # Only the double-quoted form is asserted: the opencode writer's
    # single-quoted 'enabled': True is schema-legal and stays.
    n="$(grep -c '"enabled": True' lib/linux/install.sh || true)"
    assert_eq "$n" "0"
fi

if it "mirror-litellm-env projects keys without printing them"; then
    tmp="$(mktemp -d)"
    printf 'groq: dummy-groq-1\nmeta: dummy-meta-2\ncohere: dummy-cohere-3\nSambaNova: dummy-samba-4\nzen: REPLACE_WITH_ZEN_KEY\n' >"$tmp/api-keys.yml"
    out="$(python3 tools/mirror-litellm-env.py --keys "$tmp/api-keys.yml" --env "$tmp/.env" 2>&1)"
    rc=$?
    leaked="$(printf '%s' "$out" | grep -c 'dummy-' || true)"
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "mirror rc=$rc" >&2; }
    [[ "$leaked" == "0" ]] || { ok=0; echo "value leaked" >&2; }
    grep -q '^GROQ_API_KEY=dummy-groq-1$' "$tmp/.env" || { ok=0; echo "groq" >&2; }
    grep -q '^META_API_KEY=dummy-meta-2$' "$tmp/.env" || { ok=0; echo "meta" >&2; }
    grep -q '^COHERE_API_KEY=dummy-cohere-3$' "$tmp/.env" || { ok=0; echo "cohere" >&2; }
    grep -q '^SAMBANOVA_API_KEY=dummy-samba-4$' "$tmp/.env" || { ok=0; echo "SambaNova case" >&2; }
    grep -q '^OPENCODE_ZEN_API_KEY=REPLACE' "$tmp/.env" || { ok=0; echo "zen placeholder" >&2; }
    grep -q '^LITELLM_MASTER_KEY=.' "$tmp/.env" && ! grep -q '^LITELLM_MASTER_KEY=REPLACE_' "$tmp/.env" || { ok=0; echo "master" >&2; }
    python3 tools/mirror-litellm-env.py --check --keys "$tmp/api-keys.yml" --env "$tmp/.env" >/dev/null 2>&1
    [[ $? -eq 0 ]] || { ok=0; echo "fresh check failed" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "mirror tool broken"; fi
fi

if it "mirror-litellm-env's unit tests pass (registry-sourced, task A5c)"; then
    out="$(python3 tests/test_mirror_litellm_env_registry.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "no committed secrets in router files"; then    # Report file:line only - a failure message must never echo the value it
    # found into logs or a terminal shared with anyone else.
    hits="$(grep -rnE 'sk-[A-Za-z0-9]{10,}' configuration/litellm/config.yaml \
        configuration/litellm/.env.example opencode.jsonc \
        configuration/openhands/config.toml 2>/dev/null | cut -d: -f1,2 || true)"
    # .env.example is the sanctioned placeholder pattern; only real-looking keys fail.
    if [[ -z "$hits" ]]; then pass; else fail "credential-shaped value at: $hits"; fi
fi

