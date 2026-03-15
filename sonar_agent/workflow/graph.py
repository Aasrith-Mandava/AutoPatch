from langgraph.graph import StateGraph, START, END
from langgraph.constants import Send

from sonar_agent.workflow.state import AgentState, WorkerState
from sonar_agent.workflow.nodes import supervisor_init, worker_refactor, evaluator_scan, generate_report


def route_from_init(state: AgentState):
    """Fans out to worker nodes if there are files to fix, else skip to report."""
    files = state.get("files_to_fix", [])
    if not files:
        return "generate_report"
        
    issues = state.get("issues", [])
    sends = []
    for file_path in files:
        # Get subset of issues matching this file
        file_issues = [i for i in issues if i.get("file_path") == file_path]
        sends.append(Send("worker_refactor", WorkerState(
            project_key=state["project_key"], 
            file_path=file_path, 
            issues_for_file=file_issues,
            iteration=state.get("iteration", 1)
        )))
    return sends


def route_from_evaluator(state: AgentState):
    """Loop back to workers if issues persist, up to max_loops = 3."""
    files = state.get("files_to_fix", [])
    iteration = state.get("iteration", 1)
    
    # Cap logic at 3 loops to avoid infinite loops, as per requirements
    if not files or iteration > 3:
        return "generate_report"
        
    issues = state.get("issues", [])
    sends = []
    for file_path in files:
        file_issues = [i for i in issues if i.get("file_path") == file_path]
        sends.append(Send("worker_refactor", WorkerState(
            project_key=state["project_key"], 
            file_path=file_path, 
            issues_for_file=file_issues,
            iteration=iteration
        )))
    return sends


def build_agent_graph():
    """Builds and compiles the Evaluator-Optimizer LangGraph workflow."""
    builder = StateGraph(AgentState)
    
    # 1. Add nodes
    builder.add_node("supervisor_init", supervisor_init)
    builder.add_node("worker_refactor", worker_refactor)
    builder.add_node("evaluator_scan", evaluator_scan)
    builder.add_node("generate_report", generate_report)
    
    # 2. Add edges
    builder.add_edge(START, "supervisor_init")
    
    # Send fan-out from supervisor to parallel workers
    builder.add_conditional_edges(
        "supervisor_init", 
        route_from_init, 
        ["worker_refactor", "generate_report"]
    )
    
    # The fan-in: all parallel workers will transition to evaluator_scan automatically
    builder.add_edge("worker_refactor", "evaluator_scan")
    
    # Self-correction loop: evaluates if fixed, re-routes back to workers if not
    builder.add_conditional_edges(
        "evaluator_scan",
        route_from_evaluator,
        ["worker_refactor", "generate_report"]
    )
    
    # 3. Finalize and pause setup for HITL
    builder.add_edge("generate_report", END)
    
    # Compile the graph
    # Checkpointer can be added here for HITL persistent pause
    return builder.compile()

