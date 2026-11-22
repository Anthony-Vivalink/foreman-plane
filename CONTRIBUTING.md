# Contributing to foreman-plane

Thanks for taking the time. foreman-plane is a **personal-tool-grade** project:
one person, one machine, one state directory. Contributions that keep it that
way — small, dependency-free, observable — are the easy ones to accept.

## Run the tests

The suite is `unittest` (not `pytest`) on purpose: the package ships **zero
third-party dependencies**, and the tests must not depend on whether `claude`,
`codex` or `dsh` happens to be installed on your machine.

```bash
git clone https://github.com/Anthony-Vivalink/foreman-plane.git
cd foreman-plane

# the whole suite — 78 tests, no network, no external binaries
PYTHONPATH=src python3 -m unittest discover -s tests -v

# one module only
PYTHONPATH=src python3 -m unittest discover -s tests -v -k contract
```

CI runs exactly that command on Python 3.10, 3.11 and 3.12
(`.github/workflows/ci.yml`), and a pull request is expected to be green there.

Requirements: Python **3.10+**. There is no `pip install` step for the tests.

## Drive a card by hand

```bash
export PYTHONPATH="$PWD/src"
export FOREMAN_HOME="$PWD/.foreman"

python3 -m foreman_plane.cli --help
python3 -m foreman_plane.cli dispatch --file examples/task_card.md
```

`examples/task_card.md` documents the card contract; the acceptance string
format is in [`docs/SPEC.md`](docs/SPEC.md) §5 and the wire format in
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## What a good pull request looks like

* **One concern per PR.** A refactor and a behaviour change in one diff is two
  reviews, not one.
* **Behaviour changes come with tests.** New acceptance criteria, new gate
  reasons and new CLI exit codes all need a case in `tests/`.
* **No new runtime dependency.** `pyproject.toml` keeps `dependencies = []`.
  Standard library only.
* **No absolute paths in the code.** Every path comes from `FOREMAN_HOME` /
  `$HOME/.foreman` / the working directory. This is a hard rule in
  [`docs/SPEC.md`](docs/SPEC.md) §0 and it is checked at review time.
* **Fail closed.** An unparseable dependency, an unknown acceptance key or a
  missing artefact must be an explicit error — never a silent success.
* **Say the "why" in the commit message.** What changed, and what would break
  if the change were wrong.

## Reporting an issue

Open an issue at
<https://github.com/Anthony-Vivalink/foreman-plane/issues> and include:

* what you ran, what you expected, and what happened instead;
* the output of `foreman-plane --help` and `foreman-plane config`;
* the card (or a redacted version of it) and the acceptance string;
* the relevant lines of `${FOREMAN_HOME}/ledger.jsonl` and
  `${FOREMAN_HOME}/runs/*.log`.

If you are not sure whether it is a bug, open it anyway — "the plane behaved
in a way I did not expect" is a legitimate report.

For a security problem, follow [SECURITY.md](SECURITY.md) instead of the
public issue tracker.

## License

By contributing you agree that your contribution is licensed under the MIT
license of this repository.
