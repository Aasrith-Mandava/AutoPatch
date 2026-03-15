from typing import Any, Dict
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from mcp_servers.sonar_mcp import trigger_scan, get_scan_status, get_issues, get_rule_details, get_baseline_report
from mcp_servers.github_mcp import setup_workspace, read_file, write_file

from sonar_agent.workflow.state import AgentState, WorkerState
from sonar_agent.workflow.llm_factory import get_langchain_llm


class RefactoredCode(BaseModel):
    new_content: str = Field(description="The full, complete, and refactored content of the file. No truncation.")
    explanation: str = Field(description="Explanation of what was changed and why.")


def supervisor_init(state: AgentState) -> Dict[str, Any]:
    """Sets up the workspace and runs the baseline scan."""
    print("supervisor_init: Setting up workspace and triggering scan...")
    
    # Initialize workspace structure
    setup_msg = setup_workspace(state["repo_url"], state["branch"])
    print(f"  {setup_msg}")
    
    # Trigger scan
    scan_msg = trigger_scan(state["project_key"], state["branch"])
    print(f"  {scan_msg}")
    
    # Wait for completion
    status_msg = get_scan_status(state["project_key"])
    print(f"  {status_msg}")
    
    # Get issues
    issues = get_issues(state["project_key"], state["branch"])
    print(f"  Found {len(issues)} issues.")
    
    # Group by file
    files_to_fix = list(set([issue.get("file_path") for issue in issues if issue.get("file_path")]))
    
    return {
        "issues": issues,
        "files_to_fix": files_to_fix,
        "iteration": 1,
        "fixes_applied": []
    }


def worker_refactor(state: WorkerState) -> Dict[str, Any]:
    """Worker node that refactors a specific file using the LLM in parallel."""
    print(f"[Worker] Refactoring {state['file_path']}...")
    
    content = read_file(state["file_path"])
    if "Error reading" in content:
        print(f"  {content}")
        return {"fixes_applied": [{"file_path": state["file_path"], "status": "error", "message": content}]}
        
    rules_text = ""
    for issue in state["issues_for_file"]:
        # Fetch detailed docs about why this rule exists from SonarMCP
        details = get_rule_details(issue.get("rule", ""))
        rules_text += f"\nRule {issue.get('rule')}: {issue.get('message')}\nReasoning: {details.get('htmlDesc', 'N/A')}\n"
        
    prompt = f"""You are an expert security and code quality refactoring agent.
You need to fix the following SonarQube issues in the provided file.
CRITICAL: Do not alter the business logic or remove existing features. Only apply the surgical fixes necessary to satisfy the rules.

Issues:
{rules_text}

Original File Content:
```
{content}
```
"""
    llm = get_langchain_llm()
    structured_llm = llm.with_structured_output(RefactoredCode)
    
    try:
        response = structured_llm.invoke([HumanMessage(content=prompt)])
        write_msg = write_file(state["file_path"], response.new_content)
        print(f"  Successfully applied fix. {write_msg}")
        
        return {
            "fixes_applied": [{
                "file_path": state["file_path"],
                "status": "success",
                "explanation": response.explanation,
                "iteration_applied": state["iteration"]
            }]
        }
    except Exception as e:
        print(f"  Error invoking LLM: {str(e)}")
        return {"fixes_applied": [{"file_path": state["file_path"], "status": "error", "message": str(e)}]}


def evaluator_scan(state: AgentState) -> Dict[str, Any]:
    """Runs a verification scan to see if the files are fixed."""
    print(f"\nevaluator_scan: Triggering verification scan (Iteration {state['iteration']})...")
    
    trigger_scan(state["project_key"], state["branch"])
    get_scan_status(state["project_key"])
    
    new_issues = get_issues(state["project_key"], state["branch"])
    new_files_with_issues = list(set([issue.get("file_path") for issue in new_issues if issue.get("file_path")]))
    
    # We will refine files_to_fix based on what still has issues.
    still_broken = [f for f in state["files_to_fix"] if f in new_files_with_issues]
    
    resolved_count = len(state["files_to_fix"]) - len(still_broken)
    print(f"  {resolved_count} files fixed successfully. {len(still_broken)} still have issues.")
    
    return {
        "issues": new_issues,
        "files_to_fix": still_broken,
        "iteration": state["iteration"] + 1
    }


def generate_report(state: AgentState) -> Dict[str, Any]:
    """Compiles the final JSON report combining baseline and verification results."""
    print("generate_report: Generating final contract...")
    
    success_fixes = [f for f in state["fixes_applied"] if f.get("status") == "success"]
    
    report = {
        "project_key": state["project_key"],
        "branch": state["branch"],
        "total_fixes_attempted": len(state["fixes_applied"]),
        "successful_fixes": len(success_fixes),
        "fixes": state["fixes_applied"],
        "remaining_issues": len(state["issues"])
    }
    
    return {"final_report": report}
