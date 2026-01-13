"""CSV schema definitions and header resolution utilities."""

from __future__ import annotations

from collections.abc import Iterable

ITEMS_CSV_COLUMNS: list[tuple[str, str]] = [
    ("id", "item_id"),
    ("sku", "sku"),
    ("name", "name"),
    ("type", "type"),
    ("unit", "unit"),
    ("description", "description"),
    ("min_stock", "min_stock"),
    ("notes", "notes"),
    ("list_price", "list_price"),
    ("last_unit_cost", "last_unit_cost"),
    ("item_class", "item_class"),
    ("default_location_id", "default_location_id"),
    ("default_location_code", "default_location_code"),
    ("secondary_location_id", "secondary_location_id"),
    ("secondary_location_code", "secondary_location_code"),
    ("point_of_use_location_id", "point_of_use_location_id"),
    ("point_of_use_location_code", "point_of_use_location_code"),
]

STOCK_CSV_COLUMNS: list[tuple[str, str]] = [
    ("item_id", "item_id"),
    ("sku", "sku"),
    ("name", "name"),
    ("primary_location_code", "primary_location_code"),
    ("location_id", "location_id"),
    ("location_code", "location_code"),
    ("batch_id", "batch_id"),
    ("lot_number", "lot_number"),
    ("quantity", "quantity"),
    ("person", "person"),
    ("reference", "reference"),
    ("received_date", "received_date"),
    ("expiration_date", "expiration_date"),
    ("supplier_name", "supplier_name"),
    ("supplier_code", "supplier_code"),
    ("purchase_order", "purchase_order"),
    ("notes", "notes"),
]

ITEMS_CSV_HEADERS = [header for _, header in ITEMS_CSV_COLUMNS]
STOCK_CSV_HEADERS = [header for _, header in STOCK_CSV_COLUMNS]

ITEMS_HEADER_ALIASES: dict[str, Iterable[str]] = {
    "id": ["id", "item_id"],
    "sku": ["sku", "SKU", "item_sku", "item_number", "part_number"],
    "name": ["name", "item_name"],
    "type": ["type", "item_type"],
    "unit": ["unit", "uom", "uom_code"],
    "description": ["description", "item_description"],
    "min_stock": ["min_stock", "minimum_stock", "min_qty", "minimum_qty"],
    "notes": ["notes", "item_notes"],
    "list_price": ["list_price", "price", "list"],
    "last_unit_cost": ["last_unit_cost", "unit_cost", "last_cost"],
    "item_class": ["item_class", "class"],
    "default_location_id": [
        "default_location_id",
        "default_loc_id",
        "default_location",
    ],
    "default_location_code": [
        "default_location_code",
        "default_location",
        "default_location_name",
        "location_code",
    ],
    "secondary_location_id": [
        "secondary_location_id",
        "secondary_loc_id",
        "secondary_location",
    ],
    "secondary_location_code": [
        "secondary_location_code",
        "secondary_location",
        "secondary_location_name",
        "secondary_loc_code",
    ],
    "point_of_use_location_id": [
        "point_of_use_location_id",
        "point_of_use_loc_id",
        "pou_location_id",
        "pou_loc_id",
    ],
    "point_of_use_location_code": [
        "point_of_use_location_code",
        "point_of_use_location",
        "pou_location_code",
        "pou_location",
    ],
}

STOCK_HEADER_ALIASES: dict[str, Iterable[str]] = {
    "item_id": ["item_id", "id"],
    "sku": ["sku", "SKU", "item_sku", "item_number", "part_number"],
    "name": ["name", "item_name"],
    "location_id": ["location_id", "loc_id"],
    "location_code": ["location_code", "location", "location_name", "loc_code"],
    "batch_id": ["batch_id", "lot_id", "batch"],
    "lot_number": ["lot_number", "lot", "lot_no", "batch_number"],
    "quantity": ["quantity", "qty", "on_hand", "onhand"],
    "person": ["person", "user", "adjusted_by"],
    "reference": ["reference", "ref"],
    "received_date": ["received_date", "received_at"],
    "expiration_date": ["expiration_date", "expiry_date", "expires_on"],
    "supplier_name": ["supplier_name", "vendor_name"],
    "supplier_code": ["supplier_code", "vendor_code"],
    "purchase_order": ["purchase_order", "po_number", "po"],
    "notes": ["notes", "batch_notes"],
}


def normalize_header(value: str) -> str:
    return value.strip().lower()


def resolve_import_mappings(
    headers: Iterable[str],
    import_fields: Iterable[dict[str, object]],
    aliases: dict[str, Iterable[str]],
) -> dict[str, str]:
    normalized_headers = {normalize_header(header): header for header in headers}
    resolved: dict[str, str] = {}
    for field in import_fields:
        field_name = field["field"]
        lookup_values = [field_name, *aliases.get(field_name, [])]
        for candidate in lookup_values:
            header_key = normalize_header(str(candidate))
            if header_key in normalized_headers:
                resolved[field_name] = normalized_headers[header_key]
                break
    return resolved


def expected_headers(columns: Iterable[tuple[str, str]]) -> list[str]:
    return [header for _, header in columns]
