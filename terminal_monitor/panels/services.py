from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from terminal_monitor.util.proc import run_command


@dataclass
class ServiceStatus:
    name: str
    status: str
    detail: str


DEFAULT_SERVICES = [
    ("NetworkManager", "NetworkManager"),
    ("systemd-resolved", "systemd-resolved"),
    ("internet-watchdog.service", "internet-watchdog"),
    ("hyperion-operations-hub.service", "hyperion app"),
]


def resolve_service_list() -> list[tuple[str, str]]:
    services = list(DEFAULT_SERVICES)
    custom_service = shutil.which("systemctl") and _env_service()
    if custom_service:
        services.append((custom_service, custom_service.replace(".service", "")))
    return services


def get_service_statuses(services: list[tuple[str, str]]) -> list[ServiceStatus]:
    if not shutil.which("systemctl"):
        return [ServiceStatus(name=label, status="missing", detail="systemctl not available") for name, label in services]

    results = []
    for unit, label in services:
        active = run_command(["systemctl", "is-active", unit], timeout=1.5)
        if active.timed_out:
            results.append(ServiceStatus(label, "timeout", "systemctl timed out"))
            continue
        if active.exit_code == 4:
            results.append(ServiceStatus(label, "missing", "unit not found"))
            continue
        status = active.stdout or "unknown"
        detail = _service_detail(unit)
        results.append(ServiceStatus(label, status, detail))
    return results


def _service_detail(unit: str) -> str:
    detail = run_command(["systemctl", "status", unit, "--no-pager", "--no-legend", "--lines=2"], timeout=2.0)
    if not detail.ok:
        return detail.stderr or "status unavailable"
    lines = [line.strip() for line in detail.stdout.splitlines() if line.strip()]
    return lines[0] if lines else "status unavailable"


def _env_service() -> str | None:
    value = (os.getenv("HYPERION_APP_SERVICE") or "").strip()
    if not value:
        return None
    if value.endswith(".service"):
        return value
    return f"{value}.service"
