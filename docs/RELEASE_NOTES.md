## foreman-plane v0.3.0 — Failure & Recovery

Failure & recovery. **The contracts did not move** — the ledger record
format, the task card markers, the MCP tool names, the `FOREMAN_HOME`
layout and the four acceptance criterion keys are exactly the same as in
v0.2.1. Everything that was added is an *optional* marker or a *new event
kind*, so a reader that does not know the new kinds ignores them.

### What changed since v0.2.1

**A worker that dies no longer strands the task**

Before, a worker killed with `kill -9` left the ledger on `running`, and
every re-dispatch was refused as `duplicate_task` — the task was stuck
forever and the only way out was to force the whole card to run again,
repeating the steps that had already succeeded.

Now a dispatch takes a **lease** (900 seconds by default). The execution
body can renew it with `foreman-plane heartbeat <task_id>`; when the lease
expires, `foreman-plane reclaim [--dry-run] [--now ISO8601]` marks the task
`stale` and it becomes re-dispatchable. The ledger says why it ran twice.

**Finished steps are not repeated**

`foreman-plane checkpoint <task_id> --step NAME [--artifact PATH]` records
that one step is done. A re-dispatch hands the collected step names to the
execution body through the new optional `{completed_steps}` placeholder
(`["a","b"]`, `[]` when there is none) — a template that does not mention
the placeholder behaves exactly as in 0.2.x.

**An attempt is the unit of work**

Every dispatch allocates an attempt (`attempt_id`, `epoch`) and its own
artefact directory, so the artefacts of two attempts of the same task never
mix. `foreman-plane attempts <task_id>` reports the derived
`attempt_state`; `foreman-plane retry <task_id>` runs the task again as an
explicit new attempt. The semantics are **at-least-once with an idempotency
key `(task_id, attempt_id)` — never exactly-once**, and the documentation
says so.

**The acceptance accepts the current attempt only**

A relative artefact path is resolved inside the attempt directory first.
The receipt records the path, the sha256 and the checks that were really
read. A receipt that arrives after a cancel — or after a newer attempt took
over — is written to the ledger but never counted as the success of the
current attempt.

**Two drills that really fail**

`scripts/failure_drill.sh` kills a three-step worker with `kill -9` half way
through, shows the refused re-dispatch, lets the lease expire, reclaims the
task, dispatches it again and checks that the finished step did not run
twice. `scripts/artifact_gate_drill.sh` does the harder version: the
worker writes an artefact and dies before it can write `done`, the
dependent task is refused, the task is reclaimed and retried as a second
attempt, and only *that* attempt's artefact is accepted — the decoy left by
the first one is ignored. The real outputs are in `docs/`.

### Unchanged on purpose

- **MCP tool names stay `foreman_dispatch`, `foreman_status` and
  `foreman_cancel`.** The new capabilities are CLI commands and ledger
  events; the MCP surface did not grow.
- **Retry is off by default.** Without a `retry` block the plane runs the
  execution body exactly once, like 0.2.x.
- **`FOREMAN_HOME` and its default `$HOME/.foreman` are unchanged.**

### Install

```bash
pip install -U foreman-plane
```

## foreman-plane v0.2.1

A documentation-and-hygiene release. **Nothing about how the plane behaves
changed** — the ledger format, the task card contract, the acceptance
criteria, the MCP tool names and the state directory layout are exactly as
they were in v0.2.0.

### What changed since v0.2.0

**The public surface was rewritten for a first-time reader**

- Both READMEs now lead with the contract: what a task card may contain, what
  the receipt means, what lands in the ledger, and what the plane refuses to
  do.
- A new *How this was built* chapter says which modules were written by
  coding agents and, for each area, which decisions were made by hand — the
  marker set, the definition of "done" (the artefact on disk, never a
  self-report), the append-only rule, the stable exit codes, the release
  rules.

**No local machine paths are published any more**

- The instance paths that used to appear in `DIVERGENCE.md` and
  `docs/IMPLEMENTATION_REPORT.md` are now placeholders, and the PyPI upload
  script reads its token file from `$HOME`.

**Community files were added**

- `SECURITY.md`, `CONTRIBUTING.md`, a bug report template and a pull request
  template.

## foreman-plane v0.2.0

A task governance plane that runs **outside** your agent harness: dispatch, two-level dependency gates, objective acceptance, an append-only audit ledger. Zero third-party dependencies, standard library only, Python ≥ 3.10.

**This release renames the command and states the scope.** The tool itself works exactly as before.

### What changed since v0.1.2

**The command is now `foreman-plane`**

- `foreman` is **gone** as a command. The name is taken by other projects — an AI orchestration engine on npm, an infrastructure manager on GitHub — and two packages shipping the same binary name is a real conflict on `PATH`.
- Two commands are installed instead, and they are the same program:
  - `foreman-plane` — the canonical name, matching the PyPI project and the repository.
  - `gongtou` — the short alias (工头), for people who prefer it. It reports its own name in `--help`.
- The Python package is now `foreman_plane` (was `foreman`). PyPI's `foreman` library installs a top-level `foreman` module, so installing both would have overwritten files. `import foreman_plane` is the new spelling; the module entry points are `python -m foreman_plane.cli` and `python -m foreman_plane.mcp_server`.

**The scope is now written down**

Both READMEs carry a new *Scope* chapter that says what this is and, more importantly, what it is not:

- It is a plain process for one person, one machine and one state directory — plain files you can read with `cat` and `jq`.
- It is not a multi-tenant platform (no tenancy, RBAC or SSO), not a durable workflow engine (no daemon, broker, timers or HA), not a database (the ledger is a JSONL file), not an enterprise audit product, not a place for business logic and not a dashboard.
- A table says what to reach for instead when you need team-scale workflow (Temporal / Airflow / Argo Workflows), approval and audit tooling (Jira / ServiceNow / a workflow platform with a real admin model) or human task tracking (Beads, an issue tracker).
- The tagline and the PyPI summary now say "personal toolchain" and "personal-tool grade".

### Unchanged on purpose

- **MCP tool names stay `foreman_dispatch`, `foreman_status` and `foreman_cancel`.** They are a namespace inside MCP, not a binary name, so existing clients keep working.
- **`FOREMAN_HOME` and its default `$HOME/.foreman` are unchanged**, so existing ledgers keep working.
- The task card contract, the gates, the acceptance criteria and the ledger format are untouched.

### Install

```bash
pip install foreman-plane
```

### Upgrade notes

**This is a breaking change for the command name only.** If you scripted the old command:

```bash
# before
foreman dispatch --file TASK.md
# after
foreman-plane dispatch --file TASK.md      # or: gongtou dispatch --file TASK.md
```

If you imported the Python package or pointed an MCP client at the module:

```bash
# before
python3 -m foreman.mcp_server
# after
python3 -m foreman_plane.mcp_server
```

MCP client configuration:

```json
{
  "mcpServers": {
    "foreman": {
      "command": "python3",
      "args": ["-m", "foreman_plane.mcp_server"],
      "env": { "FOREMAN_HOME": "/var/lib/foreman" }
    }
  }
}
```

Everything else is a plain `pip install -U foreman-plane`.

Assets below are the wheel and the sdist. The sdist ships the docs, the examples and the diagram sources, so the figures can be regenerated with `python3 scripts/render_diagrams.py`.
