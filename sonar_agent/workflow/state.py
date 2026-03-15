import operator
from typing import Annotated, Any, Dict, List
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """The overall state of our LangGraph application."""
    project_key: str                     # SonarQube Project Key
    branch: str                          # Branch name for fixes
    repo_url: str                        # Original repo URL if setup from scratch
    issues: List[Dict[str, Any]]         # JSON dump of all issues detected
    files_to_fix: List[str]              # List of unique files that need refactoring
    fixes_applied: Annotated[List[Dict[str, Any]], operator.add]  # Accumulates individual worker results
    iteration: int                       # Current loop count of verification
    final_report: Dict[str, Any]         # The final output contract shown in the HITL UI


class WorkerState(TypedDict):
    """The scoped state passed down to a parallelized Worker Agent."""
    project_key: str
    file_path: str                       # The specific file being refactored
    issues_for_file: List[Dict[str, Any]]# The subset of rules violated in this file
    iteration: int                       # Current iteration pass
