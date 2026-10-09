"""The shared Serena MCP server must never expose its memory tools (D-869).

Operator rule (2026-10-09): "serena memory should not be used at all" — Omnigraph
is the only memory layer (ADR 0003). The repo already hid Serena's memory tools
host-side (`~/.serena/serena_config.yml`, written by install.sh and
AutoOS.Install.psm1) but the *user-scope SSE server* on 127.0.0.1:9121, which
Claude Code sessions connect to, runs in a container with its own
`/root/.serena` volume and never reads that host file — so it still advertised
write_memory / read_memory / list_memories / edit_memory / rename_memory /
delete_memory (plus `onboarding`, which writes them).

These tests are hermetic: they read the compose file, the context file it mounts,
and the docs, and they run tools/check-serena-tools.py against a *fixture* tool
list. Nothing here starts Serena, opens a socket, or touches 9121 — the live
probe (`--url`) is a manual run by the operator/L1 after the recreated container
is up, and its output is the thing these tests cannot produce.

Run from the repo root:

    python3 -m pytest -q tests/test_serena_shared_server_no_memory.py
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "infra" / "mcp-servers" / "docker-compose.client.yml"
CONTEXT = ROOT / "infra" / "mcp-servers" / "config" / "serena-context-no-memory.yml"
CHECKER = ROOT / "tools" / "check-serena-tools.py"
FIXTURE_WITH = ROOT / "tests" / "fixtures" / "serena" / "tools-list-with-memory.json"
FIXTURE_WITHOUT = ROOT / "tests" / "fixtures" / "serena" / "tools-list-no-memory.json"

# The six tools Serena's memory layer is made of, plus onboarding, which is
# excluded with them because it writes them (serena's own `no-memories` mode says
# so). Taken from the one catalog entry every client shares, never restated.
HARNESS = json.loads((ROOT / "catalog" / "agent-harness.json").read_text(encoding="utf-8"))
MEMORY_TOOLS = sorted(HARNESS["mcp_servers"]["serena"]["memory_tools"])
FORBIDDEN = sorted(set(MEMORY_TOOLS) | {"onboarding"})

# The vendor `claude-code` context (what `--context ide-assistant` resolved to)
# already excluded these; the fork must not quietly re-expose any of them.
VENDOR_EXCLUDED = [
    "create_text_file",
    "read_file",
    "execute_shell_command",
    "find_file",
    "list_dir",
    "search_for_pattern",
]


def load_checker():
    spec = importlib.util.spec_from_file_location("check_serena_tools", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compose_serena_block():
    """The serena service of docker-compose.client.yml, as plain text.

    Read as text, not parsed: pyyaml is not guaranteed on a machine where nothing
    is installed yet, and the assertions are about exact command/mount strings.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    start = text.index("\n  serena:")
    rest = text[start + 1:]
    end = len(text)
    for marker in ("\n  graphify:", "\n  sentry-mcp:"):
        offset = rest.find(marker)
        if offset != -1:
            end = min(end, start + 1 + offset)
    return text[start + 1:end]


def context_excluded_tools():
    names = []
    lines = CONTEXT.read_text(encoding="utf-8").splitlines()
    in_block = False
    for line in lines:
        if line.startswith("excluded_tools:"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith("- "):
                names.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                break
    return names


# --- the compose service carries the exclusion -------------------------------

def test_compose_passes_the_no_memory_context_file():
    block = compose_serena_block()
    assert "--context /serena-config/serena-context-no-memory.yml" in block, block
    # the old value must be gone: it resolved to the vendor context, which
    # exposes the memory tools
    assert "--context ide-assistant" not in block


def test_compose_mounts_the_context_file_read_only_at_that_path():
    block = compose_serena_block()
    mounts = [line.strip().removeprefix("- ") for line in block.splitlines()
              if "serena-context-no-memory.yml:" in line]
    assert len(mounts) == 1, block
    source, destination, mode = mounts[0].split(":")
    assert source == "./config/serena-context-no-memory.yml", mounts[0]
    assert mode == "ro", f"{mounts[0]}: Serena re-writes files it can write; the context must not be one"
    # mount destination and the --context argument are the same path, or Serena
    # starts and simply cannot find its context
    assert f"--context {destination}" in block, block


def test_compose_serena_service_is_otherwise_unchanged():
    block = compose_serena_block()
    for fragment in (
        "image: ghcr.io/oraios/serena:latest",
        "container_name: serena-mcp",
        "--transport sse --port 9121 --host 0.0.0.0",
        "SERENA_DOCKER=1",
        "serena_data:/root/.serena",
        '"127.0.0.1:9121:9121"',
    ):
        assert fragment in block, fragment


# --- the context file itself says no ----------------------------------------

def test_context_excludes_every_memory_tool_and_onboarding():
    excluded = set(context_excluded_tools())
    missing = [name for name in FORBIDDEN if name not in excluded]
    assert not missing, f"context file does not exclude: {missing}"


def test_context_still_excludes_the_vendor_tool_set():
    excluded = set(context_excluded_tools())
    missing = [name for name in VENDOR_EXCLUDED if name not in excluded]
    assert not missing, f"fork lost the vendor exclusions: {missing}"


def test_context_memory_names_match_the_catalog_field():
    # catalog/agent-harness.json is the single source; a rename there must break
    # this test rather than leave the container serving the old name.
    excluded = set(context_excluded_tools())
    assert set(MEMORY_TOOLS) <= excluded, (MEMORY_TOOLS, sorted(excluded))
    assert len(MEMORY_TOOLS) == 6, MEMORY_TOOLS


def test_context_is_valid_yaml_and_only_uses_known_keys():
    yaml = pytest.importorskip("yaml")
    document = yaml.safe_load(CONTEXT.read_text(encoding="utf-8"))
    assert set(document) <= {
        "name", "description", "prompt", "excluded_tools",
        "included_optional_tools", "fixed_tools", "tool_description_overrides",
        "single_project", "structured_tool_output",
    }, sorted(document)
    # a context that set fixed_tools *and* excluded_tools makes Serena raise at
    # startup (ToolInclusionDefinition.is_fixed_tool_set), taking every tool down
    assert not (document.get("fixed_tools") and document.get("excluded_tools"))
    assert isinstance(document["prompt"], str) and "Omnigraph" in document["prompt"]


# --- the checker's fixture mode (no server, no socket) ----------------------

def test_fixture_shape_is_a_tools_list_result():
    document = json.loads(FIXTURE_WITH.read_text(encoding="utf-8"))
    assert "tools" in document
    names = [tool["name"] for tool in document["tools"]]
    assert set(FORBIDDEN) <= set(names), "the with-memory fixture must look like the broken server"
    assert "find_symbol" in names


def run_checker(*args):
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )


def test_checker_fails_on_the_fixture_that_exposes_memory_tools():
    proc = run_checker("--tools-file", str(FIXTURE_WITH.relative_to(ROOT)))
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    for name in FORBIDDEN:
        assert name in proc.stdout, (name, proc.stdout)


def test_checker_passes_on_the_fixture_without_memory_tools():
    proc = run_checker("--tools-file", str(FIXTURE_WITHOUT.relative_to(ROOT)))
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


def test_checker_rejects_malformed_or_missing_fixture_with_exit_two():
    missing = run_checker("--tools-file", "tests/fixtures/serena/does-not-exist.json")
    assert missing.returncode == 2, missing
    assert "ERROR" in missing.stderr, missing.stderr


def test_tool_names_from_document_accepts_every_captured_shape():
    mod = load_checker()
    names = ["find_symbol", "write_memory"]
    assert mod.tool_names_from_document(names) == names
    assert mod.tool_names_from_document([{"name": "find_symbol"}, {"name": "write_memory"}]) == names
    assert mod.tool_names_from_document({"tools": [{"name": "find_symbol"}]}) == ["find_symbol"]
    assert mod.tool_names_from_document({"result": {"tools": ["find_symbol"]}}) == ["find_symbol"]
    for bad in ({}, "nope", [123]):
        try:
            mod.tool_names_from_document(bad)
        except mod.RpcError:
            continue
        raise AssertionError(f"expected RpcError for {bad!r}")


def test_checker_judges_the_two_fixtures_differently_in_process():
    mod = load_checker()
    with_memory = mod.tool_names_from_document(json.loads(FIXTURE_WITH.read_text(encoding="utf-8")))
    without = mod.tool_names_from_document(json.loads(FIXTURE_WITHOUT.read_text(encoding="utf-8")))
    assert mod.judge(with_memory)[0] is False
    ok, problems = mod.judge(without)
    assert ok and problems == [], problems


def test_checker_url_and_tools_file_are_mutually_exclusive():
    proc = run_checker("--url", "http://127.0.0.1:1/sse", "--tools-file", str(FIXTURE_WITH))
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "not allowed with argument" in proc.stderr, proc.stderr


# --- the docs stop recommending serena memory -------------------------------

DOC_FILES = [
    ROOT / "infra" / "mcp-servers" / "docs" / "INSTALL-GUIDE.md",
    ROOT / "infra" / "mcp-servers" / "README.md",
    ROOT / "infra" / "mcp-servers" / "docker-compose.client.yml",
    ROOT / "infra" / "mcp-servers" / "config" / "serena-context-no-memory.yml",
]


def test_no_doc_shows_a_serena_memory_call_as_usage():
    # a call-shaped mention (mcp_serena_write_memory(...), serena_write_memory,
    # memory_name=) is an instruction; a prose sentence naming the tools is not.
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for fragment in ("mcp_serena_write_memory", "mcp_serena_read_memory",
                         "serena_write_memory(", "serena_read_memory(", "memory_name="):
            assert fragment not in text, (path, fragment)


def test_install_guide_recommends_omnigraph_for_project_notes():
    text = (ROOT / "infra" / "mcp-servers" / "docs" / "INSTALL-GUIDE.md").read_text(encoding="utf-8")
    assert "mcp_omnigraph_mutate" in text and "mcp_omnigraph_query" in text
    assert "structured-memory" in text
