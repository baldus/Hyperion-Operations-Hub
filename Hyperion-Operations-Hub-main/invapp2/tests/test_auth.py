import os
import sys
import time
import pytest

# ensure package path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from invapp.models import User, Role


@pytest.fixture
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def register(client, username="alice", password="password"):
    return client.post('/auth/register', data={'username': username, 'password': password}, follow_redirects=True)


def login(client, username="alice", password="password"):
    return client.post('/auth/login', data={'username': username, 'password': password}, follow_redirects=True)


def test_registration_and_login(client):
    register(client)
    resp = login(client)
    assert b'Invalid credentials' not in resp.data
    resp = client.get('/orders/', follow_redirects=True)
    assert resp.status_code == 200


def test_login_required_redirect(client):
    resp = client.get('/orders/', follow_redirects=False)
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


def test_role_restriction(client, app):
    register(client, 'bob', 'pw')
    login(client, 'bob', 'pw')
    # No admin role yet -> forbidden
    resp = client.get('/settings/printers')
    assert resp.status_code == 403
    # grant admin role
    with app.app_context():
        user = User.query.filter_by(username='bob').first()
        admin = Role(name='admin')
        db.session.add(admin)
        user.roles.append(admin)
        db.session.commit()
    resp = client.get('/settings/printers')
    assert resp.status_code == 200


def test_password_reset(client):
    register(client, 'carol', 'pw1')
    login(client, 'carol', 'pw1')
    resp = client.post('/auth/reset-password', data={'old_password':'pw1','new_password':'pw2'}, follow_redirects=True)
    assert b'Password updated' in resp.data
    client.get('/auth/logout')
    resp = login(client, 'carol', 'pw2')
    assert b'Invalid credentials' not in resp.data


def test_admin_login_button_route(client):
    resp = client.get('/admin/login')
    assert resp.status_code == 200

    resp = client.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'password'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/')

    with client.session_transaction() as session:
        assert session.get('is_admin') is True


def test_admin_session_timeout(client):
    client.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'password'},
        follow_redirects=False,
    )

    with client.session_transaction() as session:
        session['admin_last_active'] = time.time() - 301

    response = client.get('/settings/printers', follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].startswith('/admin/login')

    with client.session_transaction() as session:
        assert not session.get('is_admin')
