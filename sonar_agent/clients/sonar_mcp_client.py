"""
MCP (Model Context Protocol) client for SonarQube.

Provides an alternative transport to connect to a SonarQube MCP server
via stdio. Falls back gracefully to the REST API client if the MCP
server is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Optional

from rich.console import Console

console = Console()


class MCPClient:
    """
    Connects to a SonarQube MCP server via stdio transport.

    The MCP server is expected to be started as a subprocess that
    communicates over stdin/stdout using JSON-RPC 2.0 messages.
    """

    def __init__(self, command: list[str] | None = None) -> None:
        """
        Args:
            command: The shell command to start the MCP server process.
                     e.g. ["npx", "-y", "@sonarsource/sonarqube-mcp-server"]
                     If None, a default command is used.
        """
        self.command = command or [
            "npx", "-y", "@sonarsource/sonarqube-mcp-server",
        ]
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0

    # ── lifecycle ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Start the MCP server subprocess and perform the initialize handshake.

        Returns True on success, False on failure.
        """
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Send JSON-RPC initialize request
            response = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "sonar-correction-agent",
                    "version": "1.0.0",
                },
            })
            if response and "result" in response:
                # Send initialized notification
                self._send_notification("notifications/initialized", {})
                return True
            return False
        except FileNotFoundError:
            console.print(
                "[yellow]⚠ MCP server command not found. "
                "Falling back to REST API.[/yellow]"
            )
            return False
        except Exception as exc:
            console.print(f"[yellow]⚠ MCP connection failed: {exc}[/yellow]")
            return False

    def disconnect(self) -> None:
        """Shut down the MCP server subprocess."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    # ── tool discovery ───────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """List all tools available on the MCP server."""
        response = self._send_request("tools/list", {})
        if response and "result" in response:
            return response["result"].get("tools", [])
        return []

    # ── tool invocation ──────────────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Invoke a tool on the MCP server and return the result.

        Returns the tool's response content, or None on failure.
        """
        response = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if response and "result" in response:
            return response["result"]
        return None

    # ── JSON-RPC internals ───────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request(self, method: str, params: dict) -> Optional[dict]:
        """Send a JSON-RPC 2.0 request and read the response."""
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None

        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        try:
            line = json.dumps(msg) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()

            # Read response line
            resp_line = self._process.stdout.readline()
            if resp_line:
                return json.loads(resp_line)
            return None
        except Exception:
            return None

    def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC 2.0 notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            line = json.dumps(msg) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except Exception:
            pass
