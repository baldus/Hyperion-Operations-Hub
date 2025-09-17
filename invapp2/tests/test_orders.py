import json
import os
import sys
from datetime import date

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
        finished = Item(sku='FG-100', name='Widget')
        component = Item(sku='CMP-200', name='Component')
        db.session.add_all([finished, component])
        db.session.commit()
        return finished, component


def test_order_creation(client, app, items):
    finished, component = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD001',
        'finished_good_sku': finished.sku,
        'quantity': '5',
        'promised_date': '2024-01-10',
        'scheduled_start_date': '2024-01-05',
        'scheduled_completion_date': '2024-01-08',
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 2}
        ]),
        'routing_data': json.dumps([
            {
                'sequence': 1,
                'work_cell': 'Assembly',
                'instructions': 'Assemble parts',
                'components': [component.sku]
            }
        ]),
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        order = Order.query.filter_by(order_number='ORD001').first()
        assert order is not None
        assert order.promised_date == date(2024, 1, 10)
        primary_item = order.primary_item
        assert primary_item.item_id == finished.id
        assert primary_item.quantity == 5
        bom_component = primary_item.bom_components[0]
        assert bom_component.component_item_id == component.id
        assert bom_component.quantity == 2
        assert Reservation.query.filter_by(order_item_id=primary_item.id).one().quantity == 10
        assert len(order.steps) == 1
        step = order.steps[0]
        assert step.sequence == 1
        assert step.work_cell == 'Assembly'
        assert step.description == 'Assemble parts'
        assert step.component_usages[0].bom_component_id == bom_component.id


def test_bom_validation(client, app, items):
    finished, _ = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD002',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'promised_date': '2024-02-01',
        'scheduled_start_date': '2024-01-20',
        'scheduled_completion_date': '2024-01-25',
        'bom_data': json.dumps([
            {'sku': 'MISSING', 'quantity': 1}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': '', 'instructions': 'Step', 'components': ['MISSING']}
        ]),
    }, follow_redirects=True)
    assert b"BOM component SKU 'MISSING' was not found." in resp.data
    with app.app_context():
        assert Order.query.filter_by(order_number='ORD002').count() == 0


def test_reservation_behavior(client, app, items):
    finished, component = items
    client.post('/orders/new', data={
        'order_number': 'ORD003',
        'finished_good_sku': finished.sku,
        'quantity': '2',
        'promised_date': '2024-03-01',
        'scheduled_start_date': '2024-02-20',
        'scheduled_completion_date': '2024-02-25',
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 3}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Cut', 'instructions': 'Prep', 'components': [component.sku]}
        ]),
    }, follow_redirects=True)
    with app.app_context():
        order = Order.query.filter_by(order_number='ORD003').first()
        res = Reservation.query.filter_by(order_item_id=order.items[0].id).first()
        assert res is not None
        assert res.item_id == component.id
        assert res.quantity == 6


def test_component_usage_required(client, app, items):
    finished, component = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD004',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'promised_date': '2024-04-01',
        'scheduled_start_date': '2024-03-20',
        'scheduled_completion_date': '2024-03-22',
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 1}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Prep', 'instructions': 'Do work', 'components': []}
        ]),
    }, follow_redirects=True)
    assert b'Missing usage for' in resp.data
    with app.app_context():
        assert Order.query.filter_by(order_number='ORD004').count() == 0
