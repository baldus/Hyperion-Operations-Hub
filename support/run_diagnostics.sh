#!/usr/bin/env bash
set -euo pipefail

log_section() {
    printf '\n%s\n' "===== $1 ====="
}

run_command() {
    local description="$1"
    shift

    log_section "$description"
    if "$@"; then
        return 0
    fi

    local status=$?
    echo "(command failed with exit code $status)" >&2
    return 0
}

main() {
    echo "Hyperion Operations Diagnostics"
    echo "Generated at: $(date --iso-8601=seconds)"
    echo "Hostname: $(hostname)"

    run_command "System uptime" uptime || true

    if command -v systemctl >/dev/null 2>&1; then
        run_command "PostgreSQL service status" systemctl status postgresql --no-pager || true
    else
        echo "systemctl is not available on this host." >&2
    fi

    if command -v df >/dev/null 2>&1; then
        run_command "Disk usage" df -h || true
    fi

    if command -v free >/dev/null 2>&1; then
        run_command "Memory usage" free -h || true
    fi

    if command -v ss >/dev/null 2>&1; then
        run_command "Listening TCP sockets" ss -ltn || true
    fi

    if command -v journalctl >/dev/null 2>&1; then
        run_command "Recent Gunicorn log entries" journalctl -u gunicorn --since "-30 min" --no-pager || true
    fi

    echo "\nDiagnostics complete."
}

main "$@"
