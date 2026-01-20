from __future__ import annotations

import os
import sys
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

    login_superuser(client)
    response = client.get("/admin/backups")
    assert response.status_code == 200


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
            "csrf_token": "token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Invalid backup filename" in response.data


def test_restore_requires_superuser_for_post(client, app):
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
            "csrf_token": "token",
        },
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
            "confirm_restore": "RESTORE",
            "confirm_phrase": "I UNDERSTAND THIS WILL OVERWRITE DATA",
            "csrf_token": "token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Type RESTORE" in response.data


def test_restore_executes_psql_command(app, monkeypatch, tmp_path):
    backup_dir = tmp_path / "backups"
    (backup_dir / "db").mkdir(parents=True)
    backup_path = backup_dir / "db" / "backup.sql"
    backup_path.write_text("-- test backup")

    monkeypatch.setenv("DB_URL", "postgresql://user:pass@localhost/invdb")
    monkeypatch.setattr(backup_service, "get_backup_dir", lambda *args, **kwargs: backup_dir)

    run_calls = []

    def fake_run(command, check, env, timeout, capture_output, text):  # type: ignore[no-untyped-def]
        run_calls.append(command)

        class Result:
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)

    with app.app_context():
        result = backup_service.restore_database_backup(app, "backup.sql", app.logger)

    assert result.message == "Restore completed from backup.sql."
    assert any(call[0] == "psql" for call in run_calls)


def test_restore_writes_audit_events(client, app, monkeypatch):
    login_superuser(client)
    with client.session_transaction() as session:
        session["backup_restore_csrf"] = "token"

    def fake_restore(*args, **kwargs):
        return backup_service.RestoreResult(
            message="Restore completed from backup.sql.",
            duration_seconds=1.23,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(backup_service, "restore_database_backup", fake_restore)

    response = client.post(
        "/admin/backups/restore",
        data={
            "backup_filename": "backup.sql",
            "confirm_restore": "RESTORE backup.sql",
            "confirm_phrase": "I UNDERSTAND THIS WILL OVERWRITE DATA",
            "csrf_token": "token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        events = (
            db.session.query(BackupRestoreEvent)
            .filter_by(action="restore", backup_filename="backup.sql")
            .all()
        )
    statuses = {event.status for event in events}
    assert "started" in statuses
    assert "succeeded" in statuses
