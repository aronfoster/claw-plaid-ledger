# AGENTS Instructions

These rules apply to every AI coding agent working in this repository
(Codex, Claude Code, and similar tools).

## Non-negotiable quality gate before any commit

Do **not** commit if any of the following commands fails:

```bash
uv run ruff format . --check
uv run ruff check .
uv run mypy
uv run pytest
```

If your environment cannot run these commands (for example, temporary
network/package mirror outage), stop and report the limitation. Do not claim
that checks passed.

## Required behavior for AI agents

1. Run the full quality gate before creating a commit.
2. Include exact commands and results in your final summary.
3. If a check fails, fix the code first and rerun checks.
4. Never fabricate successful test/lint output.

## Git hooks

Install and use the repository hooks:

```bash
bash scripts/install-hooks.sh
```

The hooks run quality checks and block commits/pushes when checks fail.

## Check bypass policy (strict)

Bypassing checks (lint/type/test/security rules) is prohibited unless it is
**absolutely necessary** and there is no practical way to structure the code to
pass the check directly.

If a bypass is unavoidable:

1. Keep the bypass as narrow as possible (single line/symbol/scope).
2. Add a detailed in-code comment explaining exactly why the bypass is
   necessary and why the code cannot reasonably be structured to avoid it.
3. Include that rationale in the PR summary and mention follow-up work, if any.

Broad or silent bypasses are not allowed.
