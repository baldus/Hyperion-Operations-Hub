#!/usr/bin/env bash
set -Eeuo pipefail

CHECK_HOST="${CHECK_HOST:-1.1.1.1}"
INTERVAL="${INTERVAL:-10}"
PING_TIMEOUT="${PING_TIMEOUT:-1}"

STATUS_DIR="/var/lib/hyperion"
STATUS_FILE="${STATUS_DIR}/network_status.txt"
LOG_FILE="/var/log/internet_watchdog.log"

timestamp() {
    date --iso-8601=seconds
}

log_event() {
    local message="$1"
    printf '%s | %s\n' "$(timestamp)" "$message" >> "$LOG_FILE"
}

write_status() {
    local status="$1"
    local detail="$2"
    local temp_file
    temp_file="$(mktemp)"
    printf '%s | %s | %s\n' "$status" "$(timestamp)" "$detail" > "$temp_file"
    mv "$temp_file" "$STATUS_FILE"
}

run_cmd() {
    local description="$1"
    shift
    if "$@"; then
        log_event "RECOVERY OK: ${description}"
        return 0
    fi
    log_event "RECOVERY FAILED: ${description}"
    return 1
}

ensure_paths() {
    install -d -m 0755 "$STATUS_DIR"
    if [ ! -f "$STATUS_FILE" ]; then
        touch "$STATUS_FILE"
    fi
    chmod 0644 "$STATUS_FILE"
    if [ ! -f "$LOG_FILE" ]; then
        touch "$LOG_FILE"
    fi
    chmod 0640 "$LOG_FILE"
}

validate_interval() {
    if ! [[ "$INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        log_event "Invalid INTERVAL=$INTERVAL; defaulting to 10 seconds"
        INTERVAL="10"
    fi
}

ping_check() {
    if ! command -v ping >/dev/null 2>&1; then
        return 2
    fi
    ping -c 1 -W "$PING_TIMEOUT" "$CHECK_HOST" >/dev/null 2>&1
}

recover_connectivity() {
    log_event "Starting recovery sequence"
    run_cmd "systemctl restart systemd-resolved" systemctl restart systemd-resolved || true
    run_cmd "systemctl restart NetworkManager" systemctl restart NetworkManager || true

    if command -v nmcli >/dev/null 2>&1; then
        run_cmd "nmcli networking off" nmcli networking off || true
        sleep 2
        run_cmd "nmcli networking on" nmcli networking on || true

        local ethernet_device
        ethernet_device="$(nmcli -t -f DEVICE,TYPE,STATE dev status 2>/dev/null | awk -F: '$2=="ethernet" && $3=="connected" {print $1; exit}')"
        if [ -n "$ethernet_device" ]; then
            run_cmd "nmcli dev disconnect ${ethernet_device}" nmcli dev disconnect "$ethernet_device" || true
            sleep 2
            run_cmd "nmcli dev connect ${ethernet_device}" nmcli dev connect "$ethernet_device" || true
        else
            log_event "No active ethernet interface detected for nmcli reconnect"
        fi
    else
        log_event "nmcli not available; skipping networking toggles"
    fi
}

main_loop() {
    local last_state="UNKNOWN"
    while true; do
        local ping_result="OFFLINE"
        local detail="ping:${CHECK_HOST} FAILED | attempting recovery"
        if ping_check; then
            ping_result="ONLINE"
            detail="ping:${CHECK_HOST} OK"
        elif [ "$?" -eq 2 ]; then
            detail="ping unavailable | attempting recovery"
        fi

        write_status "$ping_result" "$detail"

        if [ "$ping_result" = "OFFLINE" ] && [ "$last_state" != "OFFLINE" ]; then
            log_event "OUTAGE detected for ${CHECK_HOST}"
        elif [ "$ping_result" = "ONLINE" ] && [ "$last_state" = "OFFLINE" ]; then
            log_event "RECOVERY detected for ${CHECK_HOST}"
        fi

        if [ "$ping_result" = "OFFLINE" ]; then
            recover_connectivity
        fi

        last_state="$ping_result"
        sleep "$INTERVAL"
    done
}

ensure_paths
validate_interval
log_event "Internet watchdog started (host=${CHECK_HOST}, interval=${INTERVAL}s)"
main_loop
