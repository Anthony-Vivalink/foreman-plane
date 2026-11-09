"""Command line interface (SPEC §8).

``python -m foreman.cli`` or the ``foreman`` console script.

Exit codes: 0 success, 2 blocked by the gate, 3 artefact acceptance failed,
1 any other error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .acceptance import verify
from .config import describe, load_config, resolve_default_agent
from .errors import UnknownTaskError
from .runner import Dispatcher

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2
EXIT_ACCEPTANCE = 3

_JSON_FLAG = argparse.SUPPRESS


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=_JSON_FLAG,
        help="machine readable output (JSON)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="foreman",
        description="foreman-plane — task governance outside the agent harness",
    )
    _add_json(parser)
    sub = parser.add_subparsers(dest="command")

    dispatch = sub.add_parser("dispatch", help="dispatch a task card file")
    dispatch.add_argument("--file", required=True, help="path to the task card")
    dispatch.add_argument("--agent", help="execution body name")
    dispatch.add_argument("--workdir", help="working directory")
    dispatch.add_argument("--force", action="store_true", help="override the duplicate check")
    _add_json(dispatch)

    status = sub.add_parser("status", help="ledger status summary")
    status.add_argument("--task-id", help="restrict to one task")
    _add_json(status)

    ledger = sub.add_parser("ledger", help="ledger operations")
    ledger_sub = ledger.add_subparsers(dest="ledger_command")

    listing = ledger_sub.add_parser("list", help="list all tasks")
    listing.add_argument("--task-id", help="restrict to one task")
    _add_json(listing)

    show = ledger_sub.add_parser("show", help="show the events of one task")
    show.add_argument("task_id")
    _add_json(show)

    ready = ledger_sub.add_parser("ready", help="tasks whose dependencies are satisfied")
    _add_json(ready)

    blocked = ledger_sub.add_parser("blocked", help="tasks currently blocked")
    _add_json(blocked)

    cancel = ledger_sub.add_parser("cancel", help="cancel a task")
    cancel.add_argument("task_id")
    cancel.add_argument("--reason", default="")
    _add_json(cancel)

    replan = ledger_sub.add_parser("replan", help="re-plan a task")
    replan.add_argument("task_id")
    replan.add_argument("--reason", default="")
    replan.add_argument("--new-task-id", default=None)
    _add_json(replan)

    verify_cmd = sub.add_parser("verify", help="verify an acceptance string")
    verify_cmd.add_argument("--acceptance", required=True, help="acceptance string")
    verify_cmd.add_argument("--base-dir", default=None, help="base directory for relative paths")
    _add_json(verify_cmd)

    sub.add_parser("mcp", help="run the MCP stdio server")
    sub.add_parser("config", help="print the effective configuration")
    _add_json(sub.choices["mcp"])
    _add_json(sub.choices["config"])

    return parser


def _emit(payload: Any, as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(text if text else json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_dispatch(args: argparse.Namespace, dispatcher: Dispatcher) -> int:
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read task card: {exc}", file=sys.stderr)
        return EXIT_ERROR
    payload = dispatcher.dispatch(
        text,
        agent=args.agent,
        workdir=args.workdir,
        force=bool(args.force),
    )
    if payload.get("blocked"):
        _emit(payload, args.json, _dispatch_text(payload))
        return EXIT_BLOCKED
    if payload.get("acceptance_ok") is False:
        _emit(payload, args.json, _dispatch_text(payload))
        return EXIT_ACCEPTANCE
    if not payload.get("ok"):
        _emit(payload, args.json, _dispatch_text(payload))
        return EXIT_ERROR
    _emit(payload, args.json, _dispatch_text(payload))
    return EXIT_OK


def _dispatch_text(payload: dict) -> str:
    lines = [
        f"task_id: {payload.get('task_id')}",
        f"ok: {payload.get('ok')}",
    ]
    if payload.get("blocked"):
        lines.append(f"blocked: {payload.get('reason')}")
        if payload.get("missing"):
            lines.append(f"missing: {', '.join(payload['missing'])}")
        return "\n".join(lines)
    lines.append(f"verdict: {payload.get('verdict')}")
    lines.append(f"acceptance_ok: {payload.get('acceptance_ok')}")
    lines.append(f"exit_code: {payload.get('exit_code')}")
    if payload.get("stderr"):
        lines.append(f"stderr: {payload.get('stderr')}")
    if payload.get("needs_api_pending"):
        lines.append("needs_api_pending: true")
    return "\n".join(lines)


def _cmd_status(args: argparse.Namespace, dispatcher: Dispatcher) -> int:
    payload = dispatcher.status(args.task_id)
    if args.json:
        _emit(payload, True)
    else:
        if args.task_id:
            state = payload.get("task")
            if not state:
                print(f"unknown task: {args.task_id}")
            else:
                for key in ("task_id", "status", "verdict", "acceptance_ok", "exit_code", "last_ts"):
                    print(f"{key}: {state.get(key)}")
        else:
            for name, count in sorted((payload.get("counts") or {}).items()):
                print(f"{name}: {count}")
    return EXIT_OK


def _cmd_ledger(args: argparse.Namespace, dispatcher: Dispatcher) -> int:
    command = getattr(args, "ledger_command", None)
    if command is None:
        print("foreman ledger: expected one of list|show|ready|blocked|cancel|replan", file=sys.stderr)
        return EXIT_ERROR
    if command == "list":
        payload = dispatcher.status(getattr(args, "task_id", None))
        tasks = payload.get("tasks") or {}
        if getattr(args, "task_id", None):
            tasks = {args.task_id: tasks.get(args.task_id)} if args.task_id in tasks else {}
        if args.json:
            _emit(tasks, True)
        else:
            for task_id, state in sorted(tasks.items()):
                print(f"{task_id}\t{state.get('status')}\t{state.get('verdict')}")
        return EXIT_OK
    if command == "show":
        events = dispatcher.ledger.events(args.task_id)
        if args.json:
            _emit(events, True)
        else:
            for event in events:
                print(json.dumps(event, ensure_ascii=False))
        return EXIT_OK
    if command == "ready":
        payload = dispatcher.ready_tasks()
        _emit(payload, args.json, "\n".join(item["task_id"] for item in payload))
        return EXIT_OK
    if command == "blocked":
        payload = dispatcher.blocked_tasks()
        _emit(payload, args.json, "\n".join(
            f"{item['task_id']}\t{item.get('reason')}" for item in payload
        ))
        return EXIT_OK
    if command == "cancel":
        try:
            payload = dispatcher.cancel(args.task_id, args.reason)
        except UnknownTaskError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_ERROR
        _emit(payload, args.json, f"cancelled: {args.task_id}")
        return EXIT_OK
    if command == "replan":
        try:
            payload = dispatcher.replan(args.task_id, args.reason, args.new_task_id)
        except UnknownTaskError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_ERROR
        _emit(payload, args.json, f"replanned: {args.task_id}")
        return EXIT_OK
    print(f"unknown ledger command: {command}", file=sys.stderr)
    return EXIT_ERROR


def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify(args.acceptance, base_dir=args.base_dir)
    payload = result.to_dict()
    if args.json:
        _emit(payload, True)
    else:
        print(f"ok: {result.ok}")
        print(f"path: {result.path}")
        if result.reason:
            print(f"reason: {result.reason}")
        for check in result.checks:
            print(f"  {check['name']}: expected={check['expected']} actual={check['actual']} ok={check['ok']}")
    return EXIT_OK if result.ok else EXIT_ACCEPTANCE


def _cmd_config(args: argparse.Namespace) -> int:
    config = load_config()
    payload = describe(config)
    payload["default_agent"] = resolve_default_agent()
    if args.json:
        _emit(payload, True)
    else:
        print(f"FOREMAN_HOME: {payload['home']}")
        print(f"ledger: {payload['ledger_path']}")
        print(f"runs_dir: {payload['runs_dir']}")
        print(f"timeout_s: {payload['timeout_s']}")
        print(f"default_agent: {payload['default_agent']}")
        print(f"agents: {', '.join(payload['agents'])}")
    return EXIT_OK


def _cmd_mcp() -> int:
    from .mcp_server import main as mcp_main

    return mcp_main()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "json"):
        args.json = False
    as_json = bool(args.json)
    command = getattr(args, "command", None)
    if not command:
        parser.print_help()
        return EXIT_ERROR

    if command == "mcp":
        return _cmd_mcp()

    dispatcher = Dispatcher(load_config())
    if command == "dispatch":
        return _cmd_dispatch(args, dispatcher)
    if command == "status":
        return _cmd_status(args, dispatcher)
    if command == "ledger":
        return _cmd_ledger(args, dispatcher)
    if command == "verify":
        return _cmd_verify(args)
    if command == "config":
        return _cmd_config(args)
    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
