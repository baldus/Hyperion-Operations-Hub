"""Automated backup service for database and critical data directories."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.engine.url import make_url
from sqlalchemy.sql import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from flask import current_app

from invapp.extensions import db
from invapp.models import AppSetting, BackupRun
from invapp.services import status_bus
from invapp.services.db_schema import ensure_app_setting_schema


BACKUP_SETTING_KEY = "backup_frequency_hours"
DEFAULT_BACKUP_FREQUENCY_HOURS = 4
BACKUP_JOB_ID = "automated-backup"
BACKUP_REFRESH_JOB_ID = "automated-backup-refresh"
BACKUP_REFRESH_MINUTES = 5
BACKUP_SUBDIRS = ("db", "files", "tmp")
AUTO_BACKUP_ALLOWED_EXTENSIONS = (".zip", ".tar.gz", ".sql", ".dump")
BACKUP_STATUS_PATH = Path(
    os.getenv("BACKUP_STATUS_PATH", "/var/lib/hyperion/backups/last_backup.json")
)
RESTORE_TIMEOUT_SECONDS = 900
_WARNED_KEYS: set[str] = set()
_APP_SETTING_REPAIR_ATTEMPTED = False


@dataclass
class RestoreOutcome:
    filename: str
    message: str
    stdout: str
    stderr: str
    duration_seconds: float


class RestoreFailure(RuntimeError):
    def __init__(
        self,
        *,
        filename: str,
        message: str,
        stdout: str = "",
        stderr: str = "",
        duration_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.filename = filename
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = duration_seconds


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _verify_writable(path: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=path, prefix=".write_test_", delete=True):
        pass


def get_backup_dir(app, logger: logging.Logger | None = None) -> Path:
    logger = logger or logging.getLogger("invapp.backup")
    env_dir = os.environ.get("BACKUP_DIR")
    config_dir = app.config.get("BACKUP_DIR")

    candidates = []
    if env_dir:
        candidates.append(("BACKUP_DIR env", Path(env_dir)))
    if config_dir:
        candidates.append(("BACKUP_DIR config", Path(config_dir)))

    instance_dir = Path(app.instance_path) / "backups"
    candidates.append(("instance path", instance_dir))
    candidates.append(("cwd fallback", Path.cwd() / "backups"))

    last_error: Exception | None = None
    for source, candidate in candidates:
        try:
            _ensure_directory(candidate)
            _verify_writable(candidate)
            for subdir in BACKUP_SUBDIRS:
                _ensure_directory(candidate / subdir)
            return candidate
        except Exception as exc:
            last_error = exc
            _warn_once(
                logger,
                f"Backup directory '{candidate}' from {source} is not writable; "
                "falling back to the next option. Set BACKUP_DIR to a writable path.",
                key=f"backup_dir:{candidate}",
            )
            status_bus.log_event(
                "warning",
                f"Backup directory '{candidate}' from {source} is not writable; falling back.",
                dedupe_key=f"backup_dir:{candidate}",
                source="backup",
            )
            continue

    raise RuntimeError(
        f"Unable to create a writable backup directory. Last error: {last_error}"
    )


def _backup_logger(backup_dir: Path) -> logging.Logger:
    logger = logging.getLogger("invapp.backup")
    log_path = backup_dir / "backup.log"
    if not any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", "") == str(log_path)
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(log_path)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _warn_once(logger: logging.Logger, message: str, key: str) -> None:
    if key in _WARNED_KEYS:
        return
    _WARNED_KEYS.add(key)
    _log_warning(logger, message)


def _log_warning(logger: logging.Logger, message: str) -> None:
    logger.warning(message)
    try:
        current_app.logger.warning(message)
    except Exception:
        pass


def _log_info(logger: logging.Logger, message: str) -> None:
    logger.info(message)
    try:
        current_app.logger.info(message)
    except Exception:
        pass


def _log_exception(logger: logging.Logger, message: str, exc: Exception) -> None:
    logger.exception(message)
    try:
        current_app.logger.exception("%s: %s", message, exc)
    except Exception:
        pass


def _timestamp_label() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d_%H%M")


def _next_copy_number(backup_dirs: Iterable[Path], timestamp: str) -> int:
    pattern = re.compile(rf"^backup_{re.escape(timestamp)}_copy(\d+)")
    max_copy = 0
    for backup_dir in backup_dirs:
        for item in backup_dir.iterdir():
            match = pattern.match(item.name)
            if not match:
                continue
            try:
                max_copy = max(max_copy, int(match.group(1)))
            except ValueError:
                continue
    return max_copy + 1


def _data_directories(app) -> list[Path]:
    config_keys = (
        "WORK_INSTRUCTION_UPLOAD_FOLDER",
        "ITEM_ATTACHMENT_UPLOAD_FOLDER",
        "QUALITY_ATTACHMENT_UPLOAD_FOLDER",
        "PURCHASING_ATTACHMENT_UPLOAD_FOLDER",
    )
    dirs: list[Path] = []
    for key in config_keys:
        path_value = app.config.get(key)
        if not path_value:
            continue
        path = Path(path_value)
        if path.exists() and path.is_dir():
            dirs.append(path)
    return dirs


def _build_pg_dump_env(app, logger: logging.Logger) -> dict[str, str] | None:
    env = os.environ.copy()
    if all(env.get(key) for key in ("PGHOST", "PGUSER", "PGDATABASE")):
        return env

    db_url = env.get("DB_URL") or app.config.get("SQLALCHEMY_DATABASE_URI")
    if not db_url:
        _log_warning(logger, "No database URL available for pg_dump; skipping database backup.")
        return None

    try:
        url = make_url(db_url)
    except Exception as exc:
        _log_warning(logger, f"Unable to parse DB_URL for pg_dump: {exc}")
        return None

    if url.host:
        env.setdefault("PGHOST", url.host)
    if url.port:
        env.setdefault("PGPORT", str(url.port))
    if url.username:
        env.setdefault("PGUSER", url.username)
    if url.password:
        env.setdefault("PGPASSWORD", url.password)
    if url.database:
        env.setdefault("PGDATABASE", url.database)

    if not env.get("PGDATABASE"):
        _log_warning(logger, "Database name missing for pg_dump; skipping database backup.")
        return None

    return env


def _run_pg_dump(app, output_path: Path, logger: logging.Logger) -> None:
    env = _build_pg_dump_env(app, logger)
    if env is None:
        raise RuntimeError("Database connection info not available for pg_dump.")

    command = [
        "pg_dump",
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(output_path),
    ]

    subprocess.run(command, check=True, env=env)


def _archive_directories(paths: Iterable[Path], archive_path: Path) -> bool:
    paths = [path for path in paths if path.exists() and path.is_dir()]
    if not paths:
        return False

    with tarfile.open(archive_path, "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=path.name)
    return True


def get_backup_frequency_hours(app, logger: logging.Logger | None = None) -> int:
    logger = logger or logging.getLogger("invapp.backup")
    default = DEFAULT_BACKUP_FREQUENCY_HOURS

    global _APP_SETTING_REPAIR_ATTEMPTED
    try:
        setting = AppSetting.get_or_create(BACKUP_SETTING_KEY, str(default))
    except ProgrammingError:
        db.session.rollback()
        if not _APP_SETTING_REPAIR_ATTEMPTED:
            _APP_SETTING_REPAIR_ATTEMPTED = True
            ensure_app_setting_schema(db.engine, logger)
            try:
                setting = AppSetting.get_or_create(BACKUP_SETTING_KEY, str(default))
            except SQLAlchemyError:
                _log_warning(
                    logger,
                    f"Unable to load backup frequency; using default ({default}h).",
                )
                return default
        else:
            _log_warning(
                logger,
                f"Unable to load backup frequency; using default ({default}h).",
            )
            return default
    except SQLAlchemyError as exc:
        _log_warning(logger, f"Unable to load backup frequency; using default ({default}h). Error: {exc}")
        db.session.rollback()
        return default

    raw_value = setting.value
    try:
        parsed = int(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        _log_warning(logger, f"Invalid backup frequency '{raw_value}'; using default ({default}h).")
        return default

    if parsed <= 0:
        _log_warning(logger, f"Backup frequency must be positive; using default ({default}h).")
        return default

    return parsed


def update_backup_frequency_hours(value: int) -> None:
    global _APP_SETTING_REPAIR_ATTEMPTED
    try:
        setting = AppSetting.query.filter_by(key=BACKUP_SETTING_KEY).first()
        if setting is None:
            setting = AppSetting(key=BACKUP_SETTING_KEY, value=str(value))
            db.session.add(setting)
        else:
            setting.value = str(value)
        db.session.commit()
    except ProgrammingError:
        db.session.rollback()
        if not _APP_SETTING_REPAIR_ATTEMPTED:
            _APP_SETTING_REPAIR_ATTEMPTED = True
            ensure_app_setting_schema(db.engine, logging.getLogger("invapp.backup"))
            update_backup_frequency_hours(value)
        else:
            raise


def run_backup_job(app) -> None:
    with app.app_context():
        try:
            backup_dir = get_backup_dir(app)
        except Exception as exc:
            app.config["BACKUPS_ENABLED"] = False
            app.logger.exception("Backups disabled due to error: %s", exc)
            _warn_once(
                app.logger,
                "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path.",
                key="backups_disabled",
            )
            status_bus.log_event(
                "error",
                "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path.",
                dedupe_key="backups_disabled",
                source="backup",
            )
            return
        logger = _backup_logger(backup_dir)
        if not app.config.get("BACKUPS_ENABLED", True):
            _log_warning(logger, "Backups are disabled; skipping automated backup run.")
            return

        db_dir = backup_dir / "db"
        files_dir = backup_dir / "files"
        timestamp = _timestamp_label()
        copy_number = _next_copy_number((db_dir, files_dir), timestamp)
        db_dump_path = db_dir / f"backup_{timestamp}_copy{copy_number}.sql"
        data_archive_path = files_dir / f"backup_{timestamp}_copy{copy_number}.tar.gz"

        _log_info(logger, f"Starting automated backup (copy {copy_number})")
        backup_record = _create_backup_run(status="started")

        try:
            _run_pg_dump(app, db_dump_path, logger)
            _log_info(logger, f"Database backup created: {db_dump_path}")

            data_dirs = _data_directories(app)
            if _archive_directories(data_dirs, data_archive_path):
                _log_info(logger, f"Data archive created: {data_archive_path}")
            else:
                _log_info(logger, "No data directories found for archival.")

            _log_info(logger, "Automated backup completed successfully.")
            _finalize_backup_run(
                backup_record,
                status="succeeded",
                filename=db_dump_path.name,
                filepath=str(db_dump_path),
                bytes=db_dump_path.stat().st_size if db_dump_path.exists() else None,
                message="Backup completed successfully.",
            )
        except Exception as exc:
            _log_exception(logger, "Automated backup failed.", exc)
            status_bus.log_event(
                "error",
                f"Automated backup failed: {exc}",
                dedupe_key="backup_run_failed",
                source="backup",
            )
            _finalize_backup_run(
                backup_record,
                status="failed",
                message=str(exc),
            )


def refresh_backup_schedule(app, *, force: bool = False) -> None:
    scheduler: BackgroundScheduler | None = app.extensions.get("backup_scheduler")
    if scheduler is None:
        return

    with app.app_context():
        try:
            backup_dir = get_backup_dir(app)
        except Exception as exc:
            app.config["BACKUPS_ENABLED"] = False
            app.logger.exception("Backups disabled due to error: %s", exc)
            _warn_once(
                app.logger,
                "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path.",
                key="backups_disabled",
            )
            status_bus.log_event(
                "error",
                "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path.",
                dedupe_key="backups_disabled",
                source="backup",
            )
            return
        logger = _backup_logger(backup_dir)
        frequency_hours = get_backup_frequency_hours(app, logger=logger)
        cached_frequency = app.config.get("BACKUP_FREQUENCY_HOURS")

        if not force and cached_frequency == frequency_hours:
            return

        app.config["BACKUP_FREQUENCY_HOURS"] = frequency_hours
        trigger = IntervalTrigger(hours=frequency_hours)
        job = scheduler.get_job(BACKUP_JOB_ID)

        if job:
            job.reschedule(trigger=trigger)
        else:
            scheduler.add_job(
                run_backup_job,
                trigger=trigger,
                id=BACKUP_JOB_ID,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                args=[app],
            )

        _log_info(logger, f"Backup schedule set to every {frequency_hours} hours.")


def initialize_backup_scheduler(app) -> None:
    if app.extensions.get("backup_scheduler") is not None:
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    app.extensions["backup_scheduler"] = scheduler

    try:
        backup_dir = get_backup_dir(app)
    except Exception as exc:
        app.config["BACKUPS_ENABLED"] = False
        app.logger.exception("Backups disabled due to error: %s", exc)
        _warn_once(
            app.logger,
            "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path.",
            key="backups_disabled",
        )
        status_bus.log_event(
            "error",
            "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path.",
            dedupe_key="backups_disabled",
            source="backup",
        )
        return

    app.config["BACKUPS_ENABLED"] = True
    _backup_logger(backup_dir)
    refresh_backup_schedule(app, force=True)
    scheduler.add_job(
        refresh_backup_schedule,
        trigger=IntervalTrigger(minutes=BACKUP_REFRESH_MINUTES),
        id=BACKUP_REFRESH_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        args=[app],
    )

    scheduler.start()


def list_backup_files(backup_dir: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for kind in ("db", "files"):
        folder = backup_dir / kind
        if not folder.exists():
            continue
        for item in folder.iterdir():
            if not item.is_file():
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            results.append(
                {
                    "filename": item.name,
                    "path": item,
                    "kind": kind,
                    "size": stat.st_size,
                    "created_at": datetime.utcfromtimestamp(stat.st_mtime),
                }
            )
    results.sort(key=lambda entry: entry["created_at"], reverse=True)
    return results


def is_valid_backup_filename(filename: str) -> bool:
    if not filename:
        return False
    if Path(filename).name != filename:
        return False
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return filename.endswith((".sql", ".dump"))


def list_auto_backup_files(app, logger: logging.Logger | None = None) -> list[dict[str, object]]:
    logger = logger or logging.getLogger("invapp.backup.auto")
    results: list[dict[str, object]] = []
    for folder in _auto_backup_dirs(app, logger=logger):
        if not folder.exists():
            continue
        try:
            entries = list(folder.iterdir())
        except OSError:
            logger.exception("Unable to read auto backup directory: %s", folder)
            continue
        for item in entries:
            if not item.is_file():
                continue
            if not _is_allowed_auto_backup_filename(item.name):
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            results.append(
                {
                    "filename": item.name,
                    "path": item,
                    "size_bytes": stat.st_size,
                    "size_hr": _format_bytes(float(stat.st_size)),
                    "created_at": datetime.utcfromtimestamp(stat.st_mtime),
                }
            )
    results.sort(key=lambda entry: entry["created_at"], reverse=True)
    return results


def is_valid_auto_backup_filename(filename: str) -> bool:
    if not filename:
        return False
    if Path(filename).name != filename:
        return False
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return _is_allowed_auto_backup_filename(filename)


def resolve_auto_backup_path(app, filename: str, logger: logging.Logger | None = None) -> Path | None:
    if not is_valid_auto_backup_filename(filename):
        return None
    for folder in _auto_backup_dirs(app, logger=logger):
        candidate = folder / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _auto_backup_dirs(app, logger: logging.Logger) -> list[Path]:
    config_dir = app.config.get("BACKUP_DIR_AUTO")
    if config_dir:
        return [Path(config_dir)]
    backup_root = get_backup_dir(app, logger=logger)
    return [backup_root / "db", backup_root / "files"]


def _is_allowed_auto_backup_filename(filename: str) -> bool:
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in AUTO_BACKUP_ALLOWED_EXTENSIONS)


def _format_bytes(value: float) -> str:
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if value < step or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} PB"


def _sanitize_restore_output(output: str, *, limit: int = 400) -> str:
    if not output:
        return ""
    sanitized = re.sub(r"password=[^\s'\"]+", "password=***", output, flags=re.IGNORECASE)
    sanitized = sanitized.replace("PGPASSWORD", "PGPASSWORD=***")
    sanitized = sanitized.strip()
    return sanitized[:limit]


def _database_identifier_from_env(env: dict[str, str]) -> str:
    name = env.get("PGDATABASE")
    if not name:
        raise RuntimeError("Database name missing for restore.")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RuntimeError("Database name contains unsupported characters.")
    return name


def _terminate_app_connections(env: dict[str, str], logger: logging.Logger) -> None:
    db_name = _database_identifier_from_env(env)
    sql = (
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        "WHERE datname = :db_name "
        "AND pid <> pg_backend_pid()"
    )
    try:
        with db.engine.begin() as connection:
            connection.execute(text(sql), {"db_name": db_name})
            logger.info("Terminated active connections for database %s.", db_name)
    except Exception as exc:
        logger.warning("Unable to terminate existing connections: %s", exc)


def _reset_public_schema(env: dict[str, str], logger: logging.Logger) -> None:
    db_name = _database_identifier_from_env(env)
    commands = [
        "DROP SCHEMA IF EXISTS public CASCADE;",
        "CREATE SCHEMA public;",
        "GRANT ALL ON SCHEMA public TO public;",
        "GRANT ALL ON SCHEMA public TO CURRENT_USER;",
    ]
    psql = ["psql", "-d", db_name, "-v", "ON_ERROR_STOP=1", "-c", "".join(commands)]
    result = subprocess.run(
        psql,
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    logger.info("Reset public schema before restore.")
    if result.stderr:
        logger.debug("Schema reset stderr: %s", _sanitize_restore_output(result.stderr))


def _run_psql_restore(env: dict[str, str], staged_backup: Path, timeout: int) -> subprocess.CompletedProcess:
    command = [
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        str(staged_backup),
    ]
    return subprocess.run(
        command,
        check=True,
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def restore_database_backup(app, filename: str, logger: logging.Logger) -> RestoreOutcome:
    if not is_valid_backup_filename(filename):
        raise ValueError("Invalid backup filename.")

    backup_dir = get_backup_dir(app, logger=logger)
    db_dir = backup_dir / "db"
    backup_path = db_dir / filename

    if not backup_path.exists():
        raise FileNotFoundError("Backup file not found.")

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    staging_dir = backup_dir / "tmp" / f"restore_{timestamp}"
    _ensure_directory(staging_dir)
    staged_backup = staging_dir / backup_path.name
    shutil.copy2(backup_path, staged_backup)

    env = _build_pg_dump_env(app, logger)
    if env is None:
        raise RuntimeError("Database connection info not available for restore.")

    timeout = app.config.get("BACKUP_RESTORE_TIMEOUT", RESTORE_TIMEOUT_SECONDS)
    start_time = datetime.utcnow()
    stdout = ""
    stderr = ""

    if staged_backup.suffix != ".sql":
        raise ValueError("Only .sql backups are supported for restore.")

    try:
        status_bus.log_event(
            "info",
            "Backup restore starting.",
            context={"filename": filename, "stage": "init"},
            source="backup_restore",
        )
        _terminate_app_connections(env, logger)

        status_bus.log_event(
            "info",
            "Clearing existing database schema.",
            context={"filename": filename, "stage": "schema_reset"},
            source="backup_restore",
        )
        _reset_public_schema(env, logger)

        status_bus.log_event(
            "info",
            "Applying backup via psql.",
            context={"filename": filename, "stage": "restore"},
            source="backup_restore",
        )
        result = _run_psql_restore(env, staged_backup, timeout)
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        status_bus.log_event(
            "info",
            "Backup restore completed.",
            context={"filename": filename, "stage": "complete"},
            source="backup_restore",
        )
    except subprocess.CalledProcessError as exc:
        stdout = _sanitize_restore_output(getattr(exc, "stdout", "") or "")
        stderr = _sanitize_restore_output(getattr(exc, "stderr", "") or str(exc))
        duration_seconds = (datetime.utcnow() - start_time).total_seconds()
        status_bus.log_event(
            "error",
            "Backup restore failed during command execution.",
            context={
                "filename": filename,
                "stage": "restore",
                "stderr": stderr,
            },
            source="backup_restore",
        )
        raise RestoreFailure(
            filename=filename,
            message="Restore failed during command execution.",
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
        ) from exc
    except Exception as exc:
        stderr = _sanitize_restore_output(str(exc))
        duration_seconds = (datetime.utcnow() - start_time).total_seconds()
        status_bus.log_event(
            "error",
            "Backup restore failed.",
            context={
                "filename": filename,
                "stage": "restore",
                "stderr": stderr,
            },
            source="backup_restore",
        )
        raise RestoreFailure(
            filename=filename,
            message="Restore failed.",
            stdout="",
            stderr=stderr,
            duration_seconds=duration_seconds,
        ) from exc

    duration_seconds = (datetime.utcnow() - start_time).total_seconds()
    message = f"Restore completed from {filename}."
    return RestoreOutcome(
        filename=filename,
        message=message,
        stdout=_sanitize_restore_output(stdout),
        stderr=_sanitize_restore_output(stderr),
        duration_seconds=duration_seconds,
    )


def _create_backup_run(status: str) -> BackupRun | None:
    try:
        record = BackupRun(status=status)
        db.session.add(record)
        db.session.commit()
        return record
    except SQLAlchemyError:
        db.session.rollback()
        return None


def _finalize_backup_run(
    record: BackupRun | None,
    *,
    status: str,
    filename: str | None = None,
    filepath: str | None = None,
    bytes: int | None = None,
    message: str | None = None,
) -> None:
    if record is None:
        return
    try:
        record.status = status
        record.filename = filename
        record.filepath = filepath
        record.bytes = bytes
        record.message = message
        record.finished_at = datetime.utcnow()
        db.session.add(record)
        db.session.commit()
        _write_backup_status_file(record)
    except SQLAlchemyError:
        db.session.rollback()


def _write_backup_status_file(record: BackupRun) -> None:
    try:
        path = BACKUP_STATUS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        started_at = record.started_at.isoformat() if record.started_at else None
        finished_at = record.finished_at.isoformat() if record.finished_at else None
        duration_sec = None
        if record.started_at and record.finished_at:
            duration_sec = (record.finished_at - record.started_at).total_seconds()
        payload = {
            "ok": record.status == "succeeded",
            "started_at": started_at,
            "finished_at": finished_at,
            "filename": record.filename,
            "size_bytes": record.bytes,
            "duration_sec": duration_sec,
            "error": record.message if record.status != "succeeded" else None,
        }
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        logging.getLogger("invapp.backup").exception(
            "Failed to write backup status file."
        )


def write_backup_status_file(record: BackupRun) -> None:
    _write_backup_status_file(record)
