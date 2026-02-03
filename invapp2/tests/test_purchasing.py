import os
import re
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import PurchaseRequest, PurchaseRequestDeleteAudit, Role, User


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
    assert b"Item shortage logged" in response.data

    with app.app_context():
        stored = PurchaseRequest.query.filter_by(title="Aluminum Plate").one()
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
    assert b"Item shortage updated" in detail_response.data

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


def test_update_shipped_from_supplier_date_persists(app, client):
    with app.app_context():
        request_record = PurchaseRequest(title="Tape", requested_by="Ops")
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    response = client.post(
        f"/purchasing/{request_id}/update",
        data={
            "status": PurchaseRequest.STATUS_NEW,
            "title": "Tape",
            "requested_by": "Ops",
            "shipped_from_supplier_date": "2024-07-15",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Item shortage updated" in response.data

    with app.app_context():
        refreshed = db.session.get(PurchaseRequest, request_id)
        assert refreshed.shipped_from_supplier_date.isoformat() == "2024-07-15"


def test_update_shipped_from_supplier_date_blank_clears(app, client):
    with app.app_context():
        request_record = PurchaseRequest(
            title="Film",
            requested_by="Ops",
            shipped_from_supplier_date=date(2024, 7, 10),
        )
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    response = client.post(
        f"/purchasing/{request_id}/update",
        data={
            "status": PurchaseRequest.STATUS_NEW,
            "title": "Film",
            "requested_by": "Ops",
            "shipped_from_supplier_date": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        refreshed = db.session.get(PurchaseRequest, request_id)
        assert refreshed.shipped_from_supplier_date is None


def test_update_shipped_from_supplier_date_invalid_rejected(app, client):
    with app.app_context():
        request_record = PurchaseRequest(
            title="Foam",
            requested_by="Ops",
            shipped_from_supplier_date=date(2024, 7, 1),
        )
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    response = client.post(
        f"/purchasing/{request_id}/update",
        data={
            "status": PurchaseRequest.STATUS_NEW,
            "title": "Foam",
            "requested_by": "Ops",
            "shipped_from_supplier_date": "2024-13-40",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Enter the shipped from supplier date in YYYY-MM-DD format." in response.data

    with app.app_context():
        refreshed = db.session.get(PurchaseRequest, request_id)
        assert refreshed.shipped_from_supplier_date == date(2024, 7, 1)


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


def _create_shortage(app, *, title: str = "Widgets") -> None:
    with app.app_context():
        request_record = PurchaseRequest(title=title, requested_by="Ops")
        db.session.add(request_record)
        db.session.commit()


def _get_superuser() -> User:
    return User.query.filter_by(username="superuser").one()


def _has_header(response_data: bytes, label: str) -> bool:
    pattern = rb"<th[^>]*>\s*%s\s*</th>" % re.escape(label.encode())
    return re.search(pattern, response_data) is not None


def _label_for(key: str) -> str:
    return key.replace("_", " ").title()


def test_shortage_columns_default_visible(app, client):
    _create_shortage(app, title="Default Columns")

    response = client.get("/purchasing/")

    assert response.status_code == 200
    assert _has_header(response.data, "Id")
    assert _has_header(response.data, "Title")
    assert _has_header(response.data, "Quantity")
    assert _has_header(response.data, "Needed By")
    assert _has_header(response.data, "Status")
    assert _has_header(response.data, "Supplier Name")
    assert _has_header(response.data, "Eta Date")
    assert _has_header(response.data, "Requested By")
    assert _has_header(response.data, "Updated At")


def test_shortage_columns_save_preference(app, client):
    _create_shortage(app, title="Custom Columns")

    response = client.post(
        "/purchasing/shortages/columns",
        data={"columns": ["id", "status"], "action": "save"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Column preferences saved" in response.data
    assert _has_header(response.data, "Id")
    assert _has_header(response.data, "Status")
    assert not _has_header(response.data, "Title")


def test_shortage_columns_table_updates_with_preference(app, client):
    _create_shortage(app, title="Visible Column Update")

    response = client.post(
        "/purchasing/shortages/columns",
        data={"columns": ["item_number", "requested_by"], "action": "save"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert _has_header(response.data, "Item Number")
    assert _has_header(response.data, "Requested By")
    assert not _has_header(response.data, "Title")


def test_shortage_columns_save_all_columns(app, client):
    _create_shortage(app, title="All Columns")
    all_columns = [column.key for column in PurchaseRequest.__table__.columns]

    response = client.post(
        "/purchasing/shortages/columns",
        data={"columns": all_columns, "action": "save"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Column preferences saved" in response.data
    for column_key in all_columns:
        assert _has_header(response.data, _label_for(column_key))


def test_shortage_columns_invalid_preference_falls_back(app, client):
    _create_shortage(app, title="Bad Pref")
    with app.app_context():
        user = _get_superuser()
        user.user_settings = {"purchasing": {"shortages_visible_columns": "not-a-list"}}
        db.session.commit()

    response = client.get("/purchasing/")

    assert response.status_code == 200
    assert _has_header(response.data, "Title")
    assert _has_header(response.data, "Quantity")


def test_shortage_columns_unknown_keys_ignored(app, client):
    _create_shortage(app, title="Unknown Keys")
    with app.app_context():
        user = _get_superuser()
        user.user_settings = {
            "purchasing": {"shortages_visible_columns": ["id", "bogus_key", "title"]}
        }
        db.session.commit()

    response = client.get("/purchasing/")

    assert response.status_code == 200
    assert _has_header(response.data, "Id")
    assert _has_header(response.data, "Title")
    assert not _has_header(response.data, "Bogus Key")


def test_shortage_columns_reset_preference(app, client):
    _create_shortage(app, title="Reset Pref")
    with app.app_context():
        user = _get_superuser()
        user.user_settings = {"purchasing": {"shortages_visible_columns": ["id"]}}
        db.session.commit()

    response = client.post(
        "/purchasing/shortages/columns",
        data={"action": "reset"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Column preferences cleared" in response.data
    assert _has_header(response.data, "Title")


def test_delete_purchase_request_as_superuser(app, client):
    with app.app_context():
        initial_count = PurchaseRequest.query.count()
        request_record = PurchaseRequest(
            title="Delete Me",
            item_number="DEL-100",
            requested_by="Ops",
        )
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    confirm_response = client.get(f"/purchasing/{request_id}/delete/confirm")
    assert confirm_response.status_code == 200
    match = re.search(
        r'name="csrf_token" value="([^"]+)"', confirm_response.data.decode("utf-8")
    )
    assert match is not None
    csrf_token = match.group(1)

    delete_response = client.post(
        f"/purchasing/{request_id}/delete",
        data={"csrf_token": csrf_token, "delete_reason": "Duplicate request"},
        follow_redirects=False,
    )
    assert delete_response.status_code == 302

    with app.app_context():
        assert PurchaseRequest.query.count() == initial_count
        audit = PurchaseRequestDeleteAudit.query.one()
        assert audit.purchase_request_id == request_id
        assert audit.item_number == "DEL-100"
        assert audit.title == "Delete Me"
        assert audit.delete_reason == "Duplicate request"
        assert audit.deleted_by_username == "superuser"


def test_delete_requires_superuser(app):
    client = app.test_client()
    with app.app_context():
        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)
            db.session.flush()
        admin_user = User(username="admin-user")
        admin_user.set_password("secret")
        admin_user.roles = [admin_role]
        db.session.add(admin_user)
        request_record = PurchaseRequest(title="Nope", requested_by="Ops")
        db.session.add(request_record)
        db.session.commit()
        request_id = request_record.id

    client.post(
        "/auth/login",
        data={"username": "admin-user", "password": "secret"},
        follow_redirects=True,
    )

    response = client.post(f"/purchasing/{request_id}/delete")
    assert response.status_code == 403


def test_delete_missing_request_returns_404(app, client):
    response = client.post("/purchasing/999/delete")
    assert response.status_code == 404
