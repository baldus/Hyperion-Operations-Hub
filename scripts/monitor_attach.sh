#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v tmux >/dev/null 2>&1; then
    echo "❌ tmux is not installed. Run: $SCRIPT_DIR/monitor_launch.sh"
    exit 1
fi

if ! tmux has-session -t hyperion >/dev/null 2>&1; then
    echo "⚠️ tmux session 'hyperion' not found."
    echo "Run: $SCRIPT_DIR/monitor_launch.sh"
    exit 1
fi

if ! tmux list-windows -t hyperion -F '#W' | grep -Fx "monitor" >/dev/null 2>&1; then
    tmux new-window -t hyperion -n monitor "PYTHONPATH=$REPO_ROOT ${PYTHON:-python} -m terminal_monitor.app"
fi

tmux attach -t hyperion
