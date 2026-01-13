import os
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import AdminAuditLog, Batch, Item, Location, Movement, Role, User


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
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


def _create_user(app, username, password, role_names=()):
    with app.app_context():
        user = User(username=username)
        user.set_password(password)
        roles = []
        for role_name in role_names:
            role = Role.query.filter_by(name=role_name).first()
            if role is None:
                role = Role(name=role_name)
                db.session.add(role)
            roles.append(role)
        user.roles = roles
        db.session.add(user)
        db.session.commit()
        return user


def _login(client, username, password):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def _set_clear_inventory_csrf(client, token="csrf-token"):
    with client.session_transaction() as session:
        session["clear_inventory_csrf"] = token
    return token


def _seed_inventory(app):
    with app.app_context():
        location = Location(code="MAIN")
        item = Item(sku="SKU-1", name="Widget")
        db.session.add_all([location, item])
        db.session.flush()

        batch = Batch(item_id=item.id, lot_number="LOT-1", quantity=5)
        db.session.add(batch)
        db.session.flush()
        movement = Movement(
            item_id=item.id,
            batch_id=batch.id,
            location_id=location.id,
            quantity=5,
            movement_type="RECEIPT",
        )
        db.session.add(movement)
        db.session.commit()


def test_admin_can_clear_inventory(client, app, monkeypatch, tmp_path: Path):
    _create_user(app, "admin_user", "pw123", role_names=("admin",))
    _seed_inventory(app)
    _login(client, "admin_user", "pw123")
    token = _set_clear_inventory_csrf(client)

    def _fake_backup(_app):
        return tmp_path / "backup.zip", tmp_path

    monkeypatch.setattr(
        "invapp.routes.admin.backup_exporter.create_database_backup_archive",
        _fake_backup,
    )

    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "confirm_clear": "1",
            "confirm_phrase": "CLEAR INVENTORY",
            "confirm_password": "pw123",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Movement.query.count() == 0
        assert Batch.query.count() == 0
        assert Item.query.count() == 1
        assert Location.query.count() == 1
        assert AdminAuditLog.query.count() == 1


def test_clear_inventory_rejects_non_admin(client, app):
    _create_user(app, "viewer", "pw123", role_names=("viewer",))
    _seed_inventory(app)
    _login(client, "viewer", "pw123")
    token = _set_clear_inventory_csrf(client)

    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "confirm_clear": "1",
            "confirm_phrase": "CLEAR INVENTORY",
            "confirm_password": "pw123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_clear_inventory_requires_checkbox(client, app, monkeypatch, tmp_path: Path):
    _create_user(app, "admin_user", "pw123", role_names=("admin",))
    _seed_inventory(app)
    _login(client, "admin_user", "pw123")
    token = _set_clear_inventory_csrf(client)

    def _fake_backup(_app):
        return tmp_path / "backup.zip", tmp_path

    monkeypatch.setattr(
        "invapp.routes.admin.backup_exporter.create_database_backup_archive",
        _fake_backup,
    )

    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "confirm_phrase": "CLEAR INVENTORY",
            "confirm_password": "pw123",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Movement.query.count() == 1
        assert Batch.query.count() == 1


def test_clear_inventory_requires_phrase_match(client, app, monkeypatch, tmp_path: Path):
    _create_user(app, "admin_user", "pw123", role_names=("admin",))
    _seed_inventory(app)
    _login(client, "admin_user", "pw123")
    token = _set_clear_inventory_csrf(client)

    def _fake_backup(_app):
        return tmp_path / "backup.zip", tmp_path

    monkeypatch.setattr(
        "invapp.routes.admin.backup_exporter.create_database_backup_archive",
        _fake_backup,
    )

    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "confirm_clear": "1",
            "confirm_phrase": "WRONG PHRASE",
            "confirm_password": "pw123",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Movement.query.count() == 1
        assert Batch.query.count() == 1


def test_clear_inventory_aborts_when_backup_fails(client, app, monkeypatch):
    _create_user(app, "admin_user", "pw123", role_names=("admin",))
    _seed_inventory(app)
    _login(client, "admin_user", "pw123")
    token = _set_clear_inventory_csrf(client)

    def _failed_backup(_app):
        raise RuntimeError("backup failed")

    monkeypatch.setattr(
        "invapp.routes.admin.backup_exporter.create_database_backup_archive",
        _failed_backup,
    )

    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "confirm_clear": "1",
            "confirm_phrase": "CLEAR INVENTORY",
            "confirm_password": "pw123",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Movement.query.count() == 1
        assert Batch.query.count() == 1
