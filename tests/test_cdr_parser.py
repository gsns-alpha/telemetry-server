import os
import pytest
from datetime import datetime
from cdr_parser import (
    parse_cdr_pdf,
    parse_duration_to_seconds,
    format_seconds_to_duration,
    parse_date_time_to_datetime
)

SAMPLE_PDF_PATH = "/Users/om/Documents/workspaces/cf/cr/1-6266035693888_17891148_6_2026.pdf"


def test_duration_parsing():
    assert parse_duration_to_seconds("00:29") == 29
    assert parse_duration_to_seconds("01:58") == 118
    assert parse_duration_to_seconds("05:29") == 329
    assert parse_duration_to_seconds("01:14:15") == 4455
    assert parse_duration_to_seconds(None) == 0
    assert parse_duration_to_seconds("invalid") == 0


def test_format_seconds():
    assert format_seconds_to_duration(29) == "00:00:29"
    assert format_seconds_to_duration(329) == "00:05:29"
    assert format_seconds_to_duration(71783) == "19:56:23"
    assert format_seconds_to_duration(0) == "00:00:00"
    assert format_seconds_to_duration(None) == "00:00:00"


def test_date_time_parsing():
    dt = parse_date_time_to_datetime("17/MAY/2026", "10:53:45")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 17
    assert dt.hour == 10
    assert dt.minute == 53
    assert dt.second == 45

    dt2 = parse_date_time_to_datetime("24/FEB/2026", "16:23:20")
    assert dt2 is not None
    assert dt2.month == 2
    assert dt2.day == 24


def test_parse_june_sample_statement():
    if not os.path.exists(SAMPLE_PDF_PATH):
        pytest.skip(f"Sample PDF {SAMPLE_PDF_PATH} not found")

    res = parse_cdr_pdf(SAMPLE_PDF_PATH, target_subscriber="7760174171")
    
    assert res['filename'] == "1-6266035693888_17891148_6_2026.pdf"
    assert "17 May 2026-16 Jun 2026" in res['bill_period']
    assert res['account_no'] == "1-6266035693888"
    assert res['target_subscriber'] == "7760174171"
    
    # Ground truth verification for June 2026 bill
    assert res['total_calls'] == 649
    assert res['total_duration_sec'] == 71783
    assert res['total_duration_formatted'] == "19:56:23"
    assert res['total_call_pulses'] == 1541
    assert res['total_sms'] == 63

    # Check top destination calls
    numbers = [c['destination_number'] for c in res['calls']]
    assert "9718405111" in numbers
    assert "9986968078" in numbers
    assert "9620986790" in numbers
    
    # Check all calls belong to subscriber 7760174171
    for c in res['calls']:
        assert c['source_subscriber'] == "7760174171"
        assert c['duration_sec'] >= 0
        assert c['pulse'] >= 1

    # Check SMS records
    sms_destinations = [s['destination_number'] for s in res['sms']]
    assert "7302722441" in sms_destinations
    assert "52263" in sms_destinations  # Shortcode
    for s in res['sms']:
        assert s['source_subscriber'] == "7760174171"
        assert s['sms_count'] >= 1
