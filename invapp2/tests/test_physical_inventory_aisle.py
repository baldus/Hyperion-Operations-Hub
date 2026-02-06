import io
import os
import sys
import zipfile
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    Item,
    Location,
    Movement,
    PhysicalInventorySnapshot,
    PhysicalInventorySnapshotLine,
)
from invapp.utils.physical_inventory_aisle import UNKNOWN_AISLE, get_location_aisle


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "PHYS_INV_AISLE_MODE": "row",
            "PHYS_INV_AISLE_REGEX": r"(?P<aisle>\d+)",
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


def _create_snapshot_with_stock(*, include_line: bool = True, source_filename: str | None = "erp.csv"):
    location = Location(code="1-A-1", description="Rack A1")
    item = Item(sku="SKU-1", name="Widget", description="Widget desc")
    snapshot = PhysicalInventorySnapshot(
        source_filename=source_filename,
        primary_upload_column="Item" if source_filename else "(none)",
        primary_item_field="name",
        secondary_upload_column=None,
        secondary_item_field=None,
        quantity_column="Qty" if source_filename else "(none)",
        normalization_options={},
        duplicate_strategy="sum",
        total_rows=1 if source_filename else 0,
        matched_rows=1 if include_line else 0,
        unmatched_rows=0,
        ambiguous_rows=0,
    )
    db.session.add_all([location, item, snapshot])
    db.session.flush()
    db.session.add(
        Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=Decimal("7"),
            movement_type="RECEIPT",
        )
    )
    if include_line:
        line = PhysicalInventorySnapshotLine(
            snapshot_id=snapshot.id,
            item_id=item.id,
            erp_quantity=Decimal("10"),
            counted_quantity=None,
        )
        db.session.add(line)
    db.session.commit()
    return snapshot.id


def test_get_location_aisle_row_mode():
    location = Location(code="1-A-1")
    aisle = get_location_aisle(location, {"PHYS_INV_AISLE_MODE": "row"})
    assert aisle == "A"


def test_get_location_aisle_level_mode():
    location = Location(code="1-A-1")
    aisle = get_location_aisle(location, {"PHYS_INV_AISLE_MODE": "level"})
    assert aisle == "1"


def test_get_location_aisle_prefix_mode():
    location = Location(code="12-XX-9")
    aisle = get_location_aisle(
        location,
        {
            "PHYS_INV_AISLE_MODE": "prefix",
            "PHYS_INV_AISLE_REGEX": r"^(?P<aisle>\d+)",
        },
    )
    assert aisle == "12"


def test_get_location_aisle_invalid_code():
    location = Location(code="BAD")
    aisle = get_location_aisle(location, {"PHYS_INV_AISLE_MODE": "row"})
    assert aisle == UNKNOWN_AISLE


def test_count_sheets_by_aisle_html(client, app):
    with app.app_context():
        snapshot_id = _create_snapshot_with_stock()

    response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/count-sheets-by-aisle"
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Select Aisle" in page
    assert "SKU" in page
    assert "<option value=\"A\" selected>" in page


def test_count_sheets_by_aisle_zip_export(client, app):
    with app.app_context():
        snapshot_id = _create_snapshot_with_stock()

    response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/export-count-sheets-by-aisle"
    )
    assert response.status_code == 200
    assert response.mimetype == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        filenames = archive.namelist()
        expected_name = f"count_sheet_snapshot_{snapshot_id}_aisle_A.csv"
        assert expected_name in filenames
        with archive.open(expected_name) as csv_file:
            header = csv_file.readline().decode("utf-8").strip()
            assert header.startswith("Aisle,Location Code,Location Description")


def test_count_sheets_work_without_erp_upload(client, app):
    with app.app_context():
        snapshot_id = _create_snapshot_with_stock(include_line=False, source_filename=None)

    html_response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/count-sheets-by-aisle"
    )
    assert html_response.status_code == 200
    page = html_response.get_data(as_text=True)
    assert "Widget" in page
    assert "1-A-1" in page

    zip_response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/export-count-sheets-by-aisle"
    )
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.data)) as archive:
        expected_name = f"count_sheet_snapshot_{snapshot_id}_aisle_A.csv"
        with archive.open(expected_name) as csv_file:
            csv_text = csv_file.read().decode("utf-8")
            assert "Widget" in csv_text
            assert "1-A-1" in csv_text


def test_count_sheets_include_ops_stock_when_erp_unmatched(client, app):
    with app.app_context():
        location = Location(code="1-A-1", description="Rack A1")
        item = Item(sku="SKU-UNMATCHED", name="Ops Only Widget", description="Not in ERP")
        snapshot = PhysicalInventorySnapshot(
            source_filename="erp.csv",
            primary_upload_column="Item",
            primary_item_field="name",
            secondary_upload_column=None,
            secondary_item_field=None,
            quantity_column="Qty",
            normalization_options={},
            duplicate_strategy="sum",
            total_rows=1,
            matched_rows=0,
            unmatched_rows=1,
            ambiguous_rows=0,
        )
        db.session.add_all([location, item, snapshot])
        db.session.flush()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location.id,
                quantity=Decimal("5"),
                movement_type="RECEIPT",
            )
        )
        db.session.commit()
        snapshot_id = snapshot.id

    response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/count-sheets-by-aisle"
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Ops Only Widget" in page
    assert "1-A-1" in page


def test_count_sheets_include_assigned_zero_items_and_all_locations(client, app):
    with app.app_context():
        loc_a = Location(code="1-A-1", description="Rack A1")
        loc_ctrl = Location(code="1-CTRL-1", description="Control")

        item_stocked = Item(sku="SKU-STOCK", name="Stocked Item", description="Has stock")
        item_assigned_zero = Item(
            sku="SKU-ZERO",
            name="Assigned Zero Item",
            description="Assigned to A",
            default_location=loc_a,
        )
        item_assigned_other = Item(
            sku="SKU-CTRL",
            name="Control Assigned Item",
            description="Assigned to CTRL",
            default_location=loc_ctrl,
        )

        snapshot = PhysicalInventorySnapshot(
            source_filename=None,
            primary_upload_column="(none)",
            primary_item_field="name",
            secondary_upload_column=None,
            secondary_item_field=None,
            quantity_column="(none)",
            normalization_options={},
            duplicate_strategy="sum",
            total_rows=0,
            matched_rows=0,
            unmatched_rows=0,
            ambiguous_rows=0,
        )

        db.session.add_all([loc_a, loc_ctrl, item_stocked, item_assigned_zero, item_assigned_other, snapshot])
        db.session.flush()

        db.session.add(
            Movement(
                item_id=item_stocked.id,
                location_id=loc_a.id,
                quantity=Decimal("4"),
                movement_type="RECEIPT",
            )
        )
        db.session.commit()
        snapshot_id = snapshot.id

    by_location = client.get(f"/inventory/physical-inventory/{snapshot_id}/count-sheet")
    assert by_location.status_code == 200
    by_location_page = by_location.get_data(as_text=True)
    assert "Stocked Item" in by_location_page
    assert "Assigned Zero Item" in by_location_page
    assert ">0<" in by_location_page

    by_aisle = client.get(f"/inventory/physical-inventory/{snapshot_id}/count-sheets-by-aisle")
    assert by_aisle.status_code == 200
    by_aisle_page = by_aisle.get_data(as_text=True)
    assert '<option value="A" selected>' in by_aisle_page
    assert '<option value="CTRL"' in by_aisle_page

    ctrl_aisle = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/count-sheets-by-aisle?aisle=CTRL"
    )
    ctrl_page = ctrl_aisle.get_data(as_text=True)
    assert "Control Assigned Item" in ctrl_page

    zip_response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/export-count-sheets-by-aisle"
    )
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.data)) as archive:
        filenames = archive.namelist()
        assert f"count_sheet_snapshot_{snapshot_id}_aisle_A.csv" in filenames
        assert f"count_sheet_snapshot_{snapshot_id}_aisle_CTRL.csv" in filenames

        with archive.open(f"count_sheet_snapshot_{snapshot_id}_aisle_A.csv") as csv_file:
            csv_text = csv_file.read().decode("utf-8")
            assert "Assigned Zero Item" in csv_text
            assert ",0," in csv_text

        with archive.open(f"count_sheet_snapshot_{snapshot_id}_aisle_CTRL.csv") as csv_file:
            csv_text = csv_file.read().decode("utf-8")
            assert "Control Assigned Item" in csv_text
            assert ",0," in csv_text
