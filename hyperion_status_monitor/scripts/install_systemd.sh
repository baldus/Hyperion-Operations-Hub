#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
UNIT_TEMPLATE="$REPO_DIR/hyperion_status_monitor/systemd/hyperion-status-monitor.service"
UNIT_TARGET="/etc/systemd/system/hyperion-status-monitor.service"
ENV_FILE="/etc/hyperion-status-monitor.env"

SERVICE_USER=${SERVICE_USER:-hyperion}
SERVICE_GROUP=${SERVICE_GROUP:-hyperion}

VENV_PYTHON=${VENV_PYTHON:-"$REPO_DIR/venv/bin/python"}
if [[ ! -x "$VENV_PYTHON" ]]; then
  VENV_PYTHON=$(command -v python3)
fi

sudo mkdir -p /var/lib/hyperion-status-monitor
sudo mkdir -p /var/log/hyperion-status-monitor

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  sudo useradd --system --no-create-home "$SERVICE_USER"
fi

sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" /var/lib/hyperion-status-monitor /var/log/hyperion-status-monitor

sudo sed \
  -e "s|{{INSTALL_DIR}}|$REPO_DIR|g" \
  -e "s|{{VENV_PYTHON}}|$VENV_PYTHON|g" \
  -e "s|User=hyperion|User=$SERVICE_USER|g" \
  -e "s|Group=hyperion|Group=$SERVICE_GROUP|g" \
  "$UNIT_TEMPLATE" | sudo tee "$UNIT_TARGET" >/dev/null

if [[ ! -f "$ENV_FILE" ]]; then
  sudo tee "$ENV_FILE" >/dev/null <<'ENV'
# Hyperion Status Monitor environment configuration
# STATUS_MONITOR_PORT=5055
# STATUS_MONITOR_INTERVAL_SEC=10
# STATUS_MONITOR_DB_PATH=/var/lib/hyperion-status-monitor/status.db
# STATUS_MONITOR_LOG_PATH=/var/log/hyperion-status-monitor/monitor.log
# STATUS_MONITOR_BACKUP_STATUS_PATH=/var/lib/hyperion/backups/last_backup.json
# STATUS_MONITOR_BACKUP_DIR=/var/lib/hyperion/backups
# MAIN_APP_HEALTH_URL=
# DATABASE_URL=
ENV
fi

sudo systemctl daemon-reload
sudo systemctl enable hyperion-status-monitor.service
sudo systemctl start hyperion-status-monitor.service

printf '\nStatus monitor installed and started.\n'
printf 'Logs: journalctl -u hyperion-status-monitor -f\n'
