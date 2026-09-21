#!/usr/bin/env python3
"""AutoOS browser UI for headless machines.

Serves web/index.html and drives the real installer by shelling out to
./setup.sh, so the browser path and the terminal path cannot drift apart.

Security: binds 127.0.0.1 by default and always requires a per-run token. A
wider bind is opt-in and warned about, because this endpoint installs software.
"""
from __future__ import annotations

import errno
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
BIND = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
# Starting the server in dry-run LOCKS the whole session to preview-only: a safe
# way to hand someone the URL without handing them the ability to change the box.
FORCE_DRY = (sys.argv[4] if len(sys.argv) > 4 else "0") == "1"
TOKEN = secrets.token_urlsafe(24)

LOCK = threading.Lock()
LOG: list[dict] = []
RUN = {"running": False, "done": 0, "total": 0, "summary": "", "current": None}
STATE_LOCK = threading.Lock()
STATE_CACHE: dict | None = None
PROGRESS_PREFIX = "@@AUTOOS_PROGRESS "


_PLATFORMS_CACHE: dict[str, list[str]] | None = None


def component_platforms() -> dict:
    """id -> the platforms whose catalog contains it.

    Read from every catalog, not just this machine's, so the page can say
    "Windows only" instead of leaving the reader to guess whether something is
    missing here because it does not exist or because nobody added it yet.
    """
    global _PLATFORMS_CACHE
    if _PLATFORMS_CACHE is not None:
        return _PLATFORMS_CACHE

    out: dict[str, list[str]] = {}
    for name in ("windows", "linux", "macos"):
        path = ROOT / "catalog" / f"{name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for grp in data.get("categories", []):
            for c in grp.get("components", []):
                out.setdefault(c["id"], []).append(name)
    _PLATFORMS_CACHE = out
    return out


def provider_status() -> list:
    """Which AI providers have a key in configuration/api-keys.yml.

    Values never leave this function: the payload carries only the provider
    id, a display label and whether a key is present.
    """
    labels = {
        "groq": "Groq",
        "google_ai_studio": "Google AI Studio (Gemini)",
        "mistral": "Mistral",
        "cloudflare_workers_ai": "Cloudflare Workers AI",
        "cohere": "Cohere",
        "hugging_face": "Hugging Face",
        "cerebras": "Cerebras",
        "sambanova": "SambaNova",
        "deepseek": "DeepSeek",
        "meta": "Meta Model API",
        "openrouter": "OpenRouter",
        "zen": "OpenCode Zen",
        "cheapinference": "Cheaper Inference (paid partner)",
        "omniroute": "OmniRoute client key",
    }
    have = set()
    path = ROOT / "configuration" / "api-keys.yml"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or ":" not in t:
                continue
            key, _, val = t.partition(":")
            if val.strip().strip("\"'"):
                have.add(key.strip().lower())
    return [{"id": k, "name": v, "configured": k in have} for k, v in labels.items()]


def build_state() -> dict:
    """System info + catalog, produced by the same shell code the CLI uses."""
    probe = r"""
set -euo pipefail
cd "$AUTOOS_ROOT"
. lib/linux/ui.sh; . lib/linux/detect.sh; . lib/linux/catalog.sh; . lib/linux/install.sh
AUTOOS_NO_COLOR=1 ui_init
detect_system
catalog_load "catalog/${SYS_OS:-linux}.json" "$SYS_ARCH" "$SYS_IS_HEADLESS"
catalog_detect_installed
installed=()
for ((i=0; i<${#CAT_ID[@]}; i++)); do
    installed+=("${CAT_ID[i]}" "$(if (( CAT_INSTALLED[i] )); then printf installed; else printf not-detected; fi)")
done
python3 - "$SYS_DISTRO_NAME" "$SYS_ARCH" "$SYS_MODEL" "$SYS_CPU_NAME" "$SYS_CPU_CORES" \
          "$SYS_RAM_GB" "$SYS_FREE_DISK_GB" "$SYS_USER" "$SYS_IS_HEADLESS" \
          "$(suggested_profile)" "$(hostname)" "${SYS_ENVIRONMENT:-unknown}" \
          "${SYS_IS_WSL:-0}" "${SYS_WSL_VERSION:-}" "${SYS_WSL_DISTRO:-}" \
          "${SYS_OS:-linux}" "${installed[@]}" <<'PY'
import json, sys
k = sys.argv[1:]
print(json.dumps({
  "system": {
    "host": k[10], "distribution": k[0], "architecture": k[1], "model": k[2],
    "cpu": k[3], "cores": k[4], "memory": k[5] + " GB", "free disk": k[6] + " GB",
    "user": k[7], "display": "headless" if k[8] == "1" else "graphical",
    "environment": k[11],
  },
  "suggested": k[9],
  "wsl": {"isWsl": k[12] == "1", "version": k[13], "distro": k[14]},
  "platform": k[15],
  "installed": dict(zip(k[16::2], k[17::2])),
}))
PY
"""
    out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True,
                         cwd=str(ROOT), env={**os.environ, "AUTOOS_ROOT": str(ROOT)})
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-2000:] or "detection failed")
    info = json.loads(out.stdout.strip().splitlines()[-1])

    platform = info["platform"]
    catalog = json.loads((ROOT / "catalog" / f"{platform}.json").read_text(encoding="utf-8"))
    platforms = component_platforms()
    arch = info["system"]["architecture"]
    headless = info["system"]["display"] == "headless"
    installed_ids = {key for key, value in info["installed"].items() if value == "installed"}
    installed_names = []
    components = []
    for grp in catalog.get("categories", []):
        if grp.get("requiresDisplay") and headless:
            continue
        for c in grp.get("components", []):
            if c.get("arch") and arch not in c["arch"]:
                continue
            is_inst = c["id"] in installed_ids
            if is_inst:
                installed_names.append(c["name"])
            components.append({
                "id": c["id"], "name": c["name"], "description": c["description"],
                "provider": c["provider"], "package": c["package"],
                "profiles": c.get("profiles", []), "prompt": c.get("prompt"),
                "category": grp["name"],
                "requires": c.get("requires", []), "homepage": c.get("homepage"),
                "verify": c.get("verify"), "notes": c.get("notes"),
                "platforms": platforms.get(c["id"], [platform]),
                "installed": info["installed"].get(c["id"]) == "installed",
                "installedStatus": info["installed"].get(c["id"], "unknown"),
            })
    info["system"]["installed applications"] = (
        "✓ " + ", ".join(installed_names) + f" ({len(installed_names)} detected)"
        if installed_names else "none detected"
    )
    return {
        "platform": "macOS" if platform == "macos" else "Linux",
        "system": info["system"],
        "suggested": info["suggested"],
        "wsl": info.get("wsl", {"isWsl": False, "version": "", "distro": ""}),
        "profiles": catalog.get("profiles", {}),
        "prompts": catalog.get("prompts", {}),
        "components": components,
        "providers": provider_status(),
    }


def cached_state() -> dict:
    global STATE_CACHE
    with STATE_LOCK:
        if STATE_CACHE is None:
            STATE_CACHE = build_state()
        return STATE_CACHE


def record_line(line: str) -> None:
    """Structured snapshots count completions; ordinary text remains just a log."""
    with LOCK:
        if line.startswith(PROGRESS_PREFIX):
            try:
                event = json.loads(line[len(PROGRESS_PREFIX):])
                done, total = int(event["done"]), int(event["total"])
                if 0 <= done <= total:
                    RUN.update(done=done, total=total, current=event)
            except (ValueError, TypeError, KeyError):
                pass  # A damaged progress line cannot interrupt pipe draining.
            return
        LOG.append({"level": classify(line), "text": line})


def classify(line: str) -> str:
    t = line.strip()
    if t.startswith("+ "):
        return "ok"
    if t.startswith("! "):
        return "warn"
    if t.startswith("x "):
        return "err"
    if t.startswith("> "):
        return "step"
    if t.startswith(("run:", "would run:", "would ")):
        return "muted"
    return ""


def run_install(ids: list[str], answers: dict, dry: bool) -> None:
    global STATE_CACHE
    env = dict(os.environ, AUTOOS_NO_COLOR="1", AUTOOS_PROGRESS_EVENTS="1")
    for key, val in (answers or {}).items():
        if val:
            env["AUTOOS_ANSWER_" + key.upper().replace("-", "_")] = str(val)

    cmd = ["bash", "setup.sh", "--only", ",".join(ids), "--yes", "--no-color"]
    if dry:
        cmd.append("--dry-run")

    with LOCK:
        LOG.append({"level": "step", "text": "$ " + " ".join(cmd)})

    try:
        # Merging stderr into stdout drains both streams even during a noisy
        # installer. Decode imperfect vendor output without losing the worker.
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
        assert proc.stdout is not None
        for raw in proc.stdout:
            record_line(raw.rstrip("\r\n"))
        proc.wait()
        with LOCK:
            RUN["summary"] = "finished (exit %d)" % proc.returncode
            LOG.append({
                "level": "ok" if proc.returncode == 0 else "err",
                "text": "--- exit code %d ---" % proc.returncode,
            })
    except Exception as exc:
        with LOCK:
            RUN["summary"] = "installer failed: " + str(exc)
            LOG.append({"level": "err", "text": RUN["summary"]})
    finally:
        with STATE_LOCK:
            STATE_CACHE = None
        with LOCK:
            RUN["running"] = False


def _run_bash(script: str) -> subprocess.CompletedProcess:
    """Run a snippet with the repo's shell modules sourced, AUTOOS_ROOT set.

    Every AUTOOS_FAKE_* / AUTOOS_* testing knob usb.sh already understands
    (AUTOOS_FAKE_LSBLK, AUTOOS_FAKE_UID, ...) reaches this subprocess
    unchanged, because os.environ is inherited rather than replaced — the
    same reason a test can call usb_create_response() directly with
    AUTOOS_FAKE_LSBLK exported in its own shell and see it honoured here.
    """
    env = dict(os.environ, AUTOOS_ROOT=str(ROOT))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=str(ROOT), env=env)


_USB_SOURCE = '. lib/linux/ui.sh; . lib/linux/detect.sh; . lib/linux/download.sh; . lib/linux/usb.sh\n'


def usb_devices_response() -> dict:
    """Live USB candidates plus whether this process can actually write one.

    B10: elevation is surfaced here, not discovered at write time, so the
    browser can disable the create button with a reason instead of
    offering an action that would fail on "Access is denied".
    """
    listing = _run_bash('set -euo pipefail\ncd "$AUTOOS_ROOT"\n' + _USB_SOURCE + 'usb_list\n')
    devices = []
    if listing.returncode == 0:
        for line in listing.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            dev, model, size, rm, tran = parts[:5]
            try:
                size = int(size)
            except ValueError:
                size = 0
            devices.append({
                "device": dev, "model": model or "(unknown model)", "size": size,
                "removable": rm == "1", "bus": tran,
            })

    elevation = _run_bash('set -euo pipefail\ncd "$AUTOOS_ROOT"\n' + _USB_SOURCE + 'usb_require_elevation\n')
    elevated = elevation.returncode == 0
    reason = "" if elevated else (elevation.stderr or elevation.stdout or "not elevated").strip()
    return {"devices": devices, "elevated": elevated, "reason": reason}


def usb_images_response() -> dict:
    """Static catalog data the dialog needs: images plus this machine's engines.

    Filtered here (not in the page) so the page never has to know an
    engine's platform/arch rules, and so `rufus` (interactive: true) never
    reaches the browser at all - it needs a human at a GUI (task-11 brief).
    """
    images = json.loads((ROOT / "catalog" / "images.json").read_text(encoding="utf-8")).get("images", [])
    engines_all = json.loads((ROOT / "catalog" / "engines.json").read_text(encoding="utf-8")).get("engines", [])
    platform = "macos" if sys.platform == "darwin" else "linux"
    try:
        import platform as _platform
        arch = "arm64" if _platform.machine().lower() in ("arm64", "aarch64") else "x64"
    except Exception:
        arch = "x64"
    engines = [
        e for e in engines_all
        if not e.get("interactive") and platform in e.get("platforms", []) and arch in e.get("arch", [])
    ]
    return {"images": images, "engines": engines}


def _image_size_bytes(image_id: str) -> int:
    """catalog/images.json's sizeGb for <image_id>, in bytes, or 0.

    Finding F12: usb_guard's own size check (usb.sh:369) compares the
    target device against $AUTOOS_IMAGE_BYTES, defaulting to 0 - "unknown,
    never a refusal" - when the caller never told it. The CLI path
    (usb_plan, usb.sh:696) always computes this before calling usb_guard;
    this is the same lookup for the HTTP path, which used to call usb_guard
    with no AUTOOS_IMAGE_BYTES at all and so accepted a device smaller than
    the image with a 202. 0 (unknown image, missing/unreadable catalog, or
    no sizeGb on this entry - e.g. custom-url/custom-local) intentionally
    reproduces the old "never a refusal on size" behaviour rather than
    inventing a size limit this catalog entry never declared.
    """
    try:
        images = json.loads((ROOT / "catalog" / "images.json").read_text(encoding="utf-8")).get("images", [])
    except (OSError, ValueError):
        return 0
    entry = next((e for e in images if e.get("id") == image_id), None)
    if entry is None:
        return 0
    size_gb = entry.get("sizeGb")
    if not isinstance(size_gb, (int, float)):
        return 0
    return int(round(size_gb * 1_000_000_000))


def usb_create_response(body: dict) -> tuple[int, dict]:
    """Validate a USB-create request. Never writes to a device.

    MODULE-LEVEL, not a Handler method: Handler subclasses
    BaseHTTPRequestHandler and every one of its helpers takes `self` as the
    first argument, so `Handler._usb_create(body)` would receive the dict
    AS self and raise TypeError the moment a test - or do_POST - called it
    with a plain dict. classify() above is module-level for the identical
    reason, and this function follows the same rule.

    The device-safety check (usb_guard) runs before anything else, mirroring
    usb_guard's own "most dangerous mistake first" ordering: a bad device is
    refused before this function ever looks at whether the rest of the
    request is even well-formed. image/engine are read (but not yet
    validated as present) ahead of the guard call so their sizeGb/mode can
    feed that same guard call (finding F12) without disturbing this
    "guard first" order - the "image and engine are both required" check
    below still runs after the guard, exactly as before.
    """
    device = str(body.get("device") or "")
    if not device:
        return 400, {"error": "a target device is required"}

    image = str(body.get("image") or "")
    engine = str(body.get("engine") or "")
    image_bytes = _image_size_bytes(image)
    mode = "mounted-fat32-writable" if engine == "uefi-copy" else "unmounted"
    guard = _run_bash(
        'set -euo pipefail\ncd "$AUTOOS_ROOT"\n' + _USB_SOURCE +
        f"AUTOOS_IMAGE_BYTES={image_bytes} usb_guard {shlex.quote(device)} {shlex.quote(mode)}\n"
    )
    if guard.returncode != 0:
        reason = (guard.stderr or guard.stdout or "device refused by usb_guard").strip()
        return 400, {"error": reason}

    kind = str(body.get("kind") or "installer")
    if not image or not engine:
        return 400, {"error": "image and engine are both required"}

    with LOCK:
        if RUN["running"]:
            return 409, {"error": "a run is already in progress"}

    return 202, {"ok": True, "image": image, "kind": kind, "engine": engine, "device": device}


def run_usb_create(image: str, kind: str, engine: str, device: str) -> None:
    """Preview a USB write through the same plan-then-execute CLI path.

    Always --dry-run: task-11's constraints are explicit that the browser
    must never trigger a real write. usb_plan (which --create-usb runs)
    documents itself as emitting commands and running nothing, so this is
    belt-and-suspenders rather than the only thing standing between this
    button and a real write - but it is a cheap and explicit one.

    Finding F11': --wipe-target-disk used to be passed here too. setup.sh's
    --dry-run flag sets AUTOOS_DRY_RUN=1 itself (setup.sh:95), so this was
    never actually a live-wipe path as Gemini's review first framed it - but
    a browser PREVIEW acknowledging "the target device's current contents
    are lost" is gratuitous, and leaves exactly one missing flag between a
    preview and a real wipe. Dropped from the command line, and
    AUTOOS_DRY_RUN=1 is now also set directly in `env` (belt-and-suspenders
    alongside the --dry-run flag already on the command line) so nothing
    about this call depends on a single flag surviving unchanged.
    """
    global STATE_CACHE
    env = dict(os.environ, AUTOOS_NO_COLOR="1", AUTOOS_PROGRESS_EVENTS="1", AUTOOS_DRY_RUN="1")
    cmd = ["bash", "setup.sh", "--create-usb", "--image", image, "--kind", kind,
           "--engine", engine, "--usb-device", device,
           "--dry-run", "--yes", "--no-color"]

    with LOCK:
        LOG.append({"level": "step", "text": "$ " + " ".join(cmd)})

    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
        assert proc.stdout is not None
        for raw in proc.stdout:
            record_line(raw.rstrip("\r\n"))
        proc.wait()
        with LOCK:
            RUN["summary"] = "finished (exit %d)" % proc.returncode
            LOG.append({
                "level": "ok" if proc.returncode == 0 else "err",
                "text": "--- exit code %d ---" % proc.returncode,
            })
    except Exception as exc:
        with LOCK:
            RUN["summary"] = "usb create failed: " + str(exc)
            LOG.append({"level": "err", "text": RUN["summary"]})
    finally:
        with STATE_LOCK:
            STATE_CACHE = None
        with LOCK:
            RUN["running"] = False


def example_block(name):
    """One top-level block of autoos.config.example.json, or {}.

    The example is the single home for the shipped defaults, so the server reads
    them from there rather than restating them.
    """
    path = ROOT / "autoos.config.example.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(name) or {}
    except (OSError, ValueError):
        return {}


def detected_answers():
    """Answers we can read off the machine instead of asking for them.

    A prefilled field is only an improvement when the value is real; a plausible
    looking placeholder is worse than an empty box, because it reads as answered.
    """
    answers = {}
    for key, args in (("git_user_name", ["user.name"]), ("git_user_email", ["user.email"])):
        try:
            res = subprocess.run(["git", "config", "--global"] + args,
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        value = res.stdout.strip()
        if res.returncode == 0 and value:
            answers[key] = value
    return answers


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoOS"

    def log_message(self, *_args):  # keep the terminal clean
        pass

    def _authed(self, qs) -> bool:
        return secrets.compare_digest((qs.get("token") or [""])[0], TOKEN)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    # ── claude session state ────────────────────────────────────────────────
    # The engine is the single source for discovery and the state format; the
    # server only forwards it, so the card and the CLI can never disagree.

    def _claude_engine(self, command):
        """Run one engine command and return its stdout, or None."""
        script = ROOT / "lib" / "linux" / "claude_sessions.py"
        if not script.is_file():
            return None
        try:
            res = subprocess.run([sys.executable, str(script), command],
                                 capture_output=True, text=True, cwd=str(ROOT), timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return res.stdout if res.returncode == 0 else None

    def _claude_state(self):
        """What the card needs to answer "will my sessions come back?"."""
        state = {"sessions": [], "captured_at_iso": None}
        raw = self._claude_engine("state")
        if raw:
            try:
                parsed = json.loads(raw)
                state["sessions"] = parsed.get("sessions", [])
                state["captured_at_iso"] = parsed.get("captured_at_iso")
            except ValueError:
                pass
        unit = Path.home() / ".config" / "systemd" / "user" / "claude-sessions-restore.service"
        state["installed"] = unit.is_file()
        return state

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            page = (ROOT / "web" / "index.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")

        if not self._authed(qs):
            return self._json(403, {"error": "bad or missing token"})

        if u.path == "/api/state":
            try:
                return self._json(200, cached_state())
            except Exception as exc:  # surface the real reason to the page
                return self._json(500, {"error": str(exc)})

        if u.path == "/api/ping":
            # Deliberately the cheapest thing this server does: the page polls it
            # every couple of seconds to notice the moment this process goes away.
            return self._json(200, {"ok": True, "running": RUN["running"]})

        if u.path == "/api/log":
            try:
                offset = max(0, int((qs.get("offset") or ["0"])[0]))
            except ValueError:
                return self._json(400, {"error": "offset must be an integer"})
            with LOCK:
                offset = min(offset, len(LOG))
                lines = LOG[offset:]
                payload = {
                    "lines": lines, "offset": offset + len(lines),
                    "running": RUN["running"], "done": RUN["done"],
                    "total": RUN["total"], "summary": RUN["summary"],
                    "current": RUN["current"],
                }
            return self._json(200, payload)

        if u.path == "/api/config":
            cfg_file = ROOT / "autoos.config.json"
            if cfg_file.is_file():
                try:
                    data = json.loads(cfg_file.read_text(encoding="utf-8"))
                    return self._json(200, data)
                except Exception as exc:
                    return self._json(500, {"error": f"failed to read config: {exc}"})
            # No config yet. Seed it from the machine, never from the example
            # file: its answers are illustrative ("Your Name", "you@example.com")
            # and serving them puts fake identity in the form, where it looks
            # answered and gets saved as though it were real.
            return self._json(200, {
                "version": 1,
                "profile": "workstation",
                "answers": detected_answers(),
                "claude_autostart": example_block("claude_autostart"),
            })

        if u.path == "/api/claude/sessions":
            # `state`, never `snapshot`: a GET must not have side effects, and the
            # first version re-recorded the machine's sessions on every page load.
            return self._json(200, self._claude_state())

        if u.path == "/api/images":
            try:
                return self._json(200, usb_images_response())
            except Exception as exc:
                return self._json(500, {"error": str(exc)})

        if u.path == "/api/usb/devices":
            try:
                return self._json(200, usb_devices_response())
            except Exception as exc:
                return self._json(500, {"error": str(exc)})

        return self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._json(403, {"error": "bad or missing token"})
        if u.path == "/api/claude/snapshot":
            out = self._claude_engine("snapshot")
            if out is None:
                return self._json(500, {"error": "could not run the session snapshot"})
            payload = self._claude_state()
            payload["ok"] = True
            return self._json(200, payload)
        if u.path == "/api/config":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    return self._json(400, {"error": "payload must be a JSON object"})
                cfg_file = ROOT / "autoos.config.json"
                tmp_file = ROOT / "autoos.config.json.tmp"
                original = cfg_file.read_text(encoding="utf-8-sig") if cfg_file.exists() else None
                merged = json.loads(original) if original is not None else {}
                if not isinstance(merged, dict):
                    raise ValueError("Existing configuration must be an object")
                for key, value in body.items():
                    if key == "answers" and isinstance(value, dict) and isinstance(merged.get(key), dict):
                        merged[key].update(value)
                    else:
                        merged[key] = value
                if original is not None:
                    cfg_file.with_name(cfg_file.name + f".autoos-backup-{time.time_ns()}").write_text(original, encoding="utf-8")
                tmp_file.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
                tmp_file.replace(cfg_file)
                return self._json(200, {"ok": True, "saved": str(cfg_file)})
            except Exception as exc:
                return self._json(500, {"error": f"failed to save config: {exc}"})
        if u.path == "/api/usb/create":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "payload must be JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "payload must be a JSON object"})
            code, payload = usb_create_response(body)
            if code != 202:
                return self._json(code, payload)
            # Reserve before starting the worker, under the SAME lock /api/install
            # uses - a write must never run alongside an install, or another write.
            with LOCK:
                if RUN["running"]:
                    return self._json(409, {"error": "a run is already in progress"})
                LOG.clear()
                RUN.update(running=True, done=0, total=1, summary="", current=None)
            threading.Thread(
                target=run_usb_create,
                args=(payload["image"], payload["kind"], payload["engine"], payload["device"]),
                daemon=True,
            ).start()
            return self._json(202, {"started": True})

        if u.path != "/api/install":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        ids = [str(i) for i in body.get("ids", []) if i]
        if not ids:
            return self._json(400, {"error": "no components selected"})

        with LOCK:
            if RUN["running"]:
                return self._json(409, {"error": "a run is already in progress"})
            LOG.clear()
            # Reserve before starting the worker: two simultaneous POSTs must
            # never launch two package managers against the same machine.
            RUN.update(running=True, done=0, total=len(ids), summary="", current=None)
        dry = bool(body.get("dryRun", True)) or FORCE_DRY
        threading.Thread(
            target=run_install,
            args=(ids, body.get("answers", {}), dry),
            daemon=True,
        ).start()
        return self._json(202, {"started": True})


def main() -> int:
    # daemon_threads: a worker still streaming a log must not keep the process
    # alive after Ctrl-C. allow_reuse_address: restarting on the same port should
    # not fail with "address already in use" during TIME_WAIT. Both are class
    # attributes read at construction, so they are set before the bind loop below.
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.allow_reuse_address = True

    # Line-buffer stdout: when this is redirected to a file or a pipe (which is
    # exactly how a wrapper reads back the URL) Python block-buffers by default
    # and the connection details never appear until the process exits.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:  # pragma: no cover - Python < 3.7
        pass

    # A busy port is not a reason to make someone re-run the whole detect pass
    # with a --port flag. Walk forward until one is free, then print the URL that
    # actually works - a moved port is a note, not an error. Bind first: printing
    # a URL for a port we never got is how people end up debugging the wrong thing.
    srv, port = None, PORT
    for candidate in range(PORT, PORT + 20):
        try:
            srv = ThreadingHTTPServer((BIND, candidate), Handler)
            port = candidate
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
    if srv is None:
        print(f"  x Could not listen on port {PORT} or the 19 ports after it.")
        return 1

    url = f"http://{'localhost' if BIND == '127.0.0.1' else BIND}:{port}/?token={TOKEN}"
    print()
    if port != PORT:
        print(f"  ! Port {PORT} was already in use - serving on {port} instead.")
    print("  AutoOS browser UI")
    print("  " + "-" * 58)
    print(f"  {url}")
    print("  " + "-" * 58)
    if BIND != "127.0.0.1":
        print("  WARNING: bound to a non-loopback address. Anyone who can reach this")
        print("           port AND has the token above can install software here.")
    if FORCE_DRY:
        print("  Session is LOCKED to dry run - the browser cannot install anything.")
    print("  The token changes every run. Ctrl-C to stop.")
    print()
    # Re-arm SIGINT explicitly. A process started in the background by a
    # non-interactive shell inherits SIGINT as SIG_IGN, and CPython then leaves
    # it ignored - so Ctrl-C (or `kill -INT`) would do nothing at all. Handling
    # SIGTERM too means `kill` and service managers stop it cleanly as well.
    def _request_stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (AttributeError, ValueError):  # pragma: no cover - not on every platform
        pass

    # serve_forever() polls on a 0.5 s tick, so SIGINT lands promptly; shutting
    # down explicitly releases the socket instead of leaving it to interpreter exit.
    try:
        srv.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n  stopping the AutoOS browser UI...", flush=True)
    finally:
        # NOT srv.shutdown(): it blocks until serve_forever() signals that it
        # has stopped, and we are on the very thread that ran it - so calling it
        # here deadlocks and Ctrl-C appears to do nothing. serve_forever() has
        # already returned by this point; closing the socket is all that is left.
        srv.server_close()
        print("  server stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
