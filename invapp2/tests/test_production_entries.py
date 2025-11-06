import os
import sys

import pytest
from sqlalchemy.exc import OperationalError

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import ProductionCustomer
from invapp.routes import production


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        production._ensure_default_customers()
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


def _failing_commit(*_args, **_kwargs):
    raise OperationalError("INSERT", {}, Exception("db down"))


def test_additional_entry_handles_database_error(client, app, monkeypatch):
    monkeypatch.setattr(db.session, "commit", _failing_commit)

    response = client.post(
        "/production/daily-entry/additional",
        data={
            "entry_date": "2024-01-01",
            "additional_employees": "2",
            "additional_hours_ot": "1.5",
            "controllers_4_stop": "1",
            "controllers_6_stop": "1",
            "door_locks_lh": "0",
            "door_locks_rh": "0",
        },
    )

    assert response.status_code == 200
    page = response.data.decode()
    assert (
        "Unable to save additional production totals because the database connection was unavailable"
        in page
    )


def test_gates_entry_handles_database_error(client, app, monkeypatch):
    with app.app_context():
        customer = ProductionCustomer.query.first()
        assert customer is not None

    monkeypatch.setattr(db.session, "commit", _failing_commit)

    response = client.post(
        "/production/daily-entry/gates",
        data={
            "entry_date": "2024-01-01",
            f"gates_packaged_{customer.id}": "5",
            "gates_employees": "3",
            "gates_hours_ot": "1.25",
            "completion_id": ["", "", ""],
            "completion_order_number": ["", "", ""],
            "completion_customer": ["", "", ""],
            "completion_gate_count": ["", "", ""],
            "completion_po_number": ["", "", ""],
        },
    )

    assert response.status_code == 200
    page = response.data.decode()
    assert (
        "Unable to save gates packaged totals because the database connection was unavailable"
        in page
    )
