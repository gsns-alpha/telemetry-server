"""
DevicePulse — CDR Statement Parser
Extracts outgoing call detail records and sent SMS messages from telecom postpaid PDF statements
specifically for target subscriber (e.g. 7760174171).
"""

import os
import re
from datetime import datetime
import pypdf


def parse_duration_to_seconds(dur_str):
    """Convert HH:MM:SS or MM:SS to integer seconds."""
    if not dur_str:
        return 0
    parts = str(dur_str).strip().split(':')
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        return 0
    return 0


def format_seconds_to_duration(seconds):
    """Format integer seconds to HH:MM:SS."""
    if seconds is None:
        return "00:00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_date_time_to_datetime(date_str, time_str):
    """Parse date string (e.g. 17/MAY/2026) and time string (e.g. 10:53:45) to datetime."""
    if not date_str or not time_str:
        return None
    clean_date = date_str.strip()
    clean_time = time_str.strip()
    full_str = f"{clean_date} {clean_time}"
    for fmt in [
        "%d/%b/%Y %H:%M:%S",
        "%d/%B/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S"
    ]:
        try:
            return datetime.strptime(full_str, fmt)
        except ValueError:
            pass
    return None


def parse_cdr_pdf(pdf_stream_or_path, target_subscriber='7760174171', filename=None):
    """
    Parse telecom statement PDF and extract outgoing calls & sent SMS for target_subscriber.

    :param pdf_stream_or_path: File path (str) or file-like object (BytesIO / FileStorage)
    :param target_subscriber: Mobile number to extract records for (default '7760174171')
    :param filename: Optional filename label
    :return: dict with metadata, calls list, and sms list
    """
    if isinstance(pdf_stream_or_path, str):
        reader = pypdf.PdfReader(pdf_stream_or_path)
        if not filename:
            filename = os.path.basename(pdf_stream_or_path)
    else:
        reader = pypdf.PdfReader(pdf_stream_or_path)
        if not filename:
            filename = getattr(pdf_stream_or_path, 'filename', 'statement.pdf')

    # 1. First page statement metadata
    first_page_text = reader.pages[0].extract_text() if len(reader.pages) > 0 else ""
    
    bill_period_match = re.search(r'Bill Period\s*[:\-]?\s*([0-9A-Za-z\s\-]+)', first_page_text)
    bill_period = bill_period_match.group(1).strip() if bill_period_match else None
    if bill_period and '\n' in bill_period:
        bill_period = bill_period.split('\n')[0].strip()
        
    bill_no_match = re.search(r'Bill NO\s*[:\-]?\s*([A-Za-z0-9]+)', first_page_text)
    bill_no = bill_no_match.group(1).strip() if bill_no_match else None
    
    bill_date_match = re.search(r'Bill Date\s*[:\-]?\s*([0-9A-Za-z\s]+)', first_page_text)
    bill_date = bill_date_match.group(1).strip() if bill_date_match else None
    if bill_date and '\n' in bill_date:
        bill_date = bill_date.split('\n')[0].strip()
        
    account_no_match = re.search(r'Account No\s*[:\-]?\s*([0-9A-Za-z\-]+)', first_page_text)
    account_no = account_no_match.group(1).strip() if account_no_match else None

    calls = []
    sms_messages = []

    current_subscriber = None
    current_section = 'Outgoing Call'
    current_subsection = ''

    # Row regex for Calls:
    # <S.No> <Date> <Time> [<Operator>] <Number> <Duration> <Pulse> <Amount>
    # (Matches durations like 00:29, 01:58, 14:15 or 01:23:45)
    call_row_pattern = re.compile(
        r'^\s*(\d+)\s+(\d{1,2}/[A-Za-z0-9]+/\d{4})\s+(\d{1,2}:\d{2}:\d{2})\s+(?:([A-Za-z0-9\-\(\)\.]+)\s+)?(\+?\d{3,15})\s+((?:\d{1,2}:)?\d{2}:\d{2})\s+(\d+)\s+([\d\.]+)',
        re.MULTILINE
    )

    # Row regex for SMS:
    # <S.No> <Date> <Time> [<Operator>] <Number/Shortcode> <Count> <Pulse> <Amount>
    sms_row_pattern = re.compile(
        r'^\s*(\d+)\s+(\d{1,2}/[A-Za-z0-9]+/\d{4})\s+(\d{1,2}:\d{2}:\d{2})\s+(?:([A-Za-z0-9\-\(\)\.]+)\s+)?(\+?\d{3,15})\s+(\d+)\s+(\d+)\s+([\d\.]+)',
        re.MULTILINE
    )

    for page_idx, page in enumerate(reader.pages):
        text = page.extract_text()
        if not text:
            continue
        lines = text.split('\n')

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Check for subscriber section header (e.g. YOUR CHARGES IN DETAIL - 7760174171)
            sub_match = re.search(r'YOUR CHARGES IN DETAIL\s*-\s*(\d+)', line_str, re.IGNORECASE)
            if sub_match:
                current_subscriber = sub_match.group(1).strip()
                current_section = 'Outgoing Call'
                current_subsection = ''
                continue

            alt_match = re.search(r'Airtel mobile number\s*(?:1-\d+\s*)?(\d{10})', line_str, re.IGNORECASE)
            if alt_match:
                current_subscriber = alt_match.group(1).strip()
                continue

            # Skip sections for other family members if target_subscriber is set
            if target_subscriber and current_subscriber != target_subscriber:
                continue

            # Section tracking
            if re.search(r'^\s*\d+\.(Local|STD|National roaming|Roaming|International|Voice Special)', line_str, re.IGNORECASE):
                current_section = line_str.strip()
                current_subsection = ''
                continue
            if re.search(r'^\s*\d+\.SMS', line_str, re.IGNORECASE):
                current_section = line_str.strip()
                current_subsection = ''
                continue
            if re.search(r'^\s*\d+\.[a-z]\s+', line_str, re.IGNORECASE):
                current_subsection = line_str.strip()
                continue
            if re.search(r'^\s*\d+\.Internet', line_str, re.IGNORECASE):
                current_section = line_str.strip()
                current_subsection = ''
                continue

            # Skip non-call/non-sms headers or internet usage
            if 'internet' in current_section.lower():
                continue

            # Check if we are in an SMS section
            is_sms_section = 'sms' in current_section.lower() or 'sms' in current_subsection.lower()

            if is_sms_section:
                # Match SMS row
                m_sms = sms_row_pattern.match(line_str)
                if m_sms:
                    sno_str, date_str, time_str, operator_str, dest_str, count_str, pulse_str, amt_str = m_sms.groups()
                    # Verify count is an integer and not a duration
                    if ':' not in count_str:
                        occurred_at = parse_date_time_to_datetime(date_str, time_str)
                        full_cat = f"{current_section} {current_subsection}".strip()
                        sms_messages.append({
                            'source_subscriber': current_subscriber,
                            'serial_no': int(sno_str),
                            'sms_date_str': date_str,
                            'sms_time_str': time_str,
                            'occurred_at': occurred_at,
                            'operator': operator_str,
                            'destination_number': dest_str,
                            'sms_count': int(count_str),
                            'pulse': int(pulse_str),
                            'amount': float(amt_str),
                            'sms_category': full_cat,
                            'page_number': page_idx + 1,
                            'statement_filename': filename,
                            'bill_period': bill_period,
                            'bill_no': bill_no
                        })
            else:
                # Match Call row
                m_call = call_row_pattern.match(line_str)
                if m_call:
                    sno_str, date_str, time_str, operator_str, num_str, dur_str, pulse_str, amt_str = m_call.groups()
                    dur_seconds = parse_duration_to_seconds(dur_str)
                    occurred_at = parse_date_time_to_datetime(date_str, time_str)
                    full_cat = f"{current_section} {current_subsection}".strip()

                    calls.append({
                        'source_subscriber': current_subscriber,
                        'serial_no': int(sno_str),
                        'call_date_str': date_str,
                        'call_time_str': time_str,
                        'occurred_at': occurred_at,
                        'operator': operator_str,
                        'destination_number': num_str,
                        'duration_str': dur_str,
                        'duration_sec': dur_seconds,
                        'pulse': int(pulse_str),
                        'amount': float(amt_str),
                        'call_category': full_cat,
                        'page_number': page_idx + 1,
                        'statement_filename': filename,
                        'bill_period': bill_period,
                        'bill_no': bill_no,
                        'account_no': account_no
                    })

    total_call_duration_sec = sum(c['duration_sec'] for c in calls)
    total_call_pulses = sum(c['pulse'] for c in calls)
    total_sms_pulses = sum(s['pulse'] for s in sms_messages)

    return {
        'filename': filename,
        'bill_period': bill_period,
        'bill_no': bill_no,
        'bill_date': bill_date,
        'account_no': account_no,
        'target_subscriber': target_subscriber,
        'total_calls': len(calls),
        'total_sms': len(sms_messages),
        'total_duration_sec': total_call_duration_sec,
        'total_duration_formatted': format_seconds_to_duration(total_call_duration_sec),
        'total_call_pulses': total_call_pulses,
        'total_sms_pulses': total_sms_pulses,
        'calls': calls,
        'sms': sms_messages
    }
