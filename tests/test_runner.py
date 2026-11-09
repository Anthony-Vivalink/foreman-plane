"""Execution body adapter tests (SPEC §10).

The tests use a fake agent script — they never depend on claude/codex/dsh
being installed and never touch the network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from foreman.config import load_config  # noqa: E402
from foreman.runner import Runner  # noqa: E402

EXIT_SCRIPT = """import os
import sys

print("ARGS:" + " ".join(sys.argv[1:]))
sys.stdout.flush()
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""

SLEEP_SCRIPT = """import time

time.sleep(30)
"""


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.exit_script = self.home / "fake_exit.py"
        self.exit_script.write_text(EXIT_SCRIPT, encoding="utf-8")
        self.sleep_script = self.home / "fake_sleep.py"
        self.sleep_script.write_text(SLEEP_SCRIPT, encoding="utf-8")
        agents = {
            "agents": {
                "fake": {
                    "command": f'"{sys.executable}" "{self.exit_script}" {{task}}',
                },
                "slow": {
                    "command": f'"{sys.executable}" "{self.sleep_script}" {{task}}',
                    "timeout_s": 1,
                },
                "missing": {"command": "foreman-plane-no-such-binary-xyz {task}"},
                "empty": {"command": ""},
                "wd": {
                    "command": f'"{sys.executable}" "{self.exit_script}" {{workdir}} {{task}}',
                },
            }
        }
        (self.home / "agents.json").write_text(json.dumps(agents), encoding="utf-8")
        self.config = load_config({"FOREMAN_HOME": str(self.home)})

    def _runner(self) -> Runner:
        return Runner(self.config)

    # ------------------------------------------------------------------
    def test_exit_code_is_passed_through(self) -> None:
        os.environ["FAKE_EXIT"] = "3"
        try:
            result = self._runner().run("做点事", agent="fake")
        finally:
            os.environ.pop("FAKE_EXIT", None)
        self.assertEqual(result.exit_code, 3)
        self.assertFalse(result.timed_out)

    def test_stdout_is_captured(self) -> None:
        result = self._runner().run("hello task", agent="fake")
        self.assertIn("ARGS:hello task", result.stdout)

    def test_missing_binary_yields_127(self) -> None:
        result = self._runner().run("anything", agent="missing")
        self.assertEqual(result.exit_code, 127)
        self.assertIn("agent not found", result.stderr)

    def test_agent_without_command_yields_127(self) -> None:
        result = self._runner().run("anything", agent="empty")
        self.assertEqual(result.exit_code, 127)
        self.assertIn("agent not found", result.stderr)

    def test_unknown_agent_yields_127(self) -> None:
        result = self._runner().run("anything", agent="nope")
        self.assertEqual(result.exit_code, 127)

    def test_timeout_yields_124(self) -> None:
        result = self._runner().run("slow task", agent="slow")
        self.assertEqual(result.exit_code, 124)
        self.assertTrue(result.timed_out)

    def test_workdir_placeholder(self) -> None:
        workdir = self.home / "wd"
        workdir.mkdir()
        runner = self._runner()
        tokens = runner.command_for("wd", "task", workdir=workdir)
        self.assertIsNotNone(tokens)
        self.assertIn(str(workdir), tokens)
        self.assertIn("task", tokens)

    def test_run_log_is_written(self) -> None:
        self._runner().run("logged task", agent="fake", task_id="T-20261109-01")
        logs = list((self.home / "runs").glob("*.log"))
        self.assertTrue(logs)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
