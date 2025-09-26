from datetime import date, timedelta

import pytest

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, Movement, Order, OrderStatus


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_dashboard_data():
    location = Location(code="MAIN", description="Main Floor")
    low_item = Item(sku="LOW-100", name="Low Stock Widget", min_stock=10)
    out_item = Item(sku="OUT-200", name="Empty Bin Bracket", min_stock=5)
    db.session.add_all([location, low_item, out_item])
    db.session.flush()

    movement = Movement(
        item=low_item,
        location=location,
        quantity=3,
        movement_type="RECEIPT",
    )
    db.session.add(movement)

    today = date.today()
    orders = [
        Order(
            order_number="SO-1001",
            status=OrderStatus.OPEN,
            promised_date=today + timedelta(days=1),
            customer_name="Acme Corp",
        ),
        Order(
            order_number="SO-1002",
            status=OrderStatus.OPEN,
            promised_date=today - timedelta(days=1),
            customer_name="Wayne Industries",
        ),
        Order(
            order_number="SO-1003",
            status=OrderStatus.WAITING_MATERIAL,
            promised_date=today + timedelta(days=5),
        ),
    ]
    db.session.add_all(orders)
    db.session.commit()


def test_home_dashboard_is_public_and_read_only(client, app):
    with app.app_context():
        _seed_dashboard_data()

    response = client.get("/")
    assert response.status_code == 200
    body = response.data

    assert b"public, read-only snapshot" in body
    assert b"<strong>1</strong> due within 3 days" in body
    assert b"Log In to manage orders" in body
    assert b"LOW-100" in body
    assert b"OUT-200" in body


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/orders/"),
        ("post", "/orders/new"),
        ("get", "/inventory/"),
    ],
)
def test_anonymous_access_to_actions_is_blocked(client, method, path):
    request_method = getattr(client, method)
    response = request_method(path, follow_redirects=False)
    assert response.status_code in (302, 401)
    assert "/auth/login" in response.headers.get("Location", "")
