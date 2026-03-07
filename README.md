# claw-plaid-ledger

Local-first financial management app that ingests Plaid data into SQLite and
prepares deterministic outputs for OpenClaw.

## Tech stack

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment, dependency, and task flow
- [Typer](https://typer.tiangolo.com/) for CLI UX
- `sqlite3` as the local source-of-truth datastore
- [pytest](https://docs.pytest.org/) for tests
- [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- [mypy](https://mypy.readthedocs.io/) for strict type checking

## Quick start

```bash
uv sync
uv run ledger --help
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy
```

## Quality defaults

- Maximum line length: **79 characters** for Python source
- Ruff linting is configured to be strict (`select = ["ALL"]`) with minimal,
  documented exceptions
- Mypy runs in strict mode for source code
- Tests use pytest and should accompany behavior changes
- Markdown files are documentation and are not part of lint/type checks

See `ARCHITECTURE.md` for structure and quality standards.
