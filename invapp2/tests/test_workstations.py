import os
import sys
from datetime import date, datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Order, OrderLine, OrderStatus, RoutingStep


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
    return app.test_client()


@pytest.fixture
def sample_data(app):
    with app.app_context():
        widget = Item(sku="FG-100", name="Widget")
        panel = Item(sku="FG-200", name="Panel")

        order_one = Order(
            order_number="ORD-100",
            status=OrderStatus.OPEN,
            promised_date=date(2024, 1, 5),
        )
        order_one.order_lines.append(OrderLine(item=widget, quantity=5))
        order_one.routing_steps.append(
            RoutingStep(sequence=1, work_cell="Cutting", description="Cut raw stock")
        )
        order_one.routing_steps.append(
            RoutingStep(sequence=2, work_cell="Assembly", description="Assemble frame")
        )

        order_two = Order(
            order_number="ORD-200",
            status=OrderStatus.SCHEDULED,
            promised_date=date(2024, 1, 3),
        )
        order_two_line = OrderLine(item=panel, quantity=2)
        order_two.order_lines.append(order_two_line)
        first_step = RoutingStep(
            sequence=1,
            work_cell="Cutting",
            description="Prep panel",
            completed=True,
            completed_at=datetime(2024, 1, 1, 8, 0, 0),
        )
        order_two.routing_steps.extend(
            [
                first_step,
                RoutingStep(
                    sequence=2,
                    work_cell="Assembly",
                    description="Mount panel",
                ),
            ]
        )

        blocked_order = Order(
            order_number="ORD-300",
            status=OrderStatus.OPEN,
        )
        blocked_order.order_lines.append(OrderLine(item=widget, quantity=1))
        blocked_order.routing_steps.extend(
            [
                RoutingStep(
                    sequence=1,
                    work_cell=None,
                    description="Office review",
                ),
                RoutingStep(
                    sequence=2,
                    work_cell="Assembly",
                    description="Final assembly",
                ),
            ]
        )

        waiting_material = Order(
            order_number="ORD-400",
            status=OrderStatus.WAITING_MATERIAL,
        )
        waiting_material.order_lines.append(OrderLine(item=widget, quantity=3))
        waiting_material.routing_steps.append(
            RoutingStep(sequence=1, work_cell="Cutting", description="Cut frame")
        )

        db.session.add_all(
            [
                widget,
                panel,
                order_one,
                order_two,
                blocked_order,
                waiting_material,
            ]
        )
        db.session.commit()


def test_station_overview_lists_waiting_jobs(client, sample_data):
    response = client.get("/work/stations")
    assert response.status_code == 200
    page = response.get_data(as_text=True)

    assert "Workstation Overview" in page
    assert "Cutting" in page
    assert "Assembly" in page
    assert "ORD-300" not in page  # blocked by unassigned step
    assert "ORD-400" not in page  # waiting on material orders are excluded
    assert "2 jobs waiting" in page


def test_station_detail_shows_queue_for_slug(client, sample_data):
    response = client.get("/work/stations/cutting")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "ORD-100" in page
    assert "Cut raw stock" in page
    assert "ORD-200" not in page

    assembly_page = client.get("/work/stations/assembly")
    assert assembly_page.status_code == 200
    assembly_html = assembly_page.get_data(as_text=True)
    assert "ORD-200" in assembly_html
    assert "Mount panel" in assembly_html
    assert "ORD-300" not in assembly_html


def test_station_detail_unknown_slug_returns_404(client, sample_data):
    response = client.get("/work/stations/unknown")
    assert response.status_code == 404
