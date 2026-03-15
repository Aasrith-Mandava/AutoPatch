"""
LLM-powered generic fixer for SonarQube issues.

Uses the fallback chain (Gemini → OpenAI → Anthropic → Mistral) to
analyse any SonarQube rule violation and generate a minimal, surgical fix.
Acts as a fallback when no rule-specific handler exists in the FIXERS registry.
"""

from __future__ import annotations

import re
from typing import Optional

from sonar_agent.llm.llm_chain import LLMFallbackChain


# ── Prompt templates ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a senior code-quality engineer. Your ONLY job is to fix a single
SonarQube issue in the provided source code snippet.

ABSOLUTE RULES — violating any of these is a critical failure:
1. Fix ONLY the specific issue described. Do NOT touch anything else.
2. Do NOT refactor, rename, reorganise, reformat, or "improve" code.
3. Preserve the EXACT original style: indentation, quotes, semicolons,
   spacing, naming conventions, blank lines, comments.
4. Make the SMALLEST possible change that resolves the issue.
5. Return ONLY the complete fixed code snippet — no explanations, no
   markdown fences, no commentary before or after the code. It must be a drop-in replacement.
6. If the file uses tabs, keep tabs. If it uses 2-space indent, keep it.
7. Do NOT add or remove blank lines unless the fix requires it.
8. Do NOT change import order unless the fix specifically requires it.
"""

FIX_PROMPT = """\
## SonarQube Issue

- **Rule:** {rule}
- **Severity:** {severity}
- **Type:** {issue_type}
- **File:** {file_path}
- **Line:** {line}
- **Message:** {message}

## Rule Description

{rule_description}

## Source Code Snippet

```
{snippet_content}
```

## Instructions

Fix ONLY the issue described above on line {line}. Return the complete
fixed code snippet with no markdown fencing, no explanation — just the
raw source code. Do NOT return the entire file, only the fixed version of the snippet provided.
"""

EXPLAIN_PROMPT = """\
In 2–3 concise sentences, explain what you changed and why it resolves
SonarQube rule {rule} ("{message}"). Be specific about the code change.

Original line {line}:
{original_line}

Fixed content around that line:
{fixed_context}
"""


# ── Singleton chain instance ────────────────────────────────────────────
# Shared across all calls so that exhaustion state persists within a run.

_chain: LLMFallbackChain | None = None


def get_chain() -> LLMFallbackChain:
    """Return the shared chain instance, creating it on first call."""
    global _chain
    if _chain is None:
        _chain = LLMFallbackChain()
    return _chain


# ── Helpers ──────────────────────────────────────────────────────────────

def _extract_code(response_text: str) -> str:
    """
    Strip markdown code fences if the LLM wrapped its output despite
    instructions. Returns the raw code content.
    """
    text = response_text.strip()

    # Search for code block anywhere in the text
    pattern = r"```[\w]*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        # Return the longest code block found (assuming it's the full file)
        longest_match = max(matches, key=len)
        return longest_match.strip() + "\n"

    return text + "\n" if not text.endswith("\n") else text


def _assess_confidence(original: str, fixed: str) -> str:
    """
    Heuristic confidence rating based on how much the LLM changed.
    Fewer changed lines → higher confidence.
    """
    orig_lines = original.splitlines()
    fixed_lines = fixed.splitlines()

    if orig_lines == fixed_lines:
        return "low"  # Nothing changed — LLM didn't fix it

    # Count differing lines
    diff_count = abs(len(orig_lines) - len(fixed_lines))
    for i in range(min(len(orig_lines), len(fixed_lines))):
        if orig_lines[i] != fixed_lines[i]:
            diff_count += 1

    if diff_count <= 2:
        return "high"
    elif diff_count <= 6:
        return "medium"
    else:
        return "low"


def _get_context(content: str, center_line: int, radius: int = 3) -> str:
    """Extract a few lines of context around a line number."""
    lines = content.splitlines()
    start = max(0, center_line - 1 - radius)
    end = min(len(lines), center_line + radius)
    return "\n".join(
        f"  {i + 1}: {lines[i]}" for i in range(start, end)
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

def llm_fix(
    source_content: str,
    file_path: str,
    rule: str,
    severity: str,
    issue_type: str,
    line: int | None,
    message: str,
    rule_description: str = "",
) -> Optional[tuple[str, str, str]]:
    """
    Use the LLM fallback chain to generate a fix for a SonarQube issue.

    Tries providers in order: Gemini → OpenAI → Anthropic → Mistral.
    Automatically falls back when a provider's quota is exhausted.

    Returns:
        (fixed_content, explanation, confidence) on success, or None on failure.
    """
    chain = get_chain()

    if not chain.available_providers:
        return None

    try:
        # ── Step 0: Extract the context window ───────────────────────────
        # We only send +/- 25 lines to save massive amounts of tokens
        WINDOW_SIZE = 25
        orig_lines = source_content.splitlines()
        
        # Determine the boundaries
        target_idx = (line - 1) if line and line > 0 else 0
        target_idx = max(0, min(target_idx, len(orig_lines) - 1))
        
        start_idx = max(0, target_idx - WINDOW_SIZE)
        end_idx = min(len(orig_lines) - 1, target_idx + WINDOW_SIZE)
        
        snippet_lines = orig_lines[start_idx:end_idx + 1]
        snippet_content = "\n".join(snippet_lines)

        # ── Step 1: Generate the fix ─────────────────────────────────────
        fix_prompt = FIX_PROMPT.format(
            rule=rule,
            severity=severity,
            issue_type=issue_type,
            file_path=file_path,
            line=line or "unknown",
            message=message,
            rule_description=rule_description or "No description available.",
            snippet_content=snippet_content,
        )

        fix_response = chain.generate(SYSTEM_PROMPT, fix_prompt)

        if not fix_response:
            return None

        fixed_snippet = _extract_code(fix_response)

        # Sanity check: reject wildly divergent output for the snippet
        fixed_snippet_lines = fixed_snippet.splitlines()
        
        if abs(len(snippet_lines) - len(fixed_snippet_lines)) > max(10, len(snippet_lines) * 0.5):
            raise Exception(f"LLM snippet output changed line count drastically (from {len(snippet_lines)} to {len(fixed_snippet_lines)})")

        if fixed_snippet.strip() == snippet_content.strip():
            raise Exception("LLM returned the exact same code (it didn't know how to fix it)")
            
        # Stitch it back into the full original file
        fixed_full_lines = orig_lines[:start_idx] + fixed_snippet_lines + orig_lines[end_idx + 1:]
        fixed_content = "\n".join(fixed_full_lines) + "\n"

        # ── Step 2: Set the explanation statically to save API tokens and rate limits ─────
        provider_name = chain.last_used or "LLM"
        model_name = chain.last_model or ""
        tag = f"{provider_name}/{model_name}" if model_name else provider_name

        explanation = f"[{tag}] Automatically generated fix by Sonar Agent based on rule {rule}."
        confidence = _assess_confidence(source_content, fixed_content)

        return fixed_content, explanation, confidence

    except Exception as exc:
        raise exc
