import streamlit as st
import time
from pathlib import Path
import os
import difflib
import requests

from sonar_agent.core import config
from sonar_agent.workflow.graph import build_agent_graph
from mcp_servers.github_mcp import revert_file, commit_and_push

st.set_page_config(page_title="AutoPatch Agent UI", page_icon="✨", layout="wide")

st.markdown("""
<style>
    /* Center Main Title */
    .title-container {
        text-align: center;
        padding-top: 1rem;
        padding-bottom: 2rem;
    }
    .title-container h1 {
        font-size: 3rem;
        margin-bottom: 0px;
    }
    .title-container p {
        color: #888888;
        font-size: 1.1rem;
    }
    /* Tidy up Metric Cards */
    div[data-testid="metric-container"] {
        background-color: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 5% 5% 5% 10%;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    /* Rounded buttons */
    .stButton>button {
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

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


st.markdown("""
<div class="title-container">
    <h1>✨ AutoPatch: Code Correction Agent</h1>
    <p>This Elicitation UI reviews changes proposed by the <b>LangGraph Swarm</b> before committing them safely into your branch.</p>
</div>
""", unsafe_allow_html=True)
st.divider()

# Sidebar setup
with st.sidebar:
    st.header("⚙️ Graph Configuration")
    st.markdown("Provide your project settings below to trigger the swarm:")
    
    project_key = st.text_input("SonarQube Project Key", value=config.SONAR_PROJECT_KEY, help="The exact project key from your Sonar dashboard.")
    branch = st.text_input("Target Branch", value="agent-sec-fixes", help="The git branch the agent will checkout and commit onto.")
    
    st.markdown("---")
    st.subheader("Target Repository")
    github_user = st.text_input("GitHub Username", value="", placeholder="e.g. Aasrith-Mandava")
    
    repo_url = ""
    if github_user:
        # Fetch public repos for the given user
        try:
            response = requests.get(f"https://api.github.com/users/{github_user}/repos?sort=updated&per_page=100")
            if response.status_code == 200:
                repos = response.json()
                if repos:
                    repo_names = [repo["name"] for repo in repos]
                    # Create a selectbox with the repo names
                    selected_repo_name = st.selectbox("Select a Repository", repo_names)
                    
                    # Find the clone_url for the selected repo
                    selected_repo = next(r for r in repos if r["name"] == selected_repo_name)
                    repo_url = selected_repo["html_url"] + ".git"
                    
                    st.caption(f"Target URL: `{repo_url}`")
                else:
                    st.warning("No public repositories found for this user.")
            else:
                st.error("Could not fetch repositories (User not found or API limit reached).")
        except Exception as e:
            st.error(f"Error connecting to GitHub: {e}")
    else:
        st.info("Enter a GitHub Username to select a repository.")
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔍 Fetch Issues from Repo", type="primary", use_container_width=True):
        if st.session_state.workflow_state != "fetching_issues":
            st.session_state.workflow_state = "fetching_issues"
            st.rerun()

# ── Stage 1: Fetching Issues ──
if st.session_state.workflow_state == "fetching_issues":
    with st.spinner("Connecting to SonarQube & pulling latest repository anomalies..."):
        try:
            from mcp_servers.sonar_mcp import trigger_scan, get_scan_status, get_issues, get_rule_details
            from mcp_servers.github_mcp import setup_workspace
            import re
            
            setup_workspace(repo_url, branch)
            trigger_scan(project_key, branch)
            get_scan_status(project_key)
            
            issues = get_issues(project_key, branch)
            files_to_fix = list(set([issue.get("file_path") for issue in issues if issue.get("file_path")]))
            
            # Pre-fetch rule details for every unique rule (so we can explain each issue in depth)
            unique_rules = set(issue.get("rule", "") for issue in issues if issue.get("rule"))
            rule_cache = {}
            for rule_key in unique_rules:
                try:
                    details = get_rule_details(rule_key)
                    html_desc = details.get("htmlDesc", "")
                    if not html_desc:
                        sections = details.get("descriptionSections", [])
                        for pkey in ["root_cause", "introduction", "how_to_fix"]:
                            for s in sections:
                                if s.get("key") == pkey and s.get("content"):
                                    html_desc = s["content"]
                                    break
                            if html_desc:
                                break
                        if not html_desc:
                            for s in sections:
                                if s.get("content"):
                                    html_desc = s["content"]
                                    break
                    clean_desc = re.sub(r'<[^>]+>', '', html_desc).strip() if html_desc else "No description available."
                    rule_cache[rule_key] = {
                        "name": details.get("name", rule_key),
                        "description": clean_desc,
                        "severity": details.get("severity", "UNKNOWN"),
                        "type": details.get("type", "UNKNOWN"),
                    }
                except Exception:
                    rule_cache[rule_key] = {"name": rule_key, "description": "Rule documentation unavailable.", "severity": "UNKNOWN", "type": "UNKNOWN"}
            
            st.session_state.fetched_issues = issues
            st.session_state.fetched_files_to_fix = files_to_fix
            st.session_state.rule_cache = rule_cache
            st.session_state.workflow_state = "issues_fetched"
            st.rerun()
        except Exception as e:
            st.error(f"Error fetching issues: {str(e)}")
            if st.button("Reset Dashboard"):
                st.session_state.workflow_state = "idle"
                st.rerun()

# ── Stage 1.5: Issues Dashboard ──
if st.session_state.workflow_state == "issues_fetched":
    st.success("Target repository scanned successfully!")
    issues = st.session_state.fetched_issues
    rule_cache = st.session_state.get("rule_cache", {})
    st.markdown("### 📥 Repository Anomalies Detected")
    
    if not issues:
        st.info("🎉 No open issues found in this repository! Your code is perfectly clean.")
        if st.button("Return to Dashboard"):
            st.session_state.workflow_state = "idle"
            st.rerun()
    else:
        # Summary metrics
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Total Issues Found", len(issues))
        with m2:
            st.metric("Files Affected", len(st.session_state.fetched_files_to_fix))
        with m3:
            # Count by severity
            critical_count = sum(1 for i in issues if i.get('severity', '').upper() in ('CRITICAL', 'BLOCKER'))
            st.metric("Critical / Blocker", critical_count, delta="high priority" if critical_count > 0 else "none", delta_color="inverse" if critical_count > 0 else "off")
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        # Group issues by file
        from collections import defaultdict
        issues_by_file = defaultdict(list)
        for issue in issues:
            issues_by_file[issue.get('file_path', 'Unknown')].append(issue)
        
        for file_path, file_issues in issues_by_file.items():
            with st.expander(f"📄 `{file_path}` — {len(file_issues)} issue(s)", expanded=True):
                for issue in file_issues:
                    severity = issue.get('severity', 'UNKNOWN').upper()
                    severity_colors = {'CRITICAL': '🔴', 'BLOCKER': '🔴', 'MAJOR': '🟠', 'MINOR': '🟡', 'INFO': '🔵'}
                    icon = severity_colors.get(severity, '⚪')
                    
                    issue_type = issue.get('issue_type', 'UNKNOWN').replace('_', ' ').title()
                    type_badges = {'Bug': '🐛', 'Vulnerability': '🔓', 'Code Smell': '🧹', 'Security Hotspot': '🔥'}
                    type_icon = type_badges.get(issue_type, '📌')
                    
                    rule_key = issue.get('rule', 'N/A')
                    rule_info = rule_cache.get(rule_key, {})
                    rule_name = rule_info.get('name', rule_key)
                    rule_desc = rule_info.get('description', 'No description available.')
                    
                    # Issue Header
                    st.markdown(f"""
#### {icon} {rule_name}
**Line {issue.get('line', 'N/A')}** · `{rule_key}` · {type_icon} {issue_type} · **{severity}**
""")
                    # Issue Message (what SonarQube detected)
                    st.error(f"**Issue:** {issue.get('message', 'No message')}")
                    
                    # Rule Explanation (from SonarQube documentation)
                    # Truncate very long descriptions for readability
                    desc_text = rule_desc[:500] + "..." if len(rule_desc) > 500 else rule_desc
                    st.info(f"**Why is this a problem?**\n\n{desc_text}")
                    
                    # Effort estimate
                    effort = issue.get('effort', None)
                    if effort:
                        st.caption(f"⏱ Estimated effort to fix: **{effort}**")
                    
                    st.markdown("---")
            
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🛠 Auto-Fix These Issues", type="primary", use_container_width=True):
            st.session_state.workflow_state = "running"
            st.rerun()

# ── Stage 2: Runner ──
if st.session_state.workflow_state == "running":
    st.info("🤖 Agent Swarm initialized. Assigning Workers to auto-fix code...")
    
    graph = build_agent_graph()
    initial_state = {
        "project_key": project_key,
        "branch": branch,
        "repo_url": repo_url,
        "iteration": 1,
        "fixes_applied": [],
        "issues": st.session_state.get("fetched_issues", []),
        "files_to_fix": st.session_state.get("fetched_files_to_fix", [])
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
        with st.expander(f"📝 {file_path} {label_suffix}", expanded=(not is_rejected) or flagged):
            
            if flagged:
                st.warning(f"**⚠️ Judge Warning:** {fix.get('judge_rationale', 'Core business logic may have been altered.')}")
            
            # Render each individual fix detail
            fix_details = fix.get('fix_details', [])
            if fix_details:
                for i, fd in enumerate(fix_details):
                    severity_colors = {'Bug': '🔴', 'Vulnerability': '🔴', 'Code Smell': '🟡', 'Security Hotspot': '🟠'}
                    sev_icon = severity_colors.get(fd.get('severity', ''), '⚪')
                    
                    st.markdown(f"#### {sev_icon} Fix {i+1}: {fd.get('issue_title', 'Untitled')}")
                    st.caption(f"Rule: `{fd.get('rule_id', 'N/A')}` · Severity: **{fd.get('severity', 'N/A')}**")
                    
                    # Root Cause
                    st.markdown("**🔍 Root Cause**")
                    st.info(fd.get('root_cause', 'N/A'))
                    
                    # Before / After Code Snippets
                    col_before, col_after = st.columns(2)
                    with col_before:
                        st.markdown("**❌ Before (Problematic)**")
                        st.code(fd.get('original_snippet', ''), language='python')
                    with col_after:
                        st.markdown("**✅ After (Fixed)**")
                        st.code(fd.get('fixed_snippet', ''), language='python')
                    
                    # What Changed
                    st.markdown("**🔄 What Changed**")
                    st.markdown(fd.get('what_changed', 'N/A'))
                    
                    # Benefit
                    st.markdown("**📈 Benefit**")
                    st.success(fd.get('benefit', 'N/A'))
                    
                    if i < len(fix_details) - 1:
                        st.markdown("---")
            else:
                st.info("No detailed fix information available.")
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Full File Diff
            orig, updated = get_diff(file_path)
            diff = list(difflib.unified_diff(
                orig.splitlines(),
                updated.splitlines(),
                fromfile=f'Baseline ({file_path})',
                tofile=f'Refactored ({file_path})',
                lineterm=''
            ))
            diff_text = '\n'.join(diff)
            
            with st.expander("📄 View Full Unified Diff", expanded=False):
                st.caption("Lines starting with `-` were removed, lines with `+` were added by the Agent.")
                if diff_text:
                    st.code(diff_text, language="diff", line_numbers=True)
                else:
                    st.info("No content changes detected in the file.")
                
            if not is_rejected:
                if st.button(f"Reject Fix for {file_path}", key=f"reject_{file_path}"):
                    msg = revert_file(file_path)
                    st.toast(msg)
                    st.session_state.rejections.add(file_path)
                    st.rerun()
            else:
                st.error(f"This fix was rejected and reverted. The file is back to baseline.")
                
    st.divider()
    
    col3, col4 = st.columns(2)
    with col3:
         if st.button("Finalize Contract & Push", type="primary", use_container_width=True):
             with st.spinner("Committing approved fixes to the remote branch..."):
                 msg = commit_and_push(report["branch"], "chore(security): applied agent-negotiated fixes")
             
             # Save repo_url so we can generate PR link later
             report["repo_url"] = repo_url
             st.session_state.workflow_state = "finalized"
             st.rerun()
             
    with col4:
         if st.button("Abort Entire Contract", use_container_width=True):
             st.warning("Reverting everything...")
             for fix in fixes:
                 revert_file(fix["file_path"])
             st.session_state.workflow_state = "idle"
             st.rerun()
             
# ── Finalized Success View ──
if st.session_state.workflow_state == "finalized":
    report = st.session_state.final_report
    st.success(f"🎉 Contract finalized! Swarm pushed fixes to remote branch: `{report['branch']}`")
    
    st.balloons()
    st.markdown("### Next Steps")
    
    # Generate dynamic GitHub PR link (extracting username/repo from URL)
    repo_url = report.get("repo_url", "")
    if repo_url and "github.com" in repo_url:
        clean_url = repo_url.replace(".git", "")
        pr_link = f"{clean_url}/compare/main...{report['branch']}?expand=1"
        st.info(f"👉 **[Click here to instantly open a Pull Request]({pr_link})** in GitHub and merge these fixes into your main branch.")
    else:
        st.info(f"Navigate to your repository and open a Pull Request for branch `{report['branch']}`.")
        
    # Generate Project Report Markdown
    report_md = f"""# 🛡 AutoPatch Autonomous Correction Report

**Generated on:** {time.strftime('%Y-%m-%d %H:%M:%S')}
**Target Branch:** `{report['branch']}`
**Repository:** {repo_url or 'Local'}

## 📊 Summary Metrics
- **Anomalies Processed:** {report.get('total_fixes_attempted', 0)}
- **Surgical Refactors Applied:** {report.get('successful_fixes', 0)}
- **Remaining Constraints Flagged:** {report.get('remaining_issues', 0)}

## 🛠 Fix Manifest
"""
    for fix in report.get("fixes", []):
        if fix.get("status") == "success":
            flag = "⚠️ Flagged by Judge" if fix.get('flagged_by_judge') else "✅ Approved"
            report_md += f"### {fix['file_path']} ({flag})\n\n"
            
            for fd in fix.get('fix_details', []):
                report_md += f"#### {fd.get('issue_title', 'Untitled')} (`{fd.get('rule_id', 'N/A')}` · {fd.get('severity', 'N/A')})\n\n"
                report_md += f"**🔍 Root Cause:** {fd.get('root_cause', 'N/A')}\n\n"
                report_md += f"**❌ Before:**\n```\n{fd.get('original_snippet', '')}\n```\n\n"
                report_md += f"**✅ After:**\n```\n{fd.get('fixed_snippet', '')}\n```\n\n"
                report_md += f"**🔄 What Changed:** {fd.get('what_changed', 'N/A')}\n\n"
                report_md += f"**📈 Benefit:** {fd.get('benefit', 'N/A')}\n\n"
                report_md += "---\n\n"
            
            if fix.get('flagged_by_judge'):
                report_md += f"> **⚠️ Judge Note:** {fix.get('judge_rationale', 'N/A')}\n\n"
            
    st.markdown("<br>", unsafe_allow_html=True)
    st.download_button(
        label="📥 Download Structured Report (.md)",
        data=report_md,
        file_name=f"autopatch_security_report_{int(time.time())}.md",
        mime="text/markdown",
        use_container_width=True
    )
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("Start New Scan", use_container_width=True):
        st.session_state.workflow_state = "idle"
        st.rerun()
