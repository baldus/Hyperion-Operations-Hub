import os
import re
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import AdminAuditLog, Batch, Item, Location, Movement, Role, User
from invapp.services import backup_exporter


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


def _login_superuser(client):
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )


def _login_viewer(client, app):
    with app.app_context():
        role = Role.query.filter_by(name="viewer").first()
        if role is None:
            role = Role(name="viewer", description="Viewer")
            db.session.add(role)
            db.session.commit()
        user = User(username="viewer_user")
        user.set_password("pw")
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "viewer_user", "password": "pw"},
        follow_redirects=True,
    )


def _seed_inventory(app):
    with app.app_context():
        item = Item(sku="SKU-1", name="Sample")
        location = Location(code="MAIN", description="Main")
        db.session.add_all([item, location])
        db.session.commit()
        batch = Batch(item_id=item.id, lot_number="LOT-1", quantity=5)
        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            batch=batch,
            quantity=5,
            movement_type="RECEIPT",
            person="Tester",
        )
        db.session.add_all([batch, movement])
        db.session.commit()
        return item.id, location.id


def _csrf_token(client):
    response = client.get("/settings/")
    text = response.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', text)
    assert match
    return match.group(1)


def test_admin_can_clear_inventory(client, app, monkeypatch):
    _seed_inventory(app)
    _login_superuser(client)

    monkeypatch.setattr(
        backup_exporter,
        "create_database_backup_archive",
        lambda *_args, **_kwargs: (Path("backup.zip"), Path("tmp")),
    )

    token = _csrf_token(client)
    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "acknowledge": "1",
            "confirm_phrase": "CLEAR INVENTORY",
            "password": "joshbaldus",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Inventory cleared" in page

    with app.app_context():
        assert Movement.query.count() == 0
        assert Batch.query.count() == 0
        assert Item.query.count() == 1
        assert Location.query.count() == 1
        audit = AdminAuditLog.query.filter_by(action="CLEAR_INVENTORY").first()
        assert audit is not None


def test_non_admin_cannot_clear_inventory(client, app):
    _seed_inventory(app)
    _login_viewer(client, app)

    response = client.post(
        "/admin/settings/clear-inventory",
        data={"csrf_token": "token", "acknowledge": "1"},
    )
    assert response.status_code == 403


def test_clear_inventory_requires_acknowledgement(client, app, monkeypatch):
    _seed_inventory(app)
    _login_superuser(client)

    monkeypatch.setattr(
        backup_exporter,
        "create_database_backup_archive",
        lambda *_args, **_kwargs: (Path("backup.zip"), Path("tmp")),
    )

    token = _csrf_token(client)
    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "confirm_phrase": "CLEAR INVENTORY",
            "password": "joshbaldus",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        assert Movement.query.count() == 1
        assert Batch.query.count() == 1


def test_clear_inventory_requires_phrase(client, app, monkeypatch):
    _seed_inventory(app)
    _login_superuser(client)

    monkeypatch.setattr(
        backup_exporter,
        "create_database_backup_archive",
        lambda *_args, **_kwargs: (Path("backup.zip"), Path("tmp")),
    )

    token = _csrf_token(client)
    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "acknowledge": "1",
            "confirm_phrase": "WRONG PHRASE",
            "password": "joshbaldus",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        assert Movement.query.count() == 1
        assert Batch.query.count() == 1


def test_clear_inventory_aborts_on_backup_failure(client, app, monkeypatch):
    _seed_inventory(app)
    _login_superuser(client)

    def _raise_backup(*_args, **_kwargs):
        raise RuntimeError("backup failed")

    monkeypatch.setattr(backup_exporter, "create_database_backup_archive", _raise_backup)

    token = _csrf_token(client)
    response = client.post(
        "/admin/settings/clear-inventory",
        data={
            "csrf_token": token,
            "acknowledge": "1",
            "confirm_phrase": "CLEAR INVENTORY",
            "password": "joshbaldus",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Backup failed" in page

    with app.app_context():
        assert Movement.query.count() == 1
        assert Batch.query.count() == 1
