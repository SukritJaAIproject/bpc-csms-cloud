# BPC EV Charging Platform — CSMS (cloud deployment)

Charging station management system for Bhutan Power Corporation Limited (**CSMS v1.15**), packaged for a
[Render](https://render.com) web service (or any Docker host). One container serves everything on one HTTPS address:

| Path | What |
|---|---|
| `/` | Operator console (personal login with a role): the tabs are listed below |
| `/app/` | BPC EV customer app (installable web app): account, wallet, map, scan & charge, pre-booking of connectors, history |
| `/pay/<station>/<connector>` | Scan-to-pay page behind the QR sticker on each charger |
| `/free/<charger>` | Free-charging start page (vehicle plate or mobile number, fair-use limits) |
| `/tech` | Technician page for phones: work orders, checklists, parts |
| `wss://<host>/ocpp/<charge point id>` | OCPP 1.6-J endpoint for chargers |
| `/health`, `/healthz`, `/readyz` | Health checks (`/readyz` also checks the database and the event loop) |

All data — stations, payments, customers, RFID cards and their owners, users and the audit log, alerts, work orders,
bookings, charger IDs, charging sessions, settings and the OCPP log — lives in MongoDB Atlas, so the service can restart
or redeploy without losing anything. Three simulated chargers (BPC-THIMPHU, BPC-PARO, BPC-PUNAKHA) run inside the
container for demonstrations; set `CSMS_SIMULATORS` to an empty value to switch them off.

## Console tabs (v1.15)

| Area | Tabs |
|---|---|
| Chargers | Stations (live connector cards, remote commands, power limits), Map, Message Log, Registration (charger IDs and charger access), Default Config, Log Export, Firmware & logs |
| Drivers and payments | Payments, Customers, RFID Cards (owner, mobile, vehicle plate, organisation; CSV / Excel import and export), Free charging, Bookings |
| Operations | Users (roles and audit log), Health, Alerts, Maintenance (work orders with SLA timers), KPIs & reports (uptime, energy, the report for DoST), Charging history (per customer and per station), Load management |
| System | Server (backups and restore, `/healthz`, `/readyz`), Database |

The console and the app are in English, Dzongkha and Thai; times are Bhutan Time (UTC+6).

## Deploy on Render (free)

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

## Environment variables

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

## Free-tier notes

* A free Render service sleeps after 15 minutes without traffic from outside and needs about a minute to wake up.
  Connected chargers send OCPP messages through Render, which keeps it awake; the built-in simulators run inside the
  container (they do not keep it awake) and start again with the service when it wakes up.
* 512 MB RAM / 0.1 CPU and an ephemeral disk: fine for pilots, demos and training. For production use a paid instance,
  an Atlas Flex/M10+ cluster with backups, and a custom domain.
* A free Render service cannot send e-mail (its SMTP ports are blocked): send alerts by Telegram, an SMS gateway or a
  webhook there.

## Security notes

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
