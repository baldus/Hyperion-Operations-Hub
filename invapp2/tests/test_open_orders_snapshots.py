from io import BytesIO

import pytest

openpyxl = pytest.importorskip("openpyxl")
Workbook = openpyxl.Workbook

from invapp import create_app
from invapp.extensions import db
from invapp.models import OpenOrderLine, OpenOrderLineSnapshot, OpenOrderUpload
from invapp.services.open_orders import commit_open_orders_import


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def session(app):
    return db.session


def _make_workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "SO No",
            "SO State",
            "SO Date",
            "Ship By",
            "Customer ID",
            "Customer Name",
            "Item ID",
            "Line Description",
            "U/M ID",
            "Qty Ordered",
            "Qty Shipped",
            "Qty Remaining",
            "Unit Price",
            "Part Number",
        ]
    )
    sheet.append(
        [
            "1001",
            "Open",
            "2024-09-01",
            "2024-09-10",
            "C-1",
            "Customer",
            "ITEM-1",
            "Widget",
            "EA",
            5,
            0,
            5,
            12.34,
            "PN-1",
        ]
    )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_snapshot_created_at_default(session):
    upload = OpenOrderUpload(source_filename="test.xlsx")
    line = OpenOrderLine(natural_key="key-1")
    session.add_all([upload, line])
    session.flush()

    snapshot = OpenOrderLineSnapshot(
        upload_id=upload.id,
        line_id=line.id,
        snapshot_json={"so_no": "1001"},
    )
    session.add(snapshot)
    session.flush()

    assert snapshot.created_at is not None


def test_commit_import_creates_snapshot_created_at(app):
    file_bytes = _make_workbook_bytes()
    commit_open_orders_import(file_bytes, "open_orders.xlsx", None, None, None)

    snapshot = OpenOrderLineSnapshot.query.first()
    assert snapshot is not None
    assert snapshot.created_at is not None
