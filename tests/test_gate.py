"""Gate tests (SPEC §10)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from foreman.contract import parse_task_card  # noqa: E402
from foreman.gate import evaluate_gate  # noqa: E402
from foreman.ledger import Ledger  # noqa: E402


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.ledger = Ledger(self.home / "ledger.jsonl")
        self.addCleanup(self._tmp.cleanup)

    def _passed(self, task_id: str) -> None:
        self.ledger.append("dispatch", task_id)
        self.ledger.append("running", task_id, agent="stub")
        self.ledger.append("done", task_id, verdict="pass", acceptance_ok=True, exit_code=0)

    def _decide(self, text: str):
        return evaluate_gate(parse_task_card(text), self.ledger)

    # ------------------------------------------------------------------
    def test_task_id_missing_blocks(self) -> None:
        decision = self._decide("没有任务号的卡")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "task_id_missing")

    def test_dependency_unknown_blocks(self) -> None:
        decision = self._decide("【id】T-20261109-20\n【depends_on】T-20261109-99")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_unknown")
        self.assertEqual(decision.missing, ["T-20261109-99"])

    def test_dependency_pending_blocks(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        decision = self._decide("【id】T-20261109-20\n【depends_on】T-20261109-01")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_pending")
        self.assertEqual(decision.missing, ["T-20261109-01"])

    def test_dependency_failed_blocks(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.ledger.append("done", "T-20261109-01", verdict="fail")
        decision = self._decide("【id】T-20261109-20\n【depends_on】T-20261109-01")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_failed")

    def test_dependency_unaccepted_blocks(self) -> None:
        self.ledger.append("dispatch", "T-20261109-01")
        self.ledger.append("done", "T-20261109-01", verdict="pass", acceptance_ok=False)
        decision = self._decide("【id】T-20261109-20\n【depends_on】T-20261109-01")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_unaccepted")

    def test_dependency_unparsed_blocks(self) -> None:
        decision = self._decide("【id】T-20261109-20\n【depends_on】等 A 完成")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_unparsed")

    def test_self_dependency_is_a_cycle(self) -> None:
        decision = self._decide("【id】T-20261109-20\n【depends_on】T-20261109-20")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_cycle")

    def test_indirect_cycle_blocks(self) -> None:
        # B depends on A, the card for A depends on B
        self.ledger.append("dispatch", "T-20261109-02", depends_on=["T-20261109-01"])
        decision = self._decide("【id】T-20261109-01\n【depends_on】T-20261109-02")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dependency_cycle")

    def test_all_dependencies_passed_allows(self) -> None:
        self._passed("T-20261109-01")
        decision = self._decide("【id】T-20261109-20\n【depends_on】T-20261109-01")
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.reason)
        self.assertEqual(decision.missing, [])

    def test_missing_soft_dependency_only_sets_pending(self) -> None:
        decision = self._decide(
            "【id】T-20261109-20\n【needs_api】" + str(self.home / "missing.json")
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.needs_api_pending)

    def test_present_soft_dependency_is_not_pending(self) -> None:
        artefact = self.home / "ready.json"
        artefact.write_text("{}", encoding="utf-8")
        decision = self._decide("【id】T-20261109-20\n【needs_api】" + str(artefact))
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.needs_api_pending)

    def test_needs_api_id_prefix_uses_the_path_part(self) -> None:
        artefact = self.home / "ready.json"
        artefact.write_text("{}", encoding="utf-8")
        decision = self._decide(
            "【id】T-20261109-20\n【needs_api】T-20261109-01#" + str(artefact)
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.needs_api_pending)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
