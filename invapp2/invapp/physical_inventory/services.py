"""Services for physical inventory workflows."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from flask import current_app
from sqlalchemy import func

from invapp.extensions import db
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    Location,
    Movement,
)

try:  # optional dependency
    from rapidfuzz import fuzz  # type: ignore
except Exception:  # pragma: no cover - optional
    fuzz = None

try:  # optional dependency
    import openpyxl
except Exception:  # pragma: no cover - optional
    openpyxl = None

REQUIRED_SNAPSHOT_HEADERS = ("part_number", "quantity")
OPTIONAL_SNAPSHOT_HEADERS = ("description", "uom", "notes")

PART_NUMBER_HINTS = ("part", "number", "num", "item", "code", "pn", "mpn")
DESCRIPTION_HINTS = ("desc", "description", "name", "title")
EXCLUDED_PART_NUMBER_FIELDS = {"sku"}

IMPORT_STORAGE_ROOT = os.path.join(tempfile.gettempdir(), "invapp_imports")
IMPORT_FILE_TTL_SECONDS = 60 * 60


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
class MatchResult:
    item_id: int | None
    match_reason: str
    confidence: str


@dataclass(frozen=True)
class MatchSummary:
    matched_rows: int
    unmatched_rows: int
    part_desc_matches: int
    normalized_matches: int


@dataclass(frozen=True)
class DuplicateSummary:
    duplicate_groups: int
    grouped_rows: int


@dataclass(frozen=True)
class ItemFieldCandidates:
    part_number_fields: list[str]
    description_fields: list[str]


@dataclass(frozen=True)
class ParsedImport:
    headers: list[str]
    rows: list[dict[str, str]]
    normalized_headers: list[str]


@dataclass(frozen=True)
class MatchPreviewRow:
    part_number: str
    description: str | None
    quantity: str | None
    match_reason: str


@dataclass(frozen=True)
class MatchContext:
    matches: list[MatchResult]
    summary: MatchSummary


def _parse_config_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def get_item_field_candidates() -> ItemFieldCandidates:
    override_part_fields = _parse_config_list(
        current_app.config.get("PHYS_INV_ITEM_ID_FIELDS")
    )
    override_desc_fields = _parse_config_list(
        current_app.config.get("PHYS_INV_DESC_FIELDS")
    )

    columns = [column.name for column in Item.__table__.columns]

    part_fields = []
    if override_part_fields:
        part_fields = override_part_fields
    else:
        for name in columns:
            lowered = name.lower()
            if lowered in EXCLUDED_PART_NUMBER_FIELDS:
                continue
            if any(hint in lowered for hint in PART_NUMBER_HINTS):
                part_fields.append(name)

    desc_fields = []
    if override_desc_fields:
        desc_fields = override_desc_fields
    else:
        for name in columns:
            lowered = name.lower()
            if any(hint in lowered for hint in DESCRIPTION_HINTS):
                desc_fields.append(name)

    return ItemFieldCandidates(part_number_fields=part_fields, description_fields=desc_fields)


def _normalize_header(header: str) -> str:
    normalized = header.strip().lower()
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def _unique_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for header in headers:
        base = header
        if base not in counts:
            counts[base] = 1
            unique.append(base)
            continue
        counts[base] += 1
        unique.append(f"{base}_{counts[base]}")
    return unique


def _read_csv(file_stream: io.BytesIO, delimiter: str | None = None) -> list[list[str]]:
    text = file_stream.read().decode("utf-8-sig")
    file_stream.seek(0)
    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(text[:1024])
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [row for row in reader]


def _read_xlsx(file_stream: io.BytesIO) -> list[list[str]]:
    if openpyxl is None:
        raise ValueError("XLSX support requires openpyxl.")
    workbook = openpyxl.load_workbook(file_stream, read_only=True, data_only=True)
    sheet = workbook.active
    rows: list[list[str]] = []
    for row in sheet.iter_rows(values_only=True):
        rows.append(["" if cell is None else str(cell) for cell in row])
    return rows


def parse_import_file(filename: str, file_stream: io.BytesIO) -> ParsedImport:
    extension = os.path.splitext(filename or "")[1].lower()
    if extension in {".csv", ".tsv", ".txt"}:
        delimiter = "\t" if extension == ".tsv" else None
        raw_rows = _read_csv(file_stream, delimiter=delimiter)
    elif extension == ".xlsx":
        raw_rows = _read_xlsx(file_stream)
    else:
        raise ValueError("Unsupported file type. Please upload CSV, TSV, or XLSX.")

    if not raw_rows:
        raise ValueError("The uploaded file does not include any rows.")

    raw_headers = raw_rows[0]
    normalized_headers = [_normalize_header(header) for header in raw_headers]
    normalized_headers = _unique_headers(normalized_headers)

    data_rows: list[dict[str, str]] = []
    for row in raw_rows[1:]:
        if not any(cell.strip() for cell in row if isinstance(cell, str)):
            continue
        row_map = {}
        for idx, header in enumerate(normalized_headers):
            value = ""
            if idx < len(row):
                cell = row[idx]
                value = "" if cell is None else str(cell)
            row_map[header] = value
        data_rows.append(row_map)

    if not data_rows:
        raise ValueError("The uploaded file does not include any data rows.")

    return ParsedImport(
        headers=raw_headers,
        rows=data_rows,
        normalized_headers=normalized_headers,
    )


def _get_import_storage_dir(namespace: str) -> str:
    path = os.path.join(IMPORT_STORAGE_ROOT, namespace)
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_import_storage(namespace: str) -> None:
    storage_dir = _get_import_storage_dir(namespace)
    current_time = time.time()
    try:
        for name in os.listdir(storage_dir):
            path = os.path.join(storage_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                if current_time - os.path.getmtime(path) > IMPORT_FILE_TTL_SECONDS:
                    os.remove(path)
            except OSError:
                continue
    except FileNotFoundError:
        pass


def store_import_payload(namespace: str, payload: dict[str, object]) -> str:
    _cleanup_import_storage(namespace)
    token = f"{int(time.time())}_{os.urandom(6).hex()}"
    path = os.path.join(_get_import_storage_dir(namespace), f"{token}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return token


def load_import_payload(namespace: str, token: str) -> dict[str, object] | None:
    if not token or any(ch in token for ch in ("/", "\\")):
        return None
    path = os.path.join(_get_import_storage_dir(namespace), f"{token}.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError:
        return None


def delete_import_payload(namespace: str, token: str) -> None:
    if not token or any(ch in token for ch in ("/", "\\")):
        return
    path = os.path.join(_get_import_storage_dir(namespace), f"{token}.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("Quantity must be numeric.")


def _is_numeric(value: str) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        Decimal(text)
        return True
    except InvalidOperation:
        return False


def _is_non_negative(value: str) -> bool:
    try:
        return Decimal(str(value).strip()) >= 0
    except (InvalidOperation, TypeError):
        return False


def _normalize_part_number(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())


def _normalize_part_number_strict(value: str) -> str:
    normalized = _normalize_part_number(value)
    return normalized.replace("-", "").replace("_", "")


def _normalize_description(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return normalized


def _build_item_lookup(
    items: list[Item],
    part_fields: list[str],
    desc_fields: list[str],
) -> tuple[dict[str, list[int]], dict[str, list[int]], dict[int, str]]:
    raw_map: dict[str, list[int]] = {}
    normalized_map: dict[str, list[int]] = {}
    descriptions: dict[int, str] = {}

    for item in items:
        part_value = None
        for field in part_fields:
            part_value = getattr(item, field, None)
            if part_value:
                break
        if part_value:
            raw_key = str(part_value).strip()
            raw_map.setdefault(raw_key, []).append(item.id)
            normalized_key = _normalize_part_number(raw_key)
            normalized_map.setdefault(normalized_key, []).append(item.id)
            strict_key = _normalize_part_number_strict(raw_key)
            normalized_map.setdefault(strict_key, []).append(item.id)

        desc_value = None
        for field in desc_fields:
            desc_value = getattr(item, field, None)
            if desc_value:
                break
        if desc_value:
            descriptions[item.id] = _normalize_description(str(desc_value))

    return raw_map, normalized_map, descriptions


def _disambiguate_by_description(
    candidate_ids: list[int],
    target_description: str,
    item_descriptions: dict[int, str],
) -> int | None:
    if not target_description:
        return None

    normalized_target = _normalize_description(target_description)
    exact_matches = [
        item_id
        for item_id in candidate_ids
        if item_descriptions.get(item_id) == normalized_target
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    contains_matches = [
        item_id
        for item_id in candidate_ids
        if normalized_target in item_descriptions.get(item_id, "")
        or item_descriptions.get(item_id, "") in normalized_target
    ]
    if len(contains_matches) == 1:
        return contains_matches[0]

    if fuzz is not None:
        scored = []
        for item_id in candidate_ids:
            score = fuzz.ratio(normalized_target, item_descriptions.get(item_id, ""))
            scored.append((score, item_id))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 90:
            top_score = scored[0][0]
            top_matches = [item_id for score, item_id in scored if score == top_score]
            if len(top_matches) == 1:
                return top_matches[0]

    return None


def match_items(
    rows: list[dict[str, str]],
    part_col: str,
    desc_col: str | None,
    candidates: ItemFieldCandidates,
) -> MatchContext:
    items = Item.query.all()
    raw_map, normalized_map, descriptions = _build_item_lookup(
        items, candidates.part_number_fields, candidates.description_fields
    )

    matches: list[MatchResult] = []
    part_desc_matches = 0
    normalized_matches = 0

    for row in rows:
        raw_value = (row.get(part_col) or "").strip()
        desc_value = (row.get(desc_col) or "").strip() if desc_col else ""
        if not raw_value:
            matches.append(MatchResult(item_id=None, match_reason="unmatched", confidence="low"))
            continue

        raw_candidates = raw_map.get(raw_value, [])
        if len(raw_candidates) == 1:
            matches.append(
                MatchResult(
                    item_id=raw_candidates[0],
                    match_reason="part_number_exact",
                    confidence="high",
                )
            )
            continue

        normalized_value = _normalize_part_number(raw_value)
        normalized_candidates = normalized_map.get(normalized_value, [])
        if len(normalized_candidates) == 1:
            normalized_matches += 1
            matches.append(
                MatchResult(
                    item_id=normalized_candidates[0],
                    match_reason="part_number_normalized",
                    confidence="medium",
                )
            )
            continue

        strict_value = _normalize_part_number_strict(raw_value)
        strict_candidates = normalized_map.get(strict_value, [])
        if len(strict_candidates) == 1:
            normalized_matches += 1
            matches.append(
                MatchResult(
                    item_id=strict_candidates[0],
                    match_reason="part_number_normalized",
                    confidence="medium",
                )
            )
            continue
        candidates_list = raw_candidates or normalized_candidates or strict_candidates

        if candidates_list and desc_value:
            disambiguated = _disambiguate_by_description(
                candidates_list, desc_value, descriptions
            )
            if disambiguated is not None:
                part_desc_matches += 1
                matches.append(
                    MatchResult(
                        item_id=disambiguated,
                        match_reason="part+desc",
                        confidence="medium",
                    )
                )
                continue

        matches.append(MatchResult(item_id=None, match_reason="unmatched", confidence="low"))

    matched_rows = sum(1 for match in matches if match.item_id)
    unmatched_rows = len(matches) - matched_rows

    summary = MatchSummary(
        matched_rows=matched_rows,
        unmatched_rows=unmatched_rows,
        part_desc_matches=part_desc_matches,
        normalized_matches=normalized_matches,
    )

    return MatchContext(matches=matches, summary=summary)


def suggest_quantity_column(headers: list[str], rows: list[dict[str, str]]) -> str | None:
    best_column = None
    best_score = -1.0

    for header in headers:
        values = [row.get(header, "") for row in rows]
        non_empty = [value for value in values if str(value).strip()]
        if not non_empty:
            continue
        numeric_count = sum(1 for value in non_empty if _is_numeric(str(value)))
        non_negative_count = sum(1 for value in non_empty if _is_non_negative(str(value)))
        coverage = numeric_count / len(non_empty)
        non_negative_ratio = non_negative_count / len(non_empty)
        score = coverage * 0.7 + non_negative_ratio * 0.3
        if score > best_score:
            best_score = score
            best_column = header

    return best_column


def suggest_part_number_column(
    headers: list[str],
    rows: list[dict[str, str]],
    candidates: ItemFieldCandidates,
) -> str | None:
    if not candidates.part_number_fields:
        return None

    items = Item.query.all()
    raw_map, normalized_map, _ = _build_item_lookup(
        items, candidates.part_number_fields, candidates.description_fields
    )

    best_column = None
    best_score = -1.0
    for header in headers:
        values = [row.get(header, "") for row in rows]
        non_empty = [value for value in values if str(value).strip()]
        if not non_empty:
            continue
        match_count = 0
        for value in non_empty:
            text = str(value).strip()
            if text in raw_map:
                match_count += 1
                continue
            if _normalize_part_number(text) in normalized_map:
                match_count += 1
                continue
            if _normalize_part_number_strict(text) in normalized_map:
                match_count += 1
        score = match_count / len(non_empty)
        if score > best_score:
            best_score = score
            best_column = header

    return best_column


def suggest_description_column(headers: list[str], rows: list[dict[str, str]]) -> str | None:
    best_column = None
    best_score = -1.0

    for header in headers:
        values = [row.get(header, "") for row in rows]
        non_empty = [value for value in values if str(value).strip()]
        if not non_empty:
            continue
        text_density = sum(1 for value in non_empty if any(ch.isalpha() for ch in str(value)))
        avg_length = sum(len(str(value)) for value in non_empty) / len(non_empty)
        score = text_density * 0.6 + avg_length * 0.4
        if score > best_score:
            best_score = score
            best_column = header

    return best_column


def group_duplicate_rows(
    rows: list[dict[str, str]],
    part_col: str,
    desc_col: str | None,
    quantity_col: str,
    strategy: str,
) -> tuple[list[dict[str, str]], DuplicateSummary]:
    grouped: dict[tuple[str, str | None], list[dict[str, str]]] = {}

    for row in rows:
        part_value = _normalize_part_number(row.get(part_col, ""))
        desc_value = _normalize_description(row.get(desc_col, "")) if desc_col else None
        key = (part_value, desc_value)
        grouped.setdefault(key, []).append(row)

    duplicate_groups = sum(1 for group in grouped.values() if len(group) > 1)
    grouped_rows = sum(len(group) for group in grouped.values() if len(group) > 1)

    result_rows: list[dict[str, str]] = []
    for group in grouped.values():
        if len(group) == 1:
            result_rows.append(group[0])
            continue
        if strategy == "take_first":
            result_rows.append(group[0])
        elif strategy == "take_last":
            result_rows.append(group[-1])
        else:
            total = Decimal("0")
            for row in group:
                if row.get(quantity_col):
                    total += _parse_decimal(row[quantity_col])
            merged = dict(group[-1])
            merged[quantity_col] = str(total)
            result_rows.append(merged)

    return result_rows, DuplicateSummary(duplicate_groups=duplicate_groups, grouped_rows=grouped_rows)


def build_preview_rows(
    rows: list[dict[str, str]],
    matches: list[MatchResult],
    part_col: str,
    desc_col: str | None,
    quantity_col: str,
    limit: int = 50,
) -> list[MatchPreviewRow]:
    preview: list[MatchPreviewRow] = []
    for row, match in zip(rows, matches):
        if len(preview) >= limit:
            break
        preview.append(
            MatchPreviewRow(
                part_number=row.get(part_col, ""),
                description=row.get(desc_col, "") if desc_col else None,
                quantity=row.get(quantity_col),
                match_reason=match.match_reason,
            )
        )
    return preview


def resolve_item_part_number(item: Item, candidates: ItemFieldCandidates) -> str:
    for field in candidates.part_number_fields:
        value = getattr(item, field, None)
        if value:
            return str(value)
    return "UNCONFIGURED"


def resolve_item_description(item: Item, candidates: ItemFieldCandidates) -> str | None:
    for field in candidates.description_fields:
        value = getattr(item, field, None)
        if value:
            return str(value)
    return None


def build_reconciliation_rows(snapshot_id: int) -> list[ReconciliationRow]:
    candidates = get_item_field_candidates()
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
                part_number=resolve_item_part_number(item, candidates),
                description=resolve_item_description(item, candidates),
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
    candidates = get_item_field_candidates()
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

    for line in lines:
        snapshot_line = snapshot_lines.get(line.item_id)
        yield {
            "location_code": line.location.code,
            "location_description": line.location.description,
            "part_number": resolve_item_part_number(line.item, candidates),
            "description": resolve_item_description(line.item, candidates),
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
