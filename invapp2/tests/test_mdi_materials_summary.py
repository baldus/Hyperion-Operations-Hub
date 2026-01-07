import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.mdi.materials_summary import build_materials_summary
from invapp.models import PurchaseRequest


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        db.session.expire_on_commit = False
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


def _create_request(status, quantity):
    return PurchaseRequest(
        title=f"Test item {status}",
        requested_by="Tester",
        status=status,
        quantity=quantity,
    )


def _clear_purchase_requests():
    db.session.query(PurchaseRequest).delete()
    db.session.commit()


def test_build_materials_summary_aggregates_statuses(app):
    with app.app_context():
        _clear_purchase_requests()
        db.session.add(_create_request(PurchaseRequest.STATUS_NEW, Decimal("5")))
        db.session.add(_create_request(PurchaseRequest.STATUS_WAITING, Decimal("3")))
        db.session.add(_create_request(PurchaseRequest.STATUS_ORDERED, Decimal("2")))
        db.session.add(_create_request(PurchaseRequest.STATUS_RECEIVED, Decimal("4")))
        db.session.add(_create_request("on_hold", Decimal("1")))
        db.session.commit()

        summary = build_materials_summary()

    assert summary["total_count"] == 5
    assert summary["total_qty"] == 15.0

    by_status = {entry["status"]: entry for entry in summary["by_status"]}
    assert by_status["New"]["count"] == 1
    assert by_status["New"]["qty_total"] == 5.0
    assert by_status["Waiting on Supplier"]["count"] == 1
    assert by_status["Ordered"]["count"] == 1
    assert by_status["Received"]["count"] == 1
    assert by_status["On Hold"]["count"] == 1
    assert by_status["On Hold"]["status_filter"] is None


def test_materials_summary_endpoint_returns_payload(app, client):
    with app.app_context():
        _clear_purchase_requests()
        db.session.add(_create_request(PurchaseRequest.STATUS_NEW, Decimal("7")))
        db.session.commit()

    response = client.get("/api/mdi/materials/summary")
    assert response.status_code == 200

    payload = response.get_json()
    assert "by_status" in payload
    assert payload["total_count"] == 1
    assert payload["by_status"][0]["status"] == "New"
    assert payload["by_status"][0]["status_filter"] == PurchaseRequest.STATUS_NEW


def test_purchasing_home_accepts_multiple_status_filters(app, client):
    with app.app_context():
        _clear_purchase_requests()
        db.session.add(_create_request(PurchaseRequest.STATUS_WAITING, Decimal("1")))
        db.session.add(_create_request(PurchaseRequest.STATUS_ORDERED, Decimal("1")))
        db.session.add(_create_request(PurchaseRequest.STATUS_RECEIVED, Decimal("1")))
        db.session.commit()

    response = client.get("/purchasing?status=waiting,ordered", follow_redirects=True)
    assert response.status_code == 200
    assert b"Waiting on Supplier" in response.data
    assert b"Ordered" in response.data
    assert b"status-received" not in response.data
