"""
Issue analysis and fix generation.

For each SonarQube issue:
  1. Reads the source file.
  2. Looks up the rule for context.
  3. Applies rule-specific fixers (or falls through to a generic handler).
  4. Produces a unified diff and explanation.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional

from sonar_agent.core.models import SonarIssue, ProposedFix
from sonar_agent.clients.sonar_client import SonarClient
from sonar_agent.llm.llm_fixer import llm_fix
from sonar_agent.core import file_manager


# ═══════════════════════════════════════════════════════════════════════════
#  Rule-based fixers
#  Each fixer receives the full file content, the issue, and rule metadata,
#  and returns (fixed_content, explanation, confidence) or None to skip.
# ═══════════════════════════════════════════════════════════════════════════

FixerResult = Optional[tuple[str, str, str]]  # (fixed_content, explanation, confidence)


def _fix_unused_import(content: str, issue: SonarIssue, rule: dict) -> FixerResult:
    """Remove a single unused import line."""
    if issue.line is None:
        return None

    lines = content.splitlines(keepends=True)
    target_line_idx = issue.line - 1

    if target_line_idx < 0 or target_line_idx >= len(lines):
        return None

    removed_line = lines[target_line_idx].rstrip()
    del lines[target_line_idx]

    explanation = (
        f"Removed unused import on line {issue.line}: `{removed_line.strip()}`. "
        f"SonarQube rule {issue.rule} flags imports that are never referenced in the file. "
        "Removing them keeps the code clean and avoids unnecessary dependencies."
    )
    return "".join(lines), explanation, "high"


def _fix_trailing_whitespace(content: str, issue: SonarIssue, rule: dict) -> FixerResult:
    """Strip trailing whitespace from the flagged line."""
    if issue.line is None:
        return None

    lines = content.splitlines(keepends=True)
    idx = issue.line - 1
    if idx < 0 or idx >= len(lines):
        return None

    lines[idx] = lines[idx].rstrip() + "\n"
    explanation = (
        f"Stripped trailing whitespace on line {issue.line}. "
        f"Rule {issue.rule} enforces consistent line endings."
    )
    return "".join(lines), explanation, "high"


def _fix_empty_block(content: str, issue: SonarIssue, rule: dict) -> FixerResult:
    """Add a TODO/pass placeholder to an empty block."""
    if issue.line is None:
        return None

    lines = content.splitlines(keepends=True)
    idx = issue.line - 1
    if idx < 0 or idx >= len(lines):
        return None

    # Detect indentation of the block line
    leading = re.match(r"^(\s*)", lines[idx])
    indent = (leading.group(1) if leading else "") + "    "

    # Insert a `pass  # TODO` after the block header
    placeholder = f"{indent}pass  # TODO: implement this block\n"
    lines.insert(idx + 1, placeholder)

    explanation = (
        f"Inserted `pass` placeholder inside empty block at line {issue.line}. "
        f"Rule {issue.rule} flags empty code blocks as likely mistakes."
    )
    return "".join(lines), explanation, "medium"


# ── Fixer registry ──────────────────────────────────────────────────────
#  Maps SonarQube rule ID patterns to handler functions.
#  Use a prefix match so that e.g. "python:S1128" matches the entry "S1128".

FIXERS: dict[str, callable] = {
    # Python
    "S1128": _fix_unused_import,        # Unused imports
    "S1116": _fix_trailing_whitespace,  # Empty statements / trailing
    "S108":  _fix_empty_block,          # Empty blocks
    # Java
    "S1128": _fix_unused_import,
    "S108":  _fix_empty_block,
    # JavaScript / TypeScript
    "S1128": _fix_unused_import,
}


def _rule_id(rule: str) -> str:
    """Extract the bare rule ID, e.g. 'python:S1128' → 'S1128'."""
    return rule.split(":")[-1] if ":" in rule else rule


def _get_rule_description(rule_meta: dict) -> str:
    """Extract a human-readable rule description from SonarQube metadata."""
    # The API returns HTML in 'htmlDesc' or plain text in 'mdDesc'
    desc = rule_meta.get("mdDesc") or rule_meta.get("htmlDesc") or ""
    # Strip HTML tags for a cleaner prompt
    if "<" in desc:
        import re as _re
        desc = _re.sub(r"<[^>]+>", "", desc)
    return desc[:2000]  # Cap length for the LLM prompt


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

def generate_diff(original: str, fixed: str, file_path: str) -> str:
    """Produce a unified diff string between original and fixed content."""
    original_lines = original.splitlines(keepends=True)
    fixed_lines = fixed.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        fixed_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )
    return "".join(diff)


def analyse_and_fix(
    issue: SonarIssue,
    client: SonarClient,
) -> Optional[ProposedFix]:
    """
    Attempt to generate a fix for the given issue.

    Strategy:
      1. Try a specialised (rule-specific) fixer first.
      2. Fall back to LLM-powered generic fixer (Gemini).
      3. If both fail, return a "manual review needed" stub.
    """
    # 1. Read the source file
    try:
        original = file_manager.read_source_file(issue.file_path)
    except FileNotFoundError as exc:
        return ProposedFix(
            issue=issue,
            original_content="",
            fixed_content="",
            explanation=f"Could not read source file: {exc}",
            confidence="low",
            diff_text="",
        )

    # 2. Fetch rule metadata for context
    try:
        rule_meta = client.get_rule(issue.rule)
    except Exception:
        rule_meta = {}

    # 3. Try a specialised fixer
    rid = _rule_id(issue.rule)
    fixer = FIXERS.get(rid)
    if fixer:
        result = fixer(original, issue, rule_meta)
        if result:
            fixed_content, explanation, confidence = result
            diff_text = generate_diff(original, fixed_content, issue.file_path)
            return ProposedFix(
                issue=issue,
                original_content=original,
                fixed_content=fixed_content,
                explanation=explanation,
                confidence=confidence,
                diff_text=diff_text,
            )

    # 4. Fall back to LLM-powered fixer
    rule_description = _get_rule_description(rule_meta)
    
    try:
        llm_result = llm_fix(
            source_content=original,
            file_path=issue.file_path,
            rule=issue.rule,
            severity=issue.severity.name,
            issue_type=issue.issue_type.value,
            line=issue.line,
            message=issue.message,
            rule_description=rule_description,
        )

        if llm_result:
            fixed_content, explanation, confidence = llm_result
            diff_text = generate_diff(original, fixed_content, issue.file_path)
            if diff_text:  # LLM actually produced a change
                return ProposedFix(
                    issue=issue,
                    original_content=original,
                    fixed_content=fixed_content,
                    explanation=f"[LLM Fix] {explanation}",
                    confidence=confidence,
                    diff_text=diff_text,
                )
            else:
                failure_reason = "LLM returned the exact same code."
        else:
            failure_reason = "No available LLM providers."
            
    except Exception as exc:
        failure_reason = str(exc)

    # 5. Both specialised + LLM failed — manual review needed
    return ProposedFix(
        issue=issue,
        original_content=original,
        fixed_content="",
        explanation=(
            f"No automatic fixer for rule {issue.rule} ({rid}) and the LLM "
            f"could not generate a valid fix.\nReason: {failure_reason}\n\n"
            f"Issue message: {issue.message}"
        ),
        confidence="low",
        diff_text="",
    )
