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

if [ "${SKIP_PIP_INSTALL:-0}" != "1" ]; then
  pip install --upgrade pip setuptools wheel >/dev/null

  if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "[setup] Installing Python dependencies"
    pip install -r "$REQUIREMENTS_FILE"
  else
    echo "[warning] Requirements file not found at $REQUIREMENTS_FILE"
  fi
else
  echo "[setup] Skipping dependency installation (SKIP_PIP_INSTALL=1)"
fi

echo "[run] Starting Hyperion Operations Console Host via Gunicorn"

GUNICORN_BIN="${GUNICORN_BIN:-gunicorn}"
GUNICORN_CONFIG="${GUNICORN_CONFIG:-$PROJECT_ROOT/gunicorn.conf.py}"
GUNICORN_BIND="${GUNICORN_BIND:-0.0.0.0:5000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
GUNICORN_ACCESS_LOGFILE="${GUNICORN_ACCESS_LOGFILE:--}"
GUNICORN_ERROR_LOGFILE="${GUNICORN_ERROR_LOGFILE:--}"

CMD=("$GUNICORN_BIN" "--chdir" "$PROJECT_ROOT" "--bind" "$GUNICORN_BIND" "--workers" "$GUNICORN_WORKERS" "--access-logfile" "$GUNICORN_ACCESS_LOGFILE" "--error-logfile" "$GUNICORN_ERROR_LOGFILE")

if [ -f "$GUNICORN_CONFIG" ]; then
  CMD+=("--config" "$GUNICORN_CONFIG")
fi

if [ -n "${GUNICORN_TIMEOUT:-}" ]; then
  CMD+=("--timeout" "$GUNICORN_TIMEOUT")
fi

if [ -n "${GUNICORN_EXTRA_ARGS:-}" ]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=($GUNICORN_EXTRA_ARGS)
  CMD+=("${EXTRA_ARGS[@]}")
fi

CMD+=("$APP_MODULE")

exec "${CMD[@]}"
