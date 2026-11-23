"""Append-only audit ledger (SPEC §3).

One JSON object per line, only ever appended.  Events are folded into a
per-task state so that the gate can ask "is this dependency satisfied?"
without rescanning the whole file.

Since v0.3.0 the ledger also carries the *failure & recovery* events:
``heartbeat`` (lease refresh), ``stale`` (lease expired, task reclaimable),
``checkpoint`` (one step finished) and ``retry`` (one attempt failed and
will be repeated).  Unknown event kinds are ignored by the fold, so an old
reader never crashes on an event written by a newer plane.

Since v0.3.0 (batch B) the ledger also carries the **attempt** dimension:

* every dispatch allocates an ``attempt_id`` and an ``epoch``; both are
  written into the ``dispatch`` event and into the new ``attempt`` event;
* a re-dispatch of the same task id allocates a **new** attempt, so the
  artefacts of two attempts never mix;
* a receipt (``done``) that belongs to a superseded attempt — the task was
  cancelled while it ran, or a newer attempt was started meanwhile — is
  **not** allowed to mark the task as done.  It is counted as a *late
  receipt* instead, which is what makes the "cancelled but the worker came
  back" race observable instead of silently wrong.

A ledger written by 0.2.x has no ``attempt_id`` / ``epoch`` field at all.
Such a task is *legacy*: the fold keeps the exact 0.2.x behaviour (a ``done``
event still wins over a ``cancelled`` one), no attempt is invented, and the
attempt state is reported as *unknown* rather than as a failure.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import UnknownTaskError

__all__ = [
    "Ledger",
    "TERMINAL_STATUSES",
    "NON_TERMINAL_STATUSES",
    "RECOVERABLE_STATUSES",
    "LEASE_STATUSES",
    "ATTEMPT_STATES",
    "utcnow",
    "iso",
    "parse_timestamp",
    "lease_remaining_s",
    "lease_expired",
    "task_age_s",
    "annotate_state",
    "infer_attempt_state",
    "attempt_id_for",
]

TERMINAL_STATUSES = ("done", "cancelled")
NON_TERMINAL_STATUSES = ("dispatched", "running", "blocked")

#: v0.3.0 — statuses a task can leave again by dispatching it anew.
#: ``blocked`` never entered the plane (gate refused it), ``stale`` lost its
#: lease.  Both stay re-dispatchable, otherwise the task graph deadlocks.
RECOVERABLE_STATUSES = ("blocked", "stale")

#: statuses that hold a lease while the worker is supposed to be alive.
LEASE_STATUSES = ("dispatched", "running")

#: v0.3.0 (batch B) — the three states an unfinished attempt can be in,
#: plus the two terminal ones.  See :func:`infer_attempt_state`.
#:
#: ``still_running``   仍在运行 — the lease is alive, the worker is presumed alive
#: ``interrupted``      已中断   — the lease expired without a terminal event
#: ``outcome_unknown``  结束未知 — a receipt arrived that cannot be attributed
#: ``finished``         已结束   — done / cancelled
#: ``not_started``      未开始   — blocked, the task never entered the plane
ATTEMPT_STATES = (
    "still_running",
    "interrupted",
    "outcome_unknown",
    "finished",
    "not_started",
)


# ----------------------------------------------------------------------
# time helpers — the ledger stores UTC ISO8601 and nothing else
# ----------------------------------------------------------------------
def utcnow() -> datetime:
    """Current time in UTC."""
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """UTC ISO8601 — the one timestamp format the ledger ever stores."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp (``Z`` tolerated).  Never raises."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def lease_remaining_s(state: dict | None, now: datetime | None = None) -> float | None:
    """Seconds left on the lease of ``state`` — negative once it expired."""
    if not state:
        return None
    expiry = parse_timestamp(state.get("lease_expires_at"))
    if expiry is None:
        return None
    moment = now or utcnow()
    return round((expiry - moment).total_seconds(), 3)


def lease_expired(state: dict | None, now: datetime | None = None) -> bool:
    """True when the state holds a lease that is already over."""
    remaining = lease_remaining_s(state, now)
    return remaining is not None and remaining <= 0


def task_age_s(state: dict | None, now: datetime | None = None) -> float | None:
    """Seconds since the first event of the task."""
    if not state:
        return None
    first = parse_timestamp(state.get("first_ts"))
    if first is None:
        return None
    moment = now or utcnow()
    return round((moment - first).total_seconds(), 3)


def annotate_state(state: dict, now: datetime | None = None) -> dict:
    """Add the time dependent observability fields to one folded state."""
    state["lease_remaining_s"] = lease_remaining_s(state, now)
    state["age_s"] = task_age_s(state, now)
    state["attempt_state"] = infer_attempt_state(state, now)
    return state


def infer_attempt_state(state: dict | None, now: datetime | None = None) -> str:
    """Classify the state of the current attempt of a task.

    The rules are executable, not heuristic:

    ==================================  ==========================  ==================
    folded status                      extra condition             attempt state
    ==================================  ==========================  ==================
    ``done``                           —                           ``finished``
    ``cancelled``                      no late receipt             ``finished``
    ``cancelled``                      a late receipt arrived      ``outcome_unknown``
    ``blocked``                        —                           ``not_started``
    ``stale``                          —                           ``interrupted``
    ``dispatched`` / ``running``       lease alive                 ``still_running``
    ``dispatched`` / ``running``       lease expired               ``interrupted``
    ``dispatched`` / ``running``       no lease at all (legacy)    ``outcome_unknown``
    anything else                      —                           ``outcome_unknown``
    ==================================  ==========================  ==================

    A legacy task (a 0.2.x ledger without a lease) is therefore reported as
    *unknown* — never as a failure.
    """
    if not state:
        return "outcome_unknown"
    status = state.get("status")
    late = int(state.get("late_receipts") or 0)
    if status == "done":
        return "finished"
    if status == "cancelled":
        # a receipt arrived after the cancel — the outcome of the attempt
        # cannot be decided any more
        return "outcome_unknown" if late else "finished"
    if status == "blocked":
        return "not_started"
    if status == "stale":
        return "interrupted"
    if status in LEASE_STATUSES:
        remaining = lease_remaining_s(state, now)
        if remaining is None:
            # no lease at all: a ledger written before v0.3.0 — the plane
            # cannot tell whether the worker is alive
            return "outcome_unknown"
        return "still_running" if remaining > 0 else "interrupted"
    return "outcome_unknown"


def attempt_id_for(task_id: str | None, seq: int) -> str:
    """Deterministic attempt id of the ``seq``-th attempt of a task.

    The id is path safe on purpose: it is used as a directory name below
    ``<workdir>/attempts/``.
    """
    base = task_id or "task"
    return f"{base}-a{int(seq):02d}"


def _as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


class Ledger:
    """Append-only JSONL ledger."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------
    def append(self, event: str, task_id: str | None = None, **fields: Any) -> dict:
        """Append one event and return the stored record."""
        actor = fields.pop("actor", None) or os.environ.get("FOREMAN_ACTOR") or "foreman"
        detail = {key: value for key, value in fields.items()}
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "task_id": task_id,
            "actor": actor,
        }
        record.update(detail)
        record["detail"] = detail
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return record

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def events(self, task_id: str | None = None) -> list[dict]:
        """All events (optionally filtered by task id), oldest first.

        A truncated or half written line is skipped — a ledger that was cut
        in the middle of a write must never crash a reader.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        out: list[dict] = []
        for line in raw.splitlines():
            chunk = line.strip()
            if not chunk:
                continue
            try:
                record = json.loads(chunk)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if task_id is not None and record.get("task_id") != task_id:
                continue
            out.append(record)
        return out

    # ------------------------------------------------------------------
    # folding
    # ------------------------------------------------------------------
    def states(self) -> dict[str, dict]:
        """Fold the event stream into ``task_id -> state``."""
        folded: dict[str, dict] = {}
        for record in self.events():
            task_id = record.get("task_id")
            if not task_id:
                continue
            state = folded.get(task_id)
            if state is None:
                state = {
                    "task_id": task_id,
                    "status": "unknown",
                    "verdict": None,
                    "acceptance_ok": None,
                    "exit_code": None,
                    "agent": None,
                    "depends_on": [],
                    "needs_api": [],
                    "acceptance": None,
                    "needs_api_pending": False,
                    "reason": None,
                    "missing": [],
                    "new_task_id": None,
                    "last_event": None,
                    "last_ts": None,
                    "first_ts": None,
                    "events": 0,
                    # --- v0.3.0: lease, checkpoints, recovery counters ---
                    "lease_expires_at": None,
                    "lease_s": None,
                    "last_heartbeat_at": None,
                    "completed_steps": [],
                    "completed_steps_count": 0,
                    "checkpoint_count": 0,
                    "artifacts": [],
                    "recoveries": 0,
                    "retries": 0,
                    "last_attempt": 0,
                    "stale_reason": None,
                    # --- v0.3.0 (batch B): attempt dimension ---
                    "attempt_id": None,
                    "epoch": None,
                    "attempt_seq": 0,
                    "attempt_dir": None,
                    "attempts": [],
                    "attempt_aware": False,
                    "late_receipts": 0,
                    "late_receipt_attempts": [],
                    "acceptance_evidence": None,
                }
                folded[task_id] = state
            state["events"] += 1
            state["last_event"] = record.get("event")
            state["last_ts"] = record.get("ts")
            if state["first_ts"] is None:
                state["first_ts"] = record.get("ts")
            kind = record.get("event")
            detail = record.get("detail") or {}

            def field(name: str, default: Any = None) -> Any:
                if name in record:
                    return record.get(name)
                return detail.get(name, default)

            event_epoch = _as_int(field("epoch"))
            current_epoch = _as_int(state.get("epoch"))

            if kind in ("dispatch", "attempt"):
                if kind == "dispatch":
                    state["status"] = "dispatched"
                    state["verdict"] = None
                    state["acceptance_ok"] = None
                    state["depends_on"] = list(field("depends_on") or [])
                    state["needs_api"] = list(field("needs_api") or [])
                    state["acceptance"] = field("acceptance")
                    state["needs_api_pending"] = bool(field("needs_api_pending"))
                attempt_id = field("attempt_id")
                epoch = event_epoch
                if attempt_id is not None or epoch is not None:
                    state["attempt_aware"] = True
                if attempt_id:
                    state["attempt_id"] = str(attempt_id)
                    attempts = state.setdefault("attempts", [])
                    if str(attempt_id) not in attempts:
                        attempts.append(str(attempt_id))
                if epoch is not None:
                    state["epoch"] = epoch
                    state["attempt_seq"] = max(int(state.get("attempt_seq") or 0), epoch)
                    state["last_attempt"] = max(
                        int(state.get("last_attempt") or 0), epoch
                    )
                state["attempt_dir"] = field("attempt_dir") or state.get("attempt_dir")
                state["lease_expires_at"] = field("lease_expires_at") or state.get("lease_expires_at")
                state["lease_s"] = field("lease_s") or state.get("lease_s")
            elif kind == "running":
                if self._is_superseded(state, event_epoch):
                    self._count_late_receipt(state, record, field)
                else:
                    state["status"] = "running"
                    state["agent"] = field("agent") or state.get("agent")
                    state["lease_expires_at"] = field("lease_expires_at") or state.get("lease_expires_at")
                    state["lease_s"] = field("lease_s") or state.get("lease_s")
            elif kind == "blocked":
                state["status"] = "blocked"
                state["reason"] = field("reason")
                state["missing"] = list(field("missing") or [])
                blocked_deps = field("depends_on")
                if blocked_deps is not None:
                    state["depends_on"] = list(blocked_deps)
                blocked_needs = field("needs_api")
                if blocked_needs is not None:
                    state["needs_api"] = list(blocked_needs)
            elif kind == "done":
                if self._is_superseded(state, event_epoch):
                    # the receipt belongs to an attempt that was cancelled
                    # or superseded — it must not mark the task as done
                    self._count_late_receipt(state, record, field)
                else:
                    state["status"] = "done"
                    state["verdict"] = field("verdict")
                    acceptance_ok = field("acceptance_ok")
                    state["acceptance_ok"] = (
                        acceptance_ok if acceptance_ok is None else bool(acceptance_ok)
                    )
                    exit_code = field("exit_code")
                    state["exit_code"] = exit_code if exit_code is None else int(exit_code)
                    state["acceptance_evidence"] = self._acceptance_evidence(field, acceptance_ok)
            elif kind == "cancelled":
                state["status"] = "cancelled"
                state["verdict"] = None
                state["reason"] = field("reason")
            elif kind == "replan":
                state["reason"] = field("reason")
                state["new_task_id"] = field("new_task_id")
            # --- v0.3.0: failure & recovery events -------------------
            elif kind == "heartbeat":
                # a lease refresh never changes the status, it only moves
                # the deadline of the running attempt
                state["last_heartbeat_at"] = record.get("ts")
                state["lease_expires_at"] = field("lease_expires_at") or state.get("lease_expires_at")
                state["lease_s"] = field("lease_s") or state.get("lease_s")
            elif kind == "stale":
                state["status"] = "stale"
                state["stale_reason"] = field("reason")
                state["recoveries"] = int(state.get("recoveries") or 0) + 1
                state["lease_expires_at"] = field("lease_expires_at") or state.get("lease_expires_at")
            elif kind == "checkpoint":
                state["checkpoint_count"] = int(state.get("checkpoint_count") or 0) + 1
                step = field("step")
                if step:
                    steps = state.setdefault("completed_steps", [])
                    if step not in steps:
                        steps.append(str(step))
                    state["completed_steps_count"] = len(steps)
                artifact = field("artifact")
                if artifact:
                    artifacts = state.setdefault("artifacts", [])
                    if artifact not in artifacts:
                        artifacts.append(str(artifact))
            elif kind == "retry":
                state["retries"] = int(state.get("retries") or 0) + 1
                attempt = _as_int(field("attempt"))
                if attempt is not None:
                    state["last_attempt"] = max(int(state.get("last_attempt") or 0), attempt)
                state["lease_expires_at"] = field("lease_expires_at") or state.get("lease_expires_at")
            # An unknown kind is ignored on purpose: an old reader must stay
            # able to fold a ledger written by a newer plane.
        return folded

    # ------------------------------------------------------------------
    # attempt helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_superseded(state: dict, event_epoch: int | None) -> bool:
        """True when a receipt must not touch the current status.

        Two cases, both only for attempt aware ledgers (a 0.2.x ledger
        keeps its old behaviour):

        * the receipt carries an epoch that is not the current one — a
          newer attempt was started while this one was still running;
        * the task was cancelled in the current epoch — the worker came
          back after the cancel.
        """
        current = _as_int(state.get("epoch"))
        if current is None:
            # legacy ledger: 0.2.x behaviour, a done event wins
            return False
        if event_epoch is not None and event_epoch != current:
            return True
        if state.get("status") == "cancelled":
            return True
        return False

    @staticmethod
    def _count_late_receipt(state: dict, record: dict, field) -> None:
        """Remember a receipt that arrived too late to be authoritative."""
        state["late_receipts"] = int(state.get("late_receipts") or 0) + 1
        attempts = state.setdefault("late_receipt_attempts", [])
        attempt_id = field("attempt_id")
        label = str(attempt_id) if attempt_id else str(record.get("ts") or "?")
        if label not in attempts:
            attempts.append(label)

    @staticmethod
    def _acceptance_evidence(field, acceptance_ok: bool | None) -> dict | None:
        """The evidence of the acceptance: path, sha256, verdict."""
        path = field("acceptance_path")
        sha256 = field("acceptance_sha256")
        if path is None and sha256 is None:
            return None
        size = _as_int(field("acceptance_size"))
        checks = field("acceptance_checks")
        return {
            "path": path,
            "sha256": sha256,
            "size_bytes": size,
            "ok": acceptance_ok,
            "reason": field("acceptance_reason"),
            "checks": list(checks) if isinstance(checks, list) else [],
        }

    def attempt_count(self, task_id: str | None) -> int:
        """How many attempts the task already had."""
        if not task_id:
            return 0
        count = 0
        epoch = 0
        for record in self.events(task_id):
            # only the `attempt` event opens an attempt — a dispatch event of
            # a 0.2.x ledger has no attempt id and must not shift the counter
            if record.get("event") != "attempt":
                continue
            detail = record.get("detail") or {}
            value = record.get("attempt_id", detail.get("attempt_id"))
            if value:
                count += 1
            epoch_value = _as_int(record.get("epoch", detail.get("epoch")))
            if epoch_value is not None:
                epoch = max(epoch, epoch_value)
        return max(count, epoch)

    def next_attempt_id(self, task_id: str | None) -> tuple[str, int]:
        """The id and the epoch of the next attempt of a task."""
        seq = self.attempt_count(task_id) + 1
        return attempt_id_for(task_id, seq), seq

    def attempts(self, task_id: str) -> list[dict]:
        """One record per attempt of a task, oldest first.

        Every record carries the attempt id, the epoch, the moment the
        attempt started, its terminal state and the artefact the acceptance
        actually read.
        """
        out: list[dict] = []
        index: dict[str, dict] = {}
        for record in self.events(task_id):
            kind = record.get("event")
            detail = record.get("detail") or {}

            def field(name: str, default: Any = None) -> Any:
                if name in record:
                    return record.get(name)
                return detail.get(name, default)

            attempt_id = field("attempt_id")
            if kind in ("dispatch", "attempt"):
                key = str(attempt_id) if attempt_id else f"legacy-{len(out) + 1}"
                entry = index.get(key)
                if entry is None:
                    entry = {
                        "attempt_id": str(attempt_id) if attempt_id else None,
                        "epoch": _as_int(field("epoch")),
                        "attempt_aware": attempt_id is not None,
                        "started_ts": record.get("ts"),
                        "ended_ts": None,
                        "status": "dispatched",
                        "verdict": None,
                        "acceptance_ok": None,
                        "acceptance_path": None,
                        "acceptance_sha256": None,
                        "attempt_dir": field("attempt_dir"),
                        "superseded": False,
                    }
                    index[key] = entry
                    out.append(entry)
                entry["started_ts"] = entry["started_ts"] or record.get("ts")
                if entry.get("epoch") is None:
                    entry["epoch"] = _as_int(field("epoch"))
                if field("attempt_dir"):
                    entry["attempt_dir"] = field("attempt_dir")
            elif kind == "done":
                key = str(attempt_id) if attempt_id else None
                entry = index.get(key) if key else (out[-1] if out else None)
                if entry is None:
                    entry = {
                        "attempt_id": key,
                        "epoch": _as_int(field("epoch")),
                        "attempt_aware": attempt_id is not None,
                        "started_ts": None,
                        "ended_ts": record.get("ts"),
                        "status": "done",
                        "verdict": None,
                        "acceptance_ok": None,
                        "acceptance_path": None,
                        "acceptance_sha256": None,
                        "attempt_dir": None,
                        "superseded": False,
                    }
                    index[key or f"legacy-{len(out) + 1}"] = entry
                    out.append(entry)
                entry["ended_ts"] = record.get("ts")
                entry["verdict"] = field("verdict")
                entry["acceptance_ok"] = field("acceptance_ok")
                entry["acceptance_path"] = field("acceptance_path")
                entry["acceptance_sha256"] = field("acceptance_sha256")
                entry["status"] = "done"
        for entry in out:
            if entry.get("status") != "done":
                entry["status"] = "interrupted"
        return out

    def attempt_of(self, task_id: str) -> dict | None:
        """The current attempt of a task, or ``None`` when it never ran."""
        attempts = self.attempts(task_id)
        return attempts[-1] if attempts else None

    def state_of(self, task_id: str) -> dict | None:
        """Folded state of one task, or ``None`` when it is unknown."""
        return self.states().get(task_id)

    def is_dependency_satisfied(self, dep_id: str) -> bool:
        """True when ``dep_id`` is terminal, passed and was accepted."""
        state = self.state_of(dep_id)
        if not state:
            return False
        if state.get("status") != "done":
            return False
        if state.get("verdict") != "pass":
            return False
        if state.get("acceptance_ok") is False:
            return False
        return True

    # ------------------------------------------------------------------
    # manual operations
    # ------------------------------------------------------------------
    def cancel(self, task_id: str, reason: str = "") -> dict:
        """Cancel a task.  Unknown task ids raise and append nothing."""
        if self.state_of(task_id) is None:
            raise UnknownTaskError(task_id)
        return self.append("cancelled", task_id, reason=reason)

    def replan(self, task_id: str, reason: str = "", new_task_id: str | None = None) -> dict:
        """Re-plan a task.  Unknown task ids raise and append nothing."""
        if self.state_of(task_id) is None:
            raise UnknownTaskError(task_id)
        return self.append("replan", task_id, reason=reason, new_task_id=new_task_id)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def dependency_graph(self) -> dict[str, list[str]]:
        """``task_id -> depends_on`` taken from the dispatch events."""
        graph: dict[str, list[str]] = {}
        for record in self.events():
            if record.get("event") != "dispatch":
                continue
            task_id = record.get("task_id")
            if not task_id:
                continue
            detail = record.get("detail") or {}
            deps = record.get("depends_on")
            if deps is None:
                deps = detail.get("depends_on")
            bucket = graph.setdefault(task_id, [])
            for dep in deps or []:
                if dep not in bucket:
                    bucket.append(dep)
        return graph

    def has_open_task(self, task_id: str) -> bool:
        """True when the task currently sits in a non-terminal state."""
        state = self.state_of(task_id)
        if not state:
            return False
        return state.get("status") in NON_TERMINAL_STATUSES

    def is_already_dispatched(self, task_id: str, now: datetime | None = None) -> bool:
        """True when the task still holds the plane and must not run again.

        Since v0.3.0 the check is **state based**, not history based.  The
        old rule ("a ``dispatch`` event exists") locked a task forever as
        soon as it entered the plane: a worker killed with ``kill -9`` left
        the ledger on ``running`` and every re-dispatch was refused as a
        duplicate — the task could only be unlocked by hand with ``--force``,
        which re-ran every step from scratch.

        The rule now is:

        * ``blocked`` — the gate refused the task, it never entered the
          plane → re-dispatchable (unchanged; a blocked task that could never
          be re-dispatched would deadlock the graph);
        * ``stale`` — the lease expired → re-dispatchable;
        * ``running`` / ``dispatched`` with a **live** lease → not
          re-dispatchable, the worker is presumed alive;
        * ``running`` / ``dispatched`` with an **expired** lease →
          re-dispatchable, the worker is presumed dead;
        * ``done`` / ``cancelled`` → not re-dispatchable without ``--force``
          (or after a replan, which allocates a fresh task id).
        """
        state = self.state_of(task_id)
        if not state:
            return False
        status = state.get("status")
        if status in RECOVERABLE_STATUSES or status == "unknown":
            return False
        if status in LEASE_STATUSES:
            return not lease_expired(state, now)
        return True

    def completed_steps(self, task_id: str) -> list[str]:
        """Ordered, de-duplicated names of the checkpointed steps of a task.

        The list is what a re-dispatch hands to the execution body through
        the ``{completed_steps}`` placeholder, so the body can skip the work
        that is already done.
        """
        steps: list[str] = []
        for record in self.events(task_id):
            if record.get("event") != "checkpoint":
                continue
            detail = record.get("detail") or {}
            step = record.get("step") or detail.get("step")
            if step and str(step) not in steps:
                steps.append(str(step))
        return steps
