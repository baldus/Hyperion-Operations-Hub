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


def login_superuser(client):
    return client.post(
        "/auth/login",
        data={"username": DEFAULT_SUPERUSER_USERNAME, "password": DEFAULT_SUPERUSER_PASSWORD},
        follow_redirects=True,
    )


def create_role(app, name="manager", description="Manager"):
    with app.app_context():
        role = Role.query.filter_by(name=name).first()
        if role is None:
            role = Role(name=name, description=description)
            db.session.add(role)
            db.session.commit()
        return role.id


def test_superuser_can_create_user(client, app):
    login_superuser(client)
    response = client.post(
        "/users/create",
        data={"username": "jane", "password": "pw123", "roles": []},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"User created" in response.data

    with app.app_context():
        user = User.query.filter_by(username="jane").one()
        assert user.check_password("pw123")
        assert any(role.name == "user" for role in user.roles)


def test_superuser_can_update_roles(client, app):
    login_superuser(client)
    manager_role_id = create_role(app, name="manager", description="Manager role")

    with app.app_context():
        user = User(username="sam")
        user.set_password("pw")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    response = client.post(
        f"/users/{user_id}/edit",
        data={"username": "sam", "roles": [str(manager_role_id)]},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"User updated" in response.data

    with app.app_context():
        user = User.query.get(user_id)
        assert user is not None
        assert {role.name for role in user.roles} == {"manager"}


def test_superuser_can_reset_password_and_delete_user(client, app):
    login_superuser(client)
    response = client.post(
        "/users/create",
        data={"username": "mark", "password": "pw1", "roles": []},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        user = User.query.filter_by(username="mark").one()
        user_id = user.id

    response = client.post(
        f"/users/{user_id}/reset-password",
        data={"password": "pw2"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Password reset" in response.data

    with app.app_context():
        user = User.query.get(user_id)
        assert user is not None
        assert user.check_password("pw2")

    response = client.post(f"/users/{user_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert b"User deleted" in response.data

    with app.app_context():
        assert User.query.get(user_id) is None


def test_non_superuser_cannot_access_user_management(client, app):
    with app.app_context():
        role = Role(name="user")
        user = User(username="regular")
        user.set_password("pw")
        user.roles.append(role)
        db.session.add_all([role, user])
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "regular", "password": "pw"},
        follow_redirects=True,
    )

    response = client.get("/users/", follow_redirects=False)
    assert response.status_code == 403
