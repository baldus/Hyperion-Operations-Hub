"""Services for physical inventory matching and normalization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import String, Text, func
from sqlalchemy.orm import load_only

from invapp.extensions import db
from invapp.models import Item


SKU_EXCLUSION_TOKENS = {"sku", "item_number", "part_number"}


@dataclass(frozen=True)
class NormalizationOptions:
    trim_whitespace: bool = True
    case_insensitive: bool = True
    remove_spaces: bool = False
    remove_dashes_underscores: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


def normalize_match_value(value: object | None, options: NormalizationOptions) -> str:
    if value is None:
        return ""
    text = str(value)
    if options.trim_whitespace:
        text = text.strip()
    if options.remove_spaces:
        text = "".join(text.split())
    if options.remove_dashes_underscores:
        text = text.replace("-", "").replace("_", "")
    if options.case_insensitive:
        text = text.lower()
    return text


def _is_excluded_item_field(field_name: str) -> bool:
    normalized = field_name.lower()
    if normalized in SKU_EXCLUSION_TOKENS:
        return True
    return any(token in normalized for token in SKU_EXCLUSION_TOKENS)


def get_item_text_fields() -> list[dict[str, str]]:
    """Return Item model string fields suitable for matching (excluding SKU)."""

    fields: list[dict[str, str]] = []
    for column in Item.__table__.columns:
        if not isinstance(column.type, (String, Text)):
            continue
        if _is_excluded_item_field(column.name):
            continue
        label = column.name.replace("_", " ").title()
        fields.append({"name": column.name, "label": label})
    return fields


def get_item_field_samples(field_name: str, limit: int = 20) -> list[str]:
    """Return distinct sample values for a given Item field."""

    allowed_fields = {field["name"] for field in get_item_text_fields()}
    if field_name not in allowed_fields:
        return []

    column = getattr(Item, field_name)
    results = (
        db.session.query(column)
        .filter(column.isnot(None))
        .filter(column != "")
        .distinct()
        .order_by(column)
        .limit(limit)
        .all()
    )
    return [value for (value,) in results]


def _parse_quantity(value: object | None) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value).strip() or 0)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _load_items_for_matching(fields: Iterable[str]) -> list[Item]:
    load_fields = [Item.id] + [getattr(Item, field) for field in fields]
    return Item.query.options(load_only(*load_fields)).all()


def items_assigned_to_location(location_id: int) -> list[Item]:
    """Return distinct items assigned to a location by any location reference field."""

    return (
        Item.query.filter(
            (Item.default_location_id == location_id)
            | (Item.secondary_location_id == location_id)
            | (Item.point_of_use_location_id == location_id)
        )
        .order_by(func.lower(Item.name), Item.id)
        .all()
    )


def match_upload_rows(
    rows: Iterable[dict[str, object]],
    primary_upload_column: str,
    primary_item_field: str,
    quantity_column: str,
    options: NormalizationOptions,
    secondary_upload_column: str | None = None,
    secondary_item_field: str | None = None,
) -> dict[str, object]:
    rows_list = list(rows)
    allowed_fields = {field["name"] for field in get_item_text_fields()}
    if primary_item_field not in allowed_fields:
        raise ValueError("Invalid primary item field selected.")
    if secondary_item_field and secondary_item_field not in allowed_fields:
        raise ValueError("Invalid secondary item field selected.")

    fields = {primary_item_field}
    if secondary_item_field:
        fields.add(secondary_item_field)

    items = _load_items_for_matching(fields)

    lookup: dict[str, list[Item]] = {}
    for item in items:
        value = getattr(item, primary_item_field, None)
        key = normalize_match_value(value, options)
        if not key:
            continue
        lookup.setdefault(key, []).append(item)

    matched_rows: list[dict[str, object]] = []
    unmatched_rows: list[dict[str, object]] = []
    ambiguous_rows: list[dict[str, object]] = []

    for index, row in enumerate(rows_list, start=1):
        raw_value = row.get(primary_upload_column)
        normalized = normalize_match_value(raw_value, options)
        if not normalized:
            unmatched_rows.append(
                {
                    "row_index": index,
                    "reason": "Missing primary match value",
                    "value": raw_value,
                    "row": row,
                }
            )
            continue

        matches = lookup.get(normalized, [])
        if len(matches) == 1:
            item = matches[0]
            matched_rows.append(
                {
                    "row_index": index,
                    "item_id": item.id,
                    "item_name": item.name,
                    "quantity": _parse_quantity(row.get(quantity_column)),
                    "matched_on_secondary": False,
                    "row": row,
                }
            )
            continue

        if len(matches) == 0:
            unmatched_rows.append(
                {
                    "row_index": index,
                    "reason": "No match found",
                    "value": raw_value,
                    "row": row,
                }
            )
            continue

        if secondary_upload_column and secondary_item_field:
            secondary_raw = row.get(secondary_upload_column)
            secondary_key = normalize_match_value(secondary_raw, options)
            if secondary_key:
                secondary_matches = [
                    item
                    for item in matches
                    if normalize_match_value(
                        getattr(item, secondary_item_field, None), options
                    )
                    == secondary_key
                ]
            else:
                secondary_matches = []

            if len(secondary_matches) == 1:
                item = secondary_matches[0]
                matched_rows.append(
                    {
                        "row_index": index,
                        "item_id": item.id,
                        "item_name": item.name,
                        "quantity": _parse_quantity(row.get(quantity_column)),
                        "matched_on_secondary": True,
                        "row": row,
                    }
                )
                continue

            ambiguous_rows.append(
                {
                    "row_index": index,
                    "reason": "Ambiguous match after secondary check",
                    "value": raw_value,
                    "row": row,
                    "candidates": [item.id for item in matches],
                }
            )
            continue

        ambiguous_rows.append(
            {
                "row_index": index,
                "reason": "Ambiguous match",
                "value": raw_value,
                "row": row,
                "candidates": [item.id for item in matches],
            }
        )

    total_rows = len(rows_list)
    match_rate = (len(matched_rows) / total_rows * 100) if total_rows else 0

    return {
        "total_rows": total_rows,
        "match_rate": match_rate,
        "matched_rows": matched_rows,
        "unmatched_rows": unmatched_rows,
        "ambiguous_rows": ambiguous_rows,
        "matched_count": len(matched_rows),
        "unmatched_count": len(unmatched_rows),
        "ambiguous_count": len(ambiguous_rows),
    }


def aggregate_matched_rows(
    matched_rows: Iterable[dict[str, object]],
    strategy: str,
) -> dict[int, Decimal]:
    """Aggregate matched rows by item_id using the selected strategy."""

    totals: dict[int, Decimal] = {}
    for entry in matched_rows:
        item_id = int(entry["item_id"])
        quantity = entry.get("quantity")
        if not isinstance(quantity, Decimal):
            quantity = _parse_quantity(quantity)

        if item_id not in totals:
            totals[item_id] = quantity
            continue

        if strategy == "keep_first":
            continue
        if strategy == "keep_last":
            totals[item_id] = quantity
            continue

        totals[item_id] += quantity

    return totals
