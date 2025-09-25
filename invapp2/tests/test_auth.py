import time

import pytest

from invapp import create_app
from invapp.extensions import db
from invapp.models import Role, User


DEFAULT_SUPERUSER_USERNAME = "superuser"
DEFAULT_SUPERUSER_PASSWORD = "joshbaldus"


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


def register(client, username="alice", password="password"):
    return client.post(
        "/auth/register",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def login(client, username="alice", password="password"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_superuser_seeded(app):
    with app.app_context():
        user = User.query.filter_by(username=DEFAULT_SUPERUSER_USERNAME).first()

        assert user is not None
        assert user.check_password(DEFAULT_SUPERUSER_PASSWORD)
        assert any(role.name == "admin" for role in user.roles)


def test_superuser_standard_login(client):
    response = client.post(
        "/auth/login",
        data={
            "username": DEFAULT_SUPERUSER_USERNAME,
            "password": DEFAULT_SUPERUSER_PASSWORD,
        },
        follow_redirects=True,
    )

    assert b"Invalid credentials" not in response.data

    protected_response = client.get("/settings/printers")
    assert protected_response.status_code == 200


def test_registration_and_login(client):
    register(client)
    resp = login(client)
    assert b"Invalid credentials" not in resp.data
    resp = client.get("/orders/", follow_redirects=True)
    assert resp.status_code == 200


def test_login_required_redirect(client):
    resp = client.get("/settings/printers", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_role_restriction(client, app):
    register(client, "bob", "pw")
    login(client, "bob", "pw")
    # No admin role yet -> forbidden
    resp = client.get("/settings/printers")
    assert resp.status_code == 403
    # grant admin role
    with app.app_context():
        user = User.query.filter_by(username="bob").first()
        admin = Role.query.filter_by(name="admin").first()
        if not admin:
            admin = Role(name="admin")
            db.session.add(admin)
        if admin not in user.roles:
            user.roles.append(admin)
        db.session.commit()
    resp = client.get("/settings/printers")
    assert resp.status_code == 200


def test_password_reset(client):
    register(client, "carol", "pw1")
    login(client, "carol", "pw1")
    resp = client.post(
        "/auth/reset-password",
        data={"old_password": "pw1", "new_password": "pw2"},
        follow_redirects=True,
    )
    assert b"Password updated" in resp.data
    client.get("/auth/logout")
    resp = login(client, "carol", "pw2")
    assert b"Invalid credentials" not in resp.data


def test_admin_login_button_route(client):
    resp = client.get("/admin/login")
    assert resp.status_code == 200

    resp = client.post(
        "/admin/login",
        data={
            "username": DEFAULT_SUPERUSER_USERNAME,
            "password": DEFAULT_SUPERUSER_PASSWORD,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")

    with client.session_transaction() as session:
        assert session.get("is_admin") is True


def test_admin_session_timeout(client):
    client.post(
        "/admin/login",
        data={
            "username": DEFAULT_SUPERUSER_USERNAME,
            "password": DEFAULT_SUPERUSER_PASSWORD,
        },
        follow_redirects=False,
    )

    with client.session_transaction() as session:
        session["admin_last_active"] = time.time() - 301

    response = client.get("/settings/printers", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login")

    with client.session_transaction() as session:
        assert not session.get("is_admin")
