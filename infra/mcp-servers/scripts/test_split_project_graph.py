import importlib.util
import os
import sys
import pytest

script_path = os.path.join(os.path.dirname(__file__), "split-project-graph.py")
spec = importlib.util.spec_from_file_location("split_project_graph", script_path)
split_project_graph = importlib.util.module_from_spec(spec)
sys.modules["split_project_graph"] = split_project_graph
spec.loader.exec_module(split_project_graph)


def test_partition_valid_subgraph():
    records = [
        {"type": "Project", "data": {"slug": "proj-a", "name": "Project A"}},
        {"type": "Decision", "data": {"slug": "dec-1", "title": "Decision 1"}},
        {"type": "Component", "data": {"slug": "comp-1", "name": "Component 1"}},
        {"type": "Project", "data": {"slug": "proj-b", "name": "Project B"}},
        # Hub edges
        {"edge": "DecidedIn", "from": "dec-1", "to": "proj-a"},
        {"edge": "PartOf", "from": "comp-1", "to": "proj-a"},
        # Relational edge inside project
        {"edge": "Affects", "from": "dec-1", "to": "comp-1"},
        # Relational edge leaving project
        {"edge": "Affects", "from": "dec-1", "to": "proj-b"},
    ]

    nodes, edges, warnings = split_project_graph.partition(records, "proj-a")

    node_slugs = {n["data"]["slug"] for n in nodes}
    assert node_slugs == {"proj-a", "dec-1", "comp-1"}
    
    assert len(edges) == 3
    edge_pairs = {(e["edge"], e["from"], e["to"]) for e in edges}
    assert ("DecidedIn", "dec-1", "proj-a") in edge_pairs
    assert ("PartOf", "comp-1", "proj-a") in edge_pairs
    assert ("Affects", "dec-1", "comp-1") in edge_pairs
    assert ("Affects", "dec-1", "proj-b") not in edge_pairs

    assert any("leaves the project" in w for w in warnings)


def test_partition_missing_project_slug():
    records = [
        {"type": "Project", "data": {"slug": "proj-other", "name": "Other"}}
    ]
    with pytest.raises(SystemExit) as exc_info:
        split_project_graph.partition(records, "proj-missing")
    assert "no node with slug 'proj-missing'" in str(exc_info.value)


def test_partition_wrong_node_type():
    records = [
        {"type": "Decision", "data": {"slug": "dec-only", "title": "Not a project"}}
    ]
    with pytest.raises(SystemExit) as exc_info:
        split_project_graph.partition(records, "dec-only")
    assert "is a Decision, not a Project" in str(exc_info.value)
