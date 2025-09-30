import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import pytest

from invapp import create_app
from invapp.extensions import db
from invapp.models import LabelProcessAssignment, LabelTemplate, Printer, Role, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()

        admin_role = Role.query.filter_by(name="admin").first()
        if admin_role is None:
            admin_role = Role(name="admin", description="Administrator")
            db.session.add(admin_role)

        user = User.query.filter_by(username="designer").first()
        if user is None:
            user = User(username="designer")
            user.set_password("password123")
            db.session.add(user)

        if admin_role not in user.roles:
            user.roles.append(admin_role)

        if not Printer.query.filter_by(name="Test Printer").first():
            printer = Printer(name="Test Printer", host="127.0.0.1", port=9100)
            db.session.add(printer)

        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login_admin(client):
    return client.post(
        "/auth/login",
        data={"username": "designer", "password": "password123"},
        follow_redirects=True,
    )


def build_layout_payload():
    return {
        "label_id": "batch-label",
        "layout": {
            "id": "batch-label",
            "name": "Batch Label",
            "size": {"width": 812, "height": 1218},
            "fields": [
                {
                    "id": "field-1",
                    "label": "Lot Number",
                    "bindingKey": "lot_number",
                    "type": "text",
                    "x": 60,
                    "y": 80,
                    "width": 640,
                    "height": 64,
                    "rotation": 0,
                    "fontSize": 48,
                    "align": "left",
                },
                {
                    "id": "field-2",
                    "label": "Lot Barcode",
                    "bindingKey": "lot_number",
                    "type": "barcode",
                    "x": 60,
                    "y": 200,
                    "width": 640,
                    "height": 220,
                    "rotation": 0,
                    "fontSize": 18,
                    "align": "center",
                    "showValue": True,
                },
            ],
        },
    }


def test_save_layout_requires_login(client):
    response = client.post(
        "/settings/printers/designer/save",
        json=build_layout_payload(),
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_save_layout_validates_payload(client):
    login_admin(client)
    response = client.post("/settings/printers/designer/save", json={})
    assert response.status_code == 400
    data = response.get_json()
    assert data["message"] == "Layout payload is required to save a label."


def test_save_layout_success_persists_to_database(client, app):
    login_admin(client)
    payload = build_layout_payload()
    response = client.post("/settings/printers/designer/save", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["label_id"] == payload["label_id"]

    with app.app_context():
        template = LabelTemplate.query.filter_by(name="LotBatchLabelTemplate").first()
        assert template is not None
        assert template.layout["elements"][0]["type"] == "field"
        assert template.fields["lot_number"] == "{{Batch.LotNumber}}"

        assignment = LabelProcessAssignment.query.filter_by(process="BatchCreated").first()
        assert assignment is not None
        assert assignment.template_id == template.id


def test_trial_print_requires_selected_printer(client):
    login_admin(client)
    payload = build_layout_payload()
    response = client.post("/settings/printers/designer/print-trial", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["message"].startswith("Select an active printer")


def test_trial_print_succeeds_with_selected_printer(client, app, monkeypatch):
    login_admin(client)

    # Persist a template to drive the trial print
    save_payload = build_layout_payload()
    client.post("/settings/printers/designer/save", json=save_payload)

    printed = {}

    def fake_print_label(process, context):
        printed["process"] = process
        printed["context"] = context
        return True

    monkeypatch.setattr("invapp.routes.printers.print_label_for_process", fake_print_label)

    with app.app_context():
        printer = Printer.query.filter_by(name="Test Printer").first()

    with client.session_transaction() as sess:
        sess["selected_printer_id"] = printer.id

    response = client.post("/settings/printers/designer/print-trial", json=save_payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["printer"] == printer.name
    assert printed["process"] == "BatchCreated"
    assert printed["context"]["Batch"]["LotNumber"]
