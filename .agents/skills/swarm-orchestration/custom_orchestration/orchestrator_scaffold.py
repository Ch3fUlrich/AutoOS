from __future__ import annotations

from dataclasses import dataclass, field, asdict
from provider_executor import ProviderExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import json
import os
import subprocess
import time
import yaml
from verification_runner import VerificationRunner
from verification_parser import VerificationParser
from decision_engine import DecisionEngine, HandoffDecision


@dataclass
class RiskSignals:
    touches_more_than_3_files: float = 0.0
    modifies_public_api: float = 0.0
    ambiguous_acceptance_criteria: float = 0.0
    high_churn_module: float = 0.0
    architect_confidence_below_threshold: float = 0.0
    weak_or_missing_test_coverage: float = 0.0
    concurrency_or_async_change: float = 0.0
    security_sensitive_code: float = 0.0
    prior_review_failure: float = 0.0
    cross_module_interface_change: float = 0.0
    estimate_variance_above_30pct: float = 0.0


@dataclass
class Checkpoint:
    agent_id: str
    task_id: str
    role: str
    branch: str
    worktree: str
    architecture_contract_hash: str
    files_touched: List[str] = field(default_factory=list)
    last_completed_step: str = ""
    next_step: str = ""
    verification_status: str = "unknown"
    tokens_used: int = 0
    risk_level: str = "low"
    unresolved_issues: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class Task:
    task_id: str
    title: str
    scope_files: List[str]
    acceptance_criteria: List[str]
    objective: str
    status: str = "draft"
    risk_score: float = 0.0
    execution_mode: str = "single-engineer"


class Config:
    def __init__(self, path: str | Path):
        with open(path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f)

    def get(self, *keys, default=None):
        cur = self.data
        for key in keys:
            if key not in cur:
                return default
            cur = cur[key]
        return cur


class StateStore:
    def __init__(self, config: Config):
        self.state_dir = Path(config.get("paths", "state_dir"))
        self.checkpoint_dir = Path(config.get("paths", "checkpoint_dir"))
        self.locks_dir = Path(config.get("paths", "locks_dir"))
        self.worktrees_dir = Path(config.get("paths", "worktrees_dir"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def checkpoint_path(self, agent_id: str) -> Path:
        return self.checkpoint_dir / f"{agent_id}.json"

    def write_checkpoint(self, cp: Checkpoint) -> None:
        path = self.checkpoint_path(cp.agent_id)
        path.write_text(json.dumps(asdict(cp), indent=2), encoding="utf-8")

    def read_checkpoint(self, agent_id: str) -> Optional[Checkpoint]:
        path = self.checkpoint_path(agent_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(**data)

    def acquire_lock(self, file_key: str, agent_id: str, task_id: str, role: str, resource: str, ttl_seconds: int = 300) -> bool:
        path = self.locks_dir / f"{file_key}.lock"
        now = time.time()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if now < data.get("expires_at", 0):
                    return False
            except json.JSONDecodeError:
                pass # Stale or corrupt lock file
                
        payload = {
            "lock_id": file_key,
            "agent_id": agent_id,
            "task_id": task_id,
            "role": role,
            "resource": resource,
            "created_at": now,
            "expires_at": now + ttl_seconds,
            "status": "active"
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True

    def release_lock(self, file_key: str, agent_id: str) -> bool:
        path = self.locks_dir / f"{file_key}.lock"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("agent_id") == agent_id:
                path.unlink()
                return True
        except json.JSONDecodeError:
            path.unlink()
            return True
        return False

    def reclaim_stale_locks(self) -> List[str]:
        reclaimed = []
        now = time.time()
        for path in self.locks_dir.glob("*.lock"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if now >= data.get("expires_at", 0):
                    path.unlink()
                    reclaimed.append(data.get("lock_id", path.stem))
            except json.JSONDecodeError:
                path.unlink()
                reclaimed.append(path.stem)
        return reclaimed


class GitManager:
    def __init__(self, repo_root: str | Path, worktrees_dir: str | Path):
        self.repo_root = Path(repo_root)
        self.worktrees_dir = Path(worktrees_dir)

    def run(self, *args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=str(cwd or self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def branch_exists(self, branch: str) -> bool:
        result = self.run("git", "show-ref", "--verify", f"refs/heads/{branch}")
        return result.returncode == 0

    def create_worktree(self, branch: str, name: str) -> Path:
        worktree = self.worktrees_dir / name
        if not self.branch_exists(branch):
            self.run("git", "branch", branch)
        if not worktree.exists():
            self.run("git", "worktree", "add", str(worktree), branch)
        return worktree

    def worktree_status(self, worktree: Path) -> str:
        result = self.run("git", "status", "--short", cwd=worktree)
        return result.stdout.strip()

    def remove_worktree(self, worktree: Path, force: bool = False) -> Dict[str, Any]:
        result = {"action": "remove_worktree", "target": str(worktree), "success": False, "reason": ""}
        if not worktree.exists():
            result["reason"] = "Path does not exist"
            return result
            
        # Safety check: do not remove the main repository
        if worktree.resolve() == self.repo_root.resolve():
            result["reason"] = "Safety block: Cannot remove main repository worktree"
            return result
            
        args = ["git", "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree))
        
        proc = self.run(*args)
        if proc.returncode == 0:
            result["success"] = True
            result["reason"] = "Removed"
        else:
            result["reason"] = proc.stderr.strip()
        return result

    def prune_worktrees(self, dry_run: bool = False) -> Dict[str, Any]:
        result = {"action": "prune_worktrees", "success": False, "reason": "", "dry_run": dry_run}
        args = ["git", "worktree", "prune"]
        if dry_run:
            args.append("--dry-run")
            
        proc = self.run(*args)
        if proc.returncode == 0:
            result["success"] = True
            result["reason"] = proc.stdout.strip() if proc.stdout.strip() else "Clean"
        else:
            result["reason"] = proc.stderr.strip()
        return result


class ContractManager:
    def __init__(self, contract_path: str | Path):
        self.contract_path = Path(contract_path)

    def load(self) -> str:
        return self.contract_path.read_text(encoding="utf-8")

    def hash(self) -> str:
        content = self.load().encode("utf-8")
        return hashlib.sha256(content).hexdigest()


class RiskEngine:
    def __init__(self, config: Config):
        self.weights = config.get("risk", "signals", default={})
        self.thresholds = config.get("risk", "thresholds", default={})

    def score(self, signals: RiskSignals) -> float:
        total = 0.0
        weight_sum = 0.0
        for field_name, weight in self.weights.items():
            weight = float(weight)
            value = float(getattr(signals, field_name, 0.0))
            total += value * weight
            weight_sum += weight
        return total / weight_sum if weight_sum else 0.0

    def classify(self, score: float) -> str:
        if score < float(self.thresholds["low"]):
            return "low"
        if score < float(self.thresholds["medium"]):
            return "medium"
        if score < float(self.thresholds["high"]):
            return "high"
        return "very_high"

    def execution_mode(self, score: float) -> str:
        level = self.classify(score)
        if level == "low":
            return "single-engineer"
        if level == "medium":
            return "single-engineer-strict-review"
        if level == "high":
            return "best-of-3"
        return "best-of-5"


class BudgetEngine:
    def __init__(self, config: Config):
        self.enabled = config.get("token_budget", "enabled", default=True)
        self.states = config.get("token_budget", "states", default={})

    def state(self, used_ratio: float) -> str:
        if used_ratio < float(self.states["green"]):
            return "green"
        if used_ratio < float(self.states["yellow"]):
            return "yellow"
        if used_ratio < float(self.states["red"]):
            return "red"
        return "red"


class GateRunner:
    def __init__(self, config: Config):
        self.enabled = config.get("safety_gates", "enabled", default=True)
        self.deny_list = config.get("safety_gates", "deny_list", default={})
        self.size_ceiling = config.get("safety_gates", "size_ceiling", default={})

    def run_gates(self, diff_stats: Dict[str, Any], changed_files: List[str]) -> Dict[str, Any]:
        if not self.enabled:
            return {"passed": True}

        # Check deny list
        import re
        for category, pattern in self.deny_list.items():
            regex = re.compile(pattern)
            for file_path in changed_files:
                if regex.search(file_path):
                    # Fail-closed invariant: if it hits a gate, it MUST block.
                    return {"passed": False, "reason": "DENY_LIST_MATCH", "category": category, "file": file_path}

        # Check size ceiling
        max_files = self.size_ceiling.get("max_files_changed", 30)
        max_lines = self.size_ceiling.get("max_lines_changed", 800)
        exemptions = self.size_ceiling.get("exemptions", [])

        non_exempt_files = []
        for file_path in changed_files:
            if not any(re.compile(ex).search(file_path) for ex in exemptions):
                non_exempt_files.append(file_path)

        if len(non_exempt_files) > max_files:
            return {"passed": False, "reason": "SIZE_CEILING_EXCEEDED", "metric": "files", "value": len(non_exempt_files)}
        if diff_stats.get("lines_changed", 0) > max_lines:
            return {"passed": False, "reason": "SIZE_CEILING_EXCEEDED", "metric": "lines", "value": diff_stats.get("lines_changed", 0)}

        return {"passed": True}


class ReviewerPanel:
    def __init__(self, config: Config, provider_executor: ProviderExecutor):
        self.config = config
        self.provider_executor = provider_executor
        self.swarm_config = config.get("swarm_review", default={})

    def execute_swarm(self, diff_content: str, metadata: dict) -> List[Dict[str, Any]]:
        if not self.swarm_config.get("enabled", True):
            return []

        perspectives = self.swarm_config.get("perspectives", {})
        import concurrent.futures
        results = []

        # Parallel convergence execution
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_perspective = {}
            for perspective_name, details in perspectives.items():
                future = executor.submit(
                    self.provider_executor.execute,
                    role="reviewer",
                    agent_id=f"swarm_{perspective_name}",
                    system_prompt=f"You are a {perspective_name}. Focus on: {details.get('focus')}",
                    user_prompt=f"Review this diff:\n{diff_content}",
                    tools=[],
                    mcp_servers=[],
                    skill_ids=[],
                    metadata=metadata,
                    preferred_provider=details.get("model_tier")
                )
                future_to_perspective[future] = perspective_name

            timeout = self.swarm_config.get("timeout_seconds", 600)
            done, not_done = concurrent.futures.wait(
                future_to_perspective.keys(), timeout=timeout
            )

            for future in done:
                try:
                    res = future.result()
                    results.append({"perspective": future_to_perspective[future], "result": res})
                except Exception as exc:
                    results.append({"perspective": future_to_perspective[future], "error": str(exc)})

            for future in not_done:
                results.append({"perspective": future_to_perspective[future], "error": "timeout"})

        return results


class TriagePipeline:
    def __init__(self, config: Config):
        self.policy = config.get("triage_policy", default={})

    def is_human_participating(self, thread_comments: List[Dict[str, Any]]) -> bool:
        gate = self.policy.get("human_participation_gate", {})
        if not gate.get("enabled", True):
            return False
        for comment in thread_comments:
            if not comment.get("is_bot", False):
                return True
        return False

    def calculate_verdict(self, swarm_findings: List[Dict[str, Any]]) -> str:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "nit": 0}
        for finding in swarm_findings:
            severity = finding.get("severity", "low").lower()
            if severity in counts:
                counts[severity] += 1

        if counts["critical"] > 0:
            return "BLOCKED"
        if counts["high"] >= 2 or (counts["high"] >= 1 and counts["medium"] >= 2):
            return "REQUEST_CHANGES"
        if counts["high"] >= 1 or counts["medium"] >= 3:
            return "APPROVE_WITH_NITS"
        return "APPROVE"


class BabysitState:
    def __init__(self, config: Config, state_store: StateStore):
        self.config = config.get("babysit_state", default={})
        self.evidence_config = config.get("evidence_bundle", default={})
        self.state_store = state_store

    def track_ci_status(self, pr_id: str, current_status: str, logs: Optional[str] = None) -> Dict[str, Any]:
        # Represents transient state tracking
        state_file = self.state_store.state_dir / f"babysit_{pr_id}.json"
        
        state = {}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        state["last_status"] = current_status
        if logs and self.evidence_config.get("capture_logs", True):
            state.setdefault("evidence_bundles", []).append(logs)
            
        if current_status == "failed":
            state["retries"] = state.get("retries", 0) + 1
            
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        
        exceeds_retries = state.get("retries", 0) > self.evidence_config.get("max_retries_per_failure", 3)
        return {"state": state, "exceeds_retries": exceeds_retries}


class Narrator:
    def __init__(self, config: Config):
        self.config = config.get("narration", default={})

    def format_review_comment(self, verdict: str, findings: List[Dict[str, Any]]) -> Optional[str]:
        if self.config.get("mode") == "silent" and verdict in ["APPROVE", "APPROVE_WITH_NITS"]:
            # In silent mode, nits are auto-fixed or ignored without posting a comment
            return None

        lines = []
        if self.config.get("bluf_required", True):
            lines.append(f"**Verdict:** {verdict}\n")

        for finding in findings:
            prefix = ""
            if finding.get("autofixable", False) and self.config.get("prefix_autofixable"):
                prefix = self.config.get("prefix_autofixable") + " "
                
            attribution = ""
            if self.config.get("attribute_findings", True) and "perspective" in finding:
                attribution = f"*(From {finding['perspective']})* "

            lines.append(f"- {prefix}{attribution}{finding.get('description')}")

        return "\n".join(lines)


class MCPRouter:
    def __init__(self, config: Config):
        self.config = config

    def for_role(self, role: str, task_tags: List[str]) -> List[str]:
        allowed = list(self.config.get("roles", role, "allowed_mcps", default=[]))
        conditional = self.config.get("mcp", "conditional", default={})
        selected = []
        for mcp in allowed:
            if mcp in conditional:
                conditions = conditional[mcp].get("when", [])
                if any(tag in task_tags for tag in conditions):
                    selected.append(mcp)
            else:
                selected.append(mcp)
        return selected


class Orchestrator:
    def __init__(self, config_path: str, repo_root: str):
        self.config = Config(config_path)
        self.state = StateStore(self.config)
        self.git = GitManager(repo_root, self.config.get("paths", "worktrees_dir"))
        self.risk_engine = RiskEngine(self.config)
        self.budget_engine = BudgetEngine(self.config)
        self.mcp_router = MCPRouter(self.config)
        self.provider_executor = ProviderExecutor(self.config.data)
        self.verification_runner = VerificationRunner(self.config.data, repo_root)
        self.verification_parser = VerificationParser()
        self.decision_engine = DecisionEngine(self.config.data)

    def evaluate_handoff(self, role: str, verification_bundle: Optional[Dict[str, Any]], normalized_response: Any) -> HandoffDecision:
        return self.decision_engine.evaluate(role, verification_bundle, normalized_response)

    def run_verification(self, worktree_path: str) -> Dict[str, Any]:
        raw_results = self.verification_runner.run_all(worktree_path)
        return self.verification_parser.parse(raw_results, worktree_path)

    def build_task(self, task_id: str, title: str, scope_files: List[str], objective: str, acceptance: List[str], signals: RiskSignals) -> Task:
        score = self.risk_engine.score(signals)
        mode = self.risk_engine.execution_mode(score)
        return Task(
            task_id=task_id,
            title=title,
            scope_files=scope_files,
            acceptance_criteria=acceptance,
            objective=objective,
            risk_score=score,
            execution_mode=mode,
        )

    def create_engineer_checkpoint(self, agent_id: str, task: Task, contract_hash: str, branch: str, worktree: str) -> Checkpoint:
        return Checkpoint(
            agent_id=agent_id,
            task_id=task.task_id,
            role="engineer",
            branch=branch,
            worktree=worktree,
            architecture_contract_hash=contract_hash,
            files_touched=task.scope_files,
            last_completed_step="initialized",
            next_step="read_contract_and_implement",
            verification_status="pending",
            risk_level=self.risk_engine.classify(task.risk_score),
        )

    def prepare_worktree(self, agent_id: str) -> tuple[str, str]:
        branch = f"agent/{agent_id}"
        worktree = self.git.create_worktree(branch, agent_id)
        return branch, str(worktree)

    def recover(self, agent_id: str) -> Optional[Checkpoint]:
        cp = self.state.read_checkpoint(agent_id)
        if not cp:
            return None
        worktree = Path(cp.worktree)
        if not worktree.exists() and cp.branch:
            self.git.create_worktree(cp.branch, agent_id)
        return cp
        
    def finalize_task_state(self, agent_id: str, status: str) -> Dict[str, Any]:
        """
        Cleans up task state based on the status (success, abort, failed).
        Releases locks, removes worktrees, and prunes metadata depending on config.
        """
        results = {"status": status, "locks_released": 0, "worktree_removed": None, "prune_result": None}
        
        # Release locks held by this agent
        locks_released = 0
        for path in self.state.locks_dir.glob("*.lock"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("agent_id") == agent_id:
                    path.unlink()
                    locks_released += 1
            except Exception:
                pass
        results["locks_released"] = locks_released
        
        cp = self.state.read_checkpoint(agent_id)
        if not cp:
            return results
            
        worktree = Path(cp.worktree)
        cleanup_cfg = self.config.get("cleanup", default={})
        
        # Determine whether to remove worktree
        should_remove = False
        force = False
        if status == "success" and cleanup_cfg.get("remove_temp_worktrees_on_success", True):
            should_remove = True
            force = True # successful tasks can be force-removed since changes are merged/pushed
        elif status in ["abort", "failed"] and not cleanup_cfg.get("preserve_failed_worktrees", True):
            should_remove = True
            
        if should_remove:
            results["worktree_removed"] = self.git.remove_worktree(worktree, force=force)
            
        if cleanup_cfg.get("prune_stale_metadata", True):
            results["prune_result"] = self.git.prune_worktrees(dry_run=False)
            
        return results

    def route_mcps(self, role: str, task_tags: List[str]) -> List[str]:
        return self.mcp_router.for_role(role, task_tags)

    def budget_state(self, estimated_tokens: int, used_tokens: int) -> str:
        if estimated_tokens <= 0:
            return "green"
        ratio = used_tokens / estimated_tokens
        return self.budget_engine.state(ratio)
    
    def invoke_role_agent(
        self,
        role: str,
        agent_id: str,
        system_prompt: str,
        user_prompt: str,
        tools: list,
        task_tags: list,
        skill_ids: list,
        metadata: dict,
        working_directory: str | None = None,
        branch: str | None = None,
        worktree: str | None = None,
        cache_control: dict | None = None,
        checkpoint_hint: dict | None = None,
        preferred_provider: str | None = None,
    ):
        mcps = self.route_mcps(role, task_tags)
        return self.provider_executor.execute(
            role=role,
            agent_id=agent_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            mcp_servers=mcps,
            skill_ids=skill_ids,
            metadata=metadata,
            working_directory=working_directory,
            branch=branch,
            worktree=worktree,
            cache_control=cache_control,
            checkpoint_hint=checkpoint_hint,
            preferred_provider=preferred_provider,
        )


if __name__ == "__main__":
    config_path = os.environ.get("ORCH_CONFIG", "agent_orchestration.config.yaml")
    repo_root = os.environ.get("REPO_ROOT", ".")

    orch = Orchestrator(config_path, repo_root)

    signals = RiskSignals(
        touches_more_than_3_files=1.0,
        modifies_public_api=1.0,
        ambiguous_acceptance_criteria=0.5,
        weak_or_missing_test_coverage=1.0,
        cross_module_interface_change=1.0,
    )

    task = orch.build_task(
        task_id="task-001",
        title="Implement scoped feature",
        scope_files=["src/module_a.py", "tests/test_module_a.py"],
        objective="Implement the requested feature without violating public contract boundaries.",
        acceptance=[
            "tests pass",
            "lint passes",
            "typecheck passes",
            "no forbidden files modified",
        ],
        signals=signals,
    )

    contract = ContractManager("ARCHITECTURE_CONTRACT.md")
    branch, worktree = orch.prepare_worktree("engineer-001")
    checkpoint = orch.create_engineer_checkpoint("engineer-001", task, contract.hash(), branch, worktree)
    orch.state.write_checkpoint(checkpoint)

    print(json.dumps({
        "task_id": task.task_id,
        "risk_score": task.risk_score,
        "execution_mode": task.execution_mode,
        "mcps_engineer": orch.route_mcps("engineer", ["runtime_error_debugging"]),
        "checkpoint_file": str(orch.state.checkpoint_path("engineer-001")),
    }, indent=2))