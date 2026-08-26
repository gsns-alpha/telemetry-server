import io
import os
import pytest
from datetime import datetime, timezone

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['API_KEY'] = 'test-api-key'
os.environ['DASHBOARD_USERNAME'] = 'admin'
os.environ['DASHBOARD_PASSWORD'] = 'secretpass'
os.environ['SECRET_KEY'] = 'test-secret'

from app import app, db, CdrStatement, CdrCall, CdrSms, ingest_cdr_data

SAMPLE_PDF_PATH = "/Users/om/Documents/workspaces/cf/cr/1-6266035693888_17891148_6_2026.pdf"


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        yield app.test_client()


def test_cdr_dashboard_unauthenticated(client):
    r = client.get('/dashboard/cdr')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_cdr_dashboard_render_and_ingestion(client):
    # Log in
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    # Mock ingest statement
    now = datetime.now(timezone.utc)
    mock_data = {
        'filename': 'test_sample_statement.pdf',
        'bill_no': 'BILL123456',
        'account_no': 'ACCT987654',
        'bill_period': '17 May 2026-16 Jun 2026',
        'bill_date': '18 Jun 2026',
        'total_calls': 2,
        'total_sms': 1,
        'total_duration_sec': 147,
        'total_call_pulses': 3,
        'total_sms_pulses': 1,
        'calls': [
            {
                'source_subscriber': '7760174171',
                'serial_no': 1,
                'call_date_str': '17/MAY/2026',
                'call_time_str': '10:53:45',
                'occurred_at': now,
                'destination_number': '9986968078',
                'duration_str': '00:29',
                'duration_sec': 29,
                'pulse': 1,
                'amount': 0.00,
                'operator': None,
                'call_category': '1.Local Calls',
                'page_number': 21
            },
            {
                'source_subscriber': '7760174171',
                'serial_no': 2,
                'call_date_str': '17/MAY/2026',
                'call_time_str': '10:55:27',
                'occurred_at': now,
                'destination_number': '9718405111',
                'duration_str': '01:58',
                'duration_sec': 118,
                'pulse': 2,
                'amount': 0.00,
                'operator': 'Airtel-UP(East)',
                'call_category': '1.National roaming',
                'page_number': 21
            }
        ],
        'sms': [
            {
                'source_subscriber': '7760174171',
                'serial_no': 1,
                'sms_date_str': '29/MAY/2026',
                'sms_time_str': '16:10:47',
                'occurred_at': now,
                'destination_number': '52263',
                'sms_count': 1,
                'pulse': 1,
                'amount': 0.00,
                'operator': None,
                'sms_category': '5.SMS - Other Services',
                'page_number': 25
            }
        ]
    }

    with app.app_context():
        ingest_res = ingest_cdr_data(mock_data, target_subscriber='7760174171')
        assert ingest_res['status'] == 'success'
        assert ingest_res['inserted_calls'] == 2
        assert ingest_res['inserted_sms'] == 1

    # 1. Main CDR Calls Tab
    r = client.get('/dashboard/cdr?tab=calls')
    assert r.status_code == 200
    assert b'7760174171' in r.data
    assert b'9986968078' in r.data
    assert b'9718405111' in r.data
    assert b'Airtel-UP(East)' in r.data

    # 2. Sent SMS Tab
    r = client.get('/dashboard/cdr?tab=sms')
    assert r.status_code == 200
    assert b'52263' in r.data
    assert b'5.SMS - Other Services' in r.data

    # 3. Analytics Tab
    r = client.get('/dashboard/cdr?tab=analytics')
    assert r.status_code == 200
    assert b'Top Numbers by Call Frequency' in r.data
    assert b'9986968078' in r.data

    # 4. Statements Tab
    r = client.get('/dashboard/cdr?tab=statements')
    assert r.status_code == 200
    assert b'test_sample_statement.pdf' in r.data
    assert b'BILL123456' in r.data


def test_cdr_export_csv(client):
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    # Export Calls CSV
    r = client.get('/dashboard/cdr/export?type=calls&subscriber=7760174171')
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'text/csv; charset=utf-8'
    assert b'Destination Number' in r.data
    assert b'Duration (Seconds)' in r.data

    # Export SMS CSV
    r = client.get('/dashboard/cdr/export?type=sms&subscriber=7760174171')
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'text/csv; charset=utf-8'
    assert b'Destination / Shortcode' in r.data


def test_cdr_global_search_integration(client):
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    # Ensure mock record exists
    now = datetime.now(timezone.utc)
    mock_data = {
        'filename': 'search_test_statement.pdf',
        'bill_no': 'SRCH_BILL_99',
        'account_no': 'ACCT99',
        'bill_period': '17 May 2026-16 Jun 2026',
        'bill_date': '18 Jun 2026',
        'total_calls': 1,
        'total_sms': 1,
        'total_duration_sec': 50,
        'calls': [{
            'source_subscriber': '7760174171',
            'serial_no': 1,
            'call_date_str': '17/MAY/2026',
            'call_time_str': '10:53:45',
            'occurred_at': now,
            'destination_number': '9718405111',
            'duration_str': '00:50',
            'duration_sec': 50,
            'pulse': 1,
            'amount': 0.00,
            'operator': 'Airtel-UP(East)',
            'call_category': '1.Local Calls',
            'page_number': 21
        }],
        'sms': [{
            'source_subscriber': '7760174171',
            'serial_no': 1,
            'sms_date_str': '29/MAY/2026',
            'sms_time_str': '16:10:47',
            'occurred_at': now,
            'destination_number': '52263',
            'sms_count': 1,
            'pulse': 1,
            'amount': 0.00,
            'operator': None,
            'sms_category': '5.SMS - Shortcode',
            'page_number': 25
        }]
    }
    with app.app_context():
        ingest_cdr_data(mock_data, target_subscriber='7760174171')

    # 1. UI Search for CDR Call Number
    r = client.get('/dashboard/search?q=9718405111')
    assert r.status_code == 200
    assert b'Postpaid Statement Calls' in r.data
    assert b'9718405111' in r.data
    assert b'search-highlight' in r.data

    # 2. UI Search for CDR SMS Shortcode
    r = client.get('/dashboard/search?q=52263')
    assert r.status_code == 200
    assert b'Postpaid Statement SMS' in r.data
    assert b'52263' in r.data

    # 3. API Search
    r_api = client.get('/api/v1/search?q=9718405111', headers={'X-API-Key': 'test-api-key'})
    assert r_api.status_code == 200
    json_data = r_api.get_json()
    assert json_data['counts']['cdr_calls'] >= 1
    assert any(c['destination_number'] == '9718405111' for c in json_data['results']['cdr_calls'])


def test_cdr_upload_endpoint(client):
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    if not os.path.exists(SAMPLE_PDF_PATH):
        pytest.skip("Sample PDF not available for upload test")

    with open(SAMPLE_PDF_PATH, 'rb') as f:
        pdf_bytes = f.read()

    data = {
        'target_subscriber': '7760174171',
        'statement_files': (io.BytesIO(pdf_bytes), 'upload_test_sample.pdf')
    }

    r = client.post('/dashboard/cdr/upload', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert r.status_code == 200
    assert b'Processed 1 statement(s)' in r.data
