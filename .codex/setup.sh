#!/usr/bin/env bash
set -euo pipefail

# Install project + dev dependencies from the committed lockfile.
uv sync --locked --dev
