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


REQUIRED_AISLES_INPUT = [
    "A", "AL", "B", "C", "Controllers", "COP", "CTRL", "D", "DF", "E", "F", "g",
    "Gates", "H", "Hinges", "I", "J", "K", "L", "LOCK", "Locks", "M", "n", "O",
    "OPER", "Operators", "P", "Packaging", "Q", "S", "Selector", "SHF", "SLCTR", "v",
    "VA", "VB", "VF", "VO", "W", "WO", "X", "Y", "Z",
]
REQUIRED_AISLES_NORMALIZED = sorted({aisle.upper() for aisle in REQUIRED_AISLES_INPUT})


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
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


def _create_snapshot_for_required_aisles() -> int:
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
    db.session.add(snapshot)
    db.session.flush()

    for index, aisle in enumerate(REQUIRED_AISLES_INPUT, start=1):
        location = Location(code=f"1-{aisle}-001", description=f"Aisle {aisle}")
        item = Item(sku=f"SKU-{index:03d}", name=f"Widget {index}", description=f"Desc {index}")
        db.session.add_all([location, item])
        db.session.flush()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location.id,
                quantity=Decimal("1"),
                movement_type="RECEIPT",
            )
        )

    db.session.commit()
    return snapshot.id


def test_get_location_aisle_from_code_token_after_first_hyphen():
    location = Location(code="1-ctrl-1")
    aisle = get_location_aisle(location, {})
    assert aisle == "CTRL"


def test_get_location_aisle_invalid_code():
    location = Location(code="BAD")
    aisle = get_location_aisle(location, {})
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


def test_required_aisles_appear_in_count_sheet_groupings(client, app):
    with app.app_context():
        snapshot_id = _create_snapshot_for_required_aisles()

    html_response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/count-sheets-by-aisle"
    )
    assert html_response.status_code == 200
    html = html_response.get_data(as_text=True)

    for aisle in REQUIRED_AISLES_NORMALIZED:
        assert f'<option value="{aisle}"' in html

    zip_response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/export-count-sheets-by-aisle"
    )
    assert zip_response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(zip_response.data)) as archive:
        filenames = set(archive.namelist())
        for aisle in REQUIRED_AISLES_NORMALIZED:
            expected_name = f"count_sheet_snapshot_{snapshot_id}_aisle_{aisle}.csv"
            assert expected_name in filenames
