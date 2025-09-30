import pytest

from invapp import create_app
from invapp.extensions import db
from invapp.models import Role, User
from invapp.permissions import update_page_roles

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


def _ensure_role(name: str, description: str = "") -> Role:
    role = Role.query.filter_by(name=name).first()
    if role is None:
        role = Role(name=name, description=description or name.title())
        db.session.add(role)
        db.session.commit()
    return role


def _create_user(username: str, password: str, role_names: list[str]) -> User:
    user = User(username=username)
    user.set_password(password)
    for role_name in role_names:
        role = _ensure_role(role_name)
        user.roles.append(role)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, username: str, password: str):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_inventory_access_requires_role(client, app):
    with app.app_context():
        _ensure_role("inventory")
        _create_user("viewer", "pw123", role_names=["orders"])

    _login(client, "viewer", "pw123")
    response = client.get("/inventory/", follow_redirects=False)
    assert response.status_code == 403


def test_page_permission_override_allows_new_role(client, app):
    with app.app_context():
        _ensure_role("inventory")
        orders_role = _ensure_role("orders")
        _create_user("orders_user", "pw123", role_names=["orders"])
        orders_role_id = orders_role.id

    login_response = _login(client, "orders_user", "pw123")
    assert login_response.status_code == 200

    home = client.get("/")
    assert b'class="nav-btn">Inventory<' not in home.data
    denied = client.get("/inventory/", follow_redirects=False)
    assert denied.status_code == 403

    with app.app_context():
        update_page_roles("inventory", [orders_role_id], label="Inventory Dashboard")
        db.session.commit()

    # Reload session to pick up new permissions
    home_after = client.get("/")
    assert b'class="nav-btn">Inventory<' in home_after.data
    allowed = client.get("/inventory/", follow_redirects=False)
    assert allowed.status_code == 200
