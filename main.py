#!/usr/bin/env python3
"""
SonarQube Code Correction Agent — Main Orchestrator

Connects to SonarQube, fetches open issues, proposes minimal fixes,
and applies them one at a time after human approval.

Usage:
    python main.py              # Run the full interactive workflow
    python main.py --scan       # Run sonar-scanner first, then process issues
    python main.py --dry-run    # Show issues and proposed fixes without applying
"""

from __future__ import annotations

import argparse
import sys

from sonar_agent.core import config
from sonar_agent.clients.sonar_client import SonarClient
from sonar_agent.clients.sonar_mcp_client import MCPClient
from sonar_agent.core.models import ApprovalAction, FixResult
from sonar_agent.core.issue_processor import analyse_and_fix
from sonar_agent.llm.llm_fixer import get_chain
from sonar_agent.core import file_manager
from sonar_agent.core import display


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SonarQube Code Correction Agent",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Run sonar-scanner before fetching issues",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed fixes without applying them",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Attempt to use the SonarQube MCP server instead of REST API",
    )
    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════════════
#  Step 1 — Connect
# ═══════════════════════════════════════════════════════════════════════════

def step_connect(use_mcp: bool) -> SonarClient:
    """Connect to SonarQube and return a ready-to-use client."""
    # Optionally try MCP first
    if use_mcp:
        mcp = MCPClient()
        if mcp.connect():
            tools = mcp.list_tools()
            display.console.print(
                f"  [green]MCP connected — {len(tools)} tool(s) available.[/green]"
            )
            for t in tools:
                display.console.print(f"    • {t.get('name', '?')}")
            mcp.disconnect()  # We still use REST for the main workflow
        else:
            display.console.print(
                "  [yellow]MCP unavailable, using REST API.[/yellow]"
            )

    # Always create a REST client (primary transport)
    client = SonarClient()
    try:
        status = client.ping()
        display.print_connection_success(status)
    except Exception as exc:
        display.print_connection_failure(str(exc))
        sys.exit(1)

    # Show LLM provider status
    chain = get_chain()
    if chain.available_providers:
        display.console.print()
        chain.print_status()
    else:
        display.console.print(
            "\n  [red]⚠ No LLM providers configured! "
            "Only rule-specific fixers will work.[/red]\n"
        )

    return client


# ═══════════════════════════════════════════════════════════════════════════
#  Step 2 — Fetch issues
# ═══════════════════════════════════════════════════════════════════════════

def step_fetch_issues(client: SonarClient):
    """Fetch and display all open issues."""
    display.console.print("\n[bold]Step 2 — Fetching open issues…[/bold]\n")
    issues = client.fetch_issues()

    if not issues:
        display.console.print("  [green]🎉 No open issues found! Your code is clean.[/green]")
        sys.exit(0)

    display.print_issues_table(issues)
    return issues


# ═══════════════════════════════════════════════════════════════════════════
#  Steps 3-5 — Process each issue
# ═══════════════════════════════════════════════════════════════════════════

def step_process_issues(client: SonarClient, issues, dry_run: bool) -> list[FixResult]:
    """Iterate through issues one by one, propose fixes, wait for approval."""
    results: list[FixResult] = []
    total = len(issues)

    for idx, issue in enumerate(issues, 1):
        # Step 3 — analyse and propose a fix
        display.print_issue_header(idx, total, issue)

        fix = analyse_and_fix(issue, client)
        if fix is None:
            display.console.print("  [dim]Could not generate a fix. Skipping.[/dim]")
            results.append(FixResult(issue=issue, action=ApprovalAction.SKIP))
            continue

        display.print_proposed_fix(fix)

        # Dry-run mode: show but never apply
        if dry_run:
            display.console.print("  [dim](dry-run — not applying)[/dim]")
            results.append(FixResult(issue=issue, action=ApprovalAction.SKIP))
            continue

        # No diff means we couldn't auto-fix
        if not fix.diff_text:
            display.console.print(
                "  [yellow]⚠ No automatic fix available. Skip or stop.[/yellow]"
            )
            action = display.ask_approval()
            results.append(FixResult(issue=issue, action=action))
            if action == ApprovalAction.STOP:
                break
            continue

        # Step 4 — wait for approval
        while True:
            action = display.ask_approval()

            if action == ApprovalAction.YES:
                # Step 5 — apply the fix
                try:
                    backup = file_manager.create_backup(issue.file_path)
                    file_manager.write_fixed_file(issue.file_path, fix.fixed_content)
                    display.print_fix_applied(
                        issue.file_path, str(backup), fix.diff_text
                    )
                    results.append(
                        FixResult(
                            issue=issue,
                            action=action,
                            fix_applied=True,
                            backup_path=str(backup),
                        )
                    )
                except Exception as exc:
                    display.console.print(f"  [red]✘ Error applying fix: {exc}[/red]")
                    results.append(
                        FixResult(issue=issue, action=action, error=str(exc))
                    )
                break

            elif action == ApprovalAction.NO:
                display.console.print("  [red]Fix rejected.[/red]")
                results.append(FixResult(issue=issue, action=action))
                break

            elif action == ApprovalAction.SKIP:
                display.console.print("  [yellow]Issue skipped.[/yellow]")
                results.append(FixResult(issue=issue, action=action))
                break

            elif action == ApprovalAction.MODIFY:
                feedback = display.ask_modification()
                display.console.print(
                    f"  [blue]Modification requested:[/blue] {feedback}"
                )
                display.console.print(
                    "  [dim]Re-generation with user feedback is not yet "
                    "implemented. Skipping for now.[/dim]"
                )
                # TODO: Re-run the fixer with the user's feedback
                results.append(FixResult(issue=issue, action=ApprovalAction.SKIP))
                break

            elif action == ApprovalAction.STOP:
                results.append(FixResult(issue=issue, action=action))
                return results  # Early exit

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()

    # Validate config before doing anything
    config.validate()

    display.print_banner()

    # Step 1
    client = step_connect(use_mcp=args.mcp)

    # Optional: run sonar-scanner first
    if args.scan:
        import subprocess
        display.console.print("\n[bold]Running sonar-scanner…[/bold]\n")
        result = subprocess.run(
            [
                "sonar-scanner",
                f"-Dsonar.projectKey={config.SONAR_PROJECT_KEY}",
                f"-Dsonar.sources=.",
                f"-Dsonar.host.url={config.SONAR_HOST_URL}",
                f"-Dsonar.token={config.SONAR_TOKEN}",
            ],
            cwd=config.PROJECT_PATH,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            err_msg = result.stderr.strip() or result.stdout.strip()
            
            # Catch macOS Desktop permission error specifically
            if "Operation not permitted" in err_msg and "Desktop" in err_msg:
                display.console.print(
                    "\n[red]🚨 macOS Security Block Detected![/red]\n"
                    "macOS is blocking VS Code from reading your Desktop folder.\n\n"
                    "[bold]How to fix this in 10 seconds:[/bold]\n"
                    "1. Open [bold]System Settings[/bold] on your Mac.\n"
                    "2. Go to [bold]Privacy & Security[/bold] -> [bold]Files and Folders[/bold].\n"
                    "3. Find [bold]Visual Studio Code[/bold] in the list.\n"
                    "4. Turn the toggle [bold]ON[/bold] for the [bold]Desktop Folder[/bold].\n"
                    "5. [bold]Completely close and restart VS Code[/bold] (Cmd+Q) for the change to take effect.\n\n"
                    "[dim](Alternatively, you can move your project folders out of the Desktop to a folder like ~/Projects/)[/dim]"
                )
            else:
                display.console.print(f"[red]Scanner failed (Code {result.returncode}):\n{err_msg}[/red]")
            sys.exit(1)
        display.console.print("[green]✔ Scan complete.[/green]\n")
        
        # Must wait for SonarQube's background engine to digest the new report
        display.console.print("[dim]Waiting for SonarQube backend to digest new report…[/dim]")
        client.wait_for_analysis()

    # Step 2
    issues = step_fetch_issues(client)

    # Steps 3-5
    results = step_process_issues(client, issues, dry_run=args.dry_run)

    # Step 6 — summary
    display.print_summary(
        total_issues=len(issues),
        results=results,
        project_key=config.SONAR_PROJECT_KEY,
    )


if __name__ == "__main__":
    main()
