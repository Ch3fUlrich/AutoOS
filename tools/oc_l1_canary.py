"""oc_l1_canary.py - Canary probe for unattended OpenCode L1 lanes.

Runs a probe shell command through a throwaway canary session to verify that
the bash-guard plugin is active and denying dangerous execution before the
lane is declared unattended-capable.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.parse

try:
    from tools.oc_l1_http import _request, _data, _scrub, ServerDown
except ImportError:
    from oc_l1_http import _request, _data, _scrub, ServerDown

INCIDENT_COMMAND = "cat <<CANARY_EOF\ncanary `date`\nCANARY_EOF"
# The probe is the TOOL CALL: a model that only writes prose has told the guard
# nothing to deny. Name the tool and forbid a text-only reply (D-665).
CANARY_PROMPT = (
    "This is a guard self-test. Make EXACTLY ONE tool call, using the shell "
    "tool, and run this command verbatim:\n"
    f"{INCIDENT_COMMAND}\n"
    "Do NOT answer in text alone - a reply without that tool call fails the "
    "test. Once the call returns, answer with one short sentence."
)
DENIED_MARKER = "bash-guard: DENIED"
# The ONLY names the bash-guard hook inspects: the plugin's `execute.before`
# guard reads `e.tool !== "shell" && e.tool !== "bash"` (opencode renamed bash
# -> shell, so both spellings are hooked). `execute` is code-mode's executor
# and the guard never looks at its input, so a "denial" carried by it is the
# model echoing the marker - a false pass. tests/test_oc_l1_canary.py pins this
# tuple against the names parsed out of the plugin source (F1).
GUARDED_TOOL_NAMES = ("shell", "bash")


def _is_denial(err):
    """True only when the error text IS a bash-guard denial, not when it quotes one.

    The plugin throws `bash-guard: DENIED - <reason>` (index.mjs, and the
    orchestrator-role throw), so an anchored start is the whole contract. A
    substring test let any model-authored text that echoed the marker pass the
    canary, which is exactly the false pass the canary exists to prevent.
    Non-string payloads are not the plugin's throw shape, so they are not
    evidence of a denial either.
    """
    return isinstance(err, str) and err.strip().startswith(DENIED_MARKER)
# Distinct from "tool completed without denial": a prose-only reply says the
# model never let the guard run, which is not evidence about the guard.
INCONCLUSIVE_TEXT_ONLY = "inconclusive: text-only answer"


def _extract_port(base_url):
    """Normalize base_url or port into an integer port."""
    if isinstance(base_url, int):
        return base_url
    if isinstance(base_url, str):
        if base_url.isdigit():
            return int(base_url)
        if "://" not in base_url:
            base_url = "http://" + base_url
        parsed = urllib.parse.urlparse(base_url)
        if parsed.port:
            return parsed.port
    raise ValueError(f"Cannot extract port from {base_url!r}")


def _extract_password(auth):
    """Extract string password from string, callable, dict, or auth provider."""
    if isinstance(auth, str):
        return auth
    if callable(auth):
        res = auth()
        if isinstance(res, str):
            return res
        if isinstance(res, dict) and "Authorization" in res:
            auth = res
    if isinstance(auth, dict):
        header = auth.get("Authorization", "")
        if header.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                if ":" in decoded:
                    return decoded.split(":", 1)[1]
            except Exception:
                pass
    return ""


def _truncate(text, max_len=200):
    if not isinstance(text, str):
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def format_canary_line(canary):
    """Return formatted single-line canary status.

    The watcher logs only the launcher's LAST stdout line, so the reason has to
    travel on this line or it is lost (D-665: 461 refusals with no recorded
    reason). One line, no newlines inside.
    """
    denied = "yes" if canary.get("denied") else "no"
    ts = canary.get("ts")
    plugin = canary.get("plugin_path")
    detail = _truncate(str(canary.get("detail") or "")).replace("\n", " ")
    return f"canary denied={denied} ts={ts} plugin={plugin} detail={detail!r}"


def write_heartbeat(lane, canary):
    """Write or merge heartbeat.json for the lane atomically."""
    hb_path = lane.get("heartbeat_file")
    if not hb_path:
        hb_path = str(Path(lane["scratch_dir"]) / "heartbeat.json")
    p = Path(hb_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    data.setdefault("turn", 0)
    data.setdefault("last_tool_event_ts", None)
    data["ts"] = canary.get("ts")
    data["state"] = "started"
    data["current_step"] = "canary"
    data["canary"] = canary

    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def run_canary(base_url, auth, lane, now=None):
    """Run the canary session to check if bash-guard denies the incident command.

    Returns:
        dict: {
            "denied": bool,
            "ts": str (UTC ISO),
            "plugin_path": str | None,
            "session_id": str | None,
            "detail": str,
        }
    """
    if callable(now):
        ts = now()
    elif isinstance(now, str):
        ts = now
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    plugins = lane.get("plugins") or []
    plugin_path = plugins[0] if (isinstance(plugins, list) and plugins) else None

    result = {
        "denied": False,
        "ts": ts,
        "plugin_path": plugin_path,
        "session_id": None,
        "detail": "",
    }

    try:
        port = _extract_port(base_url)
        password = _extract_password(auth)
    except Exception as e:
        result["detail"] = _truncate(f"setup error: {e}")
        return result

    model_cfg = lane.get("model") or {}
    model_provider = model_cfg.get("provider", "anthropic")
    model_key = model_cfg.get("modelID")
    cwd = lane.get("cwd") or "."

    # 1. Create canary session
    session_body = {
        "title": f"{lane.get('name', 'l1')}-canary",
        "location": {"directory": cwd},
        "model": {"providerID": model_provider, "id": model_key},
    }

    try:
        status, payload = _request(port, "POST", "/api/session", body=session_body, password=password)
    except (ServerDown, Exception) as e:
        result["detail"] = _truncate(_scrub(f"create session failed: {e}", password))
        return result

    if status != 200:
        result["detail"] = f"create session returned HTTP {status}"
        return result

    data = _data(payload)
    if not isinstance(data, dict) or not data.get("id"):
        result["detail"] = "create session returned no session id"
        return result

    session_id = data["id"]
    result["session_id"] = session_id

    # 2. Post canary prompt
    prompt_body = {"text": CANARY_PROMPT}
    try:
        status, payload = _request(
            port, "POST", f"/api/session/{session_id}/prompt",
            body=prompt_body, password=password
        )
    except (ServerDown, Exception) as e:
        result["detail"] = _truncate(_scrub(f"post prompt failed: {e}", password))
        return result

    if status != 200:
        result["detail"] = f"post prompt returned HTTP {status}"
        return result

    # 3. Wait for turn to finish (polling GET /api/session/<id> outcome)
    timeout_s = float(lane.get("canary_timeout_s", 120))
    deadline = time.time() + timeout_s
    finished = False

    while time.time() < deadline:
        try:
            status, payload = _request(port, "GET", f"/api/session/{session_id}", password=password)
        except (ServerDown, Exception) as e:
            result["detail"] = _truncate(_scrub(f"poll session failed: {e}", password))
            return result

        if status == 200:
            sdata = _data(payload)
            if isinstance(sdata, dict) and sdata.get("outcome"):
                finished = True
                break
        time.sleep(0.5)

    if not finished:
        result["detail"] = f"canary timed out after {timeout_s:.0f}s"
        return result

    # 4. Read messages: GET /api/session/<id>/message?order=asc
    try:
        status, payload = _request(
            port, "GET", f"/api/session/{session_id}/message?order=asc",
            password=password
        )
    except (ServerDown, Exception) as e:
        result["detail"] = _truncate(_scrub(f"get messages failed: {e}", password))
        return result

    if status != 200:
        result["detail"] = f"get messages returned HTTP {status}"
        return result

    mdata = _data(payload)
    if not isinstance(mdata, list):
        result["detail"] = "get messages returned non-list data"
        return result

    shell_call_found = False
    tools_seen = []
    assistant_text = []
    for msg in mdata:
        if not isinstance(msg, dict):
            continue
        candidates = []
        content = msg.get("content")
        if isinstance(content, list):
            candidates.extend(content)
        if msg.get("type") == "tool":
            candidates.append(msg)

        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            tool_name = item.get("tool") or item.get("name")
            state = item.get("state") if isinstance(item.get("state"), dict) else {}
            if tool_name and tool_name not in tools_seen:
                tools_seen.append(tool_name)
            if item_type == "text":
                text_val = item.get("text")
                if isinstance(text_val, str) and text_val:
                    assistant_text.append(text_val)
            # F2: a guarded call is an item that NAMES a guarded tool. A
            # state-bearing item with no name, or with another tool's name,
            # proves nothing about the guard - the guard never saw it.
            if tool_name not in GUARDED_TOOL_NAMES:
                continue
            shell_call_found = True
            status_val = state.get("status")
            err = state.get("error")
            # F3: a denial is an error-status guarded call whose text is the
            # plugin's throw - the marker anchored at its start.
            if status_val == "error" and _is_denial(err):
                result["denied"] = True
                result["detail"] = _truncate(_scrub(err, password))
                return result
            if status_val == "completed":
                result["denied"] = False
                result["detail"] = "tool completed without denial"
                return result

    if not shell_call_found:
        # Name what the session actually produced: a rc=5 with no evidence of
        # what the model did is undiagnosable from the heartbeat alone. The
        # evidence is model output, so it is scrubbed like every other detail.
        evidence = _scrub(
            "tools seen: %s; assistant text: %s" % (
                ", ".join(tools_seen) or "none",
                _truncate(" ".join(assistant_text) or "none")),
            password)
        if assistant_text:
            # a prose-only reply proved nothing about the guard
            result["detail"] = "%s; %s" % (INCONCLUSIVE_TEXT_ONLY, evidence)
        else:
            result["detail"] = "no shell tool call in canary session; %s" % evidence
    else:
        result["detail"] = "shell call inconclusive"
    return result
