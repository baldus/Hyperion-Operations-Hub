import io
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import joinedload

from invapp import create_app
from invapp.extensions import db
from invapp.models import (
    Item,
    Location,
    Movement,
    ProductionChartSettings,
    ProductionCustomer,
    ProductionDailyCustomerTotal,
    ProductionDailyGateCompletion,
    ProductionDailyRecord,
    ProductionOutputFormula,
)


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _login_admin(client):
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )


def test_backup_requires_admin(client):
    response = client.get("/admin/data-backup")
    assert response.status_code == 302
    assert "/auth/login" in response.location

    response = client.post("/admin/data-backup/export")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_export_and_import_round_trip(client, app):
    with app.app_context():
        location = Location(code="MAIN", description="Main Warehouse")
        item = Item(sku="SKU-1", name="Sample Item")
        db.session.add_all([location, item])
        db.session.commit()

        movement = Movement(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            movement_type="ADJUST",
            person="Tester",
        )
        db.session.add(movement)
        db.session.commit()

    _login_admin(client)

    export_response = client.post("/admin/data-backup/export")
    assert export_response.status_code == 200
    assert export_response.mimetype == "application/json"

    exported = json.loads(export_response.data)
    assert "item" in exported
    assert any(row["sku"] == "SKU-1" for row in exported["item"])

    with app.app_context():
        Movement.query.delete()
        Item.query.delete()
        Location.query.delete()
        db.session.commit()

    upload = io.BytesIO(export_response.data)
    upload.name = "backup.json"
    import_response = client.post(
        "/admin/data-backup/import",
        data={"backup_file": (upload, "backup.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert import_response.status_code == 200
    page = import_response.get_data(as_text=True)
    assert "Backup imported successfully" in page

    with app.app_context():
        restored_items = Item.query.all()
        restored_locations = Location.query.all()
        restored_movements = Movement.query.all()

        assert len(restored_items) == 1
        assert restored_items[0].sku == "SKU-1"
        assert len(restored_locations) == 1
        assert restored_locations[0].code == "MAIN"
        assert len(restored_movements) == 1
        assert restored_movements[0].quantity == 5


def test_export_includes_production_history(client, app):
    with app.app_context():
        customer = ProductionCustomer(
            name="Export Customer",
            color="#123456",
            is_active=True,
            is_other_bucket=False,
            lump_into_other=False,
        )
        record = ProductionDailyRecord(
            entry_date=date(2024, 4, 1),
            day_of_week="Monday",
            gates_employees=6,
            gates_hours_ot=Decimal("1.25"),
            controllers_4_stop=2,
            controllers_6_stop=3,
            door_locks_lh=4,
            door_locks_rh=5,
            operators_produced=7,
            cops_produced=8,
            additional_employees=2,
            additional_hours_ot=Decimal("0.75"),
            daily_notes="Backed up",
        )
        customer_total = ProductionDailyCustomerTotal(
            record=record,
            customer=customer,
            gates_produced=11,
            gates_packaged=9,
        )
        gate_completion = ProductionDailyGateCompletion(
            record=record,
            order_number="ORD-42",
            customer_name="Export Customer",
            gates_completed=3,
            po_number="PO-99",
            created_at=datetime(2024, 4, 1, 12, 30, 0),
            updated_at=datetime(2024, 4, 1, 13, 0, 0),
        )
        chart_settings = ProductionChartSettings(
            primary_min=Decimal("10"),
            primary_max=Decimal("200"),
            goal_value=Decimal("150"),
            show_goal=True,
        )
        output_formula = ProductionOutputFormula(
            formula="combined_output / total_hours",
            variables=[{"name": "combined_output"}, {"name": "total_hours"}],
        )

        db.session.add_all(
            [
                customer,
                record,
                customer_total,
                gate_completion,
                chart_settings,
                output_formula,
            ]
        )
        db.session.commit()

    _login_admin(client)

    export_response = client.post("/admin/data-backup/export")
    assert export_response.status_code == 200

    exported = json.loads(export_response.data)
    assert exported["production_customer"]
    assert exported["production_daily_record"]
    assert exported["production_daily_customer_total"]
    assert exported["production_daily_gate_completion"]
    assert exported["production_chart_settings"]
    assert exported["production_output_formula"]

    with app.app_context():
        ProductionDailyGateCompletion.query.delete()
        ProductionDailyCustomerTotal.query.delete()
        ProductionDailyRecord.query.delete()
        ProductionCustomer.query.delete()
        ProductionChartSettings.query.delete()
        ProductionOutputFormula.query.delete()
        db.session.commit()

    upload = io.BytesIO(export_response.data)
    upload.name = "backup.json"
    import_response = client.post(
        "/admin/data-backup/import",
        data={"backup_file": (upload, "backup.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert import_response.status_code == 200

    with app.app_context():
        restored_customers = ProductionCustomer.query.all()
        restored_records = ProductionDailyRecord.query.options(
            joinedload(ProductionDailyRecord.customer_totals),
            joinedload(ProductionDailyRecord.gate_completions),
        ).all()
        restored_chart_settings = ProductionChartSettings.query.all()
        restored_output_formulas = ProductionOutputFormula.query.all()

        assert any(customer.name == "Export Customer" for customer in restored_customers)
        matching_records = [
            record for record in restored_records if record.entry_date == date(2024, 4, 1)
        ]
        assert matching_records
        restored_record = matching_records[0]
        assert restored_record.daily_notes == "Backed up"
        assert restored_record.customer_totals[0].gates_produced == 11
        assert restored_record.gate_completions[0].order_number == "ORD-42"
        assert any(setting.show_goal for setting in restored_chart_settings)
        assert any(
            formula.formula == "combined_output / total_hours"
            for formula in restored_output_formulas
        )
