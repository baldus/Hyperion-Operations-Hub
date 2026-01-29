import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Printer, Role, User
from invapp.printing.printers import resolve_effective_printer


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "ZEBRA_PRINTER_HOST": "system-printer",
            "ZEBRA_PRINTER_PORT": 9100,
            "PRINT_DRY_RUN": True,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_role(role_name: str) -> Role:
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        role = Role(name=role_name)
        db.session.add(role)
        db.session.commit()
    return role


def _login(client, username, password):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_resolve_printer_override_beats_user_default(app):
    with app.app_context():
        printer_default = Printer(name="Line 1", host="10.0.0.10", port=9100)
        printer_override = Printer(name="Line 2", host="10.0.0.11", port=9100)
        user = User(username="operator")
        user.set_password("pw")
        user.default_printer = printer_default
        db.session.add_all([printer_default, printer_override, user])
        db.session.commit()

        resolution = resolve_effective_printer(user=user, override=printer_override.id)

        assert resolution.target is not None
        assert resolution.target.id == printer_override.id
        assert resolution.target.source == "override"


def test_resolve_printer_user_default_beats_system_default(app):
    with app.app_context():
        printer_default = Printer(name="Line 1", host="10.0.0.10", port=9100)
        user = User(username="operator")
        user.set_password("pw")
        user.default_printer = printer_default
        db.session.add_all([printer_default, user])
        db.session.commit()

        resolution = resolve_effective_printer(user=user, override=None)

        assert resolution.target is not None
        assert resolution.target.id == printer_default.id
        assert resolution.target.source == "user_default"


def test_resolve_printer_system_default_when_no_user_default(app):
    with app.app_context():
        user = User(username="operator")
        user.set_password("pw")
        db.session.add(user)
        db.session.commit()

        resolution = resolve_effective_printer(user=user, override=None)

        assert resolution.target is not None
        assert resolution.target.source == "system_default"
        assert resolution.target.host == "system-printer"


def test_invalid_default_printer_falls_back_and_warns(app, client):
    with app.app_context():
        disabled_printer = Printer(
            name="Down Printer",
            host="10.0.0.20",
            port=9100,
            enabled=False,
        )
        user = User.query.filter_by(username="superuser").first()
        if user is None:
            admin_role = _create_role("admin")
            user = User(username="superuser")
            user.set_password("joshbaldus")
            user.roles.append(admin_role)
        user.default_printer = disabled_printer
        item = Item(sku="ITEM-1", name="Widget", unit="ea")
        db.session.add_all([disabled_printer, user, item])
        db.session.commit()
        item_id = item.id

    _login(client, "superuser", "joshbaldus")
    response = client.post(
        f"/inventory/item/{item_id}/print-label",
        data={"copies": 1},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Default printer is disabled" in response.data


def test_user_can_set_own_default_printer(app, client):
    with app.app_context():
        printer = Printer(name="Line 3", host="10.0.0.30", port=9100)
        role = _create_role("user")
        user = User(username="operator")
        user.set_password("pw")
        user.roles.append(role)
        db.session.add_all([printer, user])
        db.session.commit()
        printer_id = printer.id

    _login(client, "operator", "pw")
    response = client.post(
        "/users/profile",
        data={"default_printer_id": str(printer_id)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Default printer updated" in response.data

    with app.app_context():
        refreshed = User.query.filter_by(username="operator").one()
        assert refreshed.default_printer_id == printer.id


def test_user_cannot_set_another_users_default_printer(app, client):
    with app.app_context():
        printer = Printer(name="Line 4", host="10.0.0.40", port=9100)
        role = _create_role("user")
        user = User(username="operator")
        user.set_password("pw")
        user.roles.append(role)
        other = User(username="target")
        other.set_password("pw")
        other.roles.append(role)
        db.session.add_all([printer, user, other])
        db.session.commit()
        other_id = other.id
        printer_id = printer.id

    _login(client, "operator", "pw")
    response = client.post(
        f"/users/{other_id}/edit",
        data={"username": "target", "default_printer_id": str(printer_id)},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_admin_can_set_another_users_default_printer(app, client):
    with app.app_context():
        printer = Printer(name="Line 5", host="10.0.0.50", port=9100)
        admin_role = _create_role("admin")
        admin = User(username="admin")
        admin.set_password("pw")
        admin.roles.append(admin_role)
        target = User(username="target")
        target.set_password("pw")
        target.roles.append(admin_role)
        db.session.add_all([printer, admin, target])
        db.session.commit()
        target_id = target.id
        printer_id = printer.id

    _login(client, "admin", "pw")
    response = client.post(
        f"/users/{target_id}/edit",
        data={"username": "target", "default_printer_id": str(printer_id)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"User updated" in response.data

    with app.app_context():
        refreshed = User.query.get(target_id)
        assert refreshed.default_printer_id == printer.id
