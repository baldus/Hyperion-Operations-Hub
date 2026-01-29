import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Printer, Role, User
from invapp.printing.printer_defaults import resolve_user_printer


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()

        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)

        user = User(username="printer-user")
        user.set_password("pw")
        user.roles.append(admin_role)
        db.session.add(user)

        db.session.add_all(
            [
                Printer(name="Alpha", host="10.0.0.1", port=9100),
                Printer(name="Beta", host="10.0.0.2", port=9100),
            ]
        )

        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client):
    return client.post(
        "/auth/login",
        data={"username": "printer-user", "password": "pw"},
        follow_redirects=True,
    )


def build_layout_payload():
    return {
        "label_id": "batch-label",
        "layout": {
            "id": "batch-label",
            "name": "Batch Label",
            "size": {"width": 812, "height": 1218},
            "fields": [
                {
                    "id": "field-1",
                    "label": "Lot Number",
                    "bindingKey": "lot_number",
                    "type": "text",
                    "x": 60,
                    "y": 80,
                    "width": 640,
                    "height": 64,
                    "rotation": 0,
                    "fontSize": 48,
                    "align": "left",
                }
            ],
        },
    }


def test_user_without_default_uses_system_fallback(app):
    with app.app_context():
        app.config["ZEBRA_PRINTER_HOST"] = "10.0.0.2"
        app.config["ZEBRA_PRINTER_PORT"] = 9100
        user = User.query.filter_by(username="printer-user").first()

        printer = resolve_user_printer(user)

        assert printer is not None
        assert printer.name == "Beta"


def test_missing_default_printer_clears_and_falls_back(app):
    with app.app_context():
        user = User.query.filter_by(username="printer-user").first()
        user.default_printer = "Ghost"
        db.session.commit()

        printer = resolve_user_printer(user)

        assert printer is not None
        assert user.default_printer is None


def test_save_default_printer_via_settings(client, app):
    login(client)
    response = client.post(
        "/users/settings",
        data={"default_printer": "Alpha"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Default printer set to Alpha" in response.data

    with app.app_context():
        user = User.query.filter_by(username="printer-user").first()
        assert user.default_printer == "Alpha"


def test_default_printer_is_preselected(client, app):
    with app.app_context():
        user = User.query.filter_by(username="printer-user").first()
        user.default_printer = "Beta"
        db.session.commit()

    login(client)
    response = client.get("/users/settings")
    assert response.status_code == 200
    assert b'value="Beta" selected' in response.data


def test_print_action_updates_default_printer(client, app, monkeypatch):
    login(client)
    save_payload = build_layout_payload()
    client.post("/settings/printers/designer/save", json=save_payload)

    def fake_print_label(process, context, **kwargs):
        return True

    monkeypatch.setattr("invapp.routes.printers.print_label_for_process", fake_print_label)

    response = client.post(
        "/settings/printers/designer/print-trial",
        json={**save_payload, "printer_name": "Beta"},
    )
    assert response.status_code == 200

    with app.app_context():
        user = User.query.filter_by(username="printer-user").first()
        assert user.default_printer == "Beta"
