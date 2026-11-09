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
