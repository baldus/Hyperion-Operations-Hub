"""Snapshot schema and aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor

from .collectors import (
    app_info,
    backups,
    database,
    disk,
    main_app_ping,
    scheduler,
)
from .config import Config


Section = dict[str, Any]
Collector = Callable[[], Section]


@dataclass
class SnapshotResult:
    generated_at: str
    snapshot: dict[str, Any]


def build_snapshot(
    config: Config,
    start_time: float,
    logger,
    record_error: Callable[[str, str, dict[str, Any]], None],
    collector_timeout: float,
) -> SnapshotResult:
    generated_at = datetime.now(timezone.utc).isoformat()
    errors: list[dict[str, Any]] = []

    def run_collector(name: str, collector: Collector) -> Section:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(collector)
            try:
                return future.result(timeout=collector_timeout)
            except Exception as exc:  # noqa: BLE001 - ensure resilience
                message = f"Collector '{name}' failed: {exc}"
                logger.exception(message)
                error = {
                    "timestamp": generated_at,
                    "level": "error",
                    "message": message,
                    "context": {"collector": name},
                }
                errors.append(error)
                record_error("error", message, {"collector": name})
                return {
                    "ok": False,
                    "status": "ERROR",
                    "details": "Collector failed; check logs.",
                    "metrics": {},
                }

    snapshot = {
        "generated_at": generated_at,
        "app": run_collector(
            "app", lambda: app_info.collect(start_time, config)
        ),
        "db": run_collector("db", lambda: database.collect(config)),
        "disk": run_collector("disk", lambda: disk.collect(config)),
        "backups": run_collector("backups", lambda: backups.collect(config)),
        "jobs": run_collector("jobs", lambda: scheduler.collect(config)),
        "main_app": run_collector(
            "main_app", lambda: main_app_ping.collect(config)
        ),
        "errors": errors,
    }
    return SnapshotResult(generated_at=generated_at, snapshot=snapshot)
