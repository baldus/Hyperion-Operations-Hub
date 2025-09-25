import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from invapp.models import User


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


def admin_login(client):
    return client.post(
        '/admin/login',
        data={'username': 'admin', 'password': 'password'},
        follow_redirects=True,
    )


def test_manage_users_requires_admin(client):
    resp = client.get('/admin/users', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].startswith('/admin/login')


def test_admin_can_create_and_control_users(client, app):
    admin_login(client)

    resp = client.post(
        '/admin/users',
        data={
            'username': 'tech',
            'password': 'pw',
            'roles': ['admin'],
            'is_active': 'on',
        },
        follow_redirects=True,
    )
    assert b'User account created successfully' in resp.data

    with app.app_context():
        tech = User.query.filter_by(username='tech').first()
        assert tech is not None
        assert tech.is_active
        assert tech.has_role('admin')

    resp = client.post(
        f'/admin/users/{tech.id}/update',
        data={'roles': ['admin']},
        follow_redirects=True,
    )
    assert b'At least one active administrator is required' in resp.data

    resp = client.post(
        '/admin/users',
        data={
            'username': 'support',
            'password': 'pw',
            'roles': ['admin'],
            'is_active': 'on',
        },
        follow_redirects=True,
    )
    assert b'User account created successfully' in resp.data

    with app.app_context():
        support = User.query.filter_by(username='support').first()
        assert support is not None

    resp = client.post(
        f'/admin/users/{tech.id}/update',
        data={'is_active': 'on'},
        follow_redirects=True,
    )
    assert b'User access updated' in resp.data

    with app.app_context():
        tech = User.query.filter_by(username='tech').first()
        assert tech is not None
        assert tech.is_active
        assert not tech.has_role('admin')

    resp = client.post(
        f'/admin/users/{tech.id}/update',
        data={},
        follow_redirects=True,
    )
    assert b'User access updated' in resp.data

    with app.app_context():
        tech = User.query.filter_by(username='tech').first()
        assert tech is not None
        assert not tech.is_active

    resp = client.post(
        f'/admin/users/{support.id}/update',
        data={'roles': ['admin']},
        follow_redirects=True,
    )
    assert b'At least one active administrator is required' in resp.data

    with app.app_context():
        support = User.query.filter_by(username='support').first()
        assert support is not None
        assert support.is_active
        assert support.has_role('admin')

    resp = client.post(
        f'/admin/users/{support.id}/password',
        data={'new_password': 'newpw', 'confirm_password': 'newpw'},
        follow_redirects=True,
    )
    assert b'Password updated for user' in resp.data

    login_resp = client.post(
        '/auth/login',
        data={'username': 'support', 'password': 'newpw'},
        follow_redirects=True,
    )
    assert b'Logged in' in login_resp.data


def test_admin_role_user_can_access_management(client, app):
    admin_login(client)
    client.post(
        '/admin/users',
        data={
            'username': 'boss',
            'password': 'pw',
            'roles': ['admin'],
            'is_active': 'on',
        },
        follow_redirects=True,
    )

    client.get('/admin/logout', follow_redirects=True)

    login_resp = client.post(
        '/auth/login',
        data={'username': 'boss', 'password': 'pw'},
        follow_redirects=True,
    )
    assert b'Logged in' in login_resp.data

    resp = client.get('/admin/users', follow_redirects=False)
    assert resp.status_code == 200
    assert b'Existing Accounts' in resp.data
