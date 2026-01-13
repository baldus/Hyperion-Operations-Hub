import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    OpenOrderLine,
    OpenOrderSystemState,
    OpenOrderUpload,
    Role,
    User,
)
from invapp.services.open_orders import (
    _natural_key_for,
    build_open_orders_diff,
    import_open_orders_rows,
)


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        db.session.expire_on_commit = False
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def superuser_id(app):
    with app.app_context():
        role = Role.query.filter_by(name="admin").first()
        if role is None:
            role = Role(name="admin")
            db.session.add(role)
            db.session.flush()
        user = User.query.filter_by(username="superuser").first()
        if user is None:
            user = User(username="superuser")
            user.set_password("password")
            user.roles.append(role)
            db.session.add(user)
        if role not in user.roles:
            user.roles.append(role)
        db.session.commit()
        return user.id


def _row(**kwargs):
    data = {
        "so_no": "SO-100",
        "so_state": "Open",
        "so_date": datetime(2024, 9, 1).date(),
        "ship_by": datetime(2024, 9, 15).date(),
        "customer_id": "C-1",
        "customer_name": "Acme",
        "item_id": "ITEM-1",
        "line_description": "Widget",
        "uom": "EA",
        "qty_ordered": 10,
        "qty_shipped": 2,
        "qty_remaining": 8,
        "unit_price": 12.5,
        "part_number": "PN-1",
    }
    data.update(kwargs)
    data["natural_key"] = _natural_key_for(data)
    return data


def test_open_orders_diff_new_still_completed(app, superuser_id):
    with app.app_context():
        previous_upload = OpenOrderUpload(
            uploaded_by_user_id=superuser_id,
            source_filename="prev.xlsx",
        )
        db.session.add(previous_upload)
        db.session.flush()

        still_open_row = _row(so_no="SO-100", item_id="ITEM-1", line_description="Widget")
        completed_row = _row(so_no="SO-200", item_id="ITEM-2", line_description="Gadget")
        new_row = _row(so_no="SO-300", item_id="ITEM-3", line_description="New")

        still_open_line = OpenOrderLine(
            natural_key=still_open_row["natural_key"],
            so_no=still_open_row["so_no"],
            system_state=OpenOrderSystemState.OPEN,
            last_seen_upload_id=previous_upload.id,
            last_seen_at=datetime.utcnow(),
        )
        completed_line = OpenOrderLine(
            natural_key=completed_row["natural_key"],
            so_no=completed_row["so_no"],
            system_state=OpenOrderSystemState.OPEN,
            last_seen_upload_id=previous_upload.id,
            last_seen_at=datetime.utcnow(),
        )
        db.session.add_all([still_open_line, completed_line])
        db.session.commit()

        current_rows = [
            {**still_open_row, "qty_remaining": 7},
            new_row,
        ]

        diff = build_open_orders_diff(current_rows)

        assert diff.new_keys == {new_row["natural_key"]}
        assert diff.still_open_keys == {still_open_row["natural_key"]}
        assert diff.completed_keys == {completed_row["natural_key"]}
        assert diff.changed_keys == {still_open_row["natural_key"]}


def test_open_orders_reopen_completed_line(app, superuser_id):
    with app.app_context():
        previous_upload = OpenOrderUpload(
            uploaded_by_user_id=superuser_id,
            source_filename="prev.xlsx",
        )
        db.session.add(previous_upload)
        db.session.flush()

        reopened_row = _row(so_no="SO-400", item_id="ITEM-4", line_description="Reopen")
        completed_line = OpenOrderLine(
            natural_key=reopened_row["natural_key"],
            so_no=reopened_row["so_no"],
            system_state=OpenOrderSystemState.COMPLETED,
            completed_upload_id=previous_upload.id,
            completed_at=datetime.utcnow(),
            last_seen_upload_id=previous_upload.id,
            last_seen_at=datetime.utcnow(),
        )
        db.session.add(completed_line)
        db.session.commit()

        user = User.query.get(superuser_id)
        result = import_open_orders_rows([reopened_row], "reopen.xlsx", user)

        reopened = OpenOrderLine.query.filter_by(
            natural_key=reopened_row["natural_key"]
        ).first()

        assert result.diff.new_keys == {reopened_row["natural_key"]}
        assert reopened is not None
        assert reopened.system_state == OpenOrderSystemState.REOPENED
        assert reopened.completed_at is None
