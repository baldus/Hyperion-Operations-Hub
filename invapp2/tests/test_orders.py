import json
import os
import sys
from datetime import date

import pytest

# ensure package path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from sqlalchemy.exc import IntegrityError

from invapp.models import (
    Item,
    Order,
    OrderComponent,
    OrderLine,
    Reservation,
    RoutingStep,
    RoutingStepComponent,
)


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
        'customer_name': 'Acme Industries',
        'created_by': 'Alice',
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
        assert order.customer_name == 'Acme Industries'
        assert order.created_by == 'Alice'
        primary_line = order.primary_line
        assert primary_line.item_id == finished.id
        assert primary_line.quantity == 5
        bom_component = primary_line.components[0]
        assert bom_component.component_item_id == component.id
        assert bom_component.quantity == 2
        reservation = Reservation.query.filter_by(order_line_id=primary_line.id).one()
        assert reservation.quantity == 10
        assert len(order.routing_steps) == 1
        step = order.routing_steps[0]
        assert step.sequence == 1
        assert step.work_cell == 'Assembly'
        assert step.description == 'Assemble parts'
        assert step.components[0].id == bom_component.id


def test_bom_validation(client, app, items):
    finished, _ = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD002',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Beta Corp',
        'created_by': 'Bob',
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
        'customer_name': 'Gamma LLC',
        'created_by': 'Charlie',
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
        res = Reservation.query.filter_by(order_line_id=order.order_lines[0].id).first()
        assert res is not None
        assert res.item_id == component.id
        assert res.quantity == 6


def test_component_usage_required(client, app, items):
    finished, component = items
    resp = client.post('/orders/new', data={
        'order_number': 'ORD004',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Delta Co',
        'created_by': 'Diana',
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


def test_routing_status_updates(client, app, items):
    finished, component = items
    client.post('/orders/new', data={
        'order_number': 'ORD005',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Eta Manufacturing',
        'created_by': 'Evan',
        'promised_date': '2024-05-01',
        'scheduled_start_date': '2024-04-20',
        'scheduled_completion_date': '2024-04-25',
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 1}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Assemble', 'instructions': 'Put it together', 'components': [component.sku]}
        ]),
    }, follow_redirects=True)

    with app.app_context():
        order = Order.query.filter_by(order_number='ORD005').first()
        step = order.routing_steps[0]
        assert step.completed is False
        step_id = step.id
        order_id = order.id

    resp = client.post(f'/orders/{order_id}/routing', data={
        'completed_steps': str(step_id)
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        order = Order.query.filter_by(id=order_id).first()
        step = RoutingStep.query.get(step_id)
        assert step.completed is True
        assert step.completed_at is not None
        assert order.routing_progress == 1.0

    resp = client.post(f'/orders/{order_id}/routing', data={}, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        step = RoutingStep.query.get(step_id)
        order = Order.query.filter_by(id=order_id).first()
        assert step.completed is False
        assert step.completed_at is None
        assert order.routing_progress == 0.0


def test_routing_step_component_relationships(app, items):
    finished, component = items
    with app.app_context():
        order = Order(
            order_number='ORDREL',
            promised_date=date(2024, 5, 1),
            scheduled_start_date=date(2024, 4, 1),
            scheduled_completion_date=date(2024, 4, 20),
        )
        line = OrderLine(
            order=order,
            item_id=finished.id,
            quantity=1,
            promised_date=order.promised_date,
            scheduled_start_date=order.scheduled_start_date,
            scheduled_completion_date=order.scheduled_completion_date,
        )
        component_row = OrderComponent(
            order_line=line,
            component_item_id=component.id,
            quantity=2,
        )
        step = RoutingStep(
            order=order,
            sequence=1,
            work_cell='Assembly',
            description='Link component',
        )
        RoutingStepComponent(order_component=component_row, routing_step=step)
        db.session.add(order)
        db.session.commit()

        persisted = Order.query.filter_by(order_number='ORDREL').one()
        assert persisted.routing_steps[0].components[0].id == component_row.id
        assert component_row.routing_steps[0].id == step.id


def test_order_schedule_constraints_enforced(app):
    with app.app_context():
        order = Order(
            order_number='ORDBAD',
            promised_date=date(2024, 1, 1),
            scheduled_start_date=date(2024, 1, 5),
            scheduled_completion_date=date(2024, 1, 3),
        )
        db.session.add(order)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_order_line_schedule_constraints_enforced(app, items):
    finished, _ = items
    with app.app_context():
        order = Order(
            order_number='ORDLINE',
            promised_date=date(2024, 6, 1),
            scheduled_start_date=date(2024, 5, 1),
            scheduled_completion_date=date(2024, 5, 20),
        )
        line = OrderLine(
            order=order,
            item_id=finished.id,
            quantity=1,
            promised_date=date(2024, 5, 10),
            scheduled_start_date=date(2024, 5, 15),
            scheduled_completion_date=date(2024, 5, 12),
        )
        db.session.add_all([order, line])
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
