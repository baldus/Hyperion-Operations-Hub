import io
import os
import sys
import zipfile
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, PhysicalInventorySnapshot, PhysicalInventorySnapshotLine
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


def _create_snapshot_with_line():
    location = Location(code="1-A-1", description="Rack A1")
    item = Item(sku="SKU-1", name="Widget", description="Widget desc")
    item.default_location = location
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
        matched_rows=1,
        unmatched_rows=0,
        ambiguous_rows=0,
    )
    db.session.add_all([location, item, snapshot])
    db.session.flush()
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
        snapshot_id = _create_snapshot_with_line()

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
        snapshot_id = _create_snapshot_with_line()

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
