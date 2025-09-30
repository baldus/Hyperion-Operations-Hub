import os
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest
from flask import current_app

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


def _create_record_for_date(entry_date: date, produced: int, packaged: int) -> None:
    customer = ProductionCustomer.query.filter_by(is_other_bucket=False).first()
    record = ProductionDailyRecord(
        entry_date=entry_date,
        day_of_week=entry_date.strftime("%A"),
        gates_employees=2,
        gates_hours_ot=Decimal("1.00"),
    )
    db.session.add(record)
    db.session.flush()
    db.session.add(
        ProductionDailyCustomerTotal(
            record=record,
            customer=customer,
            gates_produced=produced,
            gates_packaged=packaged,
        )
    )
    db.session.commit()


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


def _capture_history_context(app, client, monkeypatch, start_date: date, end_date: date):
    captured: list[dict] = []

    def fake_render(template_name, **context):  # pragma: no cover - helper
        captured.append(context)
        return current_app.response_class("OK")

    monkeypatch.setattr(production, "render_template", fake_render)
    response = client.get(
        "/production/history",
        query_string={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
    assert response.status_code == 200
    assert captured, "Expected the history template to be rendered"
    return captured[0]


def test_history_goal_line_flat_and_trend_toggle(client, app, monkeypatch):
    start_date = date(2024, 1, 1)  # Tuesday is 2024-01-02; both weekdays
    with app.app_context():
        ProductionDailyCustomerTotal.query.delete()
        ProductionDailyRecord.query.delete()
        db.session.commit()

        _create_record_for_date(start_date, produced=10, packaged=9)
        _create_record_for_date(start_date + timedelta(days=1), produced=14, packaged=12)

        settings = ProductionChartSettings.get_or_create()
        settings.goal_value = Decimal("12")
        settings.show_goal = True
        settings.show_trend = False
        db.session.commit()

    context = _capture_history_context(
        app,
        client,
        monkeypatch,
        start_date,
        start_date + timedelta(days=1),
    )
    overlay_datasets = context["overlay_datasets"]

    goal_dataset = next(
        dataset for dataset in overlay_datasets if dataset["label"] == "Gates Goal"
    )
    assert goal_dataset["data"] == [12.0, 12.0]
    assert not any(
        dataset["label"] == "Gates Produced Trend" for dataset in overlay_datasets
    )

    with app.app_context():
        settings = ProductionChartSettings.get_or_create()
        settings.show_trend = True
        db.session.commit()

    context = _capture_history_context(
        app,
        client,
        monkeypatch,
        start_date,
        start_date + timedelta(days=1),
    )
    overlay_datasets = context["overlay_datasets"]
    trend_dataset = next(
        dataset for dataset in overlay_datasets if dataset["label"] == "Gates Produced Trend"
    )
    assert trend_dataset["data"][0] != trend_dataset["data"][1]

