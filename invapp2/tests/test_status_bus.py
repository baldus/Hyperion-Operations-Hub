from __future__ import annotations

import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.services import status_bus


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_status_bus_dedupe(app):
    with app.app_context():
        status_bus.log_event("warning", "repeatable warning", dedupe_key="repeat")
        status_bus.log_event("warning", "repeatable warning", dedupe_key="repeat")

        events = status_bus.get_recent_events()
        matching = [event for event in events if event["message"] == "repeatable warning"]
        assert matching
        assert matching[-1]["count"] == 2
