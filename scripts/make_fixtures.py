"""Generate the desensitised contract fixtures of foreman-plane.

Run once:  python3 scripts/make_fixtures.py
The generated files live in tests/fixtures/ and are committed — the
generator is a convenience, the fixtures are the contract.

The event records are written in exactly the shape ``Ledger.append``
produces, but with a fixed timestamp: a fixture must not change when it is
regenerated.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIX = ROOT / "tests" / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

REF_NOW = "2026-11-24T00:00:00+00:00"


def append_at(path, ts, event, task_id, actor="foreman", **fields):
    """Append one event with a fixed timestamp.

    The record shape is exactly the shape of ``Ledger.append`` — the
    fixtures must look like a real ledger, byte for byte.
    """
    detail = dict(fields)
    record = {"ts": ts, "event": event, "task_id": task_id, "actor": actor}
    record.update(detail)
    record["detail"] = detail
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------
# the artefact the acceptance sheet verifies
# ----------------------------------------------------------------------
ARTIFACT = FIX / "report.json"
ARTIFACT.write_text(
    json.dumps({"status": "done", "produced_by": "foreman-plane fixture"}, indent=2)
    + "\n",
    encoding="utf-8",
)
DIGEST = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
SIZE = ARTIFACT.stat().st_size

# ----------------------------------------------------------------------
# the task card
# ----------------------------------------------------------------------
CARD_LINES = [
    "[id] T-20261123-41",
    "[depends_on] T-20261123-40",
    "[needs_api] data/input.txt",
    "[acceptance] report.json::min_bytes=10::contains=done",
    "Produce the report artefact.",
]
CARD = {
    "name": "basic",
    "task_id": "T-20261123-41",
    "depends_on": ["T-20261123-40"],
    "needs_api": ["data/input.txt"],
    "acceptance": "report.json::min_bytes=10::contains=done",
    "lines": CARD_LINES,
    "text": "\n".join(CARD_LINES) + "\n",
}
(FIX / "task_card_basic.json").write_text(
    json.dumps(CARD, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# ----------------------------------------------------------------------
# the attempt aware ledger
# ----------------------------------------------------------------------
LEDGER = FIX / "ledger_attempt.jsonl"
LEDGER.unlink(missing_ok=True)

A1 = "T-20261123-41-a01"
A2 = "T-20261123-41-a02"
emit = lambda ts, event, task_id, **fields: append_at(LEDGER, ts, event, task_id, **fields)  # noqa: E731

emit("2026-11-23T10:00:00+00:00", "attempt", "T-20261123-41",
     attempt_id=A1, epoch=1, reason="dispatch",
     attempt_dir="work/attempts/" + A1,
     lease_expires_at="2026-11-23T10:15:00+00:00", lease_s=900)
emit("2026-11-23T10:00:01+00:00", "dispatch", "T-20261123-41",
     depends_on=["T-20261123-40"], needs_api=["data/input.txt"],
     acceptance="report.json::min_bytes=10", agent="fake",
     attempt_id=A1, epoch=1, attempt_dir="work/attempts/" + A1,
     lease_expires_at="2026-11-23T10:15:00+00:00", lease_s=900)
emit("2026-11-23T10:00:02+00:00", "running", "T-20261123-41",
     agent="fake", attempt_id=A1, epoch=1,
     lease_expires_at="2026-11-23T10:15:00+00:00", lease_s=900)
emit("2026-11-23T10:05:00+00:00", "checkpoint", "T-20261123-41",
     step="step_a", artifact="work/partial.txt")
emit("2026-11-23T10:20:00+00:00", "stale", "T-20261123-41",
     reason="lease_expired", lease_expires_at="2026-11-23T10:15:00+00:00",
     previous_status="running")
emit("2026-11-23T10:30:00+00:00", "attempt", "T-20261123-41",
     attempt_id=A2, epoch=2, reason="recovered", previous_attempt_id=A1,
     attempt_dir="work/attempts/" + A2,
     lease_expires_at="2026-11-23T10:45:00+00:00", lease_s=900)
emit("2026-11-23T10:30:01+00:00", "dispatch", "T-20261123-41",
     depends_on=["T-20261123-40"], needs_api=["data/input.txt"],
     acceptance="report.json::min_bytes=10", agent="fake",
     attempt_id=A2, epoch=2, attempt_dir="work/attempts/" + A2,
     lease_expires_at="2026-11-23T10:45:00+00:00", lease_s=900)
emit("2026-11-23T10:30:02+00:00", "running", "T-20261123-41",
     agent="fake", attempt_id=A2, epoch=2,
     lease_expires_at="2026-11-23T10:45:00+00:00", lease_s=900)
emit("2026-11-23T10:35:00+00:00", "done", "T-20261123-41",
     verdict="pass", acceptance_ok=True, exit_code=0, agent="fake",
     attempt_id=A2, epoch=2, attempt_dir="work/attempts/" + A2,
     acceptance_path="work/attempts/" + A2 + "/report.json",
     acceptance_sha256=DIGEST, acceptance_size=SIZE)

# a task whose lease is alive at the reference time
emit("2026-11-23T23:50:00+00:00", "attempt", "T-20261123-42",
     attempt_id="T-20261123-42-a01", epoch=1, reason="dispatch",
     attempt_dir="work/attempts/T-20261123-42-a01",
     lease_expires_at="2026-11-24T00:30:00+00:00", lease_s=900)
emit("2026-11-23T23:50:01+00:00", "dispatch", "T-20261123-42",
     agent="fake", attempt_id="T-20261123-42-a01", epoch=1,
     attempt_dir="work/attempts/T-20261123-42-a01",
     lease_expires_at="2026-11-24T00:30:00+00:00", lease_s=900)
emit("2026-11-23T23:50:02+00:00", "running", "T-20261123-42",
     agent="fake", attempt_id="T-20261123-42-a01", epoch=1,
     lease_expires_at="2026-11-24T00:30:00+00:00", lease_s=900)

# a task whose lease expired without a terminal event
emit("2026-11-23T08:00:00+00:00", "attempt", "T-20261123-43",
     attempt_id="T-20261123-43-a01", epoch=1, reason="dispatch",
     attempt_dir="work/attempts/T-20261123-43-a01",
     lease_expires_at="2026-11-23T08:15:00+00:00", lease_s=900)
emit("2026-11-23T08:00:01+00:00", "dispatch", "T-20261123-43",
     agent="fake", attempt_id="T-20261123-43-a01", epoch=1,
     attempt_dir="work/attempts/T-20261123-43-a01",
     lease_expires_at="2026-11-23T08:15:00+00:00", lease_s=900)
emit("2026-11-23T08:00:02+00:00", "running", "T-20261123-43",
     agent="fake", attempt_id="T-20261123-43-a01", epoch=1,
     lease_expires_at="2026-11-23T08:15:00+00:00", lease_s=900)

# a task that was cancelled while the worker was running: the receipt of
# the worker arrives late and must not close the task
emit("2026-11-23T11:00:00+00:00", "attempt", "T-20261123-44",
     attempt_id="T-20261123-44-a01", epoch=1, reason="dispatch",
     attempt_dir="work/attempts/T-20261123-44-a01",
     lease_expires_at="2026-11-23T11:15:00+00:00", lease_s=900)
emit("2026-11-23T11:00:01+00:00", "dispatch", "T-20261123-44",
     agent="fake", attempt_id="T-20261123-44-a01", epoch=1,
     attempt_dir="work/attempts/T-20261123-44-a01",
     lease_expires_at="2026-11-23T11:15:00+00:00", lease_s=900)
emit("2026-11-23T11:00:02+00:00", "running", "T-20261123-44",
     agent="fake", attempt_id="T-20261123-44-a01", epoch=1,
     lease_expires_at="2026-11-23T11:15:00+00:00", lease_s=900)
emit("2026-11-23T11:10:00+00:00", "cancelled", "T-20261123-44",
     reason="cancelled while the worker ran")
emit("2026-11-23T11:12:00+00:00", "done", "T-20261123-44",
     verdict="pass", acceptance_ok=True, exit_code=0, agent="fake",
     attempt_id="T-20261123-44-a01", epoch=1,
     acceptance_path="work/attempts/T-20261123-44-a01/report.json",
     acceptance_sha256=DIGEST)

# ----------------------------------------------------------------------
# the legacy ledger (0.2.x — no attempt id, no epoch, no lease)
# ----------------------------------------------------------------------
LEGACY = FIX / "ledger_legacy.jsonl"
LEGACY.unlink(missing_ok=True)
append_at(LEGACY, "2026-11-20T08:00:00+00:00", "dispatch", "T-20261123-50",
           agent="old", depends_on=[], needs_api=[])
append_at(LEGACY, "2026-11-20T08:00:01+00:00", "running", "T-20261123-50", agent="old")
append_at(LEGACY, "2026-11-20T08:00:02+00:00", "done", "T-20261123-50",
           verdict="pass", acceptance_ok=True, exit_code=0, agent="old")
append_at(LEGACY, "2026-11-20T09:00:00+00:00", "dispatch", "T-20261123-51",
           agent="old", depends_on=[], needs_api=[])
append_at(LEGACY, "2026-11-20T09:00:01+00:00", "running", "T-20261123-51", agent="old")

# ----------------------------------------------------------------------
# the corrupt ledger: a half line, a broken line, an unknown event kind
# ----------------------------------------------------------------------
CORRUPT = FIX / "ledger_corrupt.jsonl"
CORRUPT.unlink(missing_ok=True)
append_at(CORRUPT, "2026-11-23T12:00:00+00:00", "dispatch", "T-20261123-52",
           agent="fake", depends_on=[], needs_api=[])
append_at(CORRUPT, "2026-11-23T12:05:00+00:00", "done", "T-20261123-52",
           verdict="pass", acceptance_ok=True, exit_code=0, agent="fake")
text = CORRUPT.read_text(encoding="utf-8")
lines = text.splitlines()
broken = [
    lines[0],
    '{"ts": "2026-11-23T12:00:00+00:00", "event": "running", "task_id": "T-2026112',
    '{"ts": "2026-11-23T12:00:01+00:00", "event": "quantum_flux", "task_id": "T-20261123-52", "actor": "foreman", "detail": {}}',
    "this line is not json at all",
    lines[1],
]
CORRUPT.write_text("\n".join(broken) + "\n", encoding="utf-8")

# ----------------------------------------------------------------------
# the acceptance sheet
# ----------------------------------------------------------------------
SHEET = {
    "artifact": "report.json",
    "cases": [
        {
            "name": "min_bytes_ok",
            "acceptance": "report.json::min_bytes=10",
            "expect_ok": True,
            "expect_reason": None,
        },
        {
            "name": "contains_ok",
            "acceptance": "report.json::contains=done",
            "expect_ok": True,
            "expect_reason": None,
        },
        {
            "name": "sha256_ok",
            "acceptance": "report.json::sha256=" + DIGEST,
            "expect_ok": True,
            "expect_reason": None,
        },
        {
            "name": "missing_artifact",
            "acceptance": "absent.json",
            "expect_ok": False,
            "expect_reason": "artifact_missing",
        },
        {
            "name": "criterion_failed",
            "acceptance": "report.json::contains=absent-marker",
            "expect_ok": False,
            "expect_reason": "acceptance_criterion_failed",
        },
        {
            "name": "unknown_criterion",
            "acceptance": "report.json::colour=red",
            "expect_ok": False,
            "expect_reason": "acceptance_unknown_criterion",
        },
    ],
}
(FIX / "acceptance_sheet.json").write_text(
    json.dumps(SHEET, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# ----------------------------------------------------------------------
# the fixture manifest
# ----------------------------------------------------------------------
MANIFEST = {
    "version": "1.0",
    "reference_time": REF_NOW,
    "fixtures": {
        "card": "task_card_basic.json",
        "ledger_attempt": "ledger_attempt.jsonl",
        "ledger_legacy": "ledger_legacy.jsonl",
        "ledger_corrupt": "ledger_corrupt.jsonl",
        "acceptance": "acceptance_sheet.json",
        "artifact": "report.json",
    },
}
(FIX / "manifest.json").write_text(
    json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# ----------------------------------------------------------------------
# the contract itself: the facts both implementations must compute
# ----------------------------------------------------------------------
sys.path.insert(0, str(ROOT / "tests"))
from contract_probe import compute  # noqa: E402

facts = compute(FIX)
for data in facts["ledgers"].values():
    data.pop("parsed_events", None)
CONTRACT = {
    "version": "1.0",
    "reference_time": REF_NOW,
    "fixtures": MANIFEST["fixtures"],
    "expectations": facts,
}
(FIX / "contract.json").write_text(
    json.dumps(CONTRACT, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

print("fixtures written to", FIX)
print("artifact sha256:", DIGEST, "size:", SIZE)
