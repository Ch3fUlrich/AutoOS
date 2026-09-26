# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

describe "Serena tool exclusions (serena)"

# tests/fixtures/serena/<case>.yml -> <case>.expected.yml is the ONE set of
# YAML shapes both ensure_serena_exclusions (here) and Set-AutoOSSerenaExclusions
# (tests/run-tests.ps1) are graded against, so the two implementations cannot
# quietly drift apart. Comparison is byte-for-byte (cmp), not line-based.
for f in tests/fixtures/serena/*.yml; do
    case "$f" in *.expected.yml) continue ;; esac
    base="${f%.yml}"
    name="$(basename "$base")"
    exp="${base}.expected.yml"

    if it "ensure_serena_exclusions matches fixture '$name' byte-for-byte (serena)"; then
        tmp="$(mktemp -d)"
        cp "$f" "$tmp/config.yml"
        ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/config.yml" ) >/dev/null 2>&1
        if cmp -s "$tmp/config.yml" "$exp"; then
            pass
        else
            fail "$(diff -u "$exp" "$tmp/config.yml" 2>&1 | head -20)"
        fi
        rm -rf "$tmp"
    fi

    if it "ensure_serena_exclusions second run on fixture '$name' skips and writes nothing (serena)"; then
        tmp="$(mktemp -d)"
        cp "$f" "$tmp/config.yml"
        ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/config.yml" ) >/dev/null 2>&1
        sum1="$(sha256sum "$tmp/config.yml" | awk '{print $1}')"
        out2="$( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/config.yml" 2>&1 )"
        sum2="$(sha256sum "$tmp/config.yml" | awk '{print $1}')"
        nbackups="$(find "$tmp" -maxdepth 1 -name '*.autoos-backup-*' | wc -l | tr -d ' ')"
        if [[ "$out2" == *skipped* && "$sum1" == "$sum2" && "$nbackups" == "1" ]]; then
            pass
        else
            fail "out2=[$out2] sum_changed=$([[ "$sum1" == "$sum2" ]] && echo no || echo yes) nbackups=$nbackups"
        fi
        rm -rf "$tmp"
    fi
done

if it "ensure_serena_exclusions skips on the first run when all 15 are already present, any order (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    cat >"$cfg" <<'EOF'
excluded_tools:
- delete_memory
- create_text_file
- read_file
- execute_shell_command
- list_dir
- search_for_pattern
- find_file
- replace_content
- replace_in_files
- onboarding
- write_memory
- read_memory
- list_memories
- edit_memory
- rename_memory
EOF
    orig_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    out="$( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" 2>&1 )"
    new_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    nbackups="$(find "$tmp" -maxdepth 1 -name '*.autoos-backup-*' | wc -l | tr -d ' ')"
    if [[ "$out" == *skipped* && "$orig_sum" == "$new_sum" && "$nbackups" == "0" ]]; then
        pass
    else
        fail "out=$out changed=$([[ "$orig_sum" == "$new_sum" ]] && echo no || echo yes) nbackups=$nbackups"
    fi
    rm -rf "$tmp"
fi

# Defect: an earlier version captured python's stdout with `result="$(...)"`
# and blindly printf'd it over the config, so a directory at config_path (or
# a missing python3) silently truncated it to a stray newline. Both must now
# warn, return 0, and leave the target completely untouched.
if it "ensure_serena_exclusions on a directory path warns, returns 0, and writes nothing (serena)"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/adir"
    out="$( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/adir" 2>&1 )"; rc=$?
    nbackups="$(find "$tmp" -maxdepth 1 -name '*.autoos-backup-*' | wc -l | tr -d ' ')"
    ninside="$(find "$tmp/adir" -mindepth 1 | wc -l | tr -d ' ')"
    if [[ "$rc" == "0" && -d "$tmp/adir" && "$nbackups" == "0" && "$ninside" == "0" ]]; then
        pass
    else
        fail "rc=$rc nbackups=$nbackups ninside=$ninside out=$out"
    fi
    rm -rf "$tmp"
fi

if it "ensure_serena_exclusions without python3 on PATH warns, returns 0, and writes nothing (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf 'other_key: 1\n' >"$cfg"
    orig_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    out="$( has_cmd() { [[ "$1" != "python3" ]] && command -v "$1" >/dev/null 2>&1; }
            SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
            ensure_serena_exclusions "$cfg" 2>&1 )"; rc=$?
    new_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    if [[ "$rc" == "0" && "$new_sum" == "$orig_sum" ]]; then
        pass
    else
        fail "rc=$rc changed=$([[ "$orig_sum" == "$new_sum" ]] && echo no || echo yes) out=$out"
    fi
    rm -rf "$tmp"
fi

# CRLF and a raw non-UTF-8 byte can't live in a committed *.yml fixture -
# .gitattributes forces `*.yml text eol=lf`, which would silently rewrite a
# checked-in CRLF fixture to LF and defeat the point of the test. These two
# build their own bytes at run time instead.
if it "ensure_serena_exclusions preserves CRLF line endings and untouched content byte-for-byte (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf '# top comment\r\nother_key: 1\r\nexcluded_tools:\r\n- read_file\r\n- my_tool\r\nlast_key: x\r\n' >"$cfg"
    ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" ) >/dev/null 2>&1
    cr="$(tr -cd '\r' < "$cfg" | wc -c | tr -d ' ')"
    lf="$(tr -cd '\n' < "$cfg" | wc -c | tr -d ' ')"
    first_line="$(head -1 "$cfg" | tr -d '\r\n')"
    if [[ "$cr" == "$lf" && "$cr" -gt "0" && "$first_line" == "# top comment" ]]; then
        pass
    else
        fail "cr=$cr lf=$lf first_line=[$first_line]"
    fi
    rm -rf "$tmp"
fi

if it "ensure_serena_exclusions keeps each line's own ending outside the edited block (mixed CRLF/LF) (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf 'a_setting: 1\nb_setting: 2\r\nexcluded_tools:\r\n- read_file\r\ntrailer_key: keep_me\n' >"$cfg"
    ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" ) >/dev/null 2>&1
    if out="$(python3 - "$cfg" <<'PY' 2>&1
import sys
data = open(sys.argv[1], 'rb').read()
assert data.startswith(b'a_setting: 1\nb_setting: 2\r\nexcluded_tools:'), data
assert data.endswith(b'\ntrailer_key: keep_me\n') and not data.endswith(b'\r\n'), data
assert b'- delete_memory' in data, data
PY
)"; then pass; else fail "$out"; fi
    rm -rf "$tmp"
fi

if it "ensure_serena_exclusions preserves a non-UTF-8 byte outside the block untouched (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf 'excluded_tools:\n- read_file\n#comment with byte: ' >"$cfg"
    printf '\xe9' >>"$cfg"
    printf '\nlast_key: x\n' >>"$cfg"
    ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" ) >/dev/null 2>&1
    if python3 -c "
data = open('$cfg', 'rb').read()
assert b'#comment with byte: \xe9\nlast_key: x\n' in data, data
"
    then pass; else fail "the non-UTF-8 byte (or its surrounding bytes) did not survive untouched"; fi
    rm -rf "$tmp"
fi

if it "check-serena-tools has a SERENA_FROM constant used by the uvx fallback (serena)"; then
    if python3 - <<'PY'
import importlib.util
import json
import unittest.mock as mock

spec = importlib.util.spec_from_file_location("check_serena_tools", "tools/check-serena-tools.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

expected = json.load(open("catalog/agent-harness.json", encoding="utf-8"))["mcp_servers"]["serena"]["package"]
assert mod.SERENA_FROM == expected, (mod.SERENA_FROM, expected)
with mock.patch("shutil.which", return_value=None):
    cmd = mod.default_command()
assert cmd[:3] == ["uvx", "--from", mod.SERENA_FROM], cmd
PY
    then pass; else fail "SERENA_FROM constant missing, or not used by the uvx fallback"; fi
fi

if it "check-serena-tools judge() rejects memory/onboarding tools and requires find_symbol (serena)"; then
    if python3 - <<'PY'
import importlib.util, sys

spec = importlib.util.spec_from_file_location("check_serena_tools", "tools/check-serena-tools.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ok, problems = mod.judge(["find_symbol", "read_file"])
assert ok, f"expected ok, got problems: {problems}"

ok, problems = mod.judge(["find_symbol", "write_memory"])
assert not ok, "write_memory should have failed judge()"

# Regression: MEMORY_MARKER = "memory" does not match "memories", so
# list_memories - one of Serena's own tool names - slipped past judge().
ok, problems = mod.judge(["find_symbol", "list_memories"])
assert not ok, "list_memories should have failed judge()"

ok, problems = mod.judge(["read_file"])
assert not ok, "missing find_symbol should have failed judge()"
sys.exit(0)
PY
    then pass; else fail "judge() did not behave as expected (see above)"; fi
fi

if it "setup_openhands_config pins every MCP server it writes"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
m = json.load(open(os.path.join(sys.argv[1], "settings.json"), encoding="utf-8"))["agent_settings"]["mcp_config"]
# Expected specs come from the harness, the one source of truth for the pins.
pins = {k: v["package"] for k, v in json.load(open("catalog/agent-harness.json", encoding="utf-8"))["mcp_servers"].items()}
print(" ".join(k for k, v in pins.items() if v not in m[k]["args"]) or "all-pinned")
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "all-pinned"
fi

if it "setup_openhands_config hands omnigraph the token from the per-user env file"; then
    # The agent-server may run in a container or under systemd, neither of
    # which sees a token exported from an interactive shell rc.
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=http://localhost:8080\nOMNIGRAPH_TOKEN=file-token\n' >"$tmp/.autoos-omnigraph.env"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        curl() { return 6; }
        setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
e = json.load(open(os.path.join(sys.argv[1], "settings.json"), encoding="utf-8"))["agent_settings"]["mcp_config"]["omnigraph"]["env"]
print(e.get("OMNIGRAPH_TOKEN"), e.get("OMNIGRAPH_GRAPH_ID"))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "file-token autoos"
fi

if it "setup_openhands_config points the omnigraph bridge at the host, not at the container itself"; then
    # OpenHands runs in a container (compose: extra_hosts host.docker.internal:host-gateway),
    # so localhost:8080 there is the container and nothing listens on it. The
    # published omnigraph-server is reached through the host alias, like the
    # gateway URL the same file already carries (llm.base_url).
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        curl() { return 6; }
        setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
e = json.load(open(os.path.join(sys.argv[1], "settings.json"), encoding="utf-8"))["agent_settings"]["mcp_config"]["omnigraph"]["env"]
print(e.get("OMNIGRAPH_BASE_URL"))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "http://host.docker.internal:8080"
fi

