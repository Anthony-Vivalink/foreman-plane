# foreman-plane protocol

Version: 0.1.0 · Transport: JSON-RPC 2.0 over stdio (NDJSON) · Ledger: JSONL

This document is the human readable contract of the plane: the task card
syntax, the ledger event model, the MCP tool signatures and the acceptance
criteria syntax.

---

## 1. Task card contract

### 1.1 Markers

| marker          | semantic                                                    | field       |
| --------------- | ----------------------------------------------------------- | ----------- |
| `[id]`          | task id, shape `T-YYYYMMDD-NN`                              | `task_id`   |
| `[depends_on]`  | **hard** dependency — upstream must be terminal and `pass`  | `depends_on` |
| `[needs_api]`   | **soft** dependency — artefact only has to exist            | `needs_api` |
| `[acceptance]`  | acceptance artefact (+ optional criteria)                   | `acceptance` |

Marker detection rules:

* The marker may appear **anywhere** in the task text.
* The half-width bracketed form above is canonical.  The full-width variants
  (`【id】`, `【验收】`) and the colon forms (`id:`, `验收:`) stay accepted;
  `acceptance` is an ASCII alias of `验收` and exists in the bracketed form only,
  so English prose such as `acceptance criteria: ...` can never be read as a marker.
* The colon is optional and may be full width (`：`) or half width (`:`).
* Marker names are case insensitive (`ID`, `Depends_On`, `NEEDS_API`).
* Whitespace around the marker may be a full-width space.

Accepted spellings of the same marker:

```
【id】 T-20261109-20      [id] T-20261109-20
【id：T-20261109-20】     id: T-20261109-20
```

### 1.2 Task ids

* Shape: `T-` + 8 digits + `-` + 2 or more digits.
* The 8 digits must be a **real calendar date** in 2000–2099.  `T-20260230-01`
  is not a task id.
* Task ids are extracted by *search*, so an annotation in brackets or
  parentheses is ignored: `【depends_on】T-20261109-01（等 A 完成）` yields the
  dependency `T-20261109-01`.

### 1.3 Path tokens

`needs_api` and `验收` paths must match

```
^[A-Za-z0-9_\-./~]{1,200}(?:#[A-Za-z0-9_\-./~]{1,200})?$
```

* Absolute (`/var/lib/x.json`), relative (`reports/x.md`), home (`~/x.md`)
  and prefixed (`T-20261109-01#/var/lib/x.json`) forms are legal.
* A token that contains spaces, CJK characters or punctuation is **illegal**.
  The path is not "cleaned up" — it is rejected, because a silently repaired
  path would point at a different artefact than the one that was asked for.

### 1.4 Unparsed semantics (fail closed)

| situation                                   | flag                   | effect                          |
| ------------------------------------------- | ---------------------- | ------------------------------- |
| marker absent                                | flag is `False`        | "there is none", not a failure  |
| marker present, zero legal tokens extracted  | flag is `True`         | see below                       |

* `depends_on_unparsed = True` → **the dispatch is refused** with reason
  `dependency_unparsed`.  A dependency that cannot be read is never treated
  as "no dependency".
* `needs_api_unparsed = True` → does not block, but the flag is written into
  the receipt and into the ledger event.
* `acceptance_unparsed = True` → does not block, same treatment.
* `id` marker present but no legal id → `task_id` stays `None` and the gate
  blocks with `task_id_missing`.

### 1.5 Self reference and cycles

A card that lists its own task id in `depends_on` is refused with
`dependency_cycle`.  The same holds for indirect cycles
(`A → B → A`), detected by a DFS over the dependency edges recorded in the
ledger.

---

## 2. Ledger event model

Storage: `${FOREMAN_HOME}/ledger.jsonl`, one JSON object per line, **append
only** — history is never rewritten or deleted.

Common fields of every event:

| field     | type            | meaning                                  |
| --------- | --------------- | ---------------------------------------- |
| `ts`      | string          | ISO-8601 timestamp with timezone         |
| `event`   | string          | event type (see below)                   |
| `task_id` | string \| null  | task the event belongs to                |
| `actor`   | string          | who caused the event (default `foreman`) |
| `detail`  | object          | the event specific fields                |

The event specific fields are mirrored at the top level of the record and
are also collected inside `detail`, so both access styles work.

| event      | written when                    | key fields                                                        |
| ---------- | ------------------------------- | ----------------------------------------------------------------- |
| `dispatch` | after the gate allowed it, before execution | `depends_on`, `needs_api`, `acceptance`, `needs_api_pending`, `agent` |
| `running`  | the execution body started      | `agent`                                                            |
| `blocked`  | the gate refused the task       | `reason`, `missing`, `notes`                                       |
| `done`     | the execution body finished     | `verdict` (`pass`/`fail`), `acceptance_ok` (bool/null), `exit_code` |
| `cancelled`| cancelled by a human            | `reason`                                                           |
| `replan`   | re-planned by a human           | `reason`, `new_task_id`                                            |
| `heartbeat`| the worker is alive and renews its lease | `lease_expires_at`, `lease_s`                            |
| `stale`    | the lease expired, the task is reclaimable | `reason`, `lease_expires_at`, `previous_status`     |
| `checkpoint`| one step of the task is finished | `step`, `artifact` (optional)                                    |
| `retry`    | one attempt failed and will be repeated | `attempt`, `delay_s`, `exit_code`, `max_attempts`     |
| `attempt`  | a dispatch allocated an attempt      | `attempt_id`, `epoch`, `previous_attempt_id`, `attempt_dir` |

**Ordering iron rule** — when the gate blocks, only a `blocked` event is
written.  There is never a `dispatch` event for a task that did not run.

### 2.1 State folding

Events are folded per task id, in file order:

* terminal states: `done` (with a verdict), `cancelled`
* non terminal states: `dispatched`, `running`, `blocked`, `stale` (lease expired)
* the verdict of a task is the verdict of its **last** `done` event
* a task without a `done` event has never passed

A dependency is satisfied when the upstream task is terminal, its verdict is
`pass` and its `acceptance_ok` is not `False`.

### 2.2 Idempotency

Before dispatching, the plane looks at the **current state** of the task,
not at its history:

| state                          | re-dispatch | reason          |
| ------------------------------ | ----------- | --------------- |
| `blocked`                      | allowed     | —               |
| `stale` (lease expired)        | allowed     | —               |
| `running` / `dispatched`, lease alive | refused    | `duplicate_task` |
| `running` / `dispatched`, lease expired | allowed | —            |
| `done` / `cancelled`           | refused     | `duplicate_task` |

A refused dispatch writes **no** `dispatch` event.
`--force` (CLI) / `force: true` (MCP) overrides the check; a replan
allocates a fresh task id instead.

A `blocked` event does **not** trigger the duplicate check: the gate
refused the task, so it never entered the plane.  A blocked task stays
re-dispatchable and becomes dispatchable again as soon as its dependency
is satisfied.

### 2.3 Lease, checkpoints, recovery (v0.3.0)

A successful dispatch takes a **lease**: the `dispatch` and `running` events
carry `lease_expires_at` (UTC ISO-8601) and `lease_s` (the length, 900
seconds by default — `FOREMAN_LEASE` or `lease_s` in `agents.json` override
it).  A `heartbeat` event moves the deadline forward without changing the
status of the task.

`checkpoint` events name the steps a task already finished.  When the task
is dispatched again, the collected step names are handed to the execution
body through the optional `{completed_steps}` placeholder of the command
template — the JSON array `["a","b"]`, or `[]` when there is no checkpoint.
A template without the placeholder behaves exactly as before.

`reclaim` writes a `stale` event for every task whose lease expired while
it was still `running`.  A `stale` task may be dispatched again; the
receipt then carries `completed_steps`, `skipped_steps` and `resumed`.

`retry` events record one failed attempt of a retryable exit code.  A
retry never writes a second `dispatch` event — the task stays idempotent
and the dependency graph counts it once.

Readers that do not know an event kind must ignore it: the fold simply has
no branch for it.

### 2.4 Attempts and the acceptance (v0.3.0)

Every dispatch allocates an **attempt**: `attempt_id` (`{task_id}-aNN`, the
second attempt of the same task gets the next number), `epoch` (starting at
1) and an artefact directory `<workdir>/attempts/<attempt_id>/`.  The
`attempt` event carries all of it plus the `previous_attempt_id`, so the
ledger shows why a task ran twice.

The semantics are **at-least-once with an idempotency key
`(task_id, attempt_id)` — exactly-once is not promised**.  The plane runs
an attempt at least once and never undoes side effects; a duplicate side
effect across two attempts is the execution body's job to make idempotent.

The acceptance reads the artefact of **this** attempt: a relative path is
looked up inside the attempt directory first and only then in the base
directory, and the receipt records the path, the sha256 and the checks that
were really read.  A receipt that arrives after the task was cancelled —
or after a newer attempt took over — is written to the ledger but is
**not** the success of the current attempt: the fold counts it as a
`late_receipt` instead.

---

## 3. Gate

```python
@dataclass
class GateDecision:
    allowed: bool
    reason: str | None            # blocking reason code
    missing: list[str]            # unsatisfied dependency ids
    needs_api_pending: bool
    notes: list[str]              # human readable notes
```

The first matching rule wins:

| # | condition                                            | reason                 |
| - | ---------------------------------------------------- | ---------------------- |
| 1 | no legal task id                                     | `task_id_missing`      |
| 2 | `depends_on` marker present but unparsable           | `dependency_unparsed`  |
| 3 | self reference or transitive cycle                   | `dependency_cycle`     |
| 4 | dependency unknown in the ledger                     | `dependency_unknown`   |
| 5 | dependency not in a terminal state                   | `dependency_pending`   |
| 6 | dependency terminal but `verdict != pass`            | `dependency_failed`    |
| 7 | dependency passed but `acceptance_ok is False`       | `dependency_unaccepted`|
| 8 | all hard dependencies satisfied                      | allowed                |

Soft dependencies (`needs_api`) are evaluated after the hard ones.  A
missing artefact sets `needs_api_pending = True` and never blocks.

---

## 4. Acceptance criteria syntax

```
path
path::key=value
path::key=value::key=value
```

| criterion     | meaning                                  |
| ------------- | ---------------------------------------- |
| `min_bytes=N` | file size >= N bytes                     |
| `sha256=hex`  | sha256 of the file content equals `hex`  |
| `contains=sub`| file content contains the substring      |
| `exit_code=N` | the execution body exited with code `N`  |

* Criteria are ANDed.
* Relative paths are resolved against the dispatch working directory
  (`--workdir`), defaulting to the current directory.
* A missing artefact → `ok = False`, reason `artifact_missing`.
* An unknown criterion → `ok = False`, reason `acceptance_unknown_criterion`
  (fail closed).
* No acceptance string at all → `ok = True`.

---

## 5. MCP tools

The server speaks JSON-RPC 2.0 over stdio.  **One JSON object per line**
(NDJSON framing); stdout carries protocol frames only, logs go to stderr.

Supported methods:

| method                   | result                                             |
| ------------------------ | -------------------------------------------------- |
| `initialize`             | protocol version, capabilities, server info        |
| `notifications/initialized` | no answer (notification)                         |
| `tools/list`             | the three tools below                              |
| `tools/call`             | `{"content": [{"type": "text", "text": "<json>"}], "isError": bool}` |
| `resources/list`         | `{"resources": []}`                                 |
| `prompts/list`           | `{"prompts": []}`                                   |
| anything else            | JSON-RPC error `-32601`                             |

### 5.1 `foreman_dispatch`

```jsonc
{
  "name": "foreman_dispatch",
  "arguments": {
    "task": "[id] T-20261109-20\n[depends_on] T-20261109-01\n...",
    "agent": "dsh",          // optional, defaults to FOREMAN_AGENT or "generic"
    "workdir": "/srv/work",  // optional
    "force": false           // optional, overrides the duplicate check
  }
}
```

Flow: parse the contract → duplicate check → gate → (blocked: write
`blocked`, answer, **do not execute**) → (allowed: write `dispatch` →
execute → write `running`/`done` → verify the artefact → answer).

Receipt on success:

```json
{
  "ok": true,
  "blocked": false,
  "dispatched": true,
  "task_id": "T-20261109-20",
  "verdict": "pass",
  "acceptance_ok": true,
  "needs_api_pending": false,
  "exit_code": 0,
  "duration_s": 12.4
}
```

Receipt on a blocked dispatch:

```json
{
  "ok": false,
  "blocked": true,
  "dispatched": false,
  "reason": "dependency_unknown",
  "missing": ["T-20261109-99"],
  "needs_api_pending": false
}
```

### 5.2 `foreman_status`

```jsonc
{"name": "foreman_status", "arguments": {"task_id": "T-20261109-20"}}
```

Answers with the folded state of one task, or with the summary of all tasks
when `task_id` is omitted.

### 5.3 `foreman_cancel`

```jsonc
{"name": "foreman_cancel", "arguments": {"task_id": "T-20261109-20", "reason": "obsolete"}}
```

An unknown task id answers with `isError: true` and
`{"ok": false, "error": "unknown_task"}`; no event is appended.

---

## 6. Execution bodies

| name      | command            |
| --------- | ------------------ |
| `claude`  | `claude -p <task>` |
| `codex`   | `codex exec <task>`|
| `dsh`     | `dsh headless <task>` |
| `generic` | the `generic.command` template of `agents.json` |

Templates accept the placeholders `{task}` (mandatory), `{workdir}` (optional)
and `{completed_steps}` (optional, v0.3.0 — the JSON array of the steps the
task already checkpointed).  Custom agents are declared in
`${FOREMAN_HOME}/agents.json`:

```json
{
  "agents": {
    "mybot": {
      "command": "mybot run --prompt {task} --done {completed_steps}",
      "timeout_s": 600,
      "retry": {"max_attempts": 3, "base_delay_s": 5, "retry_on_exit_codes": [124]}
    }
  }
}
```

The `retry` block is optional and **off by default**: without it the plane
runs the body exactly once, as in 0.2.x.  `max_attempts` counts the total
number of attempts (the first run included), the delay before the n-th retry
is `base_delay_s * 2 ** (n - 1)`, and every retry writes one `retry` event.

* Unknown agent or missing binary → exit code `127`, stderr
  `agent not found: <name>`; nothing is raised.
* Timeout (default 900 s, `FOREMAN_TIMEOUT` overrides) → exit code `124`,
  `timed_out = true`.
