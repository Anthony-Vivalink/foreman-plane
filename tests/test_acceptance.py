"""Acceptance tests (SPEC §10)."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from foreman.acceptance import verify  # noqa: E402


class AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, body: str) -> Path:
        path = self.base / name
        path.write_text(body, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    def test_none_acceptance_is_ok(self) -> None:
        result = verify(None)
        self.assertTrue(result.ok)
        self.assertIsNone(result.reason)
        self.assertEqual(result.checks, [])

    def test_existing_artefact_is_accepted(self) -> None:
        self._write("report.md", "hello")
        result = verify("report.md", base_dir=self.base)
        self.assertTrue(result.ok)
        self.assertIsNone(result.reason)

    def test_min_bytes(self) -> None:
        self._write("report.md", "0123456789")
        self.assertTrue(verify("report.md::min_bytes=10", base_dir=self.base).ok)
        self.assertFalse(verify("report.md::min_bytes=11", base_dir=self.base).ok)

    def test_sha256(self) -> None:
        path = self._write("report.md", "hello")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(verify(f"report.md::sha256={digest}", base_dir=self.base).ok)
        self.assertFalse(verify("report.md::sha256=" + "0" * 64, base_dir=self.base).ok)

    def test_contains(self) -> None:
        self._write("report.md", "the quick brown fox")
        self.assertTrue(verify("report.md::contains=quick brown", base_dir=self.base).ok)
        self.assertFalse(verify("report.md::contains=slow turtle", base_dir=self.base).ok)

    def test_exit_code(self) -> None:
        self._write("report.md", "hello")
        self.assertTrue(verify("report.md::exit_code=0", exit_code=0, base_dir=self.base).ok)
        self.assertFalse(verify("report.md::exit_code=0", exit_code=2, base_dir=self.base).ok)

    def test_missing_artefact_fails(self) -> None:
        result = verify("missing.md", base_dir=self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "artifact_missing")

    def test_criteria_are_anded(self) -> None:
        self._write("report.md", "hello")
        ok = verify("report.md::min_bytes=5::contains=hello", base_dir=self.base)
        self.assertTrue(ok.ok)
        bad = verify("report.md::min_bytes=50::contains=hello", base_dir=self.base)
        self.assertFalse(bad.ok)
        self.assertEqual(bad.reason, "acceptance_criterion_failed")

    def test_invalid_path_is_rejected(self) -> None:
        result = verify("out/my report.md", base_dir=self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "acceptance_invalid")

    def test_unknown_criterion_fails_closed(self) -> None:
        self._write("report.md", "hello")
        result = verify("report.md::mystery=1", base_dir=self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "acceptance_unknown_criterion")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
