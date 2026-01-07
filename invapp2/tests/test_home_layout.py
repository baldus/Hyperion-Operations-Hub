import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db


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


def _login_admin(client):
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )


def test_home_layout_defaults_to_visible_cards(client):
    _login_admin(client)
    response = client.get("/api/home_layout")
    assert response.status_code == 200
    payload = response.get_json()

    keys = [entry["key"] for entry in payload["layout"]]
    assert keys == ["orders", "inventory", "incoming_items"]
    assert all(entry["visible"] for entry in payload["layout"])
    assert payload["available_cubes"] == []


def test_home_layout_save_appends_missing_keys(client):
    _login_admin(client)
    response = client.post(
        "/api/home_layout",
        json={"layout": [{"key": "inventory", "visible": True}]},
    )
    assert response.status_code == 200

    payload = response.get_json()
    keys = [entry["key"] for entry in payload["layout"]]
    assert keys[0] == "inventory"
    hidden_keys = [entry["key"] for entry in payload["layout"] if not entry["visible"]]
    assert "orders" in hidden_keys
    assert "incoming_items" in hidden_keys


def test_home_layout_rejects_unknown_keys(client):
    _login_admin(client)
    response = client.post(
        "/api/home_layout",
        json={"layout": [{"key": "unknown", "visible": True}]},
    )
    assert response.status_code == 400
