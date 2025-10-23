from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import invapp as invapp_module
from invapp import create_app
from invapp.extensions import db


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

    home_response = client.get("/")
    assert b"Emergency access" in home_response.data
    assert b"admin tools" in home_response.data
