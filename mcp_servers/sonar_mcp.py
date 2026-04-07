import os
import subprocess
import dataclasses
from typing import Any, Dict, List
from fastmcp import FastMCP

from sonar_agent.clients.sonar_client import SonarClient
from sonar_agent.core import config

mcp = FastMCP("SonarQube Server")

@mcp.tool()
def trigger_scan(project_key: str, branch: str = "") -> str:
    """Initiates a SonarQube/SonarCloud scan on the local workspace."""
    cmd = [
        "sonar-scanner",
        f"-Dsonar.projectKey={project_key}",
        f"-Dsonar.sources=.",
        f"-Dsonar.host.url={config.SONAR_HOST_URL}",
        f"-Dsonar.token={config.SONAR_TOKEN}",
    ]
    # SonarCloud requires organization
    if getattr(config, "SONAR_ORGANIZATION", ""):
        cmd.append(f"-Dsonar.organization={config.SONAR_ORGANIZATION}")
    if branch:
         cmd.append(f"-Dsonar.branch.name={branch}")

    result = subprocess.run(
        cmd,
        cwd=config.PROJECT_PATH,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"Scan failed (Code {result.returncode}): {result.stderr or result.stdout}"

    return "Scan triggered and completed successfully."

@mcp.tool()
def get_scan_status(project_key: str) -> str:
    """Polls for completion of the SonarQube background task (Compute Engine)."""
    client = SonarClient(project_key=project_key)
    try:
        client.wait_for_analysis()
        return "Scan analysis is complete."
    except Exception as e:
        return f"Error polling scan status: {str(e)}"

@mcp.tool()
def get_issues(project_key: str, branch: str | None = None) -> List[Dict[str, Any]]:
    """Returns a structured JSON list of open issues (file path, line number, rule violated)."""
    client = SonarClient(project_key=project_key)
    issues = client.fetch_issues(branch=branch)
    
    result = []
    for issue in issues:
        issue_dict = dataclasses.asdict(issue)
        issue_dict['severity'] = issue.severity.name
        issue_dict['issue_type'] = issue.issue_type.name
        result.append(issue_dict)
    return result

@mcp.tool()
def get_rule_details(rule_key: str) -> Dict[str, Any]:
    """Fetches the specific SonarQube documentation on why the rule exists and how to fix it."""
    client = SonarClient()
    return client.get_rule(rule_key)

@mcp.resource("sonar://{project_key}/baseline_report")
def get_baseline_report(project_key: str) -> str:
    """A read-only snapshot of the initial scan before any fixes are applied."""
    client = SonarClient(project_key=project_key)
    issues = client.fetch_issues()
    lines = [f"Baseline Report for {project_key}", "-"*40]
    for i in issues:
        lines.append(f"{i.file_path}:{i.line} -> [{i.severity.name}] {i.rule}: {i.message}")
    if len(lines) == 2:
        lines.append("No open issues found in baseline.")
    return "\n".join(lines)

if __name__ == "__main__":
    mcp.run()
