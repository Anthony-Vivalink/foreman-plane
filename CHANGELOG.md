# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0

First release.

### Added

- **Task card contract** (`foreman.contract`): four markers — `【id】`,
  `【depends_on】`, `【needs_api】`, `【验收】` — tolerant of full-width/half-width
  brackets, casing and colon variants.  A marker that yields no legal token is
  reported through an explicit `*_unparsed` flag instead of being silently
  treated as "no dependency".
- **Two-level dependency gate** (`foreman.gate`): hard dependencies must be
  terminal, `pass` and accepted; soft dependencies only have to exist and are
  reported as `needs_api_pending`.  Six blocking reasons: `task_id_missing`,
  `dependency_unparsed`, `dependency_cycle`, `dependency_unknown`,
  `dependency_pending`, `dependency_failed`, `dependency_unaccepted`.
- **Append-only ledger** (`foreman.ledger`): JSONL events (`dispatch`,
  `running`, `blocked`, `done`, `cancelled`, `replan`), state folding, task
  id idempotency and `UnknownTaskError` for unknown task ids.
- **Objective artefact acceptance** (`foreman.acceptance`):
  `path::min_bytes=N::sha256=<hex>::contains=<substr>::exit_code=N`.
- **Execution body adapters** (`foreman.runner`): `claude`, `codex`, `dsh`,
  a `generic` template and custom agents declared in `agents.json`.
- **MCP server** (`foreman.mcp_server`): JSON-RPC 2.0 over stdio with NDJSON
  framing, three tools (`foreman_dispatch`, `foreman_status`,
  `foreman_cancel`).
- **CLI** (`foreman.cli`): `dispatch`, `status`, `ledger`, `verify`, `mcp`,
  `config`, with `--json` output and stable exit codes.
- Zero third-party dependencies, Python >= 3.10, `unittest` test suite.

[0.1.0]: https://example.invalid/foreman-plane/releases/tag/v0.1.0
