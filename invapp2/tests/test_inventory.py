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
