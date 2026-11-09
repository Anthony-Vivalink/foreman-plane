# foreman-plane

**A task governance plane that runs *outside* your agent harness.**

foreman-plane (the "foreman") is the layer *above* the agent, not inside it.
Claude Code, Codex, DeepSeek Harness or any CLI agent are pluggable
execution bodies; the plane owns the parts an agent session cannot own:
dispatch, dependency gating, objective acceptance and an append-only audit
trail.

```
        ┌───────────────────────────────────────────┐
        │            foreman-plane (the foreman)    │
        │  contract → gate → ledger → acceptance    │
        └───┬───────────────┬───────────────┬───────┘
            │ MCP/stdio     │ MCP/stdio     │ MCP/stdio
       ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
       │ Claude   │    │  Codex   │    │   DSH    │      ← execution bodies
       └──────────┘    └──────────┘    └──────────┘
```

* **Cross session** — the ledger outlives every conversation.
* **Cross instance** — several agents share one task graph.
* **Cross day** — a task dispatched today can be resumed tomorrow by any body.
* **Outside the harness** — the plane is a plain process; no agent framework
  has to be patched to use it.

Zero third-party dependencies. Python ≥ 3.10. Standard library only.

---

## Quick start

```bash
# 1. run it straight from the checkout
cd foreman-plane
export PYTHONPATH="$PWD/src"
export FOREMAN_HOME="$PWD/.foreman"

# 2. declare one execution body
cat > "$FOREMAN_HOME/agents.json" <<'JSON'
{"agents": {"mybot": {"command": "mybot run --prompt {task}"}}}
JSON

# 3. dispatch a task card
cat > card.md <<'MD'
【id】T-20261109-20
【depends_on】T-20261109-01
【验收】reports/report.md::min_bytes=200
写一份报告。
MD

python3 -m foreman.cli dispatch --file card.md --agent mybot --json
```

`python -m foreman.cli` and the `foreman` console script are equivalent.

---

## The task card

A task card is plain text.  Four markers may appear anywhere in it:

| marker          | meaning                                                      |
| --------------- | ------------------------------------------------------------ |
| `【id】`         | task id, `T-YYYYMMDD-NN`                                      |
| `【depends_on】` | **hard** dependency — the upstream must be done and `pass`    |
| `【needs_api】`  | **soft** dependency — an artefact that only has to exist       |
| `【验收】`       | acceptance artefact plus optional objective criteria           |

```text
【id】T-20261109-20
【depends_on】T-20261109-01
【needs_api】T-20261109-01#/var/lib/foreman/api/order_book.json
【验收】reports/order_book_20261109.md::min_bytes=200::contains=结论

请读取 order_book.json，产出分析报告并给出结论小节。
```

Marker syntax is forgiving (`【id】`, `[id]`, `ID:`, full-width spaces and
colons all work), but a dependency that cannot be parsed is **never**
silently dropped: the dispatch is refused with `dependency_unparsed`.

---

## What the plane guarantees

### Hard gate — a task never runs too early

A task with `【depends_on】` only starts when every upstream task is
terminal, passed **and** accepted.  Otherwise the plane writes a `blocked`
event and stops.  There is no `dispatch` event for a task that did not run,
so the ledger cannot show a ghost dispatch.

```text
【depends_on】T-20261109-01  →  T-20261109-01 done, verdict=pass  →  dispatch
                             →  T-20261109-01 still running       →  blocked(dependency_pending)
                             →  T-20261109-01 unknown              →  blocked(dependency_unknown)
                             →  T-20261109-01 verdict=fail         →  blocked(dependency_failed)
                             →  unparsable dependency marker       →  blocked(dependency_unparsed)
```

### Soft gate — a task may start without a missing artefact

`【needs_api】` marks artefacts that must be *ready* but do not have to be
finished.  A missing artefact is reported as `needs_api_pending` and the
task still runs.

### Objective acceptance — the artefact decides, not the model

```text
【验收】reports/report.md::min_bytes=200::sha256=<hex>::contains=结论
```

Supported criteria: `min_bytes`, `sha256`, `contains`, `exit_code`.  All
criteria are ANDed, relative paths resolve against the working directory,
and an unknown criterion fails closed.

### Append-only audit trail

Every decision is one JSON line in `${FOREMAN_HOME}/ledger.jsonl`:

```json
{"ts":"2026-11-09T09:41:02.113456+00:00","event":"dispatch","task_id":"T-20261109-20","actor":"foreman","depends_on":["T-20261109-01"],"needs_api":[],"acceptance":"reports/report.md::min_bytes=200","needs_api_pending":false,"detail":{}}
```

Event types: `dispatch`, `running`, `blocked`, `done`, `cancelled`,
`replan`.  History is never rewritten or deleted.

---

## MCP integration

The plane ships an MCP server that speaks JSON-RPC 2.0 over stdio with
NDJSON framing (one JSON object per line).  Point any MCP client at it:

```bash
python3 -m foreman.mcp_server
```

For a harness that loads MCP servers from a patch file, the entry looks
like this (the exact field names follow the MCP client in use — only the
stdio JSON-RPC handshake is fixed by the plane):

```yaml
# ~/.dsh/profiles/headless/cordis.patch.yml
mcp:
  servers:
    - name: foreman
      command: python3
      args:
        - -m
        - foreman.mcp_server
      env:
        FOREMAN_HOME: /var/lib/foreman
```

Three tools are exposed:

| tool              | purpose                                                    |
| ----------------- | ---------------------------------------------------------- |
| `foreman_dispatch`| dispatch a task card (parse → gate → run → accept)         |
| `foreman_status`  | ledger status of one task or of everything                 |
| `foreman_cancel`  | cancel a task (unknown ids are rejected)                   |

---

## CLI

```
foreman dispatch --file TASK.md [--agent NAME] [--workdir DIR] [--force] [--json]
foreman status   [--task-id ID] [--json]
foreman ledger   list | show | ready | blocked | cancel | replan
foreman verify   --acceptance "<验收串>"
foreman mcp
foreman config
```

Exit codes: `0` success · `2` blocked by the gate · `3` artefact acceptance
failed · `1` any other error.

---

## Configuration

| variable          | meaning                                              |
| ----------------- | ---------------------------------------------------- |
| `FOREMAN_HOME`    | plane home (ledger, runs, agents.json)                |
| `FOREMAN_TIMEOUT` | execution timeout in seconds (default 900)            |
| `FOREMAN_AGENT`   | default execution body (default `generic`)            |
| `FOREMAN_ACTOR`   | actor recorded in the ledger (default `foreman`)      |

`FOREMAN_HOME` is resolved as: the environment variable → `$HOME/.foreman`
→ `<cwd>/.foreman`.  When `HOME` is not set the plane still works.

---

## How this differs from other approaches

* **Against "the agent will remember"** — a session that forgets cannot
  enforce a dependency.  The gate is a separate process with its own
  durable state, so it keeps working across sessions, agents and days.
* **Against "the agent will self-report"** — the verdict is derived from an
  artefact on disk (`min_bytes`, `sha256`, `contains`, `exit_code`), not from
  a sentence in the transcript.  A task that promises a report but writes no
  report fails.
* **Against "one big agent"** — the plane does not choose a model and does
  not care which agent runs a task.  Execution bodies are interchangeable
  commands; governance stays in one place.
* **Against "a workflow engine"** — there is no DAG runtime, no scheduler and
  no background worker.  The plane is a ledger plus a gate: it decides
  *whether* a task may start, and leaves *how* to the body that is wired in.
* **Against "a chat transcript as state"** — the ledger is append-only and
  machine readable, so it can be queried, diffed and audited by something
  that is not an LLM.

---

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The specification lives in [`docs/SPEC.md`](docs/SPEC.md), the wire
contract in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## License

MIT — see [`LICENSE`](LICENSE).
