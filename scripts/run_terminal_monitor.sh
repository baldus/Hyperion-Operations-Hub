#!/usr/bin/env bash
set -euo pipefail

USE_TMUX=1
HEADLESS=0
LOG_FILE=""
REFRESH_MS=""
EXTRA_ARGS=()

while [ "${1:-}" != "" ]; do
    case "$1" in
        --no-tmux)
            USE_TMUX=0
            shift
            ;;
        --headless)
            HEADLESS=1
            shift
            ;;
        --log-file)
            LOG_FILE="${2:-}"
            shift 2
            ;;
        --refresh-ms)
            REFRESH_MS="${2:-}"
            shift 2
            ;;
        --doctor)
            EXTRA_ARGS+=("--doctor")
            shift
            ;;
        --help|-h)
            cat <<'USAGE'
Usage: ./scripts/run_terminal_monitor.sh [--no-tmux] [--headless] [--log-file PATH] [--refresh-ms MS] [--doctor]

--no-tmux       Disable tmux auto-launch.
--headless      Run in headless logging mode.
--log-file      Override log file location.
--refresh-ms    UI refresh interval in milliseconds.
--doctor        Print environment diagnostics and exit.
USAGE
            exit 0
            ;;
        *)
            break
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return
    fi
    if command -v sudo >/dev/null 2>&1; then
        sudo "$@"
        return
    fi
    return 1
}

ensure_dir() {
    local target="$1"
    if [ -d "$target" ]; then
        return
    fi
    if ! mkdir -p "$target" 2>/dev/null; then
        run_as_root mkdir -p "$target" || true
    fi
}

ensure_dir /var/log/hyperion
ensure_dir /var/lib/hyperion

if [ -z "${TERM:-}" ] || [ "${TERM:-}" = "dumb" ]; then
    export TERM=xterm-256color
fi

PYTHON_BIN="${PYTHON:-python}"
CMD=("$PYTHON_BIN" -m terminal_monitor.app)
if [ "$HEADLESS" -eq 1 ]; then
    CMD+=(--headless)
fi
if [ -n "$LOG_FILE" ]; then
    CMD+=(--log-file "$LOG_FILE")
fi
if [ -n "$REFRESH_MS" ]; then
    CMD+=(--refresh-ms "$REFRESH_MS")
fi
CMD+=("${EXTRA_ARGS[@]}")

if [ "$USE_TMUX" -eq 1 ] && [ "$HEADLESS" -eq 0 ] && command -v tmux >/dev/null 2>&1 && [ -z "${TMUX:-}" ]; then
    if tmux has-session -t hyperion >/dev/null 2>&1; then
        tmux new-window -t hyperion -n monitor "PYTHONPATH=$REPO_ROOT ${CMD[*]}"
    else
        tmux new-session -d -s hyperion -n monitor "PYTHONPATH=$REPO_ROOT ${CMD[*]}"
    fi
    echo "✅ Terminal monitor launched in tmux session 'hyperion'."
    echo "Attach with: tmux attach -t hyperion"
    exit 0
fi

exec env PYTHONPATH="$REPO_ROOT" "${CMD[@]}"
