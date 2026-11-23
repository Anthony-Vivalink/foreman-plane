"""The shared contract, as a test (v0.3.0, batch B).

The contract of foreman-plane is a set of desensitised fixtures plus the
facts **both** implementations must compute from them:

* the public implementation (contract.py / ledger.py / acceptance.py) —
  driven by ``tests/contract_probe.py``;
* the shell implementation — ``scripts/shell_impl/contract_shell_impl.sh``,
  awk and sed only.

Besides the contract itself this file carries the two regressions batch B
asks for: the crash recovery of a ledger that was cut in the middle of a
run, and the robustness of the JSONL reader against half written lines and
unknown event kinds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures"
SHELL_IMPL = ROOT / "scripts" / "shell_impl" / "contract_shell_impl.sh"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tests"))

from foreman_plane.ledger import Ledger, infer_attempt_state  # noqa: E402
from contract_probe import compute  # noqa: E402


def comparable(facts: dict) -> dict:
    """The facts both implementations can compute."""
    out = json.loads(json.dumps(facts))
    for data in (out.get("ledgers") or {}).values():
        data.pop("parsed_events", None)
    return out


class ContractFixtureTests(unittest.TestCase):
    """The fixtures are the contract — both sides must agree with them."""

    def setUp(self) -> None:
        self.contract = json.loads((FIXTURES / "contract.json").read_text(encoding="utf-8"))
        self.expected = self.contract["expectations"]

    def test_the_fixtures_exist(self) -> None:
        for name in self.contract["fixtures"].values():
            self.assertTrue((FIXTURES / name).is_file(), name)
        self.assertTrue((FIXTURES / "contract.json").is_file())

    def test_the_public_implementation_matches_the_contract(self) -> None:
        facts = comparable(compute(FIXTURES))
        self.assertEqual(facts, self.expected)

    def test_the_shell_implementation_matches_the_contract(self) -> None:
        if shutil.which("bash") is None:  # pragma: no cover - bash is a given
            self.skipTest("bash is not available")
        done = subprocess.run(
            ["bash", str(SHELL_IMPL), str(FIXTURES)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        shell = comparable(json.loads(done.stdout))
        self.assertEqual(shell, self.expected)

    def test_both_implementations_agree_with_each_other(self) -> None:
        if shutil.which("bash") is None:  # pragma: no cover - bash is a given
            self.skipTest("bash is not available")
        done = subprocess.run(
            ["bash", str(SHELL_IMPL), str(FIXTURES)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(comparable(json.loads(done.stdout)), comparable(compute(FIXTURES)))

    def test_the_fixture_directory_holds_no_host_path(self) -> None:
        for path in sorted(FIXTURES.glob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            # the literal is built at runtime: the public repository must
            # not carry the absolute path of any host
            for forbidden in ("/" + "root" + "/", "/" + "home" + "/"):
                self.assertNotIn(forbidden, text, path.name)

    def test_the_contract_check_script_passes(self) -> None:
        if shutil.which("bash") is None:  # pragma: no cover - bash is a given
            self.skipTest("bash is not available")
        done = subprocess.run(
            ["bash", str(ROOT / "scripts" / "contract_fixture_check.sh"), "--mode", "cross"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("contract OK", done.stdout)


class CrashRecoveryRegressionTests(unittest.TestCase):
    """The ledger of a worker that died mid run."""

    def test_a_dead_worker_leaves_an_interrupted_attempt(self) -> None:
        ledger = Ledger(FIXTURES / "ledger_attempt.jsonl")
        state = ledger.state_of("T-20261123-43")
        assert state is not None
        # the lease expired, there is no terminal event -> interrupted
        self.assertEqual(state["status"], "running")
        self.assertEqual(
            infer_attempt_state(state, self.contract_now()), "interrupted"
        )

    def test_a_recovered_task_carries_its_steps_and_its_last_attempt(self) -> None:
        ledger = Ledger(FIXTURES / "ledger_attempt.jsonl")
        state = ledger.state_of("T-20261123-41")
        assert state is not None
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["attempt_id"], "T-20261123-41-a02")
        self.assertEqual(state["epoch"], 2)
        self.assertEqual(ledger.completed_steps("T-20261123-41"), ["step_a"])
        self.assertEqual(state["recoveries"], 1)
        attempts = ledger.attempts("T-20261123-41")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["attempt_id"], "T-20261123-41-a01")
        self.assertEqual(attempts[1]["attempt_id"], "T-20261123-41-a02")
        self.assertEqual(attempts[1]["acceptance_sha256"], state["acceptance_evidence"]["sha256"])

    def test_a_late_receipt_does_not_close_a_cancelled_task(self) -> None:
        ledger = Ledger(FIXTURES / "ledger_attempt.jsonl")
        state = ledger.state_of("T-20261123-44")
        assert state is not None
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["late_receipts"], 1)
        self.assertEqual(
            infer_attempt_state(state, self.contract_now()), "outcome_unknown"
        )

    def contract_now(self):
        from foreman_plane.ledger import parse_timestamp

        return parse_timestamp(self.contract_time())

    def contract_time(self) -> str:
        return json.loads((FIXTURES / "contract.json").read_text(encoding="utf-8"))[
            "reference_time"
        ]


class JsonlRobustnessRegressionTests(unittest.TestCase):
    """A ledger that was cut in the middle of a write."""

    def test_a_half_line_is_ignored(self) -> None:
        path = FIXTURES / "ledger_corrupt.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(not line.strip().endswith("}") for line in lines))
        ledger = Ledger(path)
        events = ledger.events()
        self.assertEqual(len(events), 3)

    def test_an_unknown_event_kind_is_ignored_safely(self) -> None:
        ledger = Ledger(FIXTURES / "ledger_corrupt.jsonl")
        kinds = [record.get("event") for record in ledger.events()]
        self.assertIn("quantum_flux", kinds)
        state = ledger.state_of("T-20261123-52")
        assert state is not None
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["verdict"], "pass")

    def test_the_reader_survives_a_truncated_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            source = (FIXTURES / "ledger_attempt.jsonl").read_text(encoding="utf-8")
            path.write_text(source[: len(source) // 2], encoding="utf-8")
            ledger = Ledger(path)
            events = ledger.events()
            self.assertGreater(len(events), 0)
            for state in ledger.states().values():
                # a truncated ledger is still readable: the events that were
                # written completely are folded, the half line is skipped
                self.assertIn(
                    state["status"],
                    ("unknown", "dispatched", "running", "done", "stale", "blocked", "cancelled"),
                )

    def test_a_ledger_without_a_file_is_empty_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "missing.jsonl")
            self.assertEqual(ledger.events(), [])
            self.assertEqual(ledger.states(), {})
            self.assertIsNone(ledger.state_of("T-20261123-99"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
