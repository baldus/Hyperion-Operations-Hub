from __future__ import annotations

from sqlalchemy import inspect


OPEN_ORDERS_TABLES: dict[str, tuple[str, ...]] = {
    "open_order_line": (
        "status",
        "completed_at",
        "completed_by_user_id",
        "order_id",
        "last_seen_upload_id",
    ),
    "open_order_line_snapshot": (
        "created_at",
    ),
    "open_order_upload": (
        "uploaded_at",
        "source_filename",
    ),
    "open_order": (
        "so_no",
    ),
}


def audit_open_orders_schema(engine) -> dict[str, dict[str, list[str]]]:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing: dict[str, list[str]] = {}

    for table_name, expected_columns in OPEN_ORDERS_TABLES.items():
        if table_name not in tables:
            missing[table_name] = list(expected_columns)
            continue
        existing = {col.get("name") for col in inspector.get_columns(table_name)}
        missing_columns = [column for column in expected_columns if column not in existing]
        if missing_columns:
            missing[table_name] = missing_columns

    return {"missing": missing}
