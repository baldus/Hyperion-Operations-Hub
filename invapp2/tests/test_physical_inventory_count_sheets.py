import io
import os
import sys
import zipfile

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, PhysicalInventorySnapshot, PhysicalInventorySnapshotLine
from invapp.routes.inventory import _store_import_csv
from invapp.utils.physical_inventory import get_location_aisle


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


def test_get_location_aisle_row_and_fallback(app):
    with app.app_context():
        row_location = Location(code="1-A-1")
        fallback_location = Location(code="ZONE5")

        assert get_location_aisle(row_location) == "A"
        assert get_location_aisle(fallback_location) == "ZONE5"


def test_count_sheet_csv_includes_sku(client, app):
    with app.app_context():
        location = Location(code="1-A-1", description="Aisle A")
        item = Item(sku="SKU-100", name="Widget", description="Test")
        db.session.add_all([location, item])
        db.session.commit()

        snapshot = PhysicalInventorySnapshot(
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            normalization_options={},
            duplicate_strategy="sum",
            total_rows=1,
            matched_rows=1,
            unmatched_rows=0,
            ambiguous_rows=0,
        )
        db.session.add(snapshot)
        db.session.flush()

        line = PhysicalInventorySnapshotLine(
            snapshot_id=snapshot.id,
            item_id=item.id,
            erp_quantity=5,
        )
        db.session.add(line)
        db.session.commit()

        response = client.get(
            f"/inventory/physical-inventory/{snapshot.id}/count-sheet.csv"
        )
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "SKU" in data.splitlines()[0]
        assert "SKU-100" in data


def test_export_by_aisle_zip(client, app):
    with app.app_context():
        location_a = Location(code="1-A-1", description="Aisle A")
        location_b = Location(code="1-B-1", description="Aisle B")
        item_a = Item(sku="SKU-A", name="Widget A", description="A", default_location=location_a)
        item_b = Item(sku="SKU-B", name="Widget B", description="B", default_location=location_b)
        db.session.add_all([location_a, location_b, item_a, item_b])
        db.session.commit()

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
        )
        db.session.add(snapshot)
        db.session.flush()

        db.session.add_all(
            [
                PhysicalInventorySnapshotLine(
                    snapshot_id=snapshot.id,
                    item_id=item_a.id,
                    erp_quantity=3,
                ),
                PhysicalInventorySnapshotLine(
                    snapshot_id=snapshot.id,
                    item_id=item_b.id,
                    erp_quantity=4,
                ),
            ]
        )
        db.session.commit()

        response = client.get(
            f"/inventory/physical-inventory/{snapshot.id}/count-sheets-by-aisle.zip"
        )
        assert response.status_code == 200

        with zipfile.ZipFile(io.BytesIO(response.data)) as zip_file:
            filenames = zip_file.namelist()
            assert f"count_sheet_snapshot_{snapshot.id}_aisle_A.csv" in filenames
            assert f"count_sheet_snapshot_{snapshot.id}_aisle_B.csv" in filenames
            content_a = zip_file.read(f"count_sheet_snapshot_{snapshot.id}_aisle_A.csv").decode()
            content_b = zip_file.read(f"count_sheet_snapshot_{snapshot.id}_aisle_B.csv").decode()
            assert "Widget A" in content_a
            assert "Widget B" in content_b


def test_create_missing_items_flow(client, app):
    with app.app_context():
        csv_text = "Item Name,Description,Quantity\nNew Item,New Desc,5\n"
        import_token = _store_import_csv("physical_inventory", csv_text)

    response = client.post(
        "/inventory/physical-inventory",
        data={
            "step": "mapping",
            "import_token": import_token,
            "primary_upload_column": "Item Name",
            "primary_item_field": "name",
            "secondary_upload_column": "Description",
            "secondary_item_field": "description",
            "quantity_column": "Quantity",
            "duplicate_strategy": "sum",
            "trim_whitespace": "on",
            "case_insensitive": "on",
            "create_missing_items": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        items = Item.query.all()
        assert len(items) == 1
        snapshot = PhysicalInventorySnapshot.query.one()
        assert snapshot.created_items_count == 1
        assert snapshot.matched_rows == 1
        assert snapshot.unmatched_rows == 0
        lines = PhysicalInventorySnapshotLine.query.filter_by(snapshot_id=snapshot.id).all()
        assert len(lines) == 1
