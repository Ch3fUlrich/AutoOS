import shlex
import subprocess
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
import importlib.util

REPO_ROOT = REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "combo_drift.py"

def load_module():
    spec = importlib.util.spec_from_file_location("combo_drift", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_parse_live():
    m = load_module()
    text = (
        "combo1\x1fstrat1\x1fref1,ref2\x1f0\n"
        "combo2\x1f\x1fref3\x1f0\n"
        "combo3\x1fstrat3\x1fref4,ref5\x1f2\n"
        "\n"
        "badline\n"
        "combo4\x1fstrat4\x1fref6\x1f0\n"
    )
    live = m.parse_live(text)
    assert live == {
        "combo1": {"strategy": "strat1", "models": ["ref1", "ref2"], "dropped": 0},
        "combo2": {"strategy": "", "models": ["ref3"], "dropped": 0},
        "combo3": {"strategy": "strat3", "models": ["ref4", "ref5"], "dropped": 2},
        "combo4": {"strategy": "strat4", "models": ["ref6"], "dropped": 0},
    }

def test_load_known_gaps_missing_file():
    m = load_module()
    gaps, unmanaged = m.load_known_gaps(Path("/nonexistent/path.json"))
    assert gaps == {}
    assert unmanaged == set()

def test_load_known_gaps_reason_error():
    m = load_module()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"gaps": [{"ref": "ref1"}]}, f)
        f.flush()
        try:
            m.load_known_gaps(Path(f.name))
            assert False, "expected ValueError"
        except ValueError as e:
            assert "known gap needs a reason: ref1" in str(e)
        finally:
            os.unlink(f.name)

def test_load_known_gaps_valid():
    m = load_module()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"gaps": [{"ref": "ref1", "reason": "test"}, {"ref": "ref2", "reason": "why"}], "unmanaged": ["unm1"]}, f)
        f.flush()
        try:
            gaps, unmanaged = m.load_known_gaps(Path(f.name))
            assert gaps == {"ref1": "test", "ref2": "why"}
            assert unmanaged == {"unm1"}
        finally:
            os.unlink(f.name)

def test_diff_missing_drift_extra_knowngap():
    m = load_module()
    file_doc = {
        "retired": ["retired1"],
        "omitted": ["omitted1"],
        "combos": [
            {"name": "c1", "strategy": "s1", "models": ["a", "b"]},
            {"name": "c2", "strategy": "s2", "models": ["x", "y"]},
            {"name": "c3", "strategy": "s3", "models": ["p", "q"]},
            {"name": "c4", "strategy": "s4", "models": ["m", "n"]},
            {"name": "c5", "strategy": "s5", "models": ["g1"]},
        ],
    }
    live = {
        "c1": {"strategy": "s1", "models": ["a", "b"], "dropped": 0},
        "c2": {"strategy": "s2", "models": ["y", "x"], "dropped": 0},
        "c6": {"strategy": "s6", "models": ["z"], "dropped": 0},
        "omitted1": {"strategy": "s", "models": ["o"], "dropped": 0},
    }
    gaps = {"g1": "reason1"}
    unmanaged = {"unm1"}
    diffs = m.diff(file_doc, live, gaps, unmanaged)
    kinds = [d[0] for d in diffs]
    assert "missing" in kinds
    assert "drift" in kinds
    assert "extra" in kinds
    assert "known-gap" in kinds
    drift_details = [d for d in diffs if d[0] == "drift"]
    assert any("c2" in d[1] for d in drift_details)
    extra_details = [d for d in diffs if d[0] == "extra"]
    assert any("omitted: no servable leg" in d[2] for d in extra_details if d[1] == "omitted1")
    assert any(d[1] == "c6" and d[2] == "" for d in extra_details)

def test_diff_knowngap_removal_makes_match():
    m = load_module()
    file_doc = {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a", "b", "gap1"]}]}
    live = {"c1": {"strategy": "s", "models": ["a", "b"], "dropped": 0}}
    gaps = {"gap1": "reason"}
    diffs = m.diff(file_doc, live, gaps, set())
    kg = [d for d in diffs if d[0] == "known-gap"]
    assert len(kg) == 1
    assert kg[0][1] == "c1"
    assert kg[0][2] == "gap1"

def test_diff_all_gap_missing_combo():
    m = load_module()
    file_doc = {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["gap1", "gap2"]}]}
    live = {}
    gaps = {"gap1": "r1", "gap2": "r2"}
    diffs = m.diff(file_doc, live, gaps, set())
    kg = [d for d in diffs if d[0] == "known-gap"]
    assert len(kg) == 1
    assert kg[0][1] == "c1"
    assert kg[0][2] == "no live combo"

def test_diff_retired_unmanaged_ignored():
    m = load_module()
    file_doc = {"retired": ["ret1"], "omitted": [], "combos": [{"name": "ret1", "strategy": "s", "models": ["a"]}]}
    live = {"ret1": {"strategy": "s", "models": ["a"], "dropped": 0}}
    diffs = m.diff(file_doc, live, {}, {"unm1"})
    assert diffs == []

def test_diff_dropped_gt_zero_always_drift():
    m = load_module()
    file_doc = {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a", "b"]}]}
    live = {"c1": {"strategy": "s", "models": ["a", "b"], "dropped": 1}}
    diffs = m.diff(file_doc, live, {}, set())
    drift = [d for d in diffs if d[0] == "drift"]
    assert len(drift) == 1
    assert "unreadable steps=1" in drift[0][2]

def test_snapshot_build_and_verify():
    m = load_module()
    live = {
        "c2": {"strategy": "s2", "models": ["x"], "dropped": 0},
        "c1": {"strategy": "s1", "models": ["a", "b"], "dropped": 0},
    }
    snap = m.snapshot_build(live)
    assert snap["count"] == 2
    assert m.snapshot_verify(snap) is True
    assert snap["combos"][0]["name"] == "c1"
    assert snap["combos"][1]["name"] == "c2"
    tampered = dict(snap)
    tampered["sha256"] = "0" * 64
    assert m.snapshot_verify(tampered) is False
    bad = {"count": 1, "sha256": "x", "combos": []}
    assert m.snapshot_verify(bad) is False

def test_guard_unreadable():
    m = load_module()
    ok, reasons = m.guard({}, None, {"retired": [], "omitted": [], "combos": []}, set(), unreadable_reason="store down")
    assert ok is False
    assert reasons == ["live store unreadable: store down"]

def test_guard_no_snapshot_passes_when_live_equals_file():
    m = load_module()
    live = {"c1": {"strategy": "s", "models": ["a"], "dropped": 0}}
    ok, reasons = m.guard(live, None, {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a"]}]}, set())
    assert ok is True and reasons == []
    assert m.guard({}, None, {"retired": [], "omitted": [], "combos": []}, set()) == (True, [])

def test_guard_tampered_snapshot():
    m = load_module()
    live = {"c1": {"strategy": "s", "models": ["z"], "dropped": 0}}
    snap = {"count": 1, "sha256": "bad", "combos": [{"name": "c1", "strategy": "s", "models": ["a"]}]}
    ok, reasons = m.guard(live, snap, {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a"]}]}, set())
    assert ok is False
    assert any("snapshot missing or does not verify" in r for r in reasons)

def test_guard_unexported_live_change_refuse():
    m = load_module()
    live = {"c1": {"strategy": "s", "models": ["a", "b"], "dropped": 0}}
    snap = m.snapshot_build({"c1": {"strategy": "s", "models": ["a"], "dropped": 0}})
    ok, reasons = m.guard(live, snap, {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a"]}]}, set())
    assert ok is False
    assert any("unexported live change: c1" in r for r in reasons)

def test_guard_equal_to_file_pass():
    m = load_module()
    live = {"c1": {"strategy": "s", "models": ["a", "b"], "dropped": 0}}
    snap = m.snapshot_build({"c1": {"strategy": "s", "models": ["a", "b"], "dropped": 0}})
    ok, reasons = m.guard(live, snap, {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a", "b"]}]}, set())
    assert ok is True
    assert reasons == []

def test_guard_equal_to_snapshot_pass():
    m = load_module()
    live = {"c1": {"strategy": "s", "models": ["a", "b"], "dropped": 0}}
    snap = m.snapshot_build(live)
    ok, reasons = m.guard(live, snap, {"retired": [], "omitted": [], "combos": []}, set())
    assert ok is True
    assert reasons == []

def test_guard_dropped_refuse():
    m = load_module()
    live = {"c1": {"strategy": "s", "models": ["a"], "dropped": 1}}
    snap = m.snapshot_build({"c1": {"strategy": "s", "models": ["a"], "dropped": 0}})
    ok, reasons = m.guard(live, snap, {"retired": [], "omitted": [], "combos": [{"name": "c1", "strategy": "s", "models": ["a"]}]}, set())
    assert ok is False
    assert any("unexported live change: c1" in r for r in reasons)

import shlex
import json
import tempfile

def make_cli(tmp_path, live_lines, live_rc=0, combos_models=None, gaps=None, unmanaged=None, snapshot=None):
    """Build test fixtures and return (env, base_args) for subprocess runs."""
    combos_models = combos_models or []
    gaps = gaps or []
    unmanaged = unmanaged or set()
    retired = set()
    omitted = set()

    # fake.py: prints live_lines and exits with live_rc
    fake_py = tmp_path / "fake.py"
    fake_py.write_text(
        "import sys\n"
        f"for line in {live_lines!r}:\n"
        "    print(line)\n"
        f"sys.exit({live_rc})\n",
        encoding="utf-8",
    )

    # combos.json
    combos_file = tmp_path / "combos.json"
    combos_doc = {
        "retired": sorted(retired),
        "omitted": sorted(omitted),
        "combos": [
            {"name": name, "strategy": strat or "", "models": refs}
            for name, strat, refs in combos_models
        ],
    }
    combos_file.write_text(json.dumps(combos_doc, separators=(",", ":")), encoding="utf-8")

    # drift-known-gaps.json
    gaps_file = tmp_path / "drift-known-gaps.json"
    gaps_doc = {
        "gaps": [{"ref": ref, "reason": reason} for ref, reason in gaps],
        "unmanaged": sorted(unmanaged),
    }
    gaps_file.write_text(json.dumps(gaps_doc, separators=(",", ":")), encoding="utf-8")

    # snapshot file (optional)
    snap_file = tmp_path / "snapshot.json"
    if snapshot is not None:
        snap_file.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    # alert inbox
    alert_inbox = tmp_path / "alert.inbox"

    env = os.environ.copy()
    env.update({
        "AUTOOS_COMBO_LIVE_CMD": f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_py))}",
        "AUTOOS_ALERT_INBOX": str(alert_inbox),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    })

    base_args = [
        "--combos", str(combos_file),
        "--known-gaps", str(gaps_file),
        "--snapshot", str(snap_file),
    ]
    return env, base_args, alert_inbox

def test_cli_check_sync_exit0(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f0"], combos_models=[("a", "s", ["x", "y"])])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "in sync" in res.stdout

def test_cli_check_drift_exit1_prints_drift_line(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f0"], combos_models=[("a", "s", ["x", "z"])])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 1
    assert "drift a" in res.stdout

def test_cli_check_unreadable_exit3(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["reason"], live_rc=3, combos_models=[])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 3
    assert "live store unreadable" in res.stdout

def test_cli_check_alert_drift_exit0_one_inbox_line(tmp_path):
    env, base_args, alert_inbox = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f0"], combos_models=[("a", "s", ["x", "z"])])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", "--alert", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert alert_inbox.read_text(encoding="utf-8").strip() == "combo-drift: 1 differences (a)"

def test_cli_check_alert_unreadable_exit3_inbox_line(tmp_path):
    env, base_args, alert_inbox = make_cli(tmp_path, live_lines=["bad"], live_rc=3, combos_models=[])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", "--alert", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 3
    assert "live store unreadable" in alert_inbox.read_text(encoding="utf-8")

def test_cli_check_alert_sync_writes_no_inbox(tmp_path):
    env, base_args, alert_inbox = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f0"], combos_models=[("a", "s", ["x", "y"])])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", "--alert", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert (not alert_inbox.exists()) or alert_inbox.read_text(encoding="utf-8") == ""

def test_cli_check_known_gap_alone_exit0(tmp_path):
    env, base_args, _ = make_cli(
        tmp_path,
        live_lines=["a\x1fs\x1fy\x1f0"],
        combos_models=[("a", "s", ["x", "y"])],
        gaps=[("x", "deprecated")],
    )
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "known-gap a" in res.stdout

def test_cli_check_report_json_names_no_refs(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f0"], combos_models=[("a", "s", ["vendor/model-AAA-secretref", "vendor/model-BBB-secretref"])])
    report_file = tmp_path / "report.json"
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "check", "--report", str(report_file), *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 1
    doc = json.loads(report_file.read_text(encoding="utf-8"))
    assert set(doc.keys()) == {"checked_utc", "result", "counts", "names", "live_sha256"}
    assert doc["result"] == "drift"
    report_text = report_file.read_text(encoding="utf-8")
    assert "vendor/model-AAA-secretref" not in report_text
    assert "vendor/model-BBB-secretref" not in report_text
    assert all(isinstance(v, list) for v in doc["names"].values())

def test_cli_export_refuses_unreadable_exit3(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["bad"], live_rc=3, combos_models=[])
    out_file = tmp_path / "out.json"
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "export", "--out", str(out_file), *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 3
    assert not out_file.exists()

def test_cli_export_refuses_dropped_exit3(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f1"], combos_models=[("a", "s", ["x", "y"])])
    out_file = tmp_path / "out.json"
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "export", "--out", str(out_file), *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 3
    assert not out_file.exists()

def test_cli_export_writes_verifiable_snapshot(tmp_path):
    env, base_args, _ = make_cli(
        tmp_path,
        live_lines=["a\x1fs\x1fx,y\x1f0", "b\x1f\x1fz\x1f0"],
        combos_models=[("a", "s", ["x", "y"]), ("b", "", ["z"]), ("ret", "", ["w"])],
        unmanaged={"ret"},
    )
    out_file = tmp_path / "snap.json"
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "export", "--out", str(out_file), *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert out_file.exists()
    mod = load_module()
    doc = json.loads(out_file.read_text(encoding="utf-8"))
    assert mod.snapshot_verify(doc)
    names = {c["name"] for c in doc["combos"]}
    assert names == {"a", "b"}
    assert "ret" not in names

def test_cli_guard_exit4_unexported_change_prints_guard_prefix(tmp_path):
    mod = load_module()
    snap = mod.snapshot_build({"a": {"strategy": "s", "models": ["x"], "dropped": 0}})
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fy\x1f0"], combos_models=[("a", "s", ["x"])], snapshot=snap)
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "guard", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 4
    assert all(line.startswith("guard: ") for line in res.stdout.strip().splitlines())
    assert "unexported live change: a" in res.stdout

def test_cli_guard_exit0_when_live_equals_snapshot(tmp_path):
    mod = load_module()
    snap = mod.snapshot_build({"a": {"strategy": "s", "models": ["x"], "dropped": 0}})
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx\x1f0"], combos_models=[("a", "s", ["x"])], snapshot=snap)
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "guard", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert res.stdout.strip() == ""

def test_cli_guard_exit0_when_live_equals_file(tmp_path):
    mod = load_module()
    snap = mod.snapshot_build({"a": {"strategy": "s", "models": ["y"], "dropped": 0}})
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx\x1f0"], combos_models=[("a", "s", ["x"])], snapshot=snap)
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "guard", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0

def test_cli_guard_unreadable_option_exit4(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=[], live_rc=0, combos_models=[])
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "guard", "--unreadable", "boom", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 4
    assert "guard: live store unreadable: boom" in res.stdout

def test_cli_default_live_command_is_apply_sh_live_lines(tmp_path):
    mod_path = REPO / "tools" / "combo_drift.py"
    text = mod_path.read_text(encoding="utf-8")
    assert "--live-lines" in text

def test_cli_outputs_never_contain_sk(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx,y\x1f0"], combos_models=[("a", "s", ["x", "y"])])
    for cmd in ["check", "export", "guard"]:
        res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), cmd, *base_args], env=env, capture_output=True, text=True)
        assert "sk-" not in res.stdout and "sk-" not in res.stderr

import pytest

def test_clean_violations_flags_free_bzl_groq_openrouter_vertex():
    file_doc = {
        "combos": [
            {
                "name": "test-clean",
                "models": [
                    "bzl/model-a",
                    "bazaarlink/model-b",
                    "groq/model-c",
                    "openrouter/model-d",
                    "vertex/model-e",
                    "other/model-f:free",
                    "allowed/model-g",
                ],
            },
            {
                "name": "other-combo",
                "models": ["bzl/bad-model"],
            },
        ]
    }
    violations = load_module().clean_violations(file_doc)
    assert violations == sorted([
        "forbidden leg in clean combo: test-clean: bzl/model-a",
        "forbidden leg in clean combo: test-clean: bazaarlink/model-b",
        "forbidden leg in clean combo: test-clean: groq/model-c",
        "forbidden leg in clean combo: test-clean: openrouter/model-d",
        "forbidden leg in clean combo: test-clean: vertex/model-e",
        "forbidden leg in clean combo: test-clean: other/model-f:free",
    ])

def test_guard_refuses_forbidden_leg_in_clean_combo(tmp_path):
    mod = load_module()
    live = {"test-clean": {"models": ["bzl/bad"], "strategy": "round-robin", "dropped": 0}}
    file_doc = {"combos": [{"name": "test-clean", "models": ["bzl/bad"]}], "retired": []}
    snapshot_doc = mod.snapshot_build(live)
    ok, reasons = mod.guard(live, snapshot_doc, file_doc, set())
    assert ok is False
    assert any("forbidden leg in clean combo" in r for r in reasons)
    assert len(reasons) == 1

def test_cli_guard_exit4_forbidden_clean_leg(tmp_path):
    snap = load_module().snapshot_build({"x-clean": {"strategy": "priority", "models": ["bzl/bad"], "dropped": 0}})
    env, base_args, _ = make_cli(tmp_path, live_lines=["x-clean\x1fpriority\x1fbzl/bad\x1f0"],
                                 combos_models=[("x-clean", "priority", ["bzl/bad"])], snapshot=snap)
    result = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "guard", *base_args],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 4
    assert "forbidden leg in clean combo" in result.stdout

def test_real_combos_json_clean_combos_have_no_forbidden_leg():
    combos_path = REPO / "configuration" / "omniroute" / "combos.json"
    with open(combos_path) as f:
        file_doc = json.load(f)
    assert load_module().clean_violations(file_doc) == []

def test_cli_export_without_out_writes_the_snapshot_option_not_the_repo_file(tmp_path):
    env, base_args, _ = make_cli(tmp_path, live_lines=["a\x1fs\x1fx\x1f0"], combos_models=[("a", "s", ["x"])])
    repo_snap = REPO / "configuration" / "omniroute" / "combos.live.json"
    before = repo_snap.read_bytes() if repo_snap.exists() else None
    res = subprocess.run([sys.executable, str(REPO / "tools" / "combo_drift.py"), "export", *base_args], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))["count"] == 1
    assert (repo_snap.read_bytes() if repo_snap.exists() else None) == before

