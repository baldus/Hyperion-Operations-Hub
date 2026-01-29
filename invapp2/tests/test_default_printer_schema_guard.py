import logging
import os
import sqlite3
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app


def test_schema_guard_logs_missing_default_printer_id(caplog, tmp_path):
    db_path = tmp_path / "schema_guard.db"
    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE user (
            id INTEGER PRIMARY KEY,
            username VARCHAR(255),
            password_hash VARCHAR(255),
            created_at DATETIME,
            updated_at DATETIME
        )
        """
    )
    connection.commit()
    connection.close()

    caplog.set_level(logging.WARNING)
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )

    assert "Database schema out of date. Run: cd invapp2 && alembic -c alembic.ini upgrade head" in caplog.text

    with app.app_context():
        loader = app.login_manager._user_callback
        assert loader is not None
        assert loader("1") is None
