#!/usr/bin/env bash
# lint.sh — the checks this repo runs before a commit (no third-party linters
# are pinned, so we rely on compileall, import, and the test suite).
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-/home/zerops/cloud-bot-venv/bin/python}"

echo "compileall"
"$PY" -m compileall -q cloudbot
echo "import"
"$PY" -c "import cloudbot; print('import ok')"
echo "tests"
"$PY" -m pytest -q
