import os
import sys
import pytest

# ensure package path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Order, Reservation


@pytest.fixture
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def items(app):
    with app.app_context():
        main = Item(sku='100', name='Widget')
        comp = Item(sku='200', name='Component')
        db.session.add_all([main, comp])
        db.session.commit()
        return main.id, comp.id


def test_order_creation(client, app, items):
    item_id, comp_id = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD001',
        'item_id': item_id,
        'quantity': '5',
        'bom': f'{comp_id}:2',
        'steps': 'Cutting\nAssembly'
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        order = Order.query.filter_by(order_number='ORD001').first()
        assert order is not None
        assert order.items[0].bom_components[0].component_item_id == comp_id
        assert len(order.steps) == 2
        assert Reservation.query.filter_by(order_item_id=order.items[0].id).count() == 1


def test_bom_validation(client, app, items):
    item_id, comp_id = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD002',
        'item_id': item_id,
        'quantity': '1',
        'bom': '9999:1'
    }, follow_redirects=True)
    assert b'Invalid BOM component item id' in resp.data
    with app.app_context():
        assert Order.query.filter_by(order_number='ORD002').count() == 0


def test_reservation_behavior(client, app, items):
    item_id, comp_id = items
    client.post('/orders/new', data={
        'order_number': 'ORD003',
        'item_id': item_id,
        'quantity': '1',
        'bom': f'{comp_id}:3'
    }, follow_redirects=True)
    with app.app_context():
        order = Order.query.filter_by(order_number='ORD003').first()
        res = Reservation.query.filter_by(order_item_id=order.items[0].id).first()
        assert res is not None
        assert res.item_id == comp_id
        assert res.quantity == 3
