#!/usr/bin/env bash
set -euo pipefail

HEADLESS=0
GUI=0
PASS_THROUGH=()

while [ "${1:-}" != "" ]; do
    case "$1" in
        --headless)
            HEADLESS=1
            shift
            ;;
        --gui)
            GUI=1
            shift
            ;;
        --help|-h)
            cat <<'USAGE'
Usage: ./scripts/monitor_launch.sh [--headless] [--gui] [-- <extra args>]

--headless   Force headless logging mode.
--gui        Attempt to open a GUI terminal (best effort).
USAGE
            exit 0
            ;;
        --)
            shift
            PASS_THROUGH+=("$@")
            break
            ;;
        *)
            PASS_THROUGH+=("$1")
            shift
            ;;
    esac


done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LOG_DIR="${HYPERION_LOG_DIR:-$HOME/.local/state/hyperion/logs}"
LOG_FILE="$LOG_DIR/terminal_monitor_launcher.log"
FALLBACK_LOG="/tmp/hyperion/terminal_monitor_launcher.log"
APP_LOG="$LOG_DIR/terminal_monitor_app.log"
PID_FILE="${HYPERION_MONITOR_PID_FILE:-$LOG_DIR/terminal_monitor.pid}"

log() {
    local message="$1"
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
        LOG_FILE="$FALLBACK_LOG"
    fi
    printf "%s %s\n" "$ts" "$message" >> "$LOG_FILE"
}

trap 'log "launcher error: exit=$? cmd=${BASH_COMMAND}"' ERR

ensure_dir() {
    local target="$1"
    if [ -d "$target" ]; then
        return
    fi
    if ! mkdir -p "$target" 2>/dev/null; then
        if command -v sudo >/dev/null 2>&1; then
            sudo mkdir -p "$target" || true
        fi
    fi
}

ensure_dir "$LOG_DIR"
if [ ! -d "$LOG_DIR" ]; then
    LOG_DIR="/tmp/hyperion"
    LOG_FILE="$LOG_DIR/terminal_monitor_launcher.log"
    APP_LOG="$LOG_DIR/terminal_monitor_app.log"
    PID_FILE="${HYPERION_MONITOR_PID_FILE:-$LOG_DIR/terminal_monitor.pid}"
    ensure_dir "$LOG_DIR"
fi
ensure_dir /var/lib/hyperion

if [ -z "${TERM:-}" ] || [ "${TERM:-}" = "dumb" ]; then
    export TERM=xterm-256color
fi

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

PYTHON_BIN="$(resolve_python)"
CMD=("$PYTHON_BIN" -m terminal_monitor.app)
if [ "$HEADLESS" -eq 1 ]; then
    CMD+=(--headless)
fi
CMD+=("${PASS_THROUGH[@]}")

log "launcher start headless=$HEADLESS gui=$GUI tty=$( [ -t 0 ] && echo yes || echo no ) tmux=${TMUX:-none} display=${DISPLAY:-none}"
log "command: PYTHONPATH=$REPO_ROOT ${CMD[*]}"
log "python: $PYTHON_BIN"

if [ -n "${TMUX:-}" ]; then
    log "detected tmux session; launching in new tmux window"
    window_exists=$(tmux list-windows -F '#W' 2>/dev/null | grep -Fx "monitor" || true)
    if [ -n "$window_exists" ]; then
        tmux select-window -t monitor
        log "monitor window already exists; selected"
        exit 0
    fi
    if tmux new-window -n monitor "HYPERION_LOG_DIR=$LOG_DIR PYTHONPATH=$REPO_ROOT ${CMD[*]}"; then
        log "monitor window created in current tmux session"
        exit 0
    else
        log "failed to create monitor window in tmux; falling back to headless"
        HEADLESS=1
    fi
fi

if command -v tmux >/dev/null 2>&1 && [ "$HEADLESS" -eq 0 ]; then
    log "tmux available; creating/reusing session 'hyperion-monitor'"
    if tmux has-session -t hyperion-monitor >/dev/null 2>&1; then
        if tmux list-windows -t hyperion-monitor -F '#W' | grep -Fx "monitor" >/dev/null 2>&1; then
            log "monitor window already exists in hyperion-monitor session"
        else
            tmux new-window -t hyperion-monitor -n monitor "HYPERION_LOG_DIR=$LOG_DIR PYTHONPATH=$REPO_ROOT ${CMD[*]}"
            log "monitor window created in hyperion-monitor session"
        fi
    else
        tmux new-session -d -s hyperion-monitor -n monitor "HYPERION_LOG_DIR=$LOG_DIR PYTHONPATH=$REPO_ROOT ${CMD[*]}"
        log "tmux session 'hyperion-monitor' created with monitor window"
    fi
    echo "✅ Terminal monitor running in tmux session 'hyperion-monitor'."
    echo "Attach with: tmux attach -t hyperion-monitor"

    if [ "$GUI" -eq 1 ] && [ -n "${DISPLAY:-}" ]; then
        for term in gnome-terminal konsole xterm x-terminal-emulator; do
            if command -v "$term" >/dev/null 2>&1; then
                log "GUI requested; attempting to open terminal ($term) to attach"
                if [ "$term" = "gnome-terminal" ]; then
                    gnome-terminal -- "$SCRIPT_DIR/monitor_attach.sh" || true
                elif [ "$term" = "konsole" ]; then
                    konsole -e "$SCRIPT_DIR/monitor_attach.sh" || true
                else
                    "$term" -e "$SCRIPT_DIR/monitor_attach.sh" || true
                fi
                break
            fi
        done
    elif [ "$GUI" -eq 1 ]; then
        log "GUI requested but no DISPLAY; skipping GUI launch"
    fi
    exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
    log "tmux not available; defaulting to headless mode"
    HEADLESS=1
fi

if [ "$GUI" -eq 1 ] && [ -n "${DISPLAY:-}" ]; then
    for term in gnome-terminal konsole xterm x-terminal-emulator; do
        if command -v "$term" >/dev/null 2>&1; then
            log "tmux unavailable; opening GUI terminal ($term)"
            if [ "$term" = "gnome-terminal" ]; then
                gnome-terminal -- env HYPERION_LOG_DIR="$LOG_DIR" PYTHONPATH="$REPO_ROOT" "${CMD[@]}" || true
            elif [ "$term" = "konsole" ]; then
                konsole -e env HYPERION_LOG_DIR="$LOG_DIR" PYTHONPATH="$REPO_ROOT" "${CMD[@]}" || true
            else
                "$term" -e env HYPERION_LOG_DIR="$LOG_DIR" PYTHONPATH="$REPO_ROOT" "${CMD[@]}" || true
            fi
            exit 0
        fi
    done
    log "GUI requested but no GUI terminal found; falling back"
elif [ "$GUI" -eq 1 ]; then
    log "GUI requested but DISPLAY not set; falling back"
fi

if [ -t 0 ] && [ -t 1 ] && [ "$HEADLESS" -eq 0 ]; then
    log "running monitor in foreground"
    exec env HYPERION_LOG_DIR="$LOG_DIR" PYTHONPATH="$REPO_ROOT" "${CMD[@]}"
else
    log "no tty available or headless forced; running headless in background"
    ensure_dir "$LOG_DIR"
    nohup env HYPERION_LOG_DIR="$LOG_DIR" PYTHONPATH="$REPO_ROOT" "${CMD[@]}" --headless >> "$APP_LOG" 2>&1 &
    echo $! > "$PID_FILE"
    log "headless monitor pid=$(cat "$PID_FILE") log=$APP_LOG"
    echo "✅ Headless monitor started. Log: $APP_LOG"
fi
