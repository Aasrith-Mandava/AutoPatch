"""
AutoPatch — FastAPI Backend
Serves the React frontend with REST endpoints and SSE streaming for real-time agent updates.
"""

import json
import time
import re
from typing import Any, Dict, List
from pathlib import Path
from collections import defaultdict
import difflib

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sonar_agent.core import config
from sonar_agent.workflow.graph import build_agent_graph
from mcp_servers.github_mcp import revert_file, commit_and_push

app = FastAPI(title="AutoPatch API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for the latest run
_store: Dict[str, Any] = {
    "issues": [],
    "files_to_fix": [],
    "rule_cache": {},
    "final_report": None,
    "rejections": set(),
}


# ── Request Models ──────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    project_key: str
    branch: str = "agent-sec-fixes"
    repo_url: str = ""


class FixFileRequest(BaseModel):
    project_key: str
    branch: str = "agent-sec-fixes"
    repo_url: str = ""
    file_path: str


class RejectRequest(BaseModel):
    file_path: str


class FinalizeRequest(BaseModel):
    branch: str
    repo_url: str = ""


# ── Helper ──────────────────────────────────────────────────────────────

def _get_diff(file_path: str) -> dict:
    """Fetch baseline vs current content for a file."""
    base_path = Path(config.PROJECT_PATH) / config.BACKUP_DIR
    safe_name = file_path.replace("/", "__").replace("\\", "__")
    possible_backups = list(base_path.glob(f"{safe_name}*bak"))

    try:
        current_content = (Path(config.PROJECT_PATH) / file_path).read_text(encoding="utf-8")
    except Exception:
        current_content = ""

    if possible_backups:
        latest = sorted(possible_backups, key=lambda p: p.stat().st_mtime)[-1]
        original_content = latest.read_text(encoding="utf-8")
    else:
        original_content = ""

    diff = list(difflib.unified_diff(
        original_content.splitlines(),
        current_content.splitlines(),
        fromfile=f"Baseline ({file_path})",
        tofile=f"Refactored ({file_path})",
        lineterm=""
    ))

    return {
        "original": original_content,
        "current": current_content,
        "diff": "\n".join(diff)
    }


# ── Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "project_path": config.PROJECT_PATH}


@app.get("/api/repos/{username}")
def get_repos(username: str):
    """Fetch public GitHub repos for a username."""
    import requests as req
    resp = req.get(f"https://api.github.com/users/{username}/repos?sort=updated&per_page=100")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="Could not fetch repos")
    repos = resp.json()
    return [{"name": r["name"], "html_url": r["html_url"], "description": r.get("description", ""), "language": r.get("language", "")} for r in repos]


@app.post("/api/scan")
def scan_issues(req: ScanRequest):
    """Trigger SonarQube scan and return issues with rule explanations."""
    from mcp_servers.sonar_mcp import trigger_scan, get_scan_status, get_issues, get_rule_details
    from mcp_servers.github_mcp import setup_workspace

    setup_workspace(req.repo_url, req.branch)
    trigger_scan(req.project_key, req.branch)
    get_scan_status(req.project_key)

    issues = get_issues(req.project_key, req.branch)
    files_to_fix = list(set([i.get("file_path") for i in issues if i.get("file_path")]))

    # Fetch rule details
    unique_rules = set(i.get("rule", "") for i in issues if i.get("rule"))
    rule_cache = {}
    for rule_key in unique_rules:
        try:
            details = get_rule_details(rule_key)
            html_desc = details.get("htmlDesc", "")
            clean_desc = re.sub(r'<[^>]+>', '', html_desc) if html_desc else "No description available."
            rule_cache[rule_key] = {
                "name": details.get("name", rule_key),
                "description": clean_desc,
                "severity": details.get("severity", "UNKNOWN"),
                "type": details.get("type", "UNKNOWN"),
            }
        except Exception:
            rule_cache[rule_key] = {"name": rule_key, "description": "Rule documentation unavailable.", "severity": "UNKNOWN", "type": "UNKNOWN"}

    _store["issues"] = issues
    _store["files_to_fix"] = files_to_fix
    _store["rule_cache"] = rule_cache
    _store["final_report"] = None
    _store["rejections"] = set()

    return {
        "issues": issues,
        "files_to_fix": files_to_fix,
        "rule_cache": rule_cache,
    }


@app.post("/api/fix")
def run_fix(req: ScanRequest):
    """Run the LangGraph agent swarm and stream progress via SSE."""
    def event_stream():
        graph = build_agent_graph()
        initial_state = {
            "project_key": req.project_key,
            "branch": req.branch,
            "repo_url": req.repo_url,
            "iteration": 1,
            "fixes_applied": [],
            "issues": _store["issues"],
            "files_to_fix": _store["files_to_fix"],
        }

        try:
            for s in graph.stream(initial_state, stream_mode="updates"):
                for node_name, node_state in s.items():
                    event = {"node": node_name, "status": "running"}

                    if node_name == "supervisor_init":
                        event["message"] = "Baseline gathered. Launching workers..."
                        event["progress"] = 25
                    elif node_name == "worker_refactor":
                        event["message"] = "Workers applied fixes successfully."
                        event["progress"] = 50
                    elif node_name == "evaluator_scan":
                        event["message"] = "Verification scan complete."
                        event["progress"] = 75
                    elif node_name == "generate_report":
                        event["message"] = "Report generated."
                        event["progress"] = 100
                        _store["final_report"] = node_state.get("final_report")

                    yield f"data: {json.dumps(event)}\n\n"

            yield f"data: {json.dumps({'node': 'done', 'status': 'complete', 'progress': 100})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'node': 'error', 'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/fix-file")
def fix_single_file(req: FixFileRequest):
    """Run the LangGraph agent on a single file and return the result."""
    # Filter issues to only the target file
    file_issues = [i for i in _store["issues"] if i.get("file_path") == req.file_path]
    if not file_issues:
        raise HTTPException(status_code=404, detail=f"No issues found for {req.file_path}")

    graph = build_agent_graph()
    initial_state = {
        "project_key": req.project_key,
        "branch": req.branch,
        "repo_url": req.repo_url,
        "iteration": 1,
        "fixes_applied": [],
        "issues": file_issues,
        "files_to_fix": [req.file_path],
    }

    try:
        result = graph.invoke(initial_state)
        report = result.get("final_report", {})

        # Merge into the main store report
        if not _store["final_report"]:
            _store["final_report"] = {"fixes": [], "total_fixes_attempted": 0, "successful_fixes": 0, "remaining_issues": 0}

        for fix in report.get("fixes", []):
            # Remove any previous fix for this file
            _store["final_report"]["fixes"] = [f for f in _store["final_report"]["fixes"] if f.get("file_path") != req.file_path]
            _store["final_report"]["fixes"].append(fix)

        # Attach diff to the fix
        fix_result = None
        for fix in report.get("fixes", []):
            if fix.get("file_path") == req.file_path and fix.get("status") == "success":
                fix["diff_data"] = _get_diff(req.file_path)
                fix_result = fix
                break

        return {
            "status": "success",
            "file_path": req.file_path,
            "fix": fix_result,
            "report": report,
        }
    except Exception as e:
        return {
            "status": "error",
            "file_path": req.file_path,
            "message": str(e),
        }


@app.get("/api/report")
def get_report():
    """Return the final report after the swarm has completed."""
    if not _store["final_report"]:
        raise HTTPException(status_code=404, detail="No report available. Run a fix first.")

    report = _store["final_report"]

    # Attach diffs to each fix
    fixes = report.get("fixes", [])
    for fix in fixes:
        if fix.get("status") == "success":
            fix["diff_data"] = _get_diff(fix["file_path"])
            fix["rejected"] = fix["file_path"] in _store["rejections"]

    return report


@app.post("/api/reject")
def reject_fix(req: RejectRequest):
    """Reject a specific fix and revert the file."""
    msg = revert_file(req.file_path)
    _store["rejections"].add(req.file_path)
    return {"message": msg, "file_path": req.file_path}


@app.post("/api/finalize")
def finalize(req: FinalizeRequest):
    """Commit and push approved fixes."""
    msg = commit_and_push(req.branch, "chore(security): applied agent-negotiated fixes")

    pr_link = ""
    if req.repo_url and "github.com" in req.repo_url:
        clean_url = req.repo_url.replace(".git", "")
        pr_link = f"{clean_url}/compare/main...{req.branch}?expand=1"

    report = _store.get("final_report", {})
    return {
        "commit_message": msg,
        "pr_link": pr_link,
        "report": report,
    }


@app.post("/api/abort")
def abort_all():
    """Revert all fixes."""
    report = _store.get("final_report")
    if not report:
        return {"message": "Nothing to abort."}

    for fix in report.get("fixes", []):
        if fix.get("status") == "success" and fix["file_path"] not in _store["rejections"]:
            revert_file(fix["file_path"])

    _store["final_report"] = None
    _store["rejections"] = set()
    return {"message": "All fixes reverted successfully."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
