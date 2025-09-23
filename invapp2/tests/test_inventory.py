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
            "on_hand_quantity": "25",
            "current_cost": "7.77",
            "demonstrated_lead_time": "4.5",
            "make_buy": "MAKE",
            "abc_code": "A",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        item = Item.query.filter_by(name="Widget").one()
        assert item.notes == "Handle with care"
        assert item.list_price == Decimal("12.34")
        assert item.last_unit_cost == Decimal("9.87")
        assert item.item_class == "Hardware"
        assert item.on_hand_quantity == 25
        assert item.current_cost == Decimal("7.77")
        assert item.demonstrated_lead_time == Decimal("4.50")
        assert item.make_buy == "MAKE"
        assert item.abc_code == "A"


def test_edit_item_updates_notes(client, app):
    with app.app_context():
        item = Item(
            sku="200",
            name="Existing",
            notes="Old notes",
            list_price=Decimal("1.00"),
            last_unit_cost=Decimal("0.50"),
            item_class="Legacy",
            on_hand_quantity=7,
            current_cost=Decimal("1.23"),
            demonstrated_lead_time=Decimal("2.50"),
            make_buy="MAKE",
            abc_code="B",
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
            "on_hand_quantity": "12",
            "current_cost": "3.33",
            "demonstrated_lead_time": "6.25",
            "make_buy": "BUY",
            "abc_code": "C",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        updated = Item.query.get(item_id)
        assert updated.notes == "Updated notes"
        assert updated.list_price == Decimal("2.22")
        assert updated.last_unit_cost == Decimal("1.11")
        assert updated.item_class == "Updated"
        assert updated.on_hand_quantity == 12
        assert updated.current_cost == Decimal("3.33")
        assert updated.demonstrated_lead_time == Decimal("6.25")
        assert updated.make_buy == "BUY"
        assert updated.abc_code == "C"

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
            "on_hand_quantity": "",
            "current_cost": "",
            "demonstrated_lead_time": "",
            "make_buy": "",
            "abc_code": "",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        cleared = Item.query.get(item_id)
        assert cleared.notes is None
        assert cleared.list_price is None
        assert cleared.last_unit_cost is None
        assert cleared.item_class is None
        assert cleared.on_hand_quantity is None
        assert cleared.current_cost is None
        assert cleared.demonstrated_lead_time is None
        assert cleared.make_buy is None
        assert cleared.abc_code is None


def test_edit_item_requires_admin(client, app):
    with app.app_context():
        item = Item(sku="500", name="Admin Only")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.get(f"/inventory/item/{item_id}/edit")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert f"next=%2Finventory%2Fitem%2F{item_id}%2Fedit" in response.headers["Location"]


def test_delete_item_blocks_when_referenced(client, app):
    with app.app_context():
        location = Location(code="DEL-LOC")
        item = Item(sku="DEL-1", name="Delete Me")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()
        item_id = item.id

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(f"/inventory/item/{item_id}/delete")
    assert response.status_code == 302
    assert f"/inventory/item/{item_id}/edit" in response.headers["Location"]

    with app.app_context():
        assert Item.query.get(item_id) is not None


def test_delete_item_succeeds_without_references(client, app):
    with app.app_context():
        item = Item(sku="FREE-1", name="Free Item")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(f"/inventory/item/{item_id}/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/items")

    with app.app_context():
        assert Item.query.get(item_id) is None


def test_edit_location_requires_admin(client, app):
    with app.app_context():
        location = Location(code="EDIT-LOC", description="Old desc")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.get(f"/inventory/location/{location_id}/edit")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert f"next=%2Finventory%2Flocation%2F{location_id}%2Fedit" in response.headers["Location"]


def test_edit_location_updates(client, app):
    with app.app_context():
        location = Location(code="STAGE-1", description="Staging")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(
        f"/inventory/location/{location_id}/edit",
        data={"code": "STAGE-99", "description": "Updated"},
    )
    assert response.status_code == 302

    with app.app_context():
        updated = Location.query.get(location_id)
        assert updated.code == "STAGE-99"
        assert updated.description == "Updated"


def test_delete_location_blocks_when_movement_exists(client, app):
    with app.app_context():
        location = Location(code="BLOCK-1")
        item = Item(sku="BLOCK-ITEM", name="Block Item")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=10,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()
        location_id = location.id

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(f"/inventory/location/{location_id}/delete")
    assert response.status_code == 302
    assert f"/inventory/location/{location_id}/edit" in response.headers["Location"]

    with app.app_context():
        assert Location.query.get(location_id) is not None


def test_delete_location_succeeds_without_movement(client, app):
    with app.app_context():
        location = Location(code="FREE-LOC")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    with client.session_transaction() as session:
        session["is_admin"] = True

    response = client.post(f"/inventory/location/{location_id}/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/locations")

    with app.app_context():
        assert Location.query.get(location_id) is None


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
        assert updated_existing.on_hand_quantity is None
        assert updated_existing.current_cost is None
        assert updated_existing.demonstrated_lead_time is None
        assert updated_existing.make_buy is None
        assert updated_existing.abc_code is None

        new_item = Item.query.filter(Item.sku != "300").one()
        assert new_item.notes == "Fresh notes"
        assert new_item.list_price == Decimal("6.60")
        assert new_item.last_unit_cost == Decimal("5.50")
        assert new_item.item_class == "New"
        assert new_item.on_hand_quantity is None
        assert new_item.current_cost is None
        assert new_item.demonstrated_lead_time is None
        assert new_item.make_buy is None
        assert new_item.abc_code is None

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


def test_import_items_from_planning_spreadsheet(client, app):
    headers = [
        "KEY",
        "Item Number",
        "Item Name",
        "Warehouse Name",
        "Warehouse",
        "Lead Time",
        "Last Lead Time",
        "Demonstrated Lead Time",
        "UoM",
        "Current Cost",
        "Unique Item Number",
        "Abc Code",
        "Make/ Buy",
        "ERP ABC",
        "Buyer Planner",
        "Item",
        "EndDate",
        "UoM2",
        "ItemClassification",
        "Item Name3",
        "StartDate",
        "ABC (sug)",
        "LTM Max tooltip",
        "Year of StartDate",
        "Qty Con",
        "Months with Con",
        "Avg Con LT",
        "On Hand",
        "ERP MOQ",
        "ERP SS",
        "Rec SS",
        "Rec ROP",
        "SS Ratio",
        "CoV",
        "Turns",
        "MaxDemandLTM",
        "StdDevDemandLTM",
        "Rec EOQ",
        "Rec Inv $",
        "Rec ROP4",
        "Service Level",
    ]

    with app.app_context():
        existing = Item(
            sku="100",
            name="ITEM-001",
            description="Widget One",
            unit="ea",
            on_hand_quantity=5,
            current_cost=Decimal("1.00"),
            demonstrated_lead_time=Decimal("2.00"),
            make_buy="BUY",
            abc_code="Z",
        )
        db.session.add(existing)
        db.session.commit()

    existing_row = {
        "Item Number": "ITEM-001",
        "Item Name": "Widget One Updated",
        "UoM": "BX",
        "On Hand": 42,
        "Current Cost": 12.34,
        "Demonstrated Lead Time": 5.5,
        "Make/ Buy": "MAKE",
        "Abc Code": "A",
    }
    new_row = {
        "Item Number": "ITEM-002",
        "Item Name": "Widget Two",
        "UoM": "EA",
        "On Hand": 10,
        "Current Cost": 3.21,
        "Demonstrated Lead Time": 7,
        "Make/ Buy": "BUY",
        "Abc Code": "B",
    }

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(headers)
    writer.writerow([existing_row.get(column, "") for column in headers])
    writer.writerow([new_row.get(column, "") for column in headers])
    buffer = io.BytesIO(csv_buffer.getvalue().encode("utf-8"))

    response = client.post(
        "/inventory/items/import",
        data={"file": (buffer, "planning.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302

    with app.app_context():
        updated_existing = Item.query.filter_by(sku="100").one()
        assert updated_existing.description == "Widget One Updated"
        assert updated_existing.unit == "BX"
        assert updated_existing.on_hand_quantity == 42
        assert updated_existing.current_cost == Decimal("12.34")
        assert updated_existing.demonstrated_lead_time == Decimal("5.50")
        assert updated_existing.make_buy == "MAKE"
        assert updated_existing.abc_code == "A"

        new_item = Item.query.filter(Item.sku != "100").one()
        assert new_item.sku == "101"
        assert new_item.name == "ITEM-002"
        assert new_item.description == "Widget Two"
        assert new_item.unit == "EA"
        assert new_item.on_hand_quantity == 10
        assert new_item.current_cost == Decimal("3.21")
        assert new_item.demonstrated_lead_time == Decimal("7.00")
        assert new_item.make_buy == "BUY"
        assert new_item.abc_code == "B"


def test_inventory_scan_page(client):
    response = client.get("/inventory/scan")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "cameraPreview" in body
