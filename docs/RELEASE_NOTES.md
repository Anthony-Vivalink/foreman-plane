## foreman-plane v0.1.1

A task governance plane that runs **outside** your agent harness: dispatch, two-level dependency gates, objective acceptance, an append-only audit ledger. Zero third-party dependencies, standard library only, Python ≥ 3.10.

### What changed since v0.1.0

**Task card contract**

- The canonical markers are now half-width brackets: `[id]`, `[depends_on]`, `[needs_api]`, `[acceptance]`.
- `acceptance` is an ASCII alias of `验收`, recognised in the bracketed form only — English prose such as `acceptance criteria: ...` can therefore never be mistaken for a marker.
- The full-width forms (`【id】`, `【验收】`) and the colon forms (`id:`, `验收:`) stay accepted, so cards written for 0.1.0 keep working untouched.

**Docs**

- `README.md` (English) and `README_ZH.md` (Chinese), each in one language, in the DSH plugin house style: language switcher, badges, a "why" table, install paths for DSH / MCP clients, the contract table, the gates and the CLI.
- The diagrams are now rendered images — architecture and task-card pipeline, in English and Chinese — generated deterministically by `scripts/render_diagrams.py` (Graphviz). The ASCII art stays as the text fallback.

### Install

```bash
pip install foreman-plane
```

### Upgrade notes

No breaking changes. Existing 0.1.0 task cards keep parsing; only the canonical spelling of the markers changed, and both spellings are accepted.

Assets below are the wheel and the sdist. The sdist ships the docs, the examples and the diagram sources, so the figures can be regenerated with `python3 scripts/render_diagrams.py`.
