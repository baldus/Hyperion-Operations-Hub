from __future__ import annotations

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


def _create_item_and_location(app):
    with app.app_context():
        item = Item(sku="RCV-TEST", name="Receiving Widget", unit="ea")
        location = Location(code="RCV-01", description="Receiving Dock")
        db.session.add_all([item, location])
        db.session.commit()
        return item.id, location.id


def test_receiving_add_creates_batch_and_movement(client, app):
    item_id, location_id = _create_item_and_location(app)

    response = client.post(
        "/receiving/add",
        data={
            "sku": "RCV-TEST",
            "qty": "7",
            "person": "Jordan",
            "po_number": "PO-1001",
            "location_id": str(location_id),
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["receipt_id"]
    assert payload["lot_number"].startswith("RCV-TEST-")
    assert "/label-preview" in payload["label_url"]

    with app.app_context():
        movement = db.session.get(Movement, payload["receipt_id"])
        assert movement is not None
        assert movement.item_id == item_id
        assert movement.location_id == location_id
        assert movement.quantity == 7
        assert movement.movement_type == "RECEIPT"
        assert movement.batch is not None
        assert movement.batch.lot_number == payload["lot_number"]
        assert movement.batch.quantity == 7
        assert movement.batch.purchase_order == "PO-1001"


def test_receiving_reprint_routes_use_batch_helper(client, app, monkeypatch):
    _, location_id = _create_item_and_location(app)

    create_response = client.post(
        "/receiving/add",
        data={
            "sku": "RCV-TEST",
            "qty": "4",
            "person": "Morgan",
            "po_number": "PO-2002",
            "location_id": str(location_id),
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    receipt_id = create_response.get_json()["receipt_id"]

    calls: list[tuple] = []

    def fake_print(batch, item, qty, location, po_number):
        calls.append((batch, item, qty, location, po_number))
        return True

    monkeypatch.setattr("invapp.routes.receiving._print_batch_receipt_label", fake_print)

    json_response = client.post(
        "/receiving/print-label",
        json={"receipt_id": receipt_id, "copies": 2},
    )

    assert json_response.status_code == 200
    json_payload = json_response.get_json()
    assert json_payload["printed"] is True
    assert len(calls) == 2

    batch, item, qty, location, po_number = calls[0]
    assert batch is not None
    assert item.sku == "RCV-TEST"
    assert qty == 4
    assert location.code == "RCV-01"
    assert po_number == "PO-2002"

    calls.clear()

    response = client.post(
        f"/receiving/{receipt_id}/reprint",
        data={"copies": "3"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert len(calls) == 3
