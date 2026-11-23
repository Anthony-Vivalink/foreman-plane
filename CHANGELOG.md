# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.0 (2026-11-23) — Failure & Recovery

### Added

- **A lease, a heartbeat and a reclaim.**  A successful dispatch now takes a
  lease (`lease_expires_at`, `lease_s`, 900 seconds by default) and the ledger
  records it.  `foreman-plane heartbeat <task_id>` renews it,
  `foreman-plane reclaim [--dry-run] [--now ISO8601]` marks every task whose
  lease expired while it was still running as `stale`.  A stale task can be
  dispatched again — before, a worker killed with `kill -9` left the ledger on
  `running` and every re-dispatch was refused as `duplicate_task`, so the task
  was stuck forever.
- **Step checkpoints.**  `foreman-plane checkpoint <task_id> --step NAME
  [--artifact PATH]` records that one step is done.  A re-dispatch hands the
  collected step names to the execution body through the new optional
  `{completed_steps}` placeholder (`["a","b"]`, `[]` when there is none), so
  the finished work is not executed twice.  The receipt reports
  `completed_steps`, `skipped_steps` and `resumed`.
- **Backoff retry.**  Optional per execution body:
  `"retry": {"max_attempts": 3, "base_delay_s": 5, "retry_on_exit_codes": [124]}`.
  Every attempt is recorded as a `retry` event; a retry never writes a second
  `dispatch` event, so the task stays idempotent.
- **Observability.**  `foreman-plane status` shows, per task,
  `lease_expires_at`, `lease_remaining_s`, `age_s`, `completed_steps`,
  `completed_steps_count`, `recoveries` and `retries`.
- **A failure drill that really fails.**  `scripts/failure_drill.sh` prepares
  a fake three-step worker, kills it with `kill -9` half way through, shows
  the refused re-dispatch, lets the lease expire, reclaims the task,
  dispatches it again and checks that the finished step did not run twice.
  The output of a real run is in `docs/failure_drill_sample_output.txt`.
- **Attempts — the unit of work.**  Every dispatch allocates an attempt
  (`attempt_id`, `epoch`) and its own artefact directory
  (`<workdir>/attempts/<attempt_id>/`), so the artefacts of two attempts
  of the same task never mix.  `foreman-plane attempts <task_id>` lists
  them and reports the derived `attempt_state`: `still_running`,
  `interrupted`, `outcome_unknown`, `finished` or `not_started`.
  `foreman-plane retry <task_id>` runs the task again as an explicit new
  attempt.  The semantics are **at-least-once with an idempotency key
  `(task_id, attempt_id)` — never exactly-once**.
- **The acceptance accepts the current attempt only.**  A relative
  artefact path is looked up inside the attempt directory first; the
  receipt records the path, the sha256 and the checks that were really
  read.  A receipt that arrives after a cancel (or after a newer attempt
  took over) is written to the ledger but never counted as the success
  of the current attempt.
- **Contract fixtures instead of prose.**  `tests/fixtures/` holds eight
  sanitised fixtures (task card, ledger with attempts, legacy ledger,
  corrupt ledger, acceptance sheet, artefact, contract, manifest), and
  the public implementation and the local shell implementation run the
  same contract check (`scripts/contract_fixture_check.sh`).  The suite
  adds the crash-recovery and JSONL-reading regressions (half a line,
  non-JSON, unknown event kind, truncated file, missing file).
- **A second drill — `scripts/artifact_gate_drill.sh`.**  The worker writes
  an artefact and is killed with `kill -9` before it can write `done`,
  the dependent task is refused, the lease expires, the task is reclaimed
  and explicitly retried as a second attempt — and only the artefact of
  that second attempt is accepted; the decoy left by the first one is
  ignored.  The real output is in
  `docs/artifact_gate_drill_sample_output.txt`.

### Changed

- **Run logs never overwrite each other.**  A run log is named
  `{task_id}-{YYYYmmdd-HHMMSS}.log`; two runs of the same task inside the
  same second get a numeric suffix (`-2`, `-3`, ...) instead of overwriting
  each other, and the file is created exclusively so no log is ever lost.
- **The duplicate check follows the state, not the history.**  `blocked` and
  `stale` tasks are re-dispatchable, a `running` task with a live lease is
  not, and a `running` task whose lease expired is (the worker is presumed
  dead).  A task that is dispatched again after its lease expired gets a
  `stale` event first, so the ledger says why it ran twice.
- Five new ledger event kinds: `heartbeat`, `stale`, `checkpoint`, `retry`, `attempt`.

### Unchanged on purpose

- **The contracts did not move.**  The ledger record format, the task card
  markers, the MCP tool names (`foreman_dispatch` / `foreman_status` /
  `foreman_cancel`), the `FOREMAN_HOME` layout and the four acceptance
  criterion keys are exactly the same as in 0.2.1.  Everything that was added
  is an **optional** marker (`{completed_steps}`, `lease_s`, `retry`) or a
  **new event kind** — a reader that does not know the new kinds ignores them.
- **Retry is off by default.**  Without a `retry` block the plane runs the
  execution body exactly once, like 0.2.x.
- **The MCP surface did not grow.**  The three tools keep their names and
  their payloads; the new capabilities are CLI commands and ledger events.

## 0.2.1

### Changed

- **The public surface is now job-ready.**  Both READMEs were rewritten
  around what a reader actually needs in the first five minutes: what the
  plane is, how a task card is written, what the receipt means, how to read
  the ledger, and what it deliberately does not do.  A new *How this was
  built* chapter states plainly which parts of the repository were written by
  coding agents and which decisions — the task card contract, the meaning of
  "done", the append-only rule — were made by hand.
- **No local machine paths are published any more.**  The instance paths that
  used to appear in `DIVERGENCE.md` and `docs/IMPLEMENTATION_REPORT.md` are
  now written as placeholders, and the PyPI upload script reads its token
  file from `$HOME` instead of a fixed location.
- **Community files were added**: `SECURITY.md`, `CONTRIBUTING.md`, a bug
  report template and a pull request template.

### Unchanged on purpose

- **No behaviour change.**  The ledger format, the task card contract, the
  acceptance criterion keys, the MCP tool names and the `FOREMAN_HOME`
  layout are byte-for-byte the same as in 0.2.0.  This release only touches
  the published surface, so an existing 0.2.0 install keeps working.

## 0.2.0

### Changed

- **The console command is now `foreman-plane`, with `gongtou` as a short
  alias.**  The `foreman` command is gone.  That name is taken by other
  projects — an AI orchestration engine on npm, an infrastructure manager on
  GitHub — and two packages shipping the same binary name is a real conflict
  on `PATH`.
- **The Python package is now `foreman_plane`** (was `foreman`).  PyPI's
  `foreman` library installs a top-level `foreman` module, so installing both
  would have overwritten files.  The new spellings are `import foreman_plane`,
  `python -m foreman_plane.cli` and `python -m foreman_plane.mcp_server`.
- **Both READMEs now state the scope up front: a personal tool, not an
  enterprise platform.**  What it is (one person, one machine, one state
  directory, plain files), what it is not and will not grow into (no
  multi-tenancy, no RBAC, no SSO, no workflow engine, no database, no UI),
  why that is a feature, and what to reach for instead when you need
  team-scale workflow, approval or audit tooling.
- The tagline and the PyPI summary now say "personal toolchain" and
  "personal-tool grade".

### Unchanged on purpose

- **The MCP tool names stay `foreman_dispatch`, `foreman_status` and
  `foreman_cancel`.**  They are a namespace inside MCP, not a binary name, so
  the rename does not break existing clients.
- **`FOREMAN_HOME` and its default `$HOME/.foreman` are unchanged**, so
  existing ledgers keep working.

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
