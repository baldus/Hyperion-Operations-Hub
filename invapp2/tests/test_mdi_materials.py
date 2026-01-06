import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import PurchaseRequest


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SEED_PURCHASE_REQUESTS": False,
        }
    )
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


def test_materials_summary_endpoint_aggregates_statuses(app, client):
    with app.app_context():
        db.session.add_all(
            [
                PurchaseRequest(
                    title="New shortage",
                    requested_by="Ops",
                    status=PurchaseRequest.STATUS_NEW,
                    quantity=Decimal("12.50"),
                ),
                PurchaseRequest(
                    title="Waiting shortage",
                    requested_by="Ops",
                    status=PurchaseRequest.STATUS_WAITING,
                    quantity=Decimal("7.00"),
                ),
                PurchaseRequest(
                    title="Ordered shortage",
                    requested_by="Ops",
                    status=PurchaseRequest.STATUS_ORDERED,
                    quantity=Decimal("3.00"),
                ),
                PurchaseRequest(
                    title="Unexpected status",
                    requested_by="Ops",
                    status="open",
                    quantity=Decimal("1.00"),
                ),
            ]
        )
        db.session.commit()

    response = client.get("/api/mdi/materials/summary")
    assert response.status_code == 200
    data = response.get_json()

    assert data["total_count"] == 4
    assert data["total_qty"] == 23.5
    assert data["last_updated"]

    by_status = {entry["status"]: entry for entry in data["by_status"]}
    assert by_status["New"]["count"] == 1
    assert by_status["New"]["qty_total"] == 12.5
    assert by_status["Waiting on Supplier"]["count"] == 1
    assert by_status["Ordered"]["count"] == 1
    assert by_status["Open"]["count"] == 1
    assert "open" in by_status["Open"]["status_values"]
