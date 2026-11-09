## foreman-plane v0.1.2

A task governance plane that runs **outside** your agent harness: dispatch, two-level dependency gates, objective acceptance, an append-only audit ledger. Zero third-party dependencies, standard library only, Python ≥ 3.10.

### What changed since v0.1.1

**Docs — the deployment chapter**

- Both READMEs now carry a Deployment chapter: what "deployment" means for a plain process (no daemon, no port, no scheduler — so it is three decisions, not a deployment guide), the four steps (check the install, give the plane a home, pick an execution body, hand in the first card), how to wire the plane into DSH / Claude Code / Codex, who decides what gets dispatched, the configuration reference, what the plane deliberately does not do, and a troubleshooting table that maps every exit code and gate reason to a fix.
- A new deployment diagram — three entry points, one core, one ledger — rendered deterministically in English and Chinese.

**Fixes**

- A failure before the execution body starts is now diagnosable. An unknown agent, an empty command template, a template without the `{task}` placeholder and a command that is not on `PATH` used to end in `exit_code: 127` with no run log and no stderr in the receipt. Each case now says which one it was, writes the usual log under `${FOREMAN_HOME}/runs/`, and the receipt carries the tail of stderr.
- The MCP handshake no longer reports `serverInfo.version 0.1.0`; it follows the package version.

### Install

```bash
pip install foreman-plane
```

### Upgrade notes

No breaking changes. `pip install -U foreman-plane` gets you the new docs and the two fixes. The task card contract is unchanged: the same markers, the same gates, the same ledger format.

Assets below are the wheel and the sdist. The sdist ships the docs, the examples and the diagram sources, so the figures can be regenerated with `python3 scripts/render_diagrams.py`.
