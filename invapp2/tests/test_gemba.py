import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app, models
from invapp.extensions import db

DEFAULT_SUPERUSER_USERNAME = "superuser"
DEFAULT_SUPERUSER_PASSWORD = "joshbaldus"


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        models.GembaCategory.ensure_defaults()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login_admin(client):
    return client.post(
        "/auth/login",
        data={
            "username": DEFAULT_SUPERUSER_USERNAME,
            "password": DEFAULT_SUPERUSER_PASSWORD,
        },
        follow_redirects=True,
    )


def test_gemba_dashboard_requires_login(client):
    response = client.get("/admin/gemba", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/auth/login")


def test_gemba_dashboard_displays_metrics(client, app):
    login_admin(client)
    with app.app_context():
        metric = models.GembaMetric(
            category="Safety",
            department="Assembly",
            metric_name="Recordable Incidents",
            metric_value=Decimal("1"),
            target_value=Decimal("0"),
            unit="count",
            date=date.today(),
            notes="Incident investigated",
        )
        db.session.add(metric)
        db.session.commit()

    response = client.get("/admin/gemba")
    assert response.status_code == 200
    assert b"Recordable Incidents" in response.data
    assert b"Assembly" in response.data


def test_create_gemba_metric_persists_record(client, app):
    login_admin(client)
    today = date.today().isoformat()

    response = client.post(
        "/admin/gemba/metrics",
        data={
            "category": "Safety",
            "metric_name": "Daily Safety Checks",
            "metric_value": "4",
            "target_value": "5",
            "department": "Fabrication",
            "unit": "checks",
            "date": today,
            "notes": "Four checklists completed",
            "linked_record_url": "https://example.com/incidents",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        metric = models.GembaMetric.query.filter_by(metric_name="Daily Safety Checks").first()
        assert metric is not None
        assert metric.department == "Fabrication"
        assert metric.metric_value == Decimal("4")
        assert metric.target_value == Decimal("5")
        assert metric.linked_record_url == "https://example.com/incidents"


def test_update_gemba_metric_rejects_invalid_numbers(client, app):
    login_admin(client)
    with app.app_context():
        metric = models.GembaMetric(
            category="Safety",
            department="Assembly",
            metric_name="PPE Compliance",
            metric_value=Decimal("95"),
            target_value=Decimal("100"),
            unit="percent",
            date=date.today(),
        )
        db.session.add(metric)
        db.session.commit()
        metric_id = metric.id

    response = client.post(
        f"/admin/gemba/metrics/{metric_id}/update",
        data={
            "category": "Safety",
            "metric_name": "PPE Compliance",
            "metric_value": "invalid",
            "target_value": "100",
            "department": "Assembly",
            "unit": "percent",
            "date": date.today().isoformat(),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        metric = models.GembaMetric.query.get(metric_id)
        assert metric.metric_value == Decimal("95")
        assert metric.target_value == Decimal("100")

