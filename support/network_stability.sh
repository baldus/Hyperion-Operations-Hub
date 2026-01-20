#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

nm_conf="/etc/NetworkManager/conf.d/ethernet-powersave.conf"
resolved_conf="/etc/systemd/resolved.conf"
service_override_dir="/etc/systemd/system/NetworkManager.service.d"
service_override_file="$service_override_dir/override.conf"

install -d "$(dirname "$nm_conf")"
cat > "$nm_conf" <<'NMCONF'
[connection]
ethernet.powersave = 2
NMCONF

python3 - <<'PY'
from __future__ import annotations

import configparser
from pathlib import Path

path = Path("/etc/systemd/resolved.conf")
config = configparser.ConfigParser()
config.optionxform = str
if path.exists():
    config.read(path)
if "Resolve" not in config:
    config["Resolve"] = {}
resolve = config["Resolve"]
resolve["DNS"] = "1.1.1.1 8.8.8.8"
resolve["FallbackDNS"] = "9.9.9.9"
resolve["DNSStubListener"] = "yes"
with path.open("w") as handle:
    config.write(handle)
PY

install -d "$service_override_dir"
cat > "$service_override_file" <<'OVERRIDE'
[Service]
Restart=always
RestartSec=5
OVERRIDE

systemctl daemon-reload
systemctl enable --now NetworkManager
systemctl restart NetworkManager
systemctl reset-failed NetworkManager

systemctl enable --now systemd-resolved
systemctl restart systemd-resolved

resolvectl status
