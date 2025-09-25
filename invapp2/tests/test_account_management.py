import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Role, User


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


@pytest.fixture
def admin_user(app):
    with app.app_context():
        user_role = Role(name="user")
        admin_role = Role(name="admin")
        admin = User(username="admin")
        admin.set_password("secret")
        admin.roles = [user_role, admin_role]
        db.session.add_all([user_role, admin_role, admin])
        db.session.commit()
        return admin.id


@pytest.fixture
def logged_in_admin(client, admin_user):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=True,
    )
    return client


def test_manage_accounts_requires_admin(app, client):
    response = client.get("/settings/accounts", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        user_role = Role(name="user")
        regular = User(username="regular")
        regular.set_password("pw")
        regular.roles = [user_role]
        db.session.add_all([user_role, regular])
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "regular", "password": "pw"},
        follow_redirects=True,
    )
    forbidden = client.get("/settings/accounts")
    assert forbidden.status_code == 403


def test_admin_can_manage_users(app, logged_in_admin, admin_user):
    client = logged_in_admin

    create_response = client.post(
        "/settings/accounts",
        data={"username": "casey", "password": "pw123"},
        follow_redirects=True,
    )
    assert b"Created account for casey." in create_response.data

    with app.app_context():
        user_role = Role.query.filter_by(name="user").first()
        admin_role = Role.query.filter_by(name="admin").first()
        new_user = User.query.filter_by(username="casey").one()
        assert new_user.has_role("user")
        user_id = new_user.id
        admin_role_id = str(admin_role.id)
        user_role_id = str(user_role.id)

    prevent_last_admin = client.post(
        f"/settings/accounts/{admin_user}/update-roles",
        data={"roles": [user_role_id]},
        follow_redirects=True,
    )
    assert b"Cannot remove the last administrator" in prevent_last_admin.data

    update_response = client.post(
        f"/settings/accounts/{user_id}/update-roles",
        data={"roles": [user_role_id, admin_role_id]},
        follow_redirects=True,
    )
    assert b"Updated roles" in update_response.data

    with app.app_context():
        updated_user = User.query.get(user_id)
        assert updated_user.has_role("admin")
        assert updated_user.has_role("user")

    delete_response = client.post(
        f"/settings/accounts/{user_id}/delete",
        follow_redirects=True,
    )
    assert b"Deleted account for casey." in delete_response.data

    with app.app_context():
        assert User.query.filter_by(username="casey").first() is None

    cannot_delete_self = client.post(
        f"/settings/accounts/{admin_user}/delete",
        follow_redirects=True,
    )
    assert b"You cannot delete your own account." in cannot_delete_self.data
