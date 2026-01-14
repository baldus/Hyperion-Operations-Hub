from io import BytesIO

import pytest

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    OpenOrder,
    OpenOrderActionItem,
    OpenOrderLine,
    OpenOrderNote,
    Role,
    User,
)
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
def client(app):
    return app.test_client()


def _ensure_role(name: str, description: str = "") -> Role:
    role = Role.query.filter_by(name=name).first()
    if role is None:
        role = Role(name=name, description=description or name.title())
        db.session.add(role)
        db.session.commit()
    return role


def _create_user(username: str, password: str, role_names: list[str]) -> User:
    user = User(username=username)
    user.set_password(password)
    for role_name in role_names:
        role = _ensure_role(role_name)
        user.roles.append(role)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, username: str, password: str):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def _make_workbook_bytes(rows: list[list[object]]) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
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
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_commit_marks_missing_lines_completed(app):
    user = _create_user("adminuser", "pw123", role_names=["admin"])

    first_rows = [
        ["1001", "Open", "2024-09-01", "2024-09-10", "C-1", "Customer", "ITEM-1", "Widget", "EA", 5, 0, 5, 12.34, "PN-1"],
        ["1002", "Open", "2024-09-01", "2024-09-11", "C-1", "Customer", "ITEM-2", "Widget", "EA", 3, 0, 3, 5.0, "PN-2"],
    ]
    second_rows = [
        ["1001", "Open", "2024-09-01", "2024-09-10", "C-1", "Customer", "ITEM-1", "Widget", "EA", 5, 0, 5, 12.34, "PN-1"],
    ]

    first_upload = commit_open_orders_import(
        _make_workbook_bytes(first_rows),
        "open_orders.xlsx",
        user.id,
        None,
        None,
    )

    commit_open_orders_import(
        _make_workbook_bytes(second_rows),
        "open_orders.xlsx",
        user.id,
        first_upload.id,
        None,
    )

    completed_line = OpenOrderLine.query.filter_by(so_no="1002").first()
    assert completed_line is not None
    assert completed_line.status == "complete"
    assert completed_line.completed_at is not None
    assert completed_line.completed_by_user_id == user.id


def test_open_orders_filters_and_notes(client, app):
    admin = _create_user("ordersadmin", "pw123", role_names=["admin"])
    order = OpenOrder(so_no="2001", customer_id="C-2", customer_name="Acme")
    db.session.add(order)
    db.session.flush()
    order_id = order.id
    open_line = OpenOrderLine(so_no="2001", natural_key="key-open", status="open", order_id=order.id)
    completed_line = OpenOrderLine(
        so_no="2002",
        natural_key="key-complete",
        status="complete",
        completed_at=None,
    )
    db.session.add_all([open_line, completed_line])
    db.session.commit()

    _login(client, "ordersadmin", "pw123")

    open_response = client.get("/open_orders/?status=open")
    assert b"2001" in open_response.data
    assert b"2002" not in open_response.data

    completed_response = client.get("/open_orders/?status=completed")
    assert b"2002" in completed_response.data
    assert b"2001" not in completed_response.data

    all_response = client.get("/open_orders/?status=all")
    assert b"2001" in all_response.data
    assert b"2002" in all_response.data

    note_response = client.post(
        f"/open_orders/orders/{order_id}/notes",
        data={"body": "Follow up with customer."},
        follow_redirects=True,
    )
    assert note_response.status_code == 200
    note = OpenOrderNote.query.filter_by(order_id=order_id).first()
    assert note is not None
    assert note.created_by_user_id == admin.id

    item_response = client.post(
        f"/open_orders/orders/{order_id}/action_items",
        data={"title": "Confirm ship date"},
        follow_redirects=True,
    )
    assert item_response.status_code == 200
    action_item = OpenOrderActionItem.query.filter_by(order_id=order_id).first()
    assert action_item is not None
    action_item_id = action_item.id

    toggle_response = client.post(
        f"/open_orders/action_items/{action_item_id}/toggle",
        data={"is_done": "1"},
        follow_redirects=True,
    )
    assert toggle_response.status_code == 200
    refreshed_item = OpenOrderActionItem.query.get(action_item_id)
    assert refreshed_item.is_done is True
    assert refreshed_item.done_at is not None
    assert refreshed_item.done_by_user_id == admin.id
