import csv
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


def _seed_snapshot(app):
    with app.app_context():
        loc_a = Location(code="1-A-1", description="Aisle A")
        loc_b = Location(code="1-B-2", description="Aisle B")
        item_a = Item(sku="SKU-A", name="Item A", description="Desc A", default_location=loc_a)
        item_b = Item(sku="SKU-B", name="Item B", description="Desc B", default_location=loc_b)
        snapshot = PhysicalInventorySnapshot(
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            normalization_options={},
            duplicate_strategy="sum",
            total_rows=2,
            matched_rows=2,
            unmatched_rows=0,
            ambiguous_rows=0,
            created_items=0,
        )
        db.session.add_all([loc_a, loc_b, item_a, item_b, snapshot])
        db.session.flush()
        db.session.add_all(
            [
                PhysicalInventorySnapshotLine(
                    snapshot_id=snapshot.id, item_id=item_a.id, erp_quantity=Decimal("5")
                ),
                PhysicalInventorySnapshotLine(
                    snapshot_id=snapshot.id, item_id=item_b.id, erp_quantity=Decimal("3")
                ),
            ]
        )
        db.session.commit()
        return snapshot.id


def test_count_sheet_csv_includes_sku(client, app):
    snapshot_id = _seed_snapshot(app)
    response = client.get(f"/inventory/physical-inventory/{snapshot_id}/count-sheet.csv")
    assert response.status_code == 200
    lines = response.data.decode("utf-8").splitlines()
    reader = csv.reader(lines)
    headers = next(reader)
    assert "SKU" in headers
    first_row = next(reader)
    assert "SKU-A" in first_row


def test_count_sheet_export_by_aisle_zip(client, app):
    snapshot_id = _seed_snapshot(app)
    response = client.get(
        f"/inventory/physical-inventory/{snapshot_id}/count-sheet-aisles.zip"
    )
    assert response.status_code == 200
    buffer = io.BytesIO(response.data)
    with zipfile.ZipFile(buffer) as zip_file:
        names = sorted(zip_file.namelist())
        assert f"count_sheet_snapshot_{snapshot_id}_aisle_A.csv" in names
        assert f"count_sheet_snapshot_{snapshot_id}_aisle_B.csv" in names
        csv_data = zip_file.read(f"count_sheet_snapshot_{snapshot_id}_aisle_A.csv").decode(
            "utf-8"
        )
        assert "SKU" in csv_data
        assert "Item A" in csv_data
