"""On-demand database backup exporter."""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, insert
from sqlalchemy.schema import CreateIndex, CreateTable

from invapp.extensions import db
from invapp.models import BackupRun
from invapp.services import backup_service, status_bus


EXPORT_SOURCE = "backup_export"


def create_database_backup_archive(app, logger: logging.Logger | None = None) -> tuple[Path, Path]:
    """Create a zipped SQL backup of every database table.

    Returns the path to the archive and the staging directory containing it.
    """

    logger = logger or logging.getLogger("invapp.backup.export")
    backup_dir = backup_service.get_backup_dir(app, logger=logger)
    staging_dir = Path(tempfile.mkdtemp(prefix="manual_backup_", dir=backup_dir / "tmp"))

    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    archive_name = f"hyperion_backup_{timestamp}.zip"
    archive_path = staging_dir / archive_name

    status_bus.log_event("info", "Backup started.", source=EXPORT_SOURCE)

    metadata = MetaData()
    metadata.reflect(bind=db.engine)
    tables = list(metadata.sorted_tables)
    table_count = len(tables)

    status_bus.log_event(
        "info",
        f"Tables discovered ({table_count}).",
        context={"table_count": table_count},
        source=EXPORT_SOURCE,
    )

    if table_count == 0:
        _cleanup_staging_dir(staging_dir)
        _raise_and_log(logger, "No tables discovered for backup.")

    table_files: list[str] = []

    try:
        with db.engine.connect() as connection, zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for index, table in enumerate(tables, start=1):
                status_bus.log_event(
                    "info",
                    f"Backing up table {table.name} ({index}/{table_count}).",
                    context={"table": table.name, "index": index, "total": table_count},
                    source=EXPORT_SOURCE,
                )
                contents = _render_table_sql(connection, table)
                filename = _table_export_filename(table)
                archive.writestr(filename, contents)
                table_files.append(filename)

        if len(table_files) != table_count:
            _raise_and_log(
                logger,
                "Backup verification failed: table count mismatch.",
                context={"table_count": table_count, "file_count": len(table_files)},
            )

        status_bus.log_event(
            "info",
            "Backup verification succeeded.",
            context={"table_count": table_count, "file_count": len(table_files)},
            source=EXPORT_SOURCE,
        )

        _record_backup_run(
            status="succeeded",
            filename=archive_name,
            filepath=str(archive_path),
            bytes=archive_path.stat().st_size,
            message="Manual backup export completed.",
        )

        status_bus.log_event(
            "info",
            f"Backup completed successfully: {archive_name}.",
            context={"filename": archive_name},
            source=EXPORT_SOURCE,
        )
    except Exception as exc:
        _record_backup_run(status="failed", message=str(exc))
        status_bus.log_event(
            "error",
            f"Backup failed: {exc}",
            source=EXPORT_SOURCE,
        )
        _cleanup_staging_dir(staging_dir)
        raise

    return archive_path, staging_dir


def _render_table_sql(connection, table) -> str:
    dialect = connection.dialect
    lines: list[str] = [f"-- Table: {table.fullname}"]
    lines.append(f"{CreateTable(table).compile(dialect=dialect)};")

    for index in sorted(table.indexes, key=lambda idx: idx.name or ""):
        lines.append(f"{CreateIndex(index).compile(dialect=dialect)};")

    result = connection.execute(table.select()).mappings()
    for row in result:
        statement = insert(table).values(**row)
        compiled = statement.compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": True},
        )
        lines.append(f"{compiled};")

    return "\n".join(lines) + "\n"


def _table_export_filename(table) -> str:
    if table.schema:
        return f"{table.schema}__{table.name}.sql"
    return f"{table.name}.sql"


def _record_backup_run(
    *,
    status: str,
    filename: str | None = None,
    filepath: str | None = None,
    bytes: int | None = None,
    message: str | None = None,
) -> None:
    try:
        record = BackupRun(
            status=status,
            filename=filename,
            filepath=filepath,
            bytes=bytes,
            message=message,
            finished_at=datetime.utcnow(),
        )
        db.session.add(record)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _cleanup_staging_dir(staging_dir: Path) -> None:
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
    except Exception:
        pass


def _raise_and_log(logger: logging.Logger, message: str, context: dict | None = None) -> None:
    logger.error(message)
    status_bus.log_event("error", message, context=context, source=EXPORT_SOURCE)
    raise RuntimeError(message)


def cleanup_backup_artifacts(archive_path: Path, staging_dir: Path | None = None) -> None:
    """Remove the temporary backup archive and its staging directory."""

    if staging_dir is None:
        staging_dir = archive_path.parent
    _cleanup_staging_dir(staging_dir)
