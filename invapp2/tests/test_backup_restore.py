from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import BackupRestoreEvent, Role, User
from invapp.services import backup_service


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


def login_superuser(client):
    return client.post(
        "/auth/login",
        data={"username": DEFAULT_SUPERUSER_USERNAME, "password": DEFAULT_SUPERUSER_PASSWORD},
        follow_redirects=True,
    )


def login_user(client, username, password):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_initialize_backup_scheduler_handles_unwritable_dir(app, monkeypatch):
    def raise_error(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise PermissionError("no access")

    monkeypatch.setattr(backup_service, "get_backup_dir", raise_error)

    backup_service.initialize_backup_scheduler(app)

    assert app.config.get("BACKUPS_ENABLED") is False


def test_backup_dir_falls_back_when_env_path_unwritable(app, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", "/backups")
    instance_path = Path(app.instance_path) / "backups"

    original_ensure = backup_service._ensure_directory

    def fake_ensure(path: Path) -> None:
        if path == Path("/backups"):
            raise PermissionError("no access")
        original_ensure(path)

    monkeypatch.setattr(backup_service, "_ensure_directory", fake_ensure)
    monkeypatch.setattr(backup_service, "_verify_writable", lambda _: None)

    resolved = backup_service.get_backup_dir(app)
    assert resolved == instance_path


def test_restore_permissions(client, app):
    response = client.get("/admin/backups")
    assert response.status_code in {302, 401}

    with app.app_context():
        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)
            db.session.commit()
        user = User(username="jane")
        user.set_password("pw123")
        user.roles.append(admin_role)
        db.session.add(user)
        db.session.commit()

    login_user(client, "jane", "pw123")
    response = client.get("/admin/backups")
    assert response.status_code == 403

    client.get("/auth/logout", follow_redirects=True)

    login_superuser(client)
    response = client.get("/admin/backups")
    assert response.status_code == 200


def test_restore_requires_superuser(client, app):
    with app.app_context():
        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)
            db.session.commit()
        user = User(username="jane")
        user.set_password("pw123")
        user.roles.append(admin_role)
        db.session.add(user)
        db.session.commit()

    login_user(client, "jane", "pw123")
    with client.session_transaction() as session:
        session["backup_restore_csrf"] = "token"

    response = client.post(
        "/admin/backups/restore",
        data={
            "backup_filename": "backup.sql",
            "confirm_restore": "RESTORE backup.sql",
            "confirm_phrase": "I UNDERSTAND THIS WILL OVERWRITE DATA",
            "confirm_ack": "yes",
            "csrf_token": "token",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_restore_rejects_bad_confirmation(client, app):
    login_superuser(client)
    with client.session_transaction() as session:
        session["backup_restore_csrf"] = "token"

    response = client.post(
        "/admin/backups/restore",
        data={
            "backup_filename": "backup.sql",
            "confirm_restore": "RESTORE wrong.sql",
            "confirm_phrase": "I UNDERSTAND THIS WILL OVERWRITE DATA",
            "confirm_ack": "yes",
            "csrf_token": "token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Type RESTORE backup.sql to confirm the backup restore." in response.data


def test_restore_uses_psql_without_root(app, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("DB_URL", "postgresql://user:pass@localhost/invdb")
    backup_path = tmp_path / "db" / "backup.sql"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text("SELECT 1;", encoding="utf-8")

    calls = []

    def fake_run(cmd, check=True, env=None, timeout=None, capture_output=False, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)

    result = backup_service.restore_database_backup(app, "backup.sql", app.logger)

    assert any(call[0] == "psql" for call in calls)
    assert result.message == "Restore completed from backup.sql."


def test_restore_writes_audit_events(client, app, monkeypatch):
    login_superuser(client)
    with client.session_transaction() as session:
        session["backup_restore_csrf"] = "token"

    outcome = backup_service.RestoreOutcome(
        filename="backup.sql",
        message="Restore completed from backup.sql.",
        stdout="ok",
        stderr="",
        duration_seconds=1.5,
    )
    monkeypatch.setattr(backup_service, "restore_database_backup", lambda *args, **kwargs: outcome)

    response = client.post(
        "/admin/backups/restore",
        data={
            "backup_filename": "backup.sql",
            "confirm_restore": "RESTORE backup.sql",
            "confirm_phrase": "I UNDERSTAND THIS WILL OVERWRITE DATA",
            "confirm_ack": "yes",
            "csrf_token": "token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        assert BackupRestoreEvent.query.count() >= 2


def test_restore_rejects_path_traversal(client, app, monkeypatch):
    login_superuser(client)

    with client.session_transaction() as session:
        session["backup_restore_csrf"] = "token"

    response = client.post(
        "/admin/backups/restore",
        data={
            "backup_filename": "../../etc/passwd",
            "confirm_restore": "RESTORE ../../etc/passwd",
            "confirm_phrase": "I UNDERSTAND THIS WILL OVERWRITE DATA",
            "confirm_ack": "yes",
            "csrf_token": "token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Invalid backup filename" in response.data
