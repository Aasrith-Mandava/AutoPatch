"""
AutoPatch CLI — Headless entry point for CI/CD pipelines (GitHub Actions).

This module allows the agent to run without the React UI or FastAPI server.
It's designed to be invoked by GitHub Actions as:

    python -m sonar_agent.cli --project-key my-project --max-iterations 3

Exit codes:
    0 = Success (fixes applied or no issues found)
    1 = Agent encountered a fatal error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="autopatch",
        description="AutoPatch — Autonomous Code Quality Agent (CLI Mode)",
    )
    parser.add_argument(
        "--project-key",
        required=True,
        help="SonarQube project key",
    )
    parser.add_argument(
        "--branch",
        default=os.getenv("GITHUB_REF_NAME", "main"),
        help="Branch to scan/fix (default: current branch or 'main')",
    )
    parser.add_argument(
        "--repo-url",
        default="",
        help="GitHub repository URL (auto-detected in Actions)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Max fix-verify iterations (default: 3)",
    )
    parser.add_argument(
        "--output-report",
        default="autopatch-report.json",
        help="Path to write the JSON report (default: autopatch-report.json)",
    )
    parser.add_argument(
        "--output-markdown",
        default="autopatch-report.md",
        help="Path to write the markdown PR body (default: autopatch-report.md)",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan for issues, don't fix them",
    )
    parser.add_argument(
        "--skip-verification-scan",
        action="store_true",
        help="Skip the post-fix verification scan (faster, less accurate)",
    )

    args = parser.parse_args()

    # ── Auto-detect repo URL in GitHub Actions ───────────────────────────
    repo_url = args.repo_url
    if not repo_url:
        github_repo = os.getenv("GITHUB_REPOSITORY", "")
        if github_repo:
            repo_url = f"https://github.com/{github_repo}.git"
            print(f"[CLI] Auto-detected repo URL: {repo_url}")

    # ── Override config for CI ───────────────────────────────────────────
    # These env vars are set by the GitHub Actions workflow
    if os.getenv("SONAR_PROJECT_KEY"):
        os.environ.setdefault("SONAR_PROJECT_KEY", args.project_key)

    print("=" * 60)
    print("⚡ AutoPatch CLI — Autonomous Code Quality Agent")
    print("=" * 60)
    print(f"  Project Key:     {args.project_key}")
    print(f"  Branch:          {args.branch}")
    print(f"  Max Iterations:  {args.max_iterations}")
    print(f"  Repo URL:        {repo_url or '(not set)'}")
    print(f"  Scan Only:       {args.scan_only}")
    print(f"  CI Mode:         {bool(os.getenv('CI'))}")
    print("=" * 60)
    print()

    start_time = time.time()

    try:
        # Import after arg parsing so --help doesn't require all deps
        from sonar_agent.workflow.graph import build_agent_graph, build_fast_fix_graph
        from sonar_agent.report.markdown_reporter import (
            generate_markdown_report,
            generate_github_actions_summary,
        )

        # Choose graph based on options
        if args.skip_verification_scan:
            print("[CLI] Using fast-fix graph (no verification scan)")
            graph = build_fast_fix_graph()
        else:
            print("[CLI] Using full agent graph (with verification scan)")
            graph = build_agent_graph()

        initial_state = {
            "project_key": args.project_key,
            "branch": args.branch,
            "repo_url": repo_url,
            "iteration": 1,
            "fixes_applied": [],
            "issues": [],
            "files_to_fix": [],
        }

        # ── Run the agent ────────────────────────────────────────────────
        print("\n🔍 Phase 1: Scanning for issues...")
        result = graph.invoke(initial_state)

        report = result.get("final_report", {})
        fixes = report.get("fixes", [])
        successful = [f for f in fixes if f.get("status") == "success"]
        errored = [f for f in fixes if f.get("status") == "error"]
        issues_found = len(report.get("original_issues_fetched", []))

        elapsed = time.time() - start_time

        # ── Output Results ───────────────────────────────────────────────
        print()
        print("=" * 60)
        print("📊 AutoPatch Results")
        print("=" * 60)
        print(f"  Issues Found:     {issues_found}")
        print(f"  Fixes Applied:    {len(successful)}")
        print(f"  Fixes Failed:     {len(errored)}")
        print(f"  Remaining Issues: {report.get('remaining_issues', 0)}")
        print(f"  Time Elapsed:     {elapsed:.1f}s")
        print("=" * 60)

        # ── Write JSON report ────────────────────────────────────────────
        report_path = Path(args.output_report)
        report_path.write_text(json.dumps(report, indent=2, default=str))
        print(f"\n📄 JSON report written to: {report_path}")

        # ── Write markdown report ────────────────────────────────────────
        md_path = Path(args.output_markdown)
        md_content = generate_markdown_report(report, branch=args.branch)
        md_path.write_text(md_content)
        print(f"📝 Markdown report written to: {md_path}")

        # ── Set GitHub Actions outputs ───────────────────────────────────
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write(f"fixes_applied={len(successful)}\n")
                f.write(f"issues_found={issues_found}\n")
                f.write(f"remaining_issues={report.get('remaining_issues', 0)}\n")
                f.write(f"has_fixes={'true' if successful else 'false'}\n")
            print("\n✅ GitHub Actions outputs set.")

        # ── Write GitHub Actions step summary ────────────────────────────
        github_summary = os.getenv("GITHUB_STEP_SUMMARY")
        if github_summary:
            summary = generate_github_actions_summary(report)
            with open(github_summary, "a") as f:
                f.write(summary)
            print("📋 GitHub Actions step summary written.")

        # ── Final status ─────────────────────────────────────────────────
        if successful:
            print(f"\n✅ AutoPatch completed successfully: {len(successful)} fix(es) applied.")
        elif issues_found == 0:
            print("\n✅ No issues found — codebase is clean!")
        else:
            print(f"\n⚠️  AutoPatch found {issues_found} issues but could not fix any.")

        sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n⛔ Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()

        # Still try to set outputs so the workflow can handle failures gracefully
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write("fixes_applied=0\n")
                f.write("has_fixes=false\n")
                f.write(f"error={str(e)[:200]}\n")

        sys.exit(1)


if __name__ == "__main__":
    main()
