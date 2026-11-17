#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/zk-uv}"
uv sync --frozen --python 3.12
uv export --frozen --no-dev --group gpu --no-emit-project -o requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
uv pip install --python .venv/bin/python pip==25.1.1
mkdir -p wheelhouse
.venv/bin/python -m pip download --only-binary=:all: --require-hashes -r requirements.lock -d wheelhouse
