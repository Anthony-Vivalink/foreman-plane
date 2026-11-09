"""Execution bodies and the dispatch orchestration (SPEC §6).

``Runner`` adapts a generic CLI agent to a uniform call.  ``Dispatcher``
wires contract -> gate -> ledger -> runner -> acceptance and is shared by
the CLI and the MCP server so that both front-ends behave identically.

Keeping the orchestration here avoids adding a module outside the layout
mandated by SPEC §1.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acceptance import AcceptanceResult, verify
from .config import (
    ForemanConfig,
    ensure_dirs,
    load_config,
    resolve_actor,
    resolve_default_agent,
)
from .contract import TaskCard, parse_task_card
from .errors import UnknownTaskError
from .gate import GateDecision, evaluate_gate
from .ledger import Ledger

__all__ = ["RunResult", "Runner", "Dispatcher"]


@dataclass
class RunResult:
    """Outcome of one execution body run (SPEC §6)."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_s": self.duration_s,
        }


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class Runner:
    """Runs a CLI agent as a subprocess."""

    def __init__(self, config: ForemanConfig | None = None, agent: str | None = None) -> None:
        self.config = config or load_config()
        self.agent = agent or resolve_default_agent()

    # ------------------------------------------------------------------
    def command_for(
        self,
        agent: str,
        task: str,
        workdir: str | os.PathLike[str] | None = None,
    ) -> list[str] | None:
        """Split the agent command template into argv, or ``None`` if unusable."""
        spec = self.config.agents.get(agent)
        if not isinstance(spec, dict):
            return None
        template = str(spec.get("command") or "")
        if not template.strip():
            return None
        try:
            tokens = shlex.split(template)
        except ValueError:
            tokens = template.split()
        if not tokens:
            return None
        if not any("{task}" in token for token in tokens):
            return None
        workdir_text = str(Path(workdir).expanduser()) if workdir else str(Path.cwd())
        resolved: list[str] = []
        for token in tokens:
            token = token.replace("{workdir}", workdir_text)
            token = token.replace("{task}", task)
            resolved.append(token)
        return resolved

    def timeout_for(self, agent: str, timeout_s: int | None = None) -> int:
        if timeout_s:
            return int(timeout_s)
        spec = self.config.agents.get(agent) or {}
        value = spec.get("timeout_s")
        if isinstance(value, (int, float)) and int(value) > 0:
            return int(value)
        return int(self.config.timeout_s)

    # ------------------------------------------------------------------
    def run(
        self,
        task: str,
        workdir: str | os.PathLike[str] | None = None,
        timeout_s: int | None = None,
        agent: str | None = None,
        task_id: str | None = None,
    ) -> RunResult:
        """Run one task.  Never raises: failures become exit codes."""
        agent_name = agent or self.agent
        tokens = self.command_for(agent_name, task, workdir)
        if tokens is None:
            return RunResult(
                exit_code=127,
                stdout="",
                stderr=f"agent not found: {agent_name}",
            )
        executable = tokens[0]
        if shutil.which(executable) is None:
            return RunResult(
                exit_code=127,
                stdout="",
                stderr=f"agent not found: {executable}",
            )
        timeout = self.timeout_for(agent_name, timeout_s)
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell
                tokens,
                cwd=str(workdir) if workdir else None,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            result = RunResult(
                exit_code=124,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr) or f"timeout after {timeout}s",
                timed_out=True,
                duration_s=duration,
            )
            self._write_log(task_id, agent_name, result)
            return result
        except OSError as exc:
            duration = time.monotonic() - started
            result = RunResult(
                exit_code=127,
                stdout="",
                stderr=f"agent not found: {executable}: {exc}",
                duration_s=duration,
            )
            self._write_log(task_id, agent_name, result)
            return result
        duration = time.monotonic() - started
        result = RunResult(
            exit_code=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=duration,
        )
        self._write_log(task_id, agent_name, result)
        return result

    # ------------------------------------------------------------------
    def _write_log(self, task_id: str | None, agent: str, result: RunResult) -> None:
        """Best effort run log inside ``${FOREMAN_HOME}/runs``."""
        try:
            ensure_dirs(self.config)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            name = f"{task_id or 'task'}-{stamp}.log"
            path = self.config.runs_dir / name
            body = (
                f"# agent: {agent}\n"
                f"# exit_code: {result.exit_code}\n"
                f"# duration_s: {result.duration_s:.3f}\n"
                f"# timed_out: {result.timed_out}\n\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
            )
            path.write_text(body, encoding="utf-8")
        except OSError:
            pass


class Dispatcher:
    """Full dispatch flow: contract -> duplicate check -> gate -> run -> accept."""

    def __init__(
        self,
        config: ForemanConfig | None = None,
        agent: str | None = None,
        ledger: Ledger | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.config = config or load_config()
        ensure_dirs(self.config)
        self.ledger = ledger or Ledger(self.config.ledger_path)
        self.runner = runner or Runner(self.config, agent=agent)

    # ------------------------------------------------------------------
    def gate(self, card: TaskCard, workdir: str | os.PathLike[str] | None = None) -> GateDecision:
        return evaluate_gate(card, self.ledger, base_dir=workdir)

    def dispatch(
        self,
        task_text: str,
        agent: str | None = None,
        workdir: str | os.PathLike[str] | None = None,
        force: bool = False,
        actor: str | None = None,
    ) -> dict:
        """Dispatch one task and return the receipt."""
        who = actor or resolve_actor()
        card = parse_task_card(task_text)
        agent_name = agent or self.runner.agent

        # --- idempotency / duplicate check (before anything is written)
        if card.task_id and not force and self.ledger.is_already_dispatched(card.task_id):
            state = self.ledger.state_of(card.task_id) or {}
            return {
                "ok": False,
                "blocked": True,
                "dispatched": False,
                "reason": "duplicate_task",
                "task_id": card.task_id,
                "missing": [],
                "needs_api_pending": False,
                "notes": [f"task {card.task_id} was already dispatched"],
            }

        # --- gate
        decision = self.gate(card, workdir)
        if not decision.allowed:
            self.ledger.append(
                "blocked",
                card.task_id,
                reason=decision.reason,
                missing=decision.missing,
                notes=decision.notes,
                depends_on=card.depends_on,
                depends_on_unparsed=card.depends_on_unparsed,
                needs_api=card.needs_api,
                needs_api_unparsed=card.needs_api_unparsed,
                acceptance=card.acceptance,
                acceptance_unparsed=card.acceptance_unparsed,
                actor=who,
            )
            return {
                "ok": False,
                "blocked": True,
                "dispatched": False,
                "reason": decision.reason,
                "task_id": card.task_id,
                "missing": list(decision.missing),
                "needs_api_pending": decision.needs_api_pending,
                "notes": list(decision.notes),
            }

        # --- allowed: dispatch, run, accept
        self.ledger.append(
            "dispatch",
            card.task_id,
            depends_on=list(card.depends_on),
            needs_api=list(card.needs_api),
            acceptance=card.acceptance,
            needs_api_pending=decision.needs_api_pending,
            agent=agent_name,
            workdir=str(workdir) if workdir else None,
            force=bool(force),
            actor=who,
        )
        self.ledger.append("running", card.task_id, agent=agent_name, actor=who)
        result = self.runner.run(task_text, workdir=workdir, agent=agent_name, task_id=card.task_id)

        acceptance: AcceptanceResult = verify(
            card.acceptance, exit_code=result.exit_code, base_dir=workdir
        )
        acceptance_ok: bool | None = None if card.acceptance is None else acceptance.ok
        verdict = self._verdict(card, result, acceptance_ok)
        self.ledger.append(
            "done",
            card.task_id,
            verdict=verdict,
            acceptance_ok=acceptance_ok,
            exit_code=result.exit_code,
            duration_s=round(result.duration_s, 3),
            timed_out=result.timed_out,
            agent=agent_name,
            acceptance_reason=acceptance.reason,
            actor=who,
        )
        return {
            "ok": verdict == "pass",
            "blocked": False,
            "dispatched": True,
            "task_id": card.task_id,
            "verdict": verdict,
            "acceptance_ok": acceptance_ok,
            "acceptance_reason": acceptance.reason,
            "needs_api_pending": decision.needs_api_pending,
            "exit_code": result.exit_code,
            "duration_s": round(result.duration_s, 3),
            "timed_out": result.timed_out,
            "notes": list(decision.notes),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _verdict(card: TaskCard, result: RunResult, acceptance_ok: bool | None) -> str:
        if acceptance_ok is False:
            return "fail"
        if result.exit_code == 0:
            return "pass"
        # a failing exit code is expected when the acceptance string pins it
        if card.acceptance and "exit_code=" in card.acceptance:
            return "pass"
        return "fail"

    # ------------------------------------------------------------------
    def status(self, task_id: str | None = None) -> dict:
        """Ledger summary, optionally for one task."""
        states = self.ledger.states()
        if task_id is not None:
            state = states.get(task_id)
            return {
                "ok": True,
                "home": str(self.config.home),
                "ledger_path": str(self.config.ledger_path),
                "task_id": task_id,
                "task": state,
                "known": state is not None,
            }
        counts: dict[str, int] = {}
        for state in states.values():
            status = str(state.get("status"))
            counts[status] = counts.get(status, 0) + 1
        return {
            "ok": True,
            "home": str(self.config.home),
            "ledger_path": str(self.config.ledger_path),
            "counts": counts,
            "tasks": states,
        }

    def cancel(self, task_id: str, reason: str = "") -> dict:
        """Cancel a task; unknown ids raise ``UnknownTaskError``."""
        record = self.ledger.cancel(task_id, reason)
        return {"ok": True, "task_id": task_id, "event": record}

    def replan(self, task_id: str, reason: str = "", new_task_id: str | None = None) -> dict:
        """Re-plan a task; unknown ids raise ``UnknownTaskError``."""
        record = self.ledger.replan(task_id, reason, new_task_id)
        return {"ok": True, "task_id": task_id, "event": record}

    def ready_tasks(self) -> list[dict]:
        """Tasks that are not finished but whose dependencies are satisfied now."""
        states = self.ledger.states()
        out: list[dict] = []
        for task_id, state in states.items():
            if state.get("status") not in ("dispatched", "blocked"):
                continue
            missing = [
                dep
                for dep in (state.get("depends_on") or [])
                if not self.ledger.is_dependency_satisfied(dep)
            ]
            if not missing:
                out.append({"task_id": task_id, "status": state.get("status"), "missing": []})
        return out

    def blocked_tasks(self) -> list[dict]:
        """Tasks currently blocked, with the reason recorded in the ledger."""
        out: list[dict] = []
        for task_id, state in self.ledger.states().items():
            if state.get("status") != "blocked":
                continue
            out.append(
                {
                    "task_id": task_id,
                    "reason": state.get("reason"),
                    "missing": list(state.get("missing") or []),
                    "ts": state.get("last_ts"),
                }
            )
        return out
