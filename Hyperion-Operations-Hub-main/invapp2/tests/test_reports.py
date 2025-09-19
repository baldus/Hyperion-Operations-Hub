import os
import sys
import io
import zipfile
import datetime
import pytest

# ensure package path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item, Location, Batch, Movement

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


def login(client):
    client.post('/auth/register', data={'username': 'u', 'password': 'p'})
    client.post('/auth/login', data={'username': 'u', 'password': 'p'})


def seed_data(app):
    with app.app_context():
        item = Item(sku='SKU1', name='Item1')
        loc = Location(code='LOC1', description='Loc1')
        batch = Batch(item=item, lot_number='LOT1', quantity=10,
                      received_date=datetime.datetime.utcnow() - datetime.timedelta(days=5))
        mv1 = Movement(item=item, batch=batch, location=loc, quantity=5,
                       movement_type='RECEIPT', date=datetime.datetime(2021, 1, 1))
        mv2 = Movement(item=item, batch=batch, location=loc, quantity=3,
                       movement_type='ISSUE', date=datetime.datetime(2021, 1, 2))
        db.session.add_all([item, loc, batch, mv1, mv2])
        db.session.commit()


def test_summary_data(client, app):
    login(client)
    seed_data(app)
    resp = client.get('/reports/summary_data?sku=SKU1&location=LOC1&start=2020-12-31&end=2021-01-03')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data['movement_trends']) == 2
    assert data['movement_trends'][0]['quantity'] == 5
    aging = data['stock_aging'][0]
    assert aging['sku'] == 'SKU1'
    with app.app_context():
        batch = Batch.query.first()
        expected_days = (datetime.datetime.utcnow().date() - batch.received_date.date()).days
    assert aging['days'] == expected_days


def test_export(client, app):
    login(client)
    seed_data(app)
    resp = client.get('/reports/export?sku=SKU1')
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    assert 'stock_aging.csv' in names
    assert 'movement_trends.csv' in names
    aging_csv = zf.read('stock_aging.csv').decode()
    assert 'SKU1' in aging_csv
