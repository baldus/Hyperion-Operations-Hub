import os
import sys
from datetime import date

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import re

import pytest

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    ProductionCustomer,
    ProductionDailyCustomerTotal,
    ProductionDailyRecord,
    ProductionHistorySettings,
)


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


@pytest.fixture
def sample_data(app):
    with app.app_context():
        customer = ProductionCustomer(name="Alpha", color="#123456", is_active=True)
        db.session.add(customer)
        db.session.commit()

        other = ProductionCustomer.query.filter_by(is_other_bucket=True).first()

        today = date.today()
        record = ProductionDailyRecord(
            entry_date=today,
            day_of_week="Tuesday",
            gates_employees=1,
            gates_hours_ot=0,
        )
        db.session.add(record)
        db.session.flush()

        total = ProductionDailyCustomerTotal(
            record_id=record.id,
            customer_id=customer.id,
            gates_produced=10,
            gates_packaged=0,
        )
        db.session.add(total)
        db.session.commit()

        return {
            "customer_id": customer.id,
            "record_id": record.id,
        }


def test_history_uses_default_formula(client, app, sample_data):
    response = client.get("/production/history")
    assert response.status_code == 200
    page = response.get_data(as_text=True)

    # Default label and formula output
    assert "Output per Labor Hour" in page
    assert "1.25" in page  # 10 produced / 8 hours
    assert "overlayDataset" in page
    assert '"data": [1.25]' in page

    with app.app_context():
        settings = ProductionHistorySettings.query.first()
        assert settings is not None
        assert settings.output_label == "Output per Labor Hour"


def test_history_respects_custom_formula_and_axes(client, app, sample_data):
    form_data = {
        "action": "update-history-settings",
        "output_label": "Efficiency",
        "output_formula": "(produced * 2) / labor_hours",
        "variable_name_0": "produced",
        "variable_source_0": "produced_sum",
        "variable_name_1": "labor_hours",
        "variable_source_1": "gates_total_hours",
        "primary_min": "0",
        "primary_max": "200",
        "primary_step": "10",
        "secondary_min": "0",
        "secondary_max": "10",
        "secondary_step": "0.5",
        "show_goal_line": "on",
        "goal_line_value": "80",
    }

    # Ensure unused variable slots do not introduce errors
    for idx in range(2, 8):
        form_data[f"variable_name_{idx}"] = ""
        form_data[f"variable_source_{idx}"] = ""

    response = client.post("/production/settings", data=form_data, follow_redirects=True)
    assert response.status_code == 200

    history_response = client.get("/production/history")
    assert history_response.status_code == 200
    page = history_response.get_data(as_text=True)

    assert "Efficiency" in page
    assert "2.50" in page  # (10 * 2) / 8 hours
    assert '"Goal"' in page
    assert '"data": [2.5' in page or '"data": [2.50' in page
    assert '"data": [80.0' in page

    axis_match = re.search(r"const axisConfig = (?P<config>{.*?});", page, re.S)
    assert axis_match is not None
    axis_block = axis_match.group("config")
    assert "primary" in axis_block
    assert "secondary" in axis_block
    assert "min: 0.0" in axis_block
    assert "max: 200.0" in axis_block
    assert "step: 10.0" in axis_block
    assert "step: 0.5" in axis_block

    with app.app_context():
        settings = ProductionHistorySettings.query.first()
        assert settings.output_label == "Efficiency"
        assert settings.show_goal_line is True
        assert float(settings.goal_line_value) == 80.0
        assert settings.axis_config["primary"]["max"] == 200.0
