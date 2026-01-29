import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, Movement, Role, User
import invapp.printing.service as printing_service
import invapp.printing.zebra as zebra


@pytest.fixture
def app():
    upload_dir = tempfile.mkdtemp()
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "ITEM_ATTACHMENT_UPLOAD_FOLDER": upload_dir,
            "ZEBRA_PRINTER_HOST": "bad-host",
            "ZEBRA_PRINTER_PORT": 9100,
            "PRINT_DRY_RUN": False,
            "INVENTORY_MOVE_AUTO_PRINT_DEFAULT": False,
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


def create_user(app, username="operator", password="password", role_names=()):
    with app.app_context():
        user = User(username=username)
        user.set_password(password)
        roles = []
        for role_name in role_names:
            role = Role.query.filter_by(name=role_name).first()
            if role is None:
                role = Role(name=role_name)
                db.session.add(role)
            roles.append(role)
        user.roles = roles
        db.session.add(user)
        db.session.commit()
    return user


def login(client, username="operator", password="password"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def _seed_move_inventory(app):
    with app.app_context():
        from_location = Location(code="STG-01")
        to_location = Location(code="LINE-01")
        item = Item(sku="ITEM-1", name="Widget", unit="ea")
        db.session.add_all([from_location, to_location, item])
        db.session.commit()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=from_location.id,
                quantity=10,
                movement_type="ADJUST",
                date=datetime.utcnow(),
            )
        )
        db.session.commit()
        return item.id, from_location.id, to_location.id


def test_move_succeeds_even_if_printer_fails(client, app, monkeypatch):
    item_id, from_location_id, to_location_id = _seed_move_inventory(app)

    def fail_connection(_addr):
        raise OSError("printer down")

    monkeypatch.setattr(zebra.socket, "create_connection", fail_connection)

    payload = json.dumps([{"item_id": item_id, "batch_id": None, "move_qty": "3"}])
    response = client.post(
        "/inventory/move",
        data={
            "from_location_id": from_location_id,
            "to_location_id": to_location_id,
            "reference": "Transfer Test",
            "lines": payload,
            "print_label_after_move": "on",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Transfer completed but printing failed" in response.data

    with app.app_context():
        moves = Movement.query.filter(
            Movement.movement_type.in_(["MOVE_OUT", "MOVE_IN"])
        ).all()
        assert len(moves) == 2


def test_reprint_route_succeeds_in_dry_run(client, app, monkeypatch):
    with app.app_context():
        location = Location(code="LINE-02")
        item = Item(sku="ITEM-2", name="Gasket", unit="ea")
        db.session.add_all([location, item])
        db.session.commit()
        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            movement_type="MOVE_IN",
            reference="Dry Run Transfer",
            person="tester",
            date=datetime.utcnow(),
        )
        db.session.add(movement)
        db.session.commit()
        movement_id = movement.id

    app.config["PRINT_DRY_RUN"] = True
    called = {}

    def fake_render(process, context):
        called["process"] = process
        return "^XA^XZ"

    monkeypatch.setattr(printing_service, "render_label_for_process", fake_render)

    response = client.post(
        f"/inventory/move/{movement_id}/print-label", follow_redirects=True
    )

    assert response.status_code == 200
    assert b"Transfer label reprinted" in response.data
    assert called["process"] == "InventoryTransferLabel"


def test_item_print_requires_permission(app):
    create_user(app, role_names=("quality",))
    client = app.test_client()
    login(client, username="operator", password="password")

    with app.app_context():
        item = Item(sku="ITEM-3", name="Bracket", unit="ea")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(f"/inventory/item/{item_id}/print-label")
    assert response.status_code == 403


def test_item_print_warns_when_printer_unconfigured(client, app):
    with app.app_context():
        item = Item(sku="ITEM-4", name="Panel", unit="ea")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    app.config["ZEBRA_PRINTER_HOST"] = ""
    app.config["ZEBRA_PRINTER_PORT"] = None

    response = client.post(
        f"/inventory/item/{item_id}/print-label", follow_redirects=True
    )

    assert response.status_code == 200
    assert b"Unable to print item label" in response.data
