from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from terminal_monitor.util.fs import safe_read_text

NETWORK_STATUS_PATH = Path("/var/lib/hyperion/network_status.txt")
FALLBACK_STATUS = "UNKNOWN | network watchdog not running"


@dataclass
class NetworkStatus:
    status: str
    raw: str


def read_network_status(path: Path = NETWORK_STATUS_PATH) -> NetworkStatus:
    raw = safe_read_text(path, default=FALLBACK_STATUS)
    status = raw.split("|", 1)[0].strip().upper() if raw else "UNKNOWN"
    if not status:
        status = "UNKNOWN"
    return NetworkStatus(status=status, raw=raw)
