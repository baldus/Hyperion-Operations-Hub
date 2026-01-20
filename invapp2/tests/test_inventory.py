import base64
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    Batch,
    Item,
    ItemAttachment,
    Location,
    Movement,
    Order,
    OrderComponent,
    OrderLine,
    OrderStatus,
    RoutingStep,
    RoutingStepComponent,
    RoutingStepConsumption,
    Role,
    User,
)
import invapp.routes.inventory as inventory
from invapp.routes.inventory import (
    AUTO_SKU_START,
    STOCK_IMPORT_FIELDS,
    UNASSIGNED_LOCATION_CODE,
    _ensure_placeholder_location,
)


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


@pytest.fixture
def anon_client(app):
    return app.test_client()


def create_user(app, username="operator", password="password", role_names=("inventory",)):
    with app.app_context():
        user = User(username=username)
        user.set_password(password)

        assigned_roles = []
        if role_names:
            for role_name in role_names:
                role = Role.query.filter_by(name=role_name).first()
                if role is None:
                    role = Role(name=role_name)
                    db.session.add(role)
                assigned_roles.append(role)

        user.roles = assigned_roles
        db.session.add(user)
        db.session.commit()
    return user


def login(client, username="operator", password="password"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture
def stock_items(app):
    with app.app_context():
        location = Location(code="MAIN")
        low_item = Item(sku="LOW-1", name="Low Item", min_stock=100)
        near_item = Item(sku="NEAR-1", name="Near Item", min_stock=100)
        ok_item = Item(sku="OK-1", name="OK Item", min_stock=100)
        db.session.add_all([location, low_item, near_item, ok_item])
        db.session.commit()

        movements = [
            Movement(
                item_id=low_item.id,
                location_id=location.id,
                quantity=100,
                movement_type="ADJUST",
            ),
            Movement(
                item_id=near_item.id,
                location_id=location.id,
                quantity=110,
                movement_type="ADJUST",
            ),
            Movement(
                item_id=ok_item.id,
                location_id=location.id,
                quantity=140,
                movement_type="ADJUST",
            ),
        ]
        db.session.add_all(movements)
        db.session.commit()

        return {
            "low": low_item.sku,
            "near": near_item.sku,
            "ok": ok_item.sku,
        }


def test_edit_item_allows_attachment_upload(client, app):
    with app.app_context():
        item = Item(sku="ATTACH-1", name="Attachment Item", unit="ea")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "sku": "ATTACH-1",
            "name": "Attachment Item",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "list_price": "",
            "last_unit_cost": "",
            "item_class": "",
            "notes": "",
            "attachment": (io.BytesIO(b"spec"), "spec.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        attachments = ItemAttachment.query.filter_by(item_id=item_id).all()
        assert len(attachments) == 1
        attachment = attachments[0]
        file_path = os.path.join(
            app.config["ITEM_ATTACHMENT_UPLOAD_FOLDER"], attachment.filename
        )
        assert os.path.exists(file_path)

    download_response = client.get(
        f"/inventory/item/{item_id}/attachments/{attachment.id}/download"
    )
    assert download_response.status_code == 200
    content_disposition = download_response.headers.get("Content-Disposition", "")
    assert "spec.pdf" in content_disposition


def test_list_stock_low_filter(client, stock_items):
    response = client.get("/inventory/stock?status=low")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert stock_items["low"] in page
    assert stock_items["near"] not in page
    assert stock_items["ok"] not in page


def test_delete_all_locations_clears_item_references(client, app):
    with app.app_context():
        primary = Location(code="PRIMARY")
        secondary = Location(code="SECONDARY")
        item = Item(
            sku="LOC-REF",
            name="Location Reference",
            default_location=primary,
            secondary_location=secondary,
        )
        db.session.add_all([primary, secondary, item])
        db.session.commit()

    response = client.post(
        "/inventory/locations/delete-all",
        data={"confirm_delete": "DELETE"},
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        refreshed = Item.query.filter_by(sku="LOC-REF").first()
        assert refreshed.default_location_id is None
        assert refreshed.secondary_location_id is None
        assert refreshed.point_of_use_location_id is None
        assert Location.query.count() == 0


def test_delete_all_locations_does_not_call_session_begin(client, app, monkeypatch):
    with app.app_context():
        location = Location(code="BEGIN-LOC")
        db.session.add(location)
        db.session.commit()

    def _fail_begin(*args, **kwargs):
        raise AssertionError("db.session.begin should not be called")

    monkeypatch.setattr(db.session, "begin", _fail_begin)

    response = client.post(
        "/inventory/locations/delete-all",
        data={"confirm_delete": "DELETE"},
        follow_redirects=True,
    )

    assert response.status_code == 200


def test_list_stock_includes_location_metadata(client, app):
    with app.app_context():
        primary = Location(code="PRIMARY")
        secondary = Location(code="SECONDARY")
        pou = Location(code="POU")
        movement_loc = Location(code="MOVE")
        item = Item(
            sku="LOC-1",
            name="Location Item",
            default_location=primary,
            secondary_location=secondary,
            point_of_use_location=pou,
        )
        db.session.add_all([primary, secondary, pou, movement_loc, item])
        db.session.flush()

        first_date = datetime(2024, 1, 1, 8, 30)
        second_date = first_date + timedelta(days=1, hours=4)
        db.session.add_all(
            [
                Movement(
                    item_id=item.id,
                    location_id=primary.id,
                    quantity=Decimal("5"),
                    movement_type="ADJUST",
                    date=first_date,
                ),
                Movement(
                    item_id=item.id,
                    location_id=movement_loc.id,
                    quantity=Decimal("2"),
                    movement_type="ADJUST",
                    date=second_date,
                ),
            ]
        )
        db.session.commit()

    with app.app_context():
        overview_query, _, _, _, _, _, _ = inventory._stock_overview_query()
        row = overview_query.filter(Item.sku == "LOC-1").one()

    (
        item_row,
        total_qty,
        location_count,
        last_updated,
        primary_loc,
        secondary_loc,
        pou_loc,
    ) = row
    assert item_row.name == "Location Item"
    assert primary_loc.code == "PRIMARY"
    assert secondary_loc.code == "SECONDARY"
    assert pou_loc.code == "POU"
    assert float(total_qty) == 7.0
    assert int(location_count) == 2
    assert last_updated == second_date


def _default_remove_reason(app):
    reasons = app.config.get("INVENTORY_REMOVE_REASONS", [])
    if isinstance(reasons, str):
        reasons = [reason.strip() for reason in reasons.split(",") if reason.strip()]
    return reasons[0] if reasons else "Adjustment"


def test_remove_from_location_all_creates_movement(client, app):
    with app.app_context():
        location = Location(code="REM-LOC")
        item = Item(sku="REM-1", name="Remove Item")
        db.session.add_all([location, item])
        db.session.flush()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location.id,
                quantity=Decimal("8"),
                movement_type="ADJUST",
            )
        )
        db.session.commit()
        item_id = item.id
        location_id = location.id
        reason = _default_remove_reason(app)

    response = client.post(
        "/inventory/remove_from_location",
        data={
            "item_id": item_id,
            "location_id": location_id,
            "remove_mode": "all",
            "reason": reason,
            "notes": "testing",
            "next": f"/inventory/stock/{item_id}",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(Movement.item_id == item_id, Movement.location_id == location_id)
            .scalar()
        )
        assert Decimal(total or 0) == 0
        removal = Movement.query.filter_by(
            item_id=item_id,
            location_id=location_id,
            movement_type="REMOVE_FROM_LOCATION",
        ).one()
        assert reason in (removal.reference or "")


def test_remove_from_location_partial_reduces_quantity(client, app):
    with app.app_context():
        location = Location(code="REM-LOC2")
        item = Item(sku="REM-2", name="Remove Item 2")
        db.session.add_all([location, item])
        db.session.flush()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location.id,
                quantity=Decimal("10"),
                movement_type="ADJUST",
            )
        )
        db.session.commit()
        item_id = item.id
        location_id = location.id
        reason = _default_remove_reason(app)

    response = client.post(
        "/inventory/remove_from_location",
        data={
            "item_id": item_id,
            "location_id": location_id,
            "remove_mode": "partial",
            "quantity": "3",
            "reason": reason,
            "next": f"/inventory/stock/{item_id}",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(Movement.item_id == item_id, Movement.location_id == location_id)
            .scalar()
        )
        assert Decimal(total or 0) == Decimal("7")


def test_remove_from_location_rejects_overage(client, app):
    with app.app_context():
        location = Location(code="REM-LOC3")
        item = Item(sku="REM-3", name="Remove Item 3")
        db.session.add_all([location, item])
        db.session.flush()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location.id,
                quantity=Decimal("5"),
                movement_type="ADJUST",
            )
        )
        db.session.commit()
        item_id = item.id
        location_id = location.id
        reason = _default_remove_reason(app)

    response = client.post(
        "/inventory/remove_from_location",
        data={
            "item_id": item_id,
            "location_id": location_id,
            "remove_mode": "partial",
            "quantity": "12",
            "reason": reason,
            "next": f"/inventory/stock/{item_id}",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Removal quantity exceeds available stock." in response.get_data(as_text=True)

    with app.app_context():
        total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(Movement.item_id == item_id, Movement.location_id == location_id)
            .scalar()
        )
        assert Decimal(total or 0) == Decimal("5")


def test_remove_from_location_requires_admin(app, anon_client):
    create_user(app, username="operator", password="password", role_names=("inventory",))
    login(anon_client, username="operator", password="password")

    response = anon_client.post(
        "/inventory/remove_from_location",
        data={
            "item_id": 1,
            "location_id": 1,
            "remove_mode": "all",
            "reason": "Adjustment",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


def _create_pending_receipt(app, *, sku="PEND-1", location_code="PEND-LOC"):
    with app.app_context():
        location = Location(code=location_code)
        item = Item(sku=sku, name="Pending Item")
        db.session.add_all([location, item])
        db.session.flush()
        batch = Batch(item_id=item.id, lot_number=f"{sku}-LOT", quantity=0)
        db.session.add(batch)
        db.session.flush()
        receipt = Movement(
            item_id=item.id,
            batch_id=batch.id,
            location_id=location.id,
            quantity=Decimal("0"),
            movement_type="RECEIPT",
            reference="Receipt (quantity pending)",
        )
        db.session.add(receipt)
        db.session.commit()
        return {
            "location_id": location.id,
            "location_code": location.code,
            "item_id": item.id,
            "batch_id": batch.id,
            "receipt_id": receipt.id,
        }


def test_location_list_includes_pending_receipts(client, app):
    pending = _create_pending_receipt(app)

    response = client.get("/inventory/locations")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert pending["location_code"] in page
    assert "Qty Pending" in page


def test_locations_row_filter(client, app):
    with app.app_context():
        locations = [
            Location(code="01-a-12", description="Row A"),
            Location(code="2-B-03", description="Row B"),
            Location(code="NOPE", description="Invalid"),
        ]
        db.session.add_all(locations)
        db.session.commit()

    response = client.get("/inventory/locations?row=a&size=50")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "01-a-12" in page
    assert "2-B-03" not in page
    assert "NOPE" not in page


def test_locations_description_filter_case_insensitive(client, app):
    with app.app_context():
        locations = [
            Location(code="1-A-1", description="Rack Alpha"),
            Location(code="1-A-2", description="rack beta"),
            Location(code="1-A-3", description="Shelf Gamma"),
        ]
        db.session.add_all(locations)
        db.session.commit()

    response = client.get("/inventory/locations?q=RACK&size=50")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "1-A-1" in page
    assert "1-A-2" in page
    assert "1-A-3" not in page


def test_locations_sorting_by_row_and_description(client, app):
    with app.app_context():
        locations = [
            Location(code="1-A-10", description="Alpha"),
            Location(code="1-A-2", description="alpha"),
            Location(code="2-A-1", description="Zulu"),
            Location(code="1-B-1", description="Beta"),
        ]
        db.session.add_all(locations)
        db.session.commit()

    response = client.get("/inventory/locations?sort=row&dir=asc&size=50")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    row_order = [page.index(code) for code in ["1-A-2", "1-A-10", "2-A-1", "1-B-1"]]
    assert row_order == sorted(row_order)

    response = client.get("/inventory/locations?sort=description&dir=asc&size=50")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    description_order = [page.index(code) for code in ["1-A-2", "1-A-10", "1-B-1"]]
    assert description_order == sorted(description_order)


def test_pending_receipt_set_qty_updates_stock(client, app):
    pending = _create_pending_receipt(app, sku="PEND-SET", location_code="PEND-SET-LOC")

    response = client.post(
        f"/inventory/pending/{pending['receipt_id']}/set_qty",
        data={"quantity": "5", "next": f"/inventory/location/{pending['location_id']}/edit"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(
                Movement.item_id == pending["item_id"],
                Movement.location_id == pending["location_id"],
            )
            .scalar()
        )
        assert Decimal(total or 0) == Decimal("5")
        pending_receipt = Movement.query.get(pending["receipt_id"])
        assert "quantity pending" not in (pending_receipt.reference or "").lower()
        batch = Batch.query.get(pending["batch_id"])
        assert Decimal(batch.quantity or 0) == Decimal("5")


def test_pending_receipt_move_updates_location(client, app):
    pending = _create_pending_receipt(app, sku="PEND-MOVE", location_code="MOVE-A")
    with app.app_context():
        new_location = Location(code="MOVE-B")
        db.session.add(new_location)
        db.session.commit()
        new_location_id = new_location.id

    response = client.post(
        f"/inventory/pending/{pending['receipt_id']}/move",
        data={"to_location_id": new_location_id, "next": "/inventory/locations"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        moved_receipt = Movement.query.get(pending["receipt_id"])
        assert moved_receipt.location_id == new_location_id
        move_log = Movement.query.filter_by(
            item_id=pending["item_id"],
            batch_id=pending["batch_id"],
            location_id=new_location_id,
            movement_type="MOVE_PENDING",
        ).one()
        assert move_log.quantity == 0


def test_pending_receipt_remove_clears_reference(client, app):
    pending = _create_pending_receipt(app, sku="PEND-REMOVE", location_code="REMOVE-LOC")

    response = client.post(
        f"/inventory/pending/{pending['receipt_id']}/remove",
        data={"reason": "Scrap", "next": "/inventory/locations"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        pending_receipt = Movement.query.get(pending["receipt_id"])
        assert "voided" in (pending_receipt.reference or "").lower()
        removal = Movement.query.filter_by(
            item_id=pending["item_id"],
            batch_id=pending["batch_id"],
            location_id=pending["location_id"],
            movement_type="REMOVE_FROM_LOCATION",
        ).one()
        assert removal.quantity == 0


def test_stock_overview_shows_zero_batches(client, app):
    with app.app_context():
        item = Item(sku="ZERO-BATCH", name="Zero Batch Item")
        db.session.add(item)
        db.session.commit()

    response = client.get("/inventory/stock")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "ZERO-BATCH" in page
    assert "0 Batches" in page


def test_stock_detail_shows_zero_batches(client, app):
    with app.app_context():
        item = Item(sku="ZERO-BATCH-DETAIL", name="Zero Batch Detail")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.get(f"/inventory/stock/{item_id}")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "0 Batches" in page

def test_list_stock_primary_location_placeholder(client, app):
    with app.app_context():
        secondary = Location(code="SECONDARY")
        pou = Location(code="POU")
        movement_loc = Location(code="MOVE")
        item = Item(
            sku="NO-PRIMARY",
            name="No Primary Item",
            secondary_location=secondary,
            point_of_use_location=pou,
        )
        db.session.add_all([secondary, pou, movement_loc, item])
        db.session.flush()

        db.session.add(
            Movement(
                item_id=item.id,
                location_id=movement_loc.id,
                quantity=Decimal("1"),
                movement_type="ADJUST",
                date=datetime(2024, 2, 1, 9, 0),
            )
        )
        db.session.commit()

    response = client.get("/inventory/stock")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "NO-PRIMARY" in page
    assert "—" in page


def test_list_stock_near_filter(client, stock_items):
    response = client.get("/inventory/stock?status=near")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert stock_items["low"] in page
    assert stock_items["near"] in page
    assert stock_items["ok"] not in page


def test_list_stock_all_filter(client, stock_items):
    response = client.get("/inventory/stock")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert stock_items["low"] in page
    assert stock_items["near"] in page
    assert stock_items["ok"] in page


def test_set_stock_quantity_updates_location(client, app):
    with app.app_context():
        item = Item(sku="SET-1", name="Set Item")
        location = Location(code="SET-LOC")
        db.session.add_all([item, location])
        db.session.commit()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=location.id,
                quantity=5,
                movement_type="ADJUST",
            )
        )
        db.session.commit()
        item_id = item.id
        location_id = location.id

    response = client.post(
        f"/inventory/stock/{item_id}/set_quantity",
        data={"location_id": str(location_id), "quantity": "8"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(
                Movement.item_id == item_id,
                Movement.location_id == location_id,
            )
            .scalar()
        )
        assert Decimal(total or 0) == Decimal("8")


def test_transfer_stock_blocks_negative(client, app):
    with app.app_context():
        item = Item(sku="MOVE-1", name="Move Item")
        from_location = Location(code="FROM-LOC")
        to_location = Location(code="TO-LOC")
        db.session.add_all([item, from_location, to_location])
        db.session.commit()
        db.session.add(
            Movement(
                item_id=item.id,
                location_id=from_location.id,
                quantity=5,
                movement_type="ADJUST",
            )
        )
        db.session.commit()
        item_id = item.id
        from_location_id = from_location.id
        to_location_id = to_location.id

    response = client.post(
        f"/inventory/stock/{item_id}/transfer",
        data={
            "from_location_id": str(from_location_id),
            "to_location_id": str(to_location_id),
            "quantity": "10",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from_total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(
                Movement.item_id == item_id,
                Movement.location_id == from_location_id,
            )
            .scalar()
        )
        to_total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(
                Movement.item_id == item_id,
                Movement.location_id == to_location_id,
            )
            .scalar()
        )
        assert Decimal(from_total or 0) == Decimal("5")
        assert Decimal(to_total or 0) == Decimal("0")


def test_receiving_prints_batch_label(client, app, monkeypatch):
    with app.app_context():
        item = Item(sku="WIDGET-1", name="Widget", unit="ea")
        location = Location(code="RCV-01", description="Receiving Dock")
        db.session.add_all([item, location])
        db.session.commit()
        location_id = location.id

    calls: list[tuple[str, dict]] = []

    def fake_print_label(process, context):
        calls.append((process, context))
        return True

    monkeypatch.setattr(
        "invapp.printing.zebra.print_label_for_process", fake_print_label
    )

    response = client.post(
        "/inventory/receiving",
        data={
            "sku": "WIDGET-1",
            "qty": "5",
            "location_id": str(location_id),
            "person": "Dana",
            "po_number": "PO-777",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert calls, "Expected the receiving workflow to attempt label printing"

    process, context = calls[-1]
    assert process == "BatchCreated"
    assert context["Item"]["SKU"] == "WIDGET-1"
    assert context["Location"]["Code"] == "RCV-01"
    assert context["Batch"]["Quantity"] == 5
    assert context["Batch"]["LotNumber"].startswith("WIDGET-1-")
    assert context["Batch"]["PurchaseOrder"] == "PO-777"


def test_receiving_page_shows_defer_option_for_inventory_user(anon_client, app):
    create_user(app, role_names=("inventory",))
    login(anon_client)

    response = anon_client.get("/inventory/receiving", follow_redirects=True)

    assert response.status_code == 200
    assert b"Receive without quantity (add it later)" in response.data


def test_receiving_without_quantity_allows_inventory_user(anon_client, app):
    with app.app_context():
        item = Item(sku="DEFER-1", name="Deferred Widget", unit="ea")
        location = Location(code="RCV-03", description="Deferred Dock")
        db.session.add_all([item, location])
        db.session.commit()
        location_id = location.id

    create_user(app, role_names=("inventory",))
    login(anon_client)

    response = anon_client.post(
        "/inventory/receiving",
        data={
            "sku": "DEFER-1",
            "qty": "",
            "location_id": str(location_id),
            "person": "Alex",
            "po_number": "",
            "defer_qty": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        receipt = Movement.query.filter_by(movement_type="RECEIPT").first()
        assert receipt is not None
        assert receipt.quantity == 0
        assert "quantity pending" in (receipt.reference or "").lower()


def test_receiving_requires_login(anon_client):
    response = anon_client.get("/inventory/receiving", follow_redirects=False)

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_reprint_receiving_label_uses_batch_label(client, app, monkeypatch):
    with app.app_context():
        item = Item(sku="REPRINT-1", name="Reprint Widget", unit="ea")
        location = Location(code="RCV-02", description="Secondary Dock")
        db.session.add_all([item, location])
        db.session.commit()
        location_id = location.id

    calls: list[tuple[str, dict]] = []

    def fake_print_label(process, context):
        calls.append((process, context))
        return True

    monkeypatch.setattr(
        "invapp.printing.zebra.print_label_for_process", fake_print_label
    )

    client.post(
        "/inventory/receiving",
        data={
            "sku": "REPRINT-1",
            "qty": "3",
            "location_id": str(location_id),
            "person": "Casey",
            "po_number": "PO-888",
        },
        follow_redirects=True,
    )

    with app.app_context():
        receipt = Movement.query.filter_by(movement_type="RECEIPT").first()
        assert receipt is not None
        receipt_id = receipt.id
        lot_number = receipt.batch.lot_number
        location_code = receipt.location.code
        quantity = receipt.quantity

    calls.clear()

    response = client.post(
        f"/inventory/receiving/{receipt_id}/reprint",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert len(calls) == 1

    process, context = calls[0]
    assert process == "BatchCreated"
    assert context["Batch"]["LotNumber"] == lot_number
    assert context["Batch"]["Quantity"] == quantity
    assert context["Location"]["Code"] == location_code


def test_receiving_defer_option_available_to_authenticated_user(operator_client, app):
    with app.app_context():
        item = Item(sku="DEFER-1", name="Defer Widget", unit="ea")
        location = Location(code="RCV-03", description="Main Dock")
        db.session.add_all([item, location])
        db.session.commit()

    response = operator_client.get("/inventory/receiving")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Receive without quantity (add it later)" in page


def test_receiving_defer_qty_allows_non_superuser(operator_client, app):
    with app.app_context():
        item = Item(sku="DEFER-2", name="Defer Batch", unit="ea")
        location = Location(code="RCV-04", description="Receiving Dock")
        db.session.add_all([item, location])
        db.session.commit()
        location_id = location.id

    response = operator_client.post(
        "/inventory/receiving",
        data={
            "sku": "DEFER-2",
            "qty": "",
            "location_id": str(location_id),
            "person": "Alex",
            "po_number": "",
            "defer_qty": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        receipt = Movement.query.filter_by(movement_type="RECEIPT").first()
        assert receipt is not None
        assert receipt.quantity == 0
        assert "quantity pending" in (receipt.reference or "").lower()


def test_receiving_requires_login(anon_client):
    response = anon_client.get("/inventory/receiving")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_print_location_label_uses_location_process(client, app, monkeypatch):
    with app.app_context():
        location = Location(code="LOC-99", description="Overflow Racking")
        db.session.add(location)
        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)

        user = User.query.filter_by(username="superuser").first()
        if user and admin_role not in user.roles:
            user.roles.append(admin_role)

        db.session.commit()
        location_id = location.id

    calls: list[tuple[str, dict]] = []

    def fake_print_label(process, context):
        calls.append((process, context))
        return True

    monkeypatch.setattr(
        "invapp.printing.zebra.print_label_for_process", fake_print_label
    )

    response = client.post(
        f"/inventory/location/{location_id}/print-label",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert calls, "Expected location label print to be triggered"

    process, context = calls[-1]
    assert process == "LocationLabel"
    assert context["Location"]["Code"] == "LOC-99"
    assert context["Location"]["Description"] == "Overflow Racking"


def test_add_item_with_notes(client, app):
    response = client.post(
        "/inventory/item/add",
        data={
            "name": "Widget",
            "type": "Component",
            "unit": "ea",
            "description": "Sample widget",
            "min_stock": "5",
            "list_price": "12.34",
            "last_unit_cost": "9.87",
            "item_class": "Hardware",
            "notes": "Handle with care",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        item = Item.query.filter_by(name="Widget").one()
        assert item.sku == "100000"
        assert item.notes == "Handle with care"
        assert item.list_price == Decimal("12.34")
        assert item.last_unit_cost == Decimal("9.87")
        assert item.item_class == "Hardware"


def test_auto_assigned_sku_sequence(client, app):
    first = client.post("/inventory/item/add", data={"name": "First"})
    assert first.status_code == 302

    second = client.post("/inventory/item/add", data={"name": "Second"})
    assert second.status_code == 302

    with app.app_context():
        first_item = Item.query.filter_by(name="First").one()
        second_item = Item.query.filter_by(name="Second").one()

        assert first_item.sku == "100000"
        assert second_item.sku == "100001"


def test_edit_item_updates_notes(client, app):
    with app.app_context():
        item = Item(
            sku="200",
            name="Existing",
            notes="Old notes",
            list_price=Decimal("1.00"),
            last_unit_cost=Decimal("0.50"),
            item_class="Legacy",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "name": "Existing",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "list_price": "2.22",
            "last_unit_cost": "1.11",
            "item_class": "Updated",
            "notes": "Updated notes",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        updated = Item.query.get(item_id)
        assert updated.notes == "Updated notes"
        assert updated.list_price == Decimal("2.22")
        assert updated.last_unit_cost == Decimal("1.11")
        assert updated.item_class == "Updated"

    response = client.post(
        f"/inventory/item/{item_id}/edit",
        data={
            "name": "Existing",
            "type": "",
            "unit": "ea",
            "description": "",
            "min_stock": "0",
            "list_price": "",
            "last_unit_cost": "",
            "item_class": "",
            "notes": "",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        cleared = Item.query.get(item_id)
        assert cleared.notes is None
        assert cleared.list_price is None
        assert cleared.last_unit_cost is None
        assert cleared.item_class is None


def test_edit_item_requires_admin(anon_client, app):
    with app.app_context():
        item = Item(sku="500", name="Admin Only")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = anon_client.get(f"/inventory/item/{item_id}/edit")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
    assert f"next=%2Finventory%2Fitem%2F{item_id}%2Fedit" in response.headers["Location"]


def test_delete_item_blocks_when_referenced(client, app):
    with app.app_context():
        location = Location(code="DEL-LOC")
        item = Item(sku="DEL-1", name="Delete Me")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()
        item_id = item.id

    response = client.post(f"/inventory/item/{item_id}/delete")
    assert response.status_code == 302
    assert f"/inventory/item/{item_id}/edit" in response.headers["Location"]

    with app.app_context():
        assert Item.query.get(item_id) is not None


def test_delete_item_succeeds_without_references(client, app):
    with app.app_context():
        item = Item(sku="FREE-1", name="Free Item")
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(f"/inventory/item/{item_id}/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/items")

    with app.app_context():
        assert Item.query.get(item_id) is None


def test_delete_all_items_requires_admin(anon_client, app):
    with app.app_context():
        db.session.add_all([
            Item(sku="BULK-1", name="Bulk Item 1"),
            Item(sku="BULK-2", name="Bulk Item 2"),
        ])
        db.session.commit()

    response = anon_client.post("/inventory/items/delete-all")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        assert Item.query.count() == 2


def test_delete_all_items_removes_items(client, app):
    with app.app_context():
        db.session.add_all([
            Item(sku="WIPE-1", name="Wipe Item 1"),
            Item(sku="WIPE-2", name="Wipe Item 2"),
        ])
        db.session.commit()

    response = client.post("/inventory/items/delete-all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/items")

    with app.app_context():
        assert Item.query.count() == 0


def test_delete_all_items_blocks_when_dependencies_exist(client, app):
    with app.app_context():
        location = Location(code="KEEP-LOC")
        item = Item(sku="KEEP-1", name="Keep Item")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=1,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()

    response = client.post("/inventory/items/delete-all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/items")

    with client.session_transaction() as flask_session:
        prompt = flask_session.get("delete_all_prompt")
        assert prompt is not None
        assert prompt["deletable_count"] == 0
        assert "stock movements" in prompt["blocked_sources"]

    with app.app_context():
        assert Item.query.count() == 1

    page = client.get("/inventory/items").get_data(as_text=True)
    assert "No items can be deleted until the related records are removed." in page

    with client.session_transaction() as flask_session:
        assert "delete_all_prompt" not in flask_session


def test_delete_all_items_offers_partial_cleanup(client, app):
    with app.app_context():
        location = Location(code="PROMPT-LOC")
        keep_item = Item(sku="PROMPT-KEEP", name="Keep With Movement")
        free_item = Item(sku="PROMPT-FREE", name="Free To Delete")
        db.session.add_all([location, keep_item, free_item])
        db.session.commit()

        movement = Movement(
            item_id=keep_item.id,
            location_id=location.id,
            quantity=1,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()

    response = client.post("/inventory/items/delete-all")
    assert response.status_code == 302

    with client.session_transaction() as flask_session:
        prompt = flask_session.get("delete_all_prompt")
        assert prompt is not None
        assert prompt["deletable_count"] == 1
        assert "stock movements" in prompt["blocked_sources"]

    page = client.get("/inventory/items").get_data(as_text=True)
    assert "Would you like to delete the 1 item that has no related records?" in page
    assert "Delete 1 Available Item" in page

    with client.session_transaction() as flask_session:
        assert "delete_all_prompt" not in flask_session

    with app.app_context():
        assert sorted(item.sku for item in Item.query.all()) == [
            "PROMPT-FREE",
            "PROMPT-KEEP",
        ]


def test_delete_available_items_requires_admin(anon_client, app):
    with app.app_context():
        db.session.add(Item(sku="SAFE-ONLY", name="Safe Item"))
        db.session.commit()

    response = anon_client.post("/inventory/items/delete-available")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        assert Item.query.count() == 1


def test_delete_available_items_removes_unreferenced(client, app):
    with app.app_context():
        location = Location(code="PARTIAL-LOC")
        kept = Item(sku="PARTIAL-KEEP", name="Keep Me")
        removable = Item(sku="PARTIAL-FREE", name="Remove Me")
        db.session.add_all([location, kept, removable])
        db.session.commit()

        movement = Movement(
            item_id=kept.id,
            location_id=location.id,
            quantity=2,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()

    response = client.post("/inventory/items/delete-available")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/items")

    with app.app_context():
        remaining = sorted(item.sku for item in Item.query.all())
        assert remaining == ["PARTIAL-KEEP"]

    page = client.get("/inventory/items").get_data(as_text=True)
    assert "No items can be deleted until the related records are removed." in page


def test_delete_all_stock_requires_admin(anon_client):
    response = anon_client.post("/inventory/stock/delete-all")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_delete_all_stock_removes_records(client, app):
    with app.app_context():
        finished = Item(sku="FG-STOCK", name="Finished Good")
        component = Item(sku="COMP-STOCK", name="Component Item")
        location = Location(code="STOCK-LOC")
        db.session.add_all([finished, component, location])
        db.session.commit()

        batch = Batch(item_id=component.id, lot_number="STOCK-LOT", quantity=5)
        db.session.add(batch)
        db.session.commit()

        movement = Movement(
            item_id=component.id,
            batch_id=batch.id,
            location_id=location.id,
            quantity=5,
            movement_type="RECEIPT",
        )
        db.session.add(movement)
        db.session.commit()

        order = Order(order_number="ORD-STOCK", status=OrderStatus.OPEN)
        line = OrderLine(item_id=finished.id, quantity=1)
        order.order_lines.append(line)
        component_link = OrderComponent(component_item_id=component.id, quantity=1)
        line.components.append(component_link)
        step = RoutingStep(sequence=1, description="Assembly")
        order.routing_steps.append(step)
        usage = RoutingStepComponent(order_component=component_link)
        step.component_links.append(usage)
        db.session.add(order)
        db.session.commit()

        consumption = RoutingStepConsumption(
            routing_step_component_id=usage.id,
            movement_id=movement.id,
            quantity=1,
        )
        db.session.add(consumption)
        db.session.commit()

    response = client.post("/inventory/stock/delete-all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/stock")

    with app.app_context():
        assert Movement.query.count() == 0
        assert Batch.query.count() == 0
        assert RoutingStepConsumption.query.count() == 0


def test_batch_soft_delete_filters_active(app):
    with app.app_context():
        item = Item(sku="SOFT-DEL", name="Soft Delete")
        db.session.add(item)
        db.session.commit()

        batch = Batch(item_id=item.id, lot_number="LOT-DEL", quantity=5)
        db.session.add(batch)
        db.session.commit()

        assert batch.removed_at is None
        assert Batch.query.count() == 1

        batch.soft_delete()
        db.session.commit()

        assert batch.removed_at is not None
        assert Batch.query.count() == 0
        assert Batch.active().count() == 0
        assert Batch.with_removed().count() == 1
        assert Batch.query.get(batch.id) is None
        assert Batch.with_removed().get(batch.id) is not None


def test_delete_all_locations_requires_admin(anon_client, app):
    with app.app_context():
        db.session.add_all([Location(code="LOC-A"), Location(code="LOC-B")])
        db.session.commit()

    response = anon_client.post("/inventory/locations/delete-all")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        assert Location.query.count() == 2


def test_delete_all_locations_blocks_with_movements(client, app):
    with app.app_context():
        location = Location(code="LOCKED")
        item = Item(sku="LOCKED-ITEM", name="Locked Item")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=3,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()

    response = client.post("/inventory/locations/delete-all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/locations")

    with app.app_context():
        assert Location.query.count() == 1
        assert Movement.query.count() == 1


def test_delete_all_locations_removes_all(client, app):
    with app.app_context():
        db.session.add_all([Location(code="DEL-1"), Location(code="DEL-2")])
        db.session.commit()

    response = client.post("/inventory/locations/delete-all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/locations")

    with app.app_context():
        assert Location.query.count() == 0


def test_delete_all_history_requires_admin(anon_client, app):
    with app.app_context():
        item = Item(sku="HIST-ITEM", name="History Item")
        location = Location(code="HIST-LOC")
        db.session.add_all([item, location])
        db.session.commit()

    response = anon_client.post("/inventory/history/delete-all")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_delete_all_history_removes_records(client, app):
    with app.app_context():
        item = Item(sku="HIST-1", name="History Item")
        location = Location(code="HIST-STOCK")
        batch = Batch(item=item, lot_number="HIST-LOT", quantity=3)
        db.session.add_all([item, location, batch])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            batch_id=batch.id,
            location_id=location.id,
            quantity=3,
            movement_type="RECEIPT",
        )
        db.session.add(movement)
        db.session.commit()

    response = client.post("/inventory/history/delete-all")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/history")

    with app.app_context():
        assert Movement.query.count() == 0
        assert Batch.query.count() == 0

def test_edit_location_requires_admin(anon_client, app):
    with app.app_context():
        location = Location(code="EDIT-LOC", description="Old desc")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = anon_client.get(f"/inventory/location/{location_id}/edit")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
    assert f"next=%2Finventory%2Flocation%2F{location_id}%2Fedit" in response.headers["Location"]


def test_edit_location_updates(client, app):
    with app.app_context():
        location = Location(code="STAGE-1", description="Staging")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.post(
        f"/inventory/location/{location_id}/edit",
        data={"code": "STAGE-99", "description": "Updated"},
    )
    assert response.status_code == 302

    with app.app_context():
        updated = Location.query.get(location_id)
        assert updated.code == "STAGE-99"
        assert updated.description == "Updated"


def test_delete_location_blocks_when_movement_exists(client, app):
    with app.app_context():
        location = Location(code="BLOCK-1")
        item = Item(sku="BLOCK-ITEM", name="Block Item")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=10,
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()
        location_id = location.id

    response = client.post(f"/inventory/location/{location_id}/delete")
    assert response.status_code == 302
    assert f"/inventory/location/{location_id}/edit" in response.headers["Location"]

    with app.app_context():
        assert Location.query.get(location_id) is not None


def test_delete_location_succeeds_without_movement(client, app):
    with app.app_context():
        location = Location(code="FREE-LOC")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.post(f"/inventory/location/{location_id}/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inventory/locations")

    with app.app_context():
        assert Location.query.get(location_id) is None


def test_import_export_items_with_notes(client, app):
    with app.app_context():
        existing = Item(sku="300", name="Existing Item", notes="Legacy notes")
        db.session.add(existing)
        db.session.commit()

    csv_data = io.StringIO()
    writer = csv.writer(csv_data)
    from invapp.utils.csv_schema import ITEMS_CSV_HEADERS

    writer.writerow(ITEMS_CSV_HEADERS)
    writer.writerow(
        [
            "",
            "300",
            "Existing Item",
            "",
            "ea",
            "Updated description",
            "12",
            "Updated legacy note",
            "5.50",
            "4.40",
            "Legacy",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    writer.writerow(
        [
            "",
            "",
            "New Item",
            "",
            "ea",
            "Brand new",
            "3",
            "Fresh notes",
            "6.60",
            "5.50",
            "New",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )

    csv_text = csv_data.getvalue()

    response = client.post(
        "/inventory/items/import",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "items.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    page = response.get_data(as_text=True)
    token_match = re.search(r'name="import_token" value="([^"]+)"', page)
    assert token_match
    import_token = token_match.group(1)

    mapping_payload = {
        "step": "mapping",
        "import_token": import_token,

        "mapping_sku": "sku",
        "mapping_name": "name",
        "mapping_type": "type",
        "mapping_unit": "unit",
        "mapping_description": "description",
        "mapping_min_stock": "min_stock",
        "mapping_notes": "notes",
        "mapping_list_price": "list_price",
        "mapping_last_unit_cost": "last_unit_cost",
        "mapping_item_class": "item_class",
    }

    response = client.post("/inventory/items/import", data=mapping_payload)
    assert response.status_code == 302

    with app.app_context():
        updated_existing = Item.query.filter_by(sku="300").one()
        assert updated_existing.description == "Updated description"
        assert updated_existing.min_stock == 12
        assert updated_existing.notes == "Updated legacy note"
        assert updated_existing.list_price == Decimal("5.50")
        assert updated_existing.last_unit_cost == Decimal("4.40")
        assert updated_existing.item_class == "Legacy"

        new_item = Item.query.filter(Item.sku != "300").one()
        assert new_item.notes == "Fresh notes"
        assert new_item.list_price == Decimal("6.60")
        assert new_item.last_unit_cost == Decimal("5.50")
        assert new_item.item_class == "New"

    export_response = client.get("/inventory/items/export")
    assert export_response.status_code == 200

    exported = list(csv.reader(io.StringIO(export_response.data.decode("utf-8"))))
    header = exported[0]
    assert header == ITEMS_CSV_HEADERS

    header_index = {name: idx for idx, name in enumerate(header)}
    rows = {
        row[header_index["sku"]]: row for row in exported[1:]
    }  # keyed by sku
    assert rows["300"][header_index["notes"]] == "Updated legacy note"
    assert rows["300"][header_index["list_price"]] == "5.50"
    assert rows["300"][header_index["last_unit_cost"]] == "4.40"
    assert rows["300"][header_index["item_class"]] == "Legacy"

    # new SKU is auto-generated; grab its notes from the remaining row
    new_rows = [row for sku, row in rows.items() if sku != "300"]
    assert len(new_rows) == 1
    assert int(new_rows[0][header_index["sku"]]) >= 100000
    assert new_rows[0][header_index["notes"]] == "Fresh notes"
    assert new_rows[0][header_index["list_price"]] == "6.60"
    assert new_rows[0][header_index["last_unit_cost"]] == "5.50"
    assert new_rows[0][header_index["item_class"]] == "New"


def test_export_stock_headers_match_schema(client, app):
    with app.app_context():
        item = Item(sku="SKU-1", name="Widget")
        location = Location(code="LOC-A")
        db.session.add_all([item, location])
        db.session.flush()
        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=Decimal("5"),
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()

    response = client.get("/inventory/stock/export")
    assert response.status_code == 200

    from invapp.utils.csv_schema import STOCK_CSV_HEADERS

    exported = list(csv.reader(io.StringIO(response.data.decode("utf-8"))))
    assert exported[0] == STOCK_CSV_HEADERS


def test_stock_export_import_round_trip(client, app):
    with app.app_context():
        item = Item(sku="SKU-2", name="Gadget")
        location = Location(code="LOC-B")
        db.session.add_all([item, location])
        db.session.flush()
        batch = Batch(item_id=item.id, lot_number="LOT-1", quantity=Decimal("5"))
        db.session.add(batch)
        db.session.flush()
        item_id = item.id
        movement = Movement(
            item_id=item.id,
            batch_id=batch.id,
            location_id=location.id,
            quantity=Decimal("5"),
            movement_type="ADJUST",
        )
        db.session.add(movement)
        db.session.commit()

    export_response = client.get("/inventory/stock/export")
    assert export_response.status_code == 200

    exported = list(csv.reader(io.StringIO(export_response.data.decode("utf-8"))))
    header = exported[0]
    row = exported[1]
    header_index = {name: idx for idx, name in enumerate(header)}
    row[header_index["quantity"]] = "7"

    csv_data = io.StringIO()
    writer = csv.writer(csv_data)
    writer.writerow(header)
    writer.writerow(row)

    upload_response = client.post(
        "/inventory/stock/import",
        data={"file": (io.BytesIO(csv_data.getvalue().encode("utf-8")), "stock.csv")},
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200

    upload_page = upload_response.get_data(as_text=True)
    token_match = re.search(r'name="import_token" value="([^"]+)"', upload_page)
    assert token_match
    import_token = token_match.group(1)

    mapping_payload = {"step": "mapping", "import_token": import_token}
    for field in STOCK_IMPORT_FIELDS:
        field_name = field["field"]
        if field_name in header:
            mapping_payload[f"mapping_{field_name}"] = field_name

    mapping_response = client.post("/inventory/stock/import", data=mapping_payload)
    assert mapping_response.status_code == 302

    with app.app_context():
        total = (
            db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
            .filter(Movement.item_id == item_id)
            .scalar()
        )
        assert total == Decimal("12")


def test_items_and_stock_shared_headers_match():
    from invapp.utils.csv_schema import ITEMS_CSV_HEADERS, STOCK_CSV_HEADERS

    shared_headers = {"item_id", "sku", "name"}
    for header in shared_headers:
        assert header in ITEMS_CSV_HEADERS
        assert header in STOCK_CSV_HEADERS

def test_inventory_scan_page(client):
    response = client.get("/inventory/scan")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "cameraPreview" in body


def test_import_items_shows_mapping_page(client):
    csv_text = "sku,name\n100,Widget\n"
    response = client.post(
        "/inventory/items/import",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "items.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Map Imported Columns" in page
    assert "mapping_name" in page


def test_import_items_creates_records_with_mapping(client, app):
    csv_text = "sku,name,min_stock\n100,Widget,5\n,NoSku,3\n"

    upload_response = client.post(
        "/inventory/items/import",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "items.csv")},
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200

    upload_page = upload_response.get_data(as_text=True)
    token_match = re.search(r'name="import_token" value="([^"]+)"', upload_page)
    assert token_match
    import_token = token_match.group(1)


    response = client.post(
        "/inventory/items/import",
        data={
            "step": "mapping",
            "import_token": import_token,

            "mapping_sku": "sku",
            "mapping_name": "name",
            "mapping_min_stock": "min_stock",
        },
    )

    assert response.status_code == 302

    with app.app_context():
        widget = Item.query.filter_by(sku="100").one()
        assert widget.name == "Widget"
        assert widget.min_stock == 5
        assert widget.unit == "ea"

        generated = Item.query.filter(Item.sku != "100").one()
        assert generated.name == "NoSku"
        assert generated.min_stock == 3
        assert generated.sku == str(AUTO_SKU_START)



def test_import_locations_mapping_flow(client, app):
    with app.app_context():
        existing = Location(code="MAIN", description="Existing")
        db.session.add(existing)
        db.session.commit()

    csv_text = "code,description\nMAIN,Updated Main\nSIDE,Side Location\n"

    upload_response = client.post(
        "/inventory/locations/import",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "locations.csv")},
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200

    upload_page = upload_response.get_data(as_text=True)
    assert "Map Location Columns" in upload_page
    token_match = re.search(r'name="import_token" value="([^"]+)"', upload_page)
    assert token_match
    import_token = token_match.group(1)

    mapping_payload = {
        "step": "mapping",
        "import_token": import_token,
        "mapping_code": "code",
        "mapping_description": "description",
    }

    response = client.post("/inventory/locations/import", data=mapping_payload)
    assert response.status_code == 302

    with app.app_context():
        updated = Location.query.filter_by(code="MAIN").one()
        assert updated.description == "Updated Main"

        created = Location.query.filter_by(code="SIDE").one()
        assert created.description == "Side Location"


def test_import_stock_mapping_flow(client, app):
    with app.app_context():
        item = Item(sku="SKU-1", name="Widget")
        main = Location(code="MAIN", description="Main")
        db.session.add_all([item, main])
        db.session.commit()

    csv_text = (
        "sku,location_code,quantity,lot_number,person,reference\n"
        "SKU-1,MAIN,5,BATCH-1,Alex,Initial\n"
        "SKU-1,,3,BATCH-1,,\n"
    )

    upload_response = client.post(
        "/inventory/stock/import",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "stock.csv")},
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200

    upload_page = upload_response.get_data(as_text=True)
    assert "Map Stock Adjustment Columns" in upload_page
    token_match = re.search(r'name="import_token" value="([^"]+)"', upload_page)
    assert token_match
    import_token = token_match.group(1)

    mapping_payload = {
        "step": "mapping",
        "import_token": import_token,
        "mapping_sku": "sku",
        "mapping_location_code": "location_code",
        "mapping_quantity": "quantity",
        "mapping_lot_number": "lot_number",
        "mapping_person": "person",
        "mapping_reference": "reference",
    }

    response = client.post("/inventory/stock/import", data=mapping_payload)
    assert response.status_code == 302

    with app.app_context():
        batch = Batch.query.filter_by(
            item_id=Item.query.filter_by(sku="SKU-1").one().id,
            lot_number="BATCH-1",
        ).one()
        assert batch.quantity == Decimal("8")

        movements = Movement.query.filter_by(item_id=batch.item_id).order_by(Movement.id).all()
        assert len(movements) == 2
        assert movements[0].location.code == "MAIN"
        assert movements[0].quantity == Decimal("5")
        assert movements[0].person == "Alex"
        assert movements[0].reference == "Initial"

        # Second movement should default to the unassigned location when none provided.
        assert movements[1].location.code == UNASSIGNED_LOCATION_CODE
        assert movements[1].quantity == 3
        assert movements[1].person is None
        assert movements[1].reference == "Bulk Adjust"

        placeholder = Location.query.filter_by(code=UNASSIGNED_LOCATION_CODE).one()
        assert placeholder.description == "Unassigned staging location"


def test_import_stock_placeholder_race_condition(client, app, monkeypatch):
    with app.app_context():
        item = Item(sku="SKU-1", name="Widget")
        main = Location(code="MAIN", description="Main")
        db.session.add_all([item, main])
        db.session.commit()

    csv_text = (
        "sku,location_code,quantity,lot_number,person,reference\n"
        "SKU-1,MAIN,5,BATCH-1,Alex,Initial\n"
    )

    upload_response = client.post(
        "/inventory/stock/import",
        data={"file": (io.BytesIO(csv_text.encode("utf-8")), "stock.csv")},
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200

    upload_page = upload_response.get_data(as_text=True)
    token_match = re.search(r'name="import_token" value="([^"]+)"', upload_page)
    assert token_match
    import_token = token_match.group(1)

    original_flush = db.session.flush

    def flaky_flush(*args, **kwargs):
        should_raise = any(
            isinstance(obj, Location) and obj.code == UNASSIGNED_LOCATION_CODE
            for obj in db.session.new
        )
        if should_raise and not getattr(flaky_flush, "triggered", False):
            flaky_flush.triggered = True
            with db.engine.begin() as conn:
                conn.execute(
                    Location.__table__.insert().values(
                        code=UNASSIGNED_LOCATION_CODE,
                        description="Concurrent placeholder",
                    )
                )
            raise IntegrityError("", {}, Exception("duplicate"))
        return original_flush(*args, **kwargs)

    flaky_flush.triggered = False
    monkeypatch.setattr(db.session, "flush", flaky_flush)

    mapping_payload = {
        "step": "mapping",
        "import_token": import_token,
        "mapping_sku": "sku",
        "mapping_location_code": "location_code",
        "mapping_quantity": "quantity",
        "mapping_lot_number": "lot_number",
        "mapping_person": "person",
        "mapping_reference": "reference",
    }

    response = client.post("/inventory/stock/import", data=mapping_payload)
    assert response.status_code == 302

    with app.app_context():
        placeholder = Location.query.filter_by(code=UNASSIGNED_LOCATION_CODE).one()
        assert placeholder.description == "Concurrent placeholder"
        assert Location.query.filter_by(code=UNASSIGNED_LOCATION_CODE).count() == 1


def test_ensure_placeholder_location_retries_until_visible(app, monkeypatch):
    with app.app_context():
        loc_map: dict[str, Location] = {}

        original_flush = db.session.flush

        def flaky_flush(*args, **kwargs):
            should_raise = any(
                isinstance(obj, Location) and obj.code == UNASSIGNED_LOCATION_CODE
                for obj in db.session.new
            )
            if should_raise and flaky_flush.attempts < 2:
                attempt = flaky_flush.attempts
                flaky_flush.attempts += 1
                if attempt == 1:
                    with db.engine.begin() as conn:
                        conn.execute(
                            Location.__table__.insert().values(
                                code=UNASSIGNED_LOCATION_CODE,
                                description="Eventually committed",
                            )
                        )
                raise IntegrityError("", {}, Exception("duplicate"))
            return original_flush(*args, **kwargs)

        flaky_flush.attempts = 0

        monkeypatch.setattr(db.session, "flush", flaky_flush)
        monkeypatch.setattr(inventory.time, "sleep", lambda *_: None)

        placeholder = _ensure_placeholder_location(loc_map)

        assert placeholder.code == UNASSIGNED_LOCATION_CODE
        assert placeholder.description == "Eventually committed"
        assert loc_map[UNASSIGNED_LOCATION_CODE] is placeholder
        assert Location.query.filter_by(code=UNASSIGNED_LOCATION_CODE).count() == 1


def test_move_location_lines_endpoint_returns_lines(client, app):
    with app.app_context():
        source = Location(code="SRC")
        dest = Location(code="DEST")
        item = Item(sku="MOVE-1", name="Move Item", unit="ea")
        batch = Batch(item=item, lot_number="LOT-1")
        db.session.add_all([source, dest, item, batch])
        db.session.commit()
        db.session.add(
            Movement(
                item_id=item.id,
                batch_id=batch.id,
                location_id=source.id,
                quantity=5,
                movement_type="RECEIPT",
            )
        )
        db.session.commit()
        location_id = source.id

    response = client.get(f"/inventory/move/location/{location_id}/lines")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["location"]["id"] == location_id
    assert payload["lines"][0]["sku"] == "MOVE-1"
    assert payload["lines"][0]["lot_number"] == "LOT-1"


def test_move_inventory_lines_success(client, app):
    with app.app_context():
        source = Location(code="MOVE-SRC")
        dest = Location(code="MOVE-DEST")
        item = Item(sku="MOVE-2", name="Move Item 2", unit="ea")
        batch = Batch(item=item, lot_number="LOT-2")
        db.session.add_all([source, dest, item, batch])
        db.session.commit()
        db.session.add(
            Movement(
                item_id=item.id,
                batch_id=batch.id,
                location_id=source.id,
                quantity=5,
                movement_type="RECEIPT",
            )
        )
        db.session.commit()
        source_id = source.id
        dest_id = dest.id
        item_id = item.id
        batch_id = batch.id

    payload = [
        {"item_id": item_id, "batch_id": batch_id, "move_qty": "2"},
    ]
    response = client.post(
        "/inventory/move",
        data={
            "from_location_id": source_id,
            "to_location_id": dest_id,
            "reference": "Move Test",
            "lines": json.dumps(payload),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        movements = Movement.query.filter_by(item_id=item_id).order_by(Movement.id).all()
        assert len(movements) == 3
        assert movements[-2].movement_type == "MOVE_OUT"
        assert movements[-2].quantity == Decimal("-2.000")
        assert movements[-1].movement_type == "MOVE_IN"
        assert movements[-1].quantity == Decimal("2.000")


def test_move_inventory_lines_same_location_rejected(client, app):
    with app.app_context():
        location = Location(code="MOVE-SAME")
        item = Item(sku="MOVE-3", name="Move Item 3", unit="ea")
        batch = Batch(item=item, lot_number="LOT-3")
        db.session.add_all([location, item, batch])
        db.session.commit()
        db.session.add(
            Movement(
                item_id=item.id,
                batch_id=batch.id,
                location_id=location.id,
                quantity=5,
                movement_type="RECEIPT",
            )
        )
        db.session.commit()
        location_id = location.id
        item_id = item.id
        batch_id = batch.id

    payload = [
        {"item_id": item_id, "batch_id": batch_id, "move_qty": "1"},
    ]
    response = client.post(
        "/inventory/move",
        data={
            "from_location_id": location_id,
            "to_location_id": location_id,
            "reference": "Invalid Move",
            "lines": json.dumps(payload),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        assert Movement.query.filter_by(item_id=item_id).count() == 1


def test_move_inventory_lines_rolls_back_on_invalid_line(client, app):
    with app.app_context():
        source = Location(code="ROLL-SRC")
        dest = Location(code="ROLL-DEST")
        item_a = Item(sku="ROLL-1", name="Roll Item 1", unit="ea")
        item_b = Item(sku="ROLL-2", name="Roll Item 2", unit="ea")
        batch_a = Batch(item=item_a, lot_number="LOT-A")
        batch_b = Batch(item=item_b, lot_number="LOT-B")
        db.session.add_all([source, dest, item_a, item_b, batch_a, batch_b])
        db.session.commit()
        db.session.add_all(
            [
                Movement(
                    item_id=item_a.id,
                    batch_id=batch_a.id,
                    location_id=source.id,
                    quantity=5,
                    movement_type="RECEIPT",
                ),
                Movement(
                    item_id=item_b.id,
                    batch_id=batch_b.id,
                    location_id=source.id,
                    quantity=3,
                    movement_type="RECEIPT",
                ),
            ]
        )
        db.session.commit()
        source_id = source.id
        dest_id = dest.id
        item_a_id = item_a.id
        item_b_id = item_b.id
        batch_a_id = batch_a.id
        batch_b_id = batch_b.id

    payload = [
        {"item_id": item_a_id, "batch_id": batch_a_id, "move_qty": "2"},
        {"item_id": item_b_id, "batch_id": batch_b_id, "move_qty": "99"},
    ]
    response = client.post(
        "/inventory/move",
        data={
            "from_location_id": source_id,
            "to_location_id": dest_id,
            "reference": "Rollback Move",
            "lines": json.dumps(payload),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        assert Movement.query.filter_by(item_id=item_a_id).count() == 1
        assert Movement.query.filter_by(item_id=item_b_id).count() == 1
