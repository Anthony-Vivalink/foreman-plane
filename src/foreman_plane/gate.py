"""Two-level dependency gate (SPEC §4).

Level 1 (hard) ``depends_on`` — the upstream task must be terminal, passed
and accepted.  Level 2 (soft) ``needs_api`` — the artefact only has to
exist; a missing artefact never blocks, it is reported as
``needs_api_pending``.

The gate fails closed: an unparsable dependency marker blocks the task
instead of being silently treated as "no dependency".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .contract import TaskCard
from .ledger import Ledger

__all__ = ["GateDecision", "evaluate_gate", "REASONS"]

REASONS = (
    "task_id_missing",
    "dependency_unparsed",
    "dependency_cycle",
    "dependency_unknown",
    "dependency_pending",
    "dependency_failed",
    "dependency_unaccepted",
)


@dataclass
class GateDecision:
    """Outcome of one gate evaluation (SPEC §4)."""

    allowed: bool
    reason: str | None = None
    missing: list[str] = field(default_factory=list)
    needs_api_pending: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "missing": list(self.missing),
            "needs_api_pending": self.needs_api_pending,
            "notes": list(self.notes),
        }


def _expand(path: str, base_dir: str | os.PathLike[str] | None) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    base = Path(base_dir).expanduser() if base_dir else Path.cwd()
    return base / candidate


def needs_api_path(item: str) -> str:
    """Path part of a ``needs_api`` token (``id#path`` -> ``path``)."""
    if "#" in item:
        return item.split("#", 1)[1]
    return item


def _detect_cycle(task_id: str, depends_on: Iterable[str], graph: dict[str, list[str]]) -> list[str]:
    """Return the cycle path if ``task_id`` depends on itself transitively."""
    edges: dict[str, list[str]] = {key: list(value) for key, value in graph.items()}
    edges.setdefault(task_id, [])
    for dep in depends_on:
        if dep not in edges[task_id]:
            edges[task_id].append(dep)

    if task_id in edges[task_id]:
        return [task_id, task_id]

    stack: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str) -> list[str] | None:
        if node == task_id:
            return stack + [node]
        if node in visiting or node in done:
            return None
        visiting.add(node)
        stack.append(node)
        for child in edges.get(node, []):
            found = walk(child)
            if found:
                return found
        stack.pop()
        visiting.discard(node)
        done.add(node)
        return None

    for dep in edges.get(task_id, []):
        found = walk(dep)
        if found:
            return found
    return []


def evaluate_gate(
    card: TaskCard,
    ledger: Ledger,
    base_dir: str | os.PathLike[str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
) -> GateDecision:
    """Evaluate the gate for one task card.

    The first matching rule wins (SPEC §4).
    """
    notes: list[str] = []

    # 1. task id
    if not card.task_id:
        return GateDecision(
            allowed=False,
            reason="task_id_missing",
            missing=[],
            notes=["task card carries no legal task id"],
        )

    # 2. unparsable hard dependencies
    if card.depends_on_unparsed:
        return GateDecision(
            allowed=False,
            reason="dependency_unparsed",
            missing=[],
            notes=["depends_on marker present but no legal task id could be extracted"],
        )

    # 3. self reference / cycles
    cycle = _detect_cycle(card.task_id, card.depends_on, ledger.dependency_graph())
    if cycle:
        return GateDecision(
            allowed=False,
            reason="dependency_cycle",
            missing=[card.task_id],
            notes=["dependency cycle: " + " -> ".join(cycle)],
        )

    # 4. hard dependencies
    for dep in card.depends_on:
        state = ledger.state_of(dep)
        if state is None:
            return GateDecision(
                allowed=False,
                reason="dependency_unknown",
                missing=[dep],
                notes=[f"dependency {dep} is not present in the ledger"],
            )
        status = state.get("status")
        if status not in ("done", "cancelled"):
            return GateDecision(
                allowed=False,
                reason="dependency_pending",
                missing=[dep],
                notes=[f"dependency {dep} is still {status}"],
            )
        if state.get("verdict") != "pass":
            return GateDecision(
                allowed=False,
                reason="dependency_failed",
                missing=[dep],
                notes=[f"dependency {dep} finished with verdict {state.get('verdict')!r}"],
            )
        if state.get("acceptance_ok") is False:
            return GateDecision(
                allowed=False,
                reason="dependency_unaccepted",
                missing=[dep],
                notes=[f"dependency {dep} did not pass artefact acceptance"],
            )

    # 5. soft dependencies
    pending = False
    for item in card.needs_api:
        path = needs_api_path(item)
        if path_exists is not None:
            exists = bool(path_exists(path))
        else:
            exists = _expand(path, base_dir).exists()
        if not exists:
            pending = True
            notes.append(f"needs_api artefact not ready: {path}")
    if card.needs_api_unparsed:
        notes.append("needs_api marker present but no legal path could be extracted")

    # 6. allowed
    return GateDecision(
        allowed=True,
        reason=None,
        missing=[],
        needs_api_pending=pending,
        notes=notes,
    )
