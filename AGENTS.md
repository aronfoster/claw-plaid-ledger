# AGENTS Instructions

These rules apply to every AI coding agent working in this repository
(Claude Code, Codex, and similar tools).

---

## Environment and setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency and
virtualenv management. A checked-in `uv.lock` file pins every dependency.

**No install step is needed** — `uv run --locked <cmd>` creates and populates
the virtualenv on first use. Do not run `pip install`, `uv sync`, or anything
else manually before running the quality-gate commands below.

If `uv` is not on `PATH`, stop and report the limitation rather than
attempting workarounds.

---

## Quality gate — required before every commit

All four commands must exit 0. Run them in order:

```bash
uv run --locked ruff format . --check   # formatting
uv run --locked ruff check .            # linting
uv run --locked mypy .                  # type checking
uv run --locked pytest -v               # tests
```

- If any command fails, fix the code and rerun **all four** from the top.
- Do not commit if any command fails.
- Do not fabricate successful output.
- If the environment genuinely cannot run these commands (e.g. no network on
  first `uv run` invocation), stop and report the limitation.

---

## Git hooks

The repository ships pre-commit and pre-push hooks that mirror the quality
gate. Install them once per checkout:

```bash
bash scripts/install-hooks.sh
```

The hooks will block commits and pushes that fail the gate.

---

## `# noqa` and other check bypasses — strict policy

Bypassing lint, type, test, or security rules is **prohibited** unless there
is no practical way to structure the code correctly.

### What counts as a bypass

- `# noqa` (ruff/flake8)
- `# type: ignore` (mypy)
- `pragma: no cover` (coverage)
- `per-file-ignores` additions in `pyproject.toml`
- Any other mechanism that silences a tool without fixing the underlying issue

### Before reaching for a bypass, always try the correct fix first

Most common lint rules have a proper structural fix:

| Rule | Wrong | Right |
|------|-------|-------|
| FBT001/FBT002 — boolean positional/default arg | `def f(flag: bool = False) # noqa` | Use `count=True` (int) for CLI flags; use an enum or separate functions elsewhere |
| ANN* — missing annotation | `# noqa: ANN201` | Add the annotation |
| SLF001 — private member access | `# noqa: SLF001` | Expose a public method or use the public API |
| ERA001 — commented-out code | `# noqa: ERA001` | Delete the dead code |

### When a bypass is genuinely unavoidable

Unavoidable means: the rule fires on code that is **correct by design** and
**cannot be restructured** to satisfy the rule without making the code worse
(e.g. an untyped third-party boundary, a required stdlib pattern the rule
doesn't understand).

If that threshold is met:

1. Keep the bypass as narrow as possible — single line, single code, never
   a file-wide or project-wide suppression.
2. Write an inline comment **immediately above or on the same line** that
   explains:
   - exactly why the rule fires
   - exactly why the code cannot be restructured to avoid it
   - what would need to change in the future to remove the bypass
3. **Explicitly list every bypass in your task completion report**, including
   the rule code, the location, and a summary of the rationale. A bypass that
   is not mentioned in the report is not acceptable.
4. **Notify the user directly in your final response** when committing or
   pushing code that contains any bypass. State the rule code, file location,
   and one-sentence rationale. This notification is required even if the bypass
   was already documented in the inline comment and task report. A bypass
   committed without explicit user notification is a policy violation.

**Example of an acceptable bypass:**

```python
# ANN401: `response` is typed as `Any` because the Plaid SDK's
# TransactionsResponse does not expose typed field accessors; every
# field access goes through __getattr__ which returns Any. This will
# be removable if/when plaid-python ships a typed model layer.
response: Any = client.transactions_sync(body)  # noqa: ANN401
```

**Example of an unacceptable bypass:**

```python
def doctor(verbose: bool = False) -> None:  # noqa: FBT002
```

This is wrong because FBT002 has a correct structural fix (use `count=True`
and `int` instead of `bool`), so the noqa comment is not justified.

---

## No personal or private information in the repository

This is an open-source project. Do not include real personal information anywhere
in code, documentation, examples, or tests.

**Prohibited** — never use real:
- Personal names (use `alice`, `bob`, `carol`, etc.)
- Financial institution names (use `bank-alice`, `card-bob`, `bank-shared`, etc.)
- Account numbers, access tokens, or any real credentials

**Examples**

| Wrong | Right |
|-------|-------|
| `id = "chase-alice"` | `id = "bank-alice"` |
| `PLAID_ACCESS_TOKEN_CARD_ARON` | `PLAID_ACCESS_TOKEN_CARD_BOB` |

If you are asked to use real names or institution names, substitute generic
placeholders instead and note the substitution.

---

## Sprint tracking

When you complete a task from `SPRINT.md`, mark it done in that file before
committing.  Add `✅ DONE` to the end of the task's heading line, for example:

```
### Task 3: Plaid webhook signature verification ✅ DONE
```

This keeps the sprint board accurate for the next developer (human or agent)
picking up the next task.

---

## Next Task Queueing

After completing a task, provide the next step text to the user so he can copy-paste it. When starting a new Sprint, use **PM New Sprint**. When uncompleted tasks remain in SPRINT.md, use **Developer Step for Sprint Task**. When all tasks are complete in SPRINT.md, use **PM Sprint Closeout**. After the closeout, provide **PM New Sprint**.

### PM New Sprint
You are an expert PM. See AGENTS.md, ROADMAP.md, and BUGS.md. We're going to work together to turn [next milestone number and title from ROADMAP.md] into a series of Tasks within SPRINT.md. We will focus on requirements, not implementation. However, we will provide specifics, even code, if it will answer necessary design decisions for the developer. The goal is that our developers can grab a task and complete it in the way they want, with minimal intervention, and they'll produce a result that meets the customer's needs. This is the stage to ask questions.

### Developer Step for Sprint Task
See AGENTS.md and SPRINT.md. Complete Sprint [Sprint number from SPRINT.md header] Task [First incomplete task in SPRINT.md].

### PM Sprint Closeout
Complete Sprint [Sprint number from SPRINT.md header] from SPRINT.md. See AGENTS.md for additional guidance. Close out the Sprint, ensuring that all markdown files are up to date (including architecture, readme, roadmap, and runbook; but please search and assess all markdown files in the project) and all objectives have been met. Do not erase or overwrite SPRINT.md, just mark items completed.

---

## Required behavior summary

1. Run the full quality gate before committing.
2. Fix the root cause of failures; do not suppress them.
3. If a bypass is truly unavoidable, follow the narrow-bypass + detailed
   comment policy above.
4. Include quality-gate output in your task summary.
5. Mark completed tasks in `SPRINT.md` (see **Sprint tracking** above).
