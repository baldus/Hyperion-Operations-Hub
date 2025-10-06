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
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )


def test_storage_locations_requires_admin(client):
    response = client.get("/admin/storage-locations")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    response = client.post("/admin/storage-locations/migrate", data={})
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_storage_locations_overview(client, app):
    with app.app_context():
        loc_a = Location(code="A1", description="Shelf A1")
        loc_b = Location(code="B1", description="Shelf B1")
        item = Item(sku="SKU-100", name="Sample Item")
        db.session.add_all([loc_a, loc_b, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=loc_a.id,
            quantity=10,
            movement_type="ADJUST",
            person="Tester",
        )
        db.session.add(movement)
        db.session.commit()

    _login_admin(client)

    response = client.get("/admin/storage-locations")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "A1" in page
    assert "B1" in page
    assert "Movements" in page


def test_storage_location_migration_moves_records(client, app):
    with app.app_context():
        source = Location(code="OLD", description="Legacy location")
        target = Location(code="NEW", description="Primary location")
        item = Item(sku="SKU-200", name="Widget")
        db.session.add_all([source, target, item])
        db.session.commit()

        first_movement = Movement(
            item_id=item.id,
            location_id=source.id,
            quantity=5,
            movement_type="ADJUST",
            person="Tester",
        )
        second_movement = Movement(
            item_id=item.id,
            location_id=source.id,
            quantity=-2,
            movement_type="ADJUST",
            person="Tester",
        )
        db.session.add_all([first_movement, second_movement])
        db.session.commit()

        source_id = source.id
        target_id = target.id

    _login_admin(client)

    response = client.post(
        "/admin/storage-locations/migrate",
        data={
            "from_location_id": str(source_id),
            "to_location_id": str(target_id),
            "confirm_code": "OLD",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Moved 2 movement records from OLD to NEW." in page

    with app.app_context():
        moved_count = Movement.query.filter_by(location_id=target_id).count()
        remaining_count = Movement.query.filter_by(location_id=source_id).count()

        assert moved_count == 2
        assert remaining_count == 0
