"""foreman-plane — a task governance plane that lives *outside* the agent harness.

The plane owns dispatch, two-level dependency gating, objective artefact
acceptance and an append-only audit ledger.  Any CLI agent (Claude Code,
Codex, DeepSeek Harness, ...) is just a pluggable execution body.

Zero third-party dependencies: standard library only, Python >= 3.10.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
