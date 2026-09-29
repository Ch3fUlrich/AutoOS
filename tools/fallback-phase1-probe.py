#!/usr/bin/env python3
"""FALLBACK-FLEET phase 1 probe: Claude Code harness on the local OmniRoute gateway.

Spec: docs/plans/2026-09-28-fallback-fleet-spec.md section 3. Non-Claude legs only.
The gateway key comes from api-keys.yml through autoos-agent's client_key(); it goes
into the child's environment only - never argv, never printed, redacted from logs.
The measured session is narrow: --permission-mode default, an explicit --allowedTools
list (Read, Edit, Skill), no MCP servers, a scratch worktree, MemoryMax=2G, a wall cap.

    python3 tools/fallback-phase1-probe.py --leg deepseek/deepseek-flash [--wall 300]

Leg spelling: use the gateway's wire model id (provider prefix per ai-registry
providers.<name>.model_prefix) - e.g. deepseek/deepseek-flash,
scw/qwen3-235b-a22b-instruct-2507. 'scw/' is the scaleway prefix: OS-30 (2026-09-29)
showed 'scaleway/qwen3-...' returning 400 while FREEKEYS-1 measured the same leg
200 (tool-call round trip included) as 'scw/...'.
"""
import argparse, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = "http://127.0.0.1:20128"
ALLOWED_TOOLS = "Read,Edit,Skill"


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", os.path.join(ROOT, "tools", "autoos-agent.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def post(path, body, key):
    req = urllib.request.Request(GATEWAY + path, data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
        "authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def endpoint_check(leg, key):
    """Anthropic-format /v1/messages: tool_use round trip and count_tokens."""
    out = {}
    tool = {"name": "get_weather", "description": "weather for a city",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}
    msgs = [{"role": "user", "content": "What is the weather in Paris? Use the tool."}]
    st, r = post("/v1/messages", {"model": leg, "max_tokens": 300, "tools": [tool], "messages": msgs}, key)
    uses = [b for b in r.get("content", []) if b.get("type") == "tool_use"] if st == 200 else []
    out["messages_status"] = st
    if st != 200:
        out["messages_error"] = str(r.get("error", ""))[:300]
    out["shape_ok"] = st == 200 and r.get("type") == "message" and r.get("role") == "assistant"
    out["tool_use"] = bool(uses) and uses[0].get("name") == "get_weather" and "paris" in json.dumps(uses[0].get("input", {})).lower()
    out["stop_reason"] = r.get("stop_reason")
    out["model_echo"] = r.get("model")
    if uses:
        msgs += [{"role": "assistant", "content": r["content"]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": uses[0]["id"], "content": "18C"}]}]
        st2, r2 = post("/v1/messages", {"model": leg, "max_tokens": 300, "tools": [tool], "messages": msgs}, key)
        out["round_trip"] = st2 == 200 and "18" in json.dumps(r2.get("content", []))
        if st2 != 200:
            out["round_trip_error"] = str(r2.get("error", ""))[:300]
    st3, _ = post("/v1/messages/count_tokens", {"model": leg, "messages": [{"role": "user", "content": "hello"}]}, key)
    out["count_tokens_status"] = st3
    return out


def harness_run(leg, key, wall):
    work = tempfile.mkdtemp(prefix="fb1-")
    cfg = os.path.join(work, "cfgdir")
    repo = os.path.join(work, "repo")
    os.makedirs(cfg)
    os.makedirs(os.path.join(repo, ".claude", "skills", "probe-skill"))
    marker = os.path.join(work, "hook.log")
    with open(os.path.join(repo, "target.txt"), "w") as f:
        f.write("status: TODO\n")
    with open(os.path.join(repo, ".claude", "skills", "probe-skill", "SKILL.md"), "w") as f:
        f.write("---\nname: probe-skill\ndescription: Use when asked for the probe word.\n---\nThe probe word is PINEAPPLE.\n")
    with open(os.path.join(repo, ".claude", "settings.json"), "w") as f:
        json.dump({"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "echo fired >> " + marker}]}]}}, f)
    subprocess.run(["git", "init", "-q"], cwd=repo)
    env = {"PATH": os.environ["PATH"], "HOME": work, "CLAUDE_CONFIG_DIR": cfg, "ANTHROPIC_BASE_URL": GATEWAY,
           "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_MODEL": leg, "ANTHROPIC_SMALL_FAST_MODEL": leg,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": leg, "ANTHROPIC_DEFAULT_SONNET_MODEL": leg,
           "ANTHROPIC_DEFAULT_OPUS_MODEL": leg, "DISABLE_TELEMETRY": "1",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    # OS-30 (2026-09-29): the allowlisted env above stripped the caller's systemd
    # user-session bus vars, so `systemd-run --user --scope` died at once with
    # "Failed to connect to bus: No medium found" and claude never launched.
    # Pass the two path-only vars through (never a secret).
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env["DBUS_SESSION_BUS_ADDRESS"] = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or ("unix:path=" + runtime_dir + "/bus")
    task = ("Read target.txt, change the line to 'status: DONE' with the Edit tool, then invoke the probe-skill "
            "skill and finish with one line: the probe word and the new file content.")
    cmd = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=2G", "timeout", str(wall), "claude", "-p", task,
           "--output-format", "stream-json", "--verbose", "--strict-mcp-config", "--permission-mode", "default",
           "--allowedTools", ALLOWED_TOOLS, "--max-turns", "12"]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True)
    wall_s = round(time.time() - t0, 1)
    raw = (p.stdout or "").replace(key, "<KEY>")
    tools, models, result = [], set(), None
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        m = ev.get("message") or {}
        if m.get("model"):
            models.add(m["model"])
        for b in m.get("content", []) if isinstance(m.get("content"), list) else []:
            if b.get("type") == "tool_use":
                tools.append({"name": b.get("name"), "arg_keys": sorted((b.get("input") or {}).keys())})
        if ev.get("type") == "result":
            result = ev
    with open(os.path.join(repo, "target.txt")) as f:
        final = f.read().strip()
    fired = 0
    if os.path.exists(marker):
        with open(marker) as f:
            fired = len(f.read().split())
    res = {"leg": leg, "exit": p.returncode, "wall_s": wall_s, "tool_calls": tools, "models_answering": sorted(models),
           "file_after": final, "edit_correct": final == "status: DONE", "hook_fired": fired,
           "skill_tool_used": any(t["name"] == "Skill" for t in tools),
           "probe_word_in_result": "PINEAPPLE" in json.dumps((result or {}).get("result", "")),
           "result_is_error": (result or {}).get("is_error"),
           "stderr_tail": (p.stderr or "").replace(key, "<KEY>")[-300:]}
    shutil.rmtree(work, ignore_errors=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", required=True)
    ap.add_argument("--wall", type=int, default=300)
    ap.add_argument("--endpoint-only", action="store_true")
    a = ap.parse_args()
    if re.search(r"claude|opus|sonnet|haiku", a.leg, re.I):
        sys.exit("refused: non-Claude legs only (D-102/SBC)")
    key = load_agent().client_key(ROOT)
    if not key:
        sys.exit("no gateway key (api-keys.yml omniroute)")
    out = {"endpoint": endpoint_check(a.leg, key)}
    if not a.endpoint_only:
        out["harness"] = harness_run(a.leg, key, a.wall)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
