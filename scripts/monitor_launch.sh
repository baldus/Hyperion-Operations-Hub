#!/usr/bin/env bash
set -euo pipefail

HEADLESS=0
PASS_THROUGH=()

while [ "${1:-}" != "" ]; do
    case "$1" in
        --headless)
            HEADLESS=1
            shift
            ;;
        --help|-h)
            cat <<'USAGE'
Usage: ./scripts/monitor_launch.sh [--headless] [-- <extra args>]

--headless   Force headless logging mode.
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

LOG_DIR="/var/log/hyperion"
LOG_FILE="$LOG_DIR/terminal_monitor_launcher.log"
FALLBACK_LOG="/tmp/hyperion_terminal_monitor_launcher.log"

log() {
    local message="$1"
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
        LOG_FILE="$FALLBACK_LOG"
    fi
    printf "%s %s\n" "$ts" "$message" >> "$LOG_FILE"
}

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
CMD+=("${PASS_THROUGH[@]}")

log "launcher start headless=$HEADLESS tty=$( [ -t 0 ] && echo yes || echo no ) tmux=${TMUX:-none} display=${DISPLAY:-none}"
log "command: PYTHONPATH=$REPO_ROOT ${CMD[*]}"

if [ -n "${TMUX:-}" ]; then
    log "detected tmux session; launching in new tmux window"
    window_exists=$(tmux list-windows -F '#W' 2>/dev/null | grep -Fx "monitor" || true)
    if [ -n "$window_exists" ]; then
        tmux select-window -t monitor
        log "monitor window already exists; selected"
        exit 0
    fi
    tmux new-window -n monitor "PYTHONPATH=$REPO_ROOT ${CMD[*]}"
    log "monitor window created in current tmux session"
    exit 0
fi

if command -v tmux >/dev/null 2>&1; then
    log "tmux available; creating/reusing session 'hyperion'"
    if tmux has-session -t hyperion >/dev/null 2>&1; then
        if tmux list-windows -t hyperion -F '#W' | grep -Fx "monitor" >/dev/null 2>&1; then
            log "monitor window already exists in hyperion session"
        else
            tmux new-window -t hyperion -n monitor "PYTHONPATH=$REPO_ROOT ${CMD[*]}"
            log "monitor window created in hyperion session"
        fi
    else
        tmux new-session -d -s hyperion -n monitor "PYTHONPATH=$REPO_ROOT ${CMD[*]}"
        log "tmux session 'hyperion' created with monitor window"
    fi
    echo "✅ Terminal monitor running in tmux session 'hyperion'."
    echo "Attach with: tmux attach -t hyperion"

    if [ -n "${DISPLAY:-}" ]; then
        for term in gnome-terminal konsole xterm x-terminal-emulator; do
            if command -v "$term" >/dev/null 2>&1; then
                log "GUI detected ($term); attempting to open terminal to attach"
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
    else
        log "no DISPLAY; skipping GUI launch"
    fi
    exit 0
fi

if [ -n "${DISPLAY:-}" ]; then
    for term in gnome-terminal konsole xterm x-terminal-emulator; do
        if command -v "$term" >/dev/null 2>&1; then
            log "tmux unavailable; opening GUI terminal ($term)"
            if [ "$term" = "gnome-terminal" ]; then
                gnome-terminal -- env PYTHONPATH="$REPO_ROOT" "${CMD[@]}" || true
            elif [ "$term" = "konsole" ]; then
                konsole -e env PYTHONPATH="$REPO_ROOT" "${CMD[@]}" || true
            else
                "$term" -e env PYTHONPATH="$REPO_ROOT" "${CMD[@]}" || true
            fi
            exit 0
        fi
    done
    log "DISPLAY set but no GUI terminal found; falling back"
fi

if [ -t 0 ] && [ -t 1 ] && [ "$HEADLESS" -eq 0 ]; then
    log "running monitor in foreground"
    exec env PYTHONPATH="$REPO_ROOT" "${CMD[@]}"
else
    log "no tty available; running headless"
    exec env PYTHONPATH="$REPO_ROOT" "${CMD[@]}" --headless
fi
