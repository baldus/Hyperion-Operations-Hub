import csv
import io
import os
import sys
from decimal import Decimal

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
            "list_price": "12.34",
            "last_unit_cost": "9.87",
            "item_class": "Hardware",
            "notes": "Handle with care",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        item = Item.query.filter_by(name="Widget").one()
        assert item.notes == "Handle with care"
        assert item.list_price == Decimal("12.34")
        assert item.last_unit_cost == Decimal("9.87")
        assert item.item_class == "Hardware"


def test_edit_item_updates_notes(client, app):
    with app.app_context():
        item = Item(
            sku="200",
            name="Existing",
            notes="Old notes",
            list_price=Decimal("1.00"),
            last_unit_cost=Decimal("0.50"),
            item_class="Legacy",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "name": "Existing",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "list_price": "2.22",
            "last_unit_cost": "1.11",
            "item_class": "Updated",
            "notes": "Updated notes",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        updated = Item.query.get(item_id)
        assert updated.notes == "Updated notes"
        assert updated.list_price == Decimal("2.22")
        assert updated.last_unit_cost == Decimal("1.11")
        assert updated.item_class == "Updated"

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "name": "Existing",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "list_price": "",
            "last_unit_cost": "",
            "item_class": "",
            "notes": "",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        cleared = Item.query.get(item_id)
        assert cleared.notes is None
        assert cleared.list_price is None
        assert cleared.last_unit_cost is None
        assert cleared.item_class is None


def test_import_export_items_with_notes(client, app):
    with app.app_context():
        existing = Item(sku="300", name="Existing Item", notes="Legacy notes")
        db.session.add(existing)
        db.session.commit()

    csv_data = io.StringIO()
    writer = csv.writer(csv_data)
    writer.writerow(
        [
            "sku",
            "name",
            "type",
            "unit",
            "description",
            "min_stock",
            "notes",
            "list_price",
            "last_unit_cost",
            "item_class",
        ]
    )
    writer.writerow(
        [
            "300",
            "Existing Item",
            "",
            "ea",
            "Updated description",
            "12",
            "Updated legacy note",
            "5.50",
            "4.40",
            "Legacy",
        ]
    )
    writer.writerow(
        [
            "",
            "New Item",
            "",
            "ea",
            "Brand new",
            "3",
            "Fresh notes",
            "6.60",
            "5.50",
            "New",
        ]
    )

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
        assert updated_existing.list_price == Decimal("5.50")
        assert updated_existing.last_unit_cost == Decimal("4.40")
        assert updated_existing.item_class == "Legacy"

        new_item = Item.query.filter(Item.sku != "300").one()
        assert new_item.notes == "Fresh notes"
        assert new_item.list_price == Decimal("6.60")
        assert new_item.last_unit_cost == Decimal("5.50")
        assert new_item.item_class == "New"

    export_response = client.get("/inventory/items/export")
    assert export_response.status_code == 200

    exported = list(csv.reader(io.StringIO(export_response.data.decode("utf-8"))))
    header = exported[0]
    assert header == [
        "sku",
        "name",
        "type",
        "unit",
        "description",
        "min_stock",
        "notes",
        "list_price",
        "last_unit_cost",
        "item_class",
    ]

    rows = {row[0]: row for row in exported[1:]}  # keyed by sku
    assert rows["300"][6] == "Updated legacy note"
    assert rows["300"][7] == "5.50"
    assert rows["300"][8] == "4.40"
    assert rows["300"][9] == "Legacy"

    # new SKU is auto-generated; grab its notes from the remaining row
    new_rows = [row for sku, row in rows.items() if sku != "300"]
    assert len(new_rows) == 1
    assert new_rows[0][6] == "Fresh notes"
    assert new_rows[0][7] == "6.60"
    assert new_rows[0][8] == "5.50"
    assert new_rows[0][9] == "New"


def test_inventory_scan_page(client):
    response = client.get("/inventory/scan")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "cameraPreview" in body
