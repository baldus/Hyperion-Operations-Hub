#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/.venv"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"

if [ ! -d "$VENV_PATH" ]; then
    echo "Creating local virtual environment at $VENV_PATH"
    python3 -m venv "$VENV_PATH"
fi

# shellcheck disable=SC1090
source "$VENV_PATH/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$REQUIREMENTS_FILE"

export FLASK_APP=app.py

echo "Starting Hyperion Operations Console Host..."
exec python "$PROJECT_ROOT/app.py"
