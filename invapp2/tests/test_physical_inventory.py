import io
import os
import sys
from decimal import Decimal

import pytest
from openpyxl import Workbook

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    Location,
    Movement,
)
from invapp.physical_inventory.services import (
    apply_duplicate_strategy,
    build_item_lookup,
    build_reconciliation_rows,
    get_item_field_candidates,
    get_item_match_field_options,
    match_rows,
    NormalizationOptions,
    parse_import_bytes,
    suggest_column_mappings,
    summarize_match_preview,
)


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "PHYS_INV_ITEM_ID_FIELDS": "name",
            "PHYS_INV_DESC_FIELDS": "description",
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


@pytest.fixture
def sample_items(app):
    with app.app_context():
        item_a = Item(sku="SKU-1", name="PN-100", description="Widget A")
        item_b = Item(sku="SKU-2", name="PN-200", description="Widget B")
        db.session.add_all([item_a, item_b])
        db.session.commit()
        return item_a, item_b


@pytest.fixture
def sample_location(app):
    with app.app_context():
        location = Location(code="A1", description="Main Rack")
        db.session.add(location)
        db.session.commit()
        return location


def test_parse_import_csv():
    csv_text = "Part Number,Qty\nPN-100,5\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    assert data is not None
    assert data.headers == ["Part Number", "Qty"]
    assert data.rows[0]["Part Number"] == "PN-100"


def test_parse_import_xlsx():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Part Number", "Qty"])
    sheet.append(["PN-100", 5])
    output = io.BytesIO()
    workbook.save(output)
    data, errors = parse_import_bytes("snapshot.xlsx", output.getvalue())
    assert errors == []
    assert data is not None
    assert data.headers == ["Part Number", "Qty"]
    assert data.rows[0]["Qty"] == "5"


def test_suggest_quantity_column(sample_items, app):
    csv_text = "Part Number,Qty,Notes\nPN-100,5,ok\nPN-200,3,ok\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    with app.app_context():
        raw_lookup, normalized_lookup, strict_lookup, _, _, desc_fields = build_item_lookup()
    suggestions = suggest_column_mappings(
        data,
        raw_lookup,
        normalized_lookup,
        strict_lookup,
        desc_fields,
    )
    assert suggestions["quantity"] == "Qty"


def test_match_items_exact_and_normalized(sample_items, app):
    csv_text = "Item Name,Qty\n  pn-100  ,5\nPN-200,3\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    with app.app_context():
        items = Item.query.order_by(Item.id).all()
    options = NormalizationOptions(trim=True, case_insensitive=True)
    matches, _, _ = match_rows(
        data.rows,
        "Item Name",
        "name",
        None,
        None,
        options,
        items,
    )
    assert matches[0].item_id == items[0].id
    assert matches[1].item_id == items[1].id


def test_secondary_matching_resolves_unmatched(app):
    with app.app_context():
        item_a = Item(sku="SKU-1", name="Widget A", description="PN-999")
        item_b = Item(sku="SKU-2", name="Widget B", description="PN-888")
        db.session.add_all([item_a, item_b])
        db.session.commit()
        items = Item.query.order_by(Item.id).all()

    csv_text = "Name,Part,Qty\nUnknown,PN-999,4\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    options = NormalizationOptions(trim=True, case_insensitive=True)
    matches, _, _ = match_rows(
        data.rows,
        "Name",
        "name",
        "Part",
        "description",
        options,
        items,
    )
    assert matches[0].item_id == item_a.id
    assert matches[0].matched_on == "secondary"


def test_duplicate_grouping_sum():
    rows = [
        {"Part": "PN-100", "Qty": "2"},
        {"Part": "PN-100", "Qty": "3"},
    ]
    merged, duplicate_groups = apply_duplicate_strategy(
        rows,
        "Part",
        None,
        "Qty",
        "sum",
        NormalizationOptions(trim=True, case_insensitive=True),
    )
    assert duplicate_groups == 1
    assert merged[0]["Qty"] == "5"


def test_sku_excluded_from_candidates(app):
    with app.app_context():
        part_fields, _ = get_item_field_candidates()
    assert "sku" not in part_fields


def test_match_field_options_exclude_sku_and_non_string(app):
    with app.app_context():
        options = get_item_match_field_options()
    option_names = {opt.name for opt in options}
    assert "sku" not in option_names
    string_names = {opt.name for opt in options if opt.is_string}
    assert "min_stock" not in string_names


def test_ambiguous_matches_are_reported(app):
    with app.app_context():
        item_a = Item(sku="SKU-1", name="Widget", description="Alpha")
        item_b = Item(sku="SKU-2", name="Widget", description="Beta")
        db.session.add_all([item_a, item_b])
        db.session.commit()
        items = Item.query.order_by(Item.id).all()

    csv_text = "Item Name,Qty\nWidget,1\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    options = NormalizationOptions(trim=True, case_insensitive=True)
    matches, _, _ = match_rows(
        data.rows,
        "Item Name",
        "name",
        None,
        None,
        options,
        items,
    )
    assert matches[0].status == "ambiguous"


def test_match_preview_stats(app):
    with app.app_context():
        item_a = Item(sku="SKU-1", name="Widget A", description="Alpha")
        item_b = Item(sku="SKU-2", name="Widget B", description="Beta")
        db.session.add_all([item_a, item_b])
        db.session.commit()
        items = Item.query.order_by(Item.id).all()

    csv_text = "Name,Part,Qty\nWidget A,Alpha,1\nUnknown,Beta,2\n,,3\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    options = NormalizationOptions(trim=True, case_insensitive=True)
    matches, collision_count, collision_examples = match_rows(
        data.rows,
        "Name",
        "name",
        "Part",
        "description",
        options,
        items,
    )
    preview = summarize_match_preview(
        data.rows,
        matches,
        True,
        collision_count,
        collision_examples,
    )
    assert preview.matched_rows == 2
    assert preview.unmatched_rows == 0
    assert preview.empty_rows == 1


def test_reconciliation_uses_part_number(app, sample_items, sample_location):
    item_a, _ = sample_items
    with app.app_context():
        snapshot = InventorySnapshot(name="Math", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=item_a.id,
                system_total_qty=Decimal("5"),
            )
        )
        db.session.add(
            InventoryCountLine(
                snapshot_id=snapshot.id,
                item_id=item_a.id,
                location_id=sample_location.id,
                counted_qty=Decimal("5"),
            )
        )
        db.session.commit()

        rows = build_reconciliation_rows(snapshot.id)
        assert rows[0].part_number == "PN-100"


def test_export_endpoints_use_part_number(client, app, sample_items, sample_location):
    item_a, _ = sample_items
    with app.app_context():
        snapshot = InventorySnapshot(name="Export", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=item_a.id,
                system_total_qty=Decimal("3"),
            )
        )
        db.session.add(
            InventoryCountLine(
                snapshot_id=snapshot.id,
                item_id=item_a.id,
                location_id=sample_location.id,
                counted_qty=Decimal("3"),
            )
        )
        db.session.commit()

    location_response = client.get(
        f"/physical-inventory/snapshots/{snapshot.id}/export/location-sheet.csv"
    )
    assert location_response.status_code == 200
    assert b"PN-100" in location_response.data
    assert b"SKU-1" not in location_response.data

    reconciliation_response = client.get(
        f"/physical-inventory/snapshots/{snapshot.id}/export/reconciliation.csv"
    )
    assert reconciliation_response.status_code == 200
    assert b"PN-100" in reconciliation_response.data
    assert b"SKU-1" not in reconciliation_response.data
