from __future__ import annotations

import socket
import time
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import psutil
import json

from sqlalchemy import create_engine, text


@dataclass
class ProcessMetrics:
    status: str
    uptime: float
    cpu_percent: float
    memory_mb: float
    thread_count: int
    connections: int


@dataclass
class PortStatus:
    port: int
    reachable: bool
    last_checked: float


@dataclass
class LogSnapshot:
    lines: list[str]
    path: Path


@dataclass
class AccessSnapshot:
    users: list[str]
    pages: list[str]
    status: str


@dataclass
class OpsEventEntry:
    created_at: datetime
    level: str
    message: str
    source: str | None
    context: dict | None


@dataclass
class BackupStatus:
    frequency_hours: int
    frequency_source: str
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_message: str | None
    last_run_filename: str | None
    last_run_filepath: str | None
    last_success_at: datetime | None
    next_run_at: datetime | None


@dataclass
class ErrorSnapshot:
    entries: list[str]
    status: str


@dataclass
class ConnectivityStatus:
    online: bool
    last_seen: datetime | None
    last_checked: datetime | None
    last_failure: datetime | None


def ping_host(host: str, *, timeout: int = 1) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def read_process_metrics(pid: int) -> Optional[ProcessMetrics]:
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            status = proc.status()
            uptime = time.time() - proc.create_time()
            cpu_percent = proc.cpu_percent(interval=None)
            memory_mb = proc.memory_info().rss / (1024 * 1024)
            thread_count = proc.num_threads()
            connections = len(proc.connections())
        return ProcessMetrics(
            status=status,
            uptime=uptime,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            thread_count=thread_count,
            connections=connections,
        )
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def check_port(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> PortStatus:
    reachable = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            reachable = True
        except OSError:
            reachable = False
    return PortStatus(port=port, reachable=reachable, last_checked=time.time())


def tail_log(path: Path, max_lines: int = 18, state: dict[str, int] | None = None) -> LogSnapshot:
    """Read the last *max_lines* from *path* while maintaining cursor state."""

    cursor = state or {}
    position = cursor.get("position", 0)
    lines: list[str] = []

    if not path.exists():
        return LogSnapshot(lines=["Log file not found: " + str(path)], path=path)

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(0, 2)
            end_position = fh.tell()
            if end_position < position:
                position = 0
            seek_to = max(end_position - 8192, 0)
            fh.seek(seek_to)
            buffer = fh.read()
            lines = buffer.splitlines()[-max_lines:]
            cursor["position"] = end_position
    except OSError as exc:
        lines = [f"Unable to read log: {exc}"]

    return LogSnapshot(lines=lines, path=path)


def read_recent_access(
    db_url: str | None,
    *,
    limit: int = 8,
    window_seconds: int = 300,
) -> AccessSnapshot:
    if not db_url:
        return AccessSnapshot(users=[], pages=[], status="DB_URL not set")

    since = datetime.utcnow() - timedelta(seconds=window_seconds)
    users: list[str] = []
    pages: list[str] = []

    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            user_rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT COALESCE(username, ip_address, 'anonymous') AS identity
                    FROM access_log
                    WHERE occurred_at >= :since
                    ORDER BY identity
                    LIMIT :limit
                    """
                ),
                {"since": since, "limit": limit},
            )
            users = [row.identity for row in user_rows if row.identity]

            page_rows = conn.execute(
                text(
                    """
                    SELECT occurred_at, COALESCE(username, ip_address, 'anonymous') AS identity, path
                    FROM access_log
                    WHERE occurred_at >= :since AND event_type = :event_type
                    ORDER BY occurred_at DESC
                    LIMIT :limit
                    """
                ),
                {"since": since, "event_type": "request", "limit": limit},
            )
            for row in page_rows:
                timestamp = row.occurred_at.strftime("%H:%M:%S") if row.occurred_at else "--:--:--"
                pages.append(f"{timestamp} {row.identity}: {row.path or '-'}")
        engine.dispose()
    except Exception as exc:
        return AccessSnapshot(users=[], pages=[], status=f"DB error: {exc.__class__.__name__}")

    status = "Recent access (last 5m)"
    return AccessSnapshot(users=users, pages=pages, status=status)


def read_ops_events(db_url: str | None, *, limit: int = 12) -> list[OpsEventEntry]:
    if not db_url:
        return []
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT created_at, level, message, source, context_json
                    FROM ops_event_log
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            results = []
            for row in rows:
                context = row.context_json
                if isinstance(context, str):
                    try:
                        context = json.loads(context)
                    except json.JSONDecodeError:
                        context = None
                results.append(
                    OpsEventEntry(
                        created_at=row.created_at,
                        level=row.level,
                        message=row.message,
                        source=row.source,
                        context=context if isinstance(context, dict) else None,
                    )
                )
        engine.dispose()
        return results
    except Exception:
        return []


def read_backup_status(db_url: str | None, *, default_frequency: int = 4) -> BackupStatus:
    if not db_url:
        return BackupStatus(
            frequency_hours=default_frequency,
            frequency_source="default",
            last_run_at=None,
            last_run_status=None,
            last_run_message=None,
            last_run_filename=None,
            last_run_filepath=None,
            last_success_at=None,
            next_run_at=None,
        )
    frequency = default_frequency
    frequency_source = "default"
    last_run_at = None
    last_run_status = None
    last_run_message = None
    last_run_filename = None
    last_run_filepath = None
    last_success_at = None
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT value
                    FROM app_setting
                    WHERE key = :key
                    LIMIT 1
                    """
                ),
                {"key": "backup_frequency_hours"},
            ).first()
            if row and row.value is not None:
                try:
                    parsed = int(row.value)
                    if parsed > 0:
                        frequency = parsed
                        frequency_source = "setting"
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
        engine.dispose()
    except Exception:
        pass

    next_run_at = None
    if last_run_at:
        next_run_at = last_run_at + timedelta(hours=frequency)

    return BackupStatus(
        frequency_hours=frequency,
        frequency_source=frequency_source,
        last_run_at=last_run_at,
        last_run_status=last_run_status,
        last_run_message=last_run_message,
        last_run_filename=last_run_filename,
        last_run_filepath=last_run_filepath,
        last_success_at=last_success_at,
        next_run_at=next_run_at,
    )


def read_recent_errors(db_url: str | None, *, limit: int = 5) -> ErrorSnapshot:
    if not db_url:
        return ErrorSnapshot(entries=[], status="DB_URL not set")
    entries: list[str] = []
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT occurred_at, message
                    FROM error_report
                    ORDER BY occurred_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            for row in rows:
                timestamp = row.occurred_at.strftime("%H:%M:%S") if row.occurred_at else "--:--:--"
                message = (row.message or "").splitlines()[0]
                entries.append(f"{timestamp} {message}")
        engine.dispose()
        return ErrorSnapshot(entries=entries, status="Recent exceptions")
    except Exception as exc:
        return ErrorSnapshot(entries=[], status=f"Error log unavailable: {exc.__class__.__name__}")


def read_sequence_repair_summary(db_url: str | None) -> dict | None:
    if not db_url:
        return None
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT context_json
                    FROM ops_event_log
                    WHERE source = 'sequence_repair'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
            ).first()
        engine.dispose()
        if row:
            context = row.context_json
            if isinstance(context, str):
                try:
                    context = json.loads(context)
                except json.JSONDecodeError:
                    context = None
            if isinstance(context, dict):
                return context
    except Exception:
        return None
    return None


def read_boot_status(db_url: str | None) -> str | None:
    if not db_url:
        return None
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT created_at, message
                    FROM ops_event_log
                    WHERE source = 'startup'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
            ).first()
        engine.dispose()
        if row and row.created_at:
            timestamp = row.created_at.strftime("%Y-%m-%d %H:%M UTC")
            return f"{row.message} ({timestamp})"
    except Exception:
        return None
    return None


def mask_db_url(db_url: str | None) -> str:
    if not db_url:
        return "not set"
    if "@" not in db_url:
        return db_url
    prefix, rest = db_url.split("@", 1)
    if "://" in prefix:
        scheme, creds = prefix.split("://", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:****@{rest}"
    return f"****@{rest}"


def format_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def summarize_connections(connections: Iterable[psutil._common.sconn], port: int) -> int:
    count = 0
    for conn in connections:
        if conn.laddr and conn.laddr.port == port:
            count += 1
    return count
