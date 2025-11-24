import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Role, UsefulLink, User


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


def create_standard_user(app, username="reader"):
    with app.app_context():
        user = User(username=username)
        user.set_password("pw")
        role = Role.query.filter_by(name="user").first()
        if role is None:
            role = Role(name="user")
            db.session.add(role)
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        return user


def test_superuser_can_add_link(client, app):
    login_superuser(client)
    response = client.post(
        "/links/",
        data={
            "title": "Docs",
            "url": "https://example.com/docs",
            "description": "Product documentation",
            "display_order": "2",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        link = UsefulLink.query.filter_by(title="Docs").one()
        assert link.url == "https://example.com/docs"
        assert link.description == "Product documentation"
        assert link.display_order == 2


def test_non_superuser_cannot_manage_links(client, app):
    create_standard_user(app)
    client.post(
        "/auth/login",
        data={"username": "reader", "password": "pw"},
        follow_redirects=True,
    )

    response = client.get("/links/", follow_redirects=False)
    assert response.status_code == 403


def test_links_render_on_home(client, app):
    with app.app_context():
        db.session.add(
            UsefulLink(
                title="Support",
                url="https://example.com/support",
                description="Contact the support desk",
                display_order=1,
            )
        )
        db.session.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert b"Support" in response.data
    assert b"Contact the support desk" in response.data
