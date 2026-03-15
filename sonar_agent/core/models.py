"""
Data models for SonarQube issues and proposed fixes.

Uses dataclasses to keep things simple and dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Enums ────────────────────────────────────────────────────────────────

class Severity(Enum):
    """SonarQube issue severity, ordered from most to least critical."""
    BLOCKER = 1
    CRITICAL = 2
    MAJOR = 3
    MINOR = 4
    INFO = 5

    @classmethod
    def from_str(cls, value: str) -> "Severity":
        try:
            return cls[value.upper()]
        except KeyError:
            return cls.INFO


class IssueType(Enum):
    """SonarQube issue type."""
    BUG = "BUG"
    VULNERABILITY = "VULNERABILITY"
    CODE_SMELL = "CODE_SMELL"
    SECURITY_HOTSPOT = "SECURITY_HOTSPOT"

    @classmethod
    def from_str(cls, value: str) -> "IssueType":
        try:
            return cls[value.upper()]
        except KeyError:
            return cls.CODE_SMELL


class ApprovalAction(Enum):
    """Possible user responses when asked to approve a fix."""
    YES = "yes"
    NO = "no"
    SKIP = "skip"
    MODIFY = "modify"
    STOP = "stop"


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class SonarIssue:
    """Represents a single SonarQube issue."""
    key: str
    severity: Severity
    issue_type: IssueType
    rule: str
    component: str          # Full SonarQube component path (project:path/to/file)
    file_path: str          # Relative file path within the project
    line: Optional[int]
    message: str
    status: str             # OPEN, CONFIRMED, REOPENED, etc.
    effort: Optional[str] = None   # e.g. "5min"
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "SonarIssue":
        """Build a SonarIssue from a SonarQube REST API response dict."""
        component = data.get("component", "")
        # Strip the project key prefix to get the relative path
        # e.g. "my-project:src/main.py" → "src/main.py"
        if ":" in component:
            file_path = component.split(":", 1)[1]
        else:
            file_path = component

        return cls(
            key=data.get("key", ""),
            severity=Severity.from_str(data.get("severity", "INFO")),
            issue_type=IssueType.from_str(data.get("type", "CODE_SMELL")),
            rule=data.get("rule", ""),
            component=component,
            file_path=file_path,
            line=data.get("line"),
            message=data.get("message", ""),
            status=data.get("status", "OPEN"),
            effort=data.get("effort"),
            tags=data.get("tags", []),
        )


@dataclass
class ProposedFix:
    """A proposed fix for a single SonarQube issue."""
    issue: SonarIssue
    original_content: str
    fixed_content: str
    explanation: str
    confidence: str         # "high", "medium", "low"
    diff_text: str          # Pre-computed unified diff string


@dataclass
class FixResult:
    """Tracks the outcome of processing a single issue."""
    issue: SonarIssue
    action: ApprovalAction  # What the user chose
    fix_applied: bool = False
    backup_path: Optional[str] = None
    error: Optional[str] = None
