"""Services for physical inventory workflows."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from typing import Iterable

from flask import current_app
from openpyxl import load_workbook

from sqlalchemy import func

from invapp.extensions import db
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    Movement,
)



@dataclass(frozen=True)
class SnapshotLineInput:
    item_id: int
    item_code: str
    system_total_qty: Decimal
    uom: str | None
    notes: str | None
    source_description_text: str | None


@dataclass(frozen=True)
class ReconciliationRow:
    item_id: int
    part_number: str
    description: str | None
    uom: str | None
    system_total_qty: Decimal
    counted_total_qty: Decimal | None
    variance: Decimal | None
    status: str


@dataclass(frozen=True)
class ImportData:
    headers: list[str]
    rows: list[dict[str, str]]
    preview_rows: list[dict[str, str]]
    normalized_headers: list[str]


@dataclass(frozen=True)
class MatchResult:
    item_id: int | None
    match_reason: str
    confidence: str


def _normalize_header(header: str) -> str:
    return header.strip().lower()


def normalize_import_header(header: str) -> str:
    return _normalize_header(header).replace(" ", "_")


def _normalize_description(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _normalize_part_number(value: str) -> str:
    return value.strip().upper().replace(" ", "")


def _normalize_part_number_strict(value: str) -> str:
    return _normalize_part_number(value).replace("-", "").replace("_", "")


def _safe_decimal(value: str) -> Decimal | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _combine_notes(description: str | None, notes: str | None) -> str | None:
    parts: list[str] = []
    if description:
        parts.append(f"Description: {description}")
    if notes:
        parts.append(f"Notes: {notes}" if description else notes)
    return " | ".join(parts) if parts else None


def parse_import_bytes(
    filename: str,
    payload: bytes,
) -> tuple[ImportData | None, list[str]]:
    ext = (filename or "").lower().rsplit(".", maxsplit=1)
    if len(ext) == 1:
        return None, ["Upload a CSV, TSV, or XLSX file."]
    extension = ext[-1]

    if extension in {"csv", "tsv"}:
        delimiter = "\t" if extension == "tsv" else ","
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None, ["File must be UTF-8 encoded."]
        return _parse_delimited(text, delimiter)
    if extension == "xlsx":
        return _parse_xlsx(payload)
    return None, ["Unsupported file type. Use CSV, TSV, or XLSX."]


def _parse_delimited(
    text: str,
    delimiter: str,
) -> tuple[ImportData | None, list[str]]:
    reader = csv.reader(StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return None, ["The uploaded file does not include any rows."]
    headers = [str(header).strip() if header is not None else "" for header in rows[0]]
    if not any(headers):
        return None, ["The uploaded file does not include headers."]
    headers = _ensure_unique_headers(headers)
    data_rows = [
        _row_to_dict(headers, row)
        for row in rows[1:]
        if any(cell for cell in row)
    ]
    preview_rows = data_rows[:50]
    normalized_headers = [normalize_import_header(h) for h in headers]
    return (
        ImportData(
            headers=headers,
            rows=data_rows,
            preview_rows=preview_rows,
            normalized_headers=normalized_headers,
        ),
        [],
    )


def _parse_xlsx(payload: bytes) -> tuple[ImportData | None, list[str]]:
    try:
        workbook = load_workbook(filename=BytesIO(payload), read_only=True, data_only=True)
    except Exception:
        return None, ["Unable to read the XLSX file. Please re-export and try again."]
    sheet = workbook.active
    rows = []
    for row in sheet.iter_rows(values_only=True):
        rows.append([cell if cell is not None else "" for cell in row])
    if not rows:
        return None, ["The uploaded file does not include any rows."]
    headers = [str(header).strip() if header is not None else "" for header in rows[0]]
    if not any(headers):
        return None, ["The uploaded file does not include headers."]
    headers = _ensure_unique_headers(headers)
    data_rows = [
        _row_to_dict(headers, row)
        for row in rows[1:]
        if any(cell for cell in row)
    ]
    preview_rows = data_rows[:50]
    normalized_headers = [normalize_import_header(h) for h in headers]
    return (
        ImportData(
            headers=headers,
            rows=data_rows,
            preview_rows=preview_rows,
            normalized_headers=normalized_headers,
        ),
        [],
    )


def _ensure_unique_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique_headers: list[str] = []
    for header in headers:
        header_key = header or "column"
        if header_key not in counts:
            counts[header_key] = 0
            unique_headers.append(header_key)
        else:
            counts[header_key] += 1
            unique_headers.append(f"{header_key}_{counts[header_key]}")
    return unique_headers


def _row_to_dict(headers: list[str], row: list[object]) -> dict[str, str]:
    row_values = list(row) + [""] * (len(headers) - len(row))
    return {
        header: "" if value is None else str(value).strip()
        for header, value in zip(headers, row_values)
    }


def get_item_field_candidates() -> tuple[list[str], list[str]]:
    part_keywords = ("part", "number", "num", "item", "code", "pn", "mpn")
    desc_keywords = ("desc", "description", "name", "title")
    columns = [col.name for col in Item.__table__.columns]

    def _resolve_override(key: str) -> list[str]:
        override = current_app.config.get(key)
        if not override:
            return []
        if isinstance(override, (list, tuple, set)):
            values = [str(value).strip() for value in override]
        else:
            values = [value.strip() for value in str(override).split(",")]
        return [value for value in values if value and value in columns and value != "sku"]

    override_part = _resolve_override("PHYS_INV_ITEM_ID_FIELDS")
    override_desc = _resolve_override("PHYS_INV_DESC_FIELDS")
    if override_part:
        part_fields = override_part
    else:
        part_fields = [
            name
            for name in columns
            if name != "sku" and any(keyword in name.lower() for keyword in part_keywords)
        ]
    if override_desc:
        desc_fields = override_desc
    else:
        desc_fields = [
            name
            for name in columns
            if any(keyword in name.lower() for keyword in desc_keywords)
        ]
    return part_fields, desc_fields


def build_item_lookup() -> tuple[dict[str, list[int]], dict[str, list[int]], dict[str, list[int]], dict[int, list[str]], list[str], list[str]]:
    part_fields, desc_fields = get_item_field_candidates()
    raw_map: dict[str, set[int]] = {}
    normalized_map: dict[str, set[int]] = {}
    strict_map: dict[str, set[int]] = {}
    descriptions: dict[int, list[str]] = {}

    if not part_fields:
        return raw_map, normalized_map, strict_map, descriptions, part_fields, desc_fields

    for item in Item.query.all():
        for field in part_fields:
            value = getattr(item, field, None)
            if not value:
                continue
            raw_value = str(value).strip()
            if not raw_value:
                continue
            raw_map.setdefault(raw_value, set()).add(item.id)
            normalized_map.setdefault(_normalize_part_number(raw_value), set()).add(item.id)
            strict_map.setdefault(_normalize_part_number_strict(raw_value), set()).add(item.id)
        for field in desc_fields:
            value = getattr(item, field, None)
            if not value:
                continue
            descriptions.setdefault(item.id, []).append(_normalize_description(str(value)))

    raw_lookup = {key: sorted(value) for key, value in raw_map.items()}
    normalized_lookup = {key: sorted(value) for key, value in normalized_map.items()}
    strict_lookup = {key: sorted(value) for key, value in strict_map.items()}
    return raw_lookup, normalized_lookup, strict_lookup, descriptions, part_fields, desc_fields


def suggest_column_mappings(
    import_data: ImportData,
    raw_lookup: dict[str, list[int]],
    normalized_lookup: dict[str, list[int]],
    strict_lookup: dict[str, list[int]],
    desc_fields: list[str],
) -> dict[str, str | None]:
    headers = import_data.headers
    suggestions: dict[str, str | None] = {
        "part_number": None,
        "quantity": None,
        "description": None,
        "uom": None,
        "notes": None,
    }

    quantity_scores: dict[str, float] = {}
    part_scores: dict[str, float] = {}
    desc_scores: dict[str, float] = {}
    uom_scores: dict[str, float] = {}
    notes_scores: dict[str, float] = {}

    for header in headers:
        values = [row.get(header, "") for row in import_data.rows]
        non_empty = [value for value in values if value.strip()]
        total = len(values) or 1
        numeric = sum(1 for value in values if _safe_decimal(value) is not None)
        numeric_ratio = numeric / total
        quantity_scores[header] = numeric_ratio

        matches = 0
        for value in non_empty:
            if value in raw_lookup:
                matches += 1
                continue
            normalized = _normalize_part_number(value)
            if normalized in normalized_lookup or _normalize_part_number_strict(value) in strict_lookup:
                matches += 1
        part_scores[header] = matches / (len(non_empty) or 1)

        text_density = sum(
            1 for value in non_empty if any(char.isalpha() for char in value)
        ) / (len(non_empty) or 1)
        desc_scores[header] = text_density

        normalized_header = normalize_import_header(header)
        if "uom" in normalized_header or "unit" in normalized_header:
            uom_scores[header] = 1.0
        if "note" in normalized_header or "comment" in normalized_header:
            notes_scores[header] = 1.0

    if part_scores:
        suggestions["part_number"] = max(part_scores, key=part_scores.get)
    if quantity_scores:
        suggestions["quantity"] = max(quantity_scores, key=quantity_scores.get)
    if desc_fields:
        normalized_desc_fields = {normalize_import_header(field) for field in desc_fields}
        for header in headers:
            if normalize_import_header(header) in normalized_desc_fields:
                suggestions["description"] = header
                break
    if not suggestions["description"] and desc_scores:
        suggestions["description"] = max(desc_scores, key=desc_scores.get)
    if uom_scores:
        suggestions["uom"] = max(uom_scores, key=uom_scores.get)
    if notes_scores:
        suggestions["notes"] = max(notes_scores, key=notes_scores.get)

    return suggestions


def match_items(
    rows: list[dict[str, str]],
    part_col: str,
    desc_col: str | None,
    raw_lookup: dict[str, list[int]],
    normalized_lookup: dict[str, list[int]],
    strict_lookup: dict[str, list[int]],
    descriptions: dict[int, list[str]],
) -> tuple[list[MatchResult], int]:
    results: list[MatchResult] = []
    collisions_resolved = 0

    for row in rows:
        raw_value = row.get(part_col, "").strip()
        if not raw_value:
            results.append(MatchResult(None, "unmatched", "low"))
            continue

        candidates = raw_lookup.get(raw_value)
        match_reason = "part_number_exact"
        if not candidates:
            normalized = _normalize_part_number(raw_value)
            candidates = normalized_lookup.get(normalized)
            match_reason = "part_number_normalized"
        if not candidates:
            strict_value = _normalize_part_number_strict(raw_value)
            candidates = strict_lookup.get(strict_value)
            match_reason = "part_number_normalized"

        if candidates and len(candidates) == 1:
            results.append(MatchResult(candidates[0], match_reason, "high"))
            continue

        if desc_col and candidates:
            desc_value = row.get(desc_col, "")
            matched_id = _disambiguate_by_description(
                candidates,
                desc_value,
                descriptions,
            )
            if matched_id is not None:
                collisions_resolved += 1
                results.append(MatchResult(matched_id, "part+desc", "medium"))
                continue

        results.append(MatchResult(None, "unmatched", "low"))

    return results, collisions_resolved


def _disambiguate_by_description(
    candidates: list[int],
    desc_value: str,
    descriptions: dict[int, list[str]],
) -> int | None:
    normalized_desc = _normalize_description(desc_value)
    if not normalized_desc:
        return None

    exact_matches = [
        item_id
        for item_id in candidates
        if any(normalized_desc == entry for entry in descriptions.get(item_id, []))
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None

    contains_matches = [
        item_id
        for item_id in candidates
        if any(normalized_desc in entry or entry in normalized_desc for entry in descriptions.get(item_id, []))
    ]
    if len(contains_matches) == 1:
        return contains_matches[0]
    if len(contains_matches) > 1:
        return None

    try:
        from rapidfuzz import fuzz  # type: ignore
    except Exception:
        return None

    best_score = 0
    best_match = None
    for item_id in candidates:
        for entry in descriptions.get(item_id, []):
            score = fuzz.ratio(normalized_desc, entry)
            if score > best_score:
                best_score = score
                best_match = item_id
    if best_score >= 90:
        return best_match
    return None


def apply_duplicate_strategy(
    rows: list[dict[str, str]],
    part_col: str,
    desc_col: str | None,
    qty_col: str,
    strategy: str,
) -> tuple[list[dict[str, str]], int]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        part_value = row.get(part_col, "").strip()
        if desc_col:
            desc_value = row.get(desc_col, "").strip()
        else:
            desc_value = ""
        key = f"{_normalize_part_number_strict(part_value)}::{_normalize_description(desc_value)}"
        grouped.setdefault(key, []).append(row)

    duplicate_groups = sum(1 for group in grouped.values() if len(group) > 1)
    if strategy not in {"sum", "first", "last"}:
        strategy = "sum"

    merged_rows: list[dict[str, str]] = []
    for group in grouped.values():
        if strategy == "first":
            merged_rows.append(group[0])
            continue
        if strategy == "last":
            merged_rows.append(group[-1])
            continue
        total = Decimal("0")
        for row in group:
            qty = _safe_decimal(row.get(qty_col, ""))
            if qty is not None:
                total += qty
        merged = dict(group[0])
        merged[qty_col] = str(total)
        merged_rows.append(merged)

    return merged_rows, duplicate_groups


def build_snapshot_lines(
    rows: list[dict[str, str]],
    matches: list[MatchResult],
    part_col: str,
    desc_col: str | None,
    qty_col: str,
    uom_col: str | None,
    notes_col: str | None,
) -> tuple[list[SnapshotLineInput], list[str]]:
    errors: list[str] = []
    snapshot_lines: list[SnapshotLineInput] = []
    for idx, (row, match) in enumerate(zip(rows, matches), start=1):
        if match.item_id is None:
            continue
        raw_qty = row.get(qty_col, "")
        qty = _safe_decimal(raw_qty)
        if qty is None:
            errors.append(f"Row {idx}: quantity '{raw_qty}' must be numeric.")
            continue
        uom = (row.get(uom_col, "") if uom_col else "").strip() or None
        desc_value = (row.get(desc_col, "") if desc_col else "").strip() or None
        notes_value = (row.get(notes_col, "") if notes_col else "").strip() or None
        combined_notes = _combine_notes(desc_value, notes_value)
        snapshot_lines.append(
            SnapshotLineInput(
                item_id=match.item_id,
                item_code=row.get(part_col, "").strip(),
                system_total_qty=qty,
                uom=uom,
                notes=combined_notes,
                source_description_text=desc_value,
            )
        )
    return snapshot_lines, errors


def get_item_display_values(
    item: Item,
    part_fields: list[str],
    desc_fields: list[str],
) -> tuple[str, str]:
    part_value = ""
    for field in part_fields:
        value = getattr(item, field, None)
        if value:
            part_value = str(value)
            break
    desc_value = ""
    for field in desc_fields:
        value = getattr(item, field, None)
        if value:
            desc_value = str(value)
            break
    return part_value, desc_value


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
    part_fields, desc_fields = get_item_field_candidates()

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
        part_number, description = get_item_display_values(item, part_fields, desc_fields)
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
                part_number=part_number,
                description=description or None,
                uom=line.uom or item.unit,
                system_total_qty=line.system_total_qty,
                counted_total_qty=counted_total,
                variance=variance,
                status=status,
            )
        )

    rows.sort(key=lambda row: row.part_number)
    return rows


def build_count_sheet_rows(snapshot_id: int) -> Iterable[dict[str, object]]:
    lines = (
        InventoryCountLine.query.filter_by(snapshot_id=snapshot_id)
        .join(Item)
        .join(Location)
        .order_by(Location.code, Item.id)
        .all()
    )
    snapshot_lines = {
        line.item_id: line
        for line in InventorySnapshotLine.query.filter_by(snapshot_id=snapshot_id).all()
    }
    part_fields, desc_fields = get_item_field_candidates()

    for line in lines:
        snapshot_line = snapshot_lines.get(line.item_id)
        part_number, description = get_item_display_values(
            line.item, part_fields, desc_fields
        )
        yield {
            "location_code": line.location.code,
            "location_description": line.location.description,
            "part_number": part_number,
            "description": description,
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
