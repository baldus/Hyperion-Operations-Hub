import os
import sys

import pytest
from sqlalchemy import create_engine

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Location


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


def _login_admin(client):
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )


def test_storage_page_requires_admin(client):
    response = client.get("/admin/storage-locations")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_storage_page_loads_for_admin(client):
    _login_admin(client)
    response = client.get("/admin/storage-locations")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Data Storage Locations" in html
    assert "Work Instructions" in html


def test_database_migration_to_new_sqlite(client, app, tmp_path):
    with app.app_context():
        location = Location(code="MAIN", description="Main Warehouse")
        db.session.add(location)
        db.session.commit()

    _login_admin(client)

    target_path = tmp_path / "target.db"
    target_url = f"sqlite:///{target_path}"

    response = client.post(
        "/admin/storage-locations",
        data={
            "new_database_url": target_url,
            "confirm_phrase": "migrate",
            "action": "migrate",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Database copied to the new location" in page
    assert "Latest Migration Summary" in page

    engine = create_engine(target_url)
    try:
        table = db.Model.metadata.tables["location"]
        with engine.connect() as connection:
            rows = connection.execute(table.select()).fetchall()
        assert len(rows) == 1
        assert rows[0]._mapping["code"] == "MAIN"
    finally:
        engine.dispose()
