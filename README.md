# claw-plaid-ledger

Local-first financial management support app that ingests Plaid data into SQLite and
prepares deterministic outputs for OpenClaw. Bring-your-own-Plaid-integration. You're responsible for safeguarding the data at rest and keeping OpenClaw interactions safe.

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
uv run --locked ruff format . --check
uv run --locked ruff check .
uv run --locked mypy .
```

## Local configuration

Claw Plaid Ledger expects secrets and machine-specific paths to live outside
this repository.

Recommended location on Linux:

```bash
~/.config/claw-plaid-ledger/.env
```

This keeps secrets out of git and out of the OpenClaw workspace.

Create the config directory and install the template:

```bash
mkdir -p ~/.config/claw-plaid-ledger
chmod 700 ~/.config/claw-plaid-ledger
cp .env.example ~/.config/claw-plaid-ledger/.env
chmod 600 ~/.config/claw-plaid-ledger/.env
```

Then edit:

```bash
~/.config/claw-plaid-ledger/.env
```

with your Plaid credentials and local paths.

The app loads configuration from both places:

1. `~/.config/claw-plaid-ledger/.env` (if it exists)
2. Runtime environment variables

Runtime environment variables override values from the user env file.

## Security model

Keep these boundaries:

- **Repository**: source code only
- **User config**: secrets and machine-specific settings
- **Database**: local ledger state in SQLite
- **OpenClaw workspace**: agent-readable exports only

Never store Plaid secrets:

- in the git repository
- in committed files
- in markdown files
- in the OpenClaw workspace

## Configuration reference

The template file `.env.example` includes all supported keys:

```dotenv
# Plaid credentials
PLAID_CLIENT_ID=
PLAID_SECRET=
PLAID_ENV=sandbox
PLAID_ACCESS_TOKEN=

# Local application paths
CLAW_PLAID_LEDGER_DB_PATH=
CLAW_PLAID_LEDGER_WORKSPACE_PATH=
CLAW_PLAID_LEDGER_ITEM_ID=
```

Notes:

- `PLAID_ENV` should usually stay `sandbox` during local development.
- `CLAW_PLAID_LEDGER_DB_PATH` should point to a local SQLite file.
- `CLAW_PLAID_LEDGER_WORKSPACE_PATH` should be set only when OpenClaw
  exports are being used.
- `CLAW_PLAID_LEDGER_ITEM_ID` identifies the Plaid item (institution link)
  used as the sync-state key. Defaults to `"default-item"`. Set this when
  connecting more than one institution so each item's cursor is stored
  separately.

## Example

After creating your config and syncing dependencies:

```bash
uv sync --dev --locked
uv run ledger init-db
```

## Quality defaults

- Maximum line length: **79 characters** for Python source
- Ruff linting is configured to be strict (`select = ["ALL"]`) with minimal,
  documented exceptions
- Mypy runs in strict mode for source code
- Tests use pytest and should accompany behavior changes
- Markdown files are documentation and are not part of lint/type checks

See `ARCHITECTURE.md` for structure and quality standards.

## Continuous integration

GitHub Actions runs `ruff`, `mypy`, and `pytest` on every pull request
and on every push to `master` (including merged PRs).

## AI contributor policy

AI coding agents must run the full quality gate before committing.
See `AGENTS.md` and `CONTRIBUTING.md` for mandatory rules and
hook installation.
