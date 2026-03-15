# 🏗️ Updated Architecture & Project Requirements

This document outlines the revised requirements and architectural design for the SonarQube Code Correction Agent, transitioning from a linear script to a robust, graph-based agentic workflow.

## Phase 1: The MCP Layer (Standardizing the Interface)
Following Chapter 4 (The Model Context Protocol), we will decouple the tool logic from the LLM by building two dedicated MCP Servers. The Agent will act as the MCP Host.

### 1. SonarQube MCP Server
Connects to external SonarQube instances (e.g., `https://sonarqube.ai-launch-pad.com/` or `localhost`) via REST API.

**Tools (Model-Controlled Actions):**
- `trigger_scan(project_key, branch)`: Initiates a SonarQube scan on the local workspace.
- `get_scan_status(task_id)`: Polls for completion.
- `get_issues(project_key, branch)`: Returns a structured JSON of issues (file path, line number, rule violated).
- `get_rule_details(rule_key)`: Fetches the specific SonarQube documentation on why the rule exists and how to fix it.

**Resources (Application-Controlled Context):**
- `sonar://{project_key}/baseline_report`: A read-only snapshot of the initial scan before any fixes are applied.

### 2. GitHub / Workspace MCP Server
Manages the local sandbox and remote repository.

**Tools:**
- `setup_workspace(repo_url, branch)`: Clones the repo and creates a working branch (e.g., `agent-sec-fixes`).
- `read_file(file_path)`: Reads vulnerable code.
- `write_file(file_path, content)`: Applies refactored code.
- `revert_file(file_path)`: Reverts a specific file to its baseline state (crucial for the HITL rejection flow).
- `commit_and_push(branch, message)`: Finalizes accepted changes.

---

## Phase 2: Agentic Architecture & Workflow
Following Chapter 2.2 (Core Agentic Workflows), we will use LangGraph to build a stateful, cyclic graph. We will avoid a monolithic single-agent setup to prevent Capability Saturation and Context Bloat.

**Architectural Pattern:** Hierarchical + Parallelized Evaluator-Optimizer.
- **Supervisor Agent:** Orchestrates the scans and delegates tasks.
- **Worker Agents (Producers):** Refactor specific files.
- **SonarQube (Critic):** Provides deterministic evaluation.

### The LangGraph State Machine (Workflow Steps):

1. **Initialization & Baseline Scan:**
   - Supervisor calls GitHub MCP to `setup_workspace`.
   - Supervisor calls SonarQube MCP to `trigger_scan` and `get_issues`.

2. **Decomposition & Parallelization:**
   - The Supervisor groups issues by file.
   - It fans out the tasks using `RunnableParallel`. If 5 files have issues, 5 independent Worker Agents are spun up concurrently to drastically reduce latency.

3. **The Refactoring Loop (Producer):**
   - Each Worker Agent uses `read_file` and `get_rule_details`.
   - **Prompting Strategy:** The LLM is given the exact SonarQube rule description and the code snippet, instructed to fix the issue *without* altering business logic.
   - Worker uses `write_file` to apply the fix.

4. **Reflection & Self-Correction:**
   - Once all parallel workers finish, the graph fans in.
   - Supervisor triggers a **Verification Scan** via SonarQube MCP.
   - **Evaluator-Optimizer Loop:** If the new scan shows the issue persists, or a new issue was introduced, the Supervisor routes the file back to a Worker Agent with the new error context. (Capped at `max_loops = 3` to prevent infinite loops).

5. **Report Generation:**
   - The Supervisor compiles a structured JSON report comparing the Baseline Scan to the Verification Scan, detailing Before/After code diffs for each issue.

---

## Phase 3: Human-in-the-Loop (The "Contractor" Paradigm)
Following Chapter 2.5.1 (The Contractor Paradigm), the agent does not autonomously push code to production. Instead, it submits a "Contract" (the generated report) to the human for negotiation and approval.

1. **Execution Pause:**
   - The LangGraph workflow reaches an `__end__` node for the autonomous phase and pauses, saving its state.
   - Using MCP's Elicitation primitive, the system surfaces the report to the UI.

2. **The HITL Interface (UI):**
   - The user sees a dashboard with a list of fixed issues.
   - For each issue, a split-screen diff is shown: Original Code vs. Agent Refactored Code.
   - The UI provides granular controls: `[Accept Fix]` or `[Reject Fix]`.

3. **Granular Resolution:**
   - When the user clicks Reject on a specific fix, the UI sends a command back to the Agent's state machine.
   - The Agent uses the GitHub MCP `revert_file(file_path)` tool to undo that specific change in the local workspace.

4. **Finalization:**
   - Once the human clicks "Finalize Contract", the Agent resumes.
   - It calls `commit_and_push` via the GitHub MCP for all accepted changes.
   - *(Optional)* It generates a Pull Request summarizing the accepted security fixes.

---

## Phase 4: Evaluation and Guardrails
Following Chapter 3.5 (Evaluating Agent Systems), we must ensure the agent doesn't break the repository.

1. **Guardrails (Deterministic):**
   - Before the Agent is allowed to commit, a guardrail script ensures that the project still compiles (e.g., running `npm run build` or `mvn clean compile` in the sandbox).
   - If it fails, the agent is forced to revert its last change.

2. **LLM-as-a-Judge (Probabilistic):**
   - To prevent the agent from deleting necessary business logic to satisfy a SonarQube rule, an independent "Judge LLM" evaluates the Before/After diff.
   - **Prompt:** *"You are a QA Auditor. Review this code change. Did the agent remove core business logic to satisfy the security rule? Output JSON: {logic_preserved: boolean, rationale: string}."*
   - If `logic_preserved` is false, the fix is automatically flagged for human review, even if SonarQube passes it.

---

## 🛠️ Summary of Tech Stack
- **Orchestration:** LangGraph (Python) for state management, parallelization, and HITL pausing.
- **LLM:** Claude 3.5 Sonnet (ideal for complex coding tasks and native MCP support).
- **Integration:** FastMCP (Python SDK) to build the GitHub and SonarQube servers.
- **Frontend:** Streamlit or Next.js to render the Elicitation UI and code diffs.
- **Environment:** Dockerized sandbox for the agent to safely clone code and run the Sonar Scanner CLI.
