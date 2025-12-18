from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import invapp as invapp_module
from invapp import routes as routes_package  # noqa: F401 - ensures package import side effects
from invapp import create_app
from invapp.extensions import db
from invapp.routes import admin as admin_routes


def test_create_app_handles_database_outage(monkeypatch):
    """The application should initialize even when the database is offline."""

    def fake_ping() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database offline"))

    monkeypatch.setattr(invapp_module, "_ping_database", fake_ping)

    create_all_called = False

    def record_create_all(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal create_all_called
        create_all_called = True

    monkeypatch.setattr(db, "create_all", record_create_all)

    app = create_app({"TESTING": True})

    assert app.config["DATABASE_AVAILABLE"] is False
    assert "Unable to connect" in (app.config["DATABASE_ERROR"] or "")
    assert "database offline" in (app.config["DATABASE_ERROR"] or "")
    assert create_all_called is False


def test_emergency_access_allows_admin_tools(monkeypatch):
    """Emergency mode should surface admin tooling without authentication."""

    def fake_ping() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database offline"))

    monkeypatch.setattr(invapp_module, "_ping_database", fake_ping)

    app = create_app({"TESTING": True})
    client = app.test_client()

    tools_response = client.get("/admin/tools")
    assert tools_response.status_code == 200
    assert b"System Uptime" in tools_response.data
    assert b"Database-dependent shortcuts" in tools_response.data

    home_response = client.get("/")
    assert b"Emergency access" in home_response.data
    assert b"admin tools" in home_response.data

    access_log_response = client.get("/admin/access-log")
    assert access_log_response.status_code == 200
    assert b"Bring the database back online" in access_log_response.data

    users_response = client.get("/users/")
    assert users_response.status_code == 200
    assert b"User Administration Unavailable" in users_response.data

    reset_response = client.get("/auth/reset-password", follow_redirects=True)
    assert reset_response.status_code == 200
    assert b"Password changes are disabled" in reset_response.data


def test_emergency_console_runs_recovery_sequence(monkeypatch):
    def fake_ping() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database offline"))

    monkeypatch.setattr(invapp_module, "_ping_database", fake_ping)

    executed: list[tuple[str, ...]] = []

    def fake_run(parts, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        executed.append(tuple(parts))
        index = len(executed)

        class _Result:
            returncode = 0
            stdout = f"step {index} completed"
            stderr = ""

        return _Result()

    monkeypatch.setattr(admin_routes.subprocess, "run", fake_run)

    app = create_app({"TESTING": True})
    client = app.test_client()

    with app.app_context():
        recovery_sequence = admin_routes._build_recovery_sequence()

    response = client.post(
        "/admin/emergency-console",
        data={"action_id": admin_routes._AUTOMATED_RECOVERY_ACTION_ID},
    )
    assert response.status_code == 200
    assert b"Automated recovery sequence" in response.data
    assert b"steps succeeded" in response.data
    assert len(executed) == len(recovery_sequence)
    assert executed[0][:3] == ("sudo", "apt-get", "update")
    assert executed[-1][0] == "bash"
    assert executed[-1][1].endswith("support/run_diagnostics.sh")


def test_recovery_sequence_uses_configured_database(monkeypatch):
    def fake_ping() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database offline"))

    monkeypatch.setattr(invapp_module, "_ping_database", fake_ping)

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://example:sekret@db.example.com/exampledb",
        }
    )

    with app.app_context():
        recovery_sequence = admin_routes._build_recovery_sequence()

    commands = [" ".join(step["command"]) for step in recovery_sequence]

    assert any("exampledb" in command for command in commands)
    assert any("example" in command and "sekret" in command for command in commands)
    assert any("-h db.example.com" in command for command in commands)
    assert any("Application bootstrap completed successfully" in command for command in commands)


def test_emergency_console_resolves_restart_script(monkeypatch):
    def fake_ping() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database offline"))

    monkeypatch.setattr(invapp_module, "_ping_database", fake_ping)

    executed: dict[str, tuple[str, ...]] = {}

    def fake_run(parts, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        executed["parts"] = tuple(parts)

        class _Result:
            returncode = 0
            stdout = "console restarted"
            stderr = ""

        return _Result()

    monkeypatch.setattr(admin_routes.subprocess, "run", fake_run)

    app = create_app({"TESTING": True})
    client = app.test_client()

    response = client.post(
        "/admin/emergency-console",
        data={"action_id": admin_routes._CONSOLE_RESTART_ACTION_ID},
    )
    assert response.status_code == 200
    assert executed["parts"][0] == "bash"
    assert executed["parts"][1].endswith("start_operations_console.sh")
    assert Path(executed["parts"][1]).is_file()


def test_emergency_console_rejects_disallowed_custom_command(monkeypatch):
    def fake_ping() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database offline"))

    monkeypatch.setattr(invapp_module, "_ping_database", fake_ping)

    app = create_app({"TESTING": True})
    client = app.test_client()

    response = client.post(
        "/admin/emergency-console",
        data={"custom_command": "rm -rf /"},
    )
    assert response.status_code == 200
    assert b"not allowed in emergency mode" in response.data
