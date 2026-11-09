"""Append-only audit ledger (SPEC §3).

One JSON object per line, only ever appended.  Events are folded into a
per-task state so that the gate can ask "is this dependency satisfied?"
without rescanning the whole file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import UnknownTaskError

__all__ = ["Ledger", "TERMINAL_STATUSES", "NON_TERMINAL_STATUSES"]

TERMINAL_STATUSES = ("done", "cancelled")
NON_TERMINAL_STATUSES = ("dispatched", "running", "blocked")


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
        """All events (optionally filtered by task id), oldest first."""
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
                    "events": 0,
                }
                folded[task_id] = state
            state["events"] += 1
            state["last_event"] = record.get("event")
            state["last_ts"] = record.get("ts")
            kind = record.get("event")
            detail = record.get("detail") or {}

            def field(name: str, default: Any = None) -> Any:
                if name in record:
                    return record.get(name)
                return detail.get(name, default)

            if kind == "dispatch":
                state["status"] = "dispatched"
                state["verdict"] = None
                state["acceptance_ok"] = None
                state["depends_on"] = list(field("depends_on") or [])
                state["needs_api"] = list(field("needs_api") or [])
                state["acceptance"] = field("acceptance")
                state["needs_api_pending"] = bool(field("needs_api_pending"))
                state["agent"] = field("agent")
            elif kind == "running":
                state["status"] = "running"
                state["agent"] = field("agent") or state.get("agent")
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
                state["status"] = "done"
                state["verdict"] = field("verdict")
                acceptance_ok = field("acceptance_ok")
                state["acceptance_ok"] = acceptance_ok if acceptance_ok is None else bool(acceptance_ok)
                exit_code = field("exit_code")
                state["exit_code"] = exit_code if exit_code is None else int(exit_code)
            elif kind == "cancelled":
                state["status"] = "cancelled"
                state["verdict"] = None
                state["reason"] = field("reason")
            elif kind == "replan":
                state["reason"] = field("reason")
                state["new_task_id"] = field("new_task_id")
        return folded

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

    def is_already_dispatched(self, task_id: str) -> bool:
        """True when the task already entered the plane (a ``dispatch`` event exists).

        This is the idempotency check of SPEC §3.4: a task id may enter the
        plane once.  Re-dispatching is only possible with ``--force`` (or
        after a replan, which allocates a fresh task id).

        A ``blocked`` event does **not** count: the gate refused the task, so
        it never entered the plane.  A blocked task stays re-dispatchable and
        must be dispatchable again once its dependency is satisfied — a
        blocked task that could never be re-dispatched would deadlock the
        graph (the upstream task can never unblock it).
        """
        for record in self.events(task_id):
            if record.get("event") == "dispatch":
                return True
        return False
