import os
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.home_overview import get_incoming_and_overdue_items
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


def _add_request(*, eta_date, status, title="Widget", quantity=Decimal("5.00")):
    request = PurchaseRequest(
        title=title,
        requested_by="Planner",
        quantity=quantity,
        eta_date=eta_date,
        status=status,
    )
    db.session.add(request)
    return request


def test_incoming_and_overdue_items_classification(app):
    today = date(2024, 5, 20)
    with app.app_context():
        overdue = _add_request(
            eta_date=today - timedelta(days=1),
            status=PurchaseRequest.STATUS_NEW,
            title="Overdue Item",
        )
        incoming = _add_request(
            eta_date=today + timedelta(days=1),
            status=PurchaseRequest.STATUS_ORDERED,
            title="Incoming Item",
        )
        _add_request(
            eta_date=today - timedelta(days=1),
            status=PurchaseRequest.STATUS_RECEIVED,
            title="Received Item",
        )
        _add_request(
            eta_date=today + timedelta(days=5),
            status=PurchaseRequest.STATUS_NEW,
            title="Later Item",
        )
        db.session.commit()

        overdue_items, incoming_items = get_incoming_and_overdue_items(today=today)

        assert [item.id for item in overdue_items] == [overdue.id]
        assert [item.id for item in incoming_items] == [incoming.id]
