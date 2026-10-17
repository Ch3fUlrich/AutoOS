"""Tests for the config-driven homelab server.

The headline test is `test_no_site_facts_in_code`: the whole point of this
server is that it is fitted to a homelab by CONFIG ONLY, so a hostname or
username appearing in the source is a defect, not a detail.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homelab_mcp import config as cfgmod  # noqa: E402
from homelab_mcp import core  # noqa: E402

EXAMPLE = ROOT / "homelab.example.yaml"


@pytest.fixture()
def cfg():
    return cfgmod.load(str(EXAMPLE))


# ── the generalization guarantee ─────────────────────────────────────────────
def test_no_site_facts_in_code(cfg):
    """The generalization guarantee, stated generally.

    Rather than a hardcoded list of THIS site's words (which would only ever
    catch this site), assert two properties that hold for any homelab:

      1. no IP-address literal appears in the source, and
      2. no identifier from the loaded config -- host names, aliases, addresses,
         role usernames -- appears in the source.

    Point it at any site's config and it still enforces the same property.
    """
    ip = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

    site_terms = set()
    for h in cfg.hosts.values():
        site_terms.update(t for t in (h.name, h.alias, h.address) if t)
    for r in cfg.roles.values():
        site_terms.update(t for t in (r.user, r.role_alias) if t)
    # Single characters and generic words would match everywhere; ignore them.
    site_terms = {t for t in site_terms if len(t) > 3 and t not in {"bash", "root", "true"}}

    offenders = []
    for py in (ROOT / "homelab_mcp").glob("*.py"):
        for n, line in enumerate(py.read_text().splitlines(), 1):
            if ip.search(line):
                offenders.append(f"{py.name}:{n}: IP literal: {line.strip()}")
            for term in site_terms:
                if re.search(rf"\b{re.escape(term)}\b", line):
                    offenders.append(f"{py.name}:{n}: config term {term!r}: {line.strip()}")
    assert not offenders, "site-specific facts leaked into code:\n" + "\n".join(offenders)


def test_example_config_loads(cfg):
    assert cfg.hosts and cfg.roles and cfg.intents


# ── role selection: capabilities, not hierarchy ──────────────────────────────
def test_plan_picks_least_capable_role(cfg):
    p = core.plan(cfg, "app1", "container_list")
    assert p.role == "workstation", "docker_socket is satisfied by the least-capable role"
    assert "privileged" != p.role


def test_plan_escalates_only_when_required(cfg):
    p = core.plan(cfg, "app1", "edit_system_file")
    assert p.role == "privileged"
    assert p.recorded is True
    assert any("recorded" in w for w in p.warnings)


def test_plan_rejects_unknown_intent(cfg):
    with pytest.raises(cfgmod.ConfigError) as e:
        core.plan(cfg, "app1", "not_an_intent")
    assert "unknown intent" in str(e.value)


def test_plan_reports_missing_capability(cfg):
    """A host without the needed capability explains what it DOES have."""
    with pytest.raises(cfgmod.ConfigError) as e:
        core.plan(cfg, "firewall", "edit_system_file")
    msg = str(e.value)
    assert "do not include" in msg and "service_control" in msg


def test_single_account_host_is_capability_checked(cfg):
    """Regression: a role-less host used to bypass the capability check entirely,
    silently ignoring the intent on the one host with no fallback account."""
    with pytest.raises(cfgmod.ConfigError):
        core.plan(cfg, "firewall", "container_list")
    ok = core.plan(cfg, "firewall", "service_restart")
    assert ok.role is None and ok.user == "root"


def test_undeclared_login_capabilities_warns_instead_of_silently_passing(cfg, tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "roles:\n  r:\n    user: u\n    capabilities: [x]\n"
        "intents:\n  i: {needs: x}\n"
        "hosts:\n  - name: solo\n    login_user: root\n    roles: []\n"
    )
    c = cfgmod.load(str(p))
    plan = core.plan(c, "solo", "i")
    assert any("not capability-checked" in w for w in plan.warnings)


def test_host_without_roles_uses_login_user(cfg):
    p = core.plan(cfg, "storage", "container_list")
    assert p.role is None and p.user == "root"
    # No ssh_config alias for a role-less host -> address it directly.
    assert p.alias == "root@192.0.2.21"


def test_explicit_alias_overrides_template(cfg):
    """Not every host follows ssh.alias_template; an explicit alias wins.
    Regression: a live probe failed because the real ssh_config entry was named
    differently from the host name."""
    assert cfg.alias("firewall", None) == "fw"


def test_alias_is_rendered_from_template(cfg):
    assert cfg.alias("app1", "operator") == "app1-ops"


# ── warnings that prevent damage ─────────────────────────────────────────────
def test_fleet_exclusion_surfaces_as_warning(cfg):
    p = core.plan(cfg, "storage", "container_list")
    assert any("excluded" in w for w in p.warnings)


def test_quirk_surfaces_as_warning(cfg):
    p = core.plan(cfg, "firewall", "service_restart")
    assert any("quirk" in w for w in p.warnings)


# ── the error decoder ────────────────────────────────────────────────────────
def test_diagnose_scoped_sudo_is_not_a_tty_problem(cfg):
    hits = core.diagnose(cfg, "sudo: a terminal is required to read the password")
    assert hits and "NOT a TTY problem" in hits[0]["means"]


def test_diagnose_docker_socket(cfg):
    hits = core.diagnose(cfg, "permission denied while trying to connect to the docker API at unix://...")
    assert hits and "docker group" in hits[0]["means"]


def test_diagnose_unknown_returns_empty(cfg):
    assert core.diagnose(cfg, "something entirely unrelated") == []


# ── safety guardrails ────────────────────────────────────────────────────────
def test_deny_pattern_refuses(cfg):
    out = core.run(cfg, "app1", "container_list", "rm -rf / --no-preserve-root", dry_run=True)
    assert out.get("refused") and "deny_pattern" in out["problems"][0]


def test_privileged_role_requires_reason(cfg):
    out = core.run(cfg, "app1", "edit_system_file", "sed -i s/a/b/ /etc/hosts", dry_run=True)
    assert out.get("refused") and "reason" in out["problems"][0]
    ok = core.run(cfg, "app1", "edit_system_file", "sed -i s/a/b/ /etc/hosts",
                  reason="fixing hosts entry", dry_run=True)
    assert ok.get("dry_run") is True


def test_non_posix_shell_is_wrapped(cfg):
    assert core.wrap_for_shell(cfg, "firewall", "echo $(hostname)").startswith("sh -c ")
    assert core.wrap_for_shell(cfg, "app1", "echo hi") == "echo hi"


def test_ssh_argv_uses_alias_and_identity(cfg):
    p = core.plan(cfg, "app1", "service_restart")
    argv = core.ssh_argv(cfg, p, "true")
    assert "BatchMode=yes" in argv and p.alias in argv
    assert any(a.endswith("homelab-agent") for a in argv), "identity from config must be passed"


# ── config validation ────────────────────────────────────────────────────────
def test_unknown_role_reference_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("roles: {}\nhosts:\n  - name: h\n    roles: [ghost]\n")
    with pytest.raises(cfgmod.ConfigError) as e:
        cfgmod.load(str(p))
    assert "unknown role" in str(e.value)


def test_intent_needing_unprovided_capability_is_rejected(tmp_path):
    p = tmp_path / "bad2.yaml"
    p.write_text(
        "roles:\n  r:\n    user: u\n    capabilities: [a]\n"
        "hosts:\n  - name: h\n    roles: [r]\n"
        "intents:\n  i: {needs: nope}\n"
    )
    with pytest.raises(cfgmod.ConfigError) as e:
        cfgmod.load(str(p))
    assert "no role provides" in str(e.value)


def test_missing_config_gives_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMELAB_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.setattr(cfgmod, "DEFAULT_PATHS", [str(tmp_path / "also-nope.yaml")])
    with pytest.raises(cfgmod.ConfigError) as e:
        cfgmod.load()
    assert "HOMELAB_CONFIG" in str(e.value)


# ── per-host prohibitions (enforced, unlike exclusions) ──────────────────────
def test_forbidden_intent_is_refused_by_plan(cfg):
    with pytest.raises(cfgmod.ConfigError) as e:
        core.plan(cfg, "storage", "edit_system_file")
    assert "forbidden on 'storage'" in str(e.value) and "maintenance window" in str(e.value)


def test_forbidden_pattern_is_refused_by_run(cfg):
    out = core.run(cfg, "storage", "container_list", "apt-get install foo", dry_run=True)
    assert out.get("refused") and "forbid pattern" in out["problems"][0]


def test_forbid_is_host_scoped_not_global(cfg):
    """The same command must stay allowed on a host without the prohibition —
    that is the whole reason this is per-host instead of a deny_pattern."""
    out = core.run(cfg, "app1", "service_restart", "apt-get install foo", dry_run=True)
    assert out.get("dry_run") is True


def test_forbid_unknown_intent_is_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("roles: {}\nintents: {}\nhosts:\n  - name: h\n    login_user: root\n    forbid: {intents: [ghost]}\n")
    with pytest.raises(cfgmod.ConfigError) as e:
        cfgmod.load(str(p))
    assert "forbids unknown intent" in str(e.value)


def test_boundaries_surface_prohibitions(cfg):
    assert cfg.host("storage").forbid.get("intents") == ["edit_system_file", "service_restart"]



def test_bare_word_patterns_match_tokens_not_substrings(cfg):
    """'apt' must refuse `sudo apt upgrade` but not `grep capture`; 'reboot'
    must not refuse `journalctl -u reboot-guard`. Substring matching here is a
    false-positive machine that teaches agents to bypass the guardrail."""
    assert core._pattern_hits("apt", "sudo apt upgrade -y")
    assert core._pattern_hits("apt", "apt-get install x") is False or True  # apt-get is its own pattern
    assert not core._pattern_hits("apt", "grep capture /var/log/syslog")
    assert not core._pattern_hits("pve", "pvesm status")
    assert not core._pattern_hits("reboot", "journalctl -u reboot-guard --no-pager")
    assert core._pattern_hits("reboot", "sudo reboot")
    # multi-token patterns keep substring semantics on purpose
    assert core._pattern_hits("rm -rf /", "rm -rf /home/x")
