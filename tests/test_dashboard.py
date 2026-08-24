import os
import pytest
from datetime import datetime, timezone

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['API_KEY'] = 'test-api-key'
os.environ['DASHBOARD_USERNAME'] = 'admin'
os.environ['DASHBOARD_PASSWORD'] = 'secretpass'
os.environ['SECRET_KEY'] = 'test-secret'

from app import app, db, Notification, CallLog, SmsMessage, Device


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_dashboard_redirects_unauthenticated(client):
    response = client.get('/dashboard')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_login_invalid_credentials(client):
    response = client.post('/login', data={
        'username': 'admin',
        'password': 'wrongpassword'
    })
    assert response.status_code == 200
    assert b'Invalid username or password' in response.data


def test_login_valid_credentials(client):
    response = client.post('/login', data={
        'username': 'admin',
        'password': 'secretpass'
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Telemetry Overview' in response.data


def test_dashboard_pages_render(client):
    # Log in
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    # Populate some test data
    now = datetime.now(timezone.utc)
    with app.app_context():
        device = Device(device_id="dev_001", device_model="Test Phone", android_version="14", app_version="1.0.0", last_sync=now)
        db.session.add(device)
        db.session.add(Notification(device_id="dev_001", app_package="com.test", app_name="TestApp", title="TestTitle", content="TestContent", received_at=now))
        db.session.add(CallLog(device_id="dev_001", phone_number="12345", call_type="incoming", duration_sec=10, occurred_at=now))
        db.session.add(SmsMessage(device_id="dev_001", address="54321", body="Hello SMS", sms_type="received", occurred_at=now))
        db.session.commit()

    # Overview
    r = client.get('/dashboard')
    assert r.status_code == 200
    assert b'Test Phone' in r.data

    # Notifications
    r = client.get('/dashboard/notifications')
    assert r.status_code == 200
    assert b'TestTitle' in r.data

    # Calls
    r = client.get('/dashboard/calls')
    assert r.status_code == 200
    assert b'12345' in r.data

    # SMS
    r = client.get('/dashboard/sms')
    assert r.status_code == 200
    assert b'Hello SMS' in r.data


def test_logout(client):
    # Log in then out
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})
    r = client.get('/logout', follow_redirects=True)
    assert r.status_code == 200
    assert b'Sign In' in r.data
