"""MCP server tests (SPEC §10).

A real subprocess is started with NDJSON frames on stdin/stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))

from foreman.mcp_server import PROTOCOL_VERSION  # noqa: E402


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = os.environ.copy()
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
        env["FOREMAN_HOME"] = str(self.home)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "foreman.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(ROOT),
        )
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    # ------------------------------------------------------------------
    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        self.assertTrue(line, "the MCP server did not answer")
        return json.loads(line)

    def _rpc(self, method: str, params: dict | None = None, request_id: int = 1) -> dict:
        payload: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        return self._read()

    # ------------------------------------------------------------------
    def test_initialize(self) -> None:
        answer = self._rpc("initialize")
        result = answer["result"]
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(result["serverInfo"]["name"], "foreman")
        self.assertIn("tools", result["capabilities"])

    def test_notification_produces_no_answer(self) -> None:
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answer = self._rpc("tools/list", request_id=2)
        self.assertEqual(answer["id"], 2)
        self.assertIn("result", answer)

    def test_tools_list(self) -> None:
        answer = self._rpc("tools/list")
        names = [tool["name"] for tool in answer["result"]["tools"]]
        self.assertEqual(
            names, ["foreman_dispatch", "foreman_status", "foreman_cancel"]
        )

    def test_unknown_method_is_method_not_found(self) -> None:
        answer = self._rpc("does/not/exist")
        self.assertEqual(answer["error"]["code"], -32601)

    def test_resources_and_prompts_are_empty(self) -> None:
        self.assertEqual(self._rpc("resources/list")["result"]["resources"], [])
        self.assertEqual(self._rpc("prompts/list")["result"]["prompts"], [])

    def test_dispatch_with_unknown_dependency_is_blocked_without_dispatch(self) -> None:
        task = "【id】T-20261109-20\n【depends_on】T-20261109-99\n干活。"
        answer = self._rpc(
            "tools/call",
            {"name": "foreman_dispatch", "arguments": {"task": task}},
        )
        result = answer["result"]
        payload = json.loads(result["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["reason"], "dependency_unknown")

        events = [
            json.loads(line)
            for line in (self.home / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        kinds = [event["event"] for event in events]
        self.assertEqual(kinds, ["blocked"])
        self.assertNotIn("dispatch", kinds)

    def test_status_tool(self) -> None:
        answer = self._rpc("tools/call", {"name": "foreman_status", "arguments": {}})
        payload = json.loads(answer["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertIn("counts", payload)

    def test_cancel_unknown_task_is_an_error(self) -> None:
        answer = self._rpc(
            "tools/call",
            {"name": "foreman_cancel", "arguments": {"task_id": "T-20990101-99"}},
        )
        result = answer["result"]
        self.assertTrue(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["error"], "unknown_task")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
