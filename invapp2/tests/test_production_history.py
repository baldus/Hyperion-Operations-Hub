import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    ProductionChartSettings,
    ProductionCustomer,
    ProductionDailyCustomerTotal,
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


def test_builder_state_endpoint(client, app):
    response = client.post(
        "/production/history/builder-state",
        json={"dimensions": ["customer", "shift"], "metric": "gates_produced"},
    )
    assert response.status_code == 200
    with app.app_context():
        settings = ProductionChartSettings.get_or_create()
        assert settings.custom_builder_state.get("dimensions") == [
            "customer",
            "shift",
        ]
        assert settings.custom_builder_state.get("metric") == "gates_produced"

