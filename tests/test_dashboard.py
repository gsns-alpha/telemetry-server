import os
import pytest
from datetime import datetime, timezone

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['API_KEY'] = 'test-api-key'
os.environ['DASHBOARD_USERNAME'] = 'admin'
os.environ['DASHBOARD_PASSWORD'] = 'secretpass'
os.environ['SECRET_KEY'] = 'test-secret'

from app import app, db, Notification, CallLog, SmsMessage, Device, GpsLog


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
    assert b'System Overview' in response.data



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

    # Search page without query
    r = client.get('/dashboard/search')
    assert r.status_code == 200
    assert b'Global Telemetry Search' in r.data

    # Search query case-insensitive across all tables (highlighted results)
    r = client.get('/dashboard/search?q=TEST')
    assert r.status_code == 200
    assert b'search-highlight' in r.data
    assert b'Title' in r.data

    # Search query for phone number / SMS
    r = client.get('/dashboard/search?q=hello')
    assert r.status_code == 200
    assert b'search-highlight' in r.data
    assert b'SMS' in r.data

    # Search with category filter
    r = client.get('/dashboard/search?q=123&type=calls')
    assert r.status_code == 200
    assert b'search-highlight' in r.data
    assert b'45' in r.data


def test_global_search_comprehensive(client):
    # Log in
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    now = datetime.now(timezone.utc)
    with app.app_context():
        # Setup rich dataset
        d1 = Device(device_id="np2a_node_alpha", device_model="Nothing Phone (2a)", android_version="14", app_version="1.2.0", battery_level=95, is_charging=True, last_sync=now)
        d2 = Device(device_id="pixel_node_beta", device_model="Google Pixel 8", android_version="15", app_version="1.1.0", battery_level=60, is_charging=False, last_sync=now)
        db.session.add_all([d1, d2])

        # Notifications
        n1 = Notification(device_id="np2a_node_alpha", app_package="com.whatsapp", app_name="WhatsApp", title="Alice Sharma", content="Please call me back regarding Airtel payment", category="msg", received_at=now)
        n2 = Notification(device_id="pixel_node_beta", app_package="com.google.android.gm", app_name="Gmail", title="Security Alert", content="New login detected from Mumbai", category="email", received_at=now)
        db.session.add_all([n1, n2])

        # Calls
        c1 = CallLog(device_id="np2a_node_alpha", phone_number="+919824839944", contact_name="Rahul Verma", call_type="outgoing", duration_sec=248, sim_slot="SIM 1 (Jio 5G)", occurred_at=now)
        c2 = CallLog(device_id="pixel_node_beta", phone_number="+919871920832", contact_name="Doctor Clinic", call_type="incoming", duration_sec=45, sim_slot="SIM 2 (Airtel)", occurred_at=now)
        db.session.add_all([c1, c2])

        # SMS
        s1 = SmsMessage(device_id="np2a_node_alpha", address="VK-HDFCBK", contact_name="HDFC Bank", body="Your OTP for transaction is 482910. Valid for 10 mins.", sms_type="inbox", sim_slot="SIM 1 (Jio 5G)", occurred_at=now)
        s2 = SmsMessage(device_id="pixel_node_beta", address="+919824839944", contact_name="Rahul Verma", body="Sent the invoice to your mail", sms_type="sent", sim_slot="SIM 2 (Airtel)", occurred_at=now)
        db.session.add_all([s1, s2])

        # GPS
        g1 = GpsLog(device_id="np2a_node_alpha", is_enabled=True, occurred_at=now)
        g2 = GpsLog(device_id="pixel_node_beta", is_enabled=False, occurred_at=now)
        db.session.add_all([g1, g2])

        db.session.commit()

    # 1. Partial phone number search (LIKE %9824839944%) -> matches call & sms
    r = client.get('/dashboard/search?q=9824839944')
    assert r.status_code == 200
    assert b'Rahul Verma' in r.data
    assert b'9824839944' in r.data
    assert b'Voice Calls' in r.data
    assert b'Data / SMS Messages' in r.data

    # 2. Case-insensitive carrier search (LIKE %airtel%) -> matches notification, call, sms
    r = client.get('/dashboard/search?q=AIRTEL')
    assert r.status_code == 200
    assert b'Airtel' in r.data
    assert b'search-highlight' in r.data

    # 3. OTP search -> matches bank SMS
    r = client.get('/dashboard/search?q=otp')
    assert r.status_code == 200
    assert b'482910' in r.data
    assert b'HDFC Bank' in r.data

    # 4. Device model search -> matches device record & notifications
    r = client.get('/dashboard/search?q=nothing')
    assert r.status_code == 200
    assert b'Nothing Phone (2a)' in r.data

    # 5. Non-existent query -> clean empty state
    r = client.get('/dashboard/search?q=XYZNONEXISTENT999')
    assert r.status_code == 200
    assert b'No records matched' in r.data
    assert b'0' in r.data


def test_logout(client):
    # Log in then out
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})
    r = client.get('/logout', follow_redirects=True)
    assert r.status_code == 200
    assert b'Sign In' in r.data


