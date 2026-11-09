# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.2

### Added

- **A Deployment chapter in both READMEs** — what "deployment" means for a
  plain process (no daemon, no port, no scheduler), then four steps: check the
  install, give the plane a home, pick an execution body, hand in the first
  card.  It also covers how to wire the plane into DSH / Claude Code / Codex,
  who decides what gets dispatched, the full configuration reference, what the
  plane deliberately does not do, and a troubleshooting table mapping every
  exit code and gate reason to a fix.
- **A deployment diagram** in English and Chinese, rendered deterministically
  by `scripts/render_diagrams.py`: three entry points, one core, one ledger.
- The rendered diagrams (`.png` and `.svg`) are now part of the sdist, so a
  source install carries them too.

### Fixed

- **A failure before the execution body starts is now diagnosable.**  An
  unknown agent, an empty command template, a template without the `{task}`
  placeholder and a command that is not on `PATH` all used to end in
  `exit_code: 127` with no run log and no stderr in the receipt — the operator
  saw only the number.  Each case now says which one it was, writes the usual
  log under `${FOREMAN_HOME}/runs/`, and the receipt carries the tail of
  stderr.
- **The MCP handshake no longer reports a stale version.**  `serverInfo.version`
  was hard-coded to `0.1.0`; it now follows the package version.

## 0.1.1

### Changed

- **The canonical task card markers are now half-width brackets** — `[id]`,
  `[depends_on]`, `[needs_api]`, `[acceptance]`.  The full-width forms
  (`【id】`, `【验收】`) and the colon forms (`id:`, `验收:`) stay accepted, so
  cards written for 0.1.0 keep working untouched.
- **`acceptance` is an ASCII alias of `验收`** and is recognised in the
  bracketed form only.  English prose such as `acceptance criteria: ...` can
  therefore never be mistaken for a marker.
- **READMEs rewritten in the DSH plugin house style** — an English `README.md`
  and a Chinese `README_ZH.md`, each in one language, with a language switcher,
  badges, a "what it feels like" section and the DSH / MCP install path.
- Prose no longer uses parentheses asides; em dashes and tables carry the
  same meaning.

### Added

- **Rendered diagrams** for the architecture and the task-card pipeline, in
  English and Chinese, generated deterministically by
  `scripts/render_diagrams.py` (Graphviz).  The READMEs embed the PNGs through
  absolute raw URLs so PyPI renders them as well.
- The ASCII art stays as the text fallback and is now inside a code fence — it
  used to sit outside one, so GitHub collapsed it into a single run-on line.
  Box widths are computed by display width, so full-width CJK columns line up.

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
