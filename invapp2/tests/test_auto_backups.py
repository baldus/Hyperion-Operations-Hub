from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Role, User


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


def _create_admin_user(app, username: str, password: str) -> None:
    with app.app_context():
        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)
            db.session.commit()
        user = User(username=username)
        user.set_password(password)
        user.roles.append(admin_role)
        db.session.add(user)
        db.session.commit()


def _login(client, username: str, password: str):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_auto_backups_requires_admin(client, app):
    response = client.get("/admin/settings/backups/auto")
    assert response.status_code in {302, 401}

    _create_admin_user(app, "jane", "pw123")
    _login(client, "jane", "pw123")
    response = client.get("/admin/settings/backups/auto")
    assert response.status_code == 200


def test_auto_backups_blocks_path_traversal(client, app):
    _create_admin_user(app, "jane", "pw123")
    _login(client, "jane", "pw123")
    response = client.get("/admin/settings/backups/auto/download/../../etc/passwd")
    assert response.status_code == 404


def test_auto_backup_downloads_file(client, app, tmp_path: Path):
    _create_admin_user(app, "jane", "pw123")
    _login(client, "jane", "pw123")

    backup_name = "backup_2024-01-01_0101_001.zip"
    backup_path = tmp_path / backup_name
    backup_path.write_bytes(b"backup-data")

    app.config["BACKUP_DIR_AUTO"] = str(tmp_path)

    response = client.get(f"/admin/settings/backups/auto/download/{backup_name}")
    assert response.status_code == 200
    assert response.data == b"backup-data"
