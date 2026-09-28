"""``deepseek_call.py`` and the two review scripts that use it.

Operator rules: every key lives in AutoOS ``configuration/api-keys.yml`` (2026-09-25); which
DeepSeek models count comes from catalog/ai-registry.json (route ``deepseek-v4.1-flash`` and
``policy.leg_rules``, DSBACK 2026-09-28); the OmniRoute gateway is the only leg, under the
registry's monthly cap (``providers.deepseek.monthly_cap_usd``, fail-closed, 2026-09-28); never
OpenRouter, never local Ollama; no key is ever printed or put on a command line. Each rule below
has a test that fails if the rule is broken, not merely one that passes when it holds.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

import deepseek_call as dc  # noqa: E402

OMNI_KEY = "sk-omni-TESTSECRET-1111"
MANAGE_KEY = "sk-manage-TESTSECRET-3333"
OMNI_SERVES = "deepseek/deepseek-flash"          # the route's allowed leg in the fixture

# The shape of the real registry's DeepSeek policy and cap (DSBACK/DSAMEND/DSCALL 2026-09-28),
# frozen here so a registry edit cannot make these tests pass or fail by accident; the real one
# is checked separately below. The price makes one call-log token cost one dollar.
POLICY = {
    "providers": {"deepseek": {"monthly_cap_usd": 25,
                               "monthly_cap_source": "operator DeepSeek paid cap"}},
    "models": {"deepseek-flash": {"price_in": 1.0, "price_out": 1.0}},
    "routes": {"deepseek-v4.1-flash": {"legs": ["deepseek/deepseek-flash",
                                                "opencode-zen/deepseek-v4.1-flash"]}},
    "policy": {"leg_rules": [
        {"id": "deny-deepseek-pro", "match": "*deepseek*pro*", "allow": False},
        {"id": "allow-deepseek-native-flash", "match": "deepseek/deepseek-flash", "allow": True},
        {"id": "deny-deepseek", "match": "*deepseek*", "allow": False},
    ]},
}


def spend_rows(dollars):
    """Call-log rows costing `dollars` this month at the fixture price."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not dollars:
        return []
    return [{"id": "r1", "provider": "deepseek", "model": OMNI_SERVES, "timestamp": now,
             "tokens": {"in": dollars, "out": 0}}]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ("AUTOOS_API_KEYS", "AUTOOS_ROOT", "AUTOOS_OMNIROUTE_KEY", "OPENROUTER_API_KEY",
                 "DSR_OMNIROUTE_URL", "DSR_OPENROUTER_URL", "DSR_REGISTRY",
                 "AUTOOS_AI_STACK_CONFIG", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    # The real host has a real api-keys.yml and manage key; tests must never find them.
    monkeypatch.setattr(dc, "default_candidates", lambda: [])
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def keys_file(tmp_path, body):
    p = tmp_path / "configuration" / "api-keys.yml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── key lookup ─────────────────────────────────────────────────────────────────

def test_reads_the_omniroute_key_from_yml(tmp_path, monkeypatch):
    p = keys_file(tmp_path, f"# c\nomniroute: \"{OMNI_KEY}\"\n")
    monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
    assert dc.load_key() == OMNI_KEY


def test_placeholder_is_not_a_key(tmp_path, monkeypatch):
    p = keys_file(tmp_path, "omniroute: REPLACE_WITH_OMNIROUTE_CLIENT_KEY\n")
    monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
    with pytest.raises(dc.KeyError_):
        dc.load_key()


def test_env_key_overrides_file(tmp_path, monkeypatch):
    p = keys_file(tmp_path, f"omniroute: {OMNI_KEY}\n")
    monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
    monkeypatch.setenv("AUTOOS_OMNIROUTE_KEY", "env-omni")
    assert dc.load_key() == "env-omni"


def test_autoos_root_locates_the_file(tmp_path, monkeypatch):
    keys_file(tmp_path, f"omniroute: {OMNI_KEY}\n")
    monkeypatch.setenv("AUTOOS_ROOT", str(tmp_path))
    assert dc.load_key() == OMNI_KEY


def test_explicit_missing_file_fails_loudly(tmp_path, monkeypatch):
    # An explicit path that is not a file must not fall through to another file (D4).
    keys_file(tmp_path, f"omniroute: {OMNI_KEY}\n")
    monkeypatch.setenv("AUTOOS_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOOS_API_KEYS", str(tmp_path / "nope.yml"))
    with pytest.raises(dc.KeyError_) as e:
        dc.load_key()
    assert "AUTOOS_API_KEYS" in str(e.value)


def test_no_file_and_no_env_names_where_it_looked(tmp_path):
    with pytest.raises(dc.KeyError_) as e:
        dc.load_key()
    assert "api-keys.yml" in str(e.value)


# ── model guard ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("m", ["", "deepseek-v4.1-flash"])
def test_only_the_route_is_accepted(m):
    dc.check_model(m)


@pytest.mark.parametrize("m", ["deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-flash",
                               "deepseek-chat", "ollama/deepseek"])
def test_other_models_are_refused(m):
    with pytest.raises(ValueError):
        dc.check_model(m)


# ── the gateway, faked ─────────────────────────────────────────────────────────

class Fake:
    """One HTTP server playing the gateway (and, for the OpenRouter test, a would-be
    OpenRouter); records what it was sent. GET /api/usage/call-logs answers `rows`."""

    def __init__(self, status=200, model=OMNI_SERVES, content="ack", health=200,
                 echo_auth=False, rows=None):
        self.status, self.model, self.content, self.health = status, model, content, health
        self.echo_auth = echo_auth
        self.rows = rows if rows is not None else []
        self.calls = []
        self.gets = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.gets.append({"path": self.path, "auth": self.headers.get("Authorization", "")})
                if self.path.startswith("/api/usage/call-logs"):
                    offset = int(self.path.split("offset=")[1].split("&")[0]) if "offset=" in self.path else 0
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(fake.rows if offset == 0 else []).encode())
                    return
                self.send_response(fake.health)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                auth = self.headers.get("Authorization", "")
                fake.calls.append({"path": self.path, "auth": auth, "body": body})
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    msg = "bad key " + auth if fake.echo_auth else "upstream error"
                    self.wfile.write(json.dumps({"error": {"message": msg}}).encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"model": fake.model, "choices": [
                    {"message": {"role": "assistant", "content": fake.content}}]}).encode())

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    """make(omni=Fake(...), policy=...) wires the fake gateway, keys, manage key and registry."""
    made = []

    def make(omni=None, policy=None):
        omni = omni or Fake()
        made.append(omni)
        reg = tmp_path / "ai-registry.json"
        reg.write_text(json.dumps(policy or POLICY), encoding="utf-8")
        monkeypatch.setenv("DSR_REGISTRY", str(reg))
        monkeypatch.setenv("DSR_OMNIROUTE_URL", omni.url)
        monkeypatch.setenv("AUTOOS_API_KEYS", str(keys_file(tmp_path, f"omniroute: {OMNI_KEY}\n")))
        stack = tmp_path / "ai-stack"
        stack.mkdir(exist_ok=True)
        (stack / "manage.key").write_text(MANAGE_KEY + "\n", encoding="utf-8")
        monkeypatch.setenv("AUTOOS_AI_STACK_CONFIG", str(stack))
        return omni

    yield make
    for f in made:
        f.close()


def test_the_allowed_leg_is_asked_with_the_key_and_max_tokens(gw):
    omni = gw()
    text, served, leg = dc.complete("hello", timeout=10)
    assert (text, served, leg) == ("ack", OMNI_SERVES, "omniroute")
    call = omni.calls[0]
    assert call["path"] == "/v1/chat/completions"
    assert call["auth"] == "Bearer " + OMNI_KEY
    # The allowed registry leg, never the route id (deepseek-v4.1-flash answers 400;
    # L1-routing review 2026-09-28), and a max_tokens reasoning rungs can answer in.
    assert call["body"]["model"] == "deepseek/deepseek-flash"
    assert call["body"]["max_tokens"] >= 4096
    assert call["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert call["body"]["temperature"] == 0.1


# ── the monthly cap ────────────────────────────────────────────────────────────

def test_spend_below_the_cap_is_read_with_the_manage_key_and_the_call_is_made(gw):
    omni = gw(Fake(rows=spend_rows(10)))
    assert dc.complete("hello", timeout=10)[2] == "omniroute"
    logs = [g for g in omni.gets if g["path"].startswith("/api/usage/call-logs")]
    assert logs and logs[0]["auth"] == "Bearer " + MANAGE_KEY
    assert len(omni.calls) == 1


@pytest.mark.parametrize("dollars", [25, 40])
def test_spend_at_or_above_the_cap_refuses_before_any_model_call(gw, dollars):
    omni = gw(Fake(rows=spend_rows(dollars)))
    with pytest.raises(dc.CapError) as e:
        dc.complete("hello", timeout=10)
    assert "cap" in str(e.value)
    assert omni.calls == []


def test_unreadable_spend_refuses_before_any_model_call(gw, monkeypatch):
    omni = gw()

    def boom(url, headers, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(dc, "usage_fetch", boom)
    with pytest.raises(dc.CapError) as e:
        dc.complete("hello", timeout=10)
    assert "cannot read" in str(e.value)
    assert omni.calls == []


def test_no_manage_key_refuses(gw, monkeypatch, tmp_path):
    omni = gw()
    monkeypatch.setenv("AUTOOS_AI_STACK_CONFIG", str(tmp_path / "no-stack"))
    with pytest.raises(dc.CapError):
        dc.complete("hello", timeout=10)
    assert omni.calls == []


def test_a_registry_without_a_cap_refuses(gw):
    policy = json.loads(json.dumps(POLICY))
    del policy["providers"]["deepseek"]["monthly_cap_usd"]
    omni = gw(policy=policy)
    with pytest.raises(dc.CapError) as e:
        dc.complete("hello", timeout=10)
    assert "monthly_cap_usd" in str(e.value)
    assert omni.calls == []


def test_the_cli_exits_3_on_a_cap_refusal(gw, tmp_path, capsys):
    gw(Fake(rows=spend_rows(30)))
    prompt = tmp_path / "p.txt"
    prompt.write_text("x\n", encoding="utf-8")
    assert dc.main([str(prompt)]) == 3
    assert "refused" in capsys.readouterr().err


# ── OpenRouter is gone ─────────────────────────────────────────────────────────

def test_no_openrouter_url_is_ever_called(gw, monkeypatch):
    # Even with the old env var pointing at a live server and the gateway failing.
    decoy = Fake()
    try:
        monkeypatch.setenv("DSR_OPENROUTER_URL", decoy.url)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-decoy")
        gw(Fake(status=502))
        with pytest.raises(dc.RouteError):
            dc.complete("hello", timeout=10)
        assert decoy.calls == [] and decoy.gets == []
    finally:
        decoy.close()


def test_the_module_names_no_openrouter_outside_its_docstring():
    source = (SKILL / "deepseek_call.py").read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]  # everything after the module docstring
    assert "openrouter" not in code.casefold()


# ── served-model policy ────────────────────────────────────────────────────────

@pytest.mark.parametrize("served", ["deepseek/deepseek-v4-pro", "DeepSeek/DeepSeek-V4-Pro",
                                    "opencode-zen/deepseek-v4.1-flash", "deepseek-flash-pro",
                                    "deepseek-flash-20260927", "deepseek/deepseek-flash-20260927",
                                    "deepseek-v4-pro", "cheaperinference/deepseek-flash",
                                    "deepseek/deepseek-v4-flash", "qwen/qwen3"])
def test_the_served_model_is_denied_unless_it_is_exactly_an_allowed_leg(gw, served):
    # Muse xhigh (L1-routing): never normalise before the deny check. A dated snapshot, a
    # pro id in any spelling, a leg the rules deny or the same model from a foreign provider
    # is not the allowed leg - a gateway that re-routed in silence is not a DeepSeek review.
    gw(Fake(model=served))
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert served in str(e.value)


def test_a_bare_served_id_of_the_one_allowed_leg_passes(gw):
    gw(Fake(model="DeepSeek-Flash"))
    assert dc.complete("hello", timeout=10)[2] == "omniroute"


def test_an_unreadable_registry_is_a_usage_error(gw, tmp_path, monkeypatch):
    gw()
    monkeypatch.setenv("DSR_REGISTRY", str(tmp_path / "missing.json"))
    with pytest.raises(dc.PolicyError):
        dc.complete("hello", timeout=10)


def test_the_real_registry_allows_a_flash_leg_never_pro_and_carries_the_cap():
    import autoos_usage
    allowed, registry, _denied = dc.load_policy()
    assert allowed, "route deepseek-v4.1-flash has no leg policy.leg_rules allows"
    assert not any("pro" in leg.casefold() for leg in allowed)
    assert autoos_usage.monthly_cap_usd(registry) > 0


def test_an_unreachable_gateway_says_it_did_not_go_local(gw):
    gw(Fake(health=503))
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert "Ollama" in str(e.value)


def test_a_key_echoed_in_an_error_body_is_scrubbed(gw):
    gw(Fake(status=401, echo_auth=True))
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert OMNI_KEY not in str(e.value)


# ── the shell scripts, end to end against the fake gateway ─────────────────────

def git_bash():
    """Git Bash, never WSL's System32 bash (which cannot see this Windows temp dir)."""
    if os.name != "nt":
        return shutil.which("bash")
    found = shutil.which("bash")
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    git = shutil.which("git")  # ...\Git\cmd\git.exe or ...\Git\mingw64\bin\git.exe
    for root in Path(git).parents if git else ():
        if (root / "bin" / "bash.exe").is_file():
            return str(root / "bin" / "bash.exe")
    return None


BASH = git_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no Git Bash / bash on this host")


def run_script(args, env, cwd=None):
    full = {**os.environ, **env, "PYTHON": sys.executable}
    return subprocess.run([BASH, *args], env=full, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)


@needs_bash
def test_review_script_prints_the_answer_and_never_the_key(gw, tmp_path):
    omni = gw(Fake(content="line one\n\nline two"))
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("DO NOT USE ANY TOOLS.\nReview this.\n", encoding="utf-8")
    r = run_script([str(SKILL / "deepseek_review.sh"), str(prompt)], {})
    assert r.returncode == 0, r.stderr
    assert r.stdout == "line one\n\nline two\n"
    assert "served: %s via omniroute" % OMNI_SERVES in r.stderr
    assert OMNI_KEY not in r.stdout + r.stderr and MANAGE_KEY not in r.stdout + r.stderr
    assert omni.calls[0]["body"]["messages"][0]["content"] == prompt.read_text(encoding="utf-8")


@needs_bash
def test_review_script_refuses_another_model(gw, tmp_path):
    omni = gw()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("x\n", encoding="utf-8")
    r = run_script([str(SKILL / "deepseek_review.sh"), str(prompt), "deepseek/deepseek-v4-flash"], {})
    assert r.returncode == 2
    assert omni.calls == []


@needs_bash
def test_review_script_passes_the_cap_refusal_through(gw, tmp_path):
    omni = gw(Fake(rows=spend_rows(30)))
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("x\n", encoding="utf-8")
    r = run_script([str(SKILL / "deepseek_review.sh"), str(prompt)], {})
    assert r.returncode == 3, r.stderr
    assert omni.calls == []


@needs_bash
def test_chunked_script_writes_one_section_per_part(gw, tmp_path):
    omni = gw(Fake(content="no defects"))
    repo = tmp_path / "repo"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)  # noqa: E731
    g("init", "-q")
    g("config", "user.email", "t@example.invalid")
    g("config", "user.name", "t")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    g("add", ".")
    g("commit", "-qm", "base")
    base = g("rev-parse", "HEAD").stdout.strip()
    (repo / "a.txt").write_text("".join(f"line {i}\n" for i in range(1500)), encoding="utf-8")
    g("commit", "-qam", "big")
    head = g("rev-parse", "HEAD").stdout.strip()
    done = tmp_path / "done.txt"
    done.write_text("tests pass\n", encoding="utf-8")
    out = tmp_path / "out.md"
    r = run_script([str(SKILL / "deepseek_chunked_review.sh"), "lbl", base, head, str(done), str(out), str(repo)],
                   {"DSR_WORKDIR": str(tmp_path / "work")})
    assert r.returncode == 0, r.stderr
    md = out.read_text(encoding="utf-8")
    assert md.startswith("# DeepSeek review of merge %s (lbl) — 2 parts of a " % head)
    assert "## Part 1/2\nno defects\n" in md and "## Part 2/2\nno defects\n" in md
    assert len(omni.calls) == 2
    assert OMNI_KEY not in md + r.stdout + r.stderr
    err = (tmp_path / "work" / "dsr_lbl" / "err.log").read_text(encoding="utf-8")
    assert "served: %s via omniroute" % OMNI_SERVES in err
    assert OMNI_KEY not in err
