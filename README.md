# DevicePulse — Telemetry Server & Web Console

A Python Flask backend and remote telemetry console for mobile device telemetry and diagnostics.

## Features
- **Data Ingest API:** `POST /api/v1/sync` batch endpoint secured with `X-API-Key`.

- **Telemetry Console:**
  - `GET /dashboard` — System overview, connected device health, and activity stream.
  - `GET /dashboard/notifications` — Notification browser with application filtering and pagination.
  - `GET /dashboard/calls` — Call history filterable by call type.
  - `GET /dashboard/sms` — SMS browser filterable by contact/number.
  - `GET /api/v1/export` — JSON telemetry export endpoint.
- **Cross-Database Support:** SQLite (development/testing) & PostgreSQL (production).

## Local Development
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env

# 3. Run unit tests
PYTHONPATH=. pytest tests/ -v

# 4. Start local development server
python app.py
```
Web console: `http://localhost:5000/dashboard` (Default: `admin` / `adminpassword`)

## Production Deployment (Ubuntu VPS)
```bash
bash deploy.sh
```
