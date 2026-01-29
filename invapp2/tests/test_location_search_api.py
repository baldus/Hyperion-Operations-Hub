import os
import sys
from datetime import datetime

import pytest

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
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


def test_location_search_ranking(client, app):
    with app.app_context():
        db.session.add_all(
            [
                Location(code="UNASSIGNED", description="Unassigned staging location"),
                Location(code="ABC-1", description="Starts with query"),
                Location(code="XABC-2", description="Contains query in code"),
                Location(code="ZZZ-9", description="Stored near ABC bins"),
            ]
        )
        db.session.commit()

    response = client.get("/inventory/api/locations/search?q=ABC")
    assert response.status_code == 200
    data = response.get_json()
    assert [entry["code"] for entry in data[:3]] == ["ABC-1", "XABC-2", "ZZZ-9"]


def test_location_search_limit(client, app):
    with app.app_context():
        db.session.add_all(
            [Location(code=f"LOC-{idx:02d}", description="Bulk") for idx in range(40)]
        )
        db.session.commit()

    response = client.get("/inventory/api/locations/search?q=LOC")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 25


def test_location_search_unassigned_default(client, app):
    with app.app_context():
        db.session.add_all(
            [
                Location(code="UNASSIGNED", description="Unassigned staging location"),
                Location(code="A-1", description="Rack"),
            ]
        )
        db.session.commit()

    response = client.get("/inventory/api/locations/search")
    assert response.status_code == 200
    data = response.get_json()
    assert data[0]["code"] == "UNASSIGNED"


def test_location_search_excludes_removed(client, app):
    with app.app_context():
        db.session.add_all(
            [
                Location(code="ACTIVE", description="Active"),
                Location(
                    code="REMOVED", description="Removed", removed_at=datetime.utcnow()
                ),
            ]
        )
        db.session.commit()

    response = client.get("/inventory/api/locations/search?q=REM")
    assert response.status_code == 200
    data = response.get_json()
    assert all(entry["code"] != "REMOVED" for entry in data)
