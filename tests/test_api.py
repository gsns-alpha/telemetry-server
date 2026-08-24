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


def test_sync_encoded_payload(client):
    import base64
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def b64(s):
        return base64.b64encode(s.encode('utf-8')).decode('ascii')

    payload = {
        "device_id": "test_np2a_encoded",
        "device_model": "Nothing Phone 2a",
        "android_version": "14",
        "app_version": "1.0.0",
        "notifications": [
            {
                "local_id": 901,
                "app_package": "com.whatsapp",
                "app_name": b64("WhatsApp"),
                "title": b64("Private Contact"),
                "content": b64("Secret confidential message 123"),
                "category": "msg",
                "received_at": now_ms
            }
        ],
        "call_logs": [
            {
                "local_id": 902,
                "phone_number": b64("+9988776655"),
                "contact_name": b64("Dr. John"),
                "call_type": "incoming",
                "duration_sec": 120,
                "occurred_at": now_ms
            }
        ],
        "sms_messages": [
            {
                "local_id": 903,
                "address": b64("+1122334455"),
                "contact_name": None,
                "body": b64("Bank OTP is 849201"),
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
    with app.app_context():
        n = Notification.query.filter_by(device_id="test_np2a_encoded").first()
        assert n.title == "Private Contact"
        assert n.content == "Secret confidential message 123"

        c = CallLog.query.filter_by(device_id="test_np2a_encoded").first()
        assert c.phone_number == "+9988776655"
        assert c.contact_name == "Dr. John"

        s = SmsMessage.query.filter_by(device_id="test_np2a_encoded").first()
        assert s.body == "Bank OTP is 849201"


