import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

class VerificationParser:
    def __init__(self):
        pass

    def parse(self, raw_results: List[Dict[str, Any]], worktree_path: str | Path) -> Dict[str, Any]:
        """
        Takes the raw output from VerificationRunner and parses it into a unified bundle.
        """
        worktree_path = Path(worktree_path)
        verification_dir = worktree_path / ".agent-state" / "verification"

        bundle = {
            "status": "passed",
            "summary": {
                "overall_result": "passed",
                "failing_stages": [],
                "unavailable_tools": [],
                "duration_seconds": 0.0
            },
            "commands": [],
            "pytest": {},
            "ruff": {},
            "mypy": {},
            "raw_artifacts": {
                "artifact_dir": str(verification_dir.absolute()),
                "files": []
            },
            "generated_at": datetime.utcnow().isoformat() + "Z"
        }

        total_duration = 0.0

        for res in raw_results:
            name = res["name"]
            exit_code = res["exit_code"]
            status = res["status"]
            duration = res["duration_seconds"]
            
            total_duration += duration

            bundle["commands"].append({
                "name": name,
                "command": res["command"],
                "exit_code": exit_code,
                "duration_seconds": duration,
                "status": status
            })

            if status == "failed":
                bundle["summary"]["failing_stages"].append(name)
            elif status == "unavailable":
                bundle["summary"]["unavailable_tools"].append(name)

            if res.get("stdout_path") and os.path.exists(res["stdout_path"]):
                bundle["raw_artifacts"]["files"].append(res["stdout_path"])
            if res.get("stderr_path") and os.path.exists(res["stderr_path"]):
                bundle["raw_artifacts"]["files"].append(res["stderr_path"])

            if name == "pytest":
                bundle["pytest"] = self._parse_pytest(res, verification_dir)
            elif name == "ruff":
                bundle["ruff"] = self._parse_ruff(res)
            elif name == "mypy":
                bundle["mypy"] = self._parse_mypy(res)

        bundle["summary"]["duration_seconds"] = round(total_duration, 2)

        if bundle["summary"]["failing_stages"]:
            bundle["status"] = "failed"
            bundle["summary"]["overall_result"] = "failed"
        elif bundle["summary"]["unavailable_tools"]:
            if len(bundle["summary"]["unavailable_tools"]) == len(raw_results) and len(raw_results) > 0:
                bundle["status"] = "unavailable"
                bundle["summary"]["overall_result"] = "unavailable"
            else:
                bundle["status"] = "partial"
                bundle["summary"]["overall_result"] = "partial"
        else:
            bundle["status"] = "passed"
            bundle["summary"]["overall_result"] = "passed"

        return bundle

    def _parse_pytest(self, res: Dict[str, Any], verification_dir: Path) -> Dict[str, Any]:
        pytest_data = {
            "status": res["status"],
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
            "duration_seconds": res["duration_seconds"],
            "failing_cases": [],
            "xml_path": None,
            "stdout_path": res.get("stdout_path"),
            "stderr_path": res.get("stderr_path")
        }

        if res["status"] == "unavailable":
            return pytest_data

        xml_path = verification_dir / "pytest-report.xml"
        if xml_path.exists():
            pytest_data["xml_path"] = str(xml_path.absolute())
            try:
                tree = ET.parse(xml_path)
                testsuite = tree.getroot()
                if testsuite.tag == "testsuites":
                    # Pytest sometimes wraps in testsuites
                    testsuite = testsuite.find("testsuite") or testsuite

                pytest_data["total"] = int(testsuite.attrib.get("tests", 0))
                pytest_data["failed"] = int(testsuite.attrib.get("failures", 0))
                pytest_data["errors"] = int(testsuite.attrib.get("errors", 0))
                pytest_data["skipped"] = int(testsuite.attrib.get("skipped", 0))
                pytest_data["duration_seconds"] = float(testsuite.attrib.get("time", pytest_data["duration_seconds"]))
                pytest_data["passed"] = pytest_data["total"] - pytest_data["failed"] - pytest_data["errors"] - pytest_data["skipped"]

                for testcase in testsuite.findall(".//testcase"):
                    for failure in testcase.findall("failure"):
                        pytest_data["failing_cases"].append({
                            "classname": testcase.attrib.get("classname", ""),
                            "name": testcase.attrib.get("name", ""),
                            "message": failure.attrib.get("message", failure.text)
                        })
                    for err in testcase.findall("error"):
                        pytest_data["failing_cases"].append({
                            "classname": testcase.attrib.get("classname", ""),
                            "name": testcase.attrib.get("name", ""),
                            "message": err.attrib.get("message", err.text)
                        })

            except Exception:
                # If XML is malformed, we fall back to raw output
                pytest_data["failing_cases"].append({
                    "classname": "XML Parse Error",
                    "name": "N/A",
                    "message": "Failed to parse pytest-report.xml"
                })

        return pytest_data

    def _parse_ruff(self, res: Dict[str, Any]) -> Dict[str, Any]:
        data = {
            "status": res["status"],
            "issue_count": 0,
            "stdout_path": res.get("stdout_path"),
            "stderr_path": res.get("stderr_path")
        }
        if res["status"] == "unavailable":
            return data

        lines = res["raw_stdout"].splitlines()
        # Ruff output usually looks like "Found X errors."
        for line in lines:
            if "Found" in line and ("error" in line or "issue" in line):
                try:
                    words = line.split()
                    for w in words:
                        if w.isdigit():
                            data["issue_count"] = int(w)
                            break
                except ValueError:
                    pass

        return data

    def _parse_mypy(self, res: Dict[str, Any]) -> Dict[str, Any]:
        data = {
            "status": res["status"],
            "issue_count": 0,
            "stdout_path": res.get("stdout_path"),
            "stderr_path": res.get("stderr_path")
        }
        if res["status"] == "unavailable":
            return data

        lines = res["raw_stdout"].splitlines()
        # Mypy output usually ends with "Found X errors in Y files"
        for line in lines:
            if "Found" in line and "error" in line:
                try:
                    words = line.split()
                    for w in words:
                        if w.isdigit():
                            data["issue_count"] = int(w)
                            break
                except ValueError:
                    pass

        return data
