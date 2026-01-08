"""Schema self-healing helpers for legacy or drifted databases."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


def ensure_app_setting_schema(engine: Engine, logger: logging.Logger) -> list[str]:
    """Ensure app_setting has created_at/updated_at columns."""

    added: list[str] = []
    try:
        inspector = inspect(engine)
        if not inspector.has_table("app_setting"):
            return added
        columns = {column["name"] for column in inspector.get_columns("app_setting")}
    except SQLAlchemyError as exc:
        logger.warning("Unable to inspect app_setting schema: %s", exc)
        return added

    if engine.dialect.name == "postgresql":
        column_type = "TIMESTAMPTZ"
        default_value = "NOW()"
    else:
        column_type = "TIMESTAMP"
        default_value = "CURRENT_TIMESTAMP"

    statements = []
    if "created_at" not in columns:
        statements.append(
            f"ALTER TABLE app_setting ADD COLUMN created_at {column_type} NOT NULL DEFAULT {default_value}"
        )
        added.append("created_at")
    if "updated_at" not in columns:
        statements.append(
            f"ALTER TABLE app_setting ADD COLUMN updated_at {column_type} NOT NULL DEFAULT {default_value}"
        )
        added.append("updated_at")

    if not statements:
        return added

    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    except SQLAlchemyError as exc:
        logger.warning("Unable to repair app_setting schema: %s", exc)
        return []

    if added:
        logger.info("Added missing columns to app_setting: %s", ", ".join(added))
    return added
