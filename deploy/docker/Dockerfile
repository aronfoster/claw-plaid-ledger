# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Multi-stage Dockerfile for claw-plaid-ledger
#
# Stage 1 (builder) — uses the official uv image to install Python
#   dependencies into a virtual environment.
# Stage 2 (runtime) — copies only the populated venv into a slim Python
#   image.  No build tools, no dev dependencies, no secrets.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# builder: install project and production dependencies
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /build

# Copy dependency manifests first.  Docker caches this layer; the expensive
# `uv sync` only re-runs when pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./

# Install production dependencies into .venv.
# --frozen       : honour the exact versions in uv.lock
# --no-dev       : skip development-only packages (pytest, mypy, ruff …)
# --no-install-project : skip the package itself for now (installed below)
RUN uv sync --frozen --no-dev --no-install-project

# Copy the package source and install it into the same venv.
COPY src/ src/
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# runtime: minimal image that runs `ledger serve`
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Create a non-root user.  UID/GID 1000 matches the typical desktop user so
# that bind-mounted config files are readable without chmod gymnastics.
RUN groupadd --gid 1000 ledger \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false ledger

WORKDIR /app

# Copy the fully-populated virtual environment from the builder stage.
# Only the venv is copied — no source tree, no build tools.
COPY --from=builder /build/.venv /app/.venv

# Prepend the venv so `ledger` and `uvicorn` are found without activation.
ENV PATH="/app/.venv/bin:$PATH"

# All configuration is supplied at runtime via environment variables or an
# env_file.  No tokens, secrets, or site-specific paths are baked in here.

# The SQLite database lives on a named or bind-mounted volume at /data.
# Set CLAW_DB_PATH=/data/ledger.db in your .env file.
VOLUME ["/data"]

# Drop to the non-root user for all subsequent commands.
USER ledger

# Expose the default server port.  docker-compose binds this to
# 127.0.0.1:8000 only — override with the `ports` key if needed.
EXPOSE 8000

ENTRYPOINT ["ledger", "serve"]
