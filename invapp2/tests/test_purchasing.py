import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import PurchaseRequest, Role, User


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


def test_purchasing_role_seeded(app):
    with app.app_context():
        assert Role.query.filter_by(name="purchasing").first() is not None


def test_create_purchase_request_flow(app, client):
    response = client.post(
        "/purchasing/new",
        data={
            "title": "Aluminum Plate",
            "description": "Needed for upcoming production run.",
            "quantity": "25",
            "unit": "ea",
            "needed_by": "2024-05-20",
            "requested_by": "Production",
            "supplier_name": "Alloy Supply",
            "supplier_contact": "sales@alloy.example",
            "notes": "Check for volume discount.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Aluminum Plate" in response.data
    assert b"Purchase request logged" in response.data

    with app.app_context():
        stored = PurchaseRequest.query.one()
        assert stored.title == "Aluminum Plate"
        assert stored.status == PurchaseRequest.STATUS_NEW
        assert stored.requested_by == "Production"
        assert stored.quantity == Decimal("25.00")
        assert stored.unit == "ea"
        assert stored.needed_by.isoformat() == "2024-05-20"
        assert stored.supplier_name == "Alloy Supply"
        assert stored.supplier_contact == "sales@alloy.example"
        assert stored.notes == "Check for volume discount."
def test_update_requires_edit_role(app):
    client = app.test_client()
    with app.app_context():
        viewer_role = Role.query.filter_by(name="viewer").first()
        if viewer_role is None:
            viewer_role = Role(name="viewer", description="Viewer")
            db.session.add(viewer_role)
            db.session.flush()
        viewer_user = User(username="viewer-only")
        viewer_user.set_password("secret")
        viewer_user.roles = [viewer_role]
        db.session.add(viewer_user)
        request_record = PurchaseRequest(title="Viton seals", requested_by="Maintenance")
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    client.post(
        "/auth/login",
        data={"username": "viewer-only", "password": "secret"},
        follow_redirects=True,
    )

    viewer_response = client.post(
        f"/purchasing/{request_id}/update",
        data={"status": PurchaseRequest.STATUS_ORDERED},
    )
    assert viewer_response.status_code == 403

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )

    admin_response = client.post(
        f"/purchasing/{request_id}/update",
        data={
            "status": PurchaseRequest.STATUS_ORDERED,
            "requested_by": "Maintenance",
            "title": "Viton seals",
            "quantity": "10",
            "unit": "pack",
            "needed_by": "2024-06-01",
            "eta_date": "2024-06-10",
            "supplier_name": "Seal World",
            "supplier_contact": "rep@sealworld.test",
            "purchase_order_number": "PO-7788",
            "notes": "Confirmed delivery",
        },
        follow_redirects=False,
    )
    assert admin_response.status_code == 302

    detail_response = client.get(admin_response.headers["Location"], follow_redirects=True)
    assert detail_response.status_code == 200
    assert b"Purchase request updated" in detail_response.data

    with app.app_context():
        refreshed = db.session.get(PurchaseRequest, request_id)
        assert refreshed.status == PurchaseRequest.STATUS_ORDERED
        assert refreshed.quantity == Decimal("10.00")
        assert refreshed.unit == "pack"
        assert refreshed.needed_by.isoformat() == "2024-06-01"
        assert refreshed.eta_date.isoformat() == "2024-06-10"
        assert refreshed.supplier_name == "Seal World"
        assert refreshed.supplier_contact == "rep@sealworld.test"
        assert refreshed.purchase_order_number == "PO-7788"
        assert refreshed.notes == "Confirmed delivery"


def test_purchase_request_receive_link_prefills_receiving(app, client):
    with app.app_context():
        request_record = PurchaseRequest(
            title="ABC123 – Widget",
            requested_by="Receiver",
            quantity=Decimal("5.00"),
            purchase_order_number="PO-1234",
        )
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    response = client.get(f"/purchasing/{request_id}")
    assert response.status_code == 200
    assert b"/inventory/receiving?sku=ABC123" in response.data
    assert b"qty=5" in response.data
    assert b"person=Receiver" in response.data
    assert b"po_number=PO-1234" in response.data
