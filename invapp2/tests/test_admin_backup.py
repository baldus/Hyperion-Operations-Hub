import io
import json
import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, Movement


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
    with client.session_transaction() as session:
        session["is_admin"] = True


def test_backup_requires_admin(client):
    response = client.get("/admin/data-backup")
    assert response.status_code == 302
    assert "/admin/login" in response.location

    response = client.post("/admin/data-backup/export")
    assert response.status_code == 302
    assert "/admin/login" in response.location


def test_export_and_import_round_trip(client, app):
    with app.app_context():
        location = Location(code="MAIN", description="Main Warehouse")
        item = Item(sku="SKU-1", name="Sample Item")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            movement_type="ADJUST",
            person="Tester",
        )
        db.session.add(movement)
        db.session.commit()

    _login_admin(client)

    export_response = client.post("/admin/data-backup/export")
    assert export_response.status_code == 200
    assert export_response.mimetype == "application/json"

    exported = json.loads(export_response.data)
    assert "item" in exported
    assert any(row["sku"] == "SKU-1" for row in exported["item"])

    with app.app_context():
        Movement.query.delete()
        Item.query.delete()
        Location.query.delete()
        db.session.commit()

    upload = io.BytesIO(export_response.data)
    upload.name = "backup.json"
    import_response = client.post(
        "/admin/data-backup/import",
        data={"backup_file": (upload, "backup.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert import_response.status_code == 200
    page = import_response.get_data(as_text=True)
    assert "Backup imported successfully" in page

    with app.app_context():
        restored_items = Item.query.all()
        restored_locations = Location.query.all()
        restored_movements = Movement.query.all()

        assert len(restored_items) == 1
        assert restored_items[0].sku == "SKU-1"
        assert len(restored_locations) == 1
        assert restored_locations[0].code == "MAIN"
        assert len(restored_movements) == 1
        assert restored_movements[0].quantity == 5
