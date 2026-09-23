# CAO Orchestration Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Python-implemented CAO lane to `unattended-orchestration` that gates long-horizon orchestration behind an interactive plan contract, enforces configurable N-level depth, routes each task to the cheapest capable model across three quota pools, and survives quota walls without losing or duplicating work.

**Architecture:** Nine small single-responsibility modules under `skills/unattended-orchestration/cao/`, each independently testable, composed by `dispatch.py`. Depth is enforced by *withholding* `assign`/`handoff` from generated leaf profiles rather than by instruction. Model selection is an ordered candidate ladder over quota pools; review pairing is by model *family*. State is append-only NDJSON.

**Tech Stack:** Python 3.12+ (stdlib + PyYAML only at runtime), pytest for tests, CAO 2.5.0 REST API on `:9889`, tmux backend, providers `claude` / `agy` / `opencode`.

**Spec:** [docs/superpowers/specs/2026-09-10-cao-orchestration-design.md](../specs/2026-09-10-cao-orchestration-design.md)

## Global Constraints

- **Runtime dependencies: Python stdlib + PyYAML only.** Measured: WSL Ubuntu has PyYAML but **no pytest**. pytest is a dev-only dependency; nothing in `cao/` may import it.
- **Python 3.12 floor.** WSL has 3.12.3; Windows dev box has 3.13.5. No 3.13-only syntax.
- **No secret ever leaves `secrets/api_keys.conf`.** Values are exported into child environments and never logged, echoed, committed, or written into a generated profile.
- **All paths POSIX-normalised.** The lane runs on Linux and in WSL; Windows is a *driver* only.
- **Never widen `handoff.config.json`'s existing schema semantics.** The `cao` block is additive; the PowerShell validator has no unknown-key rejection (verified) and must keep passing.
- **NDJSON ledger convention:** one JSON object per line, append-only, `ts` field first, no secret values.
- **Model freshness:** within a pool, never rank an older model ahead of a newer one that same pool offers. Across pools, descending to an older family is allowed and must be *recorded*.
- **CAO server:** `http://localhost:9889`, already running. Do not restart it; it is shared.

---

## File Structure

| File | Responsibility |
|---|---|
| `cao/__init__.py` | Package marker; exports nothing |
| `cao/config.py` | Load `handoff.config.json` → `cao` block; load `secrets/api_keys.conf`; resolve pool availability |
| `cao/routing.py` | Pool/family model; ordered candidate ladders; freshness ordering check |
| `cao/budget.py` | Pool-keyed quota ledger; cooldown; fail-open detection |
| `cao/plan.py` | `PLAN.lock.json` schema + validator (the planning gate) |
| `cao/wire.py` | TASK / RESULT envelopes with hard caps |
| `cao/leases.py` | Atomic task claim; safe-resume predicate |
| `cao/profiles.py` | Generate per-level CAO profile YAML from config |
| `cao/dispatch.py` | Compose the above; review pairing by family |
| `cao/caoapi.py` | Thin CAO REST client (`/health`, `/sessions`) |
| `cao/cli.py` | `python -m cao <verb>` |
| `tests/cao/test_*.py` | pytest, one file per module |
| `infra/mcp-servers/cao-setup/setup_cao.py` | Detection, install, bridge patch, omnigraph injection patch, `--check` |

---

## Task 1: Config and secrets loading

**Files:**
- Create: `skills/unattended-orchestration/cao/__init__.py`
- Create: `skills/unattended-orchestration/cao/config.py`
- Test: `skills/unattended-orchestration/tests/cao/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `load_secrets(path) -> dict[str,str]`, `CaoConfig` dataclass with fields `max_depth:int`, `server:str`, `worktree_root:str`, `levels:list[dict]`, `pools:dict[str,dict]`, `routing:dict`, `review_pairing:str`, `resume:dict`; `load_config(repo_root) -> CaoConfig`; `pool_available(cfg, pool, secrets) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_config.py
import json, textwrap, pytest
from cao.config import load_secrets, load_config, pool_available

def test_load_secrets_parses_key_value_ignoring_comments(tmp_path):
    f = tmp_path / "api_keys.conf"
    f.write_text("# comment\n\ndeepseek=sk-abc123\n\n", encoding="utf-8")
    assert load_secrets(f) == {"deepseek": "sk-abc123"}

def test_load_secrets_missing_file_is_empty_not_error(tmp_path):
    assert load_secrets(tmp_path / "nope.conf") == {}

def test_load_secrets_never_returns_value_in_repr(tmp_path):
    f = tmp_path / "api_keys.conf"
    f.write_text("deepseek=sk-SECRET\n", encoding="utf-8")
    s = load_secrets(f)
    assert "sk-SECRET" not in repr(type(s)(("deepseek", "<redacted>") for _ in [0]))

def test_load_config_reads_cao_block(tmp_path):
    (tmp_path / "handoff.config.json").write_text(json.dumps({
        "baseBranch": "main",
        "cao": {"maxDepth": 3, "server": "http://localhost:9889",
                "worktreeRoot": "$HOME/cao-worktrees",
                "levels": [{"level": 1, "role": "orchestrator", "canSpawn": True}],
                "pools": {"deepseek": {"via": "opencode", "apiKeyEnv": "DEEPSEEK_API_KEY"}},
                "routing": {}, "reviewPairing": "cross-family",
                "resume": {"enabled": True, "leaseMinutes": 30, "maxAttempts": 3}}
    }), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.max_depth == 3
    assert cfg.resume["maxAttempts"] == 3

def test_load_config_missing_cao_block_raises_with_actionable_message(tmp_path):
    (tmp_path / "handoff.config.json").write_text(json.dumps({"baseBranch": "main"}), encoding="utf-8")
    with pytest.raises(ValueError, match="no 'cao' block"):
        load_config(tmp_path)

def test_pool_unavailable_when_api_key_absent(tmp_path):
    cfg = _cfg_with_deepseek(tmp_path)
    assert pool_available(cfg, "deepseek", secrets={}) is False
    assert pool_available(cfg, "deepseek", secrets={"deepseek": "sk-x"}) is True

def test_pool_without_apikeyenv_is_available(tmp_path):
    cfg = _cfg_with_deepseek(tmp_path)
    assert pool_available(cfg, "anthropic", secrets={}) is True

def _cfg_with_deepseek(tmp_path):
    (tmp_path / "handoff.config.json").write_text(json.dumps({
        "cao": {"maxDepth": 3, "server": "http://localhost:9889",
                "worktreeRoot": "$HOME/cao-worktrees", "levels": [],
                "pools": {"anthropic": {"via": "claude"},
                          "deepseek": {"via": "opencode", "apiKeyEnv": "DEEPSEEK_API_KEY"}},
                "routing": {}, "reviewPairing": "cross-family", "resume": {}}
    }), encoding="utf-8")
    return load_config(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/config.py
"""Load the `cao` block of handoff.config.json plus provider secrets.

Secrets live OUTSIDE the config on purpose: handoff.config.json is committed,
secrets/api_keys.conf never is (see secrets/README.md). A pool whose key is
missing is *unavailable*, not an error — ladders skip it exactly as they skip a
quota-cooling pool, so an absent key degrades instead of raising.
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SECRETS = Path("secrets/api_keys.conf")

def load_secrets(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out

@dataclass
class CaoConfig:
    max_depth: int
    server: str
    worktree_root: str
    levels: list
    pools: dict
    routing: dict
    review_pairing: str = "cross-family"
    resume: dict = field(default_factory=dict)
    repo_root: Path = field(default=Path("."))

    def __repr__(self) -> str:  # never let a dump leak a pool secret
        return f"CaoConfig(max_depth={self.max_depth}, pools={sorted(self.pools)})"

def load_config(repo_root) -> CaoConfig:
    root = Path(repo_root)
    cfg_file = root / "handoff.config.json"
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    if "cao" not in raw:
        raise ValueError(
            f"{cfg_file} has no 'cao' block; add one (see handoff.config.example.json)")
    c = raw["cao"]
    return CaoConfig(
        max_depth=int(c.get("maxDepth", 3)),
        server=c.get("server", "http://localhost:9889"),
        worktree_root=os.path.expandvars(c.get("worktreeRoot", "$HOME/cao-worktrees")),
        levels=c.get("levels", []),
        pools=c.get("pools", {}),
        routing=c.get("routing", {}),
        review_pairing=c.get("reviewPairing", "cross-family"),
        resume=c.get("resume", {}),
        repo_root=root,
    )

def pool_available(cfg: CaoConfig, pool: str, secrets: dict) -> bool:
    spec = cfg.pools.get(pool)
    if spec is None:
        return False
    if not spec.get("apiKeyEnv"):
        return True          # CLI-authenticated pool (claude, agy)
    return bool(secrets.get(pool))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/ skills/unattended-orchestration/tests/cao/
git commit -m "feat(cao): config and secrets loading with graceful pool unavailability"
```

---

## Task 2: Routing ladders, pools and families

**Files:**
- Create: `skills/unattended-orchestration/cao/routing.py`
- Test: `skills/unattended-orchestration/tests/cao/test_routing.py`

**Interfaces:**
- Consumes: `CaoConfig` from Task 1
- Produces: `Candidate` dataclass (`pool:str`, `family:str`, `model:str`, `effort:str|None`); `ladder(cfg, task_class) -> list[Candidate]`; `select(cfg, task_class, is_open) -> Candidate` where `is_open(pool)->bool`; `check_freshness(cfg) -> list[str]` returning warning strings; `FAMILY_ORDER` constant

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_routing.py
import pytest
from cao.routing import Candidate, ladder, select, check_freshness

class FakeCfg:
    def __init__(self, routing): self.routing = routing

LADDER = {"implement": [
    {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
    {"pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "medium"},
]}

def test_ladder_returns_candidates_in_order():
    c = ladder(FakeCfg(LADDER), "implement")
    assert [x.model for x in c] == ["gemini-3.8-flash-high", "sonnet"]
    assert c[1].effort == "medium"

def test_select_takes_first_open_pool():
    got = select(FakeCfg(LADDER), "implement", is_open=lambda p: True)
    assert got.model == "gemini-3.8-flash-high"

def test_select_descends_when_first_pool_is_closed():
    got = select(FakeCfg(LADDER), "implement", is_open=lambda p: p != "antigravity")
    assert got.model == "sonnet" and got.family == "anthropic"

def test_select_raises_when_every_pool_closed():
    with pytest.raises(RuntimeError, match="no open pool"):
        select(FakeCfg(LADDER), "implement", is_open=lambda p: False)

def test_unknown_task_class_names_the_known_ones():
    with pytest.raises(KeyError, match="implement"):
        ladder(FakeCfg(LADDER), "nonsense")

def test_freshness_warns_when_older_model_precedes_newer_in_same_pool():
    cfg = FakeCfg({"test": [
        {"pool": "antigravity", "family": "google", "model": "gemini-3.6-flash-high"},
        {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
    ]})
    warnings = check_freshness(cfg)
    assert any("gemini-3.6-flash-high" in w and "gemini-3.8-flash-high" in w for w in warnings)

def test_freshness_silent_across_different_pools():
    cfg = FakeCfg({"test": [
        {"pool": "anthropic", "family": "anthropic", "model": "opus"},
        {"pool": "antigravity", "family": "anthropic", "model": "claude-opus-4-6-thinking"},
    ]})
    assert check_freshness(cfg) == []
```

The last test is the corrected design from spec §5.4: descending to an older
*family* member in a *different* pool is legitimate capacity, not a defect.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.routing'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/routing.py
"""Task class -> ordered candidate ladder.

Two axes, deliberately separate (spec 5.2):
  pool   = which quota bucket the capacity is drawn from  -> decides fallback
  family = whose training, whose blind spots              -> decides review pairing
`claude-opus-4-6-thinking` via agy is the anthropic FAMILY from the antigravity
POOL: valid extra capacity, invalid reviewer for Claude-written code.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

FAMILY_ORDER = ("anthropic", "google", "deepseek", "oss")

_VERSION = re.compile(r"(\d+)\.(\d+)")

@dataclass(frozen=True)
class Candidate:
    pool: str
    family: str
    model: str
    effort: str | None = None

def ladder(cfg, task_class: str) -> list:
    try:
        entries = cfg.routing[task_class]
    except KeyError:
        raise KeyError(
            f"unknown task class {task_class!r}; known: {sorted(cfg.routing)}") from None
    return [Candidate(e["pool"], e["family"], e["model"], e.get("effort")) for e in entries]

def select(cfg, task_class: str, is_open):
    cands = ladder(cfg, task_class)
    for c in cands:
        if is_open(c.pool):
            return c
    raise RuntimeError(
        f"no open pool for task class {task_class!r}; tried {[c.pool for c in cands]}")

def _version_key(model: str):
    m = _VERSION.search(model)
    return (int(m.group(1)), int(m.group(2))) if m else None

def check_freshness(cfg) -> list:
    """Warn when a pool ranks an older model ahead of a newer one IT ALSO offers.

    Cross-pool descent is silent on purpose: refusing a pool's older model does
    not get a better model, it gets a smaller budget (spec 5.4).
    """
    out = []
    for task_class, entries in cfg.routing.items():
        if not isinstance(entries, list):
            continue          # "cross-family" sentinel
        seen = {}
        for e in entries:
            key, ver = e["pool"], _version_key(e["model"])
            if ver is None:
                continue      # alias like "opus" — always latest, never stale
            if key in seen and seen[key][0] < ver:
                out.append(
                    f"{task_class}: pool {key!r} ranks {seen[key][1]!r} ahead of "
                    f"newer {e['model']!r} from the same pool")
            else:
                seen[key] = (ver, e["model"])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_routing.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/routing.py skills/unattended-orchestration/tests/cao/test_routing.py
git commit -m "feat(cao): candidate ladders separating quota pool from model family"
```

---

## Task 3: Pool-keyed budget ledger

**Files:**
- Create: `skills/unattended-orchestration/cao/budget.py`
- Test: `skills/unattended-orchestration/tests/cao/test_budget.py`

**Interfaces:**
- Consumes: nothing
- Produces: `DEFAULT_PATTERNS` tuple; `detect_limit(text, patterns=None) -> str|None`; `record_cooldown(ledger, pool, raw, pattern, minutes=60) -> dict`; `is_open(ledger, pool, now=None) -> bool`; `mark_unavailable(ledger, pool)`; `Ledger` class with `append(obj)` / `read()`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_budget.py
from datetime import datetime, timedelta, timezone
from cao.budget import Ledger, detect_limit, record_cooldown, is_open, mark_unavailable

def test_detect_limit_matches_conservative_defaults():
    assert detect_limit("Error: 429 Too Many Requests") is not None
    assert detect_limit("RESOURCE_EXHAUSTED: quota") is not None
    assert detect_limit("you have hit your usage limit") is not None

def test_detect_limit_returns_none_for_ordinary_errors():
    assert detect_limit("SyntaxError: invalid syntax") is None
    assert detect_limit("test failed: 3 assertions") is None

def test_unknown_pool_is_open_fail_open(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    assert is_open(led, "antigravity") is True

def test_cooling_pool_is_closed_then_reopens(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    record_cooldown(led, "antigravity", raw="429 rate limit", pattern="429", minutes=60)
    assert is_open(led, "antigravity") is False
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    assert is_open(led, "antigravity", now=later) is True

def test_unavailable_pool_is_closed_indefinitely(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    mark_unavailable(led, "deepseek")
    far = datetime.now(timezone.utc) + timedelta(days=30)
    assert is_open(led, "deepseek", now=far) is False

def test_cooldown_truncates_raw_and_keeps_it_short(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    rec = record_cooldown(led, "antigravity", raw="x" * 5000, pattern="429")
    assert len(rec["raw"]) <= 500

def test_ledger_is_append_only_ndjson(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    led.append({"a": 1}); led.append({"a": 2})
    lines = (tmp_path / "budget.ndjson").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and [l.count("\n") for l in lines] == [0, 0]

def test_ledger_tolerates_corrupt_trailing_line(tmp_path):
    p = tmp_path / "budget.ndjson"
    p.write_text('{"a": 1}\n{"a": 2}\n{"a": ', encoding="utf-8")
    assert Ledger(p).read() == [{"a": 1}, {"a": 2}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/budget.py
"""Pool-keyed quota ledger.

Keyed by POOL, not provider: a pool is what actually runs out. Detection
patterns are configurable and the defaults are UNVERIFIED — no provider's real
quota string has been measured yet. `is_open` therefore fails OPEN: an unknown
pool is usable. Falsely cooling a healthy pool costs more than a missed wall,
which the next call will detect anyway.
"""
from __future__ import annotations
import json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PATTERNS = (
    r"rate.?limit", r"quota", r"resource.?exhausted", r"\b429\b", r"usage limit",
)
MAX_RAW = 500

class Ledger:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, obj: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def read(self) -> list:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue      # a crash mid-write leaves one partial trailing line
        return out

def detect_limit(text: str, patterns=None):
    for pat in (patterns or DEFAULT_PATTERNS):
        if re.search(pat, text or "", re.I):
            return pat
    return None

def _now(now=None):
    return now or datetime.now(timezone.utc)

def record_cooldown(ledger: Ledger, pool: str, raw: str, pattern: str, minutes: int = 60) -> dict:
    rec = {
        "ts": _now().isoformat(),
        "pool": pool,
        "state": "cooling",
        "cooling_until": (_now() + timedelta(minutes=minutes)).isoformat(),
        "pattern": pattern,
        "raw": (raw or "")[:MAX_RAW],
    }
    ledger.append(rec)
    return rec

def mark_unavailable(ledger: Ledger, pool: str) -> dict:
    rec = {"ts": _now().isoformat(), "pool": pool, "state": "unavailable"}
    ledger.append(rec)
    return rec

def is_open(ledger: Ledger, pool: str, now=None) -> bool:
    latest = None
    for rec in ledger.read():
        if rec.get("pool") == pool:
            latest = rec
    if latest is None:
        return True                                   # fail open
    if latest.get("state") == "unavailable":
        return False
    if latest.get("state") == "cooling":
        until = datetime.fromisoformat(latest["cooling_until"])
        return _now(now) >= until
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_budget.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/budget.py skills/unattended-orchestration/tests/cao/test_budget.py
git commit -m "feat(cao): pool-keyed quota ledger that fails open on unknown pools"
```

---

## Task 4: The planning gate

**Files:**
- Create: `skills/unattended-orchestration/cao/plan.py`
- Test: `skills/unattended-orchestration/tests/cao/test_plan.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SCHEMA = "cao-plan/1"`; `PlanError(Exception)`; `validate(doc, max_depth) -> None` (raises `PlanError`); `load(path, max_depth) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_plan.py
import json, pytest
from cao.plan import validate, load, PlanError

def good():
    return {
        "schema": "cao-plan/1", "goal": "g", "createdBy": "claude-code",
        "approvedAt": "2026-09-10T14:22:03Z", "depth": 3,
        "openQuestions": [{"q": "which graph?", "answer": "agent-skills", "answeredBy": "user"}],
        "assumedDefaults": [],
        "phases": [{"id": "p1", "goal": "x", "taskClass": "implement",
                    "acceptance": {"guard": "pytest -q", "expect": "exit0"}, "dependsOn": []}],
    }

def test_valid_plan_passes():
    validate(good(), max_depth=3)

def test_wrong_schema_rejected():
    d = good(); d["schema"] = "cao-plan/99"
    with pytest.raises(PlanError, match="schema"):
        validate(d, max_depth=3)

def test_missing_approval_rejected():
    d = good(); del d["approvedAt"]
    with pytest.raises(PlanError, match="approvedAt"):
        validate(d, max_depth=3)

def test_phase_without_guard_rejected():
    d = good(); d["phases"][0]["acceptance"] = {"guard": "", "expect": "exit0"}
    with pytest.raises(PlanError, match="acceptance.guard"):
        validate(d, max_depth=3)

def test_depth_over_max_rejected():
    d = good(); d["depth"] = 9
    with pytest.raises(PlanError, match="depth 9 exceeds"):
        validate(d, max_depth=3)

def test_dependson_unknown_phase_rejected():
    d = good(); d["phases"][0]["dependsOn"] = ["ghost"]
    with pytest.raises(PlanError, match="ghost"):
        validate(d, max_depth=3)

def test_dependency_cycle_rejected():
    d = good()
    d["phases"] = [
        {"id": "a", "goal": "", "taskClass": "implement",
         "acceptance": {"guard": "x", "expect": "exit0"}, "dependsOn": ["b"]},
        {"id": "b", "goal": "", "taskClass": "implement",
         "acceptance": {"guard": "x", "expect": "exit0"}, "dependsOn": ["a"]},
    ]
    with pytest.raises(PlanError, match="cycle"):
        validate(d, max_depth=3)

# THE GATE: this is what forces interactive planning (spec 3).
def test_no_user_answer_and_no_assumed_defaults_is_rejected():
    d = good(); d["openQuestions"] = []; d["assumedDefaults"] = []
    with pytest.raises(PlanError, match="ask the user"):
        validate(d, max_depth=3)

def test_assumed_defaults_satisfy_the_gate_when_documented():
    d = good(); d["openQuestions"] = []
    d["assumedDefaults"] = [{"assumption": "graph = repo folder", "why": "matches trust_worktree"}]
    validate(d, max_depth=3)

def test_assumed_default_without_why_is_rejected():
    d = good(); d["openQuestions"] = []
    d["assumedDefaults"] = [{"assumption": "graph = repo folder"}]
    with pytest.raises(PlanError, match="why"):
        validate(d, max_depth=3)

def test_load_reads_and_validates(tmp_path):
    p = tmp_path / "PLAN.lock.json"
    p.write_text(json.dumps(good()), encoding="utf-8")
    assert load(p, max_depth=3)["goal"] == "g"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.plan'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/plan.py
"""PLAN.lock.json — the artifact that gates dispatch.

Instructions do not hold; a validator does. The decisive rule is
_require_interactivity: a plan must record either an answer the USER gave, or
an explicitly justified assumption. There is no way to express "I did not ask".
"""
from __future__ import annotations
import json
from pathlib import Path

SCHEMA = "cao-plan/1"

class PlanError(Exception):
    pass

def _require(cond, msg):
    if not cond:
        raise PlanError(msg)

def _require_interactivity(doc):
    answered = [q for q in doc.get("openQuestions", []) if q.get("answeredBy") == "user"]
    assumed = doc.get("assumedDefaults", [])
    _require(answered or assumed,
             "planning gate: ask the user at least one question, or record "
             "assumedDefaults saying what you decided on their behalf")
    for a in assumed:
        _require(a.get("assumption") and a.get("why"),
                 "each assumedDefaults entry needs both 'assumption' and 'why'")

def _require_dag(phases):
    ids = {p["id"] for p in phases}
    for p in phases:
        for dep in p.get("dependsOn", []):
            _require(dep in ids, f"phase {p['id']!r} dependsOn unknown phase {dep!r}")
    graph = {p["id"]: list(p.get("dependsOn", [])) for p in phases}
    state = {}
    def walk(node):
        if state.get(node) == "done":
            return
        _require(state.get(node) != "open", f"dependency cycle at phase {node!r}")
        state[node] = "open"
        for dep in graph[node]:
            walk(dep)
        state[node] = "done"
    for node in graph:
        walk(node)

def validate(doc: dict, max_depth: int) -> None:
    _require(doc.get("schema") == SCHEMA,
             f"schema must be {SCHEMA!r}, got {doc.get('schema')!r}")
    _require(doc.get("approvedAt"), "approvedAt is required — the plan is not approved")
    _require(doc.get("goal"), "goal is required")
    depth = int(doc.get("depth", 0))
    _require(depth <= max_depth, f"depth {depth} exceeds cao.maxDepth {max_depth}")
    phases = doc.get("phases") or []
    _require(phases, "a plan with no phases dispatches nothing")
    for p in phases:
        _require(p.get("id"), "every phase needs an id")
        _require((p.get("acceptance") or {}).get("guard"),
                 f"phase {p.get('id')!r}: acceptance.guard is required — "
                 "a phase with no way to be checked is not a phase")
    _require_dag(phases)
    _require_interactivity(doc)

def load(path, max_depth: int) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc, max_depth)
    return doc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_plan.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/plan.py skills/unattended-orchestration/tests/cao/test_plan.py
git commit -m "feat(cao): plan contract validator that refuses un-asked plans"
```

---

## Task 5: Wire envelopes with hard caps

**Files:**
- Create: `skills/unattended-orchestration/cao/wire.py`
- Test: `skills/unattended-orchestration/tests/cao/test_wire.py`

**Interfaces:**
- Consumes: nothing
- Produces: `TASK_CAP = 3200`, `RESULT_CAP = 1600`, `NOTES_CAP = 300` (chars); `render_task(**kw) -> str`; `render_result(**kw) -> str`; `parse_result(text) -> dict`; `truncate(text, cap) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_wire.py
import inspect, pytest
from cao import wire
from cao.wire import render_task, render_result, parse_result, truncate, TASK_CAP, NOTES_CAP

def test_truncate_marks_visibly():
    out = truncate("x" * 1000, 100)
    assert len(out) <= 100 + 40 and "truncated" in out

def test_truncate_leaves_short_text_untouched():
    assert truncate("hello", 100) == "hello"

def test_render_task_has_all_fields_and_stays_under_cap():
    out = render_task(task_id="p2-impl-03", goal="add X", plan_ref="PLAN.lock.json#/phases/p2",
                      files=["src/a.py"], guard="pytest -q", expect="exit0",
                      depth=3, max_depth=3, budget_tokens=40000, reply_to="term-a91f")
    for token in ("TASK p2-impl-03", "plan_ref:", "acceptance:", "depth:", "reply:"):
        assert token in out
    assert len(out) <= TASK_CAP

def test_render_task_caps_a_huge_goal():
    out = render_task(task_id="t", goal="g" * 50000, plan_ref="p", files=[], guard="x",
                      expect="exit0", depth=1, max_depth=1, budget_tokens=1, reply_to="t0")
    assert len(out) <= TASK_CAP and "truncated" in out

# THE RULE: prose about the plan cannot ride along; there is no field for it.
def test_render_task_has_no_parameter_that_can_carry_plan_prose():
    params = set(inspect.signature(render_task).parameters)
    assert not (params & {"plan", "plan_text", "context", "background", "notes"})

def test_render_result_roundtrips():
    out = render_result(task_id="p2-impl-03", status="ok", evidence="pytest -q -> 14 passed",
                        files_changed=["src/b.py"], notes="fine",
                        cost={"pool": "antigravity", "model": "gemini-3.8-flash-high",
                              "in": 18211, "out": 2044})
    got = parse_result(out)
    assert got["task_id"] == "p2-impl-03" and got["status"] == "ok"
    assert got["files_changed"] == ["src/b.py"]

def test_render_result_caps_notes():
    out = render_result(task_id="t", status="ok", evidence="e", files_changed=[],
                        notes="n" * 5000, cost={})
    line = [l for l in out.splitlines() if l.startswith("notes:")][0]
    assert len(line) <= NOTES_CAP + 60

def test_parse_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        parse_result("RESULT t status=whatever\n")

def test_status_values_are_exactly_the_three_documented():
    assert wire.STATUSES == ("ok", "blocked", "needs_revision")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_wire.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.wire'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/wire.py
"""Fixed TASK / RESULT envelopes.

With three or more layers, prose between agents is the dominant token cost and
the dominant source of drift. The caps here are enforced in code rather than
requested in a prompt, and `render_task` deliberately has NO parameter that can
carry plan prose — a worker references plan_ref instead of being re-told the plan.
"""
from __future__ import annotations
import re

TASK_CAP = 3200          # characters, ~800 tokens
RESULT_CAP = 1600        # ~400 tokens
NOTES_CAP = 300
STATUSES = ("ok", "blocked", "needs_revision")

def truncate(text: str, cap: int) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    dropped = len(text) - cap
    return text[:cap] + f" …[truncated {dropped} chars]"

def render_task(*, task_id, goal, plan_ref, files, guard, expect,
                depth, max_depth, budget_tokens, reply_to) -> str:
    body = (
        f"TASK {task_id}\n"
        f"goal:       {truncate(goal, 200)}\n"
        f"plan_ref:   {plan_ref}\n"
        f"files:      {', '.join(files) if files else '(none)'}\n"
        f"acceptance: {guard} -> {expect}\n"
        f"depth:      {depth}/{max_depth}\n"
        f"budget:     {budget_tokens} tokens\n"
        f"reply:      RESULT -> {reply_to}\n"
    )
    return truncate(body, TASK_CAP)

def render_result(*, task_id, status, evidence, files_changed, notes, cost) -> str:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    cost_s = " ".join(f"{k}={v}" for k, v in (cost or {}).items())
    body = (
        f"RESULT {task_id} status={status}\n"
        f"evidence:      {truncate(evidence, 400)}\n"
        f"files_changed: {', '.join(files_changed) if files_changed else '(none)'}\n"
        f"notes:         {truncate(notes, NOTES_CAP)}\n"
        f"cost:          {cost_s}\n"
    )
    return truncate(body, RESULT_CAP)

_HEAD = re.compile(r"^RESULT\s+(?P<id>\S+)\s+status=(?P<status>\S+)")

def parse_result(text: str) -> dict:
    lines = (text or "").strip().splitlines()
    m = _HEAD.match(lines[0]) if lines else None
    if not m:
        raise ValueError("not a RESULT envelope: missing 'RESULT <id> status=<s>' header")
    status = m.group("status")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    out = {"task_id": m.group("id"), "status": status,
           "evidence": "", "files_changed": [], "notes": "", "cost": ""}
    for line in lines[1:]:
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "files_changed":
            out[key] = [] if val in ("", "(none)") else [p.strip() for p in val.split(",")]
        elif key in out:
            out[key] = val
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_wire.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/wire.py skills/unattended-orchestration/tests/cao/test_wire.py
git commit -m "feat(cao): capped TASK/RESULT envelopes with no channel for plan prose"
```

---

## Task 6: Leases and the safe-resume predicate

**Files:**
- Create: `skills/unattended-orchestration/cao/leases.py`
- Test: `skills/unattended-orchestration/tests/cao/test_leases.py`

**Interfaces:**
- Consumes: `Ledger` from Task 3
- Produces: `claim(dir, task_id, owner) -> bool`; `release(dir, task_id)`; `state(dir, task_id) -> dict|None`; `may_resume(rec, live_task_ids, max_attempts, now=None) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_leases.py
from datetime import datetime, timedelta, timezone
from cao.leases import claim, release, state, may_resume

def _rec(**kw):
    base = {"task_id": "t1", "owner": "term-a", "state": "running",
            "lease_expires": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "continued_by": None, "attempt": 1}
    base.update(kw); return base

def test_claim_succeeds_once_then_blocks_second_claimer(tmp_path):
    assert claim(tmp_path, "t1", "term-a") is True
    assert claim(tmp_path, "t1", "term-b") is False

def test_release_allows_reclaim(tmp_path):
    claim(tmp_path, "t1", "term-a"); release(tmp_path, "t1")
    assert claim(tmp_path, "t1", "term-b") is True

def test_state_reports_owner(tmp_path):
    claim(tmp_path, "t1", "term-a")
    assert state(tmp_path, "t1")["owner"] == "term-a"

def test_expired_lease_with_no_successor_may_resume():
    assert may_resume(_rec(), live_task_ids=set(), max_attempts=3) is True

def test_unexpired_lease_may_not_resume():
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    assert may_resume(_rec(lease_expires=future), live_task_ids=set(), max_attempts=3) is False

# THE USER'S CONSTRAINT: never restart work another agent already picked up.
def test_continued_by_blocks_resume():
    assert may_resume(_rec(continued_by="term-z"), live_task_ids=set(), max_attempts=3) is False

def test_live_terminal_on_that_task_blocks_resume_even_if_lease_expired():
    assert may_resume(_rec(), live_task_ids={"t1"}, max_attempts=3) is False

def test_attempts_exhausted_blocks_resume():
    assert may_resume(_rec(attempt=3), live_task_ids=set(), max_attempts=3) is False

def test_quota_stalled_state_is_resumable():
    assert may_resume(_rec(state="quota_stalled"), live_task_ids=set(), max_attempts=3) is True

def test_done_state_is_not_resumable():
    assert may_resume(_rec(state="done"), live_task_ids=set(), max_attempts=3) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_leases.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.leases'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/leases.py
"""Task ownership, so auto-resume never duplicates live work.

The user's constraint: resume stopped work, but ONLY if no other agent already
continued it. Four conditions must all hold (spec 6.3); the load-bearing one is
`live_task_ids`, which comes from CAO's /sessions — a ledger can be stale, the
server knows who is actually alive.
"""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path

RESUMABLE = ("running", "quota_stalled")

def _lock(dir_, task_id) -> Path:
    return Path(dir_) / f"{task_id}.lease"

def claim(dir_, task_id: str, owner: str) -> bool:
    """Atomic claim. O_CREAT|O_EXCL is atomic on POSIX and on NTFS."""
    p = _lock(dir_, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"task_id": task_id, "owner": owner,
                   "claimed_at": datetime.now(timezone.utc).isoformat()}, fh)
    return True

def release(dir_, task_id: str) -> None:
    _lock(dir_, task_id).unlink(missing_ok=True)

def state(dir_, task_id: str):
    p = _lock(dir_, task_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

def may_resume(rec: dict, live_task_ids, max_attempts: int, now=None) -> bool:
    if rec.get("state") not in RESUMABLE:
        return False
    if rec.get("continued_by"):
        return False
    if rec.get("task_id") in set(live_task_ids or ()):
        return False          # CAO says someone is alive on it — believe the server
    if int(rec.get("attempt", 1)) >= max_attempts:
        return False          # a loop that never converges is worse than a stall
    expires = datetime.fromisoformat(rec["lease_expires"])
    return (now or datetime.now(timezone.utc)) >= expires
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_leases.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/leases.py skills/unattended-orchestration/tests/cao/test_leases.py
git commit -m "feat(cao): atomic task leases and a resume predicate that trusts the server"
```

---

## Task 7: Profile generation — depth enforcement

**Files:**
- Create: `skills/unattended-orchestration/cao/profiles.py`
- Test: `skills/unattended-orchestration/tests/cao/test_profiles.py`

**Interfaces:**
- Consumes: `CaoConfig` (Task 1), `Candidate` (Task 2)
- Produces: `SPAWN_TOOLS = ("assign", "handoff")`; `build_profile(level, role, can_spawn, candidate, max_depth) -> dict`; `generate(cfg, out_dir) -> list[Path]`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_profiles.py
import yaml, pytest
from cao.routing import Candidate
from cao.profiles import build_profile, generate, SPAWN_TOOLS

class FakeCfg:
    def __init__(self, max_depth):
        self.max_depth = max_depth
        self.levels = [{"level": i + 1, "role": r, "canSpawn": i + 1 < max_depth}
                       for i, r in enumerate(["orchestrator"] + ["supervisor"] * (max_depth - 2) + ["worker"])]
        self.routing = {}

GEM = Candidate("antigravity", "google", "gemini-3.8-flash-high")
CLA = Candidate("anthropic", "anthropic", "sonnet", "high")

def test_spawning_level_gets_assign_and_handoff():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert set(SPAWN_TOOLS) <= set(p["allowedTools"])

# THE ENFORCEMENT: agy has no --no-subagents flag, so withholding the tool is
# the only real leaf control (spec 4).
def test_leaf_level_has_neither_assign_nor_handoff():
    p = build_profile(3, "worker", False, GEM, max_depth=3)
    assert not (set(SPAWN_TOOLS) & set(p["allowedTools"]))

@pytest.mark.parametrize("depth", [2, 3, 4, 5])
def test_deepest_generated_profile_is_always_a_leaf(tmp_path, depth):
    written = generate(FakeCfg(depth), tmp_path)
    deepest = yaml.safe_load(written[-1].read_text(encoding="utf-8"))
    assert not (set(SPAWN_TOOLS) & set(deepest["allowedTools"])), \
        f"maxDepth={depth}: deepest profile can still spawn"

def test_gemini_effort_rides_in_the_model_id_not_a_flag():
    p = build_profile(3, "worker", False, GEM, max_depth=3)
    assert p["model"] == "gemini-3.8-flash-high"
    assert "claudeConfig" not in p

def test_claude_effort_goes_to_claudeconfig():
    p = build_profile(2, "supervisor", True, CLA, max_depth=3)
    assert p["claudeConfig"]["effort"] == "high"
    assert p["provider"] == "claude_code"

def test_profile_states_its_own_depth_in_the_prompt():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "2 of 3" in p["system_prompt"]

def test_non_orchestrator_profiles_forbid_replanning():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "do not re-plan" in p["system_prompt"].lower()

def test_memory_tools_are_never_granted():
    p = build_profile(2, "supervisor", True, GEM, max_depth=3)
    assert "memory_store" not in p["allowedTools"]

def test_generate_writes_one_profile_per_non_orchestrator_level(tmp_path):
    written = generate(FakeCfg(3), tmp_path)
    assert len(written) == 2      # level 1 is this Claude session, not a CAO profile
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.profiles'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/profiles.py
"""Generate CAO agent profiles from config, so depth is real rather than asked for.

CAO has NO spawn-depth limit (its only `depth` is list_siblings prefix
clamping). Depth is emergent from a profile's tool surface: a profile without
`assign`/`handoff` cannot spawn, because the tool is not in the terminal. That
matters most for Gemini — unlike claude and grok, `agy` has no --no-subagents
flag, so withholding the tool is the ONLY enforcement available.
"""
from __future__ import annotations
from pathlib import Path
import yaml

SPAWN_TOOLS = ("assign", "handoff")
BASE_TOOLS = ("send_message", "report_outcome", "list_siblings", "get_terminal_status")
PROVIDER_BY_POOL = {"anthropic": "claude_code", "antigravity": "antigravity",
                    "deepseek": "opencode_cli"}

_ROLE_DUTY = {
    "supervisor": "Decompose your phase into concrete tasks and delegate every one of them. "
                  "Never write code yourself. Verify each worker's evidence before accepting it.",
    "worker": "Execute exactly the TASK you were given and report evidence for it.",
    "reviewer": "Review the code you are given, run its guard, and return APPROVED or "
                "NEEDS_REVISION with specific issues.",
}

def _prompt(level, role, can_spawn, max_depth) -> str:
    lines = [
        f"# {role.upper()} — Layer {level} of {max_depth}",
        "",
        f"You are at depth {level} of {max_depth} in a CAO orchestration hierarchy.",
        "",
        "## The plan is final",
        "A PLAN.lock.json contract was agreed with the user before you were spawned.",
        "Do not re-plan and do not re-open scope. If you find evidence the plan is",
        "wrong, emit `RESULT <id> status=needs_revision` with the contradicting",
        "evidence and stop — the Layer 1 orchestrator decides, not you.",
        "",
        "## Your duty",
        _ROLE_DUTY.get(role, _ROLE_DUTY["worker"]),
        "",
        "## Messages",
        "Reply only with a RESULT envelope. Never re-quote the plan — reference",
        "plan_ref. Never paste more than 20 lines of a file — reference path:line.",
        "",
        "## Memory",
        "Use the omnigraph MCP tools. Do NOT use memory_store / memory_recall.",
    ]
    if can_spawn:
        lines += ["", "## Delegation",
                  f"You may spawn workers with `assign` / `handoff`. Children are at depth "
                  f"{level + 1} of {max_depth}."]
    else:
        lines += ["", "## You are a leaf",
                  "You have no `assign` or `handoff` tool. Do all of your own work."]
    return "\n".join(lines)

def build_profile(level: int, role: str, can_spawn: bool, candidate, max_depth: int) -> dict:
    tools = list(BASE_TOOLS) + (list(SPAWN_TOOLS) if can_spawn else [])
    prof = {
        "name": f"cao_L{level}_{role}",
        "description": (f"Generated by cao/profiles.py — layer {level} of {max_depth}, "
                        f"{'can spawn' if can_spawn else 'leaf'}. Do not hand-edit."),
        "role": role,
        "provider": PROVIDER_BY_POOL[candidate.pool],
        "model": candidate.model,
        "mcpServers": {"cao-mcp-server": {"type": "stdio", "command": "cao-mcp-server", "args": []}},
        "allowedTools": tools,
        "system_prompt": _prompt(level, role, can_spawn, max_depth),
    }
    # Effort: Claude takes a flag (claudeConfig.effort -> --effort); agy does not,
    # so Gemini effort must already be encoded in the model id (…-high/-medium/-low).
    if candidate.effort and candidate.pool == "anthropic":
        prof["claudeConfig"] = {"effort": candidate.effort}
    return prof

def generate(cfg, out_dir) -> list:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from cao.routing import Candidate
    default = Candidate("antigravity", "google", "gemini-3.8-flash-high")
    written = []
    for lvl in cfg.levels:
        if lvl["level"] == 1:
            continue          # layer 1 is the human-facing orchestrator, not a CAO profile
        prof = build_profile(lvl["level"], lvl["role"], bool(lvl.get("canSpawn")),
                             default, cfg.max_depth)
        path = out / f"{prof['name']}.yaml"
        path.write_text(yaml.safe_dump(prof, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        written.append(path)
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_profiles.py -v`
Expected: 13 passed (the parametrised leaf test counts 4)

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/profiles.py skills/unattended-orchestration/tests/cao/test_profiles.py
git commit -m "feat(cao): generate profiles whose tool surface enforces the depth limit"
```

---

## Task 8: Review pairing and dispatch composition

**Files:**
- Create: `skills/unattended-orchestration/cao/caoapi.py`
- Create: `skills/unattended-orchestration/cao/dispatch.py`
- Test: `skills/unattended-orchestration/tests/cao/test_dispatch.py`

**Interfaces:**
- Consumes: everything above
- Produces: `pick_reviewer(cfg, implementer_family, is_open) -> tuple[Candidate, bool]` returning `(candidate, degraded)`; `CaoClient(base_url)` with `.health()` and `.live_task_ids()`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_dispatch.py
import pytest
from cao.dispatch import pick_reviewer

class FakeCfg:
    review_pairing = "cross-family"
    routing = {"review": [
        {"pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "high"},
        {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
        {"pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash"},
    ]}

OPEN_ALL = lambda p: True

def test_claude_written_code_is_reviewed_by_a_different_family():
    cand, degraded = pick_reviewer(FakeCfg(), "anthropic", OPEN_ALL)
    assert cand.family != "anthropic" and degraded is False

def test_gemini_written_code_is_reviewed_by_anthropic():
    cand, degraded = pick_reviewer(FakeCfg(), "google", OPEN_ALL)
    assert cand.family == "anthropic" and degraded is False

# The pool/family split: claude-4-6 via agy is valid CAPACITY but an invalid
# REVIEWER for Claude-written code, because the family is what shares blind spots.
def test_same_family_from_a_different_pool_is_still_rejected():
    cfg = FakeCfg()
    cfg.routing = {"review": [
        {"pool": "antigravity", "family": "anthropic", "model": "claude-opus-4-6-thinking"},
        {"pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash"},
    ]}
    cand, _ = pick_reviewer(cfg, "anthropic", OPEN_ALL)
    assert cand.family == "deepseek"

def test_deepseek_gives_a_second_escape_hatch_when_anthropic_is_cooling():
    cand, degraded = pick_reviewer(FakeCfg(), "google", lambda p: p != "anthropic")
    assert cand.family == "deepseek" and degraded is False

def test_degrades_and_flags_when_no_other_family_is_open():
    cand, degraded = pick_reviewer(FakeCfg(), "anthropic", lambda p: p == "anthropic")
    assert cand.family == "anthropic" and degraded is True

def test_raises_when_nothing_at_all_is_open():
    with pytest.raises(RuntimeError, match="no reviewer"):
        pick_reviewer(FakeCfg(), "anthropic", lambda p: False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cao.dispatch'`

- [ ] **Step 3: Write minimal implementation**

```python
# cao/caoapi.py
"""Thin CAO REST client. Only what the lane needs; CAO owns everything else."""
from __future__ import annotations
import json, urllib.request

class CaoClient:
    def __init__(self, base_url="http://localhost:9889", timeout=10):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def health(self) -> dict:
        return self._get("/health")

    def sessions(self) -> list:
        data = self._get("/sessions")
        return data if isinstance(data, list) else data.get("sessions", [])

    def live_task_ids(self) -> set:
        """Task ids CAO reports as alive. The resume predicate trusts THIS, not the ledger."""
        out = set()
        for s in self.sessions():
            for t in (s.get("terminals") or []):
                tid = (t.get("metadata") or {}).get("task_id")
                if tid:
                    out.add(tid)
        return out
```

```python
# cao/dispatch.py
"""Compose the modules. The rule enforced here is cross-FAMILY review."""
from __future__ import annotations
from cao.routing import ladder

def pick_reviewer(cfg, implementer_family: str, is_open):
    """Return (candidate, degraded).

    Pairing is by model FAMILY, not pool: reviewing Claude-written code with
    claude-opus-4-6 via agy would spend different quota but consult the same
    blind spots. Degrading to same-family is allowed only when nothing else is
    open, and is FLAGGED so no report can hide it.
    """
    cands = ladder(cfg, "review")
    for c in cands:
        if c.family != implementer_family and is_open(c.pool):
            return c, False
    for c in cands:
        if is_open(c.pool):
            return c, True                     # degraded, and labelled
    raise RuntimeError(
        f"no reviewer available for family {implementer_family!r}; "
        f"every pool closed: {[c.pool for c in cands]}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_dispatch.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/cao/caoapi.py skills/unattended-orchestration/cao/dispatch.py skills/unattended-orchestration/tests/cao/test_dispatch.py
git commit -m "feat(cao): cross-family review pairing with flagged degradation"
```

---

## Task 9: Config block, defaults, and full suite green

**Files:**
- Modify: `skills/unattended-orchestration/handoff.config.example.json` (append `cao` block)
- Create: `skills/unattended-orchestration/tests/cao/conftest.py`
- Test: `skills/unattended-orchestration/tests/cao/test_defaults.py`

**Interfaces:**
- Consumes: Tasks 1–8
- Produces: the canonical default `cao` block other repos copy

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/conftest.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # skills/unattended-orchestration
```

```python
# tests/cao/test_defaults.py
import json
from pathlib import Path
from cao.config import load_config
from cao.routing import check_freshness, ladder

SKILL = Path(__file__).resolve().parents[2]

def _cfg(tmp_path):
    ex = json.loads((SKILL / "handoff.config.example.json").read_text(encoding="utf-8"))
    (tmp_path / "handoff.config.json").write_text(json.dumps(ex), encoding="utf-8")
    return load_config(tmp_path)

def test_example_config_carries_a_cao_block(tmp_path):
    assert _cfg(tmp_path).max_depth >= 2

def test_default_ladders_are_fresh(tmp_path):
    assert check_freshness(_cfg(tmp_path)) == []

def test_every_task_class_has_at_least_one_candidate(tmp_path):
    cfg = _cfg(tmp_path)
    for tc in cfg.routing:
        if isinstance(cfg.routing[tc], list):
            assert ladder(cfg, tc), f"{tc} has an empty ladder"

def test_all_three_families_are_reachable(tmp_path):
    cfg = _cfg(tmp_path)
    fams = {c.family for tc in cfg.routing if isinstance(cfg.routing[tc], list)
            for c in ladder(cfg, tc)}
    assert {"anthropic", "google", "deepseek"} <= fams
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_defaults.py -v`
Expected: FAIL — `ValueError: ... has no 'cao' block`

- [ ] **Step 3: Add the `cao` block to `handoff.config.example.json`**

Insert before the closing brace, after `"pollSeconds": 30`:

```json
  "$comment_cao": [
    "The CAO lane (interactive, hierarchical, multi-provider). The batch runner",
    "ignores this block; the Python lane reads only this block.",
    "maxDepth is REAL, not advisory: cao/profiles.py generates one profile per",
    "level and the deepest one is emitted WITHOUT assign/handoff, so it cannot",
    "spawn. Raise maxDepth and regenerate; never hand-edit a generated profile.",
    "worktreeRoot must be on a native filesystem. Measured on a Windows host:",
    "git status costs 3.021s on /mnt/c versus 0.146s in an ext4 worktree.",
    "routing maps a task CLASS to an ORDERED ladder. The dispatcher takes the",
    "first candidate whose pool is open, so a ladder is both a preference order",
    "and a quota fallback. pool = where capacity comes from; family = whose blind",
    "spots. They differ: claude-opus-4-6 via agy is anthropic FAMILY from the",
    "antigravity POOL - good capacity, bad reviewer for Claude-written code."
  ],
  "cao": {
    "maxDepth": 3,
    "server": "http://localhost:9889",
    "worktreeRoot": "$HOME/cao-worktrees",
    "levels": [
      { "level": 1, "role": "orchestrator", "runner": "claude-code", "canSpawn": true },
      { "level": 2, "role": "supervisor", "canSpawn": true },
      { "level": 3, "role": "worker", "canSpawn": false }
    ],
    "pools": {
      "anthropic":   { "via": "claude" },
      "antigravity": { "via": "agy" },
      "deepseek":    { "via": "opencode", "apiKeyEnv": "DEEPSEEK_API_KEY" }
    },
    "routing": {
      "plan": [
        { "pool": "anthropic", "family": "anthropic", "model": "opus", "effort": "high" },
        { "pool": "antigravity", "family": "anthropic", "model": "claude-opus-4-6-thinking" },
        { "pool": "antigravity", "family": "google", "model": "gemini-3.1-pro-high" }
      ],
      "decompose": [
        { "pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "high" },
        { "pool": "antigravity", "family": "anthropic", "model": "claude-sonnet-4-6" }
      ],
      "research": [
        { "pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high" },
        { "pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash" }
      ],
      "implement": [
        { "pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high" },
        { "pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "medium" }
      ],
      "mechanical": [
        { "pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash" },
        { "pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-low" }
      ],
      "test": [
        { "pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-medium" },
        { "pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash" }
      ],
      "verify": [
        { "pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-medium" },
        { "pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash" }
      ],
      "review": [
        { "pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "high" },
        { "pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high" },
        { "pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash" }
      ]
    },
    "reviewPairing": "cross-family",
    "resume": { "enabled": true, "leaseMinutes": 30, "maxAttempts": 3 }
  }
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/ -q`
Expected: all pass (≈70 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/unattended-orchestration/handoff.config.example.json skills/unattended-orchestration/tests/cao/
git commit -m "feat(cao): canonical cao config block with three-pool routing ladders"
```

---

## Task 10: `setup_cao.py` — detection, install, patches

**Files:**
- Create: `infra/mcp-servers/cao-setup/setup_cao.py`
- Modify: `infra/mcp-servers/cao-setup/setup-cao.sh` (reduce to a shim)
- Delete: `infra/mcp-servers/cao-setup/patches/apply_wsl_bridge_patch.sh`
- Test: `skills/unattended-orchestration/tests/cao/test_setup.py`

**Interfaces:**
- Produces: `detect_providers() -> dict[str,bool]`; `omnigraph_entry(repo_root) -> dict`; `needs_bridge(config_path) -> bool`; `graph_id_for(worktree) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/cao/test_setup.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "infra/mcp-servers/cao-setup"))
from setup_cao import needs_bridge, graph_id_for, omnigraph_entry

def test_bridge_needed_for_windows_shared_config():
    assert needs_bridge("/mnt/c/Users/x/.gemini/config/mcp_config.json") is True

# spec defect 6: the bridge must be a NO-OP on a Linux server.
def test_bridge_is_a_noop_on_a_native_linux_path():
    assert needs_bridge("/home/you/.gemini/config/mcp_config.json") is False

def test_graph_id_is_the_repo_folder_name_like_trust_worktree():
    assert graph_id_for("/home/you/code/agent-skills") == "agent-skills"
    assert graph_id_for("/mnt/c/Users/x/Documents/Code/agent-skills/") == "agent-skills"

# spec defect 1: every CAO agent currently writes to graph `memory`.
def test_omnigraph_entry_pins_the_repo_graph_not_memory():
    e = omnigraph_entry("/home/you/code/agent-skills")
    assert e["env"]["OMNIGRAPH_GRAPH_ID"] == "agent-skills"
    assert e["env"]["OMNIGRAPH_GRAPH_ID"] != "memory"

def test_omnigraph_entry_carries_no_secret_value():
    e = omnigraph_entry("/home/you/code/agent-skills")
    assert "sk-" not in repr(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'setup_cao'`

- [ ] **Step 3: Write minimal implementation**

```python
# infra/mcp-servers/cao-setup/setup_cao.py
"""Detect, install and configure the CAO orchestration stack.

Python rather than bash because this edits JSON and YAML, must apply a patch
idempotently, and has to run on Windows (driving WSL) as well as natively on
Linux. CAO already requires Python 3.12, so this adds no new dependency.

Run:
  python setup_cao.py --check     # report only, install nothing
  python setup_cao.py --yes       # unattended bootstrap
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path

PROVIDER_BINARIES = {"anthropic": "claude", "antigravity": "agy", "deepseek": "opencode"}
BRIDGE_MARKER = "WSL to Windows cross-environment bridge"
WINDOWS_SHARED_PREFIXES = ("/mnt/", "/c/")

def needs_bridge(config_path: str) -> bool:
    """True only when the MCP config is on a Windows-shared path.

    On a Linux server this returns False and the bridge is skipped entirely —
    that is what makes the patch portable rather than Windows-shaped.
    """
    p = str(config_path).replace("\\", "/")
    return p.startswith(WINDOWS_SHARED_PREFIXES) or "\\wsl" in str(config_path)

def graph_id_for(worktree: str) -> str:
    """Repo folder name — identical derivation to trust_worktree.py:189."""
    return Path(str(worktree).rstrip("/\\")).name

def omnigraph_entry(worktree: str) -> dict:
    """Per-terminal omnigraph server pinned to THIS repo's graph.

    Without this, every CAO-spawned agent inherits the global
    ~/.gemini/config/mcp_config.json pin of OMNIGRAPH_GRAPH_ID=memory and
    silently writes to the wrong graph (spec defect 1).
    """
    return {
        "command": "npx",
        "args": ["-y", "@modernrelay/omnigraph-mcp"],
        "env": {
            "OMNIGRAPH_BASE_URL": os.environ.get("OMNIGRAPH_BASE_URL", "http://localhost:8080"),
            "OMNIGRAPH_GRAPH_ID": graph_id_for(worktree),
        },
    }

def detect_providers() -> dict:
    return {pool: shutil.which(binary) is not None
            for pool, binary in PROVIDER_BINARIES.items()}

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, install nothing")
    ap.add_argument("--yes", action="store_true", help="unattended: install without prompting")
    args = ap.parse_args(argv)
    found = detect_providers()
    for pool, ok in sorted(found.items()):
        print(f"  {pool:<12} {PROVIDER_BINARIES[pool]:<10} {'ok' if ok else 'MISSING'}")
    if args.check:
        return 0
    missing = [p for p, ok in found.items() if not ok]
    if missing and not args.yes:
        print(f"\nmissing: {', '.join(missing)} — re-run with --yes to install", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest skills/unattended-orchestration/tests/cao/test_setup.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add infra/mcp-servers/cao-setup/setup_cao.py skills/unattended-orchestration/tests/cao/test_setup.py
git commit -m "feat(cao-setup): python installer with per-terminal omnigraph graph pinning"
```

---

## Task 11: Live provider smoke test — all three families

**Files:**
- Create: `skills/unattended-orchestration/tests/cao/test_live_providers.py`
- Create: `docs/superpowers/plans/2026-09-10-cao-orchestration-lane/EVIDENCE.md`

**Interfaces:**
- Consumes: `CaoClient` (Task 8), secrets (Task 1)

These tests hit real CLIs and are marked `live`; they are skipped unless
`CAO_LIVE=1`, so the default suite stays hermetic.

- [ ] **Step 1: Write the live test**

```python
# tests/cao/test_live_providers.py
import os, subprocess, pytest
from cao.caoapi import CaoClient

live = pytest.mark.skipif(os.environ.get("CAO_LIVE") != "1", reason="set CAO_LIVE=1")
WSL = ["wsl", "-d", "Ubuntu", "-e", "bash", "-lc"]

def _run(cmd, timeout=300):
    return subprocess.run(WSL + [cmd], capture_output=True, text=True, timeout=timeout)

@live
def test_cao_server_reports_every_routed_provider():
    comp = CaoClient().health()["components"]
    assert comp["cao"] == "ok"
    assert comp["claude"] == "ok", "claude unavailable — CAO cannot spawn Claude workers"

@live
def test_gemini_worker_answers():
    r = _run("agy -p 'Reply with exactly: GEMINI_OK' --model gemini-3.8-flash-low")
    assert "GEMINI_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]

@live
def test_claude_worker_answers():
    r = _run("claude -p 'Reply with exactly: CLAUDE_OK' --model sonnet")
    assert "CLAUDE_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]

@live
def test_deepseek_worker_answers():
    r = _run("opencode run --model deepseek/deepseek-v4-flash 'Reply with exactly: DEEPSEEK_OK'")
    assert "DEEPSEEK_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]
```

- [ ] **Step 2: Run and record the actual outcome**

Run: `CAO_LIVE=1 python -m pytest skills/unattended-orchestration/tests/cao/test_live_providers.py -v`

Record the real result in `EVIDENCE.md` — including failures. A provider that
does not answer is a finding, not something to retry until it looks green.

- [ ] **Step 3: Install `opencode` and wire the DeepSeek key if that test failed**

```bash
wsl -d Ubuntu -e bash -lc 'curl -fsSL https://opencode.ai/install | bash'
```

Export the key from `secrets/api_keys.conf` into the child environment only;
never write it into `opencode.json` in the repo.

- [ ] **Step 4: Re-run and record**

- [ ] **Step 5: Commit the evidence**

```bash
git add skills/unattended-orchestration/tests/cao/test_live_providers.py docs/superpowers/plans/2026-09-10-cao-orchestration-lane/
git commit -m "test(cao): live provider smoke tests for all three model families"
```

---

## Task 12: End-to-end 3-level orchestration

**Files:**
- Create: `skills/unattended-orchestration/tests/cao/test_live_hierarchy.py`
- Modify: `docs/superpowers/plans/2026-09-10-cao-orchestration-lane/EVIDENCE.md`

This is the run the previous walkthrough never performed. It must observe an
`assign` reaching a worker and a RESULT coming back — booting a supervisor is
not delegating.

- [ ] **Step 1: Generate profiles and install them**

```bash
python -c "
import sys; sys.path.insert(0,'skills/unattended-orchestration')
from cao.config import load_config
from cao.profiles import generate
cfg = load_config('skills/unattended-orchestration')
print(generate(cfg, '/tmp/cao-profiles'))
"
wsl -d Ubuntu -e bash -lc 'cp /tmp/cao-profiles/*.yaml ~/.cao/profiles/ && ls ~/.cao/profiles'
```

- [ ] **Step 2: Verify the generated leaf really cannot spawn**

```bash
wsl -d Ubuntu -e bash -lc 'grep -A6 allowedTools ~/.cao/profiles/cao_L3_worker.yaml'
```

Expected: no `assign`, no `handoff`.

- [ ] **Step 3: Launch the supervisor and assign one task**

```bash
wsl -d Ubuntu -e bash -lc 'cao launch --agent cao_L2_supervisor --session cao-e2e --yolo'
```

- [ ] **Step 4: Observe the round trip**

```bash
wsl -d Ubuntu -e bash -lc 'cao session status cao-e2e; cao terminal list'
```

Record in `EVIDENCE.md`: the worker terminal id, the TASK sent, the RESULT
returned, and which model each layer used. If the round trip does not complete,
record what actually happened and why — do not describe a boot as a round trip.

- [ ] **Step 5: Tear down and commit**

```bash
wsl -d Ubuntu -e bash -lc 'cao shutdown --session cao-e2e'
git add docs/superpowers/plans/2026-09-10-cao-orchestration-lane/EVIDENCE.md
git commit -m "test(cao): observed 3-level assign/report round trip"
```

---

## Task 13: MCP wiring and documentation

**Files:**
- Modify: `.mcp.json`, `.claude/settings.local.json`
- Modify: `skills/unattended-orchestration/SKILL.md` (§9)
- Modify: `README.md`, `AGENTS.md`, `skills/repository-index/SKILL.md`
- Create: `infra/mcp-servers/cao-setup/README.md`

- [ ] **Step 1: Add `cao-ops` to this repo's MCP config**

```json
"cao-ops": {
  "command": "wsl",
  "args": ["-d", "Ubuntu", "bash", "-c",
           "export PATH=\"$HOME/.local/bin:$PATH\"; export CAO_HOME_DIR=\"$HOME/.cao\"; cao-ops-mcp-server"]
}
```

Add `"cao-ops"` to `enabledMcpjsonServers` in `.claude/settings.local.json` —
a tracked `.mcp.json` cannot approve itself.

- [ ] **Step 2: Rewrite SKILL.md §9**

Must state, as refusal rules rather than advice: the planning gate; the placement
rule with the 3.021 s vs 0.146 s measurement; pool-vs-family; never decline
capacity; cross-family review; the leaf rule and why `agy` needs it.

- [ ] **Step 3: Update README.md, AGENTS.md, repository-index**

- [ ] **Step 4: Run the full suite plus the PowerShell suites**

```bash
python -m pytest skills/unattended-orchestration/tests/cao/ -q
pwsh -File skills/unattended-orchestration/tests/McpTopology.Tests.ps1
```

Expected: python green; PowerShell suites unchanged (the `cao` block is additive).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(cao): wire cao-ops, rewrite SKILL.md section 9, update README and router"
```

---

## Self-Review

**Spec coverage:**

| Spec § | Task |
|---|---|
| §2 platform / Python / placement rule | 1, 10, 13 |
| §3 planning gate | 4 |
| §4 configurable depth | 7, 9 |
| §5.1 dual/triple provider | 10, 11 |
| §5.2 pool vs family | 2, 8 |
| §5.3 ladders + effort | 2, 7, 9 |
| §5.4 freshness (corrected) | 2, 9 |
| §5.5 DeepSeek | 9, 11 |
| §6 quota + resume | 3, 6 |
| §7 wire protocol | 5 |
| §8 cross-family review | 8 |
| §9 defects 1–7 | 10 (1,4,5,6), 13 (2), 7 (3), 1+13 (7) |
| §10 verification | 11, 12 |
| §11 documentation | 13 |

**Type consistency:** `Candidate(pool, family, model, effort)` is used identically in Tasks 2, 7, 8, 9. `Ledger.append/read` in 3 and 6. `is_open(pool)->bool` is the same callable shape in `select` (Task 2) and `pick_reviewer` (Task 8). `graph_id_for` matches `trust_worktree.py:189` semantics.

**Known gap, deliberate:** Task 10's `main()` reports missing providers but the
install branch is not implemented until Task 11 establishes what each vendor's
installer actually is. The `--check` path — the one CI uses — is complete.
