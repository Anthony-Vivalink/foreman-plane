"""Objective artefact acceptance (SPEC §5).

The acceptance string is ``path`` or ``path::key=value::key=value``.

Supported criteria:

* ``min_bytes=N``    file size >= N
* ``sha256=<hex>``   sha256 of the file content equals the digest
* ``contains=<sub>`` file content contains the substring
* ``exit_code=N``    execution body exit code equals N

Criteria are ANDed.  An unknown criterion fails closed.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from .contract import ACCEPT_PATH_RE, split_acceptance

__all__ = ["AcceptanceResult", "verify", "parse_acceptance"]


@dataclass
class AcceptanceResult:
    """Result of one acceptance verification (SPEC §5)."""

    ok: bool
    path: str | None = None
    checks: list[dict] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "path": self.path,
            "checks": list(self.checks),
            "reason": self.reason,
        }


def parse_acceptance(value: str | None) -> tuple[str | None, list[tuple[str, str]]]:
    """Public alias of the acceptance string parser."""
    return split_acceptance(value)


def _as_int(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def verify(
    acceptance: str | None,
    exit_code: int | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> AcceptanceResult:
    """Verify an artefact against an acceptance string."""
    if acceptance is None or not str(acceptance).strip():
        return AcceptanceResult(ok=True, path=None, checks=[], reason=None)

    raw_path, criteria = split_acceptance(acceptance)
    if not raw_path or not ACCEPT_PATH_RE.match(raw_path):
        return AcceptanceResult(
            ok=False,
            path=raw_path,
            checks=[],
            reason="acceptance_invalid",
        )

    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        base = Path(base_dir).expanduser() if base_dir else Path.cwd()
        target = base / target

    checks: list[dict] = []
    if not target.exists():
        checks.append({"name": "exists", "expected": True, "actual": False, "ok": False})
        return AcceptanceResult(ok=False, path=str(target), checks=checks, reason="artifact_missing")
    checks.append({"name": "exists", "expected": True, "actual": True, "ok": True})
    if not target.is_file():
        return AcceptanceResult(
            ok=False,
            path=str(target),
            checks=checks,
            reason="artifact_not_a_file",
        )

    try:
        data = target.read_bytes()
    except OSError:
        return AcceptanceResult(
            ok=False,
            path=str(target),
            checks=checks,
            reason="artifact_unreadable",
        )
    text = data.decode("utf-8", errors="replace")

    ok = True
    reason: str | None = None
    for name, expected in criteria:
        key = name.strip().lower()
        if key == "min_bytes":
            wanted = _as_int(expected)
            actual = len(data)
            passed = wanted is not None and actual >= wanted
            record_expected = wanted
        elif key == "sha256":
            actual = hashlib.sha256(data).hexdigest()
            passed = actual.lower() == str(expected).strip().lower()
            record_expected = str(expected).strip().lower()
        elif key == "contains":
            actual = None
            passed = str(expected) in text
            record_expected = str(expected)
        elif key == "exit_code":
            wanted = _as_int(expected)
            actual = exit_code
            passed = wanted is not None and exit_code is not None and int(exit_code) == wanted
            record_expected = wanted
        else:
            checks.append(
                {"name": key, "expected": str(expected), "actual": None, "ok": False}
            )
            ok = False
            reason = reason or "acceptance_unknown_criterion"
            continue
        checks.append(
            {"name": key, "expected": record_expected, "actual": actual, "ok": bool(passed)}
        )
        if not passed:
            ok = False
            reason = reason or "acceptance_criterion_failed"

    return AcceptanceResult(ok=ok, path=str(target), checks=checks, reason=reason)
