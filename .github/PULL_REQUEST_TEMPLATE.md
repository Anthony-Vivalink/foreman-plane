## What this changes

<!-- one or two sentences; the "why" matters more than the "what" -->

## Why

<!-- the problem this solves, or the failure mode it prevents -->

## Checklist

- [ ] The suite passes: `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- [ ] New behaviour comes with tests in `tests/`
- [ ] No new runtime dependency (`pyproject.toml` keeps `dependencies = []`)
- [ ] No absolute paths in `src/` — every path comes from `FOREMAN_HOME`
- [ ] Failures fail closed: an unknown key or unparseable input is an explicit error
- [ ] Docs updated if a CLI flag, an exit code or a card marker changed

## Related issue

<!-- e.g. fixes #42 -->
