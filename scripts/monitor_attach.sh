#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

resolve_python() {
    if [ -n "${VENV_DIR:-}" ] && [ -x "${VENV_DIR}/bin/python" ]; then
        echo "${VENV_DIR}/bin/python"
        return
    fi
    if [ -x "$REPO_ROOT/invapp2/.venv/bin/python" ]; then
        echo "$REPO_ROOT/invapp2/.venv/bin/python"
        return
    fi
    if [ -n "${PYTHON:-}" ]; then
        echo "$PYTHON"
        return
    fi
    echo "python3"
}

LOG_DIR="${HYPERION_LOG_DIR:-$HOME/.local/state/hyperion/logs}"
PYTHON_BIN="$(resolve_python)"

if ! command -v tmux >/dev/null 2>&1; then
    echo "❌ tmux is not installed. Run: $SCRIPT_DIR/monitor_launch.sh"
    exit 1
fi

if ! tmux has-session -t hyperion-monitor >/dev/null 2>&1; then
    echo "⚠️ tmux session 'hyperion-monitor' not found."
    echo "Run: $SCRIPT_DIR/monitor_launch.sh"
    exit 1
fi

if ! tmux list-windows -t hyperion-monitor -F '#W' | grep -Fx "monitor" >/dev/null 2>&1; then
    tmux new-window -t hyperion-monitor -n monitor "HYPERION_LOG_DIR=$LOG_DIR PYTHONPATH=$REPO_ROOT ${PYTHON_BIN} -m terminal_monitor.app"
fi

tmux attach -t hyperion-monitor
