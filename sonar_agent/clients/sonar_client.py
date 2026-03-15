"""
SonarQube REST API client.

Handles authentication, pagination, and issue fetching.
"""

from __future__ import annotations

from typing import Any

import requests

from sonar_agent.core import config
from sonar_agent.core.models import SonarIssue, Severity


class SonarClient:
    """Lightweight wrapper around the SonarQube Web API."""

    # Statuses we care about when looking for "open" issues
    OPEN_STATUSES = "OPEN,CONFIRMED,REOPENED"

    def __init__(
        self,
        host_url: str | None = None,
        token: str | None = None,
        project_key: str | None = None,
    ) -> None:
        self.host_url = (host_url or config.SONAR_HOST_URL).rstrip("/")
        self.token = token or config.SONAR_TOKEN
        self.project_key = project_key or config.SONAR_PROJECT_KEY
        self._session = requests.Session()
        self._session.auth = (self.token, "")  # Token-based auth (user=token, pass="")

    # ── health ───────────────────────────────────────────────────────────

    def ping(self) -> dict[str, Any]:
        """Return the SonarQube system status. Raises on connection failure."""
        resp = self._get("/api/system/status")
        return resp

    # ── issues ───────────────────────────────────────────────────────────

    def fetch_issues(
        self,
        statuses: str | None = None,
        severities: str | None = None,
        types: str | None = None,
        page_size: int = 100,
    ) -> list[SonarIssue]:
        """
        Fetch all matching issues for the configured project.

        Automatically paginates through all results.
        Returns a list of SonarIssue sorted by severity (BLOCKER first).
        """
        statuses = statuses or self.OPEN_STATUSES
        page = 1
        issues: list[SonarIssue] = []

        while True:
            params: dict[str, Any] = {
                "componentKeys": self.project_key,
                "statuses": statuses,
                "ps": page_size,
                "p": page,
            }
            if severities:
                params["severities"] = severities
            if types:
                params["types"] = types

            data = self._get("/api/issues/search", params=params)
            raw_issues = data.get("issues", [])
            if not raw_issues:
                break

            for raw in raw_issues:
                issues.append(SonarIssue.from_api(raw))

            total = data.get("total", 0)
            if page * page_size >= total:
                break
            page += 1

        # Sort: BLOCKER → INFO
        issues.sort(key=lambda i: i.severity.value)
        return issues

    def get_rule(self, rule_key: str) -> dict[str, Any]:
        """Fetch rule details (description, why it matters, etc.)."""
        data = self._get("/api/rules/show", params={"key": rule_key})
        return data.get("rule", {})

    def wait_for_analysis(self) -> None:
        """
        Poll the Compute Engine until the background analysis task completes.
        Prevents fetching stale issues right after a scan.
        """
        import time
        from rich.console import Console
        from rich.progress import Progress, SpinnerColumn, TextColumn

        console = Console()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("[yellow]Waiting for SonarQube to process scan results…[/yellow]", total=None)
            
            while True:
                try:
                    data = self._get("/api/ce/component", params={"component": self.project_key})
                    queue = data.get("queue", [])
                    current = data.get("current")
                    
                    if not queue and not current:
                        break
                except requests.exceptions.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code in (401, 403):
                        # Token lacks permission to view the Compute Engine queue
                        time.sleep(10)  # Safe static fallback wait
                        break
                    raise
                    
                time.sleep(1.0)

    # ── internals ────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request and return parsed JSON."""
        url = f"{self.host_url}{path}"
        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
