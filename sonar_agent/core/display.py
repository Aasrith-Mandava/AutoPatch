"""
Terminal display helpers using the Rich library.

Provides pretty-printed tables, diffs, and prompts for the interactive
human-in-the-loop workflow.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich import box

from sonar_agent.core.models import SonarIssue, ProposedFix, FixResult, ApprovalAction

console = Console()


# ═══════════════════════════════════════════════════════════════════════════
#  Banner
# ═══════════════════════════════════════════════════════════════════════════

BANNER = r"""
 ╔═══════════════════════════════════════════════════════════════╗
 ║      🔍  SonarQube Code Correction Agent  🔍                ║
 ║                                                               ║
 ║   Fetch · Analyse · Fix · Review · Apply                      ║
 ╚═══════════════════════════════════════════════════════════════╝
"""


def print_banner() -> None:
    console.print(BANNER, style="bold cyan")


# ═══════════════════════════════════════════════════════════════════════════
#  Step 1 / Connection
# ═══════════════════════════════════════════════════════════════════════════

def print_connection_success(status: dict) -> None:
    console.print(
        Panel(
            f"[green]✔ Connected to SonarQube[/green]\n"
            f"  Version: {status.get('version', '?')}\n"
            f"  Status:  {status.get('status', '?')}",
            title="[bold]Step 1 — Connection[/bold]",
            border_style="green",
        )
    )


def print_connection_failure(error: str) -> None:
    console.print(
        Panel(
            f"[red]✘ Connection failed[/red]\n  {error}",
            title="[bold]Step 1 — Connection[/bold]",
            border_style="red",
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Step 2 / Issue summary table
# ═══════════════════════════════════════════════════════════════════════════

_SEVERITY_COLORS = {
    "BLOCKER":  "bold red",
    "CRITICAL": "red",
    "MAJOR":    "yellow",
    "MINOR":    "cyan",
    "INFO":     "dim",
}


def print_issues_table(issues: list[SonarIssue]) -> None:
    table = Table(
        title="Open Issues",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold magenta",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Key", style="dim", max_width=28)
    table.add_column("Severity", width=10)
    table.add_column("Type", width=16)
    table.add_column("File", style="cyan", max_width=40)
    table.add_column("Line", justify="right", width=5)
    table.add_column("Rule", style="dim", max_width=20)
    table.add_column("Message", max_width=50)

    for idx, issue in enumerate(issues, 1):
        sev = issue.severity.name
        sev_style = _SEVERITY_COLORS.get(sev, "")
        table.add_row(
            str(idx),
            issue.key,
            Text(sev, style=sev_style),
            issue.issue_type.value,
            issue.file_path,
            str(issue.line or "—"),
            issue.rule,
            issue.message,
        )

    console.print()
    console.print(table)
    console.print(f"\n  [bold]{len(issues)}[/bold] issue(s) found.\n")


# ═══════════════════════════════════════════════════════════════════════════
#  Step 3 / Issue detail + diff
# ═══════════════════════════════════════════════════════════════════════════

def print_issue_header(issue_num: int, total: int, issue: SonarIssue) -> None:
    sev = issue.severity.name
    sev_style = _SEVERITY_COLORS.get(sev, "")

    console.print()
    console.rule(f"ISSUE #{issue_num} of {total}", style="bold white")
    console.print(f"  Key:       {issue.key}")
    console.print(f"  Severity:  [{sev_style}]{sev}[/{sev_style}]")
    console.print(f"  Type:      {issue.issue_type.value}")
    console.print(f"  Rule:      {issue.rule}")
    console.print(f"  File:      [cyan]{issue.file_path}[/cyan]")
    console.print(f"  Line:      {issue.line or '—'}")
    console.print(f"  Message:   {issue.message}")


def print_proposed_fix(fix: ProposedFix) -> None:
    # Explanation
    console.print()
    console.print(
        Panel(fix.explanation, title="[bold]Explanation[/bold]", border_style="blue")
    )

    # Diff
    if fix.diff_text:
        console.print()
        console.print(
            Syntax(fix.diff_text, "diff", theme="monokai", line_numbers=False)
        )
    else:
        console.print("\n  [dim]No diff available (manual review needed).[/dim]")

    # Confidence
    conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(
        fix.confidence, "white"
    )
    console.print(
        f"\n  Confidence: [{conf_color}]{fix.confidence.upper()}[/{conf_color}]"
    )


def ask_approval() -> ApprovalAction:
    """Prompt the user and return their chosen action."""
    console.print()
    resp = console.input(
        "[bold]Approve this fix? ([green]yes[/green] / [red]no[/red] / "
        "[yellow]skip[/yellow] / [blue]modify[/blue] / [magenta]stop[/magenta]): [/bold]"
    ).strip().lower()

    try:
        return ApprovalAction(resp)
    except ValueError:
        console.print(f"  [dim]Unknown response '{resp}', treating as 'skip'.[/dim]")
        return ApprovalAction.SKIP


def ask_modification() -> str:
    """Ask the user what they want changed about the proposed fix."""
    console.print()
    return console.input("[bold blue]What should be changed? → [/bold blue]").strip()


# ═══════════════════════════════════════════════════════════════════════════
#  Step 5 / Fix applied receipt
# ═══════════════════════════════════════════════════════════════════════════

def print_fix_applied(relative_path: str, backup_path: str, diff_text: str) -> None:
    console.print()
    console.print(
        Panel(
            f"[green]✔ Fix applied[/green]\n"
            f"  File:   {relative_path}\n"
            f"  Backup: {backup_path}",
            title="[bold]Change Receipt[/bold]",
            border_style="green",
        )
    )
    if diff_text:
        console.print(
            Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Step 6 / Final summary
# ═══════════════════════════════════════════════════════════════════════════

def print_summary(
    total_issues: int,
    results: list[FixResult],
    project_key: str,
) -> None:
    proposed = len(results)
    applied = sum(1 for r in results if r.fix_applied)
    rejected = sum(1 for r in results if r.action == ApprovalAction.NO)
    skipped = sum(1 for r in results if r.action == ApprovalAction.SKIP)

    console.print()
    console.rule("Final Summary", style="bold magenta")
    console.print(f"  Total issues found:       {total_issues}")
    console.print(f"  Total fixes proposed:     {proposed}")
    console.print(f"  Total fixes applied:      [green]{applied}[/green]")
    console.print(f"  Total fixes rejected:     [red]{rejected}[/red]")
    console.print(f"  Total fixes skipped:      [yellow]{skipped}[/yellow]")

    if applied > 0:
        console.print("\n  [bold]Files modified:[/bold]")
        for r in results:
            if r.fix_applied:
                console.print(f"    • [cyan]{r.issue.file_path}[/cyan]  — {r.issue.message}")

    console.print()
    console.print(
        Panel(
            f"[bold]Re-scan command:[/bold]\n\n"
            f"  sonar-scanner \\\n"
            f"    -Dsonar.projectKey={project_key} \\\n"
            f"    -Dsonar.sources=. \\\n"
            f"    -Dsonar.host.url=http://localhost:9000 \\\n"
            f"    -Dsonar.token=<YOUR_TOKEN>",
            title="[bold]Verify Fixes[/bold]",
            border_style="cyan",
        )
    )
    console.print()
