# Contributing

## Local quality gate (required)

Before every commit, all checks must pass:

```bash
uv run ruff format . --check
uv run ruff check .
uv run mypy
uv run pytest
```

## Enable local enforcement

Install repository git hooks (recommended for all contributors, mandatory for
AI agents):

```bash
bash scripts/install-hooks.sh
```

This configures `.githooks/pre-commit` and `.githooks/pre-push` to block
commits/pushes when checks fail.

## GitHub enforcement

Set branch protection on `master` to require the `CI / Ruff, mypy, and pytest`
status check before merge.
