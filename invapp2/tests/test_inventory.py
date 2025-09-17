import csv
import io
import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, Movement


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def stock_items(app):
    with app.app_context():
        location = Location(code="MAIN")
        low_item = Item(sku="LOW-1", name="Low Item", min_stock=100)
        near_item = Item(sku="NEAR-1", name="Near Item", min_stock=100)
        ok_item = Item(sku="OK-1", name="OK Item", min_stock=100)
        db.session.add_all([location, low_item, near_item, ok_item])
        db.session.commit()

        movements = [
            Movement(
                item_id=low_item.id,
                location_id=location.id,
                quantity=100,
                movement_type="ADJUST",
            ),
            Movement(
                item_id=near_item.id,
                location_id=location.id,
                quantity=110,
                movement_type="ADJUST",
            ),
            Movement(
                item_id=ok_item.id,
                location_id=location.id,
                quantity=140,
                movement_type="ADJUST",
            ),
        ]
        db.session.add_all(movements)
        db.session.commit()

        return {
            "low": low_item.sku,
            "near": near_item.sku,
            "ok": ok_item.sku,
        }


def test_list_stock_low_filter(client, stock_items):
    response = client.get("/inventory/stock?status=low")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert stock_items["low"] in page
    assert stock_items["near"] not in page
    assert stock_items["ok"] not in page


def test_list_stock_near_filter(client, stock_items):
    response = client.get("/inventory/stock?status=near")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert stock_items["low"] in page
    assert stock_items["near"] in page
    assert stock_items["ok"] not in page


def test_list_stock_all_filter(client, stock_items):
    response = client.get("/inventory/stock")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert stock_items["low"] in page
    assert stock_items["near"] in page
    assert stock_items["ok"] in page


def test_add_item_with_notes(client, app):
    response = client.post(
        "/inventory/item/add",
        data={
            "name": "Widget",
            "type": "Component",
            "unit": "ea",
            "description": "Sample widget",
            "min_stock": "5",
            "notes": "Handle with care",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        item = Item.query.filter_by(name="Widget").one()
        assert item.notes == "Handle with care"


def test_edit_item_updates_notes(client, app):
    with app.app_context():
        item = Item(sku="200", name="Existing", notes="Old notes")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "name": "Existing",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "notes": "Updated notes",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        updated = Item.query.get(item_id)
        assert updated.notes == "Updated notes"

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "name": "Existing",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "notes": "",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        cleared = Item.query.get(item_id)
        assert cleared.notes is None


def test_import_export_items_with_notes(client, app):
    with app.app_context():
        existing = Item(sku="300", name="Existing Item", notes="Legacy notes")
        db.session.add(existing)
        db.session.commit()

    csv_data = io.StringIO()
    writer = csv.writer(csv_data)
    writer.writerow(["sku", "name", "type", "unit", "description", "min_stock", "notes"])
    writer.writerow(["300", "Existing Item", "", "ea", "Updated description", "12", "Updated legacy note"])
    writer.writerow(["", "New Item", "", "ea", "Brand new", "3", "Fresh notes"])

    response = client.post(
        "/inventory/items/import",
        data={"file": (io.BytesIO(csv_data.getvalue().encode("utf-8")), "items.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302

    with app.app_context():
        updated_existing = Item.query.filter_by(sku="300").one()
        assert updated_existing.description == "Updated description"
        assert updated_existing.min_stock == 12
        assert updated_existing.notes == "Updated legacy note"

        new_item = Item.query.filter(Item.sku != "300").one()
        assert new_item.notes == "Fresh notes"

    export_response = client.get("/inventory/items/export")
    assert export_response.status_code == 200

    exported = list(csv.reader(io.StringIO(export_response.data.decode("utf-8"))))
    header = exported[0]
    assert header == ["sku", "name", "type", "unit", "description", "min_stock", "notes"]

    rows = {row[0]: row for row in exported[1:]}  # keyed by sku
    assert rows["300"][6] == "Updated legacy note"

    # new SKU is auto-generated; grab its notes from the remaining row
    new_rows = [row for sku, row in rows.items() if sku != "300"]
    assert len(new_rows) == 1
    assert new_rows[0][6] == "Fresh notes"
