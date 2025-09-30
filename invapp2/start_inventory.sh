#!/usr/bin/env bash
set -euo pipefail

# Resolve repository root based on script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_DIR="$PROJECT_ROOT/.venv"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"
APP_ENTRY="$PROJECT_ROOT/app.py"

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

echo "[run] Starting Hyperion Operations Console Host"
exec python "$APP_ENTRY"
