"""Ledger tests (SPEC §10)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from foreman.config import load_config  # noqa: E402
from foreman.errors import UnknownTaskError  # noqa: E402
from foreman.ledger import Ledger  # noqa: E402
from foreman.runner import Dispatcher, RunResult  # noqa: E402


class StubRunner:
    """Fake execution body — no network, no real agent."""

    def __init__(self, exit_code: int = 0) -> None:
        self.agent = "stub"
        self.exit_code = exit_code
        self.calls: list[str] = []

    def run(self, task, workdir=None, timeout_s=None, agent=None, task_id=None):
        self.calls.append(task)
        return RunResult(exit_code=self.exit_code, stdout="ok", stderr="", duration_s=0.01)


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.ledger = Ledger(self.home / "ledger.jsonl")
        self.addCleanup(self._tmp.cleanup)

    # ------------------------------------------------------------------
    def test_append_is_read_back(self) -> None:
        record = self.ledger.append("dispatch", "T-20261109-01", depends_on=["T-20261108-01"])
        events = self.ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "dispatch")
        self.assertEqual(events[0]["task_id"], "T-20261109-01")
        self.assertEqual(events[0]["depends_on"], ["T-20261108-01"])
        self.assertIn("ts", record)
        self.assertIn("actor", record)
        self.assertEqual(record["detail"]["depends_on"], ["T-20261108-01"])

    def test_ledger_is_append_only(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.ledger.append("running", "T-20261109-01")
        self.ledger.append("done", "T-20261109-01", verdict="pass")
        raw = (self.home / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(raw), 3)
        for line in raw:
            json.loads(line)

    def test_state_folding_dispatched_running_done(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.assertEqual(self.ledger.state_of("T-20261109-01")["status"], "dispatched")
        self.ledger.append("running", "T-20261109-01", agent="stub")
        self.assertEqual(self.ledger.state_of("T-20261109-01")["status"], "running")
        self.ledger.append("done", "T-20261109-01", verdict="pass", acceptance_ok=True, exit_code=0)
        state = self.ledger.state_of("T-20261109-01")
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["verdict"], "pass")
        self.assertTrue(self.ledger.is_dependency_satisfied("T-20261109-01"))

    def test_cancelled_overrides_previous_state(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.ledger.append("done", "T-20261109-01", verdict="pass", acceptance_ok=True)
        self.ledger.cancel("T-20261109-01", reason="manual")
        state = self.ledger.state_of("T-20261109-01")
        self.assertEqual(state["status"], "cancelled")
        self.assertFalse(self.ledger.is_dependency_satisfied("T-20261109-01"))

    def test_done_with_fail_is_not_a_satisfied_dependency(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.ledger.append("done", "T-20261109-01", verdict="fail", acceptance_ok=None)
        self.assertFalse(self.ledger.is_dependency_satisfied("T-20261109-01"))

    def test_task_without_done_is_never_passed(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.assertFalse(self.ledger.is_dependency_satisfied("T-20261109-01"))
        self.assertIsNone(self.ledger.state_of("T-20261109-99"))

    def test_cancel_unknown_task_raises_and_appends_nothing(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        before = len(self.ledger.events())
        with self.assertRaises(UnknownTaskError):
            self.ledger.cancel("T-20990101-99", reason="nope")
        self.assertEqual(len(self.ledger.events()), before)

    def test_replan_unknown_task_raises_and_appends_nothing(self) -> None:
        before = len(self.ledger.events())
        with self.assertRaises(UnknownTaskError):
            self.ledger.replan("T-20990101-99", reason="nope")
        self.assertEqual(len(self.ledger.events()), before)

    def test_duplicate_dispatch_is_rejected_without_second_dispatch(self) -> None:
        config = load_config({"FOREMAN_HOME": str(self.home)})
        runner = StubRunner()
        dispatcher = Dispatcher(config, runner=runner)
        task = "【id】T-20261109-01\n做点事情。"

        first = dispatcher.dispatch(task)
        self.assertTrue(first["ok"])
        second = dispatcher.dispatch(task)
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "duplicate_task")

        dispatches = [e for e in self.ledger.events() if e["event"] == "dispatch"]
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(len(runner.calls), 1)

    def test_force_overrides_the_duplicate_check(self) -> None:
        config = load_config({"FOREMAN_HOME": str(self.home)})
        dispatcher = Dispatcher(config, runner=StubRunner())
        task = "【id】T-20261109-01"
        dispatcher.dispatch(task)
        again = dispatcher.dispatch(task, force=True)
        self.assertNotEqual(again.get("reason"), "duplicate_task")
        dispatches = [e for e in self.ledger.events() if e["event"] == "dispatch"]
        self.assertEqual(len(dispatches), 2)

    def test_blocked_task_is_not_ready_until_its_dependency_passes(self) -> None:
        config = load_config({"FOREMAN_HOME": str(self.home)})
        dispatcher = Dispatcher(config, runner=StubRunner())
        blocked = dispatcher.dispatch("【id】T-20261109-02\n【depends_on】T-20261109-99\n干活")
        self.assertTrue(blocked["blocked"])
        self.assertEqual([item["task_id"] for item in dispatcher.blocked_tasks()], ["T-20261109-02"])
        self.assertEqual(dispatcher.ready_tasks(), [])
        self.ledger.append("dispatch", "T-20261109-99")
        self.ledger.append("running", "T-20261109-99", agent="stub")
        self.ledger.append("done", "T-20261109-99", verdict="pass", acceptance_ok=True, exit_code=0)
        self.assertEqual([item["task_id"] for item in dispatcher.ready_tasks()], ["T-20261109-02"])

    def test_blocked_task_can_be_dispatched_again_once_dependency_passes(self) -> None:
        """A blocked task never entered the plane — it must stay re-dispatchable.

        Regression: counting ``blocked`` as "already dispatched" locked the
        task out forever, so the upstream task could never unblock it.
        """
        config = load_config({"FOREMAN_HOME": str(self.home)})
        dispatcher = Dispatcher(config, runner=StubRunner())
        card = "【id】T-20261109-02\n【depends_on】T-20261109-01\n干活"

        blocked = dispatcher.dispatch(card)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["reason"], "dependency_unknown")
        self.assertFalse(self.ledger.is_already_dispatched("T-20261109-02"))

        # the upstream task arrives and passes
        upstream = dispatcher.dispatch("【id】T-20261109-01\n先干活")
        self.assertTrue(upstream["ok"])

        # the blocked task is dispatchable again
        again = dispatcher.dispatch(card)
        self.assertTrue(again["ok"], again)
        self.assertTrue(again["dispatched"])
        self.assertIsNone(again.get("reason"))

        dispatches = [e for e in self.ledger.events() if e["event"] == "dispatch"]
        self.assertEqual([e["task_id"] for e in dispatches], ["T-20261109-01", "T-20261109-02"])
        blocked_events = [e for e in self.ledger.events() if e["event"] == "blocked"]
        self.assertEqual(len(blocked_events), 1)

    def test_dispatched_task_is_still_a_duplicate_after_a_blocked_event(self) -> None:
        """Once a task really entered the plane, it stays duplicate-protected."""
        config = load_config({"FOREMAN_HOME": str(self.home)})
        dispatcher = Dispatcher(config, runner=StubRunner())
        card = "【id】T-20261109-03\n干活"
        self.assertTrue(dispatcher.dispatch(card)["ok"])
        self.assertTrue(self.ledger.is_already_dispatched("T-20261109-03"))
        again = dispatcher.dispatch(card)
        self.assertEqual(again["reason"], "duplicate_task")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
