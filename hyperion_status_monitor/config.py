"""Configuration for the Hyperion Status Monitor."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    interval_sec: int
    db_path: Path
    log_path: Path
    backup_status_path: Path
    backup_dir: Path
    main_app_health_url: str
    database_url: str | None
    scheduler_tick_path: Path | None
    collector_timeout_sec: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            host="127.0.0.1",
            port=int(os.getenv("STATUS_MONITOR_PORT", "5055")),
            interval_sec=int(os.getenv("STATUS_MONITOR_INTERVAL_SEC", "10")),
            db_path=Path(
                os.getenv(
                    "STATUS_MONITOR_DB_PATH",
                    "/var/lib/hyperion-status-monitor/status.db",
                )
            ),
            log_path=Path(
                os.getenv(
                    "STATUS_MONITOR_LOG_PATH",
                    "/var/log/hyperion-status-monitor/monitor.log",
                )
            ),
            backup_status_path=Path(
                os.getenv(
                    "STATUS_MONITOR_BACKUP_STATUS_PATH",
                    "/var/lib/hyperion/backups/last_backup.json",
                )
            ),
            backup_dir=Path(
                os.getenv("STATUS_MONITOR_BACKUP_DIR", "/var/lib/hyperion/backups")
            ),
            main_app_health_url=os.getenv("MAIN_APP_HEALTH_URL", ""),
            database_url=os.getenv("DATABASE_URL"),
            scheduler_tick_path=_optional_path(
                os.getenv("STATUS_MONITOR_SCHEDULER_TICK_PATH")
            ),
            collector_timeout_sec=float(
                os.getenv("STATUS_MONITOR_COLLECTOR_TIMEOUT", "3")
            ),
        )


def _optional_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(raw)
