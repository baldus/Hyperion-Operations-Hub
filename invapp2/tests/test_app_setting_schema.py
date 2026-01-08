from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import inspect, text

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.services.db_schema import ensure_app_setting_schema


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_app_setting_schema_self_heals(app):
    with app.app_context():
        engine = db.engine
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS app_setting"))
            conn.execute(
                text(
                    "CREATE TABLE app_setting (id INTEGER PRIMARY KEY, key VARCHAR(128) UNIQUE NOT NULL, value VARCHAR(255))"
                )
            )

        ensure_app_setting_schema(engine, app.logger)
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("app_setting")}
        assert "created_at" in columns
        assert "updated_at" in columns
