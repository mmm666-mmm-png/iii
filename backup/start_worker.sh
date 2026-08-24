#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_DIR="$ROOT/python_worker"

VENV_PYTHON="$ROOT/.venv_nav/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  PARENT_ROOT="$(dirname "$ROOT")"
  VENV_PYTHON="$PARENT_ROOT/.venv_nav/bin/python"
fi
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Cannot find Python venv. Create .venv_nav and install python_worker/requirements.txt first." >&2
  exit 1
fi

export WORKER_HOST="${WORKER_HOST:-127.0.0.1}"
export WORKER_PORT="${WORKER_PORT:-18082}"
export ENABLE_SYNC_RECORDER="${ENABLE_SYNC_RECORDER:-0}"
export VISION_INPUT_ROTATION="${VISION_INPUT_ROTATION:-cw90}"

cd "$WORKER_DIR"
exec "$VENV_PYTHON" app_main.py
