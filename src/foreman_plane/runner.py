"""Execution bodies and the dispatch orchestration (SPEC §6).

``Runner`` adapts a generic CLI agent to a uniform call.  ``Dispatcher``
wires contract -> gate -> ledger -> runner -> acceptance and is shared by
the CLI and the MCP server so that both front-ends behave identically.

Keeping the orchestration here avoids adding a module outside the layout
mandated by SPEC §1.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
from .ledger import (
    Ledger,
    annotate_state,
    infer_attempt_state,
    iso,
    lease_expired,
    lease_remaining_s,
    utcnow,
)

__all__ = ["RunResult", "RetryPolicy", "Runner", "Dispatcher"]


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


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff policy of one execution body (v0.3.0).

    ``max_attempts`` counts the **total** number of attempts, the first run
    included: ``1`` means "no retry" (the 0.2.x behaviour), ``3`` means "run
    at most three times".  The delay before the n-th retry is
    ``base_delay_s * 2 ** (n - 1)``.
    """

    max_attempts: int = 1
    base_delay_s: float = 5.0
    retry_on_exit_codes: tuple[int, ...] = (124,)

    @classmethod
    def from_spec(cls, spec: Any) -> "RetryPolicy | None":
        """Read the ``retry`` block of an agent spec — ``None`` when absent."""
        block = spec.get("retry") if isinstance(spec, dict) else None
        if not isinstance(block, dict):
            return None
        max_attempts = block.get("max_attempts", 1)
        try:
            max_attempts = int(max_attempts)
        except (TypeError, ValueError):
            max_attempts = 1
        base = block.get("base_delay_s", 5.0)
        try:
            base = float(base)
        except (TypeError, ValueError):
            base = 5.0
        codes = block.get("retry_on_exit_codes", [124])
        if not isinstance(codes, (list, tuple)):
            codes = [124]
        parsed: list[int] = []
        for code in codes:
            try:
                parsed.append(int(code))
            except (TypeError, ValueError):
                continue
        return cls(
            max_attempts=max(1, max_attempts),
            base_delay_s=max(0.0, base),
            retry_on_exit_codes=tuple(parsed),
        )


def _sha256_text(text: str) -> str | None:
    """Digest of a task card — lets a later retry prove it used the same card."""
    try:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    except Exception:  # pragma: no cover - defensive
        return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _tail(text: str, limit: int = 2000) -> str:
    """Last ``limit`` characters of ``text`` — enough to diagnose, small enough
    to carry in a receipt and in the ledger."""
    stripped = (text or "").strip()
    if len(stripped) <= limit:
        return stripped
    return "..." + stripped[-limit:]


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
        completed_steps: list[str] | None = None,
        attempt_dir: str | os.PathLike[str] | None = None,
    ) -> list[str] | None:
        """Split the agent command template into argv, or ``None`` if unusable.

        Placeholders: ``{task}`` (mandatory), ``{workdir}`` (optional),
        ``{completed_steps}`` (optional, since v0.3.0) and ``{attempt_dir}``
        (optional, since v0.3.0 batch B).  ``{completed_steps}`` is
        replaced by the JSON array of the steps the task already
        checkpointed, so a re-dispatch can skip the finished work.
        ``{attempt_dir}`` is the directory of the current attempt — the
        execution body writes its artefacts there, so two attempts of the
        same task never overwrite each other.  A template without a
        placeholder keeps working exactly as before.
        """
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
        attempt_text = str(Path(attempt_dir).expanduser()) if attempt_dir else ""
        completed_json = json.dumps(
            [str(step) for step in (completed_steps or [])],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        resolved: list[str] = []
        for token in tokens:
            token = token.replace("{workdir}", workdir_text)
            token = token.replace("{task}", task)
            token = token.replace("{completed_steps}", completed_json)
            token = token.replace("{attempt_dir}", attempt_text)
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
        completed_steps: list[str] | None = None,
        attempt_id: str | None = None,
        attempt_dir: str | os.PathLike[str] | None = None,
    ) -> RunResult:
        """Run one task.  Never raises: failures become exit codes.

        The attempt context is handed to the execution body twice: through
        the environment (``FOREMAN_ATTEMPT_ID``, ``FOREMAN_ATTEMPT_DIR``,
        ``FOREMAN_WORKDIR``) and through the ``{attempt_dir}`` placeholder
        of the command template.
        """
        agent_name = agent or self.agent
        spec = self.config.agents.get(agent_name)
        tokens: list[str] | None = None
        problem = ""
        if not isinstance(spec, dict) or not str(spec.get("command") or "").strip():
            problem = (
                f"agent not found: {agent_name} "
                "(no such agent, or its command template is empty)"
            )
        else:
            tokens = self.command_for(
                agent_name, task, workdir, completed_steps, attempt_dir
            )
            if tokens is None:
                problem = (
                    f"agent not found: {agent_name} "
                    "(command template has no {task} placeholder)"
                )
        if tokens is None:
            result = RunResult(exit_code=127, stdout="", stderr=problem)
            self._write_log(task_id, agent_name, result, attempt_id)
            return result
        executable = tokens[0]
        if shutil.which(executable) is None:
            result = RunResult(
                exit_code=127,
                stdout="",
                stderr=f"agent not found: {executable} (executable is not on PATH)",
            )
            self._write_log(task_id, agent_name, result, attempt_id)
            return result
        timeout = self.timeout_for(agent_name, timeout_s)
        env = os.environ.copy()
        env["FOREMAN_ATTEMPT_ID"] = str(attempt_id or "")
        env["FOREMAN_ATTEMPT_DIR"] = (
            str(Path(attempt_dir).expanduser()) if attempt_dir else ""
        )
        env["FOREMAN_WORKDIR"] = str(Path(workdir).expanduser()) if workdir else str(Path.cwd())
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell
                tokens,
                cwd=str(workdir) if workdir else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
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
            self._write_log(task_id, agent_name, result, attempt_id)
            return result
        except OSError as exc:
            duration = time.monotonic() - started
            result = RunResult(
                exit_code=127,
                stdout="",
                stderr=f"agent not found: {executable}: {exc}",
                duration_s=duration,
            )
            self._write_log(task_id, agent_name, result, attempt_id)
            return result
        duration = time.monotonic() - started
        result = RunResult(
            exit_code=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=duration,
        )
        self._write_log(task_id, agent_name, result, attempt_id)
        return result

    # ------------------------------------------------------------------
    def _write_log(
        self,
        task_id: str | None,
        agent: str,
        result: RunResult,
        attempt_id: str | None = None,
    ) -> None:
        """Best effort run log inside ``${FOREMAN_HOME}/runs``.

        The name is ``{task_id}-{YYYYmmdd-HHMMSS}.log``.  Two runs of the
        same task inside the same second used to overwrite each other, so
        a name that is already taken gets a numeric suffix (``-2``, ``-3``,
        ...).  The file is created exclusively, so a log is never lost.
        """
        try:
            ensure_dirs(self.config)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            stem = f"{task_id or 'task'}-{stamp}"
            body = (
                f"# agent: {agent}\n"
                f"# attempt_id: {attempt_id or '-'}\n"
                f"# exit_code: {result.exit_code}\n"
                f"# duration_s: {result.duration_s:.3f}\n"
                f"# timed_out: {result.timed_out}\n\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
            )
            for counter in range(1, 1000):
                name = f"{stem}.log" if counter == 1 else f"{stem}-{counter}.log"
                try:
                    with open(self.config.runs_dir / name, "x", encoding="utf-8") as handle:
                        handle.write(body)
                    break
                except FileExistsError:
                    continue
                except OSError:
                    break
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
        now: datetime | None = None,
        card_path: str | os.PathLike[str] | None = None,
    ) -> dict:
        """Dispatch one task and return the receipt.

        Every dispatch allocates an **attempt**: an ``attempt_id``, an
        ``epoch`` and a directory ``<workdir>/attempts/<attempt_id>/`` the
        execution body can write into.  A re-dispatch of the same task id
        allocates a *new* attempt, so the artefacts of two attempts never
        mix and a receipt can always be traced back to one attempt.
        """
        who = actor or resolve_actor()
        card = parse_task_card(task_text)
        agent_name = agent or self.runner.agent
        moment = now or utcnow()

        # --- idempotency / duplicate check (before anything is written)
        if card.task_id and not force and self.ledger.is_already_dispatched(card.task_id, moment):
            state = self.ledger.state_of(card.task_id) or {}
            remaining = lease_remaining_s(state, moment)
            status = state.get("status")
            notes = [f"task {card.task_id} was already dispatched"]
            if status in ("running", "dispatched"):
                if remaining is None:
                    notes.append("the task still holds the plane")
                elif remaining > 0:
                    notes.append(f"lease still held, {remaining:.0f}s left")
                else:
                    notes.append(
                        "the lease expired — run `foreman-plane reclaim` and dispatch again"
                    )
            return {
                "ok": False,
                "blocked": True,
                "dispatched": False,
                "reason": "duplicate_task",
                "task_id": card.task_id,
                "missing": [],
                "needs_api_pending": False,
                "notes": notes,
                "status": status,
                "lease_expires_at": state.get("lease_expires_at"),
                "lease_remaining_s": remaining,
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
        completed = self.ledger.completed_steps(card.task_id) if card.task_id else []

        # A previous worker died with an expired lease: record the recovery
        # before the new attempt so the trail shows why we run again.
        previous = self.ledger.state_of(card.task_id) if card.task_id else None
        recovered = False
        if (
            previous
            and previous.get("status") in ("running", "dispatched")
            and lease_expired(previous, moment)
        ):
            self.ledger.append(
                "stale",
                card.task_id,
                reason="lease_expired",
                lease_expires_at=previous.get("lease_expires_at"),
                previous_status=previous.get("status"),
                evaluated_at=iso(moment),
                actor=who,
            )
            recovered = True

        lease_expires_at = iso(moment + timedelta(seconds=self.config.lease_s))

        # --- v0.3.0 (batch B): the attempt dimension -------------------
        attempt_id, epoch = self.ledger.next_attempt_id(card.task_id)
        attempt_dir = self._attempt_dir(workdir, attempt_id)
        previous_attempt_id = (previous or {}).get("attempt_id")
        self.ledger.append(
            "attempt",
            card.task_id,
            attempt_id=attempt_id,
            epoch=epoch,
            reason="recovered" if recovered else "dispatch",
            previous_attempt_id=previous_attempt_id,
            attempt_dir=str(attempt_dir),
            lease_expires_at=lease_expires_at,
            lease_s=self.config.lease_s,
            actor=who,
        )
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
            lease_expires_at=lease_expires_at,
            lease_s=self.config.lease_s,
            completed_steps=list(completed),
            recovered=recovered,
            attempt_id=attempt_id,
            epoch=epoch,
            attempt_dir=str(attempt_dir),
            card_path=str(card_path) if card_path else None,
            card_sha256=_sha256_text(task_text),
            actor=who,
        )
        self.ledger.append(
            "running",
            card.task_id,
            agent=agent_name,
            lease_expires_at=lease_expires_at,
            lease_s=self.config.lease_s,
            attempt_id=attempt_id,
            epoch=epoch,
            actor=who,
        )
        result, attempts = self._run_with_retries(
            card.task_id,
            agent_name,
            task_text,
            workdir,
            completed,
            attempt_id=attempt_id,
            attempt_dir=attempt_dir,
        )

        # The acceptance reads the artefact of **this** attempt: a relative
        # path is looked up in the attempt directory first, the base
        # directory is only the fallback for bodies that predate it.
        acceptance: AcceptanceResult = verify(
            card.acceptance,
            exit_code=result.exit_code,
            base_dir=workdir,
            attempt_dir=attempt_dir,
        )
        acceptance_ok: bool | None = None if card.acceptance is None else acceptance.ok
        verdict = self._verdict(card, result, acceptance_ok)

        # The race: the task was cancelled (or a newer attempt took over)
        # while the worker was still running.  The receipt must not be
        # taken for the success of the current attempt.
        superseded, superseded_reason = self._superseded(card.task_id, epoch)

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
            acceptance_path=acceptance.path,
            acceptance_sha256=acceptance.sha256,
            acceptance_size=acceptance.size_bytes,
            acceptance_checks=acceptance.checks,
            stderr_tail=_tail(result.stderr, 500),
            attempts=attempts,
            completed_steps=list(completed),
            attempt_id=attempt_id,
            epoch=epoch,
            attempt_dir=str(attempt_dir),
            superseded=superseded,
            superseded_reason=superseded_reason,
            actor=who,
        )
        notes = list(decision.notes)
        if completed:
            notes.append(
                "re-dispatch: "
                + ", ".join(completed)
                + " already done — handed to the execution body as {completed_steps}"
            )
        if superseded:
            notes.append(
                f"the receipt was discarded ({superseded_reason}) — it does not "
                "close the task"
            )
        return {
            "ok": verdict == "pass" and not superseded,
            "blocked": False,
            "dispatched": True,
            "task_id": card.task_id,
            "verdict": None if superseded else verdict,
            "raw_verdict": verdict,
            "acceptance_ok": acceptance_ok,
            "acceptance_reason": acceptance.reason,
            "acceptance_path": acceptance.path,
            "acceptance_sha256": acceptance.sha256,
            "acceptance_size": acceptance.size_bytes,
            "needs_api_pending": decision.needs_api_pending,
            "exit_code": result.exit_code,
            "duration_s": round(result.duration_s, 3),
            "timed_out": result.timed_out,
            "stderr": _tail(result.stderr),
            "notes": notes,
            # --- v0.3.0 ---
            "completed_steps": list(completed),
            "skipped_steps": list(completed),
            "resumed": bool(completed),
            "attempts": attempts,
            "retries": max(0, attempts - 1),
            "recovered": recovered,
            "lease_expires_at": lease_expires_at,
            "lease_remaining_s": float(self.config.lease_s),
            # --- v0.3.0 (batch B): attempt dimension ---
            "attempt_id": attempt_id,
            "epoch": epoch,
            "attempt_dir": str(attempt_dir),
            "previous_attempt_id": previous_attempt_id,
            "superseded": superseded,
            "superseded_reason": superseded_reason,
        }

    # ------------------------------------------------------------------
    def retry_policy(self, agent: str) -> RetryPolicy | None:
        """Backoff policy of an execution body — ``None`` means "no retry"."""
        return RetryPolicy.from_spec(self.config.agents.get(agent))

    def _runner_takes(self, keyword: str) -> bool:
        """True when the runner accepts ``keyword``.

        Custom runners written for 0.2.x do not know the new keywords; they
        keep working, they simply do not receive the extra context.
        """
        try:
            parameters = inspect.signature(self.runner.run).parameters
        except (TypeError, ValueError):  # pragma: no cover - exotic runners
            return False
        if keyword in parameters:
            return True
        return any(
            item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
        )

    def _runner_takes_completed_steps(self) -> bool:
        """True when the runner accepts the ``completed_steps`` keyword."""
        return self._runner_takes("completed_steps")

    def _run_once(
        self,
        task_text: str,
        workdir: str | os.PathLike[str] | None,
        agent_name: str,
        task_id: str | None,
        completed: list[str],
        attempt_id: str | None = None,
        attempt_dir: str | os.PathLike[str] | None = None,
    ) -> RunResult:
        kwargs: dict[str, Any] = {
            "workdir": workdir,
            "agent": agent_name,
            "task_id": task_id,
        }
        if self._runner_takes_completed_steps():
            kwargs["completed_steps"] = list(completed)
        if self._runner_takes("attempt_id"):
            kwargs["attempt_id"] = attempt_id
        if self._runner_takes("attempt_dir"):
            kwargs["attempt_dir"] = attempt_dir
        return self.runner.run(task_text, **kwargs)

    def _run_with_retries(
        self,
        task_id: str | None,
        agent_name: str,
        task_text: str,
        workdir: str | os.PathLike[str] | None,
        completed: list[str],
        attempt_id: str | None = None,
        attempt_dir: str | os.PathLike[str] | None = None,
    ) -> tuple[RunResult, int]:
        """Run the execution body, retrying with exponential backoff.

        A retry never writes a second ``dispatch`` event: the task stays
        idempotent and the dependency graph counts it once.  Every retry
        writes a ``retry`` event and refreshes the lease.
        """
        policy = self.retry_policy(agent_name)
        attempts = 0
        result: RunResult | None = None
        while True:
            attempts += 1
            result = self._run_once(
                task_text,
                workdir,
                agent_name,
                task_id,
                completed,
                attempt_id=attempt_id,
                attempt_dir=attempt_dir,
            )
            if policy is None or attempts >= policy.max_attempts:
                break
            if result.exit_code not in policy.retry_on_exit_codes:
                break
            delay = round(policy.base_delay_s * (2 ** (attempts - 1)), 3)
            self.ledger.append(
                "retry",
                task_id,
                attempt=attempts,
                mode="backoff",
                attempt_id=attempt_id,
                max_attempts=policy.max_attempts,
                delay_s=delay,
                exit_code=result.exit_code,
                agent=agent_name,
                stderr=_tail(result.stderr, 300),
            )
            # the worker is alive: it just failed — move the lease forward
            self.ledger.append(
                "heartbeat",
                task_id,
                lease_expires_at=iso(utcnow() + timedelta(seconds=self.config.lease_s)),
                lease_s=self.config.lease_s,
                reason="retry",
                attempt=attempts,
            )
            if delay > 0:
                time.sleep(delay)
        assert result is not None  # the loop always runs at least once
        return result, attempts

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
        """Ledger summary, optionally for one task.

        Since v0.3.0 every task also carries the observability fields of the
        failure & recovery machinery: ``lease_expires_at``,
        ``lease_remaining_s``, ``age_s``, ``completed_steps`` /
        ``completed_steps_count``, ``recoveries`` and ``retries``.  No
        existing field changed its meaning — the new ones are additions.
        """
        states = self.ledger.states()
        moment = utcnow()
        for state in states.values():
            annotate_state(state, moment)
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

    # ------------------------------------------------------------------
    # failure & recovery operations (v0.3.0)
    # ------------------------------------------------------------------
    def heartbeat(self, task_id: str, now: datetime | None = None) -> dict:
        """Refresh the lease of a running task."""
        state = self.ledger.state_of(task_id)
        if state is None:
            raise UnknownTaskError(task_id)
        status = state.get("status")
        if status not in ("running", "dispatched"):
            return {
                "ok": False,
                "task_id": task_id,
                "status": status,
                "reason": "task_not_running",
                "notes": [f"only a running task holds a lease, this one is {status}"],
            }
        moment = now or utcnow()
        expiry = iso(moment + timedelta(seconds=self.config.lease_s))
        was_expired = lease_expired(state, moment)
        record = self.ledger.append(
            "heartbeat",
            task_id,
            lease_expires_at=expiry,
            lease_s=self.config.lease_s,
            previous_lease_expires_at=state.get("lease_expires_at"),
        )
        return {
            "ok": True,
            "task_id": task_id,
            "status": status,
            "lease_expires_at": expiry,
            "lease_remaining_s": float(self.config.lease_s),
            "lease_was_expired": was_expired,
            "event": record,
        }

    def checkpoint(self, task_id: str, step: str, artifact: str | None = None) -> dict:
        """Record that one step of a task is done."""
        state = self.ledger.state_of(task_id)
        if state is None:
            raise UnknownTaskError(task_id)
        name = (step or "").strip()
        if not name:
            return {
                "ok": False,
                "task_id": task_id,
                "reason": "step_missing",
                "notes": ["a checkpoint needs a step name"],
            }
        record = self.ledger.append("checkpoint", task_id, step=name, artifact=artifact)
        return {
            "ok": True,
            "task_id": task_id,
            "step": name,
            "artifact": artifact,
            "completed_steps": self.ledger.completed_steps(task_id),
            "event": record,
        }

    def reclaim(
        self,
        task_id: str | None = None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict:
        """Mark tasks whose lease expired as ``stale`` — they can run again."""
        moment = now or utcnow()
        states = self.ledger.states()
        if task_id is not None and task_id not in states:
            raise UnknownTaskError(task_id)
        marked: list[dict] = []
        active: list[dict] = []
        skipped: list[dict] = []
        targets = [task_id] if task_id is not None else sorted(states)
        for tid in targets:
            state = states.get(tid)
            if state is None:
                skipped.append({"task_id": tid, "status": None, "reason": "unknown_task"})
                continue
            status = state.get("status")
            if status not in ("running", "dispatched"):
                skipped.append({"task_id": tid, "status": status, "reason": "not_running"})
                continue
            expiry = state.get("lease_expires_at")
            if not expiry:
                # a ledger written before v0.3.0 has no lease at all — such a
                # task is never reclaimed automatically
                skipped.append({"task_id": tid, "status": status, "reason": "no_lease"})
                continue
            remaining = lease_remaining_s(state, moment)
            if remaining is not None and remaining > 0:
                active.append(
                    {
                        "task_id": tid,
                        "status": status,
                        "lease_expires_at": expiry,
                        "lease_remaining_s": remaining,
                    }
                )
                continue
            marked.append(
                {
                    "task_id": tid,
                    "status": status,
                    "lease_expires_at": expiry,
                    "lease_remaining_s": remaining,
                }
            )
            if not dry_run:
                self.ledger.append(
                    "stale",
                    tid,
                    reason="lease_expired",
                    lease_expires_at=expiry,
                    previous_status=status,
                    evaluated_at=iso(moment),
                )
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "now": iso(moment),
            "marked": marked,
            "active": active,
            "skipped": skipped,
            "marked_count": len(marked),
            "active_count": len(active),
            "skipped_count": len(skipped),
        }

    # ------------------------------------------------------------------
    # the attempt dimension (v0.3.0, batch B)
    # ------------------------------------------------------------------
    def _attempt_dir(
        self, workdir: str | os.PathLike[str] | None, attempt_id: str
    ) -> Path:
        """The artefact directory of one attempt: ``<workdir>/attempts/<id>``.

        The directory is created best effort — a read only workdir must not
        break the dispatch, the execution body then simply cannot use it.
        """
        base = Path(workdir).expanduser() if workdir else self.config.home
        path = base / "attempts" / attempt_id
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return path

    def _superseded(self, task_id: str | None, epoch: int) -> tuple[bool, str | None]:
        """Did the task move on while the worker was running?

        Two cases make the receipt of this attempt worthless as a result of
        *this* task:

        * the task was cancelled while the worker ran;
        * a newer attempt was started meanwhile (the epoch moved on).

        In both cases the receipt is written to the ledger but the fold
        refuses to take it for the current state — the late receipt is
        counted instead.
        """
        if not task_id:
            return False, None
        state = self.ledger.state_of(task_id)
        if not state:
            return False, None
        if state.get("status") == "cancelled":
            return True, "cancelled_during_run"
        current = state.get("epoch")
        try:
            current_int = int(current) if current is not None else None
        except (TypeError, ValueError):
            current_int = None
        if current_int is not None and current_int != int(epoch):
            return True, "superseded_by_new_attempt"
        return False, None

    def attempts(self, task_id: str) -> dict:
        """Every attempt of a task plus the state of the current one."""
        state = self.ledger.state_of(task_id)
        if state is None:
            raise UnknownTaskError(task_id)
        return {
            "ok": True,
            "task_id": task_id,
            "attempt_state": infer_attempt_state(state),
            "current_attempt_id": state.get("attempt_id"),
            "epoch": state.get("epoch"),
            "attempts": self.ledger.attempts(task_id),
            "late_receipts": int(state.get("late_receipts") or 0),
            "acceptance_evidence": state.get("acceptance_evidence"),
        }

    def retry(
        self,
        task_id: str,
        card_text: str | None = None,
        card_path: str | os.PathLike[str] | None = None,
        agent: str | None = None,
        workdir: str | os.PathLike[str] | None = None,
        reason: str = "",
        force: bool = False,
        dry_run: bool = False,
        actor: str | None = None,
    ) -> dict:
        """Run a task again — an explicit, **new** attempt.

        The difference to ``reclaim`` is the whole point:

        * ``reclaim`` is **passive** — it only marks a task whose lease
          expired as ``stale``; nothing is executed;
        * ``retry`` is **active** — it allocates a new attempt, runs the
          execution body again and accepts the artefact of *that* attempt.

        The card of the task is taken from ``card_text``, from
        ``card_path`` or from the path the last dispatch recorded.
        """
        state = self.ledger.state_of(task_id)
        if state is None:
            raise UnknownTaskError(task_id)
        text = self._card_text_for(task_id, card_text, card_path, workdir)
        if text is None:
            return {
                "ok": False,
                "task_id": task_id,
                "reason": "card_unavailable",
                "notes": [
                    "the card of the task is unknown — pass --card with the card file",
                ],
            }
        current = self.ledger.attempt_of(task_id) or {}
        attempt_state = infer_attempt_state(state)
        if not force and attempt_state == "still_running":
            return {
                "ok": False,
                "task_id": task_id,
                "reason": "attempt_still_running",
                "attempt_state": attempt_state,
                "notes": [
                    "the current attempt still holds a lease — "
                    "use --force to run it anyway"
                ],
            }
        attempt_id, epoch = self.ledger.next_attempt_id(task_id)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "epoch": epoch,
                "previous_attempt_id": current.get("attempt_id"),
                "attempt_state": attempt_state,
                "notes": ["dry run — nothing was written, nothing was executed"],
            }
        self.ledger.append(
            "retry",
            task_id,
            mode="explicit",
            attempt_id=attempt_id,
            epoch=epoch,
            previous_attempt_id=current.get("attempt_id"),
            reason=reason,
            actor=actor or resolve_actor(),
        )
        receipt = self.dispatch(
            text,
            agent=agent,
            workdir=workdir,
            force=True,
            actor=actor,
            card_path=card_path,
        )
        receipt["retry_mode"] = "explicit"
        receipt["previous_attempt_id"] = current.get("attempt_id")
        receipt.setdefault("attempt_id", attempt_id)
        receipt.setdefault("epoch", epoch)
        return receipt

    def _card_text_for(
        self,
        task_id: str,
        card_text: str | None,
        card_path: str | os.PathLike[str] | None,
        workdir: str | os.PathLike[str] | None,
    ) -> str | None:
        """The card of a task: explicit text, explicit path or the
        path the last dispatch recorded."""
        if card_text:
            return card_text
        candidates: list[Path] = []
        if card_path:
            candidates.append(Path(card_path).expanduser())
        for record in reversed(self.ledger.events(task_id)):
            if record.get("event") != "dispatch":
                continue
            detail = record.get("detail") or {}
            recorded = record.get("card_path") or detail.get("card_path")
            if recorded:
                path = Path(str(recorded)).expanduser()
                if not path.is_absolute():
                    base = Path(workdir).expanduser() if workdir else self.config.home
                    path = base / path
                candidates.append(path)
            break
        for path in candidates:
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
        return None

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
            if state.get("status") not in ("dispatched", "blocked", "stale"):
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
