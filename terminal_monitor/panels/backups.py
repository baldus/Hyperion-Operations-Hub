from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text


@dataclass
class BackupStatus:
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_message: str | None
    last_run_filename: str | None
    last_run_filepath: str | None
    last_success_at: datetime | None
    next_run_at: datetime | None
    restore_last_at: datetime | None
    restore_last_status: str | None
    restore_last_filename: str | None
    restore_last_message: str | None
    restore_last_username: str | None


def read_backup_status(db_url: str | None, *, default_frequency: int = 4) -> BackupStatus:
    if not db_url:
        return _empty_status()

    frequency = default_frequency
    last_run_at = None
    last_run_status = None
    last_run_message = None
    last_run_filename = None
    last_run_filepath = None
    last_success_at = None
    restore_last_at = None
    restore_last_status = None
    restore_last_filename = None
    restore_last_message = None
    restore_last_username = None

    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_setting WHERE key = :key LIMIT 1"),
                {"key": "backup_frequency_hours"},
            ).first()
            if row and row.value is not None:
                try:
                    parsed = int(row.value)
                    if parsed > 0:
                        frequency = parsed
                except (TypeError, ValueError):
                    pass

            last_run = conn.execute(
                text(
                    """
                    SELECT started_at, status, message, filename, filepath
                    FROM backup_run
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            ).first()
            if last_run:
                last_run_at = last_run.started_at
                last_run_status = last_run.status
                last_run_message = last_run.message
                last_run_filename = last_run.filename
                last_run_filepath = last_run.filepath

            last_success = conn.execute(
                text(
                    """
                    SELECT started_at
                    FROM backup_run
                    WHERE status = 'succeeded'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            ).first()
            if last_success:
                last_success_at = last_success.started_at

            restore_row = conn.execute(
                text(
                    """
                    SELECT occurred_at, status, backup_filename, message, username
                    FROM backup_restore_event
                    WHERE action = :action
                    ORDER BY occurred_at DESC
                    LIMIT 1
                    """
                ),
                {"action": "restore"},
            ).first()
            if restore_row:
                restore_last_at = restore_row.occurred_at
                restore_last_status = restore_row.status
                restore_last_filename = restore_row.backup_filename
                restore_last_message = restore_row.message
                restore_last_username = restore_row.username
        engine.dispose()
    except Exception:
        return _empty_status()

    next_run_at = None
    if last_run_at:
        next_run_at = last_run_at + timedelta(hours=frequency)

    return BackupStatus(
        last_run_at=last_run_at,
        last_run_status=last_run_status,
        last_run_message=last_run_message,
        last_run_filename=last_run_filename,
        last_run_filepath=last_run_filepath,
        last_success_at=last_success_at,
        next_run_at=next_run_at,
        restore_last_at=restore_last_at,
        restore_last_status=restore_last_status,
        restore_last_filename=restore_last_filename,
        restore_last_message=restore_last_message,
        restore_last_username=restore_last_username,
    )


def _empty_status() -> BackupStatus:
    return BackupStatus(
        last_run_at=None,
        last_run_status=None,
        last_run_message=None,
        last_run_filename=None,
        last_run_filepath=None,
        last_success_at=None,
        next_run_at=None,
        restore_last_at=None,
        restore_last_status=None,
        restore_last_filename=None,
        restore_last_message=None,
        restore_last_username=None,
    )
