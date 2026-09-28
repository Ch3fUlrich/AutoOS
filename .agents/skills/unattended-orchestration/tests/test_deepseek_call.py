"""``deepseek_call.py`` and the two review scripts that use it.

Operator rules (2026-09-25): every key lives in AutoOS ``configuration/api-keys.yml``; which
DeepSeek models count comes from catalog/ai-registry.json (route ``deepseek-v4.1-flash`` and
``policy.leg_rules``, DSBACK 2026-09-28); the route is OmniRoute first, then OpenRouter direct;
never local Ollama; no key is ever printed or put on a command line. Each rule below has a test
that fails if the rule is broken, not merely one that passes when it holds.
"""
from __future__ import annotations

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
OR_KEY = "sk-or-TESTSECRET-2222"
OMNI_SERVES = "deepseek/deepseek-flash"          # the combo's allowed leg in the fixture
OR_SERVES = "deepseek/deepseek-v4.1-flash"

# The shape of the real registry's DeepSeek policy (DSBACK/DSAMEND 2026-09-28), frozen here so
# a registry edit cannot make these tests pass or fail by accident; the real one is checked
# separately below.
POLICY = {
    "routes": {"deepseek-v4.1-flash": {"legs": ["deepseek/deepseek-flash",
                                                "opencode-zen/deepseek-v4.1-flash"]}},
    "policy": {"leg_rules": [
        {"id": "deny-deepseek-pro", "match": "*deepseek*pro*", "allow": False},
        {"id": "allow-openrouter-deepseek-v4.1-flash",
         "match": "openrouter/deepseek/deepseek-v4.1-flash*", "allow": True},
        {"id": "allow-deepseek-native-flash", "match": "deepseek/deepseek-flash", "allow": True},
        {"id": "deny-deepseek", "match": "*deepseek*", "allow": False},
    ]},
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("AUTOOS_API_KEYS", "AUTOOS_ROOT", "AUTOOS_OMNIROUTE_KEY", "OPENROUTER_API_KEY",
                 "DSR_OMNIROUTE_URL", "DSR_OPENROUTER_URL", "DSR_REGISTRY"):
        monkeypatch.delenv(name, raising=False)
    # The real host has a real api-keys.yml; tests must never find it by accident.
    monkeypatch.setattr(dc, "default_candidates", lambda: [])


def keys_file(tmp_path, body):
    p = tmp_path / "configuration" / "api-keys.yml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── key lookup ─────────────────────────────────────────────────────────────────

def test_reads_both_keys_from_yml(tmp_path, monkeypatch):
    p = keys_file(tmp_path, f"# c\nomniroute: {OMNI_KEY}\nopenrouter: \"{OR_KEY}\"\n")
    monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
    assert dc.load_keys() == {"omniroute": OMNI_KEY, "openrouter": OR_KEY}


def test_placeholder_is_not_a_key(tmp_path, monkeypatch):
    p = keys_file(tmp_path, "omniroute: REPLACE_WITH_OMNIROUTE_CLIENT_KEY\nopenrouter: x\n")
    monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
    assert dc.load_keys() == {"omniroute": None, "openrouter": "x"}


def test_env_key_overrides_file(tmp_path, monkeypatch):
    p = keys_file(tmp_path, f"omniroute: {OMNI_KEY}\nopenrouter: {OR_KEY}\n")
    monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
    monkeypatch.setenv("AUTOOS_OMNIROUTE_KEY", "env-omni")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-or")
    assert dc.load_keys() == {"omniroute": "env-omni", "openrouter": "env-or"}


def test_autoos_root_locates_the_file(tmp_path, monkeypatch):
    keys_file(tmp_path, f"omniroute: {OMNI_KEY}\n")
    monkeypatch.setenv("AUTOOS_ROOT", str(tmp_path))
    assert dc.load_keys()["omniroute"] == OMNI_KEY


def test_explicit_missing_file_fails_loudly(tmp_path, monkeypatch):
    # An explicit path that is not a file must not fall through to another file (D4).
    keys_file(tmp_path, f"omniroute: {OMNI_KEY}\n")
    monkeypatch.setenv("AUTOOS_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOOS_API_KEYS", str(tmp_path / "nope.yml"))
    with pytest.raises(dc.KeyError_) as e:
        dc.load_keys()
    assert "AUTOOS_API_KEYS" in str(e.value)


def test_no_file_and_no_env_names_where_it_looked(tmp_path):
    with pytest.raises(dc.KeyError_) as e:
        dc.load_keys()
    assert "api-keys.yml" in str(e.value)


# ── model guard ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("m", ["", "deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"])
def test_only_v41_flash_is_accepted(m):
    dc.check_model(m)


@pytest.mark.parametrize("m", ["deepseek/deepseek-v4-flash", "deepseek-chat", "ollama/deepseek"])
def test_other_models_are_refused(m):
    with pytest.raises(ValueError):
        dc.check_model(m)


# ── routing, against a fake gateway ────────────────────────────────────────────

class Fake:
    """One HTTP server playing either leg; records what it was sent."""

    def __init__(self, status=200, model="deepseek/deepseek-flash", content="ack",
                 health=200, echo_auth=False):
        self.status, self.model, self.content, self.health = status, model, content, health
        self.echo_auth = echo_auth
        self.calls = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
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
def legs(tmp_path, monkeypatch):
    made = []

    def make(omni=None, openrouter=None):
        omni = omni or Fake(model=OMNI_SERVES)
        openrouter = openrouter or Fake(model=OR_SERVES)
        made.extend([omni, openrouter])
        reg = tmp_path / "ai-registry.json"
        reg.write_text(json.dumps(POLICY), encoding="utf-8")
        monkeypatch.setenv("DSR_REGISTRY", str(reg))
        monkeypatch.setenv("DSR_OMNIROUTE_URL", omni.url)
        monkeypatch.setenv("DSR_OPENROUTER_URL", openrouter.url)
        p = keys_file(tmp_path, f"omniroute: {OMNI_KEY}\nopenrouter: {OR_KEY}\n")
        monkeypatch.setenv("AUTOOS_API_KEYS", str(p))
        return omni, openrouter

    yield make
    for f in made:
        f.close()


def test_omniroute_first_with_its_key_and_the_pinned_combo(legs):
    omni, orr = legs()
    text, served, leg = dc.complete("hello", timeout=10)
    assert (text, served, leg) == ("ack", OMNI_SERVES, "omniroute")
    assert orr.calls == []
    call = omni.calls[0]
    assert call["path"] == "/v1/chat/completions"
    assert call["auth"] == "Bearer " + OMNI_KEY
    # The allowed registry leg, never the combo's display id (deepseek-v4.1-flash answers
    # 400; L1-routing review 2026-09-28), and a max_tokens reasoning rungs can answer in.
    assert call["body"]["model"] == "deepseek/deepseek-flash"
    assert call["body"]["max_tokens"] >= 4096
    assert call["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert call["body"]["temperature"] == 0.1


def test_falls_back_to_openrouter_when_omniroute_errors(legs):
    omni, orr = legs(omni=Fake(status=502))
    text, served, leg = dc.complete("hello", timeout=10)
    assert leg == "openrouter"
    assert orr.calls[0]["auth"] == "Bearer " + OR_KEY
    assert orr.calls[0]["body"]["model"] == "deepseek/deepseek-v4.1-flash"


def test_skips_omniroute_when_its_health_check_fails(legs):
    omni, orr = legs(omni=Fake(health=503))
    assert dc.complete("hello", timeout=10)[2] == "openrouter"
    assert omni.calls == []


@pytest.mark.parametrize("served", ["deepseek/deepseek-v4-pro", "DeepSeek/DeepSeek-V4-Pro",
                                    "opencode-zen/deepseek-v4.1-flash", "deepseek-flash-pro"])
def test_a_served_model_the_policy_denies_is_rejected(legs, served):
    # V4 Pro in any spelling, and a combo leg leg_rules deny, never pass as the review.
    legs(omni=Fake(model=served), openrouter=Fake(model=served))
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert served in str(e.value)


@pytest.mark.parametrize("served", ["deepseek-flash-20260927", "deepseek/deepseek-flash-20260927",
                                    "deepseek-v4-pro", "cheaperinference/deepseek-flash",
                                    "openrouter/deepseek/deepseek-v4-pro"])
def test_the_served_model_is_denied_unless_it_is_exactly_an_allowed_leg(legs, served):
    # Muse xhigh (L1-routing): never normalise before the deny check. A dated snapshot, a
    # bare pro id or the same model from a foreign provider is not the allowed leg.
    legs(omni=Fake(model=served), openrouter=Fake(model=served))
    with pytest.raises(dc.RouteError):
        dc.complete("hello", timeout=10)


def test_a_bare_served_id_of_the_one_allowed_leg_passes(legs):
    legs(omni=Fake(model="DeepSeek-Flash"))
    assert dc.complete("hello", timeout=10)[2] == "omniroute"


def test_openrouter_is_asked_for_its_allowed_model_with_max_tokens(legs):
    omni, orr = legs(omni=Fake(status=502))
    assert dc.complete("hello", timeout=10)[2] == "openrouter"
    assert orr.calls[0]["body"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert orr.calls[0]["body"]["max_tokens"] >= 4096


def test_the_openrouter_leg_is_skipped_when_the_policy_denies_it(legs, tmp_path, monkeypatch):
    omni, orr = legs(omni=Fake(status=502))
    denied = json.loads(json.dumps(POLICY))
    denied["policy"]["leg_rules"] = [r for r in denied["policy"]["leg_rules"]
                                     if not r["id"].startswith("allow-openrouter")]
    (tmp_path / "ai-registry.json").write_text(json.dumps(denied), encoding="utf-8")
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert "openrouter: no DeepSeek leg allowed" in str(e.value)
    assert orr.calls == []


def test_an_unreadable_registry_is_a_usage_error(legs, tmp_path, monkeypatch):
    legs()
    monkeypatch.setenv("DSR_REGISTRY", str(tmp_path / "missing.json"))
    with pytest.raises(dc.PolicyError):
        dc.complete("hello", timeout=10)


def test_the_real_registry_allows_a_flash_leg_and_never_pro():
    allowed, openrouter, _registry, _denied = dc.load_policy()
    assert allowed, "route deepseek-v4.1-flash has no leg policy.leg_rules allows"
    assert not any("pro" in leg.casefold() for leg in allowed)
    assert isinstance(openrouter, bool)


def test_a_different_served_model_is_rejected(legs):
    # A combo that silently re-routed to another model must not pass as a DeepSeek review.
    omni, orr = legs(omni=Fake(model="deepseek/deepseek-v4-flash"),
                     openrouter=Fake(model="qwen/qwen3"))
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert "deepseek/deepseek-v4-flash" in str(e.value) and "qwen/qwen3" in str(e.value)


def test_a_key_echoed_in_an_error_body_is_scrubbed(legs):
    legs(omni=Fake(status=401, echo_auth=True), openrouter=Fake(status=401, echo_auth=True))
    with pytest.raises(dc.RouteError) as e:
        dc.complete("hello", timeout=10)
    assert OMNI_KEY not in str(e.value) and OR_KEY not in str(e.value)
    assert "Ollama" in str(e.value)  # says it did not fall back to local, rather than doing so


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
def test_review_script_prints_the_answer_and_never_the_key(legs, tmp_path):
    omni, _ = legs(omni=Fake(content="line one\n\nline two"))
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("DO NOT USE ANY TOOLS.\nReview this.\n", encoding="utf-8")
    r = run_script([str(SKILL / "deepseek_review.sh"), str(prompt)], {})
    assert r.returncode == 0, r.stderr
    assert r.stdout == "line one\n\nline two\n"
    assert "served: %s via omniroute" % OMNI_SERVES in r.stderr
    assert OMNI_KEY not in r.stdout + r.stderr and OR_KEY not in r.stdout + r.stderr
    assert omni.calls[0]["body"]["messages"][0]["content"] == prompt.read_text(encoding="utf-8")


@needs_bash
def test_review_script_refuses_another_model(legs, tmp_path):
    omni, orr = legs()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("x\n", encoding="utf-8")
    r = run_script([str(SKILL / "deepseek_review.sh"), str(prompt), "deepseek/deepseek-v4-flash"], {})
    assert r.returncode == 2
    assert omni.calls == [] and orr.calls == []


@needs_bash
def test_chunked_script_writes_one_section_per_part(legs, tmp_path):
    omni, _ = legs(omni=Fake(content="no defects"))
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
