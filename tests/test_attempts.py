"""Attempt dimension tests (v0.3.0, batch B).

Covers the four things batch B promises:

* **B1** — every dispatch allocates an attempt (id, epoch, artefact
  directory), the state of an attempt is classified into *still running* /
  *interrupted* / *outcome unknown*, an explicit ``retry`` is different
  from a passive ``reclaim``, and a 0.2.x ledger keeps working;
* **B2** — the acceptance records the path and the sha256 of the file it
  really read, a receipt that arrives after a cancel is not the success of
  the current attempt, and two runs of the same task never overwrite their
  run log.

The tests never touch the network and never need a real agent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))

from foreman_plane.config import load_config  # noqa: E402
from foreman_plane.errors import UnknownTaskError  # noqa: E402
from foreman_plane.ledger import Ledger, infer_attempt_state  # noqa: E402
from foreman_plane.runner import Dispatcher  # noqa: E402

TASK = "T-20261123-31"
OTHER = "T-20261123-32"
THIRD = "T-20261123-33"

#: A worker that writes its artefact into the attempt directory.
WRITER_AGENT = """import os
import pathlib

directory = pathlib.Path(os.environ.get("FOREMAN_ATTEMPT_DIR") or ".")
directory.mkdir(parents=True, exist_ok=True)
(directory / "out.md").write_text("done by attempt\\n")
print("wrote", directory / "out.md")
"""

#: A worker whose artefact carries the word COMPLETE.
COMPLETE_AGENT = """import os
import pathlib

directory = pathlib.Path(os.environ.get("FOREMAN_ATTEMPT_DIR") or ".")
directory.mkdir(parents=True, exist_ok=True)
(directory / "out.md").write_text("COMPLETE by attempt\\n")
"""

#: A worker that writes into the base directory — the 0.2.x behaviour.
BASE_AGENT = """import os
import pathlib

pathlib.Path(os.environ.get("FOREMAN_WORKDIR") or ".", "out.md").write_text(
    "COMPLETE in base\\n"
)
"""

#: A worker that needs a moment — used for the cancel race.
SLOW_AGENT = """import os
import pathlib
import time

time.sleep(float(os.environ.get("SLOW_SLEEP", "1.0")))
directory = pathlib.Path(os.environ.get("FOREMAN_ATTEMPT_DIR") or ".")
directory.mkdir(parents=True, exist_ok=True)
(directory / "out.md").write_text("done by attempt\\n")
"""


def moment(offset_s: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_s)


class AttemptTestCase(unittest.TestCase):
    """Shared plumbing: a temporary plane home and a fake execution body."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.writer = self.home / "writer_agent.py"
        self.writer.write_text(WRITER_AGENT, encoding="utf-8")
        self.slow = self.home / "slow_agent.py"
        self.slow.write_text(SLOW_AGENT, encoding="utf-8")
        self.complete = self.home / "complete_agent.py"
        self.complete.write_text(COMPLETE_AGENT, encoding="utf-8")
        self.base = self.home / "base_agent.py"
        self.base.write_text(BASE_AGENT, encoding="utf-8")
        self.agents = {
            "agents": {
                "fake": {
                    "command": f'"{sys.executable}" "{self.writer}" {{task}} {{attempt_dir}}'
                },
                "slow": {
                    "command": f'"{sys.executable}" "{self.slow}" {{task}} {{attempt_dir}}'
                },
                "complete": {
                    "command": f'"{sys.executable}" "{self.complete}" {{task}} {{attempt_dir}}'
                },
                "base": {
                    "command": f'"{sys.executable}" "{self.base}" {{task}}'
                },
            }
        }
        (self.home / "agents.json").write_text(json.dumps(self.agents), encoding="utf-8")
        self.config = load_config({"FOREMAN_HOME": str(self.home)})
        self.ledger = Ledger(self.config.ledger_path)

    # ------------------------------------------------------------------
    def dispatcher(self) -> Dispatcher:
        return Dispatcher(self.config)

    def card(self, task_id: str = TASK, acceptance: str = "out.md::min_bytes=3") -> str:
        return f"【id】{task_id}\n【验收】{acceptance}\n干活。\n"


# ----------------------------------------------------------------------
# B1 — the attempt dimension
# ----------------------------------------------------------------------
class AttemptAllocationTests(AttemptTestCase):
    def test_dispatch_allocates_an_attempt(self) -> None:
        receipt = self.dispatcher().dispatch(self.card(), agent="fake", workdir=str(self.home))
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(receipt["attempt_id"], f"{TASK}-a01")
        self.assertEqual(receipt["epoch"], 1)
        attempt_dir = Path(receipt["attempt_dir"])
        self.assertTrue(attempt_dir.is_dir())
        self.assertEqual(attempt_dir.name, f"{TASK}-a01")
        self.assertEqual(attempt_dir.parent.name, "attempts")
        self.assertEqual(attempt_dir.parent.parent, self.home)

        dispatch_events = [
            record
            for record in self.ledger.events(TASK)
            if record["event"] == "dispatch"
        ]
        self.assertEqual(len(dispatch_events), 1)
        self.assertEqual(dispatch_events[0]["attempt_id"], f"{TASK}-a01")
        self.assertEqual(dispatch_events[0]["epoch"], 1)

    def test_a_second_dispatch_allocates_a_new_attempt(self) -> None:
        dispatcher = self.dispatcher()
        first = dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        # the lease of the first attempt is over -> the task is reclaimable
        dispatcher.ledger.append(
            "stale", TASK, reason="lease_expired", lease_expires_at=None
        )
        second = dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        self.assertEqual(first["attempt_id"], f"{TASK}-a01")
        self.assertEqual(second["attempt_id"], f"{TASK}-a02")
        self.assertEqual(second["epoch"], 2)
        self.assertNotEqual(first["attempt_dir"], second["attempt_dir"])

        attempts = dispatcher.attempts(TASK)
        self.assertEqual(len(attempts["attempts"]), 2)
        self.assertEqual(
            [item["attempt_id"] for item in attempts["attempts"]],
            [f"{TASK}-a01", f"{TASK}-a02"],
        )

    def test_the_artefacts_of_two_attempts_do_not_mix(self) -> None:
        dispatcher = self.dispatcher()
        first = dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        dispatcher.ledger.append("stale", TASK, reason="lease_expired")
        second = dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        self.assertNotEqual(first["acceptance_path"], second["acceptance_path"])
        self.assertIn(f"{TASK}-a01", first["acceptance_path"])
        self.assertIn(f"{TASK}-a02", second["acceptance_path"])
        for receipt in (first, second):
            self.assertTrue(Path(receipt["acceptance_path"]).is_file())

    def test_the_attempt_state_is_classified(self) -> None:
        # still running: the lease is alive
        state = {
            "status": "running",
            "lease_expires_at": (moment(600)).isoformat(),
            "late_receipts": 0,
        }
        self.assertEqual(infer_attempt_state(state), "still_running")
        # interrupted: the lease expired without a terminal event
        state = {
            "status": "running",
            "lease_expires_at": (moment(-5)).isoformat(),
            "late_receipts": 0,
        }
        self.assertEqual(infer_attempt_state(state), "interrupted")
        # outcome unknown: a receipt arrived that cannot be attributed
        state = {
            "status": "cancelled",
            "lease_expires_at": (moment(600)).isoformat(),
            "late_receipts": 1,
        }
        self.assertEqual(infer_attempt_state(state), "outcome_unknown")
        # outcome unknown: a legacy task without a lease
        state = {"status": "running", "lease_expires_at": None, "late_receipts": 0}
        self.assertEqual(infer_attempt_state(state), "outcome_unknown")
        # finished
        state = {"status": "done", "lease_expires_at": None, "late_receipts": 0}
        self.assertEqual(infer_attempt_state(state), "finished")
        # never started
        state = {"status": "blocked", "lease_expires_at": None, "late_receipts": 0}
        self.assertEqual(infer_attempt_state(state), "not_started")

    def test_status_reports_the_attempt_state(self) -> None:
        dispatcher = self.dispatcher()
        dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        payload = dispatcher.status(TASK)
        self.assertEqual(payload["task"]["attempt_state"], "finished")
        self.assertEqual(payload["task"]["attempt_id"], f"{TASK}-a01")
        self.assertEqual(payload["task"]["epoch"], 1)
        self.assertEqual(payload["task"]["late_receipts"], 0)

    def test_a_legacy_ledger_is_not_misread(self) -> None:
        # a 0.2.x ledger: no attempt id, no epoch, no lease
        self.ledger.append("dispatch", TASK, agent="old")
        self.ledger.append("running", TASK, agent="old")
        self.ledger.append("done", TASK, verdict="pass", acceptance_ok=True, exit_code=0)
        state = self.ledger.state_of(TASK) or {}
        self.assertEqual(state["status"], "done")
        self.assertIsNone(state["attempt_id"])
        self.assertFalse(state["attempt_aware"])
        self.assertEqual(infer_attempt_state(state), "finished")

    def test_a_legacy_running_task_is_unknown_and_never_failed(self) -> None:
        self.ledger.append("dispatch", OTHER, agent="old")
        self.ledger.append("running", OTHER, agent="old")
        state = self.ledger.state_of(OTHER) or {}
        self.assertEqual(state["status"], "running")
        self.assertEqual(infer_attempt_state(state), "outcome_unknown")
        self.assertNotEqual(state["status"], "fail")
        self.assertNotEqual(state["status"], "failed")

    def test_a_legacy_ledger_keeps_the_0_2_x_done_wins_behaviour(self) -> None:
        self.ledger.append("dispatch", THIRD, agent="old")
        self.ledger.append("cancelled", THIRD, reason="human")
        self.ledger.append("done", THIRD, verdict="pass", acceptance_ok=True)
        state = self.ledger.state_of(THIRD) or {}
        self.assertEqual(state["status"], "done")


# ----------------------------------------------------------------------
# B1 — the explicit retry
# ----------------------------------------------------------------------
class ExplicitRetryTests(AttemptTestCase):
    def test_retry_runs_the_body_again_in_a_new_attempt(self) -> None:
        dispatcher = self.dispatcher()
        card_file = self.home / "card.md"
        card_file.write_text(self.card(), encoding="utf-8")
        first = dispatcher.dispatch(
            self.card(), agent="fake", workdir=str(self.home), card_path=str(card_file)
        )
        dispatcher.ledger.append("stale", TASK, reason="lease_expired")
        second = dispatcher.retry(
            TASK, card_path=str(card_file), agent="fake", workdir=str(self.home)
        )
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["retry_mode"], "explicit")
        self.assertEqual(second["attempt_id"], f"{TASK}-a02")
        self.assertEqual(second["epoch"], 2)
        self.assertEqual(second["previous_attempt_id"], first["attempt_id"])
        # the body really ran a second time: two run logs
        logs = sorted((self.home / "runs").glob("*.log"))
        self.assertEqual(len(logs), 2)
        # the retry is recorded as an explicit retry, not as a backoff retry
        retries = [
            record for record in self.ledger.events(TASK) if record["event"] == "retry"
        ]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["mode"], "explicit")
        self.assertEqual(retries[0]["attempt_id"], f"{TASK}-a02")

    def test_retry_is_not_reclaim(self) -> None:
        dispatcher = self.dispatcher()
        dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        before = len(list((self.home / "runs").glob("*.log")))
        # reclaim is passive: it only marks a task, it never executes
        # anything — a finished task is not reclaimable at all
        report = dispatcher.reclaim()
        self.assertEqual(report["marked_count"], 0)
        self.assertEqual(len(list((self.home / "runs").glob("*.log"))), before)
        self.assertEqual(self.ledger.state_of(TASK)["status"], "done")

    def test_retry_refuses_while_the_attempt_still_holds_a_lease(self) -> None:
        dispatcher = self.dispatcher()
        # a task that is running right now: the lease is alive
        expiry = (moment(600)).isoformat()
        self.ledger.append(
            "attempt", TASK, attempt_id=f"{TASK}-a01", epoch=1, lease_expires_at=expiry
        )
        self.ledger.append(
            "dispatch", TASK, attempt_id=f"{TASK}-a01", epoch=1, lease_expires_at=expiry
        )
        self.ledger.append(
            "running", TASK, attempt_id=f"{TASK}-a01", epoch=1, lease_expires_at=expiry
        )
        receipt = dispatcher.retry(TASK, card_text=self.card(), agent="fake")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["reason"], "attempt_still_running")
        self.assertEqual(receipt["attempt_state"], "still_running")
        # nothing was written
        self.assertEqual(
            [record["event"] for record in self.ledger.events(TASK)],
            ["attempt", "dispatch", "running"],
        )

    def test_retry_without_a_card_reports_card_unavailable(self) -> None:
        dispatcher = self.dispatcher()
        dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        receipt = dispatcher.retry(TASK, agent="fake")
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["reason"], "card_unavailable")

    def test_retry_of_an_unknown_task_raises(self) -> None:
        with self.assertRaises(UnknownTaskError):
            self.dispatcher().retry("T-20990101-99", card_text=self.card())


# ----------------------------------------------------------------------
# B2 — the acceptance only accepts the artefact of this attempt
# ----------------------------------------------------------------------
class AcceptanceEvidenceTests(AttemptTestCase):
    def test_the_acceptance_records_path_and_sha256(self) -> None:
        receipt = self.dispatcher().dispatch(self.card(), agent="fake", workdir=str(self.home))
        self.assertTrue(receipt["acceptance_path"])
        self.assertEqual(len(receipt["acceptance_sha256"]), 64)
        self.assertTrue(receipt["acceptance_size"] > 0)

        done = [
            record for record in self.ledger.events(TASK) if record["event"] == "done"
        ][0]
        self.assertEqual(done["acceptance_path"], receipt["acceptance_path"])
        self.assertEqual(done["acceptance_sha256"], receipt["acceptance_sha256"])

        state = self.ledger.state_of(TASK) or {}
        evidence = state["acceptance_evidence"]
        self.assertEqual(evidence["path"], receipt["acceptance_path"])
        self.assertEqual(evidence["sha256"], receipt["acceptance_sha256"])
        self.assertTrue(evidence["ok"])

    def test_a_stale_artefact_of_a_previous_attempt_is_not_accepted(self) -> None:
        dispatcher = self.dispatcher()
        # the stale file of an earlier attempt sits in the base directory
        stale = self.home / "out.md"
        stale.write_text("PARTIAL\n", encoding="utf-8")
        receipt = dispatcher.dispatch(
            self.card(acceptance="out.md::contains=COMPLETE"),
            agent="complete",
            workdir=str(self.home),
        )
        # the attempt directory holds the fresh artefact -> it wins.  Had
        # the stale file been read the criterion would have failed.
        self.assertTrue(receipt["acceptance_ok"], receipt)
        self.assertIn(f"{TASK}-a01", receipt["acceptance_path"])
        self.assertNotEqual(receipt["acceptance_path"], str(stale))
        self.assertIn("COMPLETE", Path(receipt["acceptance_path"]).read_text(encoding="utf-8"))

    def test_without_an_attempt_artefact_the_base_directory_is_the_fallback(self) -> None:
        dispatcher = self.dispatcher()
        # the body writes into the base directory only (0.2.x behaviour)
        receipt = dispatcher.dispatch(
            self.card(acceptance="out.md::contains=COMPLETE"),
            agent="base",
            workdir=str(self.home),
        )
        self.assertTrue(receipt["acceptance_ok"], receipt)
        self.assertEqual(receipt["acceptance_path"], str(self.home / "out.md"))

    def test_a_cancelled_task_ignores_a_late_receipt(self) -> None:
        dispatcher = self.dispatcher()
        result: dict = {}

        def run() -> None:
            result["receipt"] = dispatcher.dispatch(
                self.card(), agent="slow", workdir=str(self.home)
            )

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.4)
        dispatcher.cancel(TASK, "cancelled while the worker was running")
        worker.join(30)

        receipt = result["receipt"]
        self.assertTrue(receipt["superseded"], receipt)
        self.assertEqual(receipt["superseded_reason"], "cancelled_during_run")
        self.assertFalse(receipt["ok"])
        self.assertIsNone(receipt["verdict"])
        self.assertEqual(receipt["raw_verdict"], "pass")

        state = self.ledger.state_of(TASK) or {}
        self.assertEqual(state["status"], "cancelled")
        self.assertIsNone(state["verdict"])
        self.assertEqual(state["late_receipts"], 1)
        self.assertEqual(infer_attempt_state(state), "outcome_unknown")

    def test_a_receipt_of_a_superseded_attempt_is_ignored(self) -> None:
        self.ledger.append("attempt", TASK, attempt_id=f"{TASK}-a01", epoch=1)
        self.ledger.append("dispatch", TASK, attempt_id=f"{TASK}-a01", epoch=1)
        self.ledger.append("running", TASK, attempt_id=f"{TASK}-a01", epoch=1)
        self.ledger.append("attempt", TASK, attempt_id=f"{TASK}-a02", epoch=2)
        self.ledger.append("dispatch", TASK, attempt_id=f"{TASK}-a02", epoch=2)
        # the receipt of the first attempt arrives late
        self.ledger.append(
            "done",
            TASK,
            verdict="pass",
            acceptance_ok=True,
            attempt_id=f"{TASK}-a01",
            epoch=1,
        )
        state = self.ledger.state_of(TASK) or {}
        self.assertNotEqual(state["status"], "done")
        self.assertEqual(state["late_receipts"], 1)
        self.assertEqual(state["late_receipt_attempts"], [f"{TASK}-a01"])


# ----------------------------------------------------------------------
# B2 — the run log never overwrites another run
# ----------------------------------------------------------------------
class RunLogTests(AttemptTestCase):
    def test_two_runs_in_the_same_second_keep_both_logs(self) -> None:
        dispatcher = self.dispatcher()
        dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        dispatcher.ledger.append("stale", TASK, reason="lease_expired")
        dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        logs = sorted((self.home / "runs").glob("*.log"))
        self.assertEqual(len(logs), 2)
        bodies = [log.read_text(encoding="utf-8") for log in logs]
        self.assertEqual(len(set(bodies)), 2)

    def test_the_log_name_carries_the_task_and_the_timestamp(self) -> None:
        dispatcher = self.dispatcher()
        dispatcher.dispatch(self.card(), agent="fake", workdir=str(self.home))
        logs = list((self.home / "runs").glob("*.log"))
        self.assertEqual(len(logs), 1)
        name = logs[0].name
        self.assertTrue(name.startswith(f"{TASK}-"), name)
        stamp = name[len(TASK) + 1 :].removesuffix(".log")
        self.assertRegex(stamp, r"^\d{8}-\d{6}(-\d+)?$")
        # the attempt id is written into the log
        self.assertIn(f"{TASK}-a01", logs[0].read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# the CLI surface of the attempt dimension
# ----------------------------------------------------------------------
class AttemptCliTests(AttemptTestCase):
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

    def test_cli_attempts_and_retry(self) -> None:
        card = self.home / "card.md"
        card.write_text(self.card(), encoding="utf-8")
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

        listed = self._run("attempts", TASK, "--json")
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        payload = json.loads(listed.stdout)
        self.assertEqual(payload["current_attempt_id"], f"{TASK}-a01")
        self.assertEqual(payload["attempt_state"], "finished")

        retried = self._run(
            "retry",
            TASK,
            "--card",
            str(card),
            "--agent",
            "fake",
            "--workdir",
            str(self.home),
            "--json",
        )
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        payload = json.loads(retried.stdout)
        self.assertEqual(payload["attempt_id"], f"{TASK}-a02")
        self.assertEqual(payload["retry_mode"], "explicit")

    def test_cli_attempts_of_an_unknown_task_exits_with_1(self) -> None:
        done = self._run("attempts", "T-20990101-99")
        self.assertEqual(done.returncode, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
