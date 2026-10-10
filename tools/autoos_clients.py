"""Client adapters for tools/autoos-agent.py: one headless command per agent CLI.

Each adapter declares what the `list` matrix prints - headless, routed through
OmniRoute, native sub-agents, auth source (an env var NAME or a login step,
never a value) - and builds its command for one task. Gateway clients other
than opencode go through `omniroute run <target>`, which writes a temporary
config overlay for the child and passes the client key by env var name.
agy and qoder cannot use the gateway: they run on their own account login.

Permission levels map onto each CLI's own modes:
    read  - role=review: plan / read-only, no edits
    seat  - an --isolate read run: the same reviewer, in a private clone it is
            allowed to write its report into (see `seat_level`)
    edit  - the default (--auto): file edits approved, shell asks (and so
            fails headless) unless the CLI's sandbox allows it
    ask   - --no-auto: the CLI's default prompting

Depth budget: every child gets AUTOOS_AGENT_DEPTH (its own depth, 1 = spawned
by a human or a top-level session) and AUTOOS_AGENT_MAX_DEPTH. A spawn past the
max is refused - the only depth control qwen, gemini, codex, agy and qoder
have. opencode's in-process nesting is still experimental.subagent_depth.
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

DEFAULT_MAX_DEPTH = 2  # = opencode.jsonc experimental.subagent_depth
PROBE_STALE_DAYS = 7   # ADR 0006 Q1
SIGNIN_TIMEOUT = 15    # seconds; `agy models` answers in < 1 s either way


@dataclass(frozen=True)
class Client:
    name: str
    binary: str
    headless: bool
    gateway: bool
    subagents: bool
    auth: str
    notes: str = ""
    promo: bool = False
    modes: dict = field(default_factory=dict)
    signin_probe: tuple = ()  # args that fail fast when the own-account login is missing


CLIENTS = {c.name: c for c in (
    Client("opencode", "opencode", True, True, True, "AUTOOS_OMNIROUTE_KEY",
           "tier agents from opencode.jsonc; --isolate adds the outside-path fence"),
    Client("claude", "claude", True, False, True, "claude login (subscription)",
           "-p headless; --joinable = --bg --remote-control session",
           modes={"read": ["--permission-mode", "plan"],
                  # AO-L2-SEAT-INTEGRITY P3b (measured, run 20261009-165450-mem-tools-
                  # sonnet-68764c): a review SEAT in a private clone ran `plan` and could
                  # not write its own REVIEW-FINDINGS.txt. `acceptEdits` writes in there.
                  "seat": ["--permission-mode", "acceptEdits"],
                  "edit": ["--permission-mode", "acceptEdits"]}),
    Client("qwen", "qwen", True, True, True, "AUTOOS_OMNIROUTE_KEY (omniroute run)",
           modes={"read": ["--approval-mode", "plan"], "edit": ["--approval-mode", "auto-edit"]}),
    Client("gemini", "gemini", True, True, False, "AUTOOS_OMNIROUTE_KEY (omniroute run)",
           modes={"read": ["--approval-mode", "plan"], "edit": ["--approval-mode", "auto_edit"]}),
    Client("codex", "codex", True, True, False, "AUTOOS_OMNIROUTE_KEY (omniroute run)",
           "codex exec",
           modes={"read": ["--sandbox", "read-only"], "edit": ["--sandbox", "workspace-write"]}),
    Client("agy", "agy", True, False, False, "agy login (own Google account)",
           "own auth, not the gateway; headless only after one interactive login",
           signin_probe=("models",)),
    Client("qoder", "qodercli", True, False, True, "qodercli login (own account)",
           "own auth, not the gateway; promo - privacy=public only", promo=True,
           # qodercli 1.1.63 modes are bypass_permissions|dont_ask|auto only; the
           # former "accept_edits" does not exist and refused every write (62 runs,
           # 2026-09-27). bypass_permissions is safe only because the spawner
           # forces an --isolate sandbox for every qoder writer (autoos-agent.py).
           modes={"read": ["--permission-mode", "dont_ask"], "edit": ["--permission-mode", "bypass_permissions"]}),
)}

# AGYFIX item 2 (K3 CLI audit addendum 2026-09-27 08:1xZ): agy with no --model
# runs its own default Gemini, whose quota is out until ~2026-10-01 (429 after
# ~157 s, exit 3). claude-opus-4-6-thinking measured PONG in 8 s, so the
# spawner supplies it when the caller gives no model. clients.agy.default_model
# would be the home for this, but the clients schema (catalog/ai-registry
# .schema.json $defs.client, additionalProperties false) has no such slot; a
# constant is the brief's documented fallback rather than widening the schema.
# CLAUDE55 2026-10-05 (operator): the 4-6 generation is retired upstream - the
# agy CLI's own model list (measured 2026-10-05) carries only the 5-5
# generation - so the default moves to claude-opus-5-5-medium (the balanced
# rung of the replacement model).
AGY_DEFAULT_MODEL = "claude-opus-5-5-medium"

# Qoder's free model on the operator's account (qodercli --list-models: Efficient,
# Qwen3.8-Max, Qwen3.8-Flash; Max needs credit). Measured 2026-09-27: writes a file
# and runs a shell command unattended in 25 s with bypass_permissions.
QODER_DEFAULT_MODEL = "Qwen3.8-Flash"

# The account's own keys for that default model — what qodercli calls it when it
# speaks for itself. Measured in the FAMILYFENCE-b block below: a transcript writes
# the internal key ("qfmodel") and the fallback line names the display rung
# ("efficient"), and neither is a spelling a registry can place. The list exists so
# "this id IS the account's default" is a lookup rather than a guess: an id outside
# it is a model the caller pinned, and the default's vendor is not a fact about it.
CLIENT_DEFAULT_KEYS = {
    "qoder": ("qfmodel", "efficient"),
}

# --- FAMILYFENCE-b: what the client itself recorded about the model it used ----
#
# An own-account client has no gateway to ask, and its argv is only what was
# ASKED for. Measured 2026-09-29 on qodercli 1.1.63: `--model NoSuchModel99`
# prints
#   [config] Model "NoSuchModel99" is not in the loaded catalog; falling back to
#   default model "efficient" for this session.
#   feedback.emitted Model "NoSuchModel99" is not available right now; using
#   "efficient" instead.
# and exits 0. So argv proves the request and nothing else. The client does keep
# the answer, in files the spawner had never read:
#
#   ~/.qoder/projects/<project>/<session-id>.jsonl   one row per event: the
#       assistant rows carry message.model, and a `runtime-config` row states the
#       session's resolved model — as the account's internal key ("qfmodel"), not
#       the name a registry can place ("Qwen3.8-Flash").
#   ~/.qoder/logs/runs/<run>/manifest.json           the argv, which is how a run
#       directory is joined to the session id this spawner minted for it.
#   ~/.qoder/logs/runs/<run>/qodercli.log            the account's own
#       [QoderInferRequest details] model_config={"key":..,"display_name":..}
#       lines, which turn a key back into a name.
#   ~/.claude/projects/<project>/<session-id>.jsonl  the same transcript shape;
#       message.model is already a full model id ("claude-sonnet-5-5"), so it
#       needs no map.
#
# Both clients take the id from the caller (`qodercli --help`:
# `--session-id <id>`; `claude --help`: `--session-id <uuid>  Use a specific
# session ID for the conversation (must be a valid UUID)`), which is what makes
# the join exact instead of "the newest file in there". A client with no row here
# has no evidence read for it, and the writer record says so in `source`.
MODEL_REPORT = {
    "qoder": {"projects": (".qoder", "projects"), "aliases": (".qoder", "logs", "runs")},
    "claude": {"projects": (".claude", "projects")},
}
# Run directories are scanned newest-first and joined through their manifest, so
# the bound is "how far back a finished run could plausibly be", not a guess at
# the host's clock.
RUN_SCAN_LIMIT = 24
RUN_LOG_SCAN_BYTES = 1 << 20
# A session id is a uuid, and it is about to be part of a glob: a value with a
# metacharacter in it would reach outside the client's own tree.
SESSION_ID_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{6,63}$")


def transcript_path(client_name: str, session_id: str | None, home: str) -> str | None:
    """The client's own transcript file for `session_id`, or None.

    The project directory's name is a mangled cwd, so the id is looked up across
    the client's project roots rather than recomputed here — a rule about a
    vendor's path encoding is not this file's to own.
    """
    row = MODEL_REPORT.get(client_name)
    if not row or not session_id or not SESSION_ID_RE.match(str(session_id)):
        return None
    pattern = os.path.join(home, *row["projects"], "*", str(session_id) + ".jsonl")
    matches = sorted(glob.glob(pattern))
    return matches[0] if matches else None


def transcript_models(path: str) -> tuple:
    """(last assistant model, last runtime-config model) of one transcript.

    The assistant row is the stronger witness — a reply came back from that model
    — and `runtime-config` is what a session states before its first reply, which
    covers a run that died before answering. The LAST of each, for the reason
    `gateway_writer` takes the newest call-log row: a session that was continued
    or re-routed is asked what served it in the end.
    """
    assistant = runtime = None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("type")
                if kind == "assistant":
                    message = row.get("message")
                    value = message.get("model") if isinstance(message, dict) else None
                elif kind == "runtime-config":
                    value = row.get("model")
                else:
                    continue
                if isinstance(value, str) and value.strip():
                    if kind == "assistant":
                        assistant = value.strip()
                    else:
                        runtime = value.strip()
    except OSError:
        return None, None
    return assistant, runtime


def model_aliases(client_name: str, session_id: str | None, home: str) -> dict:
    """{internal key: the name the account gives it} from this session's run log.

    Joined through the manifest, never by timestamp: a busy host starts one run
    directory per client launch and the newest is not necessarily this one's. A
    client with no `aliases` row has no map to read — claude reports a full model
    id already.
    """
    row = MODEL_REPORT.get(client_name) or {}
    if not row.get("aliases") or not session_id:
        return {}
    directory = os.path.join(home, *row["aliases"])
    try:
        names = sorted(os.listdir(directory), reverse=True)[:RUN_SCAN_LIMIT]
    except OSError:
        return {}
    for name in names:
        run_dir = os.path.join(directory, name)
        try:
            with io.open(os.path.join(run_dir, "manifest.json"), encoding="utf-8",
                         errors="replace") as fh:
                argv = json.load(fh).get("argv") or []
        except (OSError, ValueError, AttributeError):
            continue
        if not any(session_id in str(part) for part in argv):
            continue
        for log_name in ("qodercli.log", "claude.log"):
            try:
                with io.open(os.path.join(run_dir, log_name), encoding="utf-8",
                             errors="replace") as fh:
                    text = fh.read(RUN_LOG_SCAN_BYTES)
            except OSError:
                continue
            if "session=%s" % session_id not in text:
                continue
            pairs = {}
            for match in re.finditer(r'"key":"([^"]+)","display_name":"([^"]*)"', text):
                pairs.setdefault(match.group(1), match.group(2).strip() or match.group(1))
            if pairs:
                return pairs
    return {}


def reported_model(client_name: str, session_id: str | None,
                   home: str | None = None) -> str | None:
    """The model `client_name` recorded for its own session, or None.

    None is the honest answer as often as not: no transcript, no model row, or a
    client that keeps no record anyone outside it can read. The caller then
    records its own assumption as an assumption instead of as an answer.
    """
    home = os.path.expanduser("~") if home is None else home
    path = transcript_path(client_name, session_id, home)
    if path is None:
        return None
    assistant, runtime = transcript_models(path)
    model = assistant or runtime
    if not model:
        return None
    return model_aliases(client_name, session_id, home).get(model) or model


def session_args(client_name: str, session_id: str | None) -> list:
    """The argv that tells this client which session id the spawner will read."""
    if not session_id or client_name not in MODEL_REPORT:
        return []
    return ["--session-id", session_id]


# KEYDENY3g item 3: a leaf that can spawn hands its work to a child carrying none
# of the leaf's fences, so every client gets an answer — either the spawner
# renders that CLI's own deny, or the client row says `subagents: False` and the
# CLI is documented as having nothing to deny. opencode is neither: its gate is
# the config overlay (spawn_gate_rules), not an argv flag.
# Flag names verified against each CLI's --help on this host 2026-09-28:
#   claude  --disallowedTools, --disallowed-tools <tools...>
#   qoder   --disallowed-tools <tool>
#   qwen    --exclude-tools  (array)
# The *values* are tool-name patterns, and a pattern that names no tool denies
# nothing rather than erroring, so each CLI's known spellings all go in: an extra
# spelling is inert, a missing one is the gap.
LEAF_SPAWN_DENY = {
    "claude": ("--disallowed-tools", "Task", "Agent"),
    "qoder": ("--disallowed-tools", "Agent", "Task"),
    "qwen": ("--exclude-tools", "task", "agent", "subagent"),
}


def leaf_spawn_deny(client: "Client") -> tuple:
    """The argv that denies this client's sub-agent spawn, () when it has none."""
    return LEAF_SPAWN_DENY.get(client.name, ())


def leaf_deny_argv(client: "Client") -> list:
    """The rendered deny argv, per the flag's arity (KEYDENY3 Sonnet HIGH/MED).

    The table stores one flag plus the tool names; each CLI reads them with a
    different arity, and read wrong the gate denies one tool (or none) while
    the rest lands in the prompt:
      qoder  --disallowed-tools <tool> binds ONE value per occurrence, so the
             flag repeats; `--` then separates the options from the query, as
             qodercli's help instructs ("use -p or -- to separate from query").
      claude --disallowed-tools <tools...> is variadic and takes everything up
             to the next flag - the trailing prompt included - so the list is
             terminated with `--` before the positional.
      qwen   --exclude-tools is a parsed array; `-p task` already follows it.
    """
    entry = leaf_spawn_deny(client)
    if not entry:
        return []
    flag, tools = entry[0], list(entry[1:])
    if client.name == "qoder":
        return [v for tool in tools for v in (flag, tool)] + ["--"]
    if client.name == "claude":
        return [flag] + tools + ["--"]
    return list(entry)


# --- carrying the run's gateway headers per client (FLEETP0 review item 4) ----
# `omniroute run` itself has no header option (`omniroute run --help`: port,
# remote/base-url, context, provider, model, profile, token/api-key[-env],
# dry-run, json), so each launched CLI has to put the headers on its own
# requests. Measured 2026-09-28 against a local listener the launcher was
# pointed at with --remote (no gateway spend):
#   codex  -c 'model_providers.omniroute.http_headers.<name>="<value>"', which
#          MUST sit before the `exec` subcommand: after it the override replaces
#          the whole model_providers table and codex aborts with "provider name
#          must not be empty". A dotted segment with dashes parses; a quoted
#          segment (."x-...") is dropped without a word.
#   gemini GEMINI_CLI_CUSTOM_HEADERS="<name>:<value>,<name2>:<value2>"
#          (bundle parseCustomHeaders: split on /,(?=\s*[^,:]+:)/, first ':'
#          divides). Both headers arrived on the model request.
#   qwen   nothing: customHeaders exists only in settings.json, and the launcher
#          owns the temporary QWEN_HOME it deletes on exit; the one env hook
#          (QWEN_CODE_SYSTEM_SETTINGS_PATH) is the machine-wide system settings
#          file, not something one spawn may write.
# So qwen rows stay untagged; docs/routing.md says so too.
CODEX_PROVIDER_TABLE = "model_providers.omniroute"
GEMINI_CUSTOM_HEADERS_ENV = "GEMINI_CLI_CUSTOM_HEADERS"
GEMINI_MODEL_ENV = "GEMINI_MODEL"
HEADER_CLIENTS = ("gemini", "codex")  # gateway clients that can carry a header


def gemini_side_model_env(client_name: str, model: str | None) -> dict:
    """The child-env pin that stops gemini-cli answering on an unrouted leg (D8).

    gemini-cli resolves its model by precedence (measured in the installed
    bundle's `docs/cli/model-routing.md`): the `--model` flag, then `GEMINI_MODEL`,
    then `model.name` in settings.json, then a local Gemma router, then the default
    — which is **`auto`**, and the CLI's own `docs/cli/model.md` says Auto resolves
    to `gemini-3-pro-preview` / `gemini-3-flash-preview` (and 2.5-pro/2.5-flash).
    The argv flag is precedence 1 and so covers the one request this run was asked
    to make; a call the CLI makes *inside* the process — next-speaker, routing,
    a retry — re-resolves it and with no pin falls to that `auto` default. Live
    (2026-10-01, gemini-cli seat): an internal side call went to
    `openrouter/google/gemini-3-flash-preview` and came back HTTP 402 — a leg this
    lane never routed, never priced and never agreed to spend on.

    Pinning `GEMINI_MODEL` to the very value the argv carries puts precedence 2
    behind precedence 1, so an internal call can only re-resolve to the same
    gateway leg the resolver already picked, and never to `auto`. Empty for every
    other client (their CLI reads no such variable) and for a run with no model
    named, where there is nothing to pin.
    """
    if client_name != "gemini" or not model:
        return {}
    return {GEMINI_MODEL_ENV: str(model)}


def gateway_header_args(client: Client, headers: dict | None) -> list:
    """The launcher-argv prefix that gives `client` its per-request headers."""
    if client.name != "codex" or not headers:
        return []
    args = []
    for name, value in headers.items():
        args += ["-c", '%s.http_headers.%s="%s"' % (CODEX_PROVIDER_TABLE, name, value)]
    return args


def gemini_custom_headers(headers: dict | None) -> str:
    """The GEMINI_CLI_CUSTOM_HEADERS value for these headers ('k:v' pairs)."""
    return ",".join("%s:%s" % (name, value) for name, value in (headers or {}).items())


def seat_level(client: Client, level: str, isolate: bool = False,
               read_only: bool = False) -> str:
    """The mode level for one run: "seat" when `level` is a read run that may write.

    A read run is a reviewer, and a reviewer that runs --isolate works in a private
    clone: the sandbox confines its writes and the post-run leak check proves the parent
    checkout stayed clean, so `plan` — a mode that cannot write its report at all — only
    blocks the seat's deliverable. Two read runs keep `plan`: one NOT isolated, which
    would otherwise edit the caller's checkout, and one marked --read-only, whose success
    IS an unchanged sandbox. The seat is declared, never assumed: only a client row with
    a "seat" mode gets one, so every other CLI's argv is unchanged by construction.
    """
    if level == "read" and isolate and not read_only and "seat" in client.modes:
        return "seat"
    return level


def build_command(client: Client, task: str, combo: str | None, level: str,
                  model: str | None = None, joinable: str | None = None,
                  headers: dict | None = None, deny_spawn: bool = False,
                  session_id: str | None = None) -> list:
    """The argv for one headless task. opencode is built by autoos-agent.py itself.

    `deny_spawn` is the leaf gate: it only ever applies to a run that wears a leaf
    role, because tier 1 and tier 2 are the tiers that must be able to spawn.

    `headers` are the run's gateway request headers; only a client that can put
    them on a request uses them (gateway_header_args, gemini_custom_headers).

    `session_id` is the id this spawner minted so it can find the client's own
    transcript afterwards (MODEL_REPORT); only a client that takes one from the
    caller gets it (session_args). It goes before `deny` and the task, which must
    stay last.
    """
    mode = client.modes.get(level, [])
    deny = leaf_deny_argv(client) if deny_spawn else []
    session = session_args(client.name, session_id)
    if client.name == "claude":
        if joinable:
            # No user-scope MCP servers: user-scope graphify started one docker
            # container per lane worktree (measured 2026-09-25). --mcp-config is
            # variadic - --name after it keeps the prompt from being read as a path.
            # A --bg session is attached to by `claude attach`, which mints and
            # owns the id, so no session-id is forced on it; its writer stays an
            # assumption and says so.
            return (["claude", "--bg", "--remote-control", joinable, "--strict-mcp-config",
                     "--mcp-config", '{"mcpServers":{}}', "--name", joinable] + mode +
                    ([] if not model else ["--model", model]) + deny + [task])
        return (["claude", "-p"] + mode + ([] if not model else ["--model", model]) +
                session + deny + [task])
    if client.name == "codex":
        inner = ["exec"] + mode + ["--skip-git-repo-check"] + deny + [task]
    elif client.name == "qoder":
        return (["qodercli", "-p"] + mode + ["--model", model or QODER_DEFAULT_MODEL] +
                session + deny + [task])
    elif client.name == "agy":
        # AGYFIX item 1 (measured 2026-09-27, K3 CLI audit): agy 1.2.12 reads
        # "--model" as the -p prompt when -p comes first, so the model goes
        # BEFORE -p. Working form measured: `agy --model <m> -p <task>`.
        return ["agy", "--model", model or AGY_DEFAULT_MODEL, "-p"] + deny + [task]
    elif client.name == "gemini":
        # Headless gemini exits 55 in a folder it does not trust (live
        # 2026-09-25); --skip-trust trusts the spawn cwd for this session only.
        inner = ["--skip-trust"] + mode + deny + ["-p", task]
    else:  # qwen
        inner = mode + deny + ["-p", task]
    return (["omniroute", "run", client.name, "--model", model or combo,
             "--api-key-env", "AUTOOS_OMNIROUTE_KEY", "--"] +
            gateway_header_args(client, headers) + inner)


# CLAUDEBUDGET-h item 2 (Muse#high on 4fc082b..d1eb9c8, findings 2+4): ONE table
# naming how each client receives the model that answers it, written against
# build_command above — and, for opencode, against autoos-agent.py's build_plan,
# the only place its argv is built. The last-mile Claude gate prices through it,
# so it prices what the process is actually handed instead of one literal
# `--model` token, and a client it cannot read is refused rather than assumed
# free. Kinds:
#   ("flag", NAME)     argv `NAME <value>` or `NAME=<value>` — the string the
#                      client's own parser reads (`agy` takes it BEFORE -p,
#                      `qodercli` after, `omniroute run` before the `--`)
#   ("route",)         the gateway combo the run resolves to legs through
#                      (gateway clients only: an own-account client never
#                      receives it)
#   ("config", NAME)   the launch config: "agent" is the tier agent's own model in
#                      opencode.jsonc, "overlay" the OPENCODE_CONFIG_CONTENT
#                      document build_plan injects into the child env, which the
#                      client merges LAST and so overrides both the file and argv
#   ("registry", KEY)  the `clients` row this host resolves for the client before
#                      it builds anything (`effective_spawn_model` reads it ahead
#                      of the adapter constant)
# A client with no row here is unpriceable, and under a budget unpriceable is a
# refusal. A row must exist for every client in CLIENTS — the test that says so
# is what keeps this table from drifting out of build_command.
MODEL_INPUT = {
    "opencode": (("flag", "--model"), ("config", "overlay"), ("config", "agent"),
                 ("route",)),
    "claude":   (("flag", "--model"), ("registry", "default_model")),
    "agy":      (("flag", "--model"), ("registry", "default_model")),
    "qoder":    (("flag", "--model"), ("registry", "default_model")),
    "codex":    (("flag", "--model"), ("route",)),
    "gemini":   (("flag", "--model"), ("route",)),
    "qwen":     (("flag", "--model"), ("route",)),
}


def signin_state(client: Client, env: dict | None = None) -> tuple:
    """(True, "") signed in, (False, reason) not usable, (None, "") not installed or no probe.

    agy keeps its token in the OS keyring, so no file tells whether it is signed
    in; only the CLI can. Measured 2026-09-25: signed out, `agy models` exits 1
    in 0.6 s with "Please sign in ...", while a headless `agy -p` prints an
    OAuth URL, waits 60 s for a code and only then fails.
    """
    env = os.environ if env is None else env
    if not client.signin_probe:
        return None, ""
    # No PATH in env means os.defpath, as exec would - never the caller's PATH.
    exe = shutil.which(client.binary, path=env.get("PATH", os.defpath))
    if not exe:
        return None, ""
    argv = [exe, *client.signin_probe]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, env=dict(env),
                           stdin=subprocess.DEVNULL, timeout=SIGNIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "`%s %s` did not answer within %d s" % (
            client.binary, " ".join(client.signin_probe), SIGNIN_TIMEOUT)
    except OSError as exc:
        return False, str(exc)
    if r.returncode == 0:
        return True, ""
    lines = [ln.strip() for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()]
    return False, (lines[-1] if lines else "`%s %s` exited %d" % (
        client.binary, " ".join(client.signin_probe), r.returncode))[:240]


def signed_out(reason: str) -> bool:
    """Whether a failed probe's reason is a missing login (vs. a network or CLI error)."""
    return re.search(r"sign.?in|log.?in|authenticat", reason, re.I) is not None


# SPAWNFREE (S2) item 3: a mode the CLI does not offer is not an error the CLI
# reports - qodercli 1.1.63 accepted `--permission-mode accept_edits`, ignored
# it, and refused every tool call headless (62 runs, L1-backlog qoder lanes
# 2026-09-27). So each client's declared modes are checked against the client's
# own `--help` before a run, once per binary version.
HELP_TIMEOUT = 15  # seconds; `--help` and `--version` are local, no network
# Bump when parse_mode_choices's reading of a help block changes: an entry
# written by the old parser must not keep refusing (or passing) a run.
MODE_PARSER_VERSION = 1
# An option line of a commander.js-style help block, and the `(choices: a, b, c)`
# list that may wrap onto the continuation lines under it.
_OPT_LINE_RE = re.compile(r"^\s+-{1,2}[A-Za-z0-9._-]+")
_CHOICES_RE = re.compile(r"\(choices:\s*(.*?)\)", re.S)


def parse_mode_choices(help_text: str) -> dict:
    """{flag: [choices]} for every option in `help_text` that lists choices.

    A commander.js help block wraps its description - and the `(choices: ...)`
    list inside it - onto the lines below the option, so an option's text runs
    until the next line that starts another option. A flag with no choices
    list is not reported (it cannot be validated).
    """
    out = {}
    lines = (help_text or "").splitlines()
    i = 0
    while i < len(lines):
        if not _OPT_LINE_RE.match(lines[i]):
            i += 1
            continue
        block = [lines[i]]
        j = i + 1
        while j < len(lines) and not _OPT_LINE_RE.match(lines[j]):
            block.append(lines[j])
            j += 1
        i = j
        # The option spec is the first column (the description starts after a
        # run of 2+ spaces); --thinking's spec never reaches into the prose.
        spec = re.split(r"\s{2,}", block[0].strip())[0]
        flags = re.findall(r"--[A-Za-z0-9._-]+", spec)
        found = _CHOICES_RE.search(" ".join(ln.strip() for ln in block))
        if not flags or not found:
            continue
        # Choices arrive bare (`choices: auto, plan`) or quoted
        # (`choices: "acceptEdits", "plan"`), and commander ends a wrapped list
        # with a period - all of that is decoration on the value.
        choices = [c.strip().strip("\"'").strip().rstrip(".").strip("\"'").strip()
                   for c in found.group(1).split(",")]
        choices = [c for c in choices if c]
        if choices:
            out[flags[0]] = choices
    return out


def _exe(client: Client, env: dict):
    return shutil.which(client.binary, path=env.get("PATH", os.defpath))


def _binary_version(client: Client, exe: str, env: dict) -> str:
    """The client's own `--version` line, or the binary's mtime+size when it has
    no such flag: either is enough to say "the help I cached is for this
    build". A client that changes its modes without changing either is a
    vendor bug we cannot see coming."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           env=dict(env), stdin=subprocess.DEVNULL, timeout=HELP_TIMEOUT)
        lines = [ln.strip() for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()]
        if r.returncode == 0 and lines:
            return lines[0][:120]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        st = os.stat(exe)
        return "stat:%d:%d" % (int(st.st_mtime), st.st_size)
    except OSError:
        return "unknown"


def _mode_cache_file(env: dict) -> str:
    return os.path.join(state_dir(env), "agents", "client-modes.json")


def _mode_cache(env: dict) -> dict:
    try:
        with io.open(_mode_cache_file(env), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_mode_cache(env: dict, data: dict) -> None:
    path = _mode_cache_file(env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-%d" % os.getpid()
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def mode_choices(client: Client, env: dict | None = None) -> dict:
    """The client's own `{flag: [choices]}`, from `--help` or the version cache.

    {} when nothing can be known: no binary, a help block that lists no
    choices, or a failing `--help`. A negative result is never cached, so a
    transient failure costs one retry and not a permanently blind check.
    """
    env = os.environ if env is None else env
    exe = _exe(client, env)
    if not exe:
        return {}
    version = _binary_version(client, exe, env)
    cache = _mode_cache(env)
    entry = cache.get(client.binary)
    if (isinstance(entry, dict) and entry.get("version") == version
            and entry.get("parser") == MODE_PARSER_VERSION):
        return entry.get("choices") or {}
    try:
        r = subprocess.run([exe, "--help"], capture_output=True, text=True,
                           env=dict(env), stdin=subprocess.DEVNULL, timeout=HELP_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    choices = parse_mode_choices(r.stdout + "\n" + r.stderr)
    if choices:
        _write_mode_cache(env, dict(cache, **{client.binary: {
            "version": version, "parser": MODE_PARSER_VERSION, "choices": choices}}))
    return choices


def check_client_modes(client: Client, env: dict | None = None) -> tuple:
    """(True, "") the modes this client offers; (False, why) a mode it does not;
    (None, "") nothing to check or no way to know (never a refusal).

    Runs the client's own `--help` at most once per binary version, so a spawn
    pays one cached lookup instead of a subprocess each time.
    """
    if not client.modes:
        return None, ""
    env = os.environ if env is None else env
    if not _exe(client, env):
        return None, ""
    choices = mode_choices(client, env)
    if not choices:
        return None, ""
    problems = []
    for level in sorted(client.modes):
        argv = client.modes[level]
        if len(argv) < 2:
            continue
        flag, value = argv[0], argv[1]
        offered = choices.get(flag)
        if offered and value not in offered:
            problems.append("%s mode `%s %s` (this client offers %s for %s)"
                            % (level, flag, value, ", ".join(offered), flag))
    if not problems:
        return True, ""
    return False, ("%s: %s. The list came from `%s --help`; an unknown mode makes "
                   "the CLI deny every tool call headless, so the run would only "
                   "look like a refusal. Fix client.modes in tools/autoos_clients.py."
                   % (client.binary, "; ".join(problems), client.binary))


class DepthError(RuntimeError):
    pass


def child_depth(env: dict, max_flag: int | None = None) -> tuple:
    """(child depth, max) for a spawn from a process with `env`; DepthError past the max.

    The inherited max can only be lowered - a child cannot grant itself depth."""
    try:
        cur = int(env.get("AUTOOS_AGENT_DEPTH", "0"))
        cap = int(env.get("AUTOOS_AGENT_MAX_DEPTH", str(DEFAULT_MAX_DEPTH)))
    except ValueError:
        raise DepthError("AUTOOS_AGENT_DEPTH/AUTOOS_AGENT_MAX_DEPTH are not integers; refusing to spawn")
    if max_flag is not None:
        cap = min(cap, max_flag)
    if cur + 1 > cap:
        raise DepthError("depth budget exhausted: this agent is at depth %d of %d; do the work "
                         "yourself or hand it back to your caller" % (cur, cap))
    return cur + 1, cap


def state_dir(env: dict | None = None) -> str:
    """Where run state lives: the repository's git-ignored logs/ folder.

    Operator order 2026-09-25: job/output/exit files, probe dates and sandbox
    clones stay inside the repository, git-ignored - never scattered under
    ~/.local/state. AUTOOS_STATE_DIR overrides it (the tests use a temp dir).
    """
    env = os.environ if env is None else env
    return env.get("AUTOOS_STATE_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def _probe_file(env: dict | None = None) -> str:
    return os.path.join(state_dir(env), "agents", "probes.json")


def _probes(env=None) -> dict:
    try:
        with io.open(_probe_file(env), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def record_probe(client: str, now: float | None = None, env=None) -> None:
    """Remember that `client` worked (a promo's last successful run)."""
    path = _probe_file(env)
    data = _probes(env)
    data[client] = time.time() if now is None else now
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def probe_age_days(client: str, now: float | None = None, env=None):
    at = _probes(env).get(client)
    if at is None:
        return None
    return ((time.time() if now is None else now) - at) / 86400.0


def probe_stale(client: str, now: float | None = None, env=None) -> bool:
    age = probe_age_days(client, now, env)
    return age is None or age > PROBE_STALE_DAYS
