import argparse
import json
import os
import sys
from pathlib import Path

# Ensure the root of the skill is in the Python path so we can import the orchestrator
custom_orch_root = Path(__file__).parent.parent
sys.path.insert(0, str(custom_orch_root))

from orchestrator_scaffold import Orchestrator, RiskSignals, ContractManager
from keys import KeyManager

def run_orchestrator(mode: str, force_provider: str | None = None):
    config_path = custom_orch_root / "agent_orchestration.config.yaml"
    repo_root = custom_orch_root.parent

    if mode == "live" and force_provider == "codex":
        if not os.environ.get("OPENAI_API_KEY") and not KeyManager.get("codex", "OPENAI_API_KEY"):
            print("Error: OPENAI_API_KEY environment variable or agent_keys.yaml is required to run the live codex provider.")
            sys.exit(1)
    elif mode == "live" and force_provider == "deepseek_tui":
        if not os.environ.get("DEEPSEEK_API_KEY") and not KeyManager.get("deepseek_tui", "DEEPSEEK_API_KEY"):
            print("Error: DEEPSEEK_API_KEY environment variable or agent_keys.yaml is required to run the live deepseek_tui provider.")
            sys.exit(1)
    elif mode == "live" and force_provider == "local_glm":
        if not os.environ.get("GLM_API_KEY") and not KeyManager.get("local_glm", "GLM_API_KEY"):
            print("Error: GLM_API_KEY environment variable or agent_keys.yaml is required to run the live local_glm provider.")
            sys.exit(1)
            
    if force_provider == "ollama":
        import urllib.request
        from urllib.error import URLError
        
        base_url = KeyManager.get("ollama", "OLLAMA_BASE_URL", "OLLAMA_BASE_URL") or "http://localhost:11434"
        print(f"Checking for local Ollama at {base_url}...")
        try:
            with urllib.request.urlopen(f"{base_url}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m["name"] for m in data.get("models", [])]
                if not models:
                    print("Error: Ollama is running but no local models are installed.")
                    sys.exit(1)
                print(f"Ollama detected with models: {', '.join(models)}")
        except URLError:
            print(f"Error: Could not connect to local Ollama at {base_url}. Is it running?")
            sys.exit(1)

    print("1. Loading YAML config...")
    orch = Orchestrator(str(config_path), str(repo_root))

    print("2. Building mock task...")
    signals = RiskSignals(
        touches_more_than_3_files=0.0,
        modifies_public_api=0.0,
        ambiguous_acceptance_criteria=0.0,
        weak_or_missing_test_coverage=0.0,
        cross_module_interface_change=0.0,
    )

    task = orch.build_task(
        task_id="task-mock-001",
        title="Add normalize_user_event() helper",
        scope_files=["src/events.py", "tests/test_events.py"],
        objective="Add a new normalize_user_event() helper to a small Python module and add tests.",
        acceptance=[
            "tests pass",
            "lint passes",
            "no forbidden files modified",
            "no public API break"
        ],
        signals=signals,
    )

    print(f"3. Risk Computed: Score={task.risk_score:.2f}, Mode={task.execution_mode}")

    print("4. Preparing worktree metadata...")
    agent_id = "mock-engineer-001"
    branch = f"agent/{agent_id}"
    worktree = str(orch.state.worktrees_dir / agent_id)
    print(f"   Branch: {branch}, Worktree: {worktree}")

    print("5. Writing initial checkpoint...")
    # Mocking the contract hash since we don't have a real ARCHITECTURE_CONTRACT.md in this example execution environment
    contract_hash = "mock_hash_123" 
    checkpoint = orch.create_engineer_checkpoint(agent_id, task, contract_hash, branch, worktree)
    orch.state.write_checkpoint(checkpoint)
    print(f"   Checkpoint path: {orch.state.checkpoint_path(agent_id)}")

    print(f"6. Invoking Architect ({mode.capitalize()})...")
    architect_result = orch.invoke_role_agent(
        role="architect",
        agent_id="mock-architect-001",
        system_prompt="You are the architect.",
        user_prompt="Create ARCHITECTURE_CONTRACT.md for task-mock-001",
        tools=[],
        task_tags=["planning"],
        skill_ids=["planning"],
        metadata={"execution_mode": mode},
        working_directory=str(repo_root),
        preferred_provider=force_provider
    )
    print(f"   Architect Provider: {architect_result.provider_name} ({architect_result.model})")
    print(f"   Architect Result: {architect_result.response.status}")

    print("6.5 Acquiring Lock for Engineer...")
    if orch.config.get("locking", "enabled", default=True):
        lock_acquired = orch.state.acquire_lock(
            file_key="src_events_py", 
            agent_id=agent_id, 
            task_id=task.task_id, 
            role="engineer", 
            resource="src/events.py"
        )
        print(f"   Lock Acquired: {lock_acquired}")

    print(f"7. Invoking Engineer ({mode.capitalize()})...")
    engineer_result = orch.invoke_role_agent(
        role="engineer",
        agent_id=agent_id,
        system_prompt="You are the engineer.",
        user_prompt="Implement the normalize_user_event() helper according to the contract.",
        tools=[],
        task_tags=["implementation"],
        skill_ids=["implementation"],
        metadata={"execution_mode": mode},
        working_directory=worktree,
        branch=branch,
        worktree=worktree,
        preferred_provider=force_provider
    )
    print(f"   Engineer Provider: {engineer_result.provider_name} ({engineer_result.model})")
    print(f"   Engineer Result: {engineer_result.response.status}")
    print(f"   Normalized Kind: {engineer_result.normalized_response.kind if engineer_result.normalized_response else 'none'}")

    print("7.5 Running Verification...")
    verification_bundle = orch.run_verification(worktree)
    print(f"   Verification Status: {verification_bundle['status']}")
    print(f"   Verification Summary: {verification_bundle['summary']['overall_result']}")

    # ---------------------------------------------------------
    # Handoff Routing Evaluation
    # ---------------------------------------------------------
    handoff_decision = orch.evaluate_handoff("engineer", verification_bundle, engineer_result.normalized_response)
    print(f"   Handoff Decision: {handoff_decision.decision.upper()}")
    print(f"   Decision Reason: {handoff_decision.reason}")

    reviewer_result = None

    if handoff_decision.reviewer_required:
        # Build human-readable summary for reviewer
        summary_md = f"\n\n<verification_summary>\nStatus: {verification_bundle['status']}\nOverall Result: {verification_bundle['summary']['overall_result']}\n"
        if verification_bundle["summary"]["failing_stages"]:
            summary_md += f"Failing Stages: {', '.join(verification_bundle['summary']['failing_stages'])}\n"
        if verification_bundle["summary"]["unavailable_tools"]:
            summary_md += f"Unavailable Tools: {', '.join(verification_bundle['summary']['unavailable_tools'])}\n"
        
        pytest_data = verification_bundle.get("pytest", {})
        if pytest_data.get("total", 0) > 0:
            summary_md += f"Pytest: {pytest_data['passed']}/{pytest_data['total']} passed, {pytest_data['failed']} failed\n"
            for case in pytest_data.get("failing_cases", [])[:3]:
                summary_md += f" - Failed: {case['classname']}::{case['name']}\n"
                
        summary_md += "</verification_summary>"

        print(f"8. Invoking Reviewer ({mode.capitalize()})...")
        reviewer_result = orch.invoke_role_agent(
            role="reviewer",
            agent_id="mock-reviewer-001",
            system_prompt="You are the reviewer.",
            user_prompt="Verify the engineer's implementation against the contract. Be extremely brief." + summary_md,
            tools=[],
            task_tags=["verification"],
            skill_ids=["review"],
            metadata={"execution_mode": mode, "verification_bundle": verification_bundle},
            working_directory=worktree,
            branch=branch,
            worktree=worktree,
            preferred_provider=force_provider
        )
        print(f"   Reviewer Provider: {reviewer_result.provider_name} ({reviewer_result.model})")
        print(f"   Reviewer Result: {reviewer_result.response.status}")
    else:
        print("8. Bypassing Reviewer based on Handoff Decision.")
    
    print("9. Finalizing Task State (Cleanup)...")
    if reviewer_result:
        task_status = "success" if reviewer_result.response.status == "success" else "failed"
    else:
        task_status = handoff_decision.decision if handoff_decision.decision in ["failed", "abort"] else "failed"

    cleanup_results = orch.finalize_task_state(agent_id, status=task_status)
    print(f"   Cleanup Status: {json.dumps(cleanup_results, indent=2)}")

    print("\n--- Execution Details ---")
    print(f"Mode: {mode}")
    print(f"Forced Provider: {force_provider or 'None'}")
    print(f"Credentials Source: agent_keys.yaml (or ENV fallback)")
    if force_provider == "ollama":
        print(f"Ollama Detected: Yes")
        print(f"Local Models Found: Yes")

    print("\n--- Final Status Summary ---")
    summary = {
        "task_id": "task-mock-001",
        "risk_score": 0.0,
        "execution_mode": mode,
        "verification_status": verification_bundle["status"],
        "architect_status": architect_result.response.status,
        "architect_provider": architect_result.provider_name,
        "architect_model": architect_result.model,
        "engineer_status": engineer_result.response.status,
        "engineer_provider": engineer_result.provider_name,
        "engineer_model": engineer_result.model,
        "reviewer_status": reviewer_result.response.status,
        "reviewer_provider": reviewer_result.provider_name,
        "reviewer_model": reviewer_result.model,
        "checkpoint_file": str(repo_root / ".agent-state" / "checkpoints" / "mock-engineer-001.json")
    }
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Swarm Orchestrator")
    parser.add_argument("--mode", choices=["stub", "live"], default="stub", help="Execution mode")
    parser.add_argument("--force-provider", type=str, default=None, help="Force a specific provider")
    args = parser.parse_args()
    
    if args.force_provider:
        print(f"--- Forcing Provider Override: {args.force_provider} ---")
        
    run_orchestrator(mode=args.mode, force_provider=args.force_provider)
