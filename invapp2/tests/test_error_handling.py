import pytest
from sqlalchemy.exc import ProgrammingError

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


def test_error_handler_rolls_back_session(client, app, monkeypatch):
    from invapp.routes import open_orders as open_orders_routes

    user = User(username="adminuser")
    user.set_password("pw123")
    role = Role.query.filter_by(name="admin").first()
    if role is None:
        role = Role(name="admin", description="Administrator")
        db.session.add(role)
    user.roles.append(role)
    db.session.add(user)
    db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "adminuser", "password": "pw123"},
        follow_redirects=True,
    )

    def _raise_programming_error():
        raise ProgrammingError("SELECT 1", {}, Exception("missing column"))

    monkeypatch.setattr(open_orders_routes, "open_orders_schema_status", lambda: {
        "has_open_order": True,
        "has_order_id": True,
        "has_status": True,
    })
    monkeypatch.setattr(open_orders_routes, "_open_order_base_query", _raise_programming_error)

    response = client.get("/open_orders/")
    assert response.status_code == 500

    with app.app_context():
        Role.query.count()
