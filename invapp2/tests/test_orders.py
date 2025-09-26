import io
import json
import os
import re
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
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


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


@pytest.fixture
def schedule_orders(app):
    first_date = date(2024, 1, 5)
    second_date = date(2024, 1, 10)

    with app.app_context():
        widget = Item(sku='FG-SCH-1', name='Schedule Widget', type='Widget')
        gadget = Item(sku='FG-SCH-2', name='Schedule Gadget', type='')

        order_one = Order(
            order_number='ORD-SCHED-1',
            status=OrderStatus.SCHEDULED,
            scheduled_completion_date=first_date,
        )
        order_one.order_lines.append(
            OrderLine(
                item=widget,
                quantity=12,
                scheduled_completion_date=first_date,
            )
        )
        order_one.routing_steps.append(
            RoutingStep(sequence=1, work_cell='Cell A', description='Assembly')
        )

        order_two = Order(
            order_number='ORD-SCHED-2',
            status=OrderStatus.OPEN,
            scheduled_completion_date=second_date,
        )
        order_two.order_lines.append(
            OrderLine(
                item=gadget,
                quantity=7,
            )
        )
        order_two.routing_steps.append(
            RoutingStep(sequence=1, work_cell=None, description='Inspection')
        )

        db.session.add_all([widget, gadget, order_one, order_two])
        db.session.commit()

    return {
        'dates': [first_date.isoformat(), second_date.isoformat()],
        'item_type': {
            'Uncategorized': [0, 7],
            'Widget': [12, 0],
        },
        'work_cell': {
            'Cell A': [12, 0],
            'Unassigned': [0, 7],
        },
    }


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


def test_schedule_view_groups_totals(client, schedule_orders):
    resp = client.get('/orders/schedule')
    assert resp.status_code == 200

    page_html = resp.data.decode('utf-8')
    match = re.search(r'<script id="schedule-data" type="application/json">(.*?)</script>', page_html, re.DOTALL)
    assert match is not None

    payload = json.loads(match.group(1))

    assert 'item_type' in payload
    assert 'work_cell' in payload

    expected_dates = schedule_orders['dates']

    item_dataset = payload['item_type']
    assert item_dataset['label'] == 'By Item Type'
    assert item_dataset['data']['dates'] == expected_dates
    item_series = {entry['label']: entry['data'] for entry in item_dataset['data']['series']}
    for label, values in schedule_orders['item_type'].items():
        assert item_series.get(label) == values

    work_dataset = payload['work_cell']
    assert work_dataset['label'] == 'By Work Cell'
    assert work_dataset['data']['dates'] == expected_dates
    work_series = {entry['label']: entry['data'] for entry in work_dataset['data']['series']}
    for label, values in schedule_orders['work_cell'].items():
        assert work_series.get(label) == values


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

def test_fetch_bom_template_requires_admin(client, app, items):
    finished, component, _ = items
    with app.app_context():
        template = BillOfMaterial(item_id=finished.id)
        db.session.add(template)
        db.session.add(
            BillOfMaterialComponent(bom=template, component_item_id=component.id, quantity=3)
        )
        db.session.commit()

    resp = client.get(f'/orders/bom-template/{finished.sku}')
    assert resp.status_code == 403



def test_fetch_bom_template_as_admin(client, app, items):
    finished, component, _ = items
    with app.app_context():
        template = BillOfMaterial(item_id=finished.id)
        db.session.add(template)
        db.session.add(
            BillOfMaterialComponent(bom=template, component_item_id=component.id, quantity=4)
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    resp = client.get(f'/orders/bom-template/{finished.sku}')
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['item']['sku'] == finished.sku
    assert payload['components'][0]['quantity'] == 4



def test_new_order_can_save_bom_template(client, app, items):
    finished, component, location = items
    with app.app_context():
        db.session.add(
            Movement(
                item_id=component.id,
                location_id=location.id,
                quantity=20,
                movement_type='RECEIPT',
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    today = date.today()
    payload = {
        'order_number': 'ORDLIB',
        'finished_good_sku': finished.sku,
        'quantity': '2',
        'customer_name': 'Library Inc',
        'created_by': 'Libby',
        'general_notes': '',
        'promised_date': (today + timedelta(days=14)).isoformat(),
        'scheduled_start_date': (today + timedelta(days=3)).isoformat(),
        'scheduled_completion_date': (today + timedelta(days=7)).isoformat(),
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 2}
        ]),
        'routing_data': json.dumps([
            {'sequence': 1, 'work_cell': 'Cell', 'instructions': 'Assemble', 'components': [component.sku]}
        ]),
        'save_bom_template': '1',
    }
    resp = client.post('/orders/new', data=payload, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        template = BillOfMaterial.query.filter_by(item_id=finished.id).one()
        assert template.components[0].component_item_id == component.id
        assert template.components[0].quantity == 2



def test_bom_library_manual_creation(client, app, items):
    finished, component, _ = items

    with client.session_transaction() as session:
        session['is_admin'] = True

    payload = {
        'action': 'create',
        'finished_good_sku': finished.sku,
        'bom_data': json.dumps([
            {'sku': component.sku, 'quantity': 5}
        ]),
    }

    resp = client.post('/orders/bom-library', data=payload, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        template = BillOfMaterial.query.filter_by(item_id=finished.id).one()
        assert template.components[0].component_item_id == component.id
        assert template.components[0].quantity == 5



def test_bom_library_csv_import_updates_template(client, app, items):
    finished, component, _ = items
    with app.app_context():
        template = BillOfMaterial(item_id=finished.id)
        db.session.add(template)
        db.session.add(
            BillOfMaterialComponent(bom=template, component_item_id=component.id, quantity=1)
        )
        db.session.commit()

    with client.session_transaction() as session:
        session['is_admin'] = True

    csv_content = f"component_sku,quantity\n{component.sku},7\n"
    data = {
        'action': 'import_csv',
        'finished_good_sku': finished.sku,
        'csv_file': (io.BytesIO(csv_content.encode('utf-8')), 'template.csv'),
    }

    resp = client.post('/orders/bom-library', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        template = BillOfMaterial.query.filter_by(item_id=finished.id).one()
        assert template.components[0].quantity == 7
