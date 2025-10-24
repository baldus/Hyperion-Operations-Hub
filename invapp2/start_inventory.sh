#!/usr/bin/env bash
set -euo pipefail

# Resolve repository root based on script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_DIR="$PROJECT_ROOT/.venv"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"
APP_MODULE="app:app"

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

pip install --upgrade pip setuptools wheel >/dev/null

if [ -f "$REQUIREMENTS_FILE" ]; then
  echo "[setup] Installing Python dependencies"
  pip install -r "$REQUIREMENTS_FILE"
else
  echo "[warning] Requirements file not found at $REQUIREMENTS_FILE"
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${GUNICORN_WORKERS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-600}"

if [ -z "${GUNICORN_TIMEOUT:-}" ]; then
  echo "[info] GUNICORN_TIMEOUT not provided; defaulting to ${TIMEOUT}s for large uploads"
else
  echo "[info] Using GUNICORN_TIMEOUT=${TIMEOUT}s"
fi

cd "$PROJECT_ROOT"

echo "[run] Starting Hyperion Operations Console Host via Gunicorn"
exec gunicorn --bind "$HOST:$PORT" --workers "$WORKERS" --timeout "$TIMEOUT" "$APP_MODULE"
