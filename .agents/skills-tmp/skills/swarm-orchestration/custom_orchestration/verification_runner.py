import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List

class VerificationRunner:
    def __init__(self, config_data: Dict[str, Any], repo_root: str | Path):
        self.config_data = config_data
        self.repo_root = Path(repo_root)

    def run_all(self, worktree_path: str | Path) -> List[Dict[str, Any]]:
        """
        Executes all configured verification commands in the specified worktree.
        Returns a list of raw execution results.
        """
        worktree_path = Path(worktree_path)
        
        # Ensure the verification state directory exists
        verification_dir = worktree_path / ".agent-state" / "verification"
        verification_dir.mkdir(parents=True, exist_ok=True)

        results = []
        
        # Load commands from config
        python_config = self.config_data.get("verification", {}).get("python", {})
        
        tools = ["pytest", "ruff", "mypy"]
        for tool in tools:
            tool_cfg = python_config.get(tool, {})
            if not tool_cfg.get("enabled", False):
                continue
            
            command = tool_cfg.get("command")
            if not command:
                continue
            
            start_time = time.time()
            try:
                import shlex
                # Harden execution: use argv lists and avoid shell=True
                cmd_args = shlex.split(command)
                proc = subprocess.run(
                    cmd_args,
                    cwd=str(worktree_path),
                    shell=False,
                    capture_output=True,
                    text=True
                )
                exit_code = proc.returncode
                stdout = proc.stdout
                stderr = proc.stderr
                
                # Check for "command not found" / "is not recognized" explicitly
                if exit_code != 0 and ("not found" in stderr or "not recognized" in stderr):
                    exit_code = -1
            except FileNotFoundError as e:
                exit_code = -1
                stdout = ""
                stderr = f"Command not found: {e}"
            except Exception as e:
                exit_code = -1
                stdout = ""
                stderr = str(e)
                
            end_time = time.time()
            duration = round(end_time - start_time, 2)

            # Persist raw outputs
            stdout_path = verification_dir / f"{tool}_stdout.log"
            stderr_path = verification_dir / f"{tool}_stderr.log"
            
            stdout_path.write_text(stdout, encoding="utf-8")
            if stderr:
                stderr_path.write_text(stderr, encoding="utf-8")

            results.append({
                "name": tool,
                "command": command,
                "exit_code": exit_code,
                "duration_seconds": duration,
                "stdout_path": str(stdout_path.absolute()),
                "stderr_path": str(stderr_path.absolute()) if stderr else None,
                "status": "passed" if exit_code == 0 else "failed" if exit_code > 0 else "unavailable",
                "raw_stdout": stdout,
                "raw_stderr": stderr
            })
            
        return results
