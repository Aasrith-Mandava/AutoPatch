import streamlit as st
import time
from pathlib import Path
import os
import difflib

from sonar_agent.core import config
from sonar_agent.workflow.graph import build_agent_graph
from mcp_servers.github_mcp import revert_file, commit_and_push

st.set_page_config(page_title="Lumina Agent UI", page_icon="🔍", layout="wide")

# Initialize session state for the workflow
if "workflow_state" not in st.session_state:
    st.session_state.workflow_state = "idle"  # idle, running, review
if "final_report" not in st.session_state:
    st.session_state.final_report = None
if "agent_state" not in st.session_state:
    st.session_state.agent_state = None


def get_diff(file_path: str) -> tuple[str, str]:
    """Manually fetches the baseline backup versus the currently modified file."""
    base_path = Path(config.PROJECT_PATH) / config.BACKUP_DIR
    safe_name = file_path.replace("/", "__").replace("\\", "__")
    possible_backups = list(base_path.glob(f"{safe_name}*bak"))
    
    current_content = (Path(config.PROJECT_PATH) / file_path).read_text(encoding="utf-8")
    original_content = ""
    
    if possible_backups:
        latest = sorted(possible_backups, key=lambda p: p.stat().st_mtime)[-1]
        original_content = latest.read_text(encoding="utf-8")
    else:
        original_content = "# Error: No baseline backup found."
        
    return original_content, current_content


st.title("Lumina: Autonomous Code Correction Agent")
st.markdown("This Elicitation UI reviews changes proposed by the LangGraph Swarm before committing to your branch.")

# Sidebar setup
with st.sidebar:
    st.header("Graph Configuration")
    project_key = st.text_input("SonarQube Project Key", value=config.SONAR_PROJECT_KEY)
    branch = st.text_input("Target Branch", value="agent-sec-fixes")
    repo_url = st.text_input("Repo URL", value="local")
    
    if st.button("Start Agent Workflow", type="primary"):
        if st.session_state.workflow_state != "running":
            st.session_state.workflow_state = "running"
            st.rerun()

# ── Runner ──
if st.session_state.workflow_state == "running":
    st.info("Agent Swarm is initializing. Running baseline scan and evaluating repairs...")
    
    # Progress placeholder
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    graph = build_agent_graph()
    
    initial_state = {
        "project_key": project_key,
        "branch": branch,
        "repo_url": repo_url,
        "iteration": 1,
        "fixes_applied": [],
        "files_to_fix": []
    }
    
    try:
        # Stream the graph execution to update the UI
        for s in graph.stream(initial_state, stream_mode="updates"):
            for node_name, node_state in s.items():
                status_text.text(f"Currently executing phase: {node_name}...")
                
                # Update progress bar heuristically based on node flow
                if node_name == "supervisor_init":
                    progress_bar.progress(25)
                elif node_name == "worker_refactor":
                    progress_bar.progress(50)
                elif node_name == "evaluator_scan":
                    progress_bar.progress(75)
                elif node_name == "generate_report":
                    progress_bar.progress(100)
                    st.session_state.final_report = node_state.get("final_report")
                    
        st.session_state.workflow_state = "review"
        st.rerun()
        
    except Exception as e:
        st.error(f"Agent flow encountered an error: {str(e)}")
        st.session_state.workflow_state = "idle"


# ── Review (Human-In-The-Loop) ──
if st.session_state.workflow_state == "review" and st.session_state.final_report:
    report = st.session_state.final_report
    
    st.success(f"Graph execution paused. Agent completed repairs on {report['successful_fixes']} file(s).")
    st.markdown("### Proposed Fixes (Contract Negotiation)")
    
    if "rejections" not in st.session_state:
        st.session_state.rejections = set()
    
    fixes = [f for f in report["fixes"] if f.get("status") == "success"]
    
    if not fixes:
        st.info("No files required fixing, or the agent could not fix the issues.")
        
    for fix in fixes:
        file_path = fix["file_path"]
        is_rejected = file_path in st.session_state.rejections
        
        flagged = fix.get("flagged_by_judge", False)
        
        if is_rejected:
            label_suffix = "❌ (Rejected)"
        elif flagged:
            label_suffix = "⚠️ (Flagged by AI Judge)"
        else:
            label_suffix = "✅ (Pending Approval)"
            
        # We auto-expand flagged items or pending items
        with st.expander(f"Review Fix: {file_path} {label_suffix}", expanded=(not is_rejected) or flagged):
            st.markdown(f"**Agent's Rationale:** {fix.get('explanation', 'Fixed SonarQube rules.')}")
            
            if flagged:
                st.warning(f"**Judge Warning:** {fix.get('judge_rationale', 'Core business logic may have been altered.')}")
            
            orig, updated = get_diff(file_path)
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Original Baseline (Backup)**")
                st.code(orig, language="python" if file_path.endswith(".py") else "javascript")
            with col2:
                st.markdown("**Agent Refactored Output**")
                st.code(updated, language="python" if file_path.endswith(".py") else "javascript")
                
            if not is_rejected:
                if st.button(f"Reject Fix for {file_path}", key=f"reject_{file_path}"):
                    msg = revert_file(file_path) # Call underlying GitHub MCP
                    st.toast(msg)
                    st.session_state.rejections.add(file_path)
                    st.rerun()
            else:
                st.error(f"This fix was rejected and reverted. The file is back to baseline.")
                
    st.divider()
    
    col3, col4 = st.columns(2)
    with col3:
         if st.button("Finalize Contract & Push", type="primary", use_container_width=True):
             st.info("Committing approved fixes to the remote branch...")
             msg = commit_and_push(report["branch"], "chore(security): applied agent-negotiated fixes")
             st.success(msg)
             st.session_state.workflow_state = "idle"
             
    with col4:
         if st.button("Abort Entire Contract", use_container_width=True):
             st.warning("Reverting everything...")
             for fix in fixes:
                 revert_file(fix["file_path"])
             st.session_state.workflow_state = "idle"
             st.rerun()
