import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item


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


def test_item_search_ranking(client, app):
    with app.app_context():
        items = [
            Item(sku="ABC-123", name="Widget A"),
            Item(sku="ABC-124", name="Widget B"),
            Item(sku="XABC-9", name="Misc"),
            Item(sku="ZZZ-1", name="Alpha", description="Includes ABC"),
        ]
        db.session.add_all(items)
        db.session.commit()

    response = client.get("/api/items/search?q=ABC")
    assert response.status_code == 200
    data = response.get_json()
    assert [entry["item_number"] for entry in data[:2]] == ["ABC-123", "ABC-124"]
    assert [entry["item_number"] for entry in data[2:]] == ["XABC-9", "ZZZ-1"]

    exact_response = client.get("/api/items/search?q=ABC-123")
    exact_data = exact_response.get_json()
    assert exact_data[0]["item_number"] == "ABC-123"


def test_item_search_limits_results(client, app):
    with app.app_context():
        db.session.add_all(
            [Item(sku=f"ITEM-{idx:02d}", name=f"Item {idx}") for idx in range(15)]
        )
        db.session.commit()

    response = client.get("/api/items/search?q=ITEM")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 10
