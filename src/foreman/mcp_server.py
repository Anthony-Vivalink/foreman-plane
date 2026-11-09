"""MCP server over stdio (SPEC §7).

JSON-RPC 2.0, one JSON object per line (NDJSON framing) on stdin/stdout.
stdout carries protocol frames only — every log line goes to stderr.

The server is deliberately tiny: no SDK, no third-party dependency, no
network access.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__
from .config import ForemanConfig, load_config
from .errors import UnknownTaskError
from .runner import Dispatcher

__all__ = ["ForemanMCPServer", "main", "PROTOCOL_VERSION", "TOOLS"]

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "foreman"
SERVER_VERSION = __version__

TOOLS: list[dict[str, Any]] = [
    {
        "name": "foreman_dispatch",
        "description": (
            "Dispatch a task card to an execution body. The card may carry the markers "
            "[id] [depends_on] [needs_api] [acceptance]. Hard dependencies are gated: "
            "when one is not satisfied the task is never executed and only a blocked "
            "event is written."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Full task text including markers."},
                "agent": {"type": "string", "description": "Execution body name."},
                "workdir": {"type": "string", "description": "Working directory."},
                "force": {"type": "boolean", "description": "Override the duplicate check."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "foreman_status",
        "description": "Ledger status summary, optionally for one task id.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task id."}},
            "required": [],
        },
    },
    {
        "name": "foreman_cancel",
        "description": "Cancel a task. Unknown task ids are rejected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id."},
                "reason": {"type": "string", "description": "Why the task is cancelled."},
            },
            "required": ["task_id"],
        },
    },
]


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class ForemanMCPServer:
    """Minimal MCP server speaking JSON-RPC 2.0 over NDJSON frames."""

    def __init__(
        self,
        config: ForemanConfig | None = None,
        dispatcher: Dispatcher | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.config = config or load_config()
        self.dispatcher = dispatcher or Dispatcher(self.config)
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr

    # ------------------------------------------------------------------
    # tool layer
    # ------------------------------------------------------------------
    def call_tool(self, name: str, arguments: dict) -> tuple[dict, bool]:
        """Run one tool, returning ``(payload, is_error)``."""
        arguments = arguments or {}
        if name == "foreman_dispatch":
            task = arguments.get("task")
            if not isinstance(task, str) or not task.strip():
                return ({"ok": False, "error": "invalid_params", "detail": "task is required"}, True)
            payload = self.dispatcher.dispatch(
                task,
                agent=arguments.get("agent"),
                workdir=arguments.get("workdir"),
                force=bool(arguments.get("force", False)),
            )
            return (payload, not bool(payload.get("ok", False)))
        if name == "foreman_status":
            task_id = arguments.get("task_id")
            return (self.dispatcher.status(task_id), False)
        if name == "foreman_cancel":
            task_id = arguments.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                return ({"ok": False, "error": "invalid_params", "detail": "task_id is required"}, True)
            try:
                payload = self.dispatcher.cancel(task_id, str(arguments.get("reason") or ""))
            except UnknownTaskError as exc:
                return (
                    {"ok": False, "error": "unknown_task", "task_id": task_id, "detail": str(exc)},
                    True,
                )
            return (payload, False)
        return ({"ok": False, "error": "unknown_tool", "tool": name}, True)

    # ------------------------------------------------------------------
    # protocol layer
    # ------------------------------------------------------------------
    def handle(self, request: Any) -> dict | None:
        """Handle one JSON-RPC request; returns ``None`` for notifications."""
        if not isinstance(request, dict):
            return _error(None, -32600, "invalid request")
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}
        is_notification = "id" not in request

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            if name not in {tool["name"] for tool in TOOLS}:
                if is_notification:
                    return None
                return _error(request_id, -32602, f"unknown tool: {name}")
            payload, is_error = self.call_tool(str(name), arguments)
            result = {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                ],
                "isError": bool(is_error),
            }
        elif method == "resources/list":
            result = {"resources": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        elif method == "ping":
            result = {}
        else:
            if is_notification:
                return None
            return _error(request_id, -32601, f"method not found: {method}")

        if is_notification:
            return None
        return _result(request_id, result)

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        try:
            self.stderr.write(message.rstrip("\n") + "\n")
            self.stderr.flush()
        except Exception:  # pragma: no cover - stderr must never break the server
            pass

    def _send(self, payload: dict) -> None:
        self.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def serve_forever(self) -> int:
        """Read NDJSON frames from stdin until EOF."""
        for line in self.stdin:
            chunk = line.strip()
            if not chunk:
                continue
            try:
                request = json.loads(chunk)
            except ValueError:
                self._send(_error(None, -32700, "parse error"))
                continue
            try:
                response = self.handle(request)
            except Exception as exc:  # pragma: no cover - defensive
                self._log(f"internal error: {exc!r}")
                response = _error(
                    request.get("id") if isinstance(request, dict) else None,
                    -32603,
                    "internal error",
                )
            if response is not None:
                self._send(response)
        return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m foreman.mcp_server``."""
    server = ForemanMCPServer()
    return server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
