#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT/esp32-glass-front"

export VITE_DEV_PROXY_TARGET="${VITE_DEV_PROXY_TARGET:-http://127.0.0.1:8888}"

HOST="${FRONTEND_HOST:-0.0.0.0}"
PORT="${FRONTEND_PORT:-5174}"

cd "$FRONTEND_DIR"
exec npm run dev -- --host "$HOST" --port "$PORT"
