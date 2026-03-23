#!/usr/bin/env sh
set -eu

# Standardized environment bootstrap using uv.
uv sync
uv run pytest -q
