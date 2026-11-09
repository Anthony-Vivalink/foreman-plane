# Example task card

A task card is plain text.  Four markers may appear anywhere in it; the
rest of the text is the task itself and is handed to the execution body
unchanged.

```text
【id】T-20261109-20
【depends_on】T-20261109-01
【needs_api】T-20261109-01#/var/lib/foreman/api/order_book.json
【验收】reports/order_book_20261109.md::min_bytes=200::contains=结论
```

Line by line:

| marker           | meaning                                                       |
| ---------------- | ------------------------------------------------------------- |
| `【id】`          | task id, `T-YYYYMMDD-NN`; the 8 digits must be a real date    |
| `【depends_on】`  | **hard** dependency: the upstream must be done and `pass`     |
| `【needs_api】`   | **soft** dependency: an artefact that only has to exist        |
| `【验收】`         | acceptance artefact path plus optional objective criteria      |

## The task body

```text
请读取 order_book.json，产出 2026-11-09 的订单簿分析报告，
写入 reports/order_book_20261109.md，并在报告中给出「结论」小节。
```

## Notes

* Marker syntax is forgiving: `【id】`, `[id]`, `ID:` and `id:` are all
  accepted, as are full-width spaces and colons.
* A hard dependency that cannot be parsed is **never** silently dropped.
  The dispatch is refused with `dependency_unparsed` — fail closed.
* `【验收】` takes `path` or `path::key=value::key=value`.  Supported
  criteria: `min_bytes`, `sha256`, `contains`, `exit_code`.  All criteria
  are ANDed.
* Relative artefact paths are resolved against the working directory of the
  dispatch (`--workdir`), relative paths of `needs_api` likewise.
* A task id may enter the plane once.  Re-dispatching the same id is
  refused with `duplicate_task` unless `--force` is used; a replan
  allocates a fresh id instead.
