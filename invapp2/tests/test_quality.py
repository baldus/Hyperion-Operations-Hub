import os
import sys
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import _repair_rma_status_event_sequence, create_app
from invapp.extensions import db
from invapp.models import RMARequest, RMAStatusEvent, Role, User


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
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


def test_quality_role_seeded(app):
    with app.app_context():
        assert Role.query.filter_by(name="quality").first() is not None


def test_quality_access_requires_role(app):
    client = app.test_client()
    with app.app_context():
        viewer_role = Role.query.filter_by(name="viewer").first()
        if viewer_role is None:
            viewer_role = Role(name="viewer", description="Viewer")
            db.session.add(viewer_role)
            db.session.flush()
        viewer = User(username="viewer")
        viewer.set_password("testpass")
        viewer.roles = [viewer_role]
        db.session.add(viewer)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "viewer", "password": "testpass"},
        follow_redirects=True,
    )

    response = client.get("/quality/", follow_redirects=False)
    assert response.status_code == 403


def test_create_rma_request_flow(app, client):
    response = client.post(
        "/quality/requests/new",
        data={
            "customer_name": "Apex Manufacturing",
            "customer_contact": "qa@apex.example",
            "customer_reference": "RM-1002",
            "product_sku": "GATE-42",
            "product_description": "42-bar gate kit",
            "product_serial": "SN-12345",
            "issue_category": "Finish",
            "issue_description": "Powder coat is flaking near the hinges.",
            "requested_action": "Repaint and return",
            "priority": RMARequest.PRIORITY_HIGH,
            "target_resolution_date": "2024-07-15",
            "last_customer_contact": "2024-07-01",
            "follow_up_tasks": "Collect photos from customer",
            "internal_notes": "Customer is on expedited schedule.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"RMA request logged" in response.data
    assert b"Apex Manufacturing" in response.data

    with app.app_context():
        stored = RMARequest.query.one()
        assert stored.customer_name == "Apex Manufacturing"
        assert stored.customer_contact == "qa@apex.example"
        assert stored.product_sku == "GATE-42"
        assert stored.priority == RMARequest.PRIORITY_HIGH
        assert stored.target_resolution_date == date(2024, 7, 15)
        assert stored.last_customer_contact == date(2024, 7, 1)
        assert stored.opened_by == "superuser"
        assert stored.status == RMARequest.STATUS_OPEN
        assert stored.status_events
        first_event = stored.status_events[-1]
        assert first_event.to_status == RMARequest.STATUS_OPEN
        assert "Request created" in (first_event.note or "")


def test_update_rma_request_logs_event(app, client):
    with app.app_context():
        rma = RMARequest(
            customer_name="Delta Fabrication",
            customer_contact="ops@delta.example",
            issue_description="Latch is not locking",
            opened_by="superuser",
        )
        db.session.add(rma)
        db.session.commit()
        request_id = rma.id

    response = client.post(
        f"/quality/requests/{request_id}/update",
        data={
            "status": RMARequest.STATUS_IN_REVIEW,
            "priority": RMARequest.PRIORITY_CRITICAL,
            "customer_name": "Delta Fabrication",
            "customer_contact": "ops@delta.example",
            "customer_reference": "DEL-77",
            "issue_category": "Hardware",
            "product_sku": "CTRL-880",
            "product_description": "Control box",
            "product_serial": "SER-0099",
            "issue_description": "Latch is not locking",
            "requested_action": "Replace assembly",
            "resolution": "Replacement approved",
            "target_resolution_date": "2024-08-05",
            "last_customer_contact": "2024-07-20",
            "follow_up_tasks": "Arrange UPS pickup",
            "internal_notes": "Need engineering sign-off",
            "return_tracking_number": "1ZDELTA",
            "replacement_order_number": "SO-5566",
            "note": "Escalated to engineering",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"RMA request updated" in response.data

    with app.app_context():
        updated = db.session.get(RMARequest, request_id)
        assert updated.status == RMARequest.STATUS_IN_REVIEW
        assert updated.priority == RMARequest.PRIORITY_CRITICAL
        assert updated.target_resolution_date == date(2024, 8, 5)
        assert updated.last_customer_contact == date(2024, 7, 20)
        assert updated.return_tracking_number == "1ZDELTA"
        assert updated.replacement_order_number == "SO-5566"
        events = (
            RMAStatusEvent.query.filter_by(request_id=request_id)
            .order_by(RMAStatusEvent.changed_at.desc())
            .all()
        )
        assert events
        latest = events[0]
        assert latest.to_status == RMARequest.STATUS_IN_REVIEW
        assert "Escalated to engineering" in (latest.note or "")
        assert latest.changed_by == "superuser"


def test_rma_status_event_sequence_repair(app):
    with app.app_context():
        request = RMARequest(
            customer_name="Gamma Works",
            issue_description="Paint blemish",
            opened_by="superuser",
        )
        db.session.add(request)
        db.session.flush()

        events = [
            RMAStatusEvent(
                request_id=request.id,
                from_status=None,
                to_status=RMARequest.STATUS_OPEN,
                changed_by="superuser",
                note="Seed event",
            )
            for _ in range(3)
        ]
        db.session.add_all(events)
        db.session.commit()

        max_identifier = max(event.id for event in events)

        if db.engine.dialect.name == "sqlite":
            # Push the AUTOINCREMENT counter behind the stored rows to mimic
            # sequence drift caused by manual inserts.
            db.session.execute(
                text(
                    "INSERT OR REPLACE INTO sqlite_sequence (name, seq) "
                    "VALUES ('rma_status_event', 1)"
                )
            )
            db.session.commit()

        _repair_rma_status_event_sequence(db.engine)

        repaired_event = RMAStatusEvent(
            request_id=request.id,
            from_status=RMARequest.STATUS_OPEN,
            to_status=RMARequest.STATUS_IN_REVIEW,
            changed_by="auditor",
        )
        db.session.add(repaired_event)
        db.session.commit()

        assert repaired_event.id > max_identifier


def test_new_request_handles_integrity_error(app, client, monkeypatch):
    def fail_commit():
        raise IntegrityError("stmt", {}, Exception("fail"))

    monkeypatch.setattr(db.session, "commit", fail_commit)

    response = client.post(
        "/quality/requests/new",
        data={
            "customer_name": "Omega",
            "issue_description": "Bad latch",
        },
    )

    assert response.status_code == 400
    assert b"Unable to log the RMA request" in response.data

    with app.app_context():
        assert RMARequest.query.count() == 0
        assert db.session.is_active
