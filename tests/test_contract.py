"""Contract parsing tests (SPEC §10)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from foreman.contract import is_valid_task_id, parse_task_card  # noqa: E402

FULL_CARD = """【id】T-20261109-20
【depends_on】T-20261109-01
【needs_api】/tmp/api/report.json
【验收】out/report.md::min_bytes=100
请实现并产出报告。
"""


class ContractParsingTests(unittest.TestCase):
    def test_four_markers_are_parsed(self) -> None:
        card = parse_task_card(FULL_CARD)
        self.assertEqual(card.task_id, "T-20261109-20")
        self.assertEqual(card.depends_on, ["T-20261109-01"])
        self.assertEqual(card.needs_api, ["/tmp/api/report.json"])
        self.assertEqual(card.acceptance, "out/report.md::min_bytes=100")
        self.assertFalse(card.depends_on_unparsed)
        self.assertFalse(card.needs_api_unparsed)
        self.assertFalse(card.acceptance_unparsed)

    def test_half_width_and_case_variants(self) -> None:
        text = (
            "[ID] T-20261109-20\n"
            "[depends_on]: T-20261109-01\n"
            "【Needs_Api】 /tmp/api/report.json\n"
            "[验收]: out/report.md\n"
        )
        card = parse_task_card(text)
        self.assertEqual(card.task_id, "T-20261109-20")
        self.assertEqual(card.depends_on, ["T-20261109-01"])
        self.assertEqual(card.needs_api, ["/tmp/api/report.json"])
        self.assertEqual(card.acceptance, "out/report.md")
        self.assertFalse(card.depends_on_unparsed)
        self.assertFalse(card.needs_api_unparsed)
        self.assertFalse(card.acceptance_unparsed)

    def test_canonical_half_width_markers(self) -> None:
        text = (
            "[id] T-20261109-20\n"
            "[depends_on] T-20261109-01\n"
            "[needs_api] /tmp/api/report.json\n"
            "[acceptance] out/report.md::min_bytes=100\n"
        )
        card = parse_task_card(text)
        self.assertEqual(card.task_id, "T-20261109-20")
        self.assertEqual(card.depends_on, ["T-20261109-01"])
        self.assertEqual(card.needs_api, ["/tmp/api/report.json"])
        self.assertEqual(card.acceptance, "out/report.md::min_bytes=100")
        self.assertFalse(card.depends_on_unparsed)
        self.assertFalse(card.needs_api_unparsed)
        self.assertFalse(card.acceptance_unparsed)

    def test_acceptance_alias_does_not_fire_on_english_prose(self) -> None:
        card = parse_task_card("[acceptance] out/report.md")
        self.assertEqual(card.acceptance, "out/report.md")
        self.assertFalse(card.acceptance_unparsed)

        prose = parse_task_card(
            "acceptance criteria: the agent should describe its own work\n"
            "[id] T-20261109-20\n"
        )
        self.assertIsNone(prose.acceptance)
        self.assertFalse(prose.acceptance_unparsed)

    def test_full_width_space_is_tolerated(self) -> None:
        card = parse_task_card("【id】　T-20261109-20\n【depends_on】　T-20261109-01")
        self.assertEqual(card.task_id, "T-20261109-20")
        self.assertEqual(card.depends_on, ["T-20261109-01"])

    def test_depends_on_marker_without_legal_token_is_unparsed(self) -> None:
        card = parse_task_card("【id】T-20261109-20\n【depends_on】等 A 完成")
        self.assertEqual(card.depends_on, [])
        self.assertTrue(card.depends_on_unparsed)

    def test_depends_on_keeps_legal_id_and_ignores_annotation(self) -> None:
        card = parse_task_card("【id】T-20261109-20\n【depends_on】T-20261109-01（等 A 完成）")
        self.assertEqual(card.depends_on, ["T-20261109-01"])
        self.assertFalse(card.depends_on_unparsed)

    def test_needs_api_path_with_chinese_is_unparsed(self) -> None:
        card = parse_task_card("【id】T-20261109-20\n【needs_api】/tmp/报告.json")
        self.assertEqual(card.needs_api, [])
        self.assertTrue(card.needs_api_unparsed)

    def test_acceptance_path_with_space_is_unparsed(self) -> None:
        card = parse_task_card("【id】T-20261109-20\n【验收】out/my report.md")
        self.assertTrue(card.acceptance_unparsed)

    def test_invalid_calendar_date_is_not_a_task_id(self) -> None:
        card = parse_task_card("【id】T-20260230-01")
        self.assertIsNone(card.task_id)
        self.assertFalse(is_valid_task_id("T-20260230-01"))
        self.assertTrue(is_valid_task_id("T-20261109-01"))

    def test_missing_markers_leave_all_unparsed_flags_false(self) -> None:
        card = parse_task_card("只是普通任务描述，没有任何标记。")
        self.assertIsNone(card.task_id)
        self.assertEqual(card.depends_on, [])
        self.assertEqual(card.needs_api, [])
        self.assertIsNone(card.acceptance)
        self.assertFalse(card.depends_on_unparsed)
        self.assertFalse(card.needs_api_unparsed)
        self.assertFalse(card.acceptance_unparsed)

    def test_needs_api_id_prefix_form(self) -> None:
        card = parse_task_card("【needs_api】T-20261109-01#/tmp/api/report.json")
        self.assertEqual(card.needs_api, ["T-20261109-01#/tmp/api/report.json"])
        self.assertFalse(card.needs_api_unparsed)

    def test_parsing_never_raises(self) -> None:
        for value in (None, "", "【】", "【depends_on】", "【验收】::", "【id】T-"):
            card = parse_task_card(value)  # type: ignore[arg-type]
            self.assertIsNotNone(card)

    def test_multiple_need_api_entries(self) -> None:
        card = parse_task_card("【needs_api】/tmp/a.json\n【needs_api】/tmp/b.json")
        self.assertEqual(card.needs_api, ["/tmp/a.json", "/tmp/b.json"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
