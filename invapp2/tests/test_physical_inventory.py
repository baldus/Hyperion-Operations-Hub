import io
import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    Location,
    Movement,
)
from invapp.physical_inventory.services import build_reconciliation_rows


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
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


@pytest.fixture
def sample_item(app):
    with app.app_context():
        item = Item(sku="ITEM-1", name="Widget", unit="ea")
        db.session.add(item)
        db.session.commit()
        return item


@pytest.fixture
def sample_location(app):
    with app.app_context():
        location = Location(code="A1", description="Main Rack")
        db.session.add(location)
        db.session.commit()
        return location


def _upload_snapshot(client, csv_text, **fields):
    data = {
        "snapshot_csv": (io.BytesIO(csv_text.encode("utf-8")), "snapshot.csv"),
    }
    data.update(fields)
    return client.post(
        "/physical-inventory/snapshots/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_snapshot_csv_missing_headers(client):
    response = _upload_snapshot(client, "item_code\nITEM-1\n")
    assert b"Missing required headers" in response.data
    assert b"Accepted headers" in response.data


def test_snapshot_csv_unknown_item(client):
    csv_text = "item_code,system_total_qty\nUNKNOWN,5\n"
    response = _upload_snapshot(client, csv_text)
    assert b"unknown item_code" in response.data


def test_snapshot_csv_bad_qty(client, sample_item):
    csv_text = "item_code,system_total_qty\nITEM-1,abc\n"
    response = _upload_snapshot(client, csv_text)
    assert b"must be numeric" in response.data


def test_snapshot_creation_and_count_lines(client, app, sample_item, sample_location):
    with app.app_context():
        movement = Movement(
            item_id=sample_item.id,
            location_id=sample_location.id,
            quantity=Decimal("1"),
            movement_type="RECEIPT",
        )
        db.session.add(movement)
        db.session.commit()

    csv_text = "item_code,system_total_qty\nITEM-1,10\n"
    response = _upload_snapshot(client, csv_text, name="Q1 Snapshot")
    assert b"Snapshot created" in response.data

    with app.app_context():
        snapshot = InventorySnapshot.query.first()
        assert snapshot is not None
        assert snapshot.name == "Q1 Snapshot"
        assert snapshot.lines[0].system_total_qty == Decimal("10")
        count_line = InventoryCountLine.query.first()
    assert count_line is not None
    assert count_line.location_id == sample_location.id


def test_snapshot_csv_header_aliases(client, sample_item):
    csv_text = "sku,qty\nITEM-1,8\n"
    response = _upload_snapshot(client, csv_text, name="Alias Snapshot")
    assert b"Snapshot created" in response.data


def test_snapshot_csv_manual_mapping(client, sample_item):
    csv_text = "Part Number,ERP Total\nITEM-1,12\n"
    response = _upload_snapshot(
        client,
        csv_text,
        name="Mapped Snapshot",
        item_code_column="Part Number",
        system_total_qty_column="ERP Total",
    )
    assert b"Snapshot created" in response.data


def test_reconciliation_math(client, app, sample_item, sample_location):
    with app.app_context():
        snapshot = InventorySnapshot(
            name="Math",
            created_by_user_id=1,
        )
        db.session.add(snapshot)
        db.session.flush()
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=sample_item.id,
                system_total_qty=Decimal("5"),
            )
        )
        db.session.add(
            InventoryCountLine(
                snapshot_id=snapshot.id,
                item_id=sample_item.id,
                location_id=sample_location.id,
                counted_qty=Decimal("7"),
            )
        )
        db.session.commit()

        rows = build_reconciliation_rows(snapshot.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.counted_total_qty == Decimal("7")
        assert row.variance == Decimal("2")
        assert row.status == "OVER"


def test_export_endpoints(client, app, sample_item, sample_location):
    with app.app_context():
        snapshot = InventorySnapshot(name="Export", created_by_user_id=1)
        db.session.add(snapshot)
        db.session.flush()
        db.session.add(
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=sample_item.id,
                system_total_qty=Decimal("3"),
            )
        )
        db.session.add(
            InventoryCountLine(
                snapshot_id=snapshot.id,
                item_id=sample_item.id,
                location_id=sample_location.id,
                counted_qty=Decimal("3"),
            )
        )
        db.session.commit()

    location_response = client.get(
        f"/physical-inventory/snapshots/{snapshot.id}/export/location-sheet.csv"
    )
    assert location_response.status_code == 200
    assert b"location_code" in location_response.data
    assert b"ITEM-1" in location_response.data

    reconciliation_response = client.get(
        f"/physical-inventory/snapshots/{snapshot.id}/export/reconciliation.csv"
    )
    assert reconciliation_response.status_code == 200
    assert b"item_code" in reconciliation_response.data
    assert b"MATCH" in reconciliation_response.data
