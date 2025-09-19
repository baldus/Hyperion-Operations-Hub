import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

# ensure package path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from sqlalchemy.exc import IntegrityError

from invapp.models import (
    BillOfMaterial,
    BillOfMaterialComponent,
    Item,
    Location,
    Movement,
    Order,
    OrderComponent,
    OrderLine,
    OrderStatus,
    Reservation,
    RoutingStep,
    RoutingStepComponent,
    RoutingStepConsumption,
)


@pytest.fixture
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
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
def items(app):
    with app.app_context():
        finished = Item(sku='FG-100', name='Widget')
        component = Item(sku='CMP-200', name='Component')
        location = Location(code='MAIN', description='Main Storage')
        db.session.add_all([finished, component, location])
        db.session.commit()
        yield (
            SimpleNamespace(id=finished.id, sku=finished.sku, name=finished.name),
            SimpleNamespace(id=component.id, sku=component.sku, name=component.name),
            SimpleNamespace(id=location.id, code=location.code, description=location.description),
        )


def test_order_creation(client, app, items):
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=100,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    payload = {
        'order_number': 'ORD001',
        'finished_good_sku': finished.sku,
        'quantity': '5',
        'customer_name': 'Acme Industries',
        'created_by': 'Alice',
        'general_notes': 'Initial note\nWith details',
        'promised_date': (today + timedelta(days=15)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=7)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=10)).isoformat(),
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
    }
    resp = client.post('/orders/new', data=payload, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        order = Order.query.filter_by(order_number='ORD001').first()
        assert order is not None
        assert order.promised_date == today + timedelta(days=15)
        assert order.customer_name == 'Acme Industries'
        assert order.created_by == 'Alice'
        assert order.general_notes == 'Initial note\nWith details'
        assert order.status == OrderStatus.SCHEDULED
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
    finished, _, _ = items
    with client.session_transaction() as session:
        session['is_admin'] = True
    resp = client.post('/orders/new', data={
        'order_number': 'ORD002',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Beta Corp',
        'created_by': 'Bob',
        'general_notes': 'Validation notes',
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
    assert b"BOM component SKU &#39;MISSING&#39; was not found." in resp.data
    with app.app_context():
        assert Order.query.filter_by(order_number='ORD002').count() == 0


def test_reservation_behavior(client, app, items):
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=50,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    client.post('/orders/new', data={
        'order_number': 'ORD003',
        'finished_good_sku': finished.sku,
        'quantity': '2',
        'customer_name': 'Gamma LLC',
        'created_by': 'Charlie',
        'general_notes': 'Reservation notes',
        'promised_date': (today + timedelta(days=20)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=5)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=12)).isoformat(),
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 3}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Cut', 'instructions': 'Prep', 'components': [component.sku]}
        ]),
    }, follow_redirects=True)
    with app.app_context():
        order = Order.query.filter_by(order_number='ORD003').first()
        assert order.status == OrderStatus.SCHEDULED
        res = Reservation.query.filter_by(order_line_id=order.order_lines[0].id).first()
        assert res is not None
        assert res.item_id == component.id
        assert res.quantity == 6


def test_waiting_status_when_inventory_insufficient(client, app, items):
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=5,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    client.post('/orders/new', data={
        'order_number': 'ORDWAIT',
        'finished_good_sku': finished.sku,
        'quantity': '3',
        'customer_name': 'Short Supply Co',
        'created_by': 'Sam',
        'general_notes': 'Waiting for material',
        'promised_date': (today + timedelta(days=21)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=9)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=12)).isoformat(),
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 2}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Cut', 'instructions': 'Prep', 'components': [component.sku]}
        ]),
    }, follow_redirects=True)

    with app.app_context():
        order = Order.query.filter_by(order_number='ORDWAIT').first()
        assert order.status == OrderStatus.WAITING_MATERIAL
        reservations = Reservation.query.filter_by(order_line_id=order.order_lines[0].id).all()
        assert reservations == []


def test_order_creation_saves_master_bom(client, app, items):
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=25,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    payload = {
        'order_number': 'BOM001',
        'finished_good_sku': finished.sku,
        'quantity': '3',
        'customer_name': 'Delta Corp',
        'created_by': 'Dana',
        'general_notes': '',
        'promised_date': (today + timedelta(days=10)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=2)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=5)).isoformat(),
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 4}
        ]),
        'routing_data': json.dumps([
            {
                'sequence': 1,
                'work_cell': 'Prep',
                'instructions': 'Prep materials',
                'components': [component.sku],
            }
        ]),
        'persist_bom': 'yes',
        'bom_prompt_state': 'missing',
    }

    response = client.post('/orders/new', data=payload, follow_redirects=False)
    assert response.status_code in (302, 303)

    with app.app_context():
        bom = BillOfMaterial.query.filter_by(finished_good_item_id=finished.id).one()
        assert len(bom.components) == 1
        saved_component = bom.components[0]
        assert saved_component.component_item_id == component.id
        assert saved_component.quantity == 4


def test_bom_api_requires_admin(client, app, items):
    finished, component, _ = items
    with app.app_context():
        bom = BillOfMaterial(finished_good_item_id=finished.id)
        bom.components.append(
            BillOfMaterialComponent(component_item_id=component.id, quantity=7)
        )
        db.session.add(bom)
        db.session.commit()

    resp = client.get(f'/inventory/api/bom/{finished.sku}')
    assert resp.status_code == 403

    with client.session_transaction() as session:
        session['is_admin'] = True

    resp = client.get(f'/inventory/api/bom/{finished.sku}')
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['finished_good']['sku'] == finished.sku
    assert payload['components'][0]['quantity'] == 7


def test_component_usage_required(client, app, items):
    finished, component, _ = items
    with client.session_transaction() as session:
        session['is_admin'] = True
    resp = client.post('/orders/new', data={
        'order_number': 'ORD004',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Delta Co',
        'created_by': 'Diana',
        'general_notes': 'Routing requires components',
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
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=25,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    client.post('/orders/new', data={
        'order_number': 'ORD005',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Eta Manufacturing',
        'created_by': 'Evan',
        'general_notes': 'Routing notes',
        'promised_date': (today + timedelta(days=18)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=3)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=8)).isoformat(),
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
        usage = step.component_usages[0]
        required_qty = usage.bom_component.quantity * order.order_lines[0].quantity
        selection_value = f"none::{location.id}"

    resp = client.post(f'/orders/{order_id}/routing', data={
        'completed_steps': str(step_id),
        f'usage_{usage.id}': selection_value,
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        order = Order.query.filter_by(id=order_id).first()
        step = RoutingStep.query.get(step_id)
        assert step.completed is True
        assert step.completed_at is not None
        assert order.routing_progress == 1.0
        consumption = RoutingStepConsumption.query.filter_by(routing_step_component_id=usage.id).one()
        assert consumption.quantity == required_qty
        movement = consumption.movement
        assert movement is not None
        assert movement.quantity == -required_qty
        assert movement.movement_type == 'ISSUE'
        assert movement.location_id == location.id
        assert movement.batch_id is None
        reservation = Reservation.query.filter_by(order_line_id=order.order_lines[0].id).first()
        assert reservation is None

    resp = client.post(f'/orders/{order_id}/routing', data={}, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        step = RoutingStep.query.get(step_id)
        order = Order.query.filter_by(id=order_id).first()
        assert step.completed is False
        assert step.completed_at is None
        assert order.routing_progress == 0.0
        assert RoutingStepConsumption.query.filter_by(routing_step_component_id=usage.id).count() == 0
        reservation = Reservation.query.filter_by(order_line_id=order.order_lines[0].id, item_id=component.id).one()
        assert reservation.quantity == required_qty
        assert Movement.query.filter_by(movement_type='ISSUE').count() == 0


def test_edit_updates_general_notes(client, app, items):
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=10,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    client.post('/orders/new', data={
        'order_number': 'ORDEDIT',
        'finished_good_sku': finished.sku,
        'quantity': '1',
        'customer_name': 'Edit Corp',
        'created_by': 'Eli',
        'general_notes': 'Original notes',
        'promised_date': (today + timedelta(days=12)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=3)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=6)).isoformat(),
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 1}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Assembly', 'instructions': 'Build', 'components': [component.sku]}
        ]),
    }, follow_redirects=True)

    with app.app_context():
        order = Order.query.filter_by(order_number='ORDEDIT').first()
        assert order is not None
        assert order.general_notes == 'Original notes'
        order_id = order.id
        current_status = order.status

    resp = client.post(f'/orders/{order_id}/edit', data={
        'status': current_status,
        'general_notes': 'Updated notes\nSecond line',
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        order = Order.query.get(order_id)
        assert order.general_notes == 'Updated notes\nSecond line'
        assert order.status == current_status


def test_routing_step_component_relationships(app, items):
    finished, component, _ = items
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
    finished, _, _ = items
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
