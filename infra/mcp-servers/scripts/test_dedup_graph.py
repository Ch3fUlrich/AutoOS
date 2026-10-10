"""P2d DEDUP-GRAPH (D-1038): the 0.13 snapshot shape and the fail-closed image guard.

Offline, stdlib + pytest. `dedup-graph.py` has a hyphenated name, so it is loaded
with importlib exactly like test_split_project_graph.py loads its script.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types

import pytest

script_path = os.path.join(os.path.dirname(__file__), "dedup-graph.py")
spec = importlib.util.spec_from_file_location("dedup_graph", script_path)
dedup_graph = importlib.util.module_from_spec(spec)
sys.modules["dedup_graph"] = dedup_graph
spec.loader.exec_module(dedup_graph)

# Measured against a real Omnigraph 0.13.0 server (server-L1), `snapshot --json`.
SNAPSHOT_013 = {
    "graph_branch": "main", "graph_manifest_version": 3, "internal_schema_version": 14,
    "datasets": [
        {"entity_kind": "edge", "type_name": "Affects", "entity_count": 12,
         "published_dataset_version": 2, "native_dataset_branch": None,
         "dataset_path": "edges/0000000000000009-0000000000000036"},
        {"entity_kind": "node", "type_name": "Decision", "entity_count": 16,
         "published_dataset_version": 2, "native_dataset_branch": None,
         "dataset_path": "nodes/0000000000000002-0000000000000017"},
        {"entity_kind": "node", "type_name": "Rule", "entity_count": 8,
         "published_dataset_version": 2, "native_dataset_branch": None,
         "dataset_path": "nodes/0000000000000018-0000000000000025"},
    ],
}

# What omnigraph-server v0.8.1 answered to the same call.
SNAPSHOT_081 = {"tables": [{"tableKey": "node:Decision", "rowCount": 16},
                           {"tableKey": "edge:Affects", "rowCount": 12}]}


def test_snapshot_node_count_sums_only_node_datasets_on_013():
    assert dedup_graph.snapshot_node_count(SNAPSHOT_013) == 24


def test_snapshot_node_count_keeps_the_081_tables_read():
    assert dedup_graph.snapshot_node_count(SNAPSHOT_081) == 16


def test_snapshot_node_count_is_minus_one_on_any_shape_it_cannot_read():
    # -1 is what the callers abort on: an unknown state is never a count.
    assert dedup_graph.snapshot_node_count({}) == -1
    assert dedup_graph.snapshot_node_count({"datasets": "x"}) == -1
    assert dedup_graph.snapshot_node_count({"tables": "x"}) == -1
    assert dedup_graph.snapshot_node_count(None) == -1


def test_snapshot_node_count_of_an_empty_graph_is_zero_not_minus_one():
    assert dedup_graph.snapshot_node_count({"datasets": []}) == 0
    assert dedup_graph.snapshot_node_count({"tables": []}) == 0


def test_node_count_probes_snapshot_without_a_json_flag(monkeypatch):
    # 0.8.x prints JSON by DEFAULT; --json is a 0.13 flag. Pass it anyway and the 0.8
    # CLI rejects it, stdout comes back empty, node_count returns -1 and the rebuild
    # aborts at its "graph is not verifiably empty" guard — the only supported path.
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=json.dumps(SNAPSHOT_081), returncode=0)

    monkeypatch.setattr(dedup_graph.subprocess, "run", fake_run)
    a = types.SimpleNamespace(network="mcp-server_mcp-net", image="modernrelay/omnigraph-server:v0.8.1",
                              server="http://omnigraph-server:8080")
    assert dedup_graph.node_count(a, "tok", "memory") == 16

    script = calls[0][calls[0].index("-c") + 1]
    assert "omnigraph snapshot --server local --graph memory" in script
    assert "--json" not in script


def test_refuse_unsupported_image_passes_a_0_8_tag():
    dedup_graph.refuse_unsupported_image("modernrelay/omnigraph-server:v0.8.1")


def test_refuse_unsupported_image_aborts_on_013_and_on_any_tag_it_cannot_read():
    for image in ("modernrelay/omnigraph-server:v0.13.0",
                  "modernrelay/omnigraph-server:v0.13.0@sha256:abc",
                  "modernrelay/omnigraph-server:v1.0.0",
                  "modernrelay/omnigraph-server:latest",
                  "modernrelay/omnigraph-server"):
        with pytest.raises(SystemExit) as exc_info:
            dedup_graph.refuse_unsupported_image(image)
        assert str(exc_info.value).startswith("[dedup] ABORT:"), image


def test_refuse_unsupported_image_names_the_013_migration_path():
    with pytest.raises(SystemExit) as exc_info:
        dedup_graph.refuse_unsupported_image("modernrelay/omnigraph-server:v0.13.0")
    msg = str(exc_info.value)
    assert "0.8" in msg and "omnigraph-migrate-013.py" in msg


# ── 0.13 export records through the existing cleaner ────────────────────────────
# 0.13 puts the id at TOP level (node: the slug; edge: a ULID) and leaves edges
# with an empty `data`. clean_records must not carry either into the reload.
NODES_013 = [
    {"type": "Rule", "id": "Foo", "data": {"slug": "Foo", "statement": "first"}},
    {"type": "Rule", "id": "foo", "data": {"slug": "foo", "statement": "second",
                                           "why": "extra"}},
    {"type": "Decision", "id": "dec-1",
     "data": {"slug": "dec-1", "title": "D1", "embedding": [0.1, 0.2]}},
]
EDGES_013 = [
    {"edge": "Affects", "id": "01ABC", "from": "Foo", "to": "dec-1", "data": {}},
    {"edge": "Affects", "id": "01ABC", "from": "Foo", "to": "dec-1", "data": {}},
    {"edge": "Affects", "id": "01DEF", "from": "foo", "to": "dec-1", "data": {}},
]


def test_clean_records_drops_the_top_level_and_data_id_on_013_nodes():
    dupes = dedup_graph.find_dupes(NODES_013, False)
    merged, _, _ = dedup_graph.clean_records(NODES_013, [], dupes, {})
    assert merged, "expected at least one merged node"
    for node in merged.values():
        assert "id" not in node, node
        assert "id" not in node["data"], node
        assert "embedding" not in node["data"], node


def test_clean_records_emits_edges_with_exactly_edge_from_to():
    # No dupes here, so only the byte-identical repeat collapses; the `foo` variant is
    # a different key and survives.
    _, out_edges, edge_dupes = dedup_graph.clean_records([], EDGES_013, [], {})
    for e in out_edges:
        assert set(e) == {"edge", "from", "to"}, e
    assert edge_dupes == 1, "the byte-identical duplicate edge must collapse"
    assert len(out_edges) == 2


def test_clean_records_redirects_a_case_variant_to_the_canonical_lowercase_slug():
    # canonical() prefers a lowercase slug (dedup-graph.py lines 142-144), so 'Foo'
    # maps onto 'foo'; the merged node is built from the FIRST member seen, so the
    # canonical node keeps 'Foo''s own fields and only picks up what is missing
    # (setdefault, lines 168-172) — here 'why' — while 'statement' stays "first".
    dupes = dedup_graph.find_dupes(NODES_013, False)
    assert len(dupes) == 1
    merged, out_edges, _ = dedup_graph.clean_records(NODES_013, EDGES_013, dupes, {})
    assert set(merged) == {"foo", "dec-1"}
    assert merged["foo"]["data"]["slug"] == "foo"
    assert merged["foo"]["data"]["statement"] == "first"
    assert merged["foo"]["data"]["why"] == "extra"
    assert [(e["edge"], e["from"], e["to"]) for e in out_edges] == [
        ("Affects", "foo", "dec-1")]


# ── D-1038 P2d2 (judge HOLD D-852): these tests must be physically unable to
# reach a real docker. `dedup-graph.py` wipes the MinIO store; one test patched
# subprocess.run itself, but nothing stopped another test from falling through
# to the real spawn. The autouse fixture below stubs every spawn entry point,
# pins PATH to an empty dir and DOCKER_HOST to an unreachable socket.
SPAWN_STUB_MSG = "test reached a real process spawn"
SPAWN_CALLS = []      # every argv the stub refused, newest last — tests inspect this
EMPTY_BIN = None      # the fixture's empty PATH dir; None until the fixture runs


@pytest.fixture(autouse=True)
def no_real_process_spawns(monkeypatch, tmp_path):
    """Autouse shield (D-1038 P2d2): no test in this file can spawn a process.

    `dedup_graph.subprocess` / `dedup_graph.os` ARE the stdlib modules (plain
    `import subprocess` / `import os` in the script), so patching the modules
    covers every call site: sh, cli, list_graphs, export_graph, node_count and
    main(). A test may still opt into a specific fake by monkeypatching
    `dedup_graph.subprocess.run` itself AFTER this fixture (the node_count test
    above does) — fixture patches land first and unwind last.
    """
    global EMPTY_BIN
    del SPAWN_CALLS[:]
    EMPTY_BIN = tmp_path / "empty-bin"
    EMPTY_BIN.mkdir()

    def argv_repr(args):
        for item in args:
            if isinstance(item, (list, tuple)):
                return " ".join(str(x) for x in item)
            if isinstance(item, str):
                return item
        return repr(args[0] if args else "")

    def no_spawn(*args, **kwargs):
        argv = argv_repr(args)
        SPAWN_CALLS.append(argv)
        raise AssertionError(f"{SPAWN_STUB_MSG}: {argv}")

    for module, names in (
            (subprocess, ("run", "Popen", "call", "check_call", "check_output")),
            (os, ("system", "execv", "execvp", "spawnvp", "posix_spawn"))):
        for name in names:
            if hasattr(module, name):  # posix_spawn/spawnvp are not on every platform
                monkeypatch.setattr(module, name, no_spawn)
    # `docker` can no longer be found by any lookup, and even a remembered socket
    # path points nowhere.
    monkeypatch.setenv("PATH", str(EMPTY_BIN))
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent-og013-test.sock")


def test_no_real_docker_reachable():
    # (a) no lookup finds docker, (b) every spawn path raises the stub, (c) even
    # the module's own wrapper `sh` cannot fall through to a real process.
    assert shutil.which("docker") is None
    assert os.environ["PATH"] == str(EMPTY_BIN)
    assert os.environ["DOCKER_HOST"] == "unix:///nonexistent-og013-test.sock"
    with pytest.raises(AssertionError, match=SPAWN_STUB_MSG):
        subprocess.run(["docker", "ps"])
    with pytest.raises(AssertionError, match=SPAWN_STUB_MSG):
        dedup_graph.sh(["docker", "ps"])


def test_main_dry_run_is_intercepted_by_the_spawn_stub(monkeypatch):
    # The parser requires nothing beyond the script name (every option has a
    # default) plus the brief's --image/--dry-run: with --dry-run the D-825 gate
    # is skipped (enforce returns at once for a non-mutating run), so main()'s
    # first spawn is _omni_env.detect_network's `docker inspect omnigraph-server`
    # — which must hit the stub, never a real process.
    monkeypatch.delenv("OMNI_NET", raising=False)
    monkeypatch.setattr(sys, "argv", [script_path, "--image",
                                      "modernrelay/omnigraph-server:v0.8.1", "--dry-run"])
    with pytest.raises(AssertionError, match=SPAWN_STUB_MSG):
        dedup_graph.main()
    assert SPAWN_CALLS, "main() should have reached the stub, not run silently"
    assert any("docker inspect omnigraph-server" in c for c in SPAWN_CALLS)


def test_every_spawning_function_raises_under_the_fixture():
    a = types.SimpleNamespace(network="mcp-server_mcp-net", server="http://omnigraph-server:8080",
                              image="modernrelay/omnigraph-server:v0.8.1")
    for call in (lambda: dedup_graph.sh(["docker", "ps"]),
                 lambda: dedup_graph.cli("omnigraph snapshot", a.network, a.image, "tok"),
                 lambda: dedup_graph.list_graphs(a.server, a.network, a.image, "tok"),
                 lambda: dedup_graph.export_graph(a.server, a.network, a.image, "tok"),
                 # retries=1 keeps a pre-fixture failure fast; post-fixture the
                 # stub raises on the first attempt anyway.
                 lambda: dedup_graph.node_count(a, "tok", retries=1)):
        with pytest.raises(AssertionError, match=SPAWN_STUB_MSG):
            call()
