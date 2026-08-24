import os
import pytest
from datetime import datetime, timezone

# Use in-memory SQLite for testing
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['API_KEY'] = 'test-api-key'
os.environ['DASHBOARD_USERNAME'] = 'testuser'
os.environ['DASHBOARD_PASSWORD'] = 'testpass'
os.environ['SECRET_KEY'] = 'test-secret'

from app import app, db, Device, Notification, CallLog, SmsMessage


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_sync_unauthorized(client):
    response = client.post('/api/v1/sync', json={})
    assert response.status_code == 401
    assert response.get_json()['error'] == 'unauthorized'


def test_sync_invalid_payload(client):
    response = client.post(
        '/api/v1/sync',
        headers={'X-API-Key': 'test-api-key'},
        data='invalid json'
    )
    assert response.status_code == 400


def test_sync_missing_device_id(client):
    response = client.post(
        '/api/v1/sync',
        headers={'X-API-Key': 'test-api-key'},
        json={'notifications': []}
    )
    assert response.status_code == 400


def test_sync_successful_batch(client):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    payload = {
        "device_id": "test_np2a_001",
        "device_model": "Nothing Phone 2a",
        "android_version": "14",
        "app_version": "1.0.0",
        "notifications": [
            {
                "local_id": 101,
                "app_package": "com.whatsapp",
                "app_name": "WhatsApp",
                "title": "Alice",
                "content": "Meeting at 3pm",
                "category": "msg",
                "received_at": now_ms
            }
        ],
        "call_logs": [
            {
                "local_id": 201,
                "phone_number": "+1234567890",
                "contact_name": "Bob",
                "call_type": "incoming",
                "duration_sec": 45,
                "occurred_at": now_ms
            }
        ],
        "sms_messages": [
            {
                "local_id": 301,
                "address": "+1987654321",
                "contact_name": "Charlie",
                "body": "Your verification code is 492019",
                "sms_type": "received",
                "occurred_at": now_ms
            }
        ]
    }

    response = client.post(
        '/api/v1/sync',
        headers={'X-API-Key': 'test-api-key'},
        json=payload
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    assert data['received']['notifications'] == [101]
    assert data['received']['call_logs'] == [201]
    assert data['received']['sms_messages'] == [301]

    # Verify database records
    with app.app_context():
        device = db.session.get(Device, "test_np2a_001")
        assert device is not None
        assert device.device_model == "Nothing Phone 2a"

        notifs = Notification.query.filter_by(device_id="test_np2a_001").all()
        assert len(notifs) == 1
        assert notifs[0].title == "Alice"
        assert notifs[0].content == "Meeting at 3pm"

        calls = CallLog.query.filter_by(device_id="test_np2a_001").all()
        assert len(calls) == 1
        assert calls[0].phone_number == "+1234567890"
        assert calls[0].duration_sec == 45

        sms = SmsMessage.query.filter_by(device_id="test_np2a_001").all()
        assert len(sms) == 1
        assert sms[0].body == "Your verification code is 492019"


def test_export_endpoint(client):
    # Populate a record
    test_sync_successful_batch(client)

    response = client.get(
        '/api/v1/export',
        headers={'X-API-Key': 'test-api-key'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    assert len(data['notifications']) == 1
    assert len(data['call_logs']) == 1
    assert len(data['sms_messages']) == 1

