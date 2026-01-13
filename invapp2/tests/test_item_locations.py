import os
import shutil
import sys
import tempfile

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, Movement
from invapp.services.item_locations import apply_smart_item_locations


@pytest.fixture
def app():
    upload_dir = tempfile.mkdtemp()
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "ITEM_ATTACHMENT_UPLOAD_FOLDER": upload_dir,
            "ITEM_ATTACHMENT_ALLOWED_EXTENSIONS": {"pdf"},
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


def test_apply_smart_locations_sets_primary_when_missing(app):
    with app.app_context():
        location = Location(code="MAIN")
        item = Item(sku="ITEM-1", name="Item 1")
        db.session.add_all([location, item])
        db.session.commit()

        apply_smart_item_locations(item, location.id, db.session)
        db.session.commit()

        assert item.default_location_id == location.id
        assert item.secondary_location_id is None
        assert item.point_of_use_location_id is None


def test_apply_smart_locations_sets_secondary_when_primary_has_stock(app):
    with app.app_context():
        primary = Location(code="PRIMARY")
        secondary = Location(code="SECONDARY")
        item = Item(sku="ITEM-2", name="Item 2", default_location=primary)
        db.session.add_all([primary, secondary, item])
        db.session.commit()

        db.session.add(
            Movement(
                item_id=item.id,
                location_id=primary.id,
                quantity=5,
                movement_type="ADJUST",
            )
        )
        db.session.commit()

        apply_smart_item_locations(item, secondary.id, db.session)
        db.session.commit()

        assert item.secondary_location_id == secondary.id


def test_apply_smart_locations_does_not_override_existing_secondary(app):
    with app.app_context():
        primary = Location(code="PRIMARY")
        secondary = Location(code="SECONDARY")
        other = Location(code="OTHER")
        item = Item(
            sku="ITEM-3",
            name="Item 3",
            default_location=primary,
            secondary_location=secondary,
        )
        db.session.add_all([primary, secondary, other, item])
        db.session.commit()

        db.session.add(
            Movement(
                item_id=item.id,
                location_id=primary.id,
                quantity=2,
                movement_type="ADJUST",
            )
        )
        db.session.commit()

        apply_smart_item_locations(item, other.id, db.session)
        db.session.commit()

        assert item.secondary_location_id == secondary.id


def test_apply_smart_locations_requires_primary_stock(app):
    with app.app_context():
        primary = Location(code="PRIMARY")
        secondary = Location(code="SECONDARY")
        item = Item(sku="ITEM-4", name="Item 4", default_location=primary)
        db.session.add_all([primary, secondary, item])
        db.session.commit()

        apply_smart_item_locations(item, secondary.id, db.session)
        db.session.commit()

        assert item.secondary_location_id is None


def test_apply_smart_locations_never_sets_point_of_use(app):
    with app.app_context():
        primary = Location(code="PRIMARY")
        secondary = Location(code="SECONDARY")
        pou = Location(code="POU")
        item = Item(
            sku="ITEM-5",
            name="Item 5",
            default_location=primary,
            point_of_use_location=pou,
        )
        db.session.add_all([primary, secondary, pou, item])
        db.session.commit()

        db.session.add(
            Movement(
                item_id=item.id,
                location_id=primary.id,
                quantity=3,
                movement_type="ADJUST",
            )
        )
        db.session.commit()

        apply_smart_item_locations(item, secondary.id, db.session)
        db.session.commit()

        assert item.point_of_use_location_id == pou.id


def test_item_location_validation_rejects_duplicates(client, app):
    with app.app_context():
        location = Location(code="DUP")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.post(
        "/inventory/item/add",
        data={
            "name": "Duplicate Locations",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "list_price": "",
            "last_unit_cost": "",
            "item_class": "",
            "default_location_id": str(location_id),
            "secondary_location_id": str(location_id),
            "point_of_use_location_id": "",
            "notes": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Primary and secondary locations must be different." in response.data
