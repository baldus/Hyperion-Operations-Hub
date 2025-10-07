#!/usr/bin/env bash
set -euo pipefail

# Allow operators to override these locations when deploying to a new host.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR/invapp2}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$APP_DIR/requirements.txt}"
APP_MODULE="${APP_MODULE:-app:app}"

if [ ! -d "$APP_DIR" ]; then
    echo "❌ Unable to locate application directory: $APP_DIR" >&2
    echo "   Set APP_DIR to the path that contains app.py and requirements.txt" >&2
    exit 1
fi

cd "$APP_DIR"

if [ ! -d "$VENV_DIR" ]; then
    echo "🔹 Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

echo "🔹 Activating virtual environment"
source "$VENV_DIR/bin/activate"

echo "🔹 Ensuring tooling is up to date"
python -m pip install --upgrade pip setuptools wheel >/dev/null

if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "🔹 Installing Python dependencies from $(realpath "$REQUIREMENTS_FILE")"
    python -m pip install -r "$REQUIREMENTS_FILE"
else
    echo "⚠️ Requirements file not found at $REQUIREMENTS_FILE — skipping dependency install"
fi

if [ -z "${DB_URL:-}" ]; then
    export DB_URL="postgresql+psycopg2://inv:change_me@localhost/invdb"
    echo "⚠️ DB_URL not found; defaulting to $DB_URL"
else
    echo "✅ Using DB_URL=$DB_URL"
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${GUNICORN_WORKERS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"

echo "🔹 Starting Hyperion Operations Console via Gunicorn ($HOST:$PORT)"
exec gunicorn --bind "$HOST:$PORT" --workers "$WORKERS" --timeout "$TIMEOUT" "$APP_MODULE"
