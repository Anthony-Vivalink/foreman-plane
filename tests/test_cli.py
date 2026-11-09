"""CLI tests (SPEC §10)."""

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

FAKE_AGENT = """import os

path = os.environ.get("FAKE_ARTIFACT")
if path:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("done\\n")
print("fake agent ran")
"""


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.script = self.home / "fake_agent.py"
        self.script.write_text(FAKE_AGENT, encoding="utf-8")
        agents = {
            "agents": {
                "fake": {"command": f'"{sys.executable}" "{self.script}" {{task}}'},
            }
        }
        (self.home / "agents.json").write_text(json.dumps(agents), encoding="utf-8")
        self.artefact = self.home / "report.md"

    def _env(self) -> dict:
        env = os.environ.copy()
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
        env["FOREMAN_HOME"] = str(self.home)
        env["FAKE_ARTIFACT"] = str(self.artefact)
        return env

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "foreman.cli", *args],
            capture_output=True,
            text=True,
            env=self._env(),
            cwd=str(ROOT),
            timeout=60,
        )

    # ------------------------------------------------------------------
    def test_status_json_is_parseable(self) -> None:
        done = self._run("status", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        payload = json.loads(done.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("counts", payload)

    def test_config_json_is_parseable(self) -> None:
        done = self._run("config", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        payload = json.loads(done.stdout)
        self.assertEqual(payload["home"], str(self.home))
        self.assertIn("agents", payload)

    def test_dispatch_blocked_exits_with_2(self) -> None:
        card = self.home / "card.md"
        card.write_text(
            "【id】T-20261109-20\n【depends_on】T-20261109-99\n干活。\n", encoding="utf-8"
        )
        done = self._run("dispatch", "--file", str(card), "--json")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        payload = json.loads(done.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "dependency_unknown")

    def test_dispatch_success_exits_with_0_and_writes_the_ledger(self) -> None:
        card = self.home / "card.md"
        card.write_text(
            "【id】T-20261109-20\n【验收】report.md::min_bytes=3\n干活。\n", encoding="utf-8"
        )
        done = self._run(
            "dispatch",
            "--file",
            str(card),
            "--agent",
            "fake",
            "--workdir",
            str(self.home),
            "--json",
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        payload = json.loads(done.stdout)
        self.assertEqual(payload["verdict"], "pass")
        self.assertTrue(payload["acceptance_ok"])

        events = [
            json.loads(line)
            for line in (self.home / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual([event["event"] for event in events], ["dispatch", "running", "done"])

    def test_acceptance_failure_exits_with_3(self) -> None:
        card = self.home / "card.md"
        card.write_text("【id】T-20261109-20\n【验收】missing.md\n干活。\n", encoding="utf-8")
        done = self._run(
            "dispatch",
            "--file",
            str(card),
            "--agent",
            "fake",
            "--workdir",
            str(self.home),
            "--json",
        )
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)

    def test_receipt_carries_stderr_when_the_agent_cannot_start(self) -> None:
        """A failure before the agent starts must still say why."""
        card = self.home / "card.md"
        card.write_text("[id] T-20261109-21\n干活。\n", encoding="utf-8")
        done = self._run("dispatch", "--file", str(card), "--agent", "nope", "--json")
        self.assertNotEqual(done.returncode, 0)
        payload = json.loads(done.stdout)
        self.assertEqual(payload["exit_code"], 127)
        self.assertIn("agent not found", payload["stderr"])

        other = self.home / "card2.md"
        other.write_text("[id] T-20261109-22\n干活。\n", encoding="utf-8")
        text = self._run("dispatch", "--file", str(other), "--agent", "nope")
        self.assertIn("stderr:", text.stdout)

    def test_verify_command(self) -> None:
        self.artefact.write_text("hello", encoding="utf-8")
        ok = self._run(
            "verify", "--acceptance", "report.md::min_bytes=3", "--base-dir", str(self.home), "--json"
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertTrue(json.loads(ok.stdout)["ok"])

        bad = self._run(
            "verify", "--acceptance", "report.md::min_bytes=300", "--base-dir", str(self.home), "--json"
        )
        self.assertEqual(bad.returncode, 3)
        self.assertFalse(json.loads(bad.stdout)["ok"])

    def test_ledger_cancel_unknown_task_exits_with_1(self) -> None:
        done = self._run("ledger", "cancel", "T-20990101-99")
        self.assertEqual(done.returncode, 1)

    def test_ledger_show_and_list(self) -> None:
        card = self.home / "card.md"
        card.write_text("【id】T-20261109-20\n干活。\n", encoding="utf-8")
        self._run("dispatch", "--file", str(card), "--agent", "fake", "--workdir", str(self.home))
        listing = self._run("ledger", "list", "--json")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("T-20261109-20", json.loads(listing.stdout))
        show = self._run("ledger", "show", "T-20261109-20", "--json")
        self.assertEqual(show.returncode, 0, show.stderr)
        self.assertTrue(json.loads(show.stdout))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
