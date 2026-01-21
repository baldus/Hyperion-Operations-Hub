#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="${HYPERION_LOG_DIR:-$HOME/.local/state/hyperion/logs}"
MONITOR_LOG="$LOG_DIR/terminal_monitor.log"
LAUNCHER_LOG="$LOG_DIR/terminal_monitor_launcher.log"
STATUS_FILE="/var/lib/hyperion/network_status.txt"

printf "Hyperion Terminal Monitor Doctor\n"
printf "===============================\n"
printf "isatty stdin: %s\n" "$( [ -t 0 ] && echo yes || echo no )"
printf "isatty stdout: %s\n" "$( [ -t 1 ] && echo yes || echo no )"
printf "TERM: %s\n" "${TERM:-}"
printf "DISPLAY: %s\n" "${DISPLAY:-}"
printf "log_dir: %s\n" "$LOG_DIR"

printf "\nDependencies\n-----------\n"
if command -v tmux >/dev/null 2>&1; then
    printf "tmux: available (%s)\n" "$(command -v tmux)"
else
    printf "tmux: missing\n"
fi

for term in gnome-terminal konsole xterm x-terminal-emulator; do
    if command -v "$term" >/dev/null 2>&1; then
        printf "terminal: %s -> %s\n" "$term" "$(command -v "$term")"
    fi
done

printf "\nNetwork status\n--------------\n"
if [ -r "$STATUS_FILE" ]; then
    last_line=$(tail -n 1 "$STATUS_FILE" | tr -d '\r')
    printf "status_file: readable\n"
    printf "last_line: %s\n" "$last_line"
else
    printf "status_file: missing or unreadable (%s)\n" "$STATUS_FILE"
fi

printf "\nLogs\n----\n"
printf "monitor_log: %s\n" "$MONITOR_LOG"
printf "launcher_log: %s\n" "$LAUNCHER_LOG"

if [ -f "$LAUNCHER_LOG" ]; then
    printf "launcher_log_tail: %s\n" "$(tail -n 1 "$LAUNCHER_LOG" | tr -d '\r')"
fi
