import io
import os
import sys
from datetime import date

import pytest

# ensure package path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from invapp.models import GateOrderDetail, ImportBatch, Order, OrderStatus


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


def _upload_payload(rows: str, filename: str = "orders.csv"):
    return {
        "orders_file": (io.BytesIO(rows.encode("utf-8")), filename),
        "action": "import",
    }


def test_import_creates_and_flags_missing(client, app):
    missing_order = Order(order_number="ORD-MISSING", status=OrderStatus.OPEN)
    missing_order.gate_details = GateOrderDetail(item_number="OLD", production_quantity=1)
    with app.app_context():
        db.session.add(missing_order)
        db.session.commit()

    csv_content = """SO/Proposal No.,Ship By,Customer,Item ID,Item Description,Qty on Order,Item Type
ORD-1001,2024-09-15,Acme,FG-100,Sample Description,5,Gates
ORD-1002,9/20/2024,Waypoint,FG-200,Another Item,3,Operators
"""
    resp = client.post(
        "/orders/import",
        data=_upload_payload(csv_content),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        imported = Order.query.filter_by(order_number="ORD-1001").first()
        assert imported is not None
        assert imported.customer_name == "Acme"
        assert imported.order_type == "Gates"
        assert imported.scheduled_ship_date == date(2024, 9, 15)
        assert imported.gate_details.production_quantity == 5
        assert "Imported Description: Sample Description" in (imported.general_notes or "")
        assert imported.needs_review is False
        assert imported.last_import_batch_id is not None

        updated = Order.query.filter_by(order_number="ORD-MISSING").first()
        assert updated.needs_review is True
        assert updated.review_reason == "Missing from latest import"
        assert updated.review_batch_id == imported.last_import_batch_id

        batch = ImportBatch.query.first()
        assert batch.row_count == 2
        assert batch.created_count == 2
        assert batch.updated_count == 0
        assert batch.skipped_count == 0


def test_reimport_updates_without_duplicate_notes(client, app):
    csv_content = """SO/Proposal No.,Ship By,Customer,Item ID,Item Description,Qty on Order,Item Type
ORD-2001,2024-09-01,Northwind,FG-400,Repeat Description,4,Gates
"""
    first = client.post(
        "/orders/import",
        data=_upload_payload(csv_content),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert first.status_code == 200

    # Re-import with a different quantity and ship date to trigger an update
    updated_csv = """SO/Proposal No.,Ship By,Customer,Item ID,Item Description,Qty on Order,Item Type
ORD-2001,9/05/2024,Northwind,FG-400,Repeat Description,6,Gates
"""
    second = client.post(
        "/orders/import",
        data=_upload_payload(updated_csv),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert second.status_code == 200

    with app.app_context():
        order = Order.query.filter_by(order_number="ORD-2001").one()
        assert order.gate_details.production_quantity == 6
        assert order.scheduled_ship_date == date(2024, 9, 5)
        notes = order.general_notes or ""
        assert notes.count("Imported Description: Repeat Description") == 1
        assert ImportBatch.query.count() == 2
