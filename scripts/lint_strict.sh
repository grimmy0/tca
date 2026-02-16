#!/usr/bin/env bash
set -euo pipefail

echo "Running Ruff (strict ALL rules)..."
uv run ruff check . --select ALL --ignore D203,D213

echo "Running Ruff format check..."
uv run ruff format --check .

echo "Running MyPy (strict)..."
uv run mypy . --strict

echo "Running Pyright (strict, warnings as errors)..."
uv run pyright --warnings

echo "Running BasedPyright (strict)..."
uv run basedpyright --warnings --project basedpyrightconfig.json
