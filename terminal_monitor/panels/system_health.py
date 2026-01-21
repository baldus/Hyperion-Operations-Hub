from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from terminal_monitor.util.fs import safe_read_text


@dataclass
class SystemHealth:
    load_avg: str
    mem_usage: str
    disk_root: str
    disk_var: str
    uptime: str
    cpu_temp: str


def get_system_health() -> SystemHealth:
    load_avg = _read_loadavg()
    mem_usage = _read_memory()
    disk_root = _read_disk("/")
    disk_var = _read_disk("/var")
    uptime = _read_uptime()
    cpu_temp = _read_cpu_temp()
    return SystemHealth(
        load_avg=load_avg,
        mem_usage=mem_usage,
        disk_root=disk_root,
        disk_var=disk_var,
        uptime=uptime,
        cpu_temp=cpu_temp,
    )


def _read_loadavg() -> str:
    try:
        with Path("/proc/loadavg").open("r", encoding="utf-8") as handle:
            values = handle.read().split()
        return " ".join(values[:3]) if values else "unknown"
    except OSError:
        return "unknown"


def _read_memory() -> str:
    try:
        mem = psutil.virtual_memory()
        return f"{mem.used // (1024 ** 2)}MB / {mem.total // (1024 ** 2)}MB ({mem.percent:.0f}%)"
    except Exception:
        return "unknown"


def _read_disk(path: str) -> str:
    try:
        usage = psutil.disk_usage(path)
        return f"{usage.used // (1024 ** 3)}G / {usage.total // (1024 ** 3)}G ({usage.percent:.0f}%)"
    except Exception:
        return "unknown"


def _read_uptime() -> str:
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = max(0, int(time.time() - boot_time))
    except Exception:
        return "unknown"

    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _read_cpu_temp() -> str:
    candidates = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    for candidate in candidates:
        raw = safe_read_text(candidate, default="")
        if raw.isdigit():
            value = int(raw)
            if value > 1000:
                value = value / 1000
            return f"{value:.1f}°C"
    return "n/a"


def get_platform_summary() -> str:
    return platform.platform()
