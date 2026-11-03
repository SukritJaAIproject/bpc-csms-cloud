# BPC EV Charging Platform — CSMS (cloud deployment)

Charging station management system for Bhutan Power Corporation Limited, packaged for a free
[Render](https://render.com) web service (or any Docker host). One container serves everything on one HTTPS address:

| Path | What |
|---|---|
| `/` | Operator console (login): stations, map, message log, payments, customers, RFID cards, registration, configuration, log export, database |
| `/app/` | BPC EV customer app (installable web app): account, wallet, map, scan & charge, history |
| `/pay/<station>/<connector>` | Scan-to-pay page behind the QR sticker on each charger |
| `wss://<host>/ocpp/<charge point id>` | OCPP 1.6-J endpoint for chargers |
| `/health` | Health check |

Data (stations, payments, customers, settings, RFID cards, transactions, OCPP log) lives in MongoDB Atlas, so the
service can restart or redeploy without losing anything. Three simulated chargers (BPC-THIMPHU, BPC-PARO, BPC-PUNAKHA)
run inside the container for demonstrations; set `CSMS_SIMULATORS` to an empty value to switch them off.

## Deploy on Render (free)

1. **New → Web Service → Public Git Repository**: paste the URL of this repository.
2. Language **Docker**, branch `main`, region **Singapore**, instance type **Free**.
3. Environment variables:
   * `CSMS_PASS` — the console password (user `admin`). Use a long, unique password: the console is on the internet.
   * `CSMS_MONGODB_URI` — the MongoDB Atlas connection string (`mongodb+srv://<user>:<password>@<cluster>/`).
     The Atlas network access list must allow Render (e.g. `0.0.0.0/0` with a strong database password).
4. Health check path `/health`, then **Deploy**. The first build takes a few minutes.

`render.yaml` describes the same service as a Render Blueprint.

## Connect a charger

Point the charger (or the Virtual Charger) at `wss://<service>.onrender.com/ocpp` with its charge point ID, e.g.
`wss://bpc-csms.onrender.com/ocpp/BPC-THIMPHU-01`. New chargers appear as *Pending* — accept them in the console
(Stations or Registration tab).

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CSMS_USER` / `CSMS_PASS` | `admin` / — | Console login (no password = no login possible) |
| `CSMS_MONGODB_URI` | — | MongoDB connection string; database `bpc_csms` |
| `CSMS_PUBLIC_URL` | Render's address | Public address used in QR stickers, the app poster and payment return URLs |
| `CSMS_SIMULATORS` | `BPC-THIMPHU BPC-PARO BPC-PUNAKHA` | Built-in simulated chargers (space-separated IDs, empty = none) |
| `CSMS_SIM_SPEED` | `20` | Simulator energy speed-up |
| `CSMS_TRUST_PROXY` | `1` | Client IP from `X-Forwarded-For` (login lock-out and rate limits per client behind Render's proxy) |
| `TZ` | `Asia/Thimphu` | Time zone for timestamps, daily logs and the morning session expiry |

## Free-tier notes

* A free Render service sleeps after 15 minutes without traffic and needs about a minute to wake up. Connected chargers
  (and the built-in simulators) send OCPP messages, which keeps it awake.
* 512 MB RAM / 0.1 CPU and an ephemeral disk: fine for pilots, demos and training. For production use a paid instance,
  an Atlas Flex/M10+ cluster with backups, and a custom domain.

## Security notes

* The console, the app and the pay page are served over HTTPS; session cookies are `Secure` and `HttpOnly`.
* Unknown chargers stay *Pending* until accepted, but the OCPP endpoint does not authenticate chargers (no OCPP security
  profile yet) — use station IDs that are hard to guess and watch the Registration tab.
* Never commit `*.json` data files or connection strings; secrets belong in the Render environment only.

## Run locally

```
pip install -r requirements.txt
CSMS_USER=admin CSMS_PASS=change-me python csms_web_ui_flexxfast1.py --csms-child 1.6 --port 9000 --web-port 8080
```
