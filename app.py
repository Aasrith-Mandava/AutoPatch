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


st.title("✨ Lumina: Code Correction Agent")
st.markdown("This Elicitation UI reviews changes proposed by the **LangGraph Swarm** before committing them safely into your branch.")
st.divider()

# Sidebar setup
with st.sidebar:
    st.header("⚙️ Graph Configuration")
    st.markdown("Provide your project settings below to trigger the swarm:")
    
    project_key = st.text_input("SonarQube Project Key", value=config.SONAR_PROJECT_KEY, help="The exact project key from your Sonar dashboard.")
    branch = st.text_input("Target Branch", value="agent-sec-fixes", help="The git branch the agent will checkout and commit onto.")
    repo_url = st.text_input("Repo URL", value="local", help="Leave as 'local' to use the configured PROJECT_PATH.")
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🚀 Start Agent Workflow", type="primary", use_container_width=True):
        if st.session_state.workflow_state != "running":
            st.session_state.workflow_state = "running"
            st.rerun()

# ── Runner ──
if st.session_state.workflow_state == "running":
    st.info("🤖 Agent Swarm initialized. Evaluating repository anomalies...")
    
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
        with st.status("Swarm Execution Progress...", expanded=True) as status:
            progress_bar = st.progress(0)
            
            # Stream the graph execution to update the UI
            for s in graph.stream(initial_state, stream_mode="updates"):
                for node_name, node_state in s.items():
                    if node_name == "supervisor_init":
                        status.update(label="Scanning Baseline...", state="running")
                        st.write("✅ Baseline gathered. Launching workers...")
                        progress_bar.progress(25)
                    elif node_name == "worker_refactor":
                        status.update(label="Workers Refactoring Code...", state="running")
                        st.write("✅ All parallel workers successfully applied fixes.")
                        progress_bar.progress(50)
                    elif node_name == "evaluator_scan":
                        status.update(label="Critic Evaluation Scan...", state="running")
                        st.write("✅ Verification scan complete.")
                        progress_bar.progress(75)
                    elif node_name == "generate_report":
                        st.write("📄 Generating Contractor Report...")
                        progress_bar.progress(100)
                        st.session_state.final_report = node_state.get("final_report")
                        
            status.update(label="Agent Swarm finished evaluating!", state="complete", expanded=False)

                    
        st.session_state.workflow_state = "review"
        st.rerun()
        
    except Exception as e:
        st.error(f"Agent flow encountered an error: {str(e)}")
        st.session_state.workflow_state = "idle"


# ── Review (Human-In-The-Loop) ──
if st.session_state.workflow_state == "review" and st.session_state.final_report:
    report = st.session_state.final_report
    
    st.success("Graph execution paused. Please review the Contract below.")
    st.markdown("### 📊 Scan Results & Contract Negotiation")
    
    # Render High-level Metrics
    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Total Fixes Attempted", report.get("total_fixes_attempted", 0))
    with m2:
        st.metric("Successful Fixes", report.get("successful_fixes", 0), delta=f"+{report.get('successful_fixes', 0)} accepted", delta_color="normal")
    with m3:
        st.metric("Remaining Open Issues", report.get("remaining_issues", 0), delta="needs attention", delta_color="off")
        
    st.markdown("<br>", unsafe_allow_html=True)
    
    if "rejections" not in st.session_state:
        st.session_state.rejections = set()
    
    fixes = [f for f in report.get("fixes", []) if f.get("status") == "success"]
    
    if not fixes:
        st.info("No files required fixing, or the agent could not resolve the issues without violating constraints.")
        
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
