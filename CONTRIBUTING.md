# Contributing to BPC EV Charging Platform

Thanks for your interest. Bug reports, OCPP interoperability findings, documentation fixes and code are all welcome, in English or Thai (ยินดีรับทั้งภาษาไทยและภาษาอังกฤษ).

## Ways to help

- **Report a bug or request a feature** with the issue forms. Say which page or endpoint is affected (`/`, `/app/`, `/pay/...`, `/ocpp/<id>`) and which charger model or simulator you used.
- **Interoperability reports** are especially useful: if a charger does not complete `BootNotification`, `Authorize`, `StartTransaction` or `MeterValues` against this CSMS, attach the OCPP message log with charger identifiers and card IDs removed.
- Look for the `good first issue` and `help wanted` labels for scoped tasks.
- Comment on an issue before starting larger work so nobody duplicates it.

## Running locally

```bash
pip install -r requirements.txt
export CSMS_USER=admin CSMS_PASS=change-me
python csms_web_ui_flexxfast1.py --csms-child 1.6 --port 9000 --web-port 8080
```

Open <http://localhost:8080>. To exercise the OCPP side without hardware, run the virtual charge point in a second terminal:

```bash
python mock_cp.py --url ws://127.0.0.1:9000/ocpp --id SIM-001
```

MongoDB is optional for local experiments; set `CSMS_MONGODB_URI` when you need persistence. The container build is `docker build -t bpc-csms .` and `render.yaml` describes the Render deployment.

## Pull requests

1. Fork, create a branch, keep each pull request to one change.
2. Make sure `python -m compileall -q .` passes and the operator console still starts.
3. Describe what changed and how you tested it (real charger, `mock_cp.py`, or built-in simulators).
4. **Never commit** `*.json` data files, connection strings, passwords, certificates or anything that identifies a real site, driver or payment.

## Code style

Plain Python 3.12, standard library first, `aiohttp` for HTTP and `ocpp` for the protocol layer. Keep new settings as `CSMS_*` environment variables and document them in the README.

## Licensing

By contributing you agree that your contributions are licensed under the Apache License 2.0 that covers this repository. The BPC name and logo remain excluded, as described in `NOTICE`.
