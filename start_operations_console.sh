#!/usr/bin/env bash
set -euo pipefail

APT_UPDATED=0

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return
    fi

    if command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "❌ This script needs to run '$*' with elevated permissions but sudo is not available." >&2
        echo "   Re-run the script as root or install sudo." >&2
        exit 1
    fi
}

apt_update_if_needed() {
    if [ "$APT_UPDATED" -eq 0 ]; then
        if command -v apt-get >/dev/null 2>&1; then
            echo "🔹 Refreshing apt package index"
            run_as_root apt-get update >/dev/null
            APT_UPDATED=1
        fi
    fi
}

ensure_apt_packages() {
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "⚠️ apt-get is not available; unable to auto-install packages: $*" >&2
        return
    fi

    local missing_packages=()
    for pkg in "$@"; do
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            missing_packages+=("$pkg")
        fi
    done

    if [ "${#missing_packages[@]}" -gt 0 ]; then
        apt_update_if_needed
        echo "🔹 Installing missing system packages: ${missing_packages[*]}"
        run_as_root apt-get install -y "${missing_packages[@]}"
    fi
}

ensure_python_tooling() {
    local packages=()

    if ! command -v python3 >/dev/null 2>&1; then
        packages+=(python3)
    fi

    if command -v python3 >/dev/null 2>&1; then
        if ! python3 -m ensurepip --version >/dev/null 2>&1; then
            packages+=(python3-venv)
        fi
        if ! python3 -m pip --version >/dev/null 2>&1; then
            packages+=(python3-pip)
        fi
    fi

    if [ "${#packages[@]}" -gt 0 ]; then
        ensure_apt_packages "${packages[@]}"
    fi
}

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

ensure_python_tooling

create_virtualenv() {
    local target_dir="$1"

    python3 -m venv "$target_dir"

    if [ ! -f "$target_dir/bin/activate" ]; then
        echo "❌ Virtual environment at $target_dir was not created correctly." >&2
        return 1
    fi
}

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "🔹 Creating virtual environment at $VENV_DIR"
    rm -rf "$VENV_DIR"
    if ! create_virtualenv "$VENV_DIR"; then
        echo "⚠️ python3 -m venv failed; attempting to install python3-venv and retry"
        ensure_apt_packages python3 python3-venv python3-pip
        rm -rf "$VENV_DIR"
        create_virtualenv "$VENV_DIR"
    fi
fi

echo "🔹 Activating virtual environment"
source "$VENV_DIR/bin/activate"

echo "🔹 Ensuring tooling is up to date"
if ! python -m pip install --upgrade pip setuptools wheel; then
    echo "⚠️ Unable to upgrade pip tooling automatically; continuing with existing versions" >&2
fi

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
TIMEOUT="${GUNICORN_TIMEOUT:-600}"

if [ -z "${GUNICORN_TIMEOUT:-}" ]; then
    echo "⚠️ GUNICORN_TIMEOUT not provided; defaulting to ${TIMEOUT}s to accommodate large data backups"
else
    echo "✅ Using GUNICORN_TIMEOUT=${TIMEOUT}s"
fi

echo "🔹 Running startup health check"
HEALTHCHECK_FLAGS=()
if [ "${HEALTHCHECK_FATAL:-0}" -eq 1 ]; then
    HEALTHCHECK_FLAGS+=("--fatal")
fi
if [ "${HEALTHCHECK_DRY_RUN:-0}" -eq 1 ]; then
    HEALTHCHECK_FLAGS+=("--dry-run")
fi

if ! python -m invapp.healthcheck "${HEALTHCHECK_FLAGS[@]}"; then
    echo "❌ Health check failed; aborting startup" >&2
    exit 1
fi

echo "🔹 Starting Hyperion Operations Console via Gunicorn ($HOST:$PORT)"
exec gunicorn --bind "$HOST:$PORT" --workers "$WORKERS" --timeout "$TIMEOUT" "$APP_MODULE"
