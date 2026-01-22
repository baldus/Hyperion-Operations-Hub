"""Services for physical inventory workflows."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Iterable

from sqlalchemy import func

from invapp.extensions import db
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    Movement,
)

REQUIRED_SNAPSHOT_HEADERS = ("item_code", "system_total_qty")
OPTIONAL_SNAPSHOT_HEADERS = ("uom", "description", "notes")
SNAPSHOT_HEADER_ALIASES = {
    "item_code": ("item_code", "item", "item sku", "sku", "part", "part_number"),
    "system_total_qty": (
        "system_total_qty",
        "system_total",
        "system_qty",
        "total_qty",
        "total",
        "qty",
        "quantity",
        "erp_qty",
        "erp_total",
    ),
    "uom": ("uom", "unit", "unit_of_measure"),
    "description": ("description", "item_description", "desc"),
    "notes": ("notes", "note", "comment", "comments"),
}


@dataclass(frozen=True)
class SnapshotLineInput:
    item_id: int
    item_code: str
    system_total_qty: Decimal
    uom: str | None
    notes: str | None


@dataclass(frozen=True)
class ReconciliationRow:
    item_id: int
    item_code: str
    item_description: str | None
    uom: str | None
    system_total_qty: Decimal
    counted_total_qty: Decimal | None
    variance: Decimal | None
    status: str


def _normalize_header(header: str) -> str:
    return header.strip().lower()


def _combine_notes(description: str | None, notes: str | None) -> str | None:
    parts: list[str] = []
    if description:
        parts.append(f"Description: {description}")
    if notes:
        parts.append(f"Notes: {notes}" if description else notes)
    return " | ".join(parts) if parts else None


def _resolve_header_map(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    normalized_headers = [_normalize_header(header) for header in headers]
    header_map: dict[str, str] = {}
    missing_required: list[str] = []

    for canonical, aliases in SNAPSHOT_HEADER_ALIASES.items():
        matches = [
            headers[idx]
            for idx, normalized in enumerate(normalized_headers)
            if normalized in {alias.lower() for alias in aliases}
        ]
        if matches:
            header_map[canonical] = matches[0]
        elif canonical in REQUIRED_SNAPSHOT_HEADERS:
            missing_required.append(canonical)

    return header_map, missing_required


def parse_snapshot_csv(raw_csv: str) -> tuple[list[SnapshotLineInput], list[str]]:
    errors: list[str] = []
    reader = csv.DictReader(StringIO(raw_csv))
    if not reader.fieldnames:
        return [], ["The uploaded CSV does not include headers."]

    header_map, missing_headers = _resolve_header_map(reader.fieldnames)
    if missing_headers:
        accepted = {
            header: ", ".join(SNAPSHOT_HEADER_ALIASES[header])
            for header in missing_headers
        }
        return [], [
            "Missing required headers: "
            + ", ".join(sorted(missing_headers))
            + ". Accepted headers: "
            + "; ".join(
                f"{header}: {aliases}" for header, aliases in accepted.items()
            )
        ]

    rows: list[tuple[int, dict[str, str]]] = []
    for idx, row in enumerate(reader, start=2):
        rows.append((idx, row))

    if not rows:
        return [], ["The uploaded CSV does not include any data rows."]

    item_codes = [
        (row.get(header_map.get("item_code", "")) or "").strip()
        for _, row in rows
        if (row.get(header_map.get("item_code", "")) or "").strip()
    ]
    item_map = {
        item.sku: item
        for item in Item.query.filter(Item.sku.in_(set(item_codes))).all()
    }

    seen_codes: set[str] = set()
    parsed_rows: list[SnapshotLineInput] = []

    for idx, row in rows:
        raw_code = (row.get(header_map.get("item_code", "")) or "").strip()
        if not raw_code:
            errors.append(f"Row {idx}: item_code is required.")
            continue
        if raw_code in seen_codes:
            errors.append(f"Row {idx}: duplicate item_code '{raw_code}'.")
            continue
        seen_codes.add(raw_code)

        item = item_map.get(raw_code)
        if item is None:
            errors.append(f"Row {idx}: unknown item_code '{raw_code}'.")
            continue

        raw_qty = (row.get(header_map.get("system_total_qty", "")) or "").strip()
        if not raw_qty:
            errors.append(f"Row {idx}: system_total_qty is required.")
            continue
        try:
            qty = Decimal(raw_qty)
        except InvalidOperation:
            errors.append(
                f"Row {idx}: system_total_qty '{raw_qty}' must be numeric."
            )
            continue

        uom = None
        if "uom" in header_map:
            uom = (row.get(header_map.get("uom", "")) or "").strip() or None
        description = None
        if "description" in header_map:
            description = (row.get(header_map.get("description", "")) or "").strip() or None
        notes = None
        if "notes" in header_map:
            notes = (row.get(header_map.get("notes", "")) or "").strip() or None
        combined_notes = _combine_notes(description, notes)

        parsed_rows.append(
            SnapshotLineInput(
                item_id=item.id,
                item_code=raw_code,
                system_total_qty=qty,
                uom=uom,
                notes=combined_notes,
            )
        )

    return parsed_rows, errors


def get_known_item_location_pairs() -> list[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()

    movement_pairs = (
        db.session.query(Movement.item_id, Movement.location_id)
        .filter(Movement.item_id.isnot(None), Movement.location_id.isnot(None))
        .distinct()
        .all()
    )
    pairs.update(movement_pairs)

    location_fields = [
        Item.default_location_id,
        Item.secondary_location_id,
        Item.point_of_use_location_id,
    ]
    for field in location_fields:
        item_pairs = (
            db.session.query(Item.id, field)
            .filter(field.isnot(None))
            .distinct()
            .all()
        )
        pairs.update({(item_id, location_id) for item_id, location_id in item_pairs})

    return sorted(pairs)


def ensure_count_lines_for_snapshot(snapshot: InventorySnapshot) -> int:
    item_ids = [line.item_id for line in snapshot.lines]
    if not item_ids:
        return 0

    item_id_set = set(item_ids)
    known_pairs = {
        (item_id, location_id)
        for item_id, location_id in get_known_item_location_pairs()
        if item_id in item_id_set
    }
    if not known_pairs:
        return 0

    existing_pairs = {
        (line.item_id, line.location_id)
        for line in InventoryCountLine.query.filter_by(snapshot_id=snapshot.id).all()
    }

    to_create = known_pairs - existing_pairs
    if not to_create:
        return 0

    new_lines = [
        InventoryCountLine(
            snapshot_id=snapshot.id,
            item_id=item_id,
            location_id=location_id,
        )
        for item_id, location_id in to_create
    ]
    db.session.add_all(new_lines)
    return len(new_lines)


def build_reconciliation_rows(snapshot_id: int) -> list[ReconciliationRow]:
    snapshot_lines = (
        InventorySnapshotLine.query.filter_by(snapshot_id=snapshot_id)
        .join(Item)
        .all()
    )
    count_lines = InventoryCountLine.query.filter_by(snapshot_id=snapshot_id).all()

    counts_by_item: dict[int, dict[str, object]] = {}
    for line in count_lines:
        entry = counts_by_item.setdefault(
            line.item_id,
            {
                "total": Decimal("0"),
                "has_lines": False,
                "has_values": False,
                "fully_counted": True,
            },
        )
        entry["has_lines"] = True
        if line.counted_qty is None:
            entry["fully_counted"] = False
            continue
        entry["has_values"] = True
        entry["total"] = entry["total"] + line.counted_qty

    rows: list[ReconciliationRow] = []
    for line in snapshot_lines:
        item = line.item
        count_entry = counts_by_item.get(line.item_id)
        status = "UNCOUNTED"
        counted_total = None
        variance = None
        fully_counted = False

        if count_entry is None or not count_entry["has_lines"]:
            status = "UNLOCATED"
        else:
            fully_counted = bool(count_entry["fully_counted"])
            if count_entry["has_values"]:
                counted_total = count_entry["total"]
                variance = counted_total - line.system_total_qty
                if variance == 0 and fully_counted:
                    status = "MATCH"
                elif variance > 0:
                    status = "OVER"
                elif variance < 0:
                    status = "SHORT"
                else:
                    status = "PARTIAL"
            else:
                status = "UNCOUNTED"

        rows.append(
            ReconciliationRow(
                item_id=item.id,
                item_code=item.sku,
                item_description=item.name,
                uom=line.uom or item.unit,
                system_total_qty=line.system_total_qty,
                counted_total_qty=counted_total,
                variance=variance,
                status=status,
            )
        )

    rows.sort(key=lambda row: row.item_code)
    return rows


def build_count_sheet_rows(snapshot_id: int) -> Iterable[dict[str, object]]:
    lines = (
        InventoryCountLine.query.filter_by(snapshot_id=snapshot_id)
        .join(Item)
        .join(Location)
        .order_by(Location.code, Item.sku)
        .all()
    )
    snapshot_lines = {
        line.item_id: line
        for line in InventorySnapshotLine.query.filter_by(snapshot_id=snapshot_id).all()
    }

    for line in lines:
        snapshot_line = snapshot_lines.get(line.item_id)
        yield {
            "location_code": line.location.code,
            "location_description": line.location.description,
            "item_code": line.item.sku,
            "item_description": line.item.name,
            "uom": (snapshot_line.uom if snapshot_line else None) or line.item.unit,
            "system_total_qty": snapshot_line.system_total_qty if snapshot_line else None,
            "counted_qty": line.counted_qty,
            "notes": line.notes,
        }


def summarize_snapshot(snapshot_id: int) -> dict[str, int]:
    line_total = (
        db.session.query(func.count(InventorySnapshotLine.id))
        .filter(InventorySnapshotLine.snapshot_id == snapshot_id)
        .scalar()
    )
    count_total = (
        db.session.query(func.count(InventoryCountLine.id))
        .filter(InventoryCountLine.snapshot_id == snapshot_id)
        .scalar()
    )
    return {
        "snapshot_lines": int(line_total or 0),
        "count_lines": int(count_total or 0),
    }
