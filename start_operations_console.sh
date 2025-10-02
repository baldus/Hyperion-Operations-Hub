#!/bin/bash
set -euo pipefail
set -x   # <--- Debug: show every command before it runs

APP_DIR="/home/josh/invapp2"
VENV_DIR="$APP_DIR/.venv"

# Minimum versions
MIN_PIP_VERSION="23.1"
declare -A MIN_PKG_VERSIONS=(
    [Flask]="3.0"
    [SQLAlchemy]="2.0"
    [psycopg2-binary]="2.9"
    [gunicorn]="21.0"
)

echo "🔹 Step 1: Changing to app directory..."
if ! cd "$APP_DIR"; then
    echo "❌ Failed to cd into $APP_DIR"
    read -p "Press Enter to exit..."
    exit 1
fi

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
    echo "🔹 Step 2: Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "🔹 Step 3: Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Function to compare versions
version_ge() {
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

echo "🔹 Step 4: Checking pip version..."
CURRENT_PIP_VERSION=$(pip --version | awk '{print $2}')
if version_ge "$CURRENT_PIP_VERSION" "$MIN_PIP_VERSION"; then
    echo "✅ pip $CURRENT_PIP_VERSION is up-to-date (>= $MIN_PIP_VERSION)"
else
    echo "⚠️ Upgrading pip (current: $CURRENT_PIP_VERSION, required: $MIN_PIP_VERSION)..."
    pip install -U pip --break-system-packages
fi

echo "🔹 Step 5: Checking dependencies..."
for pkg in "${!MIN_PKG_VERSIONS[@]}"; do
    MIN_VER=${MIN_PKG_VERSIONS[$pkg]}
    if pip show "$pkg" >/dev/null 2>&1; then
        CURRENT_VER=$(pip show "$pkg" | awk '/Version:/ {print $2}')
        if version_ge "$CURRENT_VER" "$MIN_VER"; then
            echo "✅ $pkg $CURRENT_VER is OK (>= $MIN_VER)"
        else
            echo "⚠️ Upgrading $pkg (current: $CURRENT_VER, required: $MIN_VER)..."
            pip install --break-system-packages "$pkg>=$MIN_VER"
        fi
    else
        echo "⬇️ Installing missing package: $pkg (>= $MIN_VER)"
        pip install --break-system-packages "$pkg>=$MIN_VER"
    fi
done

# Export DB URL if not already set
if [ -z "${DB_URL:-}" ]; then
    export DB_URL="postgresql+psycopg2://inv:change_me@localhost/invdb"
    echo "⚠️ DB_URL not found, set to default: $DB_URL"
else
    echo "✅ Using existing DB_URL: $DB_URL"
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${GUNICORN_WORKERS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"

echo "🔹 Step 6: Starting Hyperion Operations Console Host via Gunicorn..."
gunicorn --bind "$HOST:$PORT" --workers "$WORKERS" --timeout "$TIMEOUT" app:app || {
    echo "❌ Gunicorn failed to start"
    read -p "Press Enter to close..."
}
