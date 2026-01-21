"""Collector for disk checks."""
from __future__ import annotations

import os
import shutil
from typing import Any

from ..config import Config


WARN_FREE_PERCENT = 10.0
ERROR_FREE_PERCENT = 5.0


def collect(config: Config) -> dict[str, Any]:
    paths = _paths_to_check(config)
    metrics: dict[str, Any] = {}
    lowest_free = 100.0
    missing: list[str] = []

    for path in paths:
        if not path.exists():
            missing.append(str(path))
            continue
        usage = shutil.disk_usage(path)
        free_percent = (usage.free / usage.total) * 100
        lowest_free = min(lowest_free, free_percent)
        metrics[str(path)] = {
            "total_gb": round(usage.total / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "free_percent": round(free_percent, 2),
        }

    status = "OK"
    ok = True
    details = "Disk space healthy."
    if missing:
        status = "WARN"
        ok = False
        details = f"Missing paths: {', '.join(missing)}"
    if lowest_free <= ERROR_FREE_PERCENT:
        status = "ERROR"
        ok = False
        details = f"Low disk space: {lowest_free:.1f}% free"
    elif lowest_free <= WARN_FREE_PERCENT:
        status = "WARN"
        ok = False
        details = f"Disk space warning: {lowest_free:.1f}% free"

    return {
        "ok": ok,
        "status": status,
        "details": details,
        "metrics": metrics,
    }


def _paths_to_check(config: Config) -> list[os.PathLike[str]]:
    return [
        config.db_path.parent,
        config.backup_dir,
        config.log_path.parent,
    ]
