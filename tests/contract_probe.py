#!/usr/bin/env python3
"""Compute the shared contract facts of a fixture directory.

This probe drives the **public implementation** of foreman-plane:
``contract.py`` parses the task card, ``ledger.py`` folds the JSONL
ledgers, ``acceptance.py`` verifies the artefacts.  The shell
implementation of a local plane computes exactly the same facts from
exactly the same fixtures — ``scripts/contract_fixture_check.sh``
compares the two, so the contract is a test and not a document.

Usage:
    python3 tests/contract_probe.py --fixtures tests/fixtures [--json]

The output is one JSON object on stdout.  ``--json`` is the default; the
plain output is the same document, pretty printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from foreman_plane.acceptance import verify  # noqa: E402
from foreman_plane.contract import parse_task_card, split_acceptance  # noqa: E402
from foreman_plane.ledger import Ledger, infer_attempt_state, parse_timestamp  # noqa: E402


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def card_facts(fixtures: Path, name: str) -> dict:
    """The facts of the task card fixture."""
    payload = _read_json(fixtures / name)
    lines = payload.get("lines") or []
    text = payload.get("text")
    if text is None:
        text = "\n".join(str(line) for line in lines) + "\n"
    card = parse_task_card(text)
    path, criteria = split_acceptance(card.acceptance)
    return {
        "task_id": card.task_id,
        "depends_on": list(card.depends_on),
        "needs_api": list(card.needs_api),
        "acceptance_path": path,
        "acceptance_criteria": [f"{key}={value}" for key, value in criteria],
        "depends_on_unparsed": bool(card.depends_on_unparsed),
        "needs_api_unparsed": bool(card.needs_api_unparsed),
        "acceptance_unparsed": bool(card.acceptance_unparsed),
    }


def ledger_facts(fixtures: Path, name: str, reference_time: str) -> dict:
    """The facts of one JSONL ledger fixture."""
    path = fixtures / name
    raw = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in raw if line.strip()]
    usable = [
        line for line in lines if line.strip().startswith("{") and line.strip().endswith("}")
    ]
    ledger = Ledger(path)
    moment = parse_timestamp(reference_time)
    tasks: dict[str, dict] = {}
    for task_id, state in ledger.states().items():
        tasks[task_id] = {
            "status": state.get("status"),
            "attempt_id": state.get("attempt_id"),
            "epoch": state.get("epoch"),
            "completed_steps": ledger.completed_steps(task_id),
            "attempt_state": infer_attempt_state(state, moment),
            "late_receipts": int(state.get("late_receipts") or 0),
        }
    return {
        "usable_lines": len(usable),
        "ignored_lines": len(lines) - len(usable),
        "parsed_events": len(ledger.events()),
        "tasks": tasks,
    }


def acceptance_facts(fixtures: Path, name: str) -> list[dict]:
    """The facts of the acceptance sheet fixture."""
    sheet = _read_json(fixtures / name)
    out: list[dict] = []
    for case in sheet.get("cases") or []:
        result = verify(case.get("acceptance"), base_dir=str(fixtures))
        out.append(
            {
                "name": case.get("name"),
                "ok": bool(result.ok),
                "reason": result.reason,
                "sha256": result.sha256,
                "size_bytes": result.size_bytes,
            }
        )
    return out


def compute(fixtures: Path) -> dict:
    """All the facts of a fixture directory."""
    manifest = _read_json(fixtures / "manifest.json")
    names = manifest.get("fixtures") or {}
    reference_time = manifest.get("reference_time") or ""
    ledgers = {
        key: ledger_facts(fixtures, names[key], reference_time)
        for key in ("ledger_attempt", "ledger_legacy", "ledger_corrupt")
        if key in names
    }
    return {
        "card": card_facts(fixtures, names["card"]),
        "ledgers": ledgers,
        "acceptance": acceptance_facts(fixtures, names["acceptance"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        default=str(ROOT / "tests" / "fixtures"),
        help="directory with the contract fixtures",
    )
    parser.add_argument("--json", action="store_true", help="compact JSON output")
    args = parser.parse_args(argv)
    facts = compute(Path(args.fixtures).expanduser())
    if args.json:
        print(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(facts, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
