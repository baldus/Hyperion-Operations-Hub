
import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

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


def create_user(app, username="alice", password="password", role_names=("user",)):
    with app.app_context():
        user = User(username=username)
        user.set_password(password)

        assigned_roles = []
        if role_names:
            for role_name in role_names:
                role = Role.query.filter_by(name=role_name).first()
                if role is None:
                    role = Role(name=role_name)
                    db.session.add(role)
                assigned_roles.append(role)

        user.roles = assigned_roles
        db.session.add(user)
        db.session.commit()
        return user


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


def test_login_with_created_user(client, app):
    create_user(app, role_names=("orders",))
    resp = login(client)
    assert b"Invalid credentials" not in resp.data
    resp = client.get("/orders/", follow_redirects=True)
    assert resp.status_code == 200


def test_login_required_redirect(client):
    resp = client.get("/settings/printers", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_role_restriction(client, app):
    create_user(app, "bob", "pw", role_names=("user",))
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


def test_password_reset(client, app):
    create_user(app, "carol", "pw1", role_names=("user",))
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


def test_admin_login_redirects_to_auth(client):
    resp = client.get("/admin/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/auth/login")

    login(client, DEFAULT_SUPERUSER_USERNAME, DEFAULT_SUPERUSER_PASSWORD)
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    assert b"Administrator Tools" in resp.data


def test_admin_routes_require_admin_role(client, app):
    create_user(app, "mallory", "pw", role_names=("user",))
    login(client, "mallory", "pw")

    resp = client.get("/admin/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")

    resp = client.get("/admin/data-backup")
    assert resp.status_code == 403


def test_register_route_restricted(client, app):
    create_user(app, "eve", "secret", role_names=("user",))
    login(client, "eve", "secret")

    response = client.get("/auth/register")
    assert response.status_code == 404

    client.get("/auth/logout")
    login(client, DEFAULT_SUPERUSER_USERNAME, DEFAULT_SUPERUSER_PASSWORD)
    response = client.get("/auth/register", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/users/create")
