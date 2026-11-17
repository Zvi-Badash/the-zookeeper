#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/zk-uv}"
uv sync --frozen --python 3.12
uv export --frozen --no-dev --group gpu --no-emit-project -o requirements.lock
uv export --frozen --no-emit-project -o requirements-dev.lock
npm --prefix frontend ci --cache npm-cache
npm --prefix frontend run build
uv pip install --python .venv/bin/python pip==25.1.1
mkdir -p wheelhouse
.venv/bin/python -m pip download --only-binary=:all: --require-hashes -r requirements.lock -r requirements-dev.lock -d wheelhouse
