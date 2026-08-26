import io
import os
import pytest
from datetime import datetime, timezone

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['API_KEY'] = 'test-api-key'
os.environ['DASHBOARD_USERNAME'] = 'admin'
os.environ['DASHBOARD_PASSWORD'] = 'secretpass'
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['SKIP_SEED'] = '1'
os.environ['FLASK_ENV'] = 'testing'

from app import app, db, CdrStatement, CdrCall, CdrSms, ingest_cdr_data

SAMPLE_PDF_PATH = "/Users/om/Documents/workspaces/cf/cr/1-6266035693888_17891148_6_2026.pdf"


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


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
    assert b'Processed 1 statement(s)' in r.data or b'649' in r.data


def test_duplicate_statement_upload_ignored(client):
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    if not os.path.exists(SAMPLE_PDF_PATH):
        pytest.skip("Sample PDF not available for upload test")

    with open(SAMPLE_PDF_PATH, 'rb') as f:
        pdf_bytes = f.read()

    # 1. First upload
    data1 = {
        'target_subscriber': '7760174171',
        'statement_files': (io.BytesIO(pdf_bytes), 'june_bill.pdf')
    }
    r1 = client.post('/dashboard/cdr/upload', data=data1, content_type='multipart/form-data', follow_redirects=True)
    assert r1.status_code == 200

    with app.app_context():
        calls_count_1 = CdrCall.query.filter_by(source_subscriber='7760174171').count()
        sms_count_1 = CdrSms.query.filter_by(source_subscriber='7760174171').count()
        assert calls_count_1 == 649
        assert sms_count_1 == 63

    # 2. Second upload with SAME file (exact same content)
    data2 = {
        'target_subscriber': '7760174171',
        'statement_files': (io.BytesIO(pdf_bytes), 'june_bill.pdf')
    }
    r2 = client.post('/dashboard/cdr/upload', data=data2, content_type='multipart/form-data', follow_redirects=True)
    assert r2.status_code == 200
    assert b'Duplicate records ignored' in r2.data or b'duplicate' in r2.data.lower()

    # 3. Verify total records in DB did NOT increase / duplicate
    with app.app_context():
        calls_count_2 = CdrCall.query.filter_by(source_subscriber='7760174171').count()
        sms_count_2 = CdrSms.query.filter_by(source_subscriber='7760174171').count()
        assert calls_count_2 == 649  # Must remain exactly 649, zero duplicates
        assert sms_count_2 == 63    # Must remain exactly 63, zero duplicates


def test_renamed_file_hash_duplicate_ignored(client):
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    if not os.path.exists(SAMPLE_PDF_PATH):
        pytest.skip("Sample PDF not available for upload test")

    with open(SAMPLE_PDF_PATH, 'rb') as f:
        pdf_bytes = f.read()

    # 1. Initial upload as original_june.pdf
    data_orig = {
        'target_subscriber': '7760174171',
        'statement_files': (io.BytesIO(pdf_bytes), 'original_june.pdf')
    }
    r_orig = client.post('/dashboard/cdr/upload', data=data_orig, content_type='multipart/form-data')
    assert r_orig.status_code == 302

    # 2. Upload with different filename but identical file content hash
    data_renamed = {
        'target_subscriber': '7760174171',
        'statement_files': (io.BytesIO(pdf_bytes), 'renamed_duplicate_june.pdf')
    }
    r_json = client.post('/dashboard/cdr/upload', data=data_renamed, content_type='multipart/form-data', headers={'Accept': 'application/json'})
    assert r_json.status_code == 200
    res_data = r_json.get_json()
    assert res_data['files_skipped'] == 1
    assert res_data['total_calls_added'] == 0
    assert res_data['total_sms_added'] == 0
    assert res_data['details'][0]['duplicate'] is True


def test_record_level_duplicate_check_and_ignore(client):
    client.post('/login', data={'username': 'admin', 'password': 'secretpass'})

    fixed_time = datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc)
    
    # First batch with 2 unique calls and 1 SMS
    batch1 = {
        'filename': 'batch_statement_1.pdf',
        'bill_no': 'B1',
        'account_no': 'A1',
        'bill_period': 'Batch 1 Period',
        'calls': [
            {
                'source_subscriber': '7760174171',
                'occurred_at': fixed_time,
                'destination_number': '9876543210',
                'duration_sec': 100,
                'duration_str': '01:40',
                'pulse': 2
            },
            {
                'source_subscriber': '7760174171',
                'occurred_at': fixed_time,
                'destination_number': '9123456780',
                'duration_sec': 50,
                'duration_str': '00:50',
                'pulse': 1
            }
        ],
        'sms': [
            {
                'source_subscriber': '7760174171',
                'occurred_at': fixed_time,
                'destination_number': '52263',
                'sms_count': 1,
                'pulse': 1
            }
        ]
    }

    with app.app_context():
        res1 = ingest_cdr_data(batch1, target_subscriber='7760174171')
        assert res1['status'] == 'success'
        assert res1['inserted_calls'] == 2
        assert res1['inserted_sms'] == 1

    # Second batch from another statement file containing 1 duplicate call + 1 new call, and 1 duplicate SMS + 1 new SMS
    new_time = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
    batch2 = {
        'filename': 'batch_statement_2.pdf',
        'bill_no': 'B2',
        'account_no': 'A2',
        'bill_period': 'Batch 2 Period',
        'calls': [
            # DUPLICATE CALL (exact match with existing record in batch 1)
            {
                'source_subscriber': '7760174171',
                'occurred_at': fixed_time,
                'destination_number': '9876543210',
                'duration_sec': 100,
                'duration_str': '01:40',
                'pulse': 2
            },
            # NEW CALL
            {
                'source_subscriber': '7760174171',
                'occurred_at': new_time,
                'destination_number': '9999999999',
                'duration_sec': 30,
                'duration_str': '00:30',
                'pulse': 1
            }
        ],
        'sms': [
            # DUPLICATE SMS
            {
                'source_subscriber': '7760174171',
                'occurred_at': fixed_time,
                'destination_number': '52263',
                'sms_count': 1,
                'pulse': 1
            },
            # NEW SMS
            {
                'source_subscriber': '7760174171',
                'occurred_at': new_time,
                'destination_number': '7302722441',
                'sms_count': 1,
                'pulse': 1
            }
        ]
    }

    with app.app_context():
        res2 = ingest_cdr_data(batch2, target_subscriber='7760174171')
        assert res2['status'] == 'success'
        # Only the 1 new call should be inserted; the 1 duplicate call MUST be skipped
        assert res2['inserted_calls'] == 1
        assert res2['skipped_calls'] == 1
        # Only the 1 new SMS should be inserted; the 1 duplicate SMS MUST be skipped
        assert res2['inserted_sms'] == 1
        assert res2['skipped_sms'] == 1

        # Check total calls in database: exactly 3 (2 from batch 1 + 1 from batch 2)
        assert CdrCall.query.filter_by(source_subscriber='7760174171').count() == 3
        # Check total SMS in database: exactly 2 (1 from batch 1 + 1 from batch 2)
        assert CdrSms.query.filter_by(source_subscriber='7760174171').count() == 2

