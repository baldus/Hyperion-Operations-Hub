import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import Integer, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, NoSuchTableError
from sqlalchemy.orm import DeclarativeMeta


@dataclass
class SequenceCheck:
    table: str
    pk_column: str
    sequence_name: Optional[str]
    desired_next_value: int
    current_next_value: Optional[int]
    status: str
    error: Optional[str] = None


def _iter_models(base_model: DeclarativeMeta) -> Iterable[type]:
    """Yield concrete declarative models registered on the provided base."""

    registry = getattr(base_model, "registry", None)
    if not registry:
        return

    for mapper in registry.mappers:
        model = mapper.class_
        if getattr(model, "__tablename__", None):
            yield model


def _split_schema_and_name(sequence_name: str) -> tuple[Optional[str], str]:
    if "." in sequence_name:
        schema, name = sequence_name.split(".", 1)
    else:
        schema, name = None, sequence_name
    return (schema.strip("\"") if schema else None, name.strip("\""))


def _desired_next_value(connection, table, pk_column) -> int:
    query = select(func.coalesce(func.max(pk_column) + 1, 1)).select_from(table)
    return int(connection.execute(query).scalar_one())


def _pg_sequence_next_value(connection, sequence_name: str) -> Optional[int]:
    schema, name = _split_schema_and_name(sequence_name)
    params = {"seqname": name}
    query = (
        "SELECT last_value, is_called, increment_by "
        "FROM pg_sequences WHERE sequencename = :seqname"
    )
    if schema:
        query += " AND schemaname = :schemaname"
        params["schemaname"] = schema

    row = connection.execute(text(query), params).fetchone()
    if row is None:
        return None

    last_value, is_called, increment_by = row
    if is_called:
        return int(last_value + increment_by)
    return int(last_value)


def _repair_postgres_sequence(connection, table, pk_column, logger, dry_run: bool):
    pk_column_name = pk_column.name
    sequence_name = connection.execute(
        text("SELECT pg_get_serial_sequence(:table_name, :pk_column)"),
        {"table_name": table.fullname, "pk_column": pk_column_name},
    ).scalar_one_or_none()
    if not sequence_name:
        logger.debug(
            "Skipping sequence repair for %s: no backing sequence discovered", table.fullname
        )
        return False, None, None

    desired_next = _desired_next_value(connection, table, pk_column)
    current_next = _pg_sequence_next_value(connection, sequence_name)

    if not dry_run:
        connection.execute(
            text("SELECT setval(:sequence_name, GREATEST(:next_value, 1), false)"),
            {"sequence_name": sequence_name, "next_value": desired_next},
        )

    logger.info(
        "Repaired primary key sequence for %s using %s (was %s, set to %s)",
        table.fullname,
        sequence_name,
        current_next,
        desired_next,
    )
    return True, sequence_name, desired_next


def _ensure_sqlite_sequence(connection, table, pk_column, logger, dry_run: bool):
    desired_next = _desired_next_value(connection, table, pk_column)
    stored_value = max(desired_next - 1, 0)
    current_row = connection.execute(
        text("SELECT seq FROM sqlite_sequence WHERE name = :table_name"),
        {"table_name": table.name},
    ).fetchone()
    current_next = (int(current_row[0]) + 1) if current_row else None
    if dry_run:
        logger.info(
            "(dry-run) SQLite sequence for %s would be set to %s",
            table.fullname,
            desired_next,
        )
        return True, None, desired_next

    result = connection.execute(
        text(
            "UPDATE sqlite_sequence SET seq = :desired WHERE name = :table_name"
        ),
        {"desired": stored_value, "table_name": table.name},
    )
    if result.rowcount == 0:
        connection.execute(
            text("INSERT INTO sqlite_sequence(name, seq) VALUES (:table_name, :desired)"),
            {"desired": stored_value, "table_name": table.name},
        )

    logger.info(
        "Repaired SQLite autoincrement value for %s (was %s, set to %s)",
        table.fullname,
        current_next,
        desired_next,
    )
    return True, None, desired_next


def repair_primary_key_sequences(
    engine: Engine,
    base_model: DeclarativeMeta,
    logger: Optional[logging.Logger] = None,
    models: Optional[Iterable[type]] = None,
    dry_run: bool = False,
) -> dict:
    """Repair primary key sequences for all integer PK models."""

    logger = logger or logging.getLogger(__name__)
    repaired = 0
    skipped = 0
    failed = 0
    summaries: list[SequenceCheck] = []

    inspector = inspect(engine)
    target_models = list(models or _iter_models(base_model))

    with engine.begin() as connection:
        for model in target_models:
            table = getattr(model, "__table__", None)
            if table is None:
                skipped += 1
                continue

            pk_columns = list(table.primary_key.columns)
            if len(pk_columns) != 1:
                skipped += 1
                continue

            pk_column = pk_columns[0]
            if not isinstance(pk_column.type, Integer):
                skipped += 1
                continue

            try:
                inspector.get_columns(table.name, schema=table.schema)
            except (NoSuchTableError, SQLAlchemyError):
                skipped += 1
                continue

            try:
                if engine.dialect.name == "postgresql":
                    fixed, sequence_name, desired_next = _repair_postgres_sequence(
                        connection, table, pk_column, logger, dry_run
                    )
                elif engine.dialect.name == "sqlite":
                    fixed, sequence_name, desired_next = _ensure_sqlite_sequence(
                        connection, table, pk_column, logger, dry_run
                    )
                else:
                    skipped += 1
                    continue
            except SQLAlchemyError as exc:
                failed += 1
                summaries.append(
                    SequenceCheck(
                        table=table.fullname,
                        pk_column=pk_column.name,
                        sequence_name=None,
                        desired_next_value=0,
                        current_next_value=None,
                        status="failed",
                        error=str(exc),
                    )
                )
                logger.warning(
                    "Failed to repair primary key sequence for %s: %s",
                    table.fullname,
                    exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                continue

            if fixed:
                repaired += 1
                summaries.append(
                    SequenceCheck(
                        table=table.fullname,
                        pk_column=pk_column.name,
                        sequence_name=sequence_name,
                        desired_next_value=int(desired_next or 0),
                        current_next_value=None,
                        status="repaired" if not dry_run else "dry-run",
                    )
                )
            else:
                skipped += 1

    return {"repaired": repaired, "skipped": skipped, "failed": failed, "details": summaries}


def analyze_primary_key_sequences(
    engine: Engine,
    base_model: DeclarativeMeta,
    logger: Optional[logging.Logger] = None,
    models: Optional[Iterable[type]] = None,
) -> list[SequenceCheck]:
    """Inspect sequences and report whether they are aligned with table PKs."""

    logger = logger or logging.getLogger(__name__)
    inspector = inspect(engine)
    results: list[SequenceCheck] = []

    target_models = list(models or _iter_models(base_model))

    with engine.connect() as connection:
        for model in target_models:
            table = getattr(model, "__table__", None)
            if table is None:
                continue

            pk_columns = list(table.primary_key.columns)
            if len(pk_columns) != 1:
                continue

            pk_column = pk_columns[0]
            if not isinstance(pk_column.type, Integer):
                continue

            try:
                inspector.get_columns(table.name, schema=table.schema)
            except (NoSuchTableError, SQLAlchemyError):
                logger.debug("Skipping %s because the table does not exist", table.fullname)
                continue

            desired_next = _desired_next_value(connection, table, pk_column)

            if engine.dialect.name == "postgresql":
                try:
                    sequence_name = connection.execute(
                        text("SELECT pg_get_serial_sequence(:table_name, :pk_column)"),
                        {"table_name": table.fullname, "pk_column": pk_column.name},
                    ).scalar_one_or_none()
                    if not sequence_name:
                        results.append(
                            SequenceCheck(
                                table=table.fullname,
                                pk_column=pk_column.name,
                                sequence_name=None,
                                desired_next_value=desired_next,
                                current_next_value=None,
                                status="skipped",
                                error="No sequence",
                            )
                        )
                        continue

                    current_next = _pg_sequence_next_value(connection, sequence_name)
                except SQLAlchemyError as exc:
                    results.append(
                        SequenceCheck(
                            table=table.fullname,
                            pk_column=pk_column.name,
                            sequence_name=None,
                            desired_next_value=desired_next,
                            current_next_value=None,
                            status="failed",
                            error=str(exc),
                        )
                    )
                    continue

                status = "ok" if current_next is not None and current_next >= desired_next else "mismatch"
                results.append(
                    SequenceCheck(
                        table=table.fullname,
                        pk_column=pk_column.name,
                        sequence_name=sequence_name,
                        desired_next_value=desired_next,
                        current_next_value=current_next,
                        status=status,
                        error=None,
                    )
                )
            elif engine.dialect.name == "sqlite":
                current_row = connection.execute(
                    text(
                        "SELECT seq FROM sqlite_sequence WHERE name = :table_name"
                    ),
                    {"table_name": table.name},
                ).fetchone()
                current_next = (int(current_row[0]) + 1) if current_row else None
                status = "ok" if current_next is not None and current_next >= desired_next else "mismatch"
                results.append(
                    SequenceCheck(
                        table=table.fullname,
                        pk_column=pk_column.name,
                        sequence_name=None,
                        desired_next_value=desired_next,
                        current_next_value=current_next,
                        status=status,
                    )
                )

    return results
