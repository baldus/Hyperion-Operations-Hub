"""Automated backup service for database and critical data directories."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError

from flask import current_app

from invapp.extensions import db
from invapp.models import AppSetting


BACKUP_SETTING_KEY = "backup_frequency_hours"
DEFAULT_BACKUP_FREQUENCY_HOURS = 4
BACKUP_JOB_ID = "automated-backup"
BACKUP_REFRESH_JOB_ID = "automated-backup-refresh"
BACKUP_REFRESH_MINUTES = 5
BACKUP_SUBDIRS = ("db", "files", "tmp")
RESTORE_TIMEOUT_SECONDS = 900


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
            _log_warning(
                logger,
                f"Backup directory '{candidate}' from {source} is not writable; "
                "falling back to the next option. Set BACKUP_DIR to a writable path.",
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

    try:
        setting = AppSetting.get_or_create(BACKUP_SETTING_KEY, str(default))
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
    setting = AppSetting.query.filter_by(key=BACKUP_SETTING_KEY).first()
    if setting is None:
        setting = AppSetting(key=BACKUP_SETTING_KEY, value=str(value))
        db.session.add(setting)
    else:
        setting.value = str(value)
    db.session.commit()


def run_backup_job(app) -> None:
    with app.app_context():
        backup_dir = get_backup_dir(app)
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

        try:
            _run_pg_dump(app, db_dump_path, logger)
            _log_info(logger, f"Database backup created: {db_dump_path}")

            data_dirs = _data_directories(app)
            if _archive_directories(data_dirs, data_archive_path):
                _log_info(logger, f"Data archive created: {data_archive_path}")
            else:
                _log_info(logger, "No data directories found for archival.")

            _log_info(logger, "Automated backup completed successfully.")
        except Exception as exc:
            _log_exception(logger, "Automated backup failed.", exc)


def refresh_backup_schedule(app, *, force: bool = False) -> None:
    scheduler: BackgroundScheduler | None = app.extensions.get("backup_scheduler")
    if scheduler is None:
        return

    with app.app_context():
        try:
            backup_dir = get_backup_dir(app)
        except Exception as exc:
            app.config["BACKUPS_ENABLED"] = False
            current_app.logger.exception("Backups disabled due to error: %s", exc)
            current_app.logger.warning(
                "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path."
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
        app.logger.warning(
            "Backups disabled due to error; app will continue. Set BACKUP_DIR to a writable path."
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


def restore_database_backup(app, filename: str, logger: logging.Logger) -> str:
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

    if staged_backup.suffix == ".sql":
        command = ["psql", "-f", str(staged_backup)]
    else:
        command = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-d",
            env.get("PGDATABASE", ""),
            str(staged_backup),
        ]

    subprocess.run(command, check=True, env=env, timeout=timeout)
    return f"Restore completed from {filename}."
