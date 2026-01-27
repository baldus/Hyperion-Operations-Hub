import io
import json
import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotImportIssue,
    InventorySnapshotLine,
    Item,
    Location,
    Movement,
)
from invapp.physical_inventory.services import (
    apply_duplicate_strategy,
    build_item_lookup,
    build_reconciliation_rows,
    ensure_count_lines_for_snapshot,
    get_item_field_candidates,
    get_item_match_field_options,
    match_rows,
    NormalizationOptions,
    normalize_import_row,
    MAX_FIELD_LEN,
    MAX_ROW_BYTES,
    parse_import_bytes,
    suggest_column_mappings,
    summarize_match_preview,
)

from invapp.physical_inventory.routes import _store_import_payload


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
        return item_a.id, item_b.id


@pytest.fixture
def sample_location(app):
    with app.app_context():
        location = Location(code="A1", description="Main Rack")
        db.session.add(location)
        db.session.commit()
        return location.id


def test_parse_import_csv():
    csv_text = "Part Number,Qty\nPN-100,5\n"
    data, errors = parse_import_bytes("snapshot.csv", csv_text.encode("utf-8"))
    assert errors == []
    assert data is not None
    assert data.headers == ["Part Number", "Qty"]
    assert data.rows[0]["Part Number"] == "PN-100"


def test_parse_import_xlsx():
    Workbook = pytest.importorskip("openpyxl").Workbook
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
    item_a_id, _ = sample_items
    with app.app_context():
        snapshot = InventorySnapshot(name="Math", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=item_a_id,
                system_total_qty=Decimal("5"),
            )
        )
        db.session.add(
            InventoryCountLine(
                snapshot_id=snapshot.id,
                item_id=item_a_id,
                location_id=sample_location,
                counted_qty=Decimal("5"),
            )
        )
        db.session.commit()

        rows = build_reconciliation_rows(snapshot.id)
        assert rows[0].part_number == "PN-100"


def test_export_endpoints_use_part_number(client, app, sample_items, sample_location):
    item_a_id, _ = sample_items
    with app.app_context():
        snapshot = InventorySnapshot(name="Export", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=item_a_id,
                system_total_qty=Decimal("3"),
            )
        )
        db.session.add(
            InventoryCountLine(
                snapshot_id=snapshot.id,
                item_id=item_a_id,
                location_id=sample_location,
                counted_qty=Decimal("3"),
            )
        )
        db.session.commit()
        snapshot_id = snapshot.id

    location_response = client.get(
        f"/physical-inventory/snapshots/{snapshot_id}/export/location-sheet.csv"
    )
    assert location_response.status_code == 200
    assert b"PN-100" in location_response.data
    assert b"SKU-1" not in location_response.data

    reconciliation_response = client.get(
        f"/physical-inventory/snapshots/{snapshot_id}/export/reconciliation.csv"
    )
    assert reconciliation_response.status_code == 200
    assert b"PN-100" in reconciliation_response.data
    assert b"SKU-1" not in reconciliation_response.data


def test_import_issue_allows_large_payload(app, sample_items):
    item_a_id, _ = sample_items
    large_value = "X" * 600
    row_data = {"Item Name": large_value, "Extra Notes": large_value}
    with app.app_context():
        snapshot = InventorySnapshot(name="Large", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        issue = InventorySnapshotImportIssue(
            snapshot_id=snapshot.id,
            row_index=1,
            reason="no match",
            primary_value=large_value,
            secondary_value=large_value,
            row_data=normalize_import_row(row_data),
        )
        db.session.add(issue)
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=item_a_id,
                system_total_qty=Decimal("1"),
            )
        )
        db.session.commit()

        stored = InventorySnapshotImportIssue.query.filter_by(snapshot_id=snapshot.id).one()
        assert stored.primary_value == large_value
        assert stored.row_data["Item Name"] == large_value


def test_normalize_row_data_invalid_json():
    result = normalize_import_row("not json")
    assert result["_extras"]["raw"] == "not json"
    assert result["_meta"]["invalid_json"] is True


def test_normalize_import_row_extras_and_truncation():
    long_value = "X" * (MAX_FIELD_LEN + 10)
    row = {
        "Item Name": "Widget",
        "": "blank",
        "Extra Field": long_value,
    }
    normalized = normalize_import_row(row)
    assert normalized["Item Name"] == "Widget"
    assert "_extras" in normalized
    assert normalized["_extras"]["Extra Field"]["_truncated"] is True
    assert normalized["_meta"]["blank_header_count"] == 1


def test_normalize_import_row_json_string():
    payload = {"Item Name": "Widget", "Extra": "X" * 400}
    normalized = normalize_import_row(json.dumps(payload))
    assert normalized["Item Name"] == "Widget"
    assert "Extra" in normalized["_extras"]


def test_ensure_count_lines_no_autoflush(app):
    with app.app_context():
        location = Location(code="B2", description="Overflow")
        item = Item(sku="SKU-9", name="Widget Z", description="Large")
        db.session.add_all([location, item])
        db.session.flush()
        item.default_location_id = location.id
        snapshot = InventorySnapshot(name="AutoFlush", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        line = InventorySnapshotLine(
            snapshot_id=snapshot.id,
            item_id=item.id,
            system_total_qty=Decimal("2"),
        )
        issue = InventorySnapshotImportIssue(
            snapshot_id=snapshot.id,
            row_index=1,
            reason="ambiguous",
            primary_value="W" * 600,
            secondary_value="Z" * 600,
            row_data=normalize_import_row({"Item Name": "W" * 600}),
        )
        db.session.add_all([line, issue])

        created = ensure_count_lines_for_snapshot(snapshot)
        assert issue in db.session.new
        assert created >= 0


def test_create_snapshot_large_issue_payload(client, app):
    long_name = "NAME-" + ("X" * 600)
    payload = {
        "headers": ["Item Name", "Qty"],
        "rows": [{"Item Name": long_name, "Qty": "5"}],
        "normalized_headers": ["item_name", "qty"],
    }
    with app.app_context():
        import_token = _store_import_payload(payload)

    response = client.post(
        "/physical-inventory/snapshots/new",
        data={
            "step": "commit",
            "import_token": import_token,
            "primary_upload_column": "Item Name",
            "primary_item_field": "name",
            "quantity_column": "Qty",
            "duplicate_strategy": "sum",
            "normalize_trim": "1",
            "normalize_case": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        snapshot = InventorySnapshot.query.order_by(InventorySnapshot.id.desc()).first()
        assert snapshot is not None
        issues = InventorySnapshotImportIssue.query.filter_by(snapshot_id=snapshot.id).all()
        assert len(issues) == 1
        assert len(issues[0].primary_value or "") > 255
        diagnostics = snapshot.import_diagnostics[0]
        assert diagnostics.issue_count_total == 1
        assert diagnostics.schema_signature is not None


def test_normalize_import_row_compacts_large_payload():
    row = {"Item Name": "Widget"}
    for idx in range(200):
        row[f"Extra {idx}"] = "X" * 1000
    normalized = normalize_import_row(row)
    row_bytes = len(json.dumps(normalized, default=str))
    assert row_bytes <= MAX_ROW_BYTES
    assert normalized["_meta"]["row_data_compacted"] is True
    assert len(normalized["_extras"]) <= 50
