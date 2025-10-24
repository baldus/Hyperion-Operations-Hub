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

ensure_command() {
    local cmd=$1
    if ! command -v "$cmd" >/dev/null 2>&1; then
        return 1
    fi
}

bootstrap_pip() {
    if python3 -m pip --version >/dev/null 2>&1; then
        return 0
    fi

    echo "🔹 Bootstrapping pip for system Python"
    if python3 -m ensurepip --upgrade >/dev/null 2>&1; then
        return 0
    fi

    local tmp_dir
    tmp_dir=$(mktemp -d)
    local get_pip_script="$tmp_dir/get-pip.py"

    echo "   ensurepip unavailable; downloading get-pip.py"
    if ensure_command curl; then
        curl -fsSL "https://bootstrap.pypa.io/get-pip.py" -o "$get_pip_script"
    elif ensure_command wget; then
        wget -qO "$get_pip_script" "https://bootstrap.pypa.io/get-pip.py"
    else
        python3 - "$get_pip_script" <<'PY'
import sys
import urllib.request

url = "https://bootstrap.pypa.io/get-pip.py"
dest = sys.argv[1]

with urllib.request.urlopen(url) as response, open(dest, "wb") as fh:
    fh.write(response.read())
PY
    fi

    python3 "$get_pip_script" >/dev/null
    rm -rf "$tmp_dir"
}

create_virtualenv() {
    echo "🔹 Creating virtual environment at $VENV_DIR"
    if python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
        return 0
    fi

    echo "⚠️ python3 -m venv failed; attempting fallback bootstrap"
    bootstrap_pip || {
        echo "❌ Unable to bootstrap pip; ensure Python is installed correctly" >&2
        exit 1
    }

    python3 -m pip install --upgrade pip setuptools wheel virtualenv >/dev/null
    python3 -m virtualenv "$VENV_DIR"
}

if [ ! -d "$VENV_DIR" ]; then
    create_virtualenv
fi

echo "🔹 Activating virtual environment"
source "$VENV_DIR/bin/activate"

echo "🔹 Ensuring tooling is up to date"
if ! python -m pip install --upgrade pip setuptools wheel >/dev/null; then
    echo "⚠️ Unable to update pip tooling; continuing with existing versions" >&2
fi

if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "🔹 Installing Python dependencies from $(realpath "$REQUIREMENTS_FILE")"
    if ! python -m pip install -r "$REQUIREMENTS_FILE"; then
        echo "❌ Failed to install dependencies from $REQUIREMENTS_FILE" >&2
        exit 1
    fi
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
