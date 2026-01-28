import os
import sys
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.routes.inventory import _store_import_csv


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


def test_physical_inventory_schema_guard(monkeypatch, client, app):
    with app.app_context():
        csv_text = "Item Name,Quantity\nWidget,5\n"
        import_token = _store_import_csv("physical_inventory", csv_text)

    monkeypatch.setattr(
        "invapp.routes.inventory._physical_inventory_snapshot_columns",
        lambda: set(),
    )

    response = client.post(
        "/inventory/physical-inventory",
        data={
            "step": "mapping",
            "import_token": import_token,
            "primary_upload_column": "Item Name",
            "primary_item_field": "name",
            "quantity_column": "Quantity",
            "duplicate_strategy": "sum",
            "trim_whitespace": "on",
            "case_insensitive": "on",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Database schema is out of date" in page


def test_physical_inventory_migration_adds_columns(monkeypatch, tmp_path):
    db_path = tmp_path / "migration.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DB_URL", db_url)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    alembic_cfg = Config(os.path.join(repo_root, "alembic.ini"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(db_url)
    columns = {col["name"] for col in inspect(engine).get_columns("physical_inventory_snapshot")}
    assert "created_items_count" in columns
    assert "unmatched_details" in columns
    assert "ambiguous_details" in columns
