<div align="center">

# BPC EV Charging Platform

**Charging station management system (CSMS) for Bhutan Power Corporation Limited**

OCPP 1.6-J central system · operator console · operations platform · BPC EV app · scan-to-pay

[![CSMS v1.15.0](https://img.shields.io/badge/CSMS-v1.15.0-0a7a3c)](#versions)
[![OCPP 1.6-J](https://img.shields.io/badge/OCPP-1.6--J-1f6feb)](#connect-a-charger)
[![Docker](https://img.shields.io/badge/Docker-one%20container-2496ed?logo=docker&logoColor=white)](Dockerfile)
[![Render](https://img.shields.io/badge/Render-ready-46e3b7?logo=render&logoColor=white)](#deploy-on-render)
[![MongoDB Atlas](https://img.shields.io/badge/MongoDB-Atlas-47a248?logo=mongodb&logoColor=white)](#configuration)
[![English · Dzongkha · Thai](https://img.shields.io/badge/UI-English%20%C2%B7%20Dzongkha%20%C2%B7%20Thai-5b6b62)](#screenshots)

<br>

<img src="docs/screenshots/hero.jpg" alt="Operator console with ten chargers in western Bhutan, and the BPC EV app on a phone" width="100%">

<sub>All screenshots: CSMS v1.15 with demonstration data — no real customers or chargers.</sub>

</div>

## What it does

| | |
|---|---|
| ⚡ **Chargers** | OCPP 1.6-J (plus an OCPP 2.0.1 mode): live connector status, remote start and stop, reset, unlock, power limits, firmware updates and diagnostics files. Charger IDs issued by the CSMS, a password per charger and an identity check at every start-up. |
| 🩺 **Operations** | Health checks every 30 seconds with fault codes explained, alerts with escalation by e-mail, Telegram, SMS or webhook, work orders with SLA timers and a technician page for phones, load management per site. |
| 📈 **KPIs and reports** | Uptime per charger and site, energy, sessions and outages, the monthly report for DoST, charging history per customer and per station — with CSV and Excel exports. |
| 📱 **Drivers** | The BPC EV app (installable web app): map, booking of connectors, wallet, live session and receipts. A scan-to-pay QR sticker on every connector, and a free-charging mode with fair-use limits. |
| 💳 **Payments** | Bhutan QR, RMA Payment Gateway (sandbox), wallet and cash; refunds, tariffs, tiers and promo codes. |
| 🔐 **Accounts and data** | Personal logins with roles and an audit log, all data in MongoDB Atlas, backups with a checked restore, Bhutan Time in every report. |

<details>
<summary><b>All 21 console tabs</b></summary>

| Area | Tabs |
|---|---|
| Chargers | Stations, Map, Message Log, Registration (charger IDs and charger access), Default Config, Log Export, Firmware & logs |
| Drivers and payments | Payments, Customers, RFID Cards, Free charging, Bookings |
| Operations | Users, Health, Alerts, Maintenance, KPIs & reports, Charging history, Load management |
| System | Server (backups and restore), Database |

</details>

## Screenshots

<table>
<tr>
<td width="50%"><a href="docs/screenshots/stations.jpg"><img src="docs/screenshots/stations.jpg" alt="Stations tab"></a><br><b>Stations</b> — every charger and connector live, problems first</td>
<td width="50%"><a href="docs/screenshots/station-drawer.jpg"><img src="docs/screenshots/station-drawer.jpg" alt="Station drawer"></a><br><b>Station drawer</b> — power, energy and state of charge per connector; remote commands</td>
</tr>
<tr>
<td><a href="docs/screenshots/map.jpg"><img src="docs/screenshots/map.jpg" alt="Map tab"></a><br><b>Map</b> — every station on OpenStreetMap, coloured by state</td>
<td><a href="docs/screenshots/kpis.jpg"><img src="docs/screenshots/kpis.jpg" alt="KPIs and reports"></a><br><b>KPIs &amp; reports</b> — uptime, energy, sessions and outages; the report for DoST</td>
</tr>
<tr>
<td><a href="docs/screenshots/health.jpg"><img src="docs/screenshots/health.jpg" alt="Health tab"></a><br><b>Health</b> — open issues and what to do, checked every 30 seconds</td>
<td><a href="docs/screenshots/maintenance.jpg"><img src="docs/screenshots/maintenance.jpg" alt="Maintenance tab"></a><br><b>Maintenance</b> — work orders with SLA timers and preventive plans</td>
</tr>
<tr>
<td><a href="docs/screenshots/charging-history.jpg"><img src="docs/screenshots/charging-history.jpg" alt="Charging history per customer"></a><br><b>Charging history</b> — who charged, how often and how much</td>
<td><a href="docs/screenshots/charging-history-days.jpg"><img src="docs/screenshots/charging-history-days.jpg" alt="Charging history day by day"></a><br><b>Day by day</b> — sessions and energy per station and day</td>
</tr>
<tr>
<td><a href="docs/screenshots/charger-ids.jpg"><img src="docs/screenshots/charger-ids.jpg" alt="Charger IDs"></a><br><b>Charger IDs</b> — issued by the CSMS, with a password, a setup sheet and an identity check</td>
<td><a href="docs/screenshots/rfid-cards.jpg"><img src="docs/screenshots/rfid-cards.jpg" alt="RFID cards"></a><br><b>RFID cards</b> — the owner of every card; CSV and Excel import and export</td>
</tr>
<tr>
<td><a href="docs/screenshots/bookings.jpg"><img src="docs/screenshots/bookings.jpg" alt="Bookings tab"></a><br><b>Bookings</b> — reserve now or a time slot; the charger or the CSMS keeps the connector</td>
<td><a href="docs/screenshots/stations-dzongkha.jpg"><img src="docs/screenshots/stations-dzongkha.jpg" alt="Stations tab in Dzongkha"></a><br><b>Dzongkha</b> — every tab in Dzongkha too (and Thai)</td>
</tr>
</table>

### BPC EV app, scan-to-pay and technician page

<table>
<tr>
<td align="center" width="25%"><img src="docs/screenshots/app-home.jpg" alt="App home"><br><sub><b>Home</b> — wallet and bookings</sub></td>
<td align="center" width="25%"><img src="docs/screenshots/app-map.jpg" alt="App map"><br><sub><b>Map</b> — chargers nearby</sub></td>
<td align="center" width="25%"><img src="docs/screenshots/app-booking.jpg" alt="Book a connector"><br><sub><b>Booking</b> — reserve now or later</sub></td>
<td align="center" width="25%"><img src="docs/screenshots/app-charging.jpg" alt="Live charging session"><br><sub><b>Charging</b> — live energy and cost</sub></td>
</tr>
<tr>
<td align="center"><img src="docs/screenshots/app-station.jpg" alt="Station page"><br><sub><b>Station</b> — connectors and price</sub></td>
<td align="center"><img src="docs/screenshots/scan-to-pay.jpg" alt="Scan-to-pay page"><br><sub><b>Scan-to-pay</b> — the page behind the QR sticker</sub></td>
<td align="center"><img src="docs/screenshots/technician.jpg" alt="Technician page"><br><sub><b>Technician</b> — field jobs on the phone</sub></td>
<td></td>
</tr>
</table>

## How it fits together

```mermaid
flowchart TB
    CH["Chargers<br/>Setec · Blue Sky · Flexxfast"]
    OPS["Operators and technicians<br/>browser · phone"]
    DRV["Drivers<br/>BPC EV app · QR sticker"]
    CSMS["<b>CSMS v1.15</b><br/>one Docker container<br/>console · operations<br/>app · pay page · OCPP"]
    DB[("MongoDB Atlas")]
    PAY["Bhutan QR<br/>RMA Payment Gateway"]
    CH -- "OCPP 1.6-J over wss" --> CSMS
    OPS -- "HTTPS" --> CSMS
    DRV -- "HTTPS" --> CSMS
    CSMS <--> DB
    CSMS <--> PAY
```

One HTTPS address serves everything:

| Path | What |
|---|---|
| `/` | Operator console (personal login with a role) |
| `/app/` | BPC EV customer app (installable web app) |
| `/pay/<station>/<connector>` | Scan-to-pay page behind the QR sticker on each charger |
| `/free/<charger>` | Free-charging start page (vehicle plate or mobile number, fair-use limits) |
| `/tech` | Technician page for phones: work orders, checklists, parts |
| `wss://<host>/ocpp/<charge point id>` | OCPP 1.6-J endpoint for chargers |
| `/health`, `/healthz`, `/readyz` | Health checks (`/readyz` also checks the database and the event loop) |

All data — stations, payments, customers, RFID cards and their owners, users and the audit log, alerts, work orders,
bookings, charger IDs, charging sessions, settings and the OCPP log — lives in MongoDB Atlas, so the service can restart
or redeploy without losing anything. Three simulated chargers (BPC-THIMPHU, BPC-PARO, BPC-PUNAKHA) run inside the
container for demonstrations; set `CSMS_SIMULATORS` to an empty value to switch them off.

## Deploy on Render

1. **New → Web Service → Public Git Repository**: paste the URL of this repository.
2. Language **Docker**, branch `main`, region **Singapore**, instance type **Free**.
3. Environment variables:
    * `CSMS_PASS` — the console password (user `admin`). Use a long, unique password: the console is on the internet.
    * `CSMS_MONGODB_URI` — the MongoDB Atlas connection string (`mongodb+srv://<user>:<password>@<cluster>/`).
      The Atlas network access list must allow Render (e.g. `0.0.0.0/0` with a strong database password).
    * A second (staging) service on the same cluster gets its own database: `CSMS_MONGODB_DB=bpc_csms_staging`.
4. Health check path `/health`, then **Deploy**. The first build takes a few minutes.

A service created from the public repository URL does not redeploy on a push: use **Manual Deploy → Deploy latest
commit**, and **Rollback** on an earlier deploy to go back. `render.yaml` describes the same service as a Render Blueprint.

## Connect a charger

Issue the charger's ID in **Registration › Charger IDs** (for example `BPC-THM-0001`): the ID is accepted at once and gets
its own password and a printable setup sheet for the installer. Point the charger (or the Virtual Charger) at
`wss://<service>.onrender.com/ocpp` with that ID and password, e.g. `wss://bpc-csms.onrender.com/ocpp/BPC-THM-0001`.
The CSMS checks the charger's manufacturer, model and serial number at every start-up and warns when they change.
A charger without an issued ID waits as *Pending* until an operator accepts it (Stations or Registration tab), or is
refused when charger access is set to known chargers only. A charger that is not accepted cannot start charging (a
setting, on by default).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CSMS_USER` / `CSMS_PASS` | `admin` / — | First console login (no password = no login possible); personal accounts are added in the Users tab |
| `CSMS_MONGODB_URI` | — | MongoDB connection string |
| `CSMS_MONGODB_DB` | `bpc_csms` | Database name |
| `CSMS_PUBLIC_URL` | Render's address | Public address used in QR stickers, the app poster and payment return URLs |
| `CSMS_SIMULATORS` | `BPC-THIMPHU BPC-PARO BPC-PUNAKHA` | Built-in simulated chargers (space-separated IDs, empty = none) |
| `CSMS_SIM_SPEED` | `20` | Simulator energy speed-up |
| `CSMS_OCPP_PORT` | `0` | Extra OCPP-only port (e.g. `9000` for `docker run -p 9000:9000`); `0` = OCPP only on the web port |
| `CSMS_OCPP_BIND` | `127.0.0.1` | Address the extra OCPP port listens on |
| `CSMS_TRUST_PROXY` | `1` | Client IP from `X-Forwarded-For` (login lock-out and rate limits per client behind Render's proxy) |
| `CSMS_CLIENT_IP_HEADER` | — | Take the client IP from this header instead (e.g. `True-Client-IP` behind a proxy that sets one) |
| `TZ` | `Asia/Thimphu` | Time zone for timestamps, daily logs and the morning session expiry |

## Notes

**Free tier**

* A free Render service sleeps after 15 minutes without traffic from outside and needs about a minute to wake up.
  Connected chargers send OCPP messages through Render, which keeps it awake; the built-in simulators run inside the
  container (they do not keep it awake) and start again with the service when it wakes up.
* 512 MB RAM / 0.1 CPU and an ephemeral disk: fine for pilots, demos and training. For production use a paid instance,
  an Atlas Flex/M10+ cluster with backups, and a custom domain.
* A free Render service cannot send e-mail (its SMTP ports are blocked): send alerts by Telegram, an SMS gateway or a
  webhook there.

**Security**

* The console, the app and the pay page are served over HTTPS; session cookies are `Secure` and `HttpOnly`.
* Every staff member has a personal account with a role (admin, operator, technician, viewer); changes and commands are
  kept in the audit log; repeated wrong passwords lock the account and the address for a while.
* Chargers use OCPP security profile 1–2 (a password per charger, over wss); charger access can be open, known chargers
  only or strict, with lock-outs after repeated failures, plus the identity check above.
* Never commit `*.json` data files or connection strings; secrets belong in the Render environment only.

## Run locally

```
pip install -r requirements.txt
CSMS_USER=admin CSMS_PASS=change-me python csms_web_ui_flexxfast1.py --csms-child 1.6 --port 9000 --web-port 8080
```

Then open http://localhost:8080 and point a charger or the Virtual Charger at `ws://localhost:9000/<charge point id>`.

## Versions

| Version | What it added |
|---|---|
| **1.15** | Charging history per customer and per station; the KPI sheet "Daily by charger" |
| 1.14 | RFID card owners (name, mobile, vehicle plate, organisation); CSV / Excel import and export |
| 1.13 | Charger IDs issued by the CSMS, a password and setup sheet per charger, identity check at start-up |
| 1.12 | Pre-booking of connectors (OCPP ReserveNow, or kept by the CSMS) |
| 1.11 | Bhutan Time everywhere, firmware updates, diagnostics files, Dzongkha for the operations tabs |
| 1.10 | Fixes from the 200-charger load test |
| 1.9 | Backups and a checked restore, `/healthz` and `/readyz`, staging |
| 1.8 | Load management per site |
| 1.7 | Free charging with fair-use limits |
| 1.6 | Charger security: OCPP security profiles 1–2 |
| 1.5 | Maintenance: work orders, SLA timers, technician page |
| 1.4 | Alerts with escalation |
| 1.3 | Uptime and energy reports, KPI dashboard, the report for DoST |
| 1.2 | Status history and network health |
| 1.1 | Users, roles and audit log |

---

<sub>Prepared for Bhutan Power Corporation Limited. Screenshots show demonstration data.</sub>
