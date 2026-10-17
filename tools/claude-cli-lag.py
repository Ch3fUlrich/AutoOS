#!/usr/bin/env python3
"""claude-cli-lag — does this host run the newest Claude Code, and is its
auto-update still switched on?

CLIPIN / D-137. The policy this tool serves is the opposite of a version pin:
every host runs the latest published Claude Code and the autoupdater stays on
(nobody sets ``DISABLE_AUTOUPDATER``). The tool only *reports* — it never
installs, updates, or edits a host's Claude settings.

For the host it runs on it prints one block: the installed version
(``claude --version``), the newest published release (the npm registry, cached
for an hour), whether this host lags, the autoupdater state read from
``DISABLE_AUTOUPDATER`` in the environment or in the ``env`` block of
``~/.claude/settings.json`` (``%USERPROFILE%\\.claude\\settings.json`` on
Windows), and the settings file it looked at. When the host lags, the status
line says so and reminds the reader that an auto-update is picked up on restart.

Two outcomes that are not failures: an unreachable registry or a missing
``claude`` binary prints ``unknown`` and exits 0. Only a confirmed lag exits 1.

After an installed version that differs from the last one recorded in the
state file, it prints a recommendation (never a gate) to re-run the cheap spec
behaviour checks — ORCH-A1 §3.3 and the HOOKS guard contracts. Claude Code
re-ships its permission matcher and hook payloads with every release, so those
two contracts are the first thing a version change can break.

Usage:
    python3 tools/claude-cli-lag.py                 # this host
    python3 tools/claude-cli-lag.py --settings /mnt/c/Users/<profile>/.claude/settings.json
    python3 tools/claude-cli-lag.py --offline       # skip the registry call

Exit codes: 0 up to date / ahead / unknown, 1 this host lags behind the release.

Run it on every host that carries a Claude subscription; the state file lives
in the repository's git-ignored ``logs/`` (override with ``AUTOOS_STATE_DIR``).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REGISTRY_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"
PACKAGE = "@anthropic-ai/claude-code"
CACHE_TTL_SECONDS = 3600
DEFAULT_TIMEOUT = 10.0
STATE_BASENAME = "claude-cli-lag.json"
CACHE_BASENAME = "claude-cli-lag-registry.json"
VERSION_CMD = ["claude", "--version"]
ENV_FLAG = "DISABLE_AUTOUPDATER"

# The two contracts a version change can break, named exactly as the specs name them.
CHECK_SECTION_A1 = ("ORCH-A1 §3.3 deny-over-allow, always-deny fences "
                    "(docs/plans/2026-09-28-orch-a1-role-launch-profiles-spec.md)")
CHECK_SECTION_HOOKS = ("HOOKS §6 guard contracts and the §2 Claude Code hook inventory "
                       "(docs/plans/2026-09-28-agent-hooks-spec.md)")

_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.+-]+)?")
_TRUTHY = ("1", "true", "yes", "on")


def hostname() -> str:
    """The host this report is about — its own name, never an inventory entry."""
    return socket.gethostname() or "this-host"


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_state_dir(env=None) -> str:
    """Run state stays inside the repository, in the git-ignored logs/ folder
    (the operator's order for all AutoOS run state; AUTOOS_STATE_DIR moves it)."""
    env = os.environ if env is None else env
    override = (env.get("AUTOOS_STATE_DIR") or "").strip()
    if override:
        return override
    return os.path.join(repo_root(), "logs")


def state_paths(state_dir: str) -> tuple[str, str]:
    return (os.path.join(state_dir, STATE_BASENAME),
            os.path.join(state_dir, CACHE_BASENAME))


def parse_version(text) -> str | None:
    """First version-shaped token of ``claude --version`` output ('2.1.284 (Claude Code)')."""
    if not text:
        return None
    match = _VERSION_RE.search(text)
    return match.group(0) if match else None


def capture_output(cmd, timeout=DEFAULT_TIMEOUT):
    """(stdout, error) of a command list. Never raises, never uses a shell."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "%s: %s" % (type(exc).__name__, getattr(exc, "strerror", None) or exc)
    if proc.returncode != 0:
        return None, "exit %d" % proc.returncode
    return proc.stdout, None


def installed_version(cmd=None, timeout=DEFAULT_TIMEOUT) -> str | None:
    """What `claude --version` says here, or None when it cannot be asked."""
    out, _ = capture_output(cmd or VERSION_CMD, timeout=timeout)
    return parse_version(out)


def _parts(version: str) -> tuple:
    """('2.2.0-beta.1') -> ((2,2,0), 'beta.1') — release tuple plus prerelease."""
    head, _, pre = version.partition("-")
    head = head.split("+", 1)[0]
    bits = []
    for chunk in head.split("."):
        bits.append(int(chunk) if chunk.isdigit() else 0)
    while len(bits) < 3:
        bits.append(0)
    return tuple(bits), pre


def compare_versions(left, right) -> int:
    """-1 / 0 / 1. A prerelease sorts before its release, like npm does."""
    if left is None or right is None:
        raise ValueError("compare_versions needs two versions")
    l_rel, l_pre = _parts(left)
    r_rel, r_pre = _parts(right)
    if l_rel != r_rel:
        return -1 if l_rel < r_rel else 1
    if l_pre == r_pre:
        return 0
    if not l_pre:          # a release outranks any prerelease of the same number
        return 1
    if not r_pre:
        return -1
    return -1 if l_pre < r_pre else 1


def assess(installed, newest) -> str:
    """lags / up-to-date / ahead / unknown — unknown is never a failure."""
    if not installed or not newest:
        return "unknown"
    order = compare_versions(installed, newest)
    if order < 0:
        return "lags"
    if order > 0:
        return "ahead"
    return "up-to-date"


def read_json_object(path):
    """A JSON object from disk, or None. A missing, unreadable, truncated or
    non-object file is simply no answer — this tool must never crash a host."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path, payload) -> bool:
    directory = os.path.dirname(os.path.abspath(path))
    tmp = path + ".tmp"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def urlopen_json(url: str, timeout: float):
    """GET a URL and parse it as JSON. Raises on any transport or parse failure."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def read_cache(path, now: float, ttl: float = float(CACHE_TTL_SECONDS)) -> str | None:
    """The cached newest version, only while it is still inside the TTL."""
    cached = read_json_object(path)
    if not cached:
        return None
    try:
        age = now - float(cached.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None
    if age < 0 or age > ttl:
        return None
    version = cached.get("version")
    return version if isinstance(version, str) and version else None


def newest_release(cache_path=None, url: str = REGISTRY_URL, now=None,
                   timeout: float = DEFAULT_TIMEOUT, fetch=None,
                   ttl: float = float(CACHE_TTL_SECONDS), offline: bool = False,
                   no_cache: bool = False):
    """(version | None, how_we_know). The registry answers once an hour; after
    that the cache answers; when neither can, the answer is unknown."""
    now = time.time() if now is None else now
    fetch = urlopen_json if fetch is None else fetch
    cached = None if no_cache else read_cache(cache_path, now, ttl)
    if cached:
        return cached, "cache (npm registry, under %ds old)" % int(ttl)
    if offline:
        return cached or None, ("cache (npm registry, offline)" if cached
                                else "unknown (offline; registry call skipped)")
    try:
        payload = fetch(url, timeout)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        reason = type(exc).__name__
        if cached:
            return cached, "cache (npm registry, refreshed failed: %s)" % reason
        return None, "unknown (offline; registry unreachable: %s)" % reason
    version = payload.get("version") if isinstance(payload, dict) else None
    if not (isinstance(version, str) and version):
        return (cached, "cache (npm registry, no version field)") if cached else \
               (None, "unknown (npm registry answered no version)")
    if not no_cache:
        write_json_atomic(cache_path, {"package": PACKAGE, "version": version,
                                       "fetched_at": now})
    return version, "npm registry (live)"


def settings_path(env=None, os_name=None, override=None) -> str | None:
    """Where this host keeps `~/.claude/settings.json` — read-only, never written.

    Windows is the odd one: the file lives under `%USERPROFILE%`, not under
    `$HOME`. WSL hosts see the Linux path for their own distro; the Windows-side
    file has to be named with --settings (or AUTOOS_CLAUDE_SETTINGS), because
    guessing a profile directory across /mnt would read the wrong machine.
    """
    env = os.environ if env is None else env
    os_name = os.name if os_name is None else os_name
    explicit = override or env.get("AUTOOS_CLAUDE_SETTINGS") or ""
    if explicit.strip():
        return explicit.strip()
    base = (env.get("USERPROFILE") if os_name == "nt" else env.get("HOME")) or ""
    if not base.strip():
        return None
    return os.path.join(base.strip(), ".claude", "settings.json")


def _flag_disables(value) -> bool:
    """DISABLE_AUTOUPDATER only bites when it carries a truthy value. A JSON
    `true` is as disabling as the shell string "1"; "0"/""/"false" are not."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in _TRUTHY


def autoupdater_state(env=None, settings_path=None) -> tuple[str, str | None]:
    """('on' | 'off', where). The setting is read, never changed — a host's
    Claude configuration is not this tool's to edit."""
    env = os.environ if env is None else env
    if _flag_disables(env.get(ENV_FLAG)):
        return "off", "env"
    stored = read_json_object(settings_path)
    block = stored.get("env") if stored else None
    if isinstance(block, dict) and _flag_disables(block.get(ENV_FLAG)):
        return "off", settings_path
    return "on", None


def read_state(path) -> dict:
    return read_json_object(path) or {}


def note_version_change(installed, path, now=None):
    """(changed, previous) against the last version this host reported.

    The first run has nothing to compare against, so it records and stays quiet.
    An unknown installed version neither changes nor overwrites the record —
    one offline run must not erase the last thing we actually knew.
    """
    previous = read_state(path).get("installed")
    previous = previous if isinstance(previous, str) and previous else None
    if not installed:
        return False, previous
    changed = bool(previous) and previous != installed
    write_json_atomic(path, {"installed": installed,
                             "seen_at": time.time() if now is None else now})
    return changed, previous


def recommendation(previous: str, installed: str) -> str:
    return ("note: Claude Code moved %s -> %s on this host. Re-run the cheap spec "
            "behaviour checks: %s; %s. A recommendation, not a gate - the auto-update "
            "still applies on restart and nothing here blocks a lane."
            % (previous, installed, CHECK_SECTION_A1, CHECK_SECTION_HOOKS))


def lines_for(host, installed, newest, source, verdict, updater, where,
              settings=None) -> list:
    """The report block. `verdict` already decides the exit code."""
    if newest is not None:
        shown = "%s [%s]" % (newest, source)
    else:
        # `source` already reads "unknown (offline; …)" - do not wrap it twice.
        shown = source
    if verdict == "lags":
        status = "lags - restart picks it up (auto-update applies on restart)"
    elif verdict == "ahead":
        status = "ahead of the published npm release (%s)" % newest
    elif verdict == "up-to-date":
        status = "up to date"
    else:
        status = "unknown - nothing to compare"
    updater_line = "on" if updater == "on" else "off (set in %s)" % (where or "env")
    block = [
        "claude-cli-lag: host %s" % host,
        "  installed:   %s" % (installed or "unknown (claude --version gave no version)"),
        "  newest:      %s" % shown,
        "  status:      %s" % status,
        "  autoupdater: %s" % updater_line,
        "  settings:    %s" % (settings or "not found (no HOME / USERPROFILE)"),
    ]
    return block


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-cli-lag.py",
        description="Report this host's Claude Code version against the newest "
                    "published release, plus the autoupdater state. Read-only; "
                    "no version is ever pinned.")
    parser.add_argument("--claude", metavar="PATH", default=None,
                        help="claude binary to ask (default: claude on PATH)")
    parser.add_argument("--settings", metavar="PATH", default=None,
                        help="path to .claude/settings.json (WSL: name the Windows-side file here)")
    parser.add_argument("--state-dir", metavar="DIR", default=None,
                        help="where the state and cache files live (default: the repo's logs/)")
    parser.add_argument("--registry-url", metavar="URL", default=REGISTRY_URL)
    parser.add_argument("--offline", action="store_true",
                        help="skip the registry call (still uses a cache under the TTL)")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore and do not write the cache file")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return parser


def main(argv=None, env=None, out=None, os_name=None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env
    stream = sys.stdout if out is None else out
    state_dir = args.state_dir or default_state_dir(env)
    state_path, cache_path = state_paths(state_dir)

    cmd = [args.claude, "--version"] if args.claude else VERSION_CMD
    installed = installed_version(cmd, timeout=args.timeout)
    newest, source = newest_release(cache_path=cache_path, url=args.registry_url,
                                    timeout=args.timeout, offline=args.offline,
                                    no_cache=args.no_cache)
    verdict = assess(installed, newest)
    path = settings_path(env=env, os_name=os_name, override=args.settings)
    updater, where = autoupdater_state(env=env, settings_path=path)

    for line in lines_for(hostname(), installed, newest, source, verdict, updater,
                          where, settings=path):
        print(line, file=stream)

    changed, previous = note_version_change(installed, state_path)
    if changed:
        print(recommendation(previous, installed), file=stream)
    stream.flush()
    return 1 if verdict == "lags" else 0


if __name__ == "__main__":
    sys.exit(main())
