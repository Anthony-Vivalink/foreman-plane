"""Exception types for foreman-plane.

Design rule (SPEC §13): exceptions never escape to the caller of the
library.  Library functions translate failures into structured ``reason``
codes; only the CLI layer turns them into exit codes.
"""

from __future__ import annotations


class ForemanError(Exception):
    """Base class for every error raised by foreman-plane."""


class ConfigError(ForemanError):
    """Invalid or unusable configuration."""


class ContractError(ForemanError):
    """The task card could not be parsed into a usable contract."""


class LedgerError(ForemanError):
    """The ledger could not be read or appended to."""


class UnknownTaskError(ForemanError):
    """A task id referenced by the caller does not exist in the ledger."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"unknown task id: {task_id}")


class RunnerError(ForemanError):
    """The execution body could not be prepared."""


class AcceptanceError(ForemanError):
    """The acceptance specification is malformed."""


__all__ = [
    "ForemanError",
    "ConfigError",
    "ContractError",
    "LedgerError",
    "UnknownTaskError",
    "RunnerError",
    "AcceptanceError",
]
