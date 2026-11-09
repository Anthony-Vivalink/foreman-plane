"""Configuration resolution (SPEC §9).

Every path used by foreman-plane comes from configuration:

1. ``FOREMAN_HOME`` environment variable
2. ``$HOME/.foreman`` (skipped when ``HOME`` is not set — never crash)
3. ``<cwd>/.foreman``

No absolute path constant and no host module is referenced anywhere.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, MutableMapping

DEFAULT_TIMEOUT_S = 900
DEFAULT_AGENT = "generic"

ENV_HOME = "FOREMAN_HOME"
ENV_TIMEOUT = "FOREMAN_TIMEOUT"
ENV_AGENT = "FOREMAN_AGENT"
ENV_ACTOR = "FOREMAN_ACTOR"

AGENTS_FILE = "agents.json"
LEDGER_FILE = "ledger.jsonl"
RUNS_DIR = "runs"

#: Built-in execution bodies.  Every one of them is a generic CLI agent
#: entry point — none of them depends on the host harness.
BUILTIN_AGENTS: dict[str, dict] = {
    "claude": {"command": "claude -p {task}", "description": "Claude Code CLI"},
    "codex": {"command": "codex exec {task}", "description": "Codex CLI"},
    "dsh": {"command": "dsh headless {task}", "description": "DeepSeek Harness, headless"},
    "generic": {"command": "", "description": "custom agent, define it in agents.json"},
}


@dataclass
class ForemanConfig:
    """Effective configuration of one foreman-plane instance."""

    home: Path
    ledger_path: Path
    runs_dir: Path
    agents: dict[str, dict] = field(default_factory=dict)
    timeout_s: int = DEFAULT_TIMEOUT_S


def resolve_home(
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve ``FOREMAN_HOME`` following SPEC §3.1."""
    env = os.environ if env is None else env
    raw = (env.get(ENV_HOME) or "").strip()
    if raw:
        return Path(raw).expanduser()
    for var in ("HOME", "USERPROFILE"):
        home = (env.get(var) or "").strip()
        if home:
            return Path(home).expanduser() / ".foreman"
    base = Path(cwd) if cwd else Path.cwd()
    return base / ".foreman"


def load_agents(home: str | os.PathLike[str]) -> dict[str, dict]:
    """Load ``${FOREMAN_HOME}/agents.json`` merged over the built-in agents.

    The file is optional.  A malformed file never raises: the built-in
    agents are returned instead.
    """
    agents: MutableMapping[str, dict] = {name: dict(spec) for name, spec in BUILTIN_AGENTS.items()}
    path = Path(home).expanduser() / AGENTS_FILE
    try:
        if not path.is_file():
            return dict(agents)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(agents)
    if not isinstance(data, dict):
        return dict(agents)
    extra = data.get("agents")
    if isinstance(extra, dict):
        for name, spec in extra.items():
            if not isinstance(spec, dict):
                continue
            merged = dict(agents.get(str(name), {}))
            merged.update(spec)
            agents[str(name)] = merged
    return dict(agents)


def _resolve_timeout(env: Mapping[str, str]) -> int:
    raw = (env.get(ENV_TIMEOUT) or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = DEFAULT_TIMEOUT_S
        if value > 0:
            return value
    return DEFAULT_TIMEOUT_S


def load_config(
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> ForemanConfig:
    """Build the effective configuration."""
    env = os.environ if env is None else env
    home = resolve_home(env, cwd)
    return ForemanConfig(
        home=home,
        ledger_path=home / LEDGER_FILE,
        runs_dir=home / RUNS_DIR,
        agents=load_agents(home),
        timeout_s=_resolve_timeout(env),
    )


def resolve_default_agent(env: Mapping[str, str] | None = None) -> str:
    """Name of the execution body used when the caller does not pick one."""
    env = os.environ if env is None else env
    return (env.get(ENV_AGENT) or "").strip() or DEFAULT_AGENT


def resolve_actor(env: Mapping[str, str] | None = None) -> str:
    """Name recorded in the ``actor`` field of every ledger event."""
    env = os.environ if env is None else env
    return (env.get(ENV_ACTOR) or "").strip() or "foreman"


def ensure_dirs(config: ForemanConfig) -> None:
    """Create the home / runs directories, ignoring filesystem failures."""
    for directory in (config.home, config.runs_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def describe(config: ForemanConfig, env: Mapping[str, str] | None = None) -> dict:
    """Human/machine readable snapshot of the effective configuration."""
    env = os.environ if env is None else env
    return {
        "home": str(config.home),
        "ledger_path": str(config.ledger_path),
        "runs_dir": str(config.runs_dir),
        "timeout_s": config.timeout_s,
        "default_agent": resolve_default_agent(env),
        "agents": sorted(config.agents),
    }


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_AGENT",
    "ENV_HOME",
    "ENV_TIMEOUT",
    "ENV_AGENT",
    "ENV_ACTOR",
    "BUILTIN_AGENTS",
    "ForemanConfig",
    "resolve_home",
    "load_agents",
    "load_config",
    "resolve_default_agent",
    "resolve_actor",
    "ensure_dirs",
    "describe",
]
