"""Failure & recovery tests (v0.3.0).

Lease, heartbeat, stale reclaim, step checkpoints and backoff retry.  The
tests never touch the network and never depend on a real agent being
installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))

from foreman_plane.config import load_config  # noqa: E402
from foreman_plane.errors import UnknownTaskError  # noqa: E402
from foreman_plane.ledger import Ledger  # noqa: E402
from foreman_plane.runner import Dispatcher, RetryPolicy, RunResult  # noqa: E402

TASK = "T-20261123-01"
OTHER = "T-20261123-02"

#: A fake agent that writes its argv to ${DRILL_ARGV} and exits with the
#: code in ${FAKE_EXIT}.
ARGV_AGENT = """import json
import os
import sys

path = os.environ.get("FAKE_ARGV")
if path:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""

#: A fake agent that fails with a retryable exit code once.
FLAKY_AGENT = """import os
import sys

counter = os.environ.get("FAKE_COUNTER", "")
if counter:
    try:
        with open(counter, "r", encoding="utf-8") as handle:
            runs = int(handle.read().strip() or "0")
    except OSError:
        runs = 0
    runs += 1
    with open(counter, "w", encoding="utf-8") as handle:
        handle.write(str(runs))
    if runs == 1:
        sys.exit(int(os.environ.get("FAKE_EXIT", "1")))
sys.exit(0)
"""


def moment(offset_s: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_s)


class RecoveryTestCase(unittest.TestCase):
    """Shared plumbing: a temporary plane home and a fake execution body."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.argv_file = self.home / "argv.jsonl"
        self.agent = self.home / "fake_agent.py"
        self.agent.write_text(ARGV_AGENT, encoding="utf-8")
        self.flaky = self.home / "flaky_agent.py"
        self.flaky.write_text(FLAKY_AGENT, encoding="utf-8")
        self.counter = self.home / "counter.txt"
        self.agents = {
            "agents": {
                "fake": {
                    "command": f'"{sys.executable}" "{self.agent}" {{task}} {{completed_steps}}'
                },
                "plain": {"command": f'"{sys.executable}" "{self.agent}" {{task}}'},
                "flaky": {
                    "command": f'"{sys.executable}" "{self.flaky}" {{task}}',
                    "retry": {
                        "max_attempts": 3,
                        "base_delay_s": 0,
                        "retry_on_exit_codes": [7],
                    },
                },
                "noretry": {"command": f'"{sys.executable}" "{self.flaky}" {{task}}'},
            }
        }
        (self.home / "agents.json").write_text(json.dumps(self.agents), encoding="utf-8")
        self.config = load_config({"FOREMAN_HOME": str(self.home)})
        self.ledger = Ledger(self.config.ledger_path)

    # ------------------------------------------------------------------
    def dispatcher(self) -> Dispatcher:
        return Dispatcher(self.config)

    def argv_rows(self) -> list[list[str]]:
        if not self.argv_file.exists():
            return []
        return [
            json.loads(line)
            for line in self.argv_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def running_task(self, task_id: str = TASK, expires_in_s: float = 900.0) -> dict:
        """Put a task into the ``running`` state with a lease."""
        expiry = (moment() + timedelta(seconds=expires_in_s)).isoformat()
        self.ledger.append("dispatch", task_id, lease_expires_at=expiry, lease_s=900)
        self.ledger.append("running", task_id, lease_expires_at=expiry, lease_s=900)
        return self.ledger.state_of(task_id) or {}


class LeaseTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def test_dispatch_records_a_lease(self) -> None:
        dispatcher = self.dispatcher()
        payload = dispatcher.dispatch(f"【id】{TASK}\n干活。", agent="plain")
        self.assertTrue(payload["ok"], payload)
        dispatch = [
            event
            for event in self.ledger.events(TASK)
            if event["event"] == "dispatch"
        ][0]
        self.assertIn("lease_expires_at", dispatch)
        self.assertEqual(dispatch["lease_s"], self.config.lease_s)
        self.assertEqual(self.config.lease_s, 900)

    def test_heartbeat_refreshes_the_lease(self) -> None:
        state = self.running_task(expires_in_s=10)
        dispatcher = self.dispatcher()
        payload = dispatcher.heartbeat(TASK)
        self.assertTrue(payload["ok"], payload)
        self.assertNotEqual(payload["lease_expires_at"], state["lease_expires_at"])
        events = [event["event"] for event in self.ledger.events(TASK)]
        self.assertEqual(events[-1], "heartbeat")
        # a heartbeat never changes the status of the task
        self.assertEqual(self.ledger.state_of(TASK)["status"], "running")

    def test_heartbeat_on_unknown_task_raises_and_appends_nothing(self) -> None:
        dispatcher = self.dispatcher()
        before = len(self.ledger.events())
        with self.assertRaises(UnknownTaskError):
            dispatcher.heartbeat("T-20990101-99")
        self.assertEqual(len(self.ledger.events()), before)

    def test_heartbeat_on_a_finished_task_is_refused(self) -> None:
        self.ledger.append("dispatch", TASK)
        self.ledger.append("done", TASK, verdict="pass")
        payload = self.dispatcher().heartbeat(TASK)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "task_not_running")

    def test_lease_s_is_configurable(self) -> None:
        (self.home / "agents.json").write_text(
            json.dumps({"lease_s": 42, "agents": self.agents["agents"]}), encoding="utf-8"
        )
        config = load_config({"FOREMAN_HOME": str(self.home)})
        self.assertEqual(config.lease_s, 42)
        env_config = load_config({"FOREMAN_HOME": str(self.home), "FOREMAN_LEASE": "77"})
        self.assertEqual(env_config.lease_s, 77)


class ReclaimTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def test_reclaim_marks_an_expired_task_as_stale(self) -> None:
        self.running_task(expires_in_s=-5)
        payload = self.dispatcher().reclaim()
        self.assertEqual(payload["marked_count"], 1)
        self.assertEqual(payload["marked"][0]["task_id"], TASK)
        self.assertEqual(self.ledger.state_of(TASK)["status"], "stale")
        self.assertEqual(
            [event["event"] for event in self.ledger.events(TASK)][-1], "stale"
        )

    def test_reclaim_dry_run_writes_nothing(self) -> None:
        self.running_task(expires_in_s=-5)
        payload = self.dispatcher().reclaim(dry_run=True)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["marked_count"], 1)
        self.assertEqual(self.ledger.state_of(TASK)["status"], "running")
        self.assertNotIn("stale", [event["event"] for event in self.ledger.events(TASK)])

    def test_reclaim_leaves_a_live_lease_alone(self) -> None:
        self.running_task(expires_in_s=600)
        payload = self.dispatcher().reclaim()
        self.assertEqual(payload["marked_count"], 0)
        self.assertEqual(payload["active_count"], 1)
        self.assertEqual(self.ledger.state_of(TASK)["status"], "running")

    def test_reclaim_skips_tasks_that_are_not_running(self) -> None:
        self.ledger.append("dispatch", OTHER)
        self.ledger.append("done", OTHER, verdict="pass")
        payload = self.dispatcher().reclaim()
        self.assertEqual(payload["marked_count"], 0)
        self.assertEqual(payload["skipped"][0]["task_id"], OTHER)
        self.assertEqual(payload["skipped"][0]["reason"], "not_running")

    def test_reclaim_of_an_unknown_task_raises(self) -> None:
        with self.assertRaises(UnknownTaskError):
            self.dispatcher().reclaim(task_id="T-20990101-99")

    def test_reclaim_honours_the_now_argument(self) -> None:
        self.running_task(expires_in_s=600)
        later = moment(3600)
        payload = self.dispatcher().reclaim(now=later)
        self.assertEqual(payload["marked_count"], 1)


class RedispatchTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def test_running_task_with_a_live_lease_is_refused(self) -> None:
        self.running_task(expires_in_s=600)
        payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="plain")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "duplicate_task")
        self.assertEqual(payload["lease_remaining_s"] > 0, True)

    def test_stale_task_can_be_dispatched_again(self) -> None:
        self.running_task(expires_in_s=-5)
        self.dispatcher().reclaim()
        payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="plain")
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["dispatched"])
        self.assertNotEqual(payload.get("reason"), "duplicate_task")

    def test_blocked_task_stays_re_dispatchable(self) -> None:
        """Regression: a blocked task never entered the plane."""
        dispatcher = self.dispatcher()
        card = f"【id】{TASK}\n【depends_on】T-20261123-99\n干活。"
        blocked = dispatcher.dispatch(card, agent="plain")
        self.assertTrue(blocked["blocked"])
        self.assertFalse(self.ledger.is_already_dispatched(TASK))

    def test_done_task_stays_duplicate_protected(self) -> None:
        dispatcher = self.dispatcher()
        card = f"【id】{TASK}\n干活。"
        self.assertTrue(dispatcher.dispatch(card, agent="plain")["ok"])
        self.assertTrue(self.ledger.is_already_dispatched(TASK))
        again = dispatcher.dispatch(card, agent="plain")
        self.assertEqual(again["reason"], "duplicate_task")

    def test_expired_lease_makes_a_running_task_re_dispatchable(self) -> None:
        self.running_task(expires_in_s=-5)
        self.assertFalse(self.ledger.is_already_dispatched(TASK))
        payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="plain")
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["recovered"])
        kinds = [event["event"] for event in self.ledger.events(TASK)]
        self.assertIn("stale", kinds)


class CheckpointTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def test_checkpoint_records_the_step(self) -> None:
        self.running_task()
        payload = self.dispatcher().checkpoint(TASK, "step_a", artifact="out/a.txt")
        self.assertTrue(payload["ok"], payload)
        state = self.ledger.state_of(TASK) or {}
        self.assertEqual(state["completed_steps"], ["step_a"])
        self.assertEqual(state["completed_steps_count"], 1)

    def test_checkpoint_of_an_unknown_task_raises_and_appends_nothing(self) -> None:
        before = len(self.ledger.events())
        with self.assertRaises(UnknownTaskError):
            self.dispatcher().checkpoint("T-20990101-99", "step_a")
        self.assertEqual(len(self.ledger.events()), before)

    def test_re_dispatch_hands_the_completed_steps_to_the_body(self) -> None:
        self.running_task(expires_in_s=-5)
        self.dispatcher().reclaim()
        self.dispatcher().checkpoint(TASK, "step_a")
        os.environ["FAKE_ARGV"] = str(self.argv_file)
        try:
            payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="fake")
        finally:
            os.environ.pop("FAKE_ARGV", None)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["completed_steps"], ["step_a"])
        self.assertEqual(payload["skipped_steps"], ["step_a"])
        self.assertTrue(payload["resumed"])
        rows = self.argv_rows()
        self.assertTrue(rows)
        self.assertIn('["step_a"]', rows[-1])

    def test_a_template_without_the_placeholder_still_works(self) -> None:
        os.environ["FAKE_ARGV"] = str(self.argv_file)
        try:
            payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="plain")
        finally:
            os.environ.pop("FAKE_ARGV", None)
        self.assertTrue(payload["ok"], payload)
        rows = self.argv_rows()
        self.assertEqual(len(rows[-1]), 1)
        self.assertNotIn("[]", rows[-1][0])
        self.assertEqual(payload["completed_steps"], [])

    def test_completed_steps_are_empty_without_checkpoints(self) -> None:
        os.environ["FAKE_ARGV"] = str(self.argv_file)
        try:
            payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="fake")
        finally:
            os.environ.pop("FAKE_ARGV", None)
        self.assertIn("[]", self.argv_rows()[-1])


class RetryTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def test_no_retry_without_a_retry_block(self) -> None:
        os.environ["FAKE_EXIT"] = "7"
        os.environ["FAKE_COUNTER"] = str(self.counter)
        try:
            payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="noretry")
        finally:
            os.environ.pop("FAKE_EXIT", None)
            os.environ.pop("FAKE_COUNTER", None)
        self.assertEqual(payload["attempts"], 1)
        self.assertEqual(payload["retries"], 0)
        self.assertNotIn("retry", [event["event"] for event in self.ledger.events(TASK)])

    def test_retry_on_a_retryable_exit_code(self) -> None:
        os.environ["FAKE_EXIT"] = "7"
        os.environ["FAKE_COUNTER"] = str(self.counter)
        try:
            payload = self.dispatcher().dispatch(f"【id】{TASK}\n干活。", agent="flaky")
        finally:
            os.environ.pop("FAKE_EXIT", None)
            os.environ.pop("FAKE_COUNTER", None)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["attempts"], 2)
        self.assertEqual(payload["retries"], 1)
        events = [event["event"] for event in self.ledger.events(TASK)]
        self.assertEqual(events.count("retry"), 1)
        # a retry never produces a second dispatch event
        self.assertEqual(events.count("dispatch"), 1)
        retry = [event for event in self.ledger.events(TASK) if event["event"] == "retry"][0]
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["exit_code"], 7)
        self.assertIn("delay_s", retry)

    def test_retry_policy_defaults(self) -> None:
        self.assertIsNone(RetryPolicy.from_spec({}))
        policy = RetryPolicy.from_spec({"retry": {}})
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual(policy.max_attempts, 1)
        self.assertEqual(policy.retry_on_exit_codes, (124,))


class ObservabilityTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def test_status_carries_the_new_fields(self) -> None:
        self.running_task(expires_in_s=600)
        self.dispatcher().checkpoint(TASK, "step_a")
        state = self.dispatcher().status(TASK)["task"] or {}
        self.assertIn("lease_expires_at", state)
        self.assertIn("lease_remaining_s", state)
        self.assertIn("age_s", state)
        self.assertEqual(state["completed_steps_count"], 1)
        self.assertEqual(state["recoveries"], 0)
        self.assertEqual(state["retries"], 0)

    def test_status_keeps_the_old_fields(self) -> None:
        self.running_task()
        state = self.dispatcher().status(TASK)["task"] or {}
        for key in (
            "task_id",
            "status",
            "verdict",
            "acceptance_ok",
            "exit_code",
            "agent",
            "depends_on",
            "needs_api",
            "acceptance",
            "reason",
            "missing",
            "last_event",
            "last_ts",
            "events",
        ):
            self.assertIn(key, state)

    def test_an_unknown_event_kind_does_not_break_the_fold(self) -> None:
        self.ledger.append("dispatch", TASK)
        self.ledger.append("a_kind_from_the_future", TASK, whatever=1)
        state = self.ledger.state_of(TASK) or {}
        self.assertEqual(state["status"], "dispatched")
        self.assertEqual(state["events"], 2)


class RecoveryCliTests(RecoveryTestCase):
    # ------------------------------------------------------------------
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
        env["FOREMAN_HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, "-m", "foreman_plane.cli", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            timeout=60,
        )

    def test_cli_heartbeat_checkpoint_and_reclaim(self) -> None:
        self.running_task(expires_in_s=600)
        beat = self._run("heartbeat", TASK, "--json")
        self.assertEqual(beat.returncode, 0, beat.stdout + beat.stderr)
        self.assertTrue(json.loads(beat.stdout)["ok"])

        mark = self._run("checkpoint", TASK, "--step", "step_a", "--json")
        self.assertEqual(mark.returncode, 0, mark.stdout + mark.stderr)
        self.assertEqual(json.loads(mark.stdout)["completed_steps"], ["step_a"])

        report = self._run("reclaim", "--dry-run", "--json")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        payload = json.loads(report.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["marked_count"], 0)

    def test_cli_reclaim_marks_the_expired_task(self) -> None:
        self.running_task(expires_in_s=-5)
        report = self._run("reclaim", "--json")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        payload = json.loads(report.stdout)
        self.assertEqual(payload["marked_count"], 1)
        self.assertEqual(self.ledger.state_of(TASK)["status"], "stale")

    def test_cli_reclaim_rejects_a_broken_now(self) -> None:
        done = self._run("reclaim", "--now", "not-a-timestamp")
        self.assertEqual(done.returncode, 1)

    def test_cli_checkpoint_of_an_unknown_task_exits_with_1(self) -> None:
        done = self._run("checkpoint", "T-20990101-99", "--step", "x")
        self.assertEqual(done.returncode, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
