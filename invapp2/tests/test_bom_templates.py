import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app  # noqa: E402
from invapp.extensions import db  # noqa: E402
from invapp.models import (  # noqa: E402
    BillOfMaterial,
    BillOfMaterialComponent,
    Item,
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
def client(app):
    return app.test_client()


@pytest.fixture
def items(app):
    with app.app_context():
        finished = Item(sku="FG-100", name="Widget")
        component_a = Item(sku="CMP-200", name="Component A")
        component_b = Item(sku="CMP-300", name="Component B")
        db.session.add_all([finished, component_a, component_b])
        db.session.commit()
        yield SimpleNamespace(
            finished=SimpleNamespace(id=finished.id, sku=finished.sku, name=finished.name),
            component_a=SimpleNamespace(
                id=component_a.id, sku=component_a.sku, name=component_a.name
            ),
            component_b=SimpleNamespace(
                id=component_b.id, sku=component_b.sku, name=component_b.name
            ),
        )


def test_create_bom_template_via_inventory_route(client, app, items):
    payload = {
        "name": "Widget BOM",
        "finished_good_sku": items.finished.sku,
        "description": "Standard build",
        "components_json": json.dumps(
            [
                {"sku": items.component_a.sku, "quantity": 2},
                {"sku": items.component_b.sku, "quantity": 4},
            ]
        ),
    }
    resp = client.post("/inventory/boms/new", data=payload, follow_redirects=True)
    assert resp.status_code == 200
    assert b"BOM template &#39;Widget BOM&#39; created" in resp.data
    with app.app_context():
        bom = BillOfMaterial.query.filter_by(name="Widget BOM").one()
        assert bom.item_id == items.finished.id
        assert bom.description == "Standard build"
        component_skus = sorted(component.component_item.sku for component in bom.components)
        assert component_skus == sorted([items.component_a.sku, items.component_b.sku])


def test_list_boms_displays_components(client, app, items):
    with app.app_context():
        bom = BillOfMaterial(name="Widget BOM", item_id=items.finished.id)
        bom.components.append(
            BillOfMaterialComponent(
                component_item_id=items.component_a.id,
                quantity=3,
            )
        )
        db.session.add(bom)
        db.session.commit()
    resp = client.get("/inventory/boms")
    assert resp.status_code == 200
    assert b"Widget BOM" in resp.data
    assert items.component_a.sku.encode() in resp.data


def test_edit_bom_template_updates_components(client, app, items):
    with app.app_context():
        bom = BillOfMaterial(name="Widget BOM", item_id=items.finished.id)
        bom.components.append(
            BillOfMaterialComponent(
                component_item_id=items.component_a.id,
                quantity=1,
            )
        )
        db.session.add(bom)
        db.session.commit()
        bom_id = bom.id
    payload = {
        "name": "Widget BOM",
        "finished_good_sku": items.finished.sku,
        "description": "Revised build",
        "components_json": json.dumps(
            [
                {"sku": items.component_b.sku, "quantity": 5},
            ]
        ),
    }
    resp = client.post(
        f"/inventory/boms/{bom_id}/edit", data=payload, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"updated for" in resp.data
    with app.app_context():
        refreshed = BillOfMaterial.query.get(bom_id)
        assert refreshed.description == "Revised build"
        assert len(refreshed.components) == 1
        component = refreshed.components[0]
        assert component.component_item.sku == items.component_b.sku
        assert component.quantity == 5


def test_save_bom_from_order_form_creates_template(client, app, items):
    with client.session_transaction() as session:
        session["is_admin"] = True
    payload = {
        "action": "save_bom",
        "finished_good_sku": items.finished.sku,
        "bom_template_name": "Order Capture",
        "bom_data": json.dumps(
            [
                {"sku": items.component_a.sku, "quantity": 2},
            ]
        ),
        "routing_data": "[]",
        "order_number": "",
        "quantity": "",
        "customer_name": "",
        "created_by": "",
        "promised_date": "",
        "scheduled_start_date": "",
        "scheduled_completion_date": "",
        "general_notes": "Captured via order form",
    }
    resp = client.post("/orders/new", data=payload, follow_redirects=True)
    assert resp.status_code == 200
    assert b"BOM template &#39;Order Capture&#39;" in resp.data
    with app.app_context():
        bom = BillOfMaterial.query.filter_by(name="Order Capture").one()
        assert bom.item_id == items.finished.id
        assert bom.description == "Captured via order form"
        assert len(bom.components) == 1
        component = bom.components[0]
        assert component.component_item.sku == items.component_a.sku
        assert component.quantity == 2
