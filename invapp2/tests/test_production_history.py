import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    ProductionCustomer,
    ProductionDailyCustomerTotal,
    ProductionDailyGateCompletion,
    ProductionDailyRecord,
    ProductionOutputFormula,
)
from invapp.routes import production


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        production._ensure_default_customers()
        production._ensure_output_formula()
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


def _create_sample_record() -> date:
    today = date.today()
    customer = ProductionCustomer.query.filter_by(is_other_bucket=False).first()
    record = ProductionDailyRecord(
        entry_date=today,
        day_of_week=today.strftime("%A"),
        gates_employees=2,
        gates_hours_ot=Decimal("1.00"),
        controllers_4_stop=1,
        controllers_6_stop=1,
        door_locks_lh=1,
        door_locks_rh=1,
        operators_produced=3,
        cops_produced=1,
    )
    db.session.add(record)
    db.session.flush()
    db.session.add(
        ProductionDailyCustomerTotal(
            record=record,
            customer=customer,
            gates_produced=10,
            gates_packaged=4,
        )
    )
    db.session.commit()
    return today


def test_history_displays_default_output_formula(client, app):
    with app.app_context():
        entry_date = _create_sample_record()

    response = client.get(
        "/production/history",
        query_string={
            "start_date": entry_date.isoformat(),
            "end_date": entry_date.isoformat(),
        },
    )
    assert response.status_code == 200
    page = response.data.decode()
    assert "0.82" in page
    assert "Combined Output: 14.00" in page
    assert "Labor Hours: 17.00" in page


def test_history_uses_custom_output_formula(client, app):
    with app.app_context():
        entry_date = _create_sample_record()
        setting = ProductionOutputFormula.query.first()
        setting.formula = "produced_only / hours"
        setting.variables = [
            {
                "name": "produced_only",
                "label": "Produced Only",
                "expression": "produced",
            },
            {
                "name": "hours",
                "label": "Hours",
                "expression": "employees * shift_hours + overtime",
            },
        ]
        db.session.commit()

    response = client.get(
        "/production/history",
        query_string={
            "start_date": entry_date.isoformat(),
            "end_date": entry_date.isoformat(),
        },
    )
    assert response.status_code == 200
    page = response.data.decode()
    assert "0.59" in page
    assert "Produced Only: 10.00" in page
    assert "Hours: 17.00" in page


def test_final_process_entry_creates_completion(client, app):
    target_date = date.today()

    response = client.post(
        "/production/final-process-entry",
        data={
            "entry_date": target_date.isoformat(),
            "order_number": "G-12345",
            "customer": "Example Customer",
            "gates_completed": "2",
            "po_number": "PO-9",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        record = ProductionDailyRecord.query.filter_by(entry_date=target_date).first()
        assert record is not None
        assert len(record.gate_completions) == 1
        completion = record.gate_completions[0]
        assert completion.order_number == "G-12345"
        assert completion.customer_name == "Example Customer"
        assert completion.gates_completed == 2
        assert completion.po_number == "PO-9"


def test_daily_entry_updates_gate_completion(client, app):
    target_date = date.today()

    client.post(
        "/production/final-process-entry",
        data={
            "entry_date": target_date.isoformat(),
            "order_number": "G-54321",
            "customer": "Initial Customer",
            "gates_completed": "1",
            "po_number": "PO-1",
        },
        follow_redirects=True,
    )

    with app.app_context():
        record = ProductionDailyRecord.query.filter_by(entry_date=target_date).first()
        assert record is not None
        completion = record.gate_completions[0]
        customers = ProductionCustomer.query.filter_by(is_active=True).all()

    form_data = {
        "entry_date": target_date.isoformat(),
        "gates_employees": "0",
        "gates_hours_ot": "0",
        "controllers_4_stop": "0",
        "controllers_6_stop": "0",
        "door_locks_lh": "0",
        "door_locks_rh": "0",
        "operators_produced": "0",
        "cops_produced": "0",
        "additional_employees": "0",
        "additional_hours_ot": "0",
        "daily_notes": "",
        "completion_id": str(completion.id),
        "completion_order_number": "G-54321",
        "completion_customer": "Updated Customer",
        "completion_gate_count": "4",
        "completion_po_number": "PO-77",
    }

    for customer in customers:
        form_data[f"gates_produced_{customer.id}"] = "0"
        form_data[f"gates_packaged_{customer.id}"] = "0"

    response = client.post(
        "/production/daily-entry",
        data=form_data,
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        updated_completion = ProductionDailyGateCompletion.query.get(completion.id)
        assert updated_completion is not None
        assert updated_completion.customer_name == "Updated Customer"
        assert updated_completion.gates_completed == 4
        assert updated_completion.po_number == "PO-77"

