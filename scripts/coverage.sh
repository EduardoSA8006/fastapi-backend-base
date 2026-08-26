#!/usr/bin/env bash
# Cobertura COMBINADA (unit + integração), gate 100%.
# unit e integração rodam código de app in-process → coverage.py mede ambos.
# e2e NÃO entra (app roda em container separado, fora do processo de teste).
set -euo pipefail
export ENVIRONMENT="${ENVIRONMENT:-development}"

uv run coverage erase
# -o addopts="" limpa o addopts do pytest (marker/fail-under/report) para
# controlar cada run explicitamente.
uv run pytest -o addopts="" --cov=app --cov-report= -m "not integration and not e2e"
uv run pytest -o addopts="" --cov=app --cov-append --cov-report= -m integration
uv run coverage report --show-missing --fail-under=100
