from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, TYPE_CHECKING

from flask import current_app

from invapp.extensions import db
from invapp.models import (
    OpenOrderInternalStatus,
    OpenOrderLine,
    OpenOrderLineSnapshot,
    OpenOrderSystemState,
    OpenOrderUpload,
    User,
)

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

COLUMN_ALIASES = {
    "so_no": {"so no", "so #", "so#", "sales order"},
    "so_state": {"so state", "state"},
    "so_date": {"so date", "order date"},
    "ship_by": {"ship by", "ship by date", "ship date"},
    "customer_id": {"customer id", "customer #", "cust id"},
    "customer_name": {"customer name", "customer"},
    "item_id": {"item id", "item", "item #"},
    "line_description": {"line description", "description"},
    "uom": {"u/m id", "uom", "u/m", "unit"},
    "qty_ordered": {"qty ordered", "quantity ordered", "ordered"},
    "qty_shipped": {"qty shipped", "quantity shipped", "shipped"},
    "qty_remaining": {"qty remaining", "quantity remaining", "remaining"},
    "unit_price": {"unit price", "price"},
    "part_number": {"part number", "part #", "part"},
}

REQUIRED_COLUMNS = {
    "so_no",
    "so_state",
    "so_date",
    "ship_by",
    "customer_id",
    "customer_name",
    "item_id",
    "line_description",
    "uom",
    "qty_ordered",
    "qty_shipped",
    "qty_remaining",
    "unit_price",
    "part_number",
}

CHANGE_FIELDS = (
    "so_state",
    "ship_by",
    "qty_ordered",
    "qty_shipped",
    "qty_remaining",
    "unit_price",
)


@dataclass
class OpenOrderDiff:
    previous_upload: OpenOrderUpload | None
    current_rows: list[dict]
    new_keys: set[str]
    completed_keys: set[str]
    still_open_keys: set[str]
    changed_keys: set[str]
    changed_details: dict[str, list[str]]
    previous_open_lines: dict[str, OpenOrderLine]


@dataclass
class OpenOrderImportResult:
    upload: OpenOrderUpload
    diff: OpenOrderDiff


class OpenOrderImportError(RuntimeError):
    pass


def _normalize_column_name(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise OpenOrderImportError(
            "pandas is required to import Excel files. Please install pandas and openpyxl."
        ) from exc
    return pd


def _is_nan(value) -> bool:
    try:  # pragma: no cover - best effort for pandas NaN detection
        pd = _pandas()
    except OpenOrderImportError:
        return False
    return pd.isna(value)


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    normalized_columns = {_normalize_column_name(col): col for col in df.columns}
    resolved: dict[str, str] = {}

    for target, aliases in COLUMN_ALIASES.items():
        if target in resolved:
            continue
        for alias in aliases:
            normalized_alias = _normalize_column_name(alias)
            if normalized_alias in normalized_columns:
                resolved[target] = normalized_columns[normalized_alias]
                break

    missing = REQUIRED_COLUMNS - set(resolved.keys())
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise OpenOrderImportError(
            f"Missing required columns: {missing_list}."
        )

    return resolved


def _parse_decimal(value) -> Decimal | None:
    if value is None or (isinstance(value, float) and _is_nan(value)):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)).quantize(Decimal("0.001"))
    except Exception:
        return None


def _parse_currency(value) -> Decimal | None:
    if value is None or (isinstance(value, float) and _is_nan(value)):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


def _parse_date(value) -> datetime.date | None:
    if value is None or (isinstance(value, float) and _is_nan(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    pd = _pandas()
    parsed = pd.to_datetime(value, errors="coerce")
    if _is_nan(parsed):
        return None
    return parsed.date()


def _normalize_string(value) -> str:
    if value is None or (isinstance(value, float) and _is_nan(value)):
        return ""
    return str(value).strip()


def _natural_key_for(row: dict) -> str:
    components = [
        _normalize_string(row.get("so_no")).upper(),
        _normalize_string(row.get("item_id")).upper(),
        _normalize_string(row.get("line_description")).upper(),
        _normalize_string(row.get("customer_id")).upper(),
    ]
    payload = "|".join(components)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def parse_open_orders_excel(file_stream: io.BytesIO) -> list[dict]:
    pd = _pandas()
    try:
        df = pd.read_excel(file_stream, engine="openpyxl")
    except Exception as exc:
        raise OpenOrderImportError(
            "Unable to read the Excel file. Ensure it is a valid .xlsx file."
        ) from exc

    if df.empty:
        raise OpenOrderImportError("The uploaded Excel file is empty.")

    column_map = _resolve_columns(df)
    df = df.rename(columns=column_map)

    def normalize_row(row):
        so_state = _normalize_string(row.get("so_state"))
        return {
            "so_no": _normalize_string(row.get("so_no")),
            "so_state": so_state,
            "so_date": _parse_date(row.get("so_date")),
            "ship_by": _parse_date(row.get("ship_by")),
            "customer_id": _normalize_string(row.get("customer_id")),
            "customer_name": _normalize_string(row.get("customer_name")),
            "item_id": _normalize_string(row.get("item_id")),
            "line_description": _normalize_string(row.get("line_description")),
            "uom": _normalize_string(row.get("uom")),
            "qty_ordered": _parse_decimal(row.get("qty_ordered")),
            "qty_shipped": _parse_decimal(row.get("qty_shipped")),
            "qty_remaining": _parse_decimal(row.get("qty_remaining")),
            "unit_price": _parse_currency(row.get("unit_price")),
            "part_number": _normalize_string(row.get("part_number")),
        }

    rows = [normalize_row(record) for record in df.to_dict(orient="records")]

    filtered_rows = []
    for row in rows:
        qty_remaining = row.get("qty_remaining") or Decimal("0")
        so_state = (row.get("so_state") or "").strip().lower()
        if qty_remaining > 0 or so_state == "open":
            row["natural_key"] = _natural_key_for(row)
            filtered_rows.append(row)

    if not filtered_rows:
        raise OpenOrderImportError(
            "No open orders were found in the uploaded file."
        )

    return filtered_rows


def build_open_orders_diff(current_rows: Iterable[dict]) -> OpenOrderDiff:
    previous_upload = (
        OpenOrderUpload.query.order_by(OpenOrderUpload.uploaded_at.desc()).first()
    )

    previous_open_lines: dict[str, OpenOrderLine] = {}
    if previous_upload is not None:
        previous_open_lines = {
            line.natural_key: line
            for line in OpenOrderLine.query.filter(
                OpenOrderLine.last_seen_upload_id == previous_upload.id,
                OpenOrderLine.system_state != OpenOrderSystemState.COMPLETED,
            )
        }

    current_rows_list = list(current_rows)
    current_by_key = {row["natural_key"]: row for row in current_rows_list}
    current_keys = set(current_by_key.keys())
    previous_keys = set(previous_open_lines.keys())

    new_keys = current_keys - previous_keys
    completed_keys = previous_keys - current_keys
    still_open_keys = current_keys & previous_keys

    changed_keys: set[str] = set()
    changed_details: dict[str, list[str]] = {}
    for key in still_open_keys:
        previous_line = previous_open_lines[key]
        current_row = current_by_key[key]
        changed_fields: list[str] = []
        for field in CHANGE_FIELDS:
            previous_value = getattr(previous_line, field)
            current_value = current_row.get(field)
            if isinstance(previous_value, Decimal) and isinstance(
                current_value, Decimal
            ):
                if previous_value != current_value:
                    changed_fields.append(field)
            elif previous_value != current_value:
                changed_fields.append(field)
        if changed_fields:
            changed_keys.add(key)
            changed_details[key] = changed_fields

    return OpenOrderDiff(
        previous_upload=previous_upload,
        current_rows=current_rows_list,
        new_keys=new_keys,
        completed_keys=completed_keys,
        still_open_keys=still_open_keys,
        changed_keys=changed_keys,
        changed_details=changed_details,
        previous_open_lines=previous_open_lines,
    )


def _line_snapshot_payload(row: dict) -> dict:
    return {
        "so_no": row.get("so_no"),
        "so_state": row.get("so_state"),
        "so_date": row.get("so_date").isoformat() if row.get("so_date") else None,
        "ship_by": row.get("ship_by").isoformat() if row.get("ship_by") else None,
        "customer_id": row.get("customer_id"),
        "customer_name": row.get("customer_name"),
        "item_id": row.get("item_id"),
        "line_description": row.get("line_description"),
        "uom": row.get("uom"),
        "qty_ordered": str(row.get("qty_ordered"))
        if row.get("qty_ordered") is not None
        else None,
        "qty_shipped": str(row.get("qty_shipped"))
        if row.get("qty_shipped") is not None
        else None,
        "qty_remaining": str(row.get("qty_remaining"))
        if row.get("qty_remaining") is not None
        else None,
        "unit_price": str(row.get("unit_price"))
        if row.get("unit_price") is not None
        else None,
        "part_number": row.get("part_number"),
    }


def _apply_import(
    rows: list[dict],
    filename: str,
    user: User,
    file_hash: str | None = None,
) -> OpenOrderImportResult:
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    diff = build_open_orders_diff(rows)

    upload = OpenOrderUpload(
        uploaded_by_user_id=user.id if user else None,
        source_filename=filename,
        file_hash=file_hash,
    )
    db.session.add(upload)
    db.session.flush()

    current_by_key = {row["natural_key"]: row for row in diff.current_rows}

    now = datetime.utcnow()

    for key in diff.still_open_keys:
        row = current_by_key[key]
        line = diff.previous_open_lines[key]
        line.so_no = row["so_no"]
        line.so_state = row.get("so_state")
        line.so_date = row.get("so_date")
        line.ship_by = row.get("ship_by")
        line.customer_id = row.get("customer_id")
        line.customer_name = row.get("customer_name")
        line.item_id = row.get("item_id")
        line.line_description = row.get("line_description")
        line.uom = row.get("uom")
        line.qty_ordered = row.get("qty_ordered")
        line.qty_shipped = row.get("qty_shipped")
        line.qty_remaining = row.get("qty_remaining")
        line.unit_price = row.get("unit_price")
        line.part_number = row.get("part_number")
        line.last_seen_upload_id = upload.id
        line.last_seen_at = now
        if line.system_state in {
            OpenOrderSystemState.NEW,
            OpenOrderSystemState.REOPENED,
        }:
            line.system_state = OpenOrderSystemState.OPEN

    for key in diff.new_keys:
        row = current_by_key[key]
        existing = OpenOrderLine.query.filter_by(natural_key=key).first()
        if existing:
            if existing.system_state == OpenOrderSystemState.COMPLETED:
                existing.system_state = OpenOrderSystemState.REOPENED
                existing.completed_at = None
                existing.completed_upload_id = None
            else:
                existing.system_state = OpenOrderSystemState.OPEN
            existing.last_seen_upload_id = upload.id
            existing.last_seen_at = now
            existing.so_no = row["so_no"]
            existing.so_state = row.get("so_state")
            existing.so_date = row.get("so_date")
            existing.ship_by = row.get("ship_by")
            existing.customer_id = row.get("customer_id")
            existing.customer_name = row.get("customer_name")
            existing.item_id = row.get("item_id")
            existing.line_description = row.get("line_description")
            existing.uom = row.get("uom")
            existing.qty_ordered = row.get("qty_ordered")
            existing.qty_shipped = row.get("qty_shipped")
            existing.qty_remaining = row.get("qty_remaining")
            existing.unit_price = row.get("unit_price")
            existing.part_number = row.get("part_number")
            if existing.first_seen_upload_id is None:
                existing.first_seen_upload_id = upload.id
                existing.first_seen_at = now
        else:
            line = OpenOrderLine(
                natural_key=key,
                so_no=row["so_no"],
                so_state=row.get("so_state"),
                so_date=row.get("so_date"),
                ship_by=row.get("ship_by"),
                customer_id=row.get("customer_id"),
                customer_name=row.get("customer_name"),
                item_id=row.get("item_id"),
                line_description=row.get("line_description"),
                uom=row.get("uom"),
                qty_ordered=row.get("qty_ordered"),
                qty_shipped=row.get("qty_shipped"),
                qty_remaining=row.get("qty_remaining"),
                unit_price=row.get("unit_price"),
                part_number=row.get("part_number"),
                system_state=OpenOrderSystemState.NEW,
                internal_status=OpenOrderInternalStatus.UNREVIEWED,
                first_seen_upload_id=upload.id,
                last_seen_upload_id=upload.id,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.session.add(line)

    for key in diff.completed_keys:
        line = diff.previous_open_lines[key]
        line.system_state = OpenOrderSystemState.COMPLETED
        line.completed_at = now
        line.completed_upload_id = upload.id

    db.session.flush()

    for row in diff.current_rows:
        line = OpenOrderLine.query.filter_by(natural_key=row["natural_key"]).first()
        if not line:
            continue
        snapshot = OpenOrderLineSnapshot(
            upload_id=upload.id,
            line_id=line.id,
            snapshot_json=_line_snapshot_payload(row),
        )
        db.session.add(snapshot)

    db.session.commit()

    logger.info(
        "Open order import completed: %s new, %s still open, %s completed.",
        len(diff.new_keys),
        len(diff.still_open_keys),
        len(diff.completed_keys),
    )

    return OpenOrderImportResult(upload=upload, diff=diff)


def import_open_orders(
    file_stream: io.BytesIO, filename: str, user: User
) -> OpenOrderImportResult:
    file_bytes = file_stream.getvalue()
    file_hash = hashlib.sha1(file_bytes).hexdigest()
    rows = parse_open_orders_excel(io.BytesIO(file_bytes))
    return _apply_import(rows, filename, user, file_hash=file_hash)


def import_open_orders_rows(
    rows: list[dict], filename: str, user: User
) -> OpenOrderImportResult:
    return _apply_import(rows, filename, user)


def summarize_diff(diff: OpenOrderDiff) -> dict:
    current_by_key = {row["natural_key"]: row for row in diff.current_rows}
    new_rows = [current_by_key[key] for key in diff.new_keys]
    still_open_rows = [current_by_key[key] for key in diff.still_open_keys]
    changed_rows = [
        {
            **current_by_key[key],
            "changed_fields": diff.changed_details.get(key, []),
        }
        for key in diff.changed_keys
    ]
    completed_rows = [
        {
            "so_no": line.so_no,
            "customer_name": line.customer_name,
            "item_id": line.item_id,
            "line_description": line.line_description,
            "qty_remaining": line.qty_remaining,
            "ship_by": line.ship_by,
            "natural_key": line.natural_key,
        }
        for line in diff.previous_open_lines.values()
        if line.natural_key in diff.completed_keys
    ]

    return {
        "previous_upload": diff.previous_upload,
        "counts": {
            "new": len(diff.new_keys),
            "still_open": len(diff.still_open_keys),
            "completed": len(diff.completed_keys),
            "changed": len(diff.changed_keys),
        },
        "new_rows": new_rows,
        "still_open_rows": still_open_rows,
        "completed_rows": completed_rows,
        "changed_rows": changed_rows,
    }
