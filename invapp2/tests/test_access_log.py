import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import AccessLog

DEFAULT_SUPERUSER_USERNAME = "superuser"
DEFAULT_SUPERUSER_PASSWORD = "joshbaldus"


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


def login(client, username=DEFAULT_SUPERUSER_USERNAME, password=DEFAULT_SUPERUSER_PASSWORD):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_login_events_recorded(client, app):
    bad_response = client.post(
        "/auth/login",
        data={"username": DEFAULT_SUPERUSER_USERNAME, "password": "wrong"},
        follow_redirects=False,
    )
    assert bad_response.status_code == 200

    good_response = login(client)
    assert good_response.status_code == 302

    with app.app_context():
        successes = AccessLog.query.filter_by(event_type=AccessLog.EVENT_LOGIN_SUCCESS).all()
        failures = AccessLog.query.filter_by(event_type=AccessLog.EVENT_LOGIN_FAILURE).all()
        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0].username == DEFAULT_SUPERUSER_USERNAME
        assert successes[0].ip_address == "127.0.0.1"
        assert failures[0].username == DEFAULT_SUPERUSER_USERNAME
        assert failures[0].status_code == 401

        request_events = AccessLog.query.filter(
            AccessLog.event_type == AccessLog.EVENT_REQUEST,
            AccessLog.path.like("/auth/login%"),
        ).all()
        assert request_events, "Expected request events to be captured"


def test_admin_access_log_page(client):
    login_response = login(client)
    assert login_response.status_code == 302

    response = client.get("/admin/access-log")
    assert response.status_code == 200
    assert b"Access Log" in response.data
    assert b"Recent Events" in response.data
    assert b"IP Address Activity" in response.data
