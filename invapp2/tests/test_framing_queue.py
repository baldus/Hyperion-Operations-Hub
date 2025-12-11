from decimal import Decimal
import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import GateOrderDetail, Order, OrderStatus, Role, RoutingStep, User
from invapp.routes.orders import GATE_ROUTING_STEPS
from invapp.settings_service import get_decimal, set_decimal


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
def client(app):
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


@pytest.fixture
def limited_client(app):
    client = app.test_client()
    with app.app_context():
        role = Role.query.filter_by(name="production").first()
        if role is None:
            role = Role(name="production")
            db.session.add(role)
            db.session.flush()
        user = User(username="limited")
        user.set_password("secret")
        user.roles = [role]
        db.session.add(user)
        db.session.commit()
    client.post(
        "/auth/login",
        data={"username": "limited", "password": "secret"},
        follow_redirects=True,
    )
    return client


def _create_gate_order(order_number: str, *, height: Decimal, qty: int, panels: int, complete_framing: bool = False):
    order = Order(
        order_number=order_number,
        status=OrderStatus.SCHEDULED,
        order_type="Gates",
    )
    detail = GateOrderDetail(
        order=order,
        item_number=f"ITEM-{order_number}",
        production_quantity=qty,
        panel_count=panels,
        total_gate_height=height,
        al_color="AL",
        insert_color="Acrylic",
        lead_post_direction="L",
        visi_panels="0",
        half_panel_color="None",
    )
    db.session.add(order)
    db.session.add(detail)

    for idx, step_name in enumerate(GATE_ROUTING_STEPS, start=1):
        db.session.add(
            RoutingStep(
                order=order,
                sequence=idx,
                work_cell=step_name,
                description=f"{step_name} step",
                completed=complete_framing and step_name == "Framing",
            )
        )

    db.session.commit()
    return order


def test_settings_service_validation(app):
    with app.app_context():
        assert get_decimal("framing_cut_offset") == Decimal("0")

        record = set_decimal("framing_cut_offset", "1.25", None)
        assert record.value == "1.25"
        assert get_decimal("framing_cut_offset") == Decimal("1.25")

        with pytest.raises(ValueError):
            set_decimal("framing_cut_offset", "-1", None)
        with pytest.raises(ValueError):
            set_decimal("framing_cut_offset", "13.1", None)


def test_framing_queue_calculations(client, app):
    with app.app_context():
        _create_gate_order("ORD-1", height=Decimal("10"), qty=2, panels=3)
        _create_gate_order("ORD-2", height=Decimal("8.5"), qty=1, panels=4)

    response = client.get("/production/framing")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<strong>Total panels required in this queue:</strong> 10" in body
    assert "10.00" in body
    assert "8.50" in body


def test_panel_length_clamped_when_offset_exceeds_height(client, app):
    with app.app_context():
        set_decimal("framing_cut_offset", "12", None)
        _create_gate_order("ORD-3", height=Decimal("5.5"), qty=1, panels=2)

    response = client.get("/production/framing")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "0.00" in body
    assert "Offset exceeds total height" in body


def test_offset_endpoint_permissions(client, limited_client, app):
    with app.app_context():
        _create_gate_order("ORD-4", height=Decimal("10"), qty=1, panels=1)
        limited_user = User.query.filter_by(username="limited").first()
        assert limited_user is not None
        assert not limited_user.has_role("admin")

    unauthorized = limited_client.post(
        "/production/framing/offset", json={"value": "1.5"}, follow_redirects=False
    )
    assert unauthorized.status_code == 403

    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    response = client.post(
        "/production/framing/offset",
        json={"value": "1.25"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["value"] == "1.25"

    refreshed = client.get("/production/framing")
    assert refreshed.status_code == 200
    assert "8.75" in refreshed.get_data(as_text=True)
