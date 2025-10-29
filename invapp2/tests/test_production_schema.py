import os
import sys

from sqlalchemy import create_engine, inspect, text

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import _ensure_production_schema


LEGACY_SCHEMA_SQL = """
CREATE TABLE production_daily_record (
    id INTEGER PRIMARY KEY,
    entry_date DATE NOT NULL,
    day_of_week VARCHAR(16) NOT NULL,
    gates_employees INTEGER NOT NULL DEFAULT 0,
    gates_hours_ot NUMERIC(7, 2) NOT NULL DEFAULT 0,
    controllers_4_stop INTEGER NOT NULL DEFAULT 0,
    controllers_6_stop INTEGER NOT NULL DEFAULT 0,
    door_locks_lh INTEGER NOT NULL DEFAULT 0,
    door_locks_rh INTEGER NOT NULL DEFAULT 0,
    operators_produced INTEGER NOT NULL DEFAULT 0,
    cops_produced INTEGER NOT NULL DEFAULT 0,
    additional_employees INTEGER NOT NULL DEFAULT 0,
    additional_hours_ot NUMERIC(7, 2) NOT NULL DEFAULT 0,
    daily_notes TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
"""


NOTES_COLUMNS = {
    "gates_notes",
    "gates_summary",
    "additional_notes",
    "additional_summary",
}


def test_ensure_production_schema_adds_note_columns(tmp_path):
    database_path = tmp_path / "legacy_production.db"
    engine = create_engine(f"sqlite:///{database_path}")

    with engine.begin() as connection:
        connection.execute(text(LEGACY_SCHEMA_SQL))

    _ensure_production_schema(engine)

    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("production_daily_record")}

    assert NOTES_COLUMNS.issubset(column_names)
